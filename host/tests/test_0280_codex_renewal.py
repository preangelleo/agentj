"""Offline renewal boundary: synthetic auth only, all files under TMPDIR."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agentj import accounts as ac


class Renewal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'native'; self.home.mkdir()
        self.old = self.auth('one', 'old'); self.new = self.auth('one', 'new')
        (self.home / 'auth.json').write_bytes(self.old)
        self.item = {'id': '11111111', 'name': 'Original', 'method': 'device',
                     'fp': ac.codex_fp(self.old), 'added': 1, 'last_quota': None}
        self.doc = ac._blank(); self.doc['harness']['codex'].update(
            active=self.item['id'], switched=1, items=[self.item])
        ac._save(self.root, self.doc)
        fd = ac._open_dir(self.root, (ac.DIR, 'codex', self.item['id']))
        try: ac._write_at(fd, 'auth.json', self.old)
        finally: os.close(fd)
        self.p = patch.object(ac, 'codex_home', return_value=self.home)
        self.p.start(); self.addCleanup(self.p.stop)
        self.iid, self.slot = ac.new_codex_slot(self.root)
        (self.slot / 'auth.json').write_bytes(self.new)

    @staticmethod
    def auth(who, version):
        return json.dumps({'tokens': {'account_id': 'fake-' + who,
            'access_token': 'fake-' + version, 'refresh_token': 'fake-' + version}}).encode()

    def renew(self, live=lambda: True):
        return ac.renew_from_isolated_slot(self.root, self.iid,
            expected_active='11111111', expected_switched=1, bound_live=live)

    def unchanged(self):
        self.assertEqual((self.home / 'auth.json').read_bytes(), self.old)
        self.assertEqual((self.root / ac.DIR / 'codex' / '11111111' / 'auth.json').read_bytes(), self.old)

    def test_same_account_renewal(self):
        # Reproduce the old integration path until the narrow helper exists.
        if not hasattr(ac, 'renew_from_isolated_slot'):
            ac.commit_codex(self.root, self.iid, 'Renewal')
        result = self.renew()
        self.assertTrue(result['ok'])
        hd = ac.load(self.root)['harness']['codex']
        self.assertEqual(hd['active'], '11111111'); self.assertEqual(len(hd['items']), 1)
        self.assertEqual(hd['items'][0]['name'], 'Original')
        self.assertEqual((self.home / 'auth.json').read_bytes(), self.new)
        self.assertEqual((self.root / ac.DIR / 'codex' / '11111111' / 'auth.json').read_bytes(), self.new)

    def test_ordinary_duplicate_refused(self):
        with self.assertRaisesRegex(ac.AccountError, '^same_account$'):
            ac.commit_codex(self.root, self.iid, 'Duplicate')
        self.unchanged()

    def test_different_account_only_registers(self):
        (self.slot / 'auth.json').write_bytes(self.auth('two', 'new'))
        self.assertIsNone(self.renew())
        ac.commit_codex(self.root, self.iid, 'Different', bound_live=lambda: True)
        self.unchanged()
        hd = ac.load(self.root)['harness']['codex']
        self.assertEqual(hd['active'], '11111111'); self.assertEqual(len(hd['items']), 2)

    def test_missing_or_unknown_identity_refused(self):
        for data in (b'{}', b'', b'not-json'):
            (self.slot / 'auth.json').write_bytes(data)
            with self.assertRaises(ac.AccountError): self.renew()
            self.unchanged()

    def test_changed_active_or_switch_refused(self):
        for change in ({'active': None}, {'switched': 2}):
            doc = ac.load(self.root); doc['harness']['codex'].update(change); ac._save(self.root, doc)
            with self.assertRaisesRegex(ac.AccountError, '^cancelled$'): self.renew()
            self.unchanged()
            ac._save(self.root, self.doc)

    def test_revoked_bound_refused(self):
        # The callback includes key revocation, stop, expiry and account/RPC changes.
        with self.assertRaisesRegex(ac.AccountError, '^cancelled$'): self.renew(lambda: False)
        self.unchanged()

    def test_guard_at_both_actual_writes(self):
        for refuse_at in (2, 3):
            calls = 0
            def live():
                nonlocal calls
                calls += 1
                return calls < refuse_at
            with self.assertRaisesRegex(ac.AccountError, '^cancelled$'): self.renew(live)
            self.assertEqual(calls, refuse_at); self.unchanged()

    def test_wrong_native_or_original_slot_refused(self):
        original = self.root / ac.DIR / 'codex' / '11111111' / 'auth.json'
        for target in (self.home / 'auth.json', original):
            target.write_bytes(self.auth('wrong', 'new'))
            with self.assertRaises(ac.AccountError): self.renew()
            self.assertEqual(target.read_bytes(), self.auth('wrong', 'new'))
            target.write_bytes(self.old)
            self.unchanged()

    def test_new_registration_bound_guard(self):
        (self.slot / 'auth.json').write_bytes(self.auth('two', 'new'))
        with self.assertRaisesRegex(ac.AccountError, '^cancelled$'):
            ac.commit_codex(self.root, self.iid, 'Different', bound_live=lambda: False)
        self.unchanged()
        self.assertEqual(len(ac.load(self.root)['harness']['codex']['items']), 1)

    def test_missing_native_or_slot_never_replaces_identity(self):
        for target in (self.home / 'auth.json', self.slot / 'auth.json',
                       self.root / ac.DIR / 'codex' / '11111111' / 'auth.json'):
            previous = target.read_bytes(); target.unlink()
            with self.assertRaises(ac.AccountError): self.renew()
            self.assertFalse(target.exists())
            target.write_bytes(previous); self.unchanged()

    def test_second_write_failure_restores_native(self):
        write = ac._write_at
        calls = 0
        def failing(fd, name, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('synthetic write failure')
            return write(fd, name, data)
        with patch.object(ac, '_write_at', side_effect=failing):
            with self.assertRaises(OSError): self.renew()
        self.unchanged()



class SlotFlow(unittest.IsolatedAsyncioTestCase):
    auth = staticmethod(Renewal.auth)
    unchanged = Renewal.unchanged
    def setUp(self):
        Renewal.setUp(self)
        import asyncio
        import importlib.util
        import sys
        import threading
        import time
        import types
        from unittest.mock import AsyncMock, Mock
        N = types.SimpleNamespace
        self.stopped = False; self.key = b'fake-public-key'; self.hook = lambda: None
        self.remote = N(device='phone')
        self.host = N(st=N(root=self.root, sign_key=lambda _: self.key), channel='owner',
            hist=N(epoch=1), task_epoch=1, _task_id=lambda: 'task',
            sessions={'phone':self.remote}, _paired_live=lambda _: True, agent_notice=Mock(),
            task_grants={('task',):{'until':99,'used':{'consumed'}}, ('other',):{'until':99}},
            _approval_operation_once=object(), nonces=object(), goals=object(),
            preferences={'approval':{'level':'signed'}})
        self.agent = N(host=self.host, owns_harness=lambda:True, halting=False, proc=object(),
            tid='thread', cur_send=None, request_restart=Mock())
        self.host.agent = self.agent
        self.rescue = N(host=self.host, cancel=threading.Event(), lang=lambda:'zh',
            flow={}, broadcast=Mock())
        outer = self
        class FakeFlow:
            def __init__(self, home, bound_live): self.home=home; self.expires=600
            def start(self): return 'https://example.invalid/device', 'FAKE-CODE', 600
            def wait(self, cancel):
                (self.home/'auth.json').write_bytes(outer.auth('one', 'new'))
                outer.hook()
                return 'ok'
            def close(self, restore=False): pass
        package = types.ModuleType('renewal_fixture'); package.__path__=[]
        ar = N(CodexFlow=FakeFlow, RescueError=type('RescueError',(Exception,),{}),
            HEALTH_TIMEOUT=60, health_check=Mock(return_value={'ok':True}))
        modules = {'renewal_fixture':package, 'renewal_fixture.accounts':ac,
            'renewal_fixture.auth_rescue':ar,
            'renewal_fixture.controls':N(estop_state=lambda _: {'on':self.stopped})}
        patcher = patch.dict(sys.modules, modules); patcher.start(); self.addCleanup(patcher.stop)
        def load(name):
            spec=importlib.util.spec_from_file_location('renewal_fixture.'+name,
                Path(__file__).parents[1]/'agentj'/(name+'.py'))
            mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod
            self.addCleanup(sys.modules.pop,spec.name,None); spec.loader.exec_module(mod)
            setattr(package,name,mod); return mod
        self.q=load('quota_refresh'); self.lf=load('login_flow'); self.ar=ar
        self.live=self.q.owner_bound(self.host,self.remote,self.agent)
        self.live.verify_account=AsyncMock(return_value=True)
        self.login=self.lf.Login(self.rescue)
        self.login.active={'bound_live':self.live, 'cancel':threading.Event(),
            'deadline':time.monotonic()+60,'device':'phone','r':'request','shown':{}}
        self.host._send_device=AsyncMock()

    async def test_flow_renews_and_only_schedules_owned_next_message(self):
        ledger, nonces, goals = self.host._approval_operation_once, self.host.nonces, self.host.goals
        result=await self.login._codex_slot()
        self.assertTrue(result['ok']); self.assertNotIn('pending_binding',result)
        self.assertEqual(result['live'],'NOT_VERIFIED')
        self.agent.request_restart.assert_called_once_with('auth_rescue')
        self.assertTrue(self.live())
        self.assertEqual((self.home/'auth.json').read_bytes(), self.new)
        self.assertEqual(self.host.task_grants[('task',)],{'until':0,'used':{'consumed'}})
        self.assertEqual(self.host.task_grants[('other',)],{'until':99})
        self.assertIs(self.host._approval_operation_once,ledger); self.assertIs(self.host.nonces,nonces)
        self.assertIs(self.host.goals,goals); self.assertEqual(self.host.preferences['approval']['level'],'signed')
        self.assertEqual(len(ac.load(self.root)['harness']['codex']['items']),1)

    async def test_key_stop_expiry_account_switch_and_owner_changes(self):
        import time
        for change in ('key','stop','expiry','active','switched','shared'):
            with self.subTest(change=change):
                def hook():
                    if change=='key': self.key=b'revoked'
                    elif change=='stop': self.stopped=True
                    elif change=='expiry': self.login.active['deadline']=time.monotonic()-1
                    elif change=='shared': self.agent.owns_harness=lambda:False
                    else:
                        doc=ac.load(self.root)
                        doc['harness']['codex'][change]=None if change=='active' else 2
                        ac._save(self.root,doc)
                self.hook=hook
                result=await self.login._codex_slot()
                self.assertFalse(result['ok']); self.unchanged()
                self.agent.request_restart.assert_not_called()
                self.key=b'fake-public-key'; self.stopped=False
                self.login.active['deadline']=time.monotonic()+60
                self.agent.owns_harness=lambda:True
                ac._save(self.root,self.doc)

    async def test_rpc_account_change_and_health_failure_preserve_active(self):
        self.live.verify_account.return_value=False
        self.assertFalse((await self.login._codex_slot())['ok']); self.unchanged()
        self.live.verify_account.return_value=True
        self.ar.health_check.return_value={'ok':False}
        self.assertFalse((await self.login._codex_slot())['ok']); self.unchanged()
        self.agent.request_restart.assert_not_called()

    async def test_different_account_flow_requires_phone_selection(self):
        original_wait=self.ar.CodexFlow.wait
        def wait(flow,cancel):
            result=original_wait(flow,cancel)
            (flow.home/'auth.json').write_bytes(self.auth('two','new'))
            return result
        self.ar.CodexFlow.wait=wait
        result=await self.login._codex_slot()
        self.assertTrue(result['ok']); self.assertTrue(result['pending_binding'])
        self.unchanged(); self.agent.request_restart.assert_not_called()
        hd=ac.load(self.root)['harness']['codex']
        self.assertEqual(hd['active'],'11111111'); self.assertEqual(len(hd['items']),2)

    async def test_bound_change_immediately_before_accounts_commit(self):
        original=ac.renew_from_isolated_slot
        def changed(*args,**kwargs):
            self.key=b'revoked-at-commit'
            return original(*args,**kwargs)
        with patch.object(ac,'renew_from_isolated_slot',side_effect=changed):
            result=await self.login._codex_slot()
        self.assertFalse(result['ok']); self.unchanged()
        self.agent.request_restart.assert_not_called()


class DefaultAndExternalWriter(unittest.TestCase):
    setUp = Renewal.setUp
    auth = staticmethod(Renewal.auth)
    renew = Renewal.renew

    def test_default_single_account_without_registry(self):
        ac._save(self.root, ac._blank())
        before = ac.load(self.root)
        result = ac.renew_from_isolated_slot(self.root, self.iid,
            expected_active=None, expected_switched=None, bound_live=lambda:True)
        self.assertTrue(result['renewed'])
        self.assertIsNone(result['id'])
        self.assertEqual(ac.load(self.root), before)
        self.assertEqual((self.home/'auth.json').read_bytes(), self.new)
        self.assertEqual((self.slot/'auth.json').read_bytes(), self.new)

    def test_default_wrong_fp_never_changes_native(self):
        ac._save(self.root, ac._blank())
        (self.slot/'auth.json').write_bytes(self.auth('other','new'))
        result = ac.renew_from_isolated_slot(self.root, self.iid,
            expected_active=None, expected_switched=None, bound_live=lambda:True)
        self.assertIsNone(result)
        self.assertEqual((self.home/'auth.json').read_bytes(), self.old)
        self.assertIsNone(ac.load(self.root)['harness']['codex']['active'])

    def test_failed_second_write_does_not_rollback_an_external_replacement(self):
        write = ac._write_at
        external = self.auth('external','current')
        for same_bytes in (False, True):
            (self.home/'auth.json').write_bytes(self.old)
            calls = 0
            replaced_inode = []
            def failing(fd,name,data):
                nonlocal calls
                calls += 1
                if calls == 2:
                    replacement = self.home/'external-replacement'
                    replacement.write_bytes(self.new if same_bytes else external)
                    replacement.replace(self.home/'auth.json')
                    replaced_inode.append((self.home/'auth.json').stat().st_ino)
                    raise OSError('synthetic second-write failure')
                return write(fd,name,data)
            with patch.object(ac,'_write_at',side_effect=failing):
                with self.assertRaises(OSError):
                    self.renew()
            self.assertEqual((self.home/'auth.json').stat().st_ino,replaced_inode[0])
            self.assertEqual((self.home/'auth.json').read_bytes(), self.new if same_bytes else external)
            self.assertEqual((self.root/ac.DIR/'codex'/'11111111'/'auth.json').read_bytes(),self.old)
