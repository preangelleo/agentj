"""Real Desk/State/gate/seal; synthetic Host adapter, not an end-to-end Host test.
No network, proof/UV shortcuts, dependency replacements, or external state.
"""
import asyncio
import copy
import hashlib
import json
import pathlib
import tempfile
import types
import unittest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from agentj import diskq, gate, passkey, pkpeer, wire
from agentj.elevate import seal, seal_aad
from agentj.noise import Keypair
from agentj.state import State


class Adapter:
    def __init__(self, st):
        self.st, self.kp = st, st.host_keypair()
        self.channel, self.hist = st.config()['channel'], types.SimpleNamespace(epoch=1)
        self.agent, self.lang, self.peers = None, 'en', None
        self.sessions, self.sent, self.pushes, self.tasks = {}, [], [], []
        self.offers, self.estop, self.pk_guard = [], False, passkey.Guard()
        self.pkpeer = pkpeer.Desk(self)

    def stopped(self): return self.estop
    def _dev_label(self, did): return self.st.devices()[did]['name']
    def _dname(self, did): return self._dev_label(did)
    def activity(self, *args, **kw): pass
    def push_notify(self, kind): self.pushes.append(kind)
    def _bg(self, coro, label):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task
    async def send_app(self, session, obj): self.sent.append((session.device, obj))
    async def broadcast(self, text, **kw): self.sent.append(('broadcast', {'text':text}))
    async def _pk_offer(self, session, peer):
        if self.pkpeer.approved(session.device, peer): self.offers.append((session.device, peer))


class Coordinator(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_buffer = diskq.BUFFER_FOR
        diskq.BUFFER_FOR = 0
        self.st = State(pathlib.Path(self.tmp.name)/'synthetic-state')
        self.st.init(relay='ws://127.0.0.1:1')  # URL data only; no connection
        self.h = Adapter(self.st)
        self.mac, self.phone = self.device('Mac'), self.device('phone')
        self.password = 'synthetic-only-p575-password'
        gate.set_passphrase(self.st, self.password)
        await self.h.pkpeer.on_phone(self.mac, 'pk_peer_req', {'r':'r1'})
        self.ask = self.messages(self.phone, 'pk_peer_ask')[-1]
        self.r = self.h.pkpeer.reqs[self.ask['id']]

    async def asyncTearDown(self):
        for task in self.h.tasks: task.cancel()
        await asyncio.gather(*self.h.tasks, return_exceptions=True)
        diskq.BUFFER_FOR = self.old_buffer
        self.tmp.cleanup()

    def device(self, name):
        kp, sign = Keypair.generate(), ed25519.Ed25519PrivateKey.generate()
        pub = sign.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        did = self.st.pair_device(kp.pub, name, pub)['device']
        session = types.SimpleNamespace(cid=len(self.h.sessions)+1, pub=kp.pub, device=did, name=name, state='ready', p33=True)
        self.h.sessions[session.cid] = session
        return session
    def messages(self, session, typ):
        return [m for did,m in self.h.sent if did == session.device and m.get('t') == typ]
    def package(self, password=None, device=None):
        a = self.ask
        epk, ct = seal(wire.unb64u(a['epk']), (password or self.password).encode(),
                       seal_aad(self.h.channel, device or self.phone.device, a['id'], 'pk_bootstrap', a['n'], a['digest']))
        return {'id':a['id'], 'ok':True, 'bootstrap':True, 'epk':wire.b64u(epk), 'ct':wire.b64u(ct)}
    async def submit(self, obj=None, session=None):
        s = session or self.phone
        await self.h.pkpeer.on_phone(s, 'pk_peer_answer', obj or self.package())
        return self.messages(s, 'pk_peer_answer_res')[-1]

    async def test_delivery_and_real_password(self):
        self.assertTrue(self.ask['bootstrap'])
        self.assertEqual(self.messages(self.mac,'pk_peer_res')[-1]['online'], 1)
        self.assertTrue((await self.submit())['ok'])
        self.assertEqual(self.r.exp, self.ask['exp'])
        self.assertEqual(len(self.h.offers), 1)
        self.assertNotIn(self.password, json.dumps(self.h.sent))
        self.assertNotIn(self.password, self.st.log_path.read_text())
        self.assertNotIn(self.password, self.st.approvals_path.read_text())

    async def test_wrong_and_lock(self):
        for n in range(gate.FREE_TRIES):
            result = await self.submit(self.package('synthetic-wrong'))
            self.assertEqual(result['why'], 'locked' if n == gate.FREE_TRIES-1 else 'wrong')
            self.ask = self.messages(self.phone,'pk_peer_ask')[-1]
        self.assertEqual((await self.submit())['why'], 'locked')
        self.assertFalse(self.h.offers)

    async def test_cross_device_ciphertext(self):
        self.assertEqual((await self.submit(self.package(device=self.mac.device)))['why'], 'bad_seal')
        self.assertFalse(self.h.offers)

    async def test_replay(self):
        obj = self.package()
        self.assertTrue((await self.submit(obj))['ok'])
        self.assertFalse((await self.submit(obj))['ok'])
        self.assertEqual(len(self.h.offers), 1)

    async def test_target_pk_prevents_downgrade(self):
        self.st.set_passkey(self.mac.pub, self.pk_record()[1])
        self.assertFalse((await self.submit())['ok'])
        self.assertFalse(self.h.offers)

    async def test_other_root_prevents_downgrade(self):
        other = self.device('offline root')
        self.h.sessions.pop(other.cid)
        self.st.set_passkey(other.pub, self.pk_record()[1])
        self.assertFalse((await self.submit())['ok'])
        self.assertFalse(self.h.offers)

    async def test_cancel(self):
        obj = self.package()
        await self.h.pkpeer.on_phone(self.mac,'pk_peer_cancel',{'id':self.r.id})
        self.assertFalse((await self.submit(obj))['ok'])

    async def test_estop(self):
        self.h.estop = True
        self.assertFalse((await self.submit())['ok'])

    async def test_epoch(self):
        self.h.hist.epoch += 1
        self.assertFalse((await self.submit())['ok'])

    async def test_expiry(self):
        self.h.pkpeer.clock = lambda: self.r.exp+1
        self.assertFalse((await self.submit())['ok'])

    async def test_deadline_mutation(self):
        self.r.exp += 30
        self.assertFalse((await self.submit())['ok'])

    async def test_replaced_approver_session(self):
        replacement = copy.copy(self.phone)
        self.h.sessions[self.phone.cid] = replacement
        self.assertFalse((await self.submit(session=replacement))['ok'])

    async def test_unpair(self):
        self.st.remove_device(self.mac.device)
        self.assertFalse((await self.submit())['ok'])

    async def test_password_verification_yield_target_replaced(self):
        original = gate.verify
        from unittest.mock import patch
        def verify(st, password):
            result = original(st, password)
            self.h.sessions[self.mac.cid] = copy.copy(self.mac)
            return result
        with patch.object(gate,'verify', verify):
            self.assertFalse((await self.submit())['ok'])
        self.assertFalse(self.h.offers)

    async def test_phone_without_pk_can_decline(self):
        self.assertTrue((await self.submit({'id':self.r.id,'ok':False}))['ok'])
        self.assertFalse(self.h.offers)

    @staticmethod
    def pk_record():
        private = ed25519.Ed25519PrivateKey.generate()
        spki = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return private, {'id':wire.b64u(b'synthetic-credential'), 'alg':-8, 'spki':wire.b64u(spki), 'rp':'m.agentj.app','at':1}

    async def test_real_uv_assertion_required(self):
        private, record = self.pk_record()
        self.st.set_passkey(self.phone.pub, record)
        ask = self.h.pkpeer.ask_msg(self.phone, self.r)
        self.assertNotIn('bootstrap', ask)
        cd = json.dumps({'type':'webauthn.get','challenge':ask['fa_ch'],'origin':'https://m.agentj.app'}).encode()
        ad = hashlib.sha256(b'm.agentj.app').digest()+bytes([passkey.FLAG_UP|passkey.FLAG_UV])+bytes(4)
        signature = private.sign(ad+hashlib.sha256(cd).digest())
        fa = {'id':record['id'], 'cd':wire.b64u(cd), 'ad':wire.b64u(ad), 'sig':wire.b64u(signature)}
        self.assertTrue((await self.submit({'id':self.r.id,'ok':True,'fa':fa}))['ok'])
        self.assertEqual(len(self.h.offers), 1)

    async def test_signed_assertion_without_uv_is_rejected(self):
        private, record = self.pk_record()
        self.st.set_passkey(self.phone.pub, record)
        ask = self.h.pkpeer.ask_msg(self.phone, self.r)
        cd = json.dumps({'type':'webauthn.get','challenge':ask['fa_ch'],'origin':'https://m.agentj.app'}).encode()
        ad = hashlib.sha256(b'm.agentj.app').digest()+bytes([passkey.FLAG_UP])+bytes(4)
        fa = {'id':record['id'], 'cd':wire.b64u(cd), 'ad':wire.b64u(ad), 'sig':wire.b64u(private.sign(ad+hashlib.sha256(cd).digest()))}
        self.assertFalse((await self.submit({'id':self.r.id,'ok':True,'fa':fa}))['ok'])
        self.assertFalse(self.h.offers)
