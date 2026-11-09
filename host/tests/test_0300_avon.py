"""0.30 Avon: native dispatch authority and inherited permissions; no model calls."""
import _hermetic
import os
import asyncio
import tempfile
import time
import json
import base64
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock, Mock
from agentj import busy_launch, busy_native, approval_material, provenance


class Admission(unittest.TestCase):
    def host(self, source=None):
        pid = os.getpid()
        row = busy_launch.procs()[pid]
        agent = SimpleNamespace(kind='codex', cfg={'dir':'/synthetic','fence':True},
            proc=SimpleNamespace(pid=pid,returncode=None), session=None,
            cur_send=SimpleNamespace(source=source or provenance.system()),
            thread={'sandbox':{'type':'dangerFullAccess'},'approvalPolicy':'never','approvalsReviewer':'user'})
        return SimpleNamespace(agent=agent, busy=SimpleNamespace(context={'own':{'pid':pid,'proc_start':row[2]}}),
                               _agent_pids=lambda:[pid])

    def request(self):
        return {'argv':['codex','exec','synthetic task'],'cwd':'/synthetic'}

    def test_host_system_turn_dispatches_without_owner_source_promotion(self):
        h=self.host()
        with patch.object(approval_material,'verified_caller_origin',return_value=None):
            _,cfg,source,mode,_=busy_native.policy(h,self.request(),os.getpid(),os.getuid())
        self.assertEqual(mode,'danger-full-access')
        self.assertFalse(source.get('verified'))
        self.assertEqual(cfg['_native_permissions']['approvalPolicy'],'never')

    def test_parent_native_policy_is_frozen_not_request_claim(self):
        h=self.host()
        h.agent.thread['approvalPolicy']='on-request'
        req={**self.request(),'approvalPolicy':'never'}
        with patch.object(approval_material,'verified_caller_origin',return_value={'verified':True}):
            _,cfg,_,_,_=busy_native.policy(h,req,os.getpid(),os.getuid())
        h.agent.thread['approvalPolicy']='never'
        self.assertEqual(cfg['_native_permissions']['approvalPolicy'],'on-request')

    def test_pid_reuse_cannot_dispatch(self):
        h=self.host();h.busy.context['own']['proc_start']='not-current'
        with patch.object(approval_material,'verified_caller_origin',return_value=None):
            with self.assertRaisesRegex(ValueError,'unknown source'):
                busy_native.policy(h,self.request(),os.getpid(),os.getuid())

    def test_guest_cannot_dispatch_through_main_source(self):
        h=self.host(provenance.email_guest('fixture@example.test'))
        with patch.object(approval_material,'verified_caller_origin',return_value=None):
            with self.assertRaisesRegex(ValueError,'unknown source'):
                busy_native.policy(h,self.request(),os.getpid(),os.getuid())


class Settings(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_l1 import _state, _ready
        from _p541_device import _owner_host, Phone
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.sent=[];self.h=_owner_host(_state(tmp.name),self.sent)
        self.h.ask_ttl=1
        self.phone=Phone(self.h.st,with_passkey=True);self.session=_ready(self.h,self.phone)
        self.ar=self.h.auth_rescue
        self.ar.broadcast=Mock();self.ar._codex=AsyncMock()

    async def request(self):
        task=asyncio.create_task(self.ar.start('codex','device',remote=self.session,account={'name':'Synthetic backup'}))
        for _ in range(100):
            if self.h.asks or task.done():break
            await asyncio.sleep(.002)
        self.assertFalse(task.done(),str(task.result()) if task.done() else '')
        ask=next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.finish,task,ask)
        self.assertTrue(ask.require_passkey)
        return task,ask

    async def finish(self,task,ask):
        if not ask.fut.done():ask.fut.set_result(('deny',None,None,None))
        await asyncio.gather(task,return_exceptions=True)
        for bg in list(self.h.__dict__.get('_bg_tasks',())):
            bg.cancel()
        await asyncio.sleep(0)

    async def test_settings_signed_uv_survives_unrelated_main_agent_change(self):
        task,a=await self.request()
        from agentj.agent import Agent
        self.h.agent=Agent(self.h,dict(self.h.agent.cfg))
        self.h.agent.native_source=provenance.email_guest('fixture@example.test')
        self.h.agent.submit=Mock()
        self.h.task_epoch='different-task'
        await self.h._answer(self.session,self.phone.answer(self.h._ask_msg(a,self.session),True))
        self.assertEqual(await task,{'ok':True})
        await asyncio.sleep(.01)
        self.ar._codex.assert_awaited_once()
        self.assertEqual(a.envelope['source']['channel'],'phone')

    async def test_missing_uv_never_starts_login(self):
        task,a=await self.request()
        answer=self.phone.answer(self.h._ask_msg(a,self.session),True);answer.pop('fa')
        await self.h._answer(self.session,answer)
        self.assertFalse(a.fut.done());self.ar._codex.assert_not_awaited()
        await self.h._answer(self.session,self.phone.answer(self.h._ask_msg(a,self.session),False))
        self.assertEqual((await task)['why'],'denied')

    async def test_session_replacement_refuses_after_valid_crypto(self):
        task,a=await self.request()
        self.h.sessions.pop(self.session.cid)
        await self.h._answer(self.session,self.phone.answer(self.h._ask_msg(a,self.session),True))
        self.assertFalse(a.fut.done());self.ar._codex.assert_not_awaited()

    async def test_revoke_during_operation_reservation_never_starts_login(self):
        from agentj.bounded_plan import OperationOnce
        task,a=await self.request()
        reserve=OperationOnce.reserve
        def revoke(book,*args):
            result=reserve(book,*args)
            self.h.st.remove_device(self.phone.did)
            return result
        with patch.object(OperationOnce,'reserve',revoke):
            await self.h._answer(self.session,self.phone.answer(self.h._ask_msg(a,self.session),True))
        self.assertFalse(a.fut.done());self.ar._codex.assert_not_awaited()

    async def test_exact_rejection_classification_and_exception_privacy(self):
        for raw,expected in (('device','denied'),('timeout','expired'),('no_device','no_device'),
                ('group_source','source_unavailable'),('authority_changed','session_changed'),('estop','estop')):
            failed={}
            with patch.object(self.h,'ask',AsyncMock(return_value={'behavior':'deny','reason':raw})):
                self.assertFalse(await self.ar._confirm('codex',failure=failed))
            self.assertEqual(failed['why'],expected)
        failed={}
        with patch.object(self.h,'ask',AsyncMock(side_effect=RuntimeError('synthetic private exception'))):
            self.assertFalse(await self.ar._confirm('codex',failure=failed))
        self.assertEqual(failed['why'],'execution_error')
        self.assertNotIn('synthetic private exception',self.h.st.log_path.read_text())

    async def test_settings_subject_identity_epoch_host_key_expiry_and_copy_are_bound(self):
        import dataclasses
        from agentj.list_approval import SettingsContext
        h=self.h
        inp={'summary':self.ar._summary('codex','Synthetic backup')}
        context=SettingsContext.capture(h,self.ar,self.session,inp)
        self.assertTrue(context.live(h))
        self.assertFalse(dataclasses.replace(context).live(h))
        for target,field,value in ((h,'channel','other'),(h.hist,'epoch','other'),
                (h,'auth_rescue',object()),(h,'kp',SimpleNamespace(pub=b'other')),
                (self.session,'pub',b'other'),(self.session,'state','gone')):
            before=getattr(target,field)
            setattr(target,field,value)
            self.assertFalse(context.live(h),field)
            setattr(target,field,before)
            self.assertTrue(context.live(h),field)
        with patch('agentj.list_approval.time.monotonic',return_value=context.until):
            self.assertFalse(context.live(h))
        with self.assertRaises(ValueError):SettingsContext.capture(h,object(),self.session,inp)
        context.discard();self.assertFalse(context.live(h))


class Proxy(unittest.IsolatedAsyncioTestCase):
    async def test_native_context_keeps_child_directory_and_rejects_changed_runner(self):
        from agentj.list_approval import NativeContext
        controller=SimpleNamespace(ask=AsyncMock(return_value={'behavior':'allow'}))
        proxy=busy_native._runner_host(controller)
        proxy.runner=SimpleNamespace(host=proxy,kind='codex',cfg={'dir':'/child'},proc=object(),tid='child-thread')
        await proxy.ask('Write',{'file_path':'.codex/config.toml'},_harness_permission=True,
                        _native_context=NativeContext.capture(proxy.runner))
        context=controller.ask.call_args.kwargs['_native_context']
        self.assertTrue(context.live(controller));self.assertEqual(context.cwd,'/child')
        proxy.runner.cfg['dir']='/other'
        self.assertFalse(context.live(controller))

    async def test_busy_runner_preserves_real_parent_policy_in_thread_and_turn(self):
        from agentj import agent_codex, busy
        for policy in ('never','on-request',{'reject':{'sandbox_approval':True,'rules':False,'mcp_elicitations':True}}):
            calls=[]
            class Adapter:
                def __init__(self,host,cfg,**kw):
                    self.host=host;self.cfg=cfg;self.proc=None;self.thread={}
                def policy(self):return {'approvalPolicy':'untrusted'}
                async def call(self,method,params=None,*a,**kw):calls.append((method,params))
                async def run_once(self,text):
                    await self.call('thread/start',self.policy())
                    await self.call('turn/start',{'threadId':'synthetic'})
                    return '',0
            with tempfile.TemporaryDirectory() as tmp:
                host=SimpleNamespace(st=SimpleNamespace(root=Path(tmp)),stopped=lambda:False)
                it=busy.item('run:123456abcdef',None,'ceo','synthetic',cwd=tmp,harness='codex')
                with patch.object(agent_codex,'CodexAgent',Adapter),patch.object(busy,'wait_capacity',return_value=True):
                    await busy_native.run(host,{'_native_permissions':{'approvalPolicy':policy}},'task','read-only',it)
            self.assertEqual([params['approvalPolicy'] for _,params in calls],[policy,policy])

    async def test_child_relative_control_write_still_needs_valid_uv(self):
        from test_l1 import _state, _ready
        from _p541_device import _owner_host, Phone
        with tempfile.TemporaryDirectory() as tmp:
            sent=[];h=_owner_host(_state(tmp),sent);h.ask_ttl=1
            phone=Phone(h.st,with_passkey=True);session=_ready(h,phone)
            proxy=busy_native._runner_host(h)
            child=Path(tmp)/'child';child.mkdir()
            proxy.runner=SimpleNamespace(host=proxy,kind='codex',cfg={'dir':str(child)},proc=object(),tid='synthetic')
            task=asyncio.create_task(proxy.ask('Write',{'file_path':'.codex/config.toml','content':'synthetic'},_harness_permission=True))
            for _ in range(100):
                if h.asks:break
                await asyncio.sleep(.002)
            ask=next(iter(h.asks.values()));self.assertTrue(ask.require_passkey)
            answer=phone.answer(h._ask_msg(ask,session),True);answer.pop('fa')
            await h._answer(session,answer);self.assertFalse(ask.fut.done())
            await h._answer(session,phone.answer(h._ask_msg(ask,session),False))
            self.assertEqual((await task)['behavior'],'deny')


class PasskeyLifecycle(unittest.IsolatedAsyncioTestCase):
    # Reuse only the real paired Host fixture, not its test cases.
    asyncSetUp=Settings.asyncSetUp
    async def test_no_check_is_not_key_loss_for_all_four_challenge_lifetimes(self):
        peer=self.h.pkpeer
        now=[100.0];peer.clock=lambda:now[0]
        async def check(n):
            self.sent.clear()
            await peer._check(self.session,{'n':n})
            response=next(obj for _,obj in self.sent if obj.get('t')=='pk_check_res')
            self.assertEqual((response['ok'],response['why']),(False,'no_check'))
            self.assertTrue(any(obj.get('t')=='pk_state' and obj.get('fa_ch') for _,obj in self.sent))
        old=peer.check_fields(self.session)['n'];now[0]+=601
        await check(old)
        old=peer.check_fields(self.session)['n']
        for _ in range(4):peer.check_fields(self.session)
        await check(old)
        old=peer.check_fields(self.session)['n']
        await peer._check(self.session,{'n':old})  # consumes even a malformed assertion
        await check(old)
        old=peer.check_fields(self.session)['n'];peer.checks.clear()  # service restart's in-memory state
        await check(old)


import test_p114 as browser_fixture


@unittest.skipUnless(browser_fixture.chrome_path(), 'needs isolated Chromium')
class AvonBrowserIPC(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Existing local QR site and a fresh private browser profile, no customer login.
        for name,value in (('display_env',{}),('_manager_has_display',False)):
            guard=patch.object(browser_fixture.browser,name,return_value=value)
            guard.start();self.addCleanup(guard.stop)
        await browser_fixture.RealBrowser.asyncSetUp(self)
        self.st.init(relay='ws://127.0.0.1:1')
        from _p541_device import _owner_host, Phone
        from test_l1 import _ready
        from agentj import elevate
        self.sent=[];self.h=_owner_host(self.st,self.sent)
        self.phone=Phone(self.st,with_passkey=True);self.session=_ready(self.h,self.phone)
        self.h.push_notify=Mock()
        self.el=elevate.Elevator(self.h)
        await self.el.start();self.addAsyncCleanup(self.el.stop)
        self.h.agent.kind='codex'
        self.h.agent.thread={'sandbox':{'type':'dangerFullAccess'},'approvalPolicy':'never','approvalsReviewer':'user'}
        self.h.agent.proc=SimpleNamespace(pid=os.getpid(),returncode=None)
        self.h.busy.context={'own':{'pid':os.getpid(),'proc_start':busy_launch.procs()[os.getpid()][2]}}
        self.ctl=await asyncio.start_unix_server(self.h.on_ctl,path=str(self.st.sock_path))
        async def close_ctl():
            self.ctl.close();await self.ctl.wait_closed()
        self.addAsyncCleanup(close_ctl)

    async def flow(self, source, origin):
        from agentj import elevate, agent_codex, names, busy, approval_review
        self.h.agent.cur_send=SimpleNamespace(source=source)
        fixture=self;results=[]
        class Adapter:
            """Only the model execution is replaced; command-side IPC and browser stay real."""
            def __init__(self,host,cfg,**kw):self.host=host;self.cfg=cfg;self.proc=None
            async def run_once(self,text):
                actual=approval_review.ORIGIN.get() or {}
                fixture.assertEqual(bool(actual.get('verified')),bool((origin or {}).get('verified')))
                opened=await asyncio.to_thread(elevate.client_request,fixture.st,
                    {'t':'browser','action':'open','url':fixture.fx.origin+'/login'},10)
                fixture.assertTrue(opened.get('ok'),opened)
                result=await asyncio.to_thread(elevate.client_request,fixture.st,
                    {'t':'browser_login','site':'fixture'},20)
                results.append(result)
                return '',0
        key='run:123456abcdef'
        with patch.object(approval_material,'verified_caller_origin',return_value=origin), \
                patch.object(agent_codex,'CodexAgent',Adapter),patch.object(busy,'wait_capacity',return_value=True):
            receipt=await asyncio.to_thread(names.ctl_call,self.st,
                {'cmd':'busy_native','key':key,'argv':['codex','exec','open local fixture and send login card'],
                 'cwd':self.h.agent.cfg['dir']},10)
            self.assertTrue(receipt.get('ok'),receipt)
            self.assertEqual(receipt['approval'],'never')
            await self.h._busy_native[key]['task']
        self.assertEqual(results[0]['result'],'sent',results)
        card=next(obj for cid,obj in self.sent if cid==self.session.cid and obj.get('t')=='login_qr')
        self.assertTrue(base64.b64decode(card['img']).startswith(b'\x89PNG'))
        self.assertEqual(len(self.h.asks),0)
        self.el.login.end_all('gone')
        await asyncio.sleep(0)

    async def test_system_codex_turn_opens_browser_and_sends_login_card(self):
        await self.flow(provenance.system(),None)

    async def test_owner_dispatched_ceo_opens_browser_and_sends_login_card(self):
        await self.flow(provenance.phone(),{'verified':True,'suspicious':False,'channel':'phone',
            'text':'Synthetic request to open the browser login page','session':str(self.h.hist.epoch)})
