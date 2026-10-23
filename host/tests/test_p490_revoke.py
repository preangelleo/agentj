import _hermetic
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import credential_revoke as cr, cloud, wire, serve, remote_pair
from agentj.state import State

class Revoke(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=State(Path(self.tmp.name)/'s');self.st.init()
        self.link={'api':'https://agentj.app/api','host_id':wire.b64u(b'h'*16),'tenant':{'slug':'p490qa','name':'QA'},'linked_at':1,'last_seq':0,'via':'code'}
        cloud._write_cloud_file(self.st,self.link)
        self.sk=Ed25519PrivateKey.generate();self.now=int(time.time()*1000)
        self.q={'v':1,'host':self.link['host_id'],'seat':self.link['host_id'],'account':'p490qa','channel':self.st.config()['channel'],
                'seq':11,'nonce':wire.b64u(os.urandom(16)),'created_at':self.now,'expires_at':self.now+300000,
                'epoch':1,'fingerprints':[],'queries':[]}
    def signed(self,q):
        body=wire.b64u(json.dumps(q).encode());return {'body':body,'sig':wire.b64u(self.sk.sign((cr.CONTEXT+'\n'+body).encode()))}
    def open(self,q):
        pub=self.sk.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        return cr.snapshot(self.signed(q),self.st,11,self.now,[pub])
    def device(self,name,cred):
        pub=os.urandom(32);self.st.pair_device(pub,name,os.urandom(32));self.st.set_passkey(pub,{'id':cred,'sc':0})
        return wire.device_id(pub),pub
    def host(self):
        h=serve.Host(self.st,events='jsonl',read_stdin=False)
        h._drop_device=AsyncMock();return h
    async def test_signature_target_ttl_and_epoch_binding(self):
        self.assertEqual(self.open(self.q),self.q)
        for k,v in [('host','wrong'),('seat','wrong'),('account','wrong'),('channel','wrong'),('seq',10),('expires_at',self.now),('epoch',True),('queries',[{'id':'bad','fingerprints':[]}])]:
            with self.subTest(k=k):self.assertIsNone(self.open({**self.q,k:v}))
        env=self.signed(self.q);env['sig']=wire.b64u(os.urandom(64))
        self.assertIsNone(cr.snapshot(env,self.st,11,self.now,[]))
    async def test_targeted_revoke_restore_ticket_sessions_new_remote_survives_restart(self):
        cred=wire.b64u(os.urandom(32));old,pub=self.device('Mac old',cred)
        fresh,newpub=self.device('iPhone new',wire.b64u(os.urandom(32)))
        h=self.host();q={**self.q,'fingerprints':['m.agentj.app:'+cr.fingerprint(cred)]}
        await h.credential_revocations.apply(q)
        self.assertFalse(self.st.is_allowed(pub));self.assertTrue(self.st.is_allowed(newpub))
        self.assertIsNone(self.st.passkey_of(cred));self.assertIsNone(self.st.passkey_restore(old,cred,os.urandom(32),os.urandom(32)))
        self.assertNotIn(old,State(self.st.root).devices());self.assertIn(fresh,State(self.st.root).devices())
        self.assertFalse(self.st.set_passkey(newpub,{'id':cred}))
        h._drop_device.assert_awaited_with(old,'credential_revoked',True)
        await h.credential_revocations.apply(q)
        self.assertIn(fresh,self.st.devices())
        self.assertEqual(h._drop_device.await_count,1)
    async def test_crash_after_journal_before_allowlist_write_is_fail_closed(self):
        cred=wire.b64u(os.urandom(32));old,pub=self.device('old',cred);h=self.host()
        original=self.st.write_private
        def write(path,data):
            if path==self.st.devices_path:raise OSError('synthetic crash')
            original(path,data)
        with patch.object(self.st,'write_private',side_effect=write):
            with self.assertRaises(OSError):await h.credential_revocations.apply({**self.q,'fingerprints':['m.agentj.app:'+cr.fingerprint(cred)]})
        self.assertNotIn(old,State(self.st.root).devices())
        self.assertEqual(cr.read(State(self.st.root)).get('ack_epoch',0),0,'crash must not ACK received epoch')
        await h.credential_revocations.apply({**self.q,'fingerprints':['m.agentj.app:'+cr.fingerprint(cred)]})
        self.assertFalse(self.st.is_allowed(pub))
        self.assertEqual(cr.read(State(self.st.root))['ack_epoch'],1)
    async def test_replaced_passkey_cannot_escape_targeted_revocation_after_restore(self):
        leaked=wire.b64u(os.urandom(32));old,pub=self.device('old',leaked)
        fresh,fp=self.device('new safe',wire.b64u(os.urandom(32)))
        restored=os.urandom(32)
        did=self.st.passkey_restore(old,leaked,restored,os.urandom(32))
        self.assertTrue(self.st.set_passkey(restored,{'id':wire.b64u(os.urandom(32)),'rp':'m.agentj.app'}))
        h=self.host();await h.credential_revocations.apply({**self.q,'fingerprints':['m.agentj.app:'+cr.fingerprint(leaked)]})
        self.assertFalse(self.st.is_allowed(restored))
        self.assertTrue(self.st.is_allowed(fp))
        h._drop_device.assert_awaited_with(did,'credential_revoked',True)
        self.assertNotIn(did,State(self.st.root).devices())

    async def test_unknown_fingerprint_does_not_revoke_normal_and_epoch_cannot_regress(self):
        did,pub=self.device('normal',wire.b64u(os.urandom(32)));h=self.host()
        q={**self.q,'fingerprints':['m.agentj.app:'+cr.fingerprint(wire.b64u(os.urandom(32)))]}
        await h.credential_revocations.apply(q);self.assertTrue(self.st.is_allowed(pub))
        for bad in [{**q,'epoch':0},{**q,'fingerprints':[]}]:
            with self.assertRaises(ValueError):await h.credential_revocations.apply(bad)
    async def test_epoch_invalidates_inflight_pair_and_pending_approval(self):
        h=self.host();fut=asyncio.get_running_loop().create_future()
        from types import SimpleNamespace
        h.asks['a']=SimpleNamespace(fut=fut)
        await h.credential_revocations.apply(self.q)
        self.assertEqual(fut.result()[0],'deny')
        q={'host':self.link['host_id'],'account':'p490qa','expires_at':self.now+100000}
        self.assertFalse(h.remote_pair.allowed(q))
        self.assertTrue(h.remote_pair.allowed({**q,'epoch':1}))
    async def test_uuid_and_base64_are_same_fingerprint(self):
        self.assertEqual(cr.fingerprint('00000000-0000-0000-0000-000000000000'),cr.fingerprint(wire.b64u(bytes(16))))

class Rotation(Revoke):
    async def control(self,h,s,sk,action,epoch=0,keep=None,fa=True):
        from agentj import controls,passkey
        target={'epoch':epoch,'keep':keep or []};n=os.urandom(16).hex();ts=int(time.time()*1000)
        sig=wire.b64u(sk.sign(controls.signed_message(h.channel,s.device,action,n,ts,controls.object_digest(action,target))))
        obj={'t':action,'r':'p490-test','n':n,'ts':ts,'sig':sig,**target}
        if fa: obj['fa']={'id':self.st.passkey_of_device(s.device)['id'],'cd':'e30','ad':'AA','sig':'AA'}
        with patch.object(passkey,'verify_assertion',return_value=0):await h.security_rotation.on_phone(s,obj)
        return h._ctl_res.call_args.kwargs
    def session(self,h,name):
        pk=Ed25519PrivateKey.generate();pub=os.urandom(32);did=wire.device_id(pub)
        self.st.pair_device(pub,name,pk.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))
        self.st.set_passkey(pub,{'id':wire.b64u(os.urandom(32)),'rp':'m.agentj.app'})
        s=serve.Session(cid=len(h.sessions)+1,state='ready',pub=pub,device=did,name=name,mode='resume')
        h.sessions[s.cid]=s;return s,pk
    async def test_new_pair_verify_retire_preserves_both_safe_devices(self):
        from agentj.security_rotation import read
        h=self.host();h._ctl_res=AsyncMock();old,sk=self.session(h,'old Mac')
        await self.control(h,old,sk,'security_begin');j=read(self.st);epoch=j['started_at']
        await self.control(h,old,sk,'security_verify',epoch);self.assertEqual(h._ctl_res.call_args.kwargs['why'],'new_pairing_required')
        phone,pkey=self.session(h,'new iPhone');mac,mkey=self.session(h,'new Mac')
        await self.control(h,phone,pkey,'security_retire',epoch,[phone.device]);self.assertEqual(h._ctl_res.call_args.kwargs['why'],'verified_channel_required')
        await self.control(h,phone,pkey,'security_verify',epoch)
        await self.control(h,mac,mkey,'security_verify',epoch)
        await self.control(h,phone,pkey,'security_retire',epoch,[phone.device,mac.device])
        self.assertFalse(self.st.is_allowed(old.pub));self.assertTrue(self.st.is_allowed(phone.pub));self.assertTrue(self.st.is_allowed(mac.pub))
        self.assertTrue(read(State(self.st.root))['done'])
        # A later cloud snapshot must retain the local tombstones, rather than resurrecting the old credential.
        await h.credential_revocations.apply(self.q)
        self.assertFalse(self.st.is_allowed(old.pub));self.assertTrue(self.st.is_allowed(phone.pub))
    async def test_restored_old_remote_cannot_be_verified_or_escape_retirement(self):
        from agentj.security_rotation import read
        for method in ('passkey', 'ticket'):
            with self.subTest(method=method):
                h=self.host();h._ctl_res=AsyncMock();old,key=self.session(h,'old')
                await self.control(h,old,key,'security_begin',(read(self.st) or {}).get('started_at',0));epoch=read(self.st)['started_at']
                pub=os.urandom(32);sign=Ed25519PrivateKey.generate()
                raw=sign.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
                if method=='passkey':
                    did=self.st.passkey_restore(old.device,self.st.passkey_of_device(old.device)['id'],pub,raw)
                else:
                    self.st.set_ticket(old.pub,{'i':'test-ticket'})
                    did=self.st.ticket_restore(old.device,'test-ticket',pub,raw,{'i':'new-ticket'})
                # An attacker replaces the restored Passkey and reconnects (mode=resume).
                initial=self.st.passkey_of_device(did)
                self.st.set_passkey(pub,{'id':wire.b64u(os.urandom(32)),'rp':'m.agentj.app'})
                restored=serve.Session(cid=99,state='ready',pub=pub,device=did,mode='resume')
                await self.control(h,restored,sign,'security_verify',epoch)
                self.assertEqual(h._ctl_res.call_args.kwargs.get('why'),'new_pairing_required')
                fresh,fkey=self.session(h,'new safe')
                await self.control(h,fresh,fkey,'security_verify',epoch)
                self.assertNotIn('why',h._ctl_res.call_args.kwargs)
                await self.control(h,fresh,fkey,'security_retire',epoch,[fresh.device])
                self.assertNotIn('why',h._ctl_res.call_args.kwargs)
                self.assertFalse(self.st.is_allowed(pub));self.assertTrue(self.st.is_allowed(fresh.pub))
                h._drop_device.assert_awaited_with(did,'security_rotation',True)
                self.assertFalse(self.st.set_passkey(fresh.pub,initial))

    async def test_new_remote_must_reconnect_and_faceid_is_required(self):
        from agentj.security_rotation import read
        h=self.host();h._ctl_res=AsyncMock();old,sk=self.session(h,'old')
        await self.control(h,old,sk,'security_begin',fa=False)
        self.assertEqual(h._ctl_res.call_args.kwargs['why'],'passkey_required');self.assertIsNone(read(self.st))
        await self.control(h,old,sk,'security_begin');epoch=read(self.st)['started_at']
        new,key=self.session(h,'new');new.mode='pair'
        await self.control(h,new,key,'security_verify',epoch)
        self.assertEqual(h._ctl_res.call_args.kwargs['why'],'new_pairing_required')
    async def test_inflight_sensitive_card_cancelled_before_execution(self):
        from types import SimpleNamespace
        import threading
        h=self.host();future=asyncio.get_running_loop().create_future();cancel=threading.Event()
        h.elevate=SimpleNamespace(cards={'pending':{'fut':future,'cancel':cancel}})
        await h.credential_revocations.apply(self.q)
        self.assertEqual(future.result()['result'],'denied');self.assertTrue(cancel.is_set())

class LiveDrop(Revoke):
    async def test_real_drop_removes_all_sessions_and_tickets_only_for_target(self):
        from unittest.mock import Mock
        cred=wire.b64u(os.urandom(32));old,pub=self.device('old',cred);new,npub=self.device('new',wire.b64u(os.urandom(32)))
        self.st.set_ticket(pub,{'id':'synthetic-ticket'})
        h=self.host();h._drop_device=serve.Host._drop_device.__get__(h);h._op=AsyncMock();h.uploads.device_gone=Mock()
        for cid in [1,2]:h.sessions[cid]=serve.Session(cid=cid,state='ready',pub=pub,device=old)
        h.sessions[3]=serve.Session(cid=3,state='ready',pub=npub,device=new)
        await h.credential_revocations.apply({**self.q,'fingerprints':['m.agentj.app:'+cr.fingerprint(cred)]})
        self.assertEqual(set(h.sessions),{3});self.assertIsNone(self.st.ticket_of('synthetic-ticket'))
        h.uploads.device_gone.assert_called_once_with(old)
    async def test_wrong_rp_never_revokes_same_public_credential(self):
        cred=wire.b64u(os.urandom(32));did,pub=self.device('normal',cred);h=self.host()
        await h.credential_revocations.apply({**self.q,'fingerprints':['alpha-web.agentjarvis.net:'+cr.fingerprint(cred)]})
        self.assertTrue(self.st.is_allowed(pub))

class Inflight(Revoke):
    async def test_account_pair_inserted_but_not_finished_dies_with_epoch(self):
        h=self.host();did,pub=self.device('inflight account grant',wire.b64u(os.urandom(32)))
        # The grant is suspended after State.pair_device and still belongs to the active account Pairing.
        remote={'epoch':0};p=serve.Pairing(os.urandom(16),os.urandom(32),time.time()+60,time.monotonic()+60,None,remote=remote)
        p.cid=7;h.pairing=p;h.sessions[7]=serve.Session(cid=7,state='ready',pub=pub,device=did)
        await h.credential_revocations.apply(self.q)
        self.assertFalse(self.st.is_allowed(pub));self.assertIsNone(h.pairing)

class Rollback(Revoke):
    async def test_downgrade_guard_preserves_security_state_and_never_runs_installer(self):
        from agentj import update
        from unittest.mock import Mock
        wheel=self.st.root/'old.whl';wheel.write_bytes(b'isolated-wheel')
        import hashlib
        wheel.with_suffix('.verified.json').write_text(json.dumps({'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()}))
        await self.host().credential_revocations.apply(self.q)
        before=(self.st.root/'credential_revocations.json').read_bytes()
        run=Mock(return_value=__import__('subprocess').CompletedProcess([],0,stdout='0.25.4a1'))
        with patch('agentj.auto_update.install_commands',return_value=[['synthetic-install']]), patch.object(update,'new_argv',return_value=['synthetic-version']):
            self.assertFalse(update.roll_back(self.st,{},wheel,'0.25.4a1',run))
        run.assert_not_called()
        self.assertEqual((self.st.root/'credential_revocations.json').read_bytes(),before)

    async def test_automatic_failed_upgrade_cannot_roll_back_to_revocation_unaware_host(self):
        from test_p83_auto_update import Worker
        from agentj import auto_update as au
        case=Worker('test_upgrade_failed_restores_cached_old_wheel');case.setUp()
        try:
            case.st.write_private(case.st.root/'credential_revocations.json',json.dumps({'epoch':1,'fingerprints':[],'devices':[]}).encode())
            import test_p83_auto_update as fixture
            # Explicitly model the revocation-unaware old release, not this candidate.
            with patch.object(fixture,'__version__','0.25.4a1'), patch.object(au,'__version__','0.25.4a1'):
                r=case.run_worker(fail=True)
            self.assertEqual(case.installed,['99.0.0a1'])
            self.assertEqual(r['reason'],'rollback_failed')
            self.assertNotEqual(r['result'],'rolled_back')
            self.assertEqual(au.read(case.st,au.JOB)['diagnostic']['failure'],'uv_failed')
        finally:case.doCleanups()


class Freshness(Revoke):
    async def sync_response(self, h, offset=0, elapsed=0, replay=None):
        from types import SimpleNamespace
        from unittest.mock import Mock
        clock = SimpleNamespace(time=lambda: (self.now + offset) / 1000,
                                monotonic=Mock(return_value=100.0))
        def poll(link, seq):
            clock.monotonic.return_value = 100.0 + elapsed
            q = replay or {**self.q, 'seq': seq}
            return 200, {'snapshot': self.signed(q)}
        pub = self.sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        with patch.object(cr, 'time', clock), patch.object(h.credential_revocations, 'poll', side_effect=poll), patch.object(cr.notices, 'official_keys', return_value=[pub]):
            return await h.credential_revocations.sync()

    async def test_signed_current_request_accepts_wall_clock_skew(self):
        for offset in (-30000, 0, 30000, -600000, 600000):
            with self.subTest(offset=offset):
                self.assertTrue(await self.sync_response(self.host(), offset=offset))
                self.assertEqual(cr.read(self.st)['ack_epoch'], 1)

    async def test_late_signed_response_fails_before_apply(self):
        h = self.host()
        with patch.object(h.credential_revocations, 'apply', new_callable=AsyncMock) as apply:
            with self.assertRaises((ValueError, TimeoutError)):
                await self.sync_response(h, elapsed=12.0)
            apply.assert_not_awaited()
        self.assertEqual(cr.read(self.st)['epoch'], 0)
        self.assertTrue(await self.sync_response(h), 'a fresh retry may succeed')

    async def test_transport_timeout_fails_closed_and_retry_advances_sequence(self):
        h = self.host()
        with patch('agentj.reporter.in_daemon_thread', new_callable=AsyncMock, side_effect=TimeoutError), patch.object(h.credential_revocations, 'apply', new_callable=AsyncMock) as apply:
            with self.assertRaises(TimeoutError):
                await h.credential_revocations.sync()
            apply.assert_not_awaited()
        failed = json.loads((self.st.root/'credential_revoke_seq.json').read_text())
        self.assertTrue(await self.sync_response(h))
        self.assertGreater(json.loads((self.st.root/'credential_revoke_seq.json').read_text()), failed)

    async def test_restart_clock_rollback_and_old_signed_response_rejected(self):
        h = self.host()
        self.assertTrue(await self.sync_response(h))
        old = json.loads((self.st.root/'credential_revoke_seq.json').read_text())
        replay = {**self.q, 'seq': old}
        restarted = serve.Host(State(self.st.root), events='jsonl', read_stdin=False)
        restarted._drop_device = AsyncMock()
        with patch.object(restarted.credential_revocations, 'apply', new_callable=AsyncMock) as apply:
            with self.assertRaises(ValueError):
                await self.sync_response(restarted, offset=-30000, replay=replay)
            apply.assert_not_awaited()
        self.assertGreater(json.loads((self.st.root/'credential_revoke_seq.json').read_text()), old)
        self.assertTrue(await self.sync_response(restarted, offset=-30000))


    async def test_sequence_survives_real_process_restart_with_clock_rollback(self):
        import subprocess
        import sys
        code = r"""
import asyncio, json, sys, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import credential_revoke as cr, serve, wire
from agentj.state import State
q = json.loads(sys.stdin.read())
sk = Ed25519PrivateKey.generate()
pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
st = State(Path(sys.argv[1]))
h = serve.Host(st, events='jsonl', read_stdin=False)
def poll(link, seq):
    body = wire.b64u(json.dumps({**q, 'seq': seq}).encode())
    return 200, {'snapshot': {'body': body, 'sig': wire.b64u(sk.sign((cr.CONTEXT+'\n'+body).encode()))}}
clock = SimpleNamespace(time=lambda: int(sys.argv[2])/1000, monotonic=time.monotonic)
with patch.object(cr, 'time', clock), patch.object(h.credential_revocations, 'poll', side_effect=poll), patch.object(cr.notices, 'official_keys', return_value=[pub]):
    assert asyncio.run(h.credential_revocations.sync())
print(json.loads((st.root/'credential_revoke_seq.json').read_text()))
"""
        values = []
        for offset in (30000, -30000):
            result = subprocess.run([sys.executable, '-c', code, str(self.st.root), str(self.now+offset)],
                                    input=json.dumps(self.q), text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            values.append(int(result.stdout.strip()))
        self.assertGreater(values[1], values[0])
