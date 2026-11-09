"""Independent native adapter pre-execution channels; no native permission overrides."""
import _hermetic
from _testtmp import TEST_TMP
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from agentj.agent_codex import CodexAgent, RPCError
from agentj.agent_opencode import OpenCodeAgent
from agentj import shared_hook, shared_risk

class NativeControls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cwd=self.root/'project';self.cwd.mkdir()
        self.perm=self.root/'state'/'agentperm';self.perm.mkdir(parents=True)
        self.host=NS(st=NS(root=self.root/'state',perm_dir=self.perm),ask=AsyncMock(return_value={'behavior':'allow'}),stopped=lambda:False)

    def codex(self):
        a=object.__new__(CodexAgent)
        a.cfg={'dir':str(self.cwd),'_workflow_ceo':True};a.host=self.host
        a.persist=False;a.research=False;a.tid=None;a.proc=NS(pid=os.getpid())
        a.human={'approval_policy':'never','sandbox_mode':'danger-full-access'}
        return a

    async def test_codex_additive_launch_current_hook_hash_and_thread_binding(self):
        a=self.codex()
        await a._prepare_control_paths();self.addAsyncCleanup(a._control_channel.stop)
        argv=a.argv();self.assertTrue(any(v.startswith('hooks.PreToolUse=') for v in argv))
        self.assertFalse(any('approval_policy=' in v or 'sandbox_mode=' in v for v in argv))
        self.assertTrue(a.initialize_capabilities()['experimentalApi'])
        command=shared_hook.settings(a._control_channel.path,('PreToolUse',))['hooks']['PreToolUse'][0]['hooks'][0]['command']
        async def call(method,params):
            if method=='hooks/list':return {'data':[{'cwd':str(self.cwd),'hooks':[{'command':command,'enabled':True,'key':'OWN','currentHash':'CURRENT','eventName':'preToolUse','handlerType':'command'}, {'command':'foreign','enabled':True,'key':'FOREIGN','currentHash':'OTHER','eventName':'preToolUse','handlerType':'command'}]}]}
            self.assertEqual('thread/start',method)
            self.assertEqual({'hooks.state':{'OWN':{'trusted_hash':'CURRENT'}}},params['config'])
            self.assertEqual('never',params['approvalPolicy']);self.assertEqual('danger-full-access',params['sandbox'])
            return {'thread':{'id':'native-thread'}}
        a.call=AsyncMock(side_effect=call)
        self.assertTrue(await a._thread());self.assertEqual('native-thread',a.tid)
        a.tid=None;a.call=AsyncMock(return_value={'data':[]})
        with self.assertRaises(RPCError):await a._thread()
        self.assertEqual(['hooks/list'],[x.args[0] for x in a.call.call_args_list])

    async def exchange(self,channel,event):
        reader,writer=await asyncio.open_unix_connection(str(channel.path))
        writer.write((json.dumps(event)+'\n').encode());await writer.drain()
        result=json.loads(await reader.readline());writer.close();await writer.wait_closed();return result

    async def test_actual_channel_kernel_binding_and_list_only_asks(self):
        for family in ('codex','opencode'):
            with self.subTest(family=family):
                a=self.codex();a.kind=family;a.tid=a.sid='native-session'
                guard=shared_risk.NativePathGuard(a)
                channel=shared_hook.Channel(guard,shared_hook.channel_path(self.perm,family));await channel.start()
                try:
                    event={'hook_event_name':'PreToolUse','session_id':'native-session','cwd':str(self.cwd),'tool_name':'Write','tool_input':{'file_path':str(self.root/'state'/'settings.json'),'content':'changed'}}
                    self.host.ask.reset_mock()
                    self.assertEqual({},await self.exchange(channel,event));self.host.ask.assert_awaited_once()
                    self.assertTrue(self.host.ask.call_args.kwargs['_harness_permission']);self.assertFalse(self.host.ask.call_args.kwargs['batch'])
                    self.host.ask.reset_mock()
                    bad=await self.exchange(channel,{**event,'session_id':'foreign-session'})
                    self.assertEqual('deny',bad['hookSpecificOutput']['permissionDecision']);self.host.ask.assert_not_awaited()
                    normal=await self.exchange(channel,{**event,'tool_input':{'file_path':str(self.cwd/'main.py'),'content':'ordinary'}})
                    self.assertEqual({},normal);self.host.ask.assert_not_awaited()
                    self.host.ask.return_value={'behavior':'deny'}
                    denied=await self.exchange(channel,event)
                    self.assertEqual('deny',denied['hookSpecificOutput']['permissionDecision'])
                    self.host.ask.return_value={'behavior':'allow'}
                finally:await channel.stop()

    async def test_ready_requires_kernel_ancestry_and_exact_native_cwd(self):
        a=self.codex();a.kind='opencode';a.sid='native-session'
        guard=shared_risk.NativePathGuard(a)
        event={'hook_event_name':'SharedGuardReady','cwd':str(self.cwd)}
        self.assertEqual({},await guard.hook_event(event));self.assertFalse(guard.ready.is_set())
        guard.authentic(event)
        await guard.hook_event(event);self.assertFalse(guard.ready.is_set())
        guard.authentic(event := {**event,'_agentj_chain':[os.getpid()]})
        await guard.hook_event({**event,'cwd':str(self.root/'other')});self.assertFalse(guard.ready.is_set())
        await guard.hook_event(event);self.assertTrue(guard.ready.is_set())

    async def test_shared_codex_missing_hook_capability_refuses_instead_of_degrading_open(self):
        from agentj.shared_codex import SharedCodexAgent
        a=SharedCodexAgent(Mock(),{'kind':'codex','dir':str(self.cwd),'_workflow_ceo':True})
        a.risk_channel=NS(path=self.perm/'shared-codex.sock')
        a.call=AsyncMock(return_value={'data':[]});a.degraded=Mock()
        with self.assertRaises(RPCError):await a._hook_config()
        a.degraded.assert_called_once()

    async def test_instance_channels_do_not_collide_and_cleanup_owned_directories(self):
        adapters=[]
        for cls in (CodexAgent,OpenCodeAgent):
            for _ in range(2):
                a=object.__new__(cls);a.cfg={'dir':str(self.cwd)};a.host=self.host
                if cls is OpenCodeAgent:a.v2=False
                adapters.append(a)
        await asyncio.gather(*(a._prepare_control_paths() for a in adapters))
        paths=[a._control_channel.path for a in adapters]
        self.assertEqual(4,len(set(paths)))
        self.assertTrue(all(path.exists() for path in paths))
        await asyncio.gather(*(a._stop_control_paths() for a in adapters))
        self.assertTrue(all(not path.parent.exists() for path in paths))

    async def test_opencode_same_cwd_cold_loads_serialize_until_native_ack(self):
        active=0;peak=0
        async def loader(a):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            await asyncio.sleep(.01)
            active-=1
            return True
        adapters=[]
        for _ in range(2):
            a=object.__new__(OpenCodeAgent);a.cfg={'dir':str(self.cwd)};a.host=self.host
            a._spawn_native=lambda a=a:loader(a)
            adapters.append(a)
        self.assertEqual([True,True],await asyncio.gather(*(a._spawn() for a in adapters)))
        self.assertEqual(1,peak)

    async def test_opencode_both_native_plugin_installers_without_permission_rules(self):
        for v2 in (False,True):
            a=object.__new__(OpenCodeAgent);a.cfg={'dir':str(self.cwd)};a.host=self.host;a.v2=v2;a.proc=NS(pid=os.getpid());a.sid='native-session'
            await a._prepare_control_paths()
            try:
                target=self.cwd/('.opencode/plugins/agentj-dispatch/index.js' if v2 else '.opencode/plugins/agentj-shared.js')
                text=target.read_text()
                self.assertIn('execute.before' if v2 else 'tool.execute.before',text)
                self.assertIn(str(a._control_channel.path),text)
                self.assertNotIn('approvalPolicy',text);self.assertNotIn('sandbox_mode',text)
                if v2:self.assertIn('170000',text)
            finally:await a._control_channel.stop()

class HelperDirectoryUV(unittest.IsolatedAsyncioTestCase):
    from test_uv_list import ListApproval as _Fixture
    asyncSetUp=_Fixture.asyncSetUp
    close=_Fixture.close
    review_intent=_Fixture.review_intent
    question_approve=_Fixture.question_approve

    async def open(self,tool='Write',inp=None):
        from agentj.list_approval import NativeContext
        from pathlib import Path
        if not hasattr(self,'native'):
            main=Path(self.st.root)/'main';helper=Path(self.st.root)/'helper'
            main.mkdir();helper.mkdir();self.h.agent_cfg={'dir':str(main)}
            self.native=NS(kind='opencode',host=self.h,cfg={'dir':str(helper)},sid='helper-session',proc=NS(pid=os.getpid()))
        task=asyncio.create_task(self.h.ask(tool,inp or {'file_path':'.claude/settings.json','content':'changed'},
            _harness_permission=True,_native_context=NativeContext.capture(self.native),grant_info=True))
        for _ in range(100):
            if self.h.asks or task.done():break
            await asyncio.sleep(.002)
        if task.done():self.fail(str(task.result()))
        ask=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.close,task,ask)
        return task,ask

    async def test_actual_host_uses_helper_cwd_uv_and_rejects_missing_uv(self):
        task,ask=await self.open()
        self.assertTrue(ask.require_passkey)
        self.assertIn(self.native.cfg['dir'],ask.summary)
        obj=self.ph.answer(self.h._ask_msg(ask,self.s),True);obj.pop('fa')
        await self.h._answer(self.s,obj)
        self.assertFalse(ask.fut.done());self.assertFalse(ask.uv_ok)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        self.assertEqual('allow',(await task)['behavior'])

    async def test_claude_permission_token_binds_launch_cwd_not_wire_cwd(self):
        from pathlib import Path
        from agentj.list_approval import PermissionContext
        helper=Path(self.st.root)/'task-helper';helper.mkdir()
        self.h.new_perm_env(background='task',cwd=str(helper))
        self.assertEqual(str(helper),self.h.perm_cwd_next)
        writer=NS();self.h.perm_w=writer
        context=PermissionContext(self.h,writer,self.h.perm_cwd_next)
        self.h._perm_send=AsyncMock();self.h.peers=None
        request={'t':'ask','id':91,'cwd':'/forged-wire-cwd','tool':'Write',
                 'input':{'file_path':'.claude/settings.json','content':'changed'}}
        gone={91:asyncio.get_running_loop().create_future()}
        task=asyncio.create_task(self.h._perm_ask(writer,request,gone,background='task',launch_context=context))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.002)
        ask=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.close,task,ask)
        self.assertTrue(ask.require_passkey);self.assertIn(str(helper),ask.summary)
        self.assertNotIn('/forged-wire-cwd',ask.summary)
        answer=self.ph.answer(self.h._ask_msg(ask,self.s),True);answer.pop('fa')
        await self.h._answer(self.s,answer);self.assertFalse(ask.fut.done())
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True));await task
        self.assertEqual('allow',self.h._perm_send.call_args.args[1]['behavior'])
        # A new permission connection cannot borrow the captured context.
        self.h.perm_w=NS()
        result=await self.h.ask('Write',request['input'],_harness_permission=True,_native_context=context)
        self.assertEqual('native_context_changed',result['reason'])

    async def test_question_continuation_cannot_move_to_another_helper_directory(self):
        from pathlib import Path
        inp={'file_path':'.claude/settings.json','content':'changed'}
        original,resume=await self.question_approve('Write',inp)
        self.assertTrue(original.require_passkey)
        other=Path(self.st.root)/'other-helper';other.mkdir();self.native.cfg['dir']=str(other)
        task,ask=await self.open('Write',inp)
        self.assertNotEqual(original.rid,ask.rid)
        self.assertNotEqual(original.list_resume_context,ask.list_resume_context)
        self.assertTrue(ask.require_passkey)
        self.assertFalse(task.done())
