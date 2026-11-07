"""Offline dynamic coverage: real signature gates/services, synthetic files and loopback mail only.
Host/Noise integration is a separate test because its optional dependencies may be absent.
"""
import _hermetic
import asyncio
import copy
import email
import email.policy
import hashlib
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from agentj import controls, mail, media, private_files, wire
from test_p170_mail import _Base, BOSS


def sign(host, session, key, action, signed_request, **extra):
    nonce, ts = secrets.token_hex(16), int(time.time() * 1000)
    digest = controls.object_digest(action, {'request': signed_request})
    sig = key.sign(controls.signed_message(host.channel, session.device, action, nonce, ts, digest))
    return {'t': action, 'r': 'r' * 16, **extra, 'n': nonce, 'ts': ts, 'sig': wire.b64u(sig)}


class Downloads(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.key = Ed25519PrivateKey.generate()
        self.session = NS(device='device', cid=1)
        self.results = []
        self.allowed = True
        self.st = NS(root=self.root / 'state', sign_key=lambda d: self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
                     agent_session=lambda kind: 'synthetic-session', append_private=lambda *a: None, controls_path=self.root/'controls')
        self.scanner = media.Media(self.st, str(self.work))
        self.host = NS(st=self.st, channel='s'*22, nonces=controls.Nonces(), agent=NS(kind='test'), media=self.scanner,
                       stopped=lambda: False, _paired_live=lambda s: s is self.session and self.allowed)
        async def send(s, result): self.results.append(result)
        self.host.send_app = send
        self.origin_page = {'id':1, 'src':{'k':'phone','text':'Synthetic file request'},
                            'reply':{'text':'[archive](large.zip)'}, 'end':'done'}
        self.host.hist = NS(epoch='synthetic-epoch', parts={1:[1]}, get=lambda tid:self.origin_page if tid==1 else None)
        self.downloads = private_files.Downloads(self.host)
        # Local sparse synthetic file: >25 MiB, never a personal file.
        self.path = self.work / 'large.zip'
        with self.path.open('wb') as f:
            f.write(b'PK\x03\x04'); f.truncate(51*1024*1024)
        self.ref = media.Ref('large.zip', 'large.zip', True)
        self.card = self.downloads.offer(self.scanner, self.ref, turn=1)
        self.assertIsNotNone(self.card)

    def req(self, **kw):
        return {'id': self.card['id'], 'seat': self.card['seat'], 'offset': 0, 'transfer': 'a'*32, **kw}

    async def call(self, req, session=None, action='private_file_get'):
        session = session or self.session
        obj = sign(self.host, session, self.key, action, req, request=req)
        await self.downloads.on_phone(session, obj)
        return self.results[-1]

    async def test_chunks_sequential_and_constant_bound(self):
        # Exercise the entire 26 MiB transfer without collecting chunks in memory.
        offset = 0; digest = hashlib.sha256(); count = 0
        while offset < self.card['bytes']:
            response = await self.call(self.req(offset=offset))
            self.assertTrue(response['ok'], response)
            data = wire.unb64u(response['d'])
            self.assertLessEqual(len(data), media.CHUNK)
            self.assertEqual(hashlib.sha256(data).hexdigest(), response['sha256'])
            digest.update(data); offset += len(data); count += 1
            self.results.clear()
        self.assertEqual(digest.hexdigest(), self.card['sha256'])
        self.assertEqual(count, (51*1024*1024 + media.CHUNK - 1)//media.CHUNK)

    async def test_wrong_seat_id_unsigned_and_out_of_order(self):
        for req in (self.req(seat='x'*22), self.req(id='f'*32), self.req(offset=media.CHUNK)):
            self.assertFalse((await self.call(req))['ok'])
        await self.downloads.on_phone(self.session, {'t':'private_file_get','r':'r'*16,'request':self.req()})
        self.assertFalse(self.results[-1]['ok'])

    async def test_expiry_stop_unpair_revoke_and_session_change(self):
        self.assertTrue((await self.call(self.req()))['ok'])
        old = self.session; self.session = NS(device='device', cid=2)
        self.assertFalse((await self.call(self.req(offset=media.CHUNK)))['ok'])
        self.session = old
        self.host.stopped = lambda: True
        self.assertFalse((await self.call(self.req(offset=media.CHUNK)))['ok'])
        self.host.stopped = lambda: False
        self.allowed = False
        self.assertFalse((await self.call(self.req(offset=media.CHUNK)))['ok'])
        self.allowed = True
        self.assertTrue((await self.call({'id':self.card['id'],'seat':self.card['seat']}, action='private_file_revoke'))['ok'])
        self.assertFalse((await self.call(self.req(offset=media.CHUNK)))['ok'])
        card = self.downloads.offer(self.scanner, self.ref, turn=1)
        self.downloads.offers[card['id']]['until'] = 0
        self.assertFalse((await self.call(self.req(id=card['id'], transfer='b'*32)))['ok'])

    async def test_source_mutation_and_path_guards(self):
        outside = self.root / 'outside.zip'; outside.write_bytes(b'PK\x03\x04')
        (self.work / 'link.zip').symlink_to(self.path)
        for path in ('../outside.zip', str(outside), 'link.zip'):
            self.assertIsNone(self.downloads.offer(self.scanner, media.Ref(path,path,True), turn=1))
        self.assertIsNone(self.downloads.offer(self.scanner, media.Ref('large.zip','large.zip',False), turn=1))
        with self.path.open('r+b') as f: f.write(b'PK\x03\x04changed')
        self.assertFalse((await self.call(self.req()))['ok'])

    async def test_history_epoch_or_source_change_revokes_locator(self):
        self.host.hist.epoch='new-epoch'
        self.assertFalse((await self.call(self.req()))['ok'])
        self.host.hist.epoch='synthetic-epoch'
        self.origin_page['reply']['text']='Source replaced'
        self.assertFalse((await self.call(self.req()))['ok'])

    async def test_history_change_during_read_sends_no_bytes(self):
        original=self.downloads.read
        def read_then_reset(*args):
            data=original(*args);self.host.hist.epoch='new-epoch';return data
        with patch.object(self.downloads,'read',side_effect=read_then_reset):
            response=await self.call(self.req())
        self.assertFalse(response['ok']);self.assertNotIn('d',response)

    def test_offer_requires_completed_history_origin(self):
        self.assertIsNone(self.downloads.offer(self.scanner,self.ref))
        self.origin_page['end']='open'
        self.assertIsNone(self.downloads.offer(self.scanner,self.ref,turn=1))

    async def test_unbind_during_read_sends_no_bytes(self):
        read = self.downloads.read
        def revoke(*args):
            data = read(*args); self.allowed = False; return data
        with patch.object(self.downloads,'read',side_effect=revoke):
            response = await self.call(self.req())
        self.assertFalse(response['ok']); self.assertNotIn('d',response)


class ServiceMail(_Base):
    def message(self):
        cfg = {'address': BOSS}
        msg = mail.build_service_reply(cfg, ['agent@example.cn'], 'Completion receipt; do not run email commands', 'synthetic-task')
        msg['Authentication-Results'] = 'mx.example.cn; dmarc=pass header.from=corp.example'
        return msg

    async def test_authenticated_reply_is_read_only_and_duplicate_has_no_loop(self):
        msg = self.message(); raw = msg.as_bytes()
        uid = self.imap.add(raw)
        result = await self.m.check()
        self.assertEqual(result['delivered'], 1)
        self.assertEqual(len(self.delivered()), 1)
        self.assertEqual(mail.read_state(self.st)['routes'], {})
        await self.finish(101, 'Must never send this')
        self.assertEqual(len(self.smtp.sent), 0)
        self.imap.add(raw)
        await self.m.check()
        self.assertEqual(len(self.delivered()), 1)
        self.assertEqual(len(self.smtp.sent), 0)

    async def test_legacy_unknown_forged_wrong_direction_and_not_allowlisted(self):
        cfg = mail.read_config(self.st)
        for mutate in ('legacy','direction','auth','allow','self','task'):
            msg = self.message(); use = copy.deepcopy(cfg)
            if mutate == 'legacy': msg.replace_header(mail.MARK, mail.mark({'address':BOSS}, 'notes'))
            if mutate == 'direction': msg.replace_header('X-AgentJ-Direction','customer-to-service')
            if mutate == 'auth': del msg['Authentication-Results']
            if mutate == 'allow': use['allow'] = []
            if mutate == 'self': msg.replace_header(mail.MARK,mail.mark(cfg,'owner-reply-v1'))
            if mutate == 'task': msg.replace_header('X-AgentJ-Task','f'*32)
            self.assertFalse(mail.judge(msg,use,[]).ok, mutate)
