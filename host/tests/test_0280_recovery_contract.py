"""Offline contract: real recovery source, fake RPC/registry/control boundaries only."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

N = types.SimpleNamespace
class RPCError(Exception):
    def __init__(self, err): self.err = err

class Contract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.meta = {'harness': {'codex': {'active': 'a', 'switched': 1}}}
        package = types.ModuleType('recovery_fixture'); package.__path__ = []
        self.modules = {'recovery_fixture': package,
            'recovery_fixture.accounts': N(load=lambda _: self.meta),
            'recovery_fixture.controls': N(estop_state=lambda _: {'on': False}),
            'recovery_fixture.agent_codex': N(RPCError=RPCError)}
        self.p = patch.dict(sys.modules, self.modules); self.p.start(); self.addCleanup(self.p.stop)
        spec = importlib.util.spec_from_file_location('recovery_fixture.quota_refresh',
            Path(__file__).parents[1] / 'agentj' / 'quota_refresh.py')
        self.q = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.q)
        sys.modules[spec.name] = self.q; package.quota_refresh = self.q
        self.remote = N(device='phone', cid='connection', pub=b'public')
        self.h = N(channel='owner', sessions={'connection': self.remote}, _paired_live=lambda _: True,
            st=N(root='unused', sign_key=lambda _: b'public'), hist=N(epoch=1), task_epoch=1,
            agent_cfg={'dir': '/fake', 'profile': 'p'}, _task_id=lambda: 'task',
            task_grants={('task',): {'used': {'consumed'}}, ('unrelated',): {'used': {'other'}}},
            _approval_operation_once=object(), preferences={'approval': {'level': 'signed'}}, goals=object())
        self.a = N(host=self.h, cfg=self.h.agent_cfg, tid='thread', proc=object(), task=object(),
            owns_harness=lambda: True, halting=False, _quota_evidence=False, turn_id=None, turn_status={},
            cur_send=N(device='phone', sid='sid', turn=7, source={'kind': 'phone', 'role': 'owner'}, state='delivering'),
            call=AsyncMock(), request_restart=Mock())
        self.h.agent = self.a
        self.account = {'account': {'type': 'chatgpt', 'email': 'fake@example.invalid', 'workspaceName': None, 'planType': 'plus'}, 'requiresOpenaiAuth': True}
    def replies(self, after=None):
        self.a.call.side_effect = [RPCError({'code': 'usage_limit_reached'}), self.account,
                                  after or self.account, {'rateLimits': {}}, {'turn': {'id': 'new'}}]
    async def test_refresh_before_retry_and_preserve_consumption(self):
        ledger, goals = self.h._approval_operation_once, self.h.goals
        self.replies(); await self.q.start(self.a, 'turn/start', {})
        self.assertEqual([c.args[0] for c in self.a.call.await_args_list],
            ['turn/start', 'account/read', 'account/read', 'account/rateLimits/read', 'turn/start'])
        self.assertEqual(self.a.call.await_args_list[1].args[1], {})
        self.assertEqual(self.a.call.await_args_list[2].args[1], {'refreshToken': True})
        self.assertEqual(self.h.task_grants[('unrelated',)]['used'], {'other'})
        self.assertEqual(self.h.task_grants[('task',)]['used'], {'consumed'})
        self.assertIs(ledger, self.h._approval_operation_once); self.assertIs(goals, self.h.goals)
        self.assertEqual(self.h.preferences['approval']['level'], 'signed')
    async def test_changed_rpc_identity_never_replays(self):
        self.replies({'account': {**self.account['account'], 'email': 'other@example.invalid'}})
        with self.assertRaises(RPCError): await self.q.start(self.a, 'turn/start', {})
        self.assertEqual(sum(c.args[0] == 'turn/start' for c in self.a.call.await_args_list), 1)
    async def test_missing_identity_never_replays(self):
        self.account = {'account': {'type': 'chatgpt', 'email': None, 'workspaceName': None}}
        self.replies()
        with self.assertRaises(RPCError): await self.q.start(self.a, 'turn/start', {})
    async def test_replaced_session_and_host_agent_refused(self):
        for change in (lambda: self.h.sessions.update(connection=N(device='phone')),
                       lambda: setattr(self.h, 'agent', object()),
                       lambda: self.a.cfg.update(profile='changed'),
                       lambda: setattr(self.a.cur_send, 'source', {'kind': 'system'})):
            self.setUp(); calls = 0
            async def rpc(method, *args):
                nonlocal calls
                calls += 1
                if calls == 1: raise RPCError({'code': 'usage_limit_reached'})
                change(); return self.account
            self.a.call.side_effect = rpc
            with self.assertRaises(RPCError): await self.q.start(self.a, 'turn/start', {})
            self.assertEqual(sum(c.args[0] == 'turn/start' for c in self.a.call.await_args_list), 1)
    async def test_plan_may_change(self):
        self.replies({'account': {**self.account['account'], 'planType': 'pro'}})
        self.assertEqual(await self.q.start(self.a, 'turn/start', {}), {'turn': {'id': 'new'}})
    async def test_second_quota_no_loop(self):
        self.replies(); self.a.call.side_effect = [RPCError({'code': 'usage_limit_reached'}), self.account, self.account, {}, RPCError({'code': 'usage_limit_reached'})]
        with self.assertRaises(RPCError): await self.q.start(self.a, 'turn/start', {})
        self.assertEqual(self.a.call.await_count, 5)
    async def test_unknown_review_tool_output_disconnect_timeout(self):
        for method, evidence, error in [('review/start', False, RPCError({'code': 'usage_limit_reached'})),
                ('turn/start', True, RPCError({'code': 'usage_limit_reached'})),
                ('turn/start', False, TimeoutError()), ('turn/start', False, ConnectionError()),
                ('turn/start', False, RPCError({'code': -32601}))]:
            self.a._quota_evidence=evidence; self.a.call.reset_mock(); self.a.call.side_effect=error
            with self.assertRaises(type(error)): await self.q.start(self.a, method, {})
            self.assertEqual(self.a.call.await_count, 1)


class Phone(Contract):
    def setUp(self):
        super().setUp()
        import contextlib
        import threading
        self.jobs=[]; self.ui=[]; self.commits=[]; self.discards=[]; self.ends=[]; self.hook=lambda:None
        self.h._bg=lambda coro, label: self.jobs.append(coro)
        self.h._send_device=AsyncMock(side_effect=lambda device, message:self.ui.append(message))
        self.h.agent_notice=Mock(); self.h.st.log=Mock(); self.a.turn_done=None
        self.a.kind='codex'; self.a.call.return_value=self.account
        self.modules['recovery_fixture.provenance']=N(paired_request=lambda _:contextlib.nullcontext())
        sys.modules['recovery_fixture.provenance']=self.modules['recovery_fixture.provenance']
        root=Path(__file__).parents[1] / 'agentj'
        def load(name):
            spec=importlib.util.spec_from_file_location('recovery_fixture.'+name, root/(name+'.py'))
            mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
            self.addCleanup(sys.modules.pop, spec.name, None)
            setattr(sys.modules['recovery_fixture'], name, mod)
            return mod
        self.ar=load('auth_rescue'); self.lf=load('login_flow')
        self.modules['recovery_fixture.accounts'].AccountError=type('AccountError',(Exception,),{})
        ac=self.modules['recovery_fixture.accounts']
        ac.new_codex_slot=Mock(return_value=('fixture',Path('/fake/new-slot')))
        ac.renew_from_isolated_slot=Mock(return_value=None)
        ac.commit_codex=Mock(side_effect=lambda *args,**kwargs:self.commits.append(args) or {'id':'fixture'})
        ac.discard_slot=Mock(side_effect=lambda *args:self.discards.append(args))
        outer=self
        class FakeFlow:
            def __init__(self,home=None,bound_live=None):
                outer.assertEqual(home,Path('/fake/new-slot')); self.live=bound_live; self.expires=600
            def start(self):
                outer.assertTrue(self.live()); return 'https://example.invalid/device','FAKE-CODE',600
            def wait(self,cancel): outer.hook(); return 'ok'
            def close(self,restore=False): pass
        self.RealCodexFlow=self.ar.CodexFlow
        self.ar.CodexFlow=FakeFlow; self.ar.health_check=Mock(return_value={'ok':True})
        r=N(host=self.h,flow=None,cancel=threading.Event(),lang=lambda:'zh',current=lambda:'codex',
            broadcast=Mock(),_confirm=AsyncMock(return_value=True),state={'codex':{'cls':'balance'}},
            clear=Mock(),_health_mark=Mock(),why=lambda why,**_:why)
        def end(*args): self.ends.append(args); r.flow={'stage':'done'}
        r._end=end; self.h.auth_rescue=r; r.login=self.lf.Login(r)
        r._codex=self.ar.Rescue._codex.__get__(r)
        r.login._say=lambda key,**kwargs:key
        self.addCleanup(self.close_jobs)
    def close_jobs(self):
        for coro in self.jobs: coro.close()
        self.jobs.clear()
    async def drain(self):
        while self.jobs: await self.jobs.pop(0)
    async def test_card_enters_existing_phone_device_ui(self):
        result=await self.q.recheck(self.h,self.remote,'card')
        self.assertTrue(result['ok']); await self.drain()
        self.assertTrue(any(m['view'].get('code')=='FAKE-CODE' for m in self.ui))
        self.assertEqual(len(self.commits),1); self.a.request_restart.assert_not_called()
        self.h.auth_rescue._health_mark.assert_not_called()
        self.assertIn('NOT_VERIFIED',self.ui[-1]['view']['text'])
    async def test_upgrade_text_enters_existing_phone_flow(self):
        result=await self.q.upgraded(self.h,self.remote,'升级了','text')
        self.assertTrue(result['ok']); await self.drain()
        self.assertTrue(any(m['view'].get('code')=='FAKE-CODE' for m in self.ui))
        self.assertEqual(len(self.commits),1)
    async def test_phone_bound_identity_changes_refuse_registry_commit(self):
        for change in (lambda:setattr(self.h,'channel','other'),
                       lambda:self.h.sessions.update(connection=N(device='phone')),
                       lambda:setattr(self.h.st,'sign_key',lambda _:b'changed'),
                       lambda:self.meta['harness']['codex'].update(active='other'),
                       lambda:self.a.cfg.update(profile='changed'),
                       lambda:setattr(self.h,'task_epoch',2),
                       lambda:setattr(self.a,'_quota_stop',1),
                       lambda:self.h.auth_rescue.login.active['cancel'].set()):
            self.setUp(); self.hook=change
            self.assertTrue((await self.q.recheck(self.h,self.remote,'card'))['ok'])
            await self.drain(); self.assertEqual(self.commits,[]); self.assertEqual(len(self.discards),1)
    async def test_phone_rpc_identity_change_refuses_commit(self):
        self.a.call.side_effect=[self.account, {'account':{**self.account['account'],'email':'changed@example.invalid'}}]
        self.assertTrue((await self.q.recheck(self.h,self.remote,'card'))['ok'])
        await self.drain(); self.assertEqual(self.commits,[])
    async def test_confirm_change_and_denial_do_not_start(self):
        async def confirm(*args,**kwargs): self.h.sessions.clear(); return True
        self.h.auth_rescue._confirm.side_effect=confirm
        self.assertFalse((await self.q.recheck(self.h,self.remote,'card'))['ok'])
        self.assertEqual(self.jobs,[]); self.assertEqual(self.commits,[])
    async def test_duplicate_card_refuses(self):
        self.assertTrue((await self.q.recheck(self.h,self.remote,'card'))['ok'])
        self.assertEqual((await self.q.recheck(self.h,self.remote,'card'))['why'],'replay')
        await self.drain(); self.assertEqual(len(self.commits),1)

    async def test_same_account_registration_is_honest_failure(self):
        ac=self.modules['recovery_fixture.accounts']
        ac.commit_codex.side_effect=ac.AccountError('same_account')
        self.assertTrue((await self.q.recheck(self.h,self.remote,'same'))['ok'])
        await self.drain()
        self.assertFalse(self.ui[-1]['view']['ok'])
        self.assertEqual(self.ui[-1]['view']['why'],'same_account')
        self.h.auth_rescue._health_mark.assert_not_called()
        self.assertEqual(len(self.discards),1)
    async def test_real_codex_flow_refuses_before_any_credential_read_or_cli(self):
        flow=self.RealCodexFlow(home=Path('/fake/unused'),bound_live=lambda:False)
        with patch.object(self.ar,'binary',side_effect=AssertionError('must not resolve CLI')):
            with self.assertRaises(self.ar.RescueError): flow.start()
    async def test_start_delivery_failure_releases_active_flow(self):
        self.h._send_device.side_effect=ConnectionError()
        with self.assertRaises(ConnectionError): await self.q.recheck(self.h,self.remote,'disconnect')
        self.assertIsNone(self.h.auth_rescue.login.active)
        self.assertEqual(self.jobs,[])
    async def test_cancel_during_device_wait_never_commits_and_finishes(self):
        import threading
        entered=threading.Event(); release=threading.Event()
        def wait_hook():
            entered.set(); release.wait(2)
        self.hook=wait_hook
        self.assertTrue((await self.q.recheck(self.h,self.remote,'cancel'))['ok'])
        running=asyncio.create_task(self.jobs.pop(0))
        await asyncio.to_thread(entered.wait,2)
        running.cancel(); release.set()
        with self.assertRaises(asyncio.CancelledError): await running
        self.assertEqual(self.commits,[])
        self.assertIsNone(self.h.auth_rescue.login.active)
        self.assertEqual(len(self.discards),1)

class Entrypoints(Phone):
    def method(self, filename, name, **globs):
        import ast
        path=Path(__file__).parents[1]/'agentj'/filename
        tree=ast.parse(path.read_text())
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
        module=ast.Module(body=[fn],type_ignores=[])
        scope={'__package__':'recovery_fixture','Session':N, 'asyncio':asyncio, **globs}
        exec(compile(module,str(path),'exec'),scope)
        return scope[name]
    async def test_actual_serve_card_route_enters_phone_ui(self):
        import contextlib
        self.remote.state='ready'; self.h.st.is_allowed=lambda _:True
        self.h.send_app=AsyncMock(); self.h.auth_rescue.view=lambda:{}
        fn=self.method('serve.py','on_auth',wire=N(is_rid=lambda _:True),
                       provenance=N(paired_request=lambda _:contextlib.nullcontext()))
        await fn(self.h,self.remote,'auth_start',{'r':'card','kind':'codex','route':'quota_recheck'})
        self.assertEqual(len(self.jobs),1) # relay handler returns before passkey/device wait
        await self.drain()
        self.assertTrue(any(m['view'].get('code')=='FAKE-CODE' for m in self.ui))
        self.assertEqual(len(self.commits),1)
    async def test_actual_serve_upgrade_text_enters_phone_ui(self):
        self.h.send_app=AsyncMock(); self.h.receipts=None
        self.h.sends=N(used=lambda *a:False, full=lambda *a:False,add=Mock())
        self.h.stopped=lambda:False; self.h.seat_refusal=lambda:None
        fn=self.method('serve.py','on_say',wire=N(is_id22=lambda _:True,text_problem=lambda *a:None,
            units=len,MAX_TEXT_P33=10000),compose=N(EXCERPT_MAX=1000,Send=lambda *a,**k:N()),
            meeting=N(parse=lambda _:None),slash=N(parse=lambda _:None),_rcpt_recv=AsyncMock(),_rcpt=Mock())
        # The real method's relative import resolves to the real recovery module.
        sys.modules['recovery_fixture.quota_refresh']=self.q
        setattr(sys.modules['recovery_fixture'],'quota_refresh',self.q)
        self.addCleanup(sys.modules.pop,'recovery_fixture.quota_refresh',None)
        await fn(self.h,self.remote,{'sid':'text','text':'升级了'})
        self.assertEqual(len(self.jobs),1); self.h.auth_rescue._confirm.assert_not_awaited()
        await self.drain()
        self.assertTrue(any(m['view'].get('code')=='FAKE-CODE' for m in self.ui))
        self.assertEqual(len(self.commits),1)
    async def test_actual_accepted_turn_refreshes_without_replay(self):
        self.a._ready=AsyncMock(return_value=True); self.a.persist=False
        self.a.tail_reset=Mock(); self.a.revert=None; self.a.thread={}; self.a.fail_notice=Mock()
        async def deliver(fn): return await fn()
        async def finish():
            self.a._quota_completed=True; self.a._quota_evidence=True
            self.a.turn_status={'id':'accepted','status':'failed'}
        self.a.deliver=deliver; self.a.await_turn=finish
        self.a.call.side_effect=[{'turn':{'id':'accepted'}},self.account,self.account,{'rateLimits':{}}]
        sys.modules['recovery_fixture.quota_refresh']=self.q
        setattr(sys.modules['recovery_fixture'],'quota_refresh',self.q)
        self.addCleanup(sys.modules.pop,'recovery_fixture.quota_refresh',None)
        fn=self.method('agent_codex.py','turn',slash=N(parse=lambda _:None),RPCError=RPCError)
        await fn(self.a,'fake request')
        self.assertEqual([c.args[0] for c in self.a.call.await_args_list],
                         ['turn/start','account/read','account/read','account/rateLimits/read'])
        self.assertTrue(self.a._quota_no_replay); self.assertFalse(self.a._quota_completed)
        self.a.fail_notice.assert_called_once()

class Ledger(Contract):
    async def test_real_consumed_operation_stays_rejected_after_refresh(self):
        import tempfile
        path=Path(__file__).parents[1]/'agentj'/'bounded_plan.py'
        spec=importlib.util.spec_from_file_location('fixture_bounded_plan',path)
        module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module
        self.addCleanup(sys.modules.pop,spec.name,None); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='qa-398-',dir=Path(__file__).parents[3]) as directory:
            ledger=module.OperationOnce(Path(directory)/'once.sqlite3')
            self.h._approval_operation_once=ledger
            self.assertTrue(ledger.reserve('consumed','original'))
            ledger.finish('consumed','original','completed')
            self.replies(); await self.q.start(self.a,'turn/start',{})
            self.assertFalse(ledger.reserve('consumed','new-request'))
            # Also exercise the durable DB, independent of the in-memory terminal set.
            reopened=module.OperationOnce(ledger.path)
            self.assertFalse(reopened.reserve('consumed','another-request'))
            self.assertTrue(reopened.reserve('unrelated-new-operation','fresh'))
    async def test_real_signed_approval_setting_survives_refresh(self):
        import base64
        import hashlib
        import json
        import tempfile
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        package=sys.modules['recovery_fixture']
        wire=N(unb64u=lambda s:base64.urlsafe_b64decode(s+'='*(-len(s)%4)))
        for name,value in [('wire',wire),('passkey',N()),('policy_registry',N(HOST_TOOLS=()))]:
            sys.modules['recovery_fixture.'+name]=value; setattr(package,name,value)
        def load(name):
            spec=importlib.util.spec_from_file_location('recovery_fixture.'+name,Path(__file__).parents[1]/'agentj'/(name+'.py'))
            mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod
            setattr(package,name,mod); spec.loader.exec_module(mod); return mod
        controls=load('controls'); controls.estop_state=lambda _: {'on':False}
        level=load('approval_level')
        private=Ed25519PrivateKey.generate()
        public=private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        self.h.st.sign_key=lambda _:public; self.h.st.config=lambda:{'channel':'owner'}
        digest=controls.object_digest('approval_set',{'key':'approval.level','value':'harness'})
        signature=private.sign(controls.signed_message('owner','phone','approval_set','nonce',1,digest))
        record={'approval.level':{'value':'harness','device':'phone','rid':'signed-request',
                'sk_sha':hashlib.sha256(public).hexdigest(),'n':'nonce','ts':1,
                'sig':base64.urlsafe_b64encode(signature).decode().rstrip('=')}}
        with tempfile.TemporaryDirectory(prefix='qa-398-signed-',dir=Path(__file__).parents[3]) as directory:
            self.h.st.root=Path(directory)
            path=Path(directory)/'approval_signers.json'; path.write_text(json.dumps(record))
            original=path.read_bytes()
            self.assertEqual(level.signed_value(self.h.st,'approval.level'),'harness')
            self.h.nonces=controls.Nonces()
            self.assertTrue(self.h.nonces.take('consumed-nonce'))
            self.replies(); await self.q.start(self.a,'turn/start',{})
            self.assertEqual(path.read_bytes(),original)
            self.assertFalse(self.h.nonces.take('consumed-nonce'))
            self.assertTrue(self.h.nonces.take('fresh-nonce'))
            self.assertEqual(level.signed_value(self.h.st,'approval.level'),'harness')
            self.h.st.sign_key=lambda _:b'x'*32
            self.assertIsNone(level.signed_value(self.h.st,'approval.level'))
    async def test_key_epoch_task_stop_and_revocation_refuse_retry(self):
        for change in (lambda:setattr(self.h,'_pairing_epoch',1),
                       lambda:setattr(self.h,'task_epoch',2),
                       lambda:setattr(self.h.st,'sign_key',lambda _:b'replaced'),
                       lambda:setattr(self.h,'_paired_live',lambda _:False),
                       lambda:setattr(self.a,'_quota_stop',1),
                       lambda:setattr(self.a,'halting',True),
                       lambda:setattr(self.a.cur_send,'state','cancelled')):
            self.setUp(); calls=0
            async def rpc(method,*args):
                nonlocal calls
                calls+=1
                if calls==1: raise RPCError({'code':'usage_limit_reached'})
                change(); return self.account
            self.a.call.side_effect=rpc
            with self.assertRaises(RPCError): await self.q.start(self.a,'turn/start',{})
            self.assertEqual(sum(c.args[0]=='turn/start' for c in self.a.call.await_args_list),1)
            self.assertFalse(self.a._quota_refreshing)
    async def test_cancelled_refresh_releases_state(self):
        self.a.call.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError): await self.q.refresh(self.a)
        self.assertFalse(self.a._quota_refreshing)

class AdapterGuard(unittest.TestCase):
    def test_actual_fail_notice_cannot_schedule_quota_resume_after_acceptance(self):
        import ast
        import contextlib
        path=Path(__file__).parents[1]/'agentj'/'agent_codex.py'
        tree=ast.parse(path.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='CodexAgent')
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='fail_notice')
        cls.bases=[ast.Name(id='Base',ctx=ast.Load())]; cls.body=[fn]
        module=ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[]))
        class Base:
            def fail_notice(self,text): self.notice=text
        scope={'Base':Base,'contextlib':contextlib,'__package__':'guard_fixture'}
        exec(compile(module,str(path),'exec'),scope)
        package=types.ModuleType('guard_fixture'); package.__path__=[]
        resume=N(main_hit=Mock())
        with patch.dict(sys.modules,{'guard_fixture':package,'guard_fixture.quota_resume':resume}):
            a=scope['CodexAgent'](); a.kind='codex'; a.cfg={}; a.host=N(auth_rescue=N(signal=Mock()))
            a.failure=lambda _:('usage','quota exhausted',None); a._quota_no_replay=True
            a.fail_notice('usageLimitExceeded')
            self.assertEqual(a.notice,'quota exhausted'); resume.main_hit.assert_not_called()

if __name__=='__main__': unittest.main()
