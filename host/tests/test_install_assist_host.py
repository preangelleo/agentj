"""Offline adversarial installation boundary tests; no real State or installer."""
import asyncio
import json
import time
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from agentj import controls, wire, install_assist, first_run

class FakeState:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.allowed = True
        self.records = []
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'setup').mkdir()
        (self.root / 'setup/checklist.json').write_text(json.dumps(first_run._blank(self)))
    def exists(self):
        return False
    def sign_key(self, did):
        return self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) if self.allowed and did == 'owner' else None
    def is_allowed(self, pub):
        return self.allowed and pub == b'paired'
    def append_private(self, path, line):
        self.records.append(json.loads(line))

class FakeHost:
    def __init__(self):
        self.st = FakeState()
        self.channel = 'synthetic-channel'
        self.nonces = controls.Nonces()
        self.sessions = {}
        self.halt = False
        self.revision = 1
    def stopped(self):
        return self.halt

class Boundary(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = FakeHost()
        self.s = SimpleNamespace(device='owner', pub=b'paired', cid='cid', state='ready')
        self.h.sessions['cid'] = self.s
        self.runner = install_assist.Runner(self.h)
    def tearDown(self):
        self.h.st.tmp.cleanup()
    def request(self, op='ai.plan', params=None, **kw):
        obj = dict(item='ai', op=op, params=params or {}, rev=1)
        obj.update(kw)
        n = '%032x' % (len(self.h.nonces.seen) + 1)
        ts = int(time.time() * 1000)
        digest = controls.object_digest('setup_run', obj)
        obj.update(n=n, ts=ts, sig=wire.b64u(self.h.st.key.sign(controls.signed_message(self.h.channel, 'owner', 'setup_run', n, ts, digest))))
        return obj
    async def test_signed_plan_and_replay(self):
        obj = self.request()
        self.assertTrue((await self.runner.run(self.s, obj))['ok'])
        self.assertEqual((await self.runner.run(self.s, obj))['why'], 'replay')
    async def test_tampered_params(self):
        obj = self.request('ai.install', {'cli':'claude', 'source':'official'})
        obj['params']['source'] = 'mirror'
        self.assertEqual((await self.runner.run(self.s, obj))['why'], 'bad_signature')
    async def test_unpaired_disconnected_stopped(self):
        self.h.st.allowed = False
        self.assertEqual((await self.runner.run(self.s, self.request()))['why'], 'no_device')
        self.h.st.allowed = True
        self.h.sessions.clear()
        self.assertEqual((await self.runner.run(self.s, self.request()))['why'], 'no_device')
        self.h.sessions['cid'] = self.s
        self.h.halt = True
        self.assertEqual((await self.runner.run(self.s, self.request()))['why'], 'stopped')
    async def test_fixed_schema_no_url_shell_secret_or_wrong_item(self):
        for param in ('url','shell','path','key'):
            self.assertEqual((await self.runner.run(self.s, self.request(params={param:'CANARY'})))['why'], 'unknown_field')
        self.assertEqual((await self.runner.run(self.s, self.request(item='mail')))['why'], 'not_for_item')
    async def test_missing_backend_fails_closed(self):
        self.assertEqual((await self.runner.run(self.s, self.request('ai.install', {'cli':'claude'})))['why'], 'unavailable')
    async def test_forged_installer_metadata_never_grants(self):
        self.s.role = 'install_assistant'
        self.s.expires_at = time.time() + 9999
        result = self.runner.assist.status()
        self.assertFalse(result['available'])
        self.assertEqual(result['why'], 'cloud_qualification_unavailable')
        with self.assertRaises(install_assist.Unavailable):
            self.runner.assist.activate({'role':'install_assistant', 'owner':'owner', 'signed':True})
    async def test_metadata_does_not_echo_error_or_params(self):
        await self.runner.run(self.s, self.request(params={'key':'CANARY'}))
        self.assertNotIn('CANARY', json.dumps(self.runner.events))

class Lease(unittest.TestCase):
    def test_expiry_disconnect_owner_kick_handover(self):
        for why in ('owner_kick','cancel','disconnected','stopped'):
            lease = install_assist.Lease('helper', 'owner', 10)
            self.assertTrue(lease.live(11))
            lease.end(why)
            self.assertFalse(lease.live(11))
        lease = install_assist.Lease('helper', 'owner', 10)
        self.assertFalse(lease.live(10 + 3*3600))
        lease = install_assist.Lease('helper', 'owner', 10)
        lease.handover(20)
        self.assertTrue(lease.live(21))
        self.assertFalse(lease.live(20 + install_assist.CLOSE_DELAY))
    def test_capabilities_are_explicit_and_secrets_owner_only(self):
        self.assertNotIn('ai.key', install_assist.INSTALLER_OPS)
        self.assertNotIn('terminal', install_assist.INSTALLER_OPS)
        self.assertNotIn('pair_new', install_assist.INSTALLER_OPS)

class RunningBoundary(unittest.IsolatedAsyncioTestCase):
    setUp = Boundary.setUp
    tearDown = Boundary.tearDown
    request = Boundary.request
    async def test_rev_change_fails_before_operation(self):
        record = json.loads((self.h.st.root / 'setup/checklist.json').read_text())
        record['revision'] = 2
        (self.h.st.root / 'setup/checklist.json').write_text(json.dumps(record))
        self.assertEqual((await self.runner.run(self.s, self.request()))['why'], 'changed')

    async def test_running_disconnect_and_estop_cancel_task(self):
        for reason in ('disconnect', 'estop'):
            started, cancelled = asyncio.Event(), asyncio.Event()
            async def slow(s, op, params):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            self.runner._operation = slow
            pending = asyncio.create_task(self.runner.run(self.s, self.request()))
            await started.wait()
            if reason == 'disconnect':
                self.h.sessions.clear()
            else:
                self.h.halt = True
            result = await pending
            self.assertEqual(result['why'], 'no_device' if reason == 'disconnect' else 'stopped')
            self.assertTrue(cancelled.is_set())
            self.assertIsNone(self.runner.active)
            self.h.sessions['cid'] = self.s
            self.h.halt = False

    async def test_timeout_and_exception_redaction(self):
        old = install_assist.TIMEOUT['ai.plan']
        async def slow(s, op, params):
            await asyncio.Event().wait()
        try:
            install_assist.TIMEOUT['ai.plan'] = .001
            self.runner._operation = slow
            self.assertEqual((await self.runner.run(self.s, self.request()))['why'], 'timeout')
        finally:
            install_assist.TIMEOUT['ai.plan'] = old
        async def fail(s, op, params):
            raise RuntimeError('SECRET_CANARY')
        self.runner._operation = fail
        result = await self.runner.run(self.s, self.request())
        self.assertEqual(result['why'], 'error')
        self.assertNotIn('SECRET_CANARY', json.dumps(result) + json.dumps(self.runner.events))

    async def test_signed_cancel_revokes_running_operation(self):
        replies=[]
        async def respond(*args, **kwargs):
            replies.append((args, kwargs))
        self.h._ctl_res = respond
        started=asyncio.Event()
        async def slow(s, op, params):
            started.set()
            await asyncio.Event().wait()
        self.runner._operation = slow
        pending=asyncio.create_task(self.runner.run(self.s, self.request()))
        await started.wait()
        n='c'*32; ts=int(time.time()*1000)
        sig=self.h.st.key.sign(controls.signed_message(self.h.channel, 'owner', 'setup_cancel', n, ts,
                                                     controls.object_digest('setup_cancel', {})))
        obj={'t':'setup_cancel','n':n,'ts':ts,'sig':wire.b64u(sig),'r':'cancel'}
        await self.runner.on_phone(self.s,obj)
        self.assertEqual((await pending)['why'], 'denied')
        self.assertTrue(replies[0][0][-1])

    async def test_log_failure_refuses_before_effect(self):
        async def effect(*args):
            self.fail('effect after audit failed')
        self.runner._operation=effect
        def broken(*args):
            raise OSError('PRIVATE_CANARY')
        self.h.st.append_private=broken
        result=await self.runner.run(self.s,self.request())
        self.assertEqual(result['why'],'io')
        self.assertNotIn('PRIVATE_CANARY', str(result))
