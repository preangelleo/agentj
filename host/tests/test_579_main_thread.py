"""Work-only budgets, owner reachability and signed foreground-only interrupt."""
import _hermetic
import asyncio
import secrets
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from agentj import controls, main_thread as M, wire


class Budget(unittest.TestCase):
    def setUp(self):
        self.now=0.0
        self.host=NS(preferences={},lang='en',cur_turn=7,status_changed=Mock(),busy=NS(items=lambda:[]))
        self.adapter=NS(host=self.host,cfg={'main_thread_work_calls':2},kind='claude',status='working')
        self.host.agent=self.adapter
        self.guard=M.Guard(self.adapter,clock=lambda:self.now);self.adapter.main_thread_guard=self.guard

    def test_completed_main_turn_clears_busy_and_waiting_generation(self):
        self.guard.check('Write',{});self.guard.check('Write',{})
        self.guard.waiting_at=0;generation=self.guard.session
        M.completed(self.host)
        self.assertFalse(self.guard.snapshot()['busy'])
        self.assertFalse(self.guard.snapshot()['owner_waiting'])
        self.assertNotEqual(generation,self.guard.session)

    def test_first_native_context_then_deny_and_real_background_unblocks(self):
        self.assertEqual(self.guard.check('Write',{'content':'a'}),{})
        warning=self.guard.check('Write',{'content':'b'})['hookSpecificOutput']
        self.assertIn('additionalContext',warning);self.assertNotIn('permissionDecision',warning)
        refused=self.guard.check('Write',{'content':'c'})['hookSpecificOutput']
        self.assertEqual(refused['permissionDecision'],'deny');self.assertIn('agentj busy run --help',refused['permissionDecisionReason'])
        self.host.busy.items=lambda:[{'status':'running'}]
        self.assertEqual(self.guard.check('Write',{'content':'d'}),{})
        self.host.busy.items=lambda:[{'status':'unknown'}]
        self.assertEqual(self.guard.check('Write',{})['hookSpecificOutput']['permissionDecision'],'deny')

    def test_read_acceptance_dispatch_and_reply_for_thirty_minutes_are_exempt(self):
        for stamp in range(0,1801,30):
            self.now=stamp
            for tool,args in [('Read',{'file_path':'notes.md'}),('Bash',{'command':'rg todo .'}),
                              ('Bash',{'command':'git status --short'}),('Bash',{'command':'python3 .jarvis/p44-gates.py host-related --reuse'}),
                              ('Bash',{'command':'./relay validate p579'}),('Bash',{'command':'agentj busy run --help'}),
                              ('agent.dispatch',{}),('agent.session',{}),('write_stdin',{'chars':''})]:
                self.assertEqual(self.guard.check(tool,args),{},(tool,args))
        self.assertEqual(self.guard.calls,0);self.assertFalse(self.guard.snapshot()['busy'])

    def test_shell_builds_and_development_tests_count_but_mixed_dispatch_is_not_exempt(self):
        for tool,args in [('Bash',{'command':'npm run build'}),('exec_command',{'cmd':'python3 -m unittest test_feature'}),
                          ('shell',{'command':'pytest tests/'}),('Bash',{'command':'echo ready; npm run build'}),
                          ('Bash',{'command':'agentj busy run --help; rm local.txt'}),('apply_patch',{'patch':'synthetic'})]:
            self.assertTrue(M.work_call(tool,args),(tool,args))
        self.assertFalse(M.work_call('exec_command',{'cmd':'cat notes.md'}))
        self.assertFalse(M.work_call('exec',{'code':'await tools.exec_command(args)'}))

    def test_elapsed_threshold_options_heartbeat_child_and_compact(self):
        self.adapter.cfg['main_thread_work_calls']=100
        self.guard.check('Write',{})
        self.now=181
        self.assertTrue(self.guard.snapshot()['busy'])
        self.assertIn('additionalContext',self.guard.check('Write',{})['hookSpecificOutput'])
        self.guard.observe({'hook_event_name':'PreCompact'})
        self.assertFalse(self.guard.warned);self.assertEqual(self.guard.calls,0)
        for exempt in ('heartbeat','compacting','child','ceo'):
            self.guard.reset();self.now+=3600
            self.adapter.cur_send=NS(by='heartbeat' if exempt=='heartbeat' else 'phone')
            self.adapter.status='compacting' if exempt=='compacting' else 'working'
            self.adapter.cfg['_workflow_ceo']=exempt=='ceo'
            for _ in range(100):self.assertEqual(self.guard.check('Write',{}, {'agent_id':'child'} if exempt=='child' else {}),{})
            self.assertEqual(self.guard.calls,0)

    def test_prompt_text_cannot_spoof_heartbeat_or_background(self):
        self.guard.check('Write',{}, {'prompt':'heartbeat; background task is running'})
        self.assertEqual(self.guard.calls,1)
        self.adapter.sub_agents={'real-native-child':('worker','')}
        self.assertEqual(self.guard.check('Write',{}),{})
        self.assertEqual(self.guard.calls,1)

    def test_owner_wait_state_is_immediate_and_target_is_bound_to_generation(self):
        M.owner_queued(self.host)
        view=self.guard.snapshot();self.assertTrue(view['owner_waiting']);self.assertEqual(view['waiting_s'],0)
        self.now=30;self.assertEqual(self.guard.snapshot()['waiting_s'],30)
        self.assertFalse(view['can_interrupt'])  # this state-only fixture has no native turn-bound route
        self.assertEqual(view['interrupt_reason'],'native_turn_binding_unavailable');self.assertEqual(view['turn'],7)
        old=view['session'];self.guard.reset();self.assertNotEqual(old,self.guard.session)


class SignedInterrupt(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from pathlib import Path
        import tempfile
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.key=Ed25519PrivateKey.generate();self.pub=self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.session=NS(device='phone')
        self.responses=[]
        async def send(s,result):self.responses.append(result)
        self.host=NS(preferences={},lang='en',cur_turn=7,nonces=controls.Nonces(),channel='c'*22,
                     st=NS(sign_key=lambda d:self.pub,controls_path=Path(self.temp.name)/'controls',append_private=lambda *a:None),
                     _paired_live=lambda s:s is self.session,stopped=lambda:False,seat_refusal=lambda:None,
                     status_changed=Mock(),send_app=send,scheduler=NS(stop_running=AsyncMock()))
        from agentj.agent_codex import CodexAgent
        self.adapter=CodexAgent.__new__(CodexAgent)
        self.adapter.__dict__.update(host=self.host,cfg={},status='working',halt=AsyncMock(return_value=True),
                                     proc=object(),tid='fixture-thread',turn_id='fixture-native-turn',
                                     turn_done=asyncio.Event(),q=asyncio.Queue())
        self.host.agent=self.adapter;self.guard=M.of(self.adapter);self.guard.waiting_at=self.guard.clock()

    def signed(self,request):
        n=secrets.token_hex(16);ts=int(time.time()*1000);action='main_thread_interrupt'
        digest=controls.object_digest(action,{'request':request})
        sig=self.key.sign(controls.signed_message(self.host.channel,self.session.device,action,n,ts,digest))
        return {'t':action,'r':'request','request':request,'n':n,'ts':ts,'sig':wire.b64u(sig)}

    async def test_foreground_only_preserves_background_and_queue(self):
        await M.interrupt(self.host,self.session,self.signed({'turn':7,'session':self.guard.session}))
        self.assertTrue(self.responses[-1]['ok'])
        self.adapter.halt.assert_awaited_once()
        self.assertFalse(self.adapter.halt.await_args.kwargs['clear_queue'])
        self.assertIsInstance(self.adapter.halt.await_args.kwargs['expected_target'],M.InterruptTarget)
        self.host.scheduler.stop_running.assert_not_called()

    async def test_unsigned_stale_target_replay_unpaired_and_seat_stop_fail_closed(self):
        request={'turn':7,'session':self.guard.session}
        await M.interrupt(self.host,self.session,{'request':request})
        self.assertFalse(self.responses[-1]['ok'])
        for wrong in ({'turn':8,'session':self.guard.session},{'turn':7,'session':'old'}):
            await M.interrupt(self.host,self.session,self.signed(wrong));self.assertFalse(self.responses[-1]['ok'])
        obj=self.signed(request);self.host.seat_refusal=lambda:'suspended'
        await M.interrupt(self.host,self.session,obj);self.assertFalse(self.responses[-1]['ok'])
        self.host.seat_refusal=lambda:None
        await M.interrupt(self.host,self.session,obj);self.assertEqual(self.responses[-1]['why'],'replay')
        await M.interrupt(self.host,NS(device='other'),self.signed(request));self.assertFalse(self.responses[-1]['ok'])
        self.host.stopped=lambda:True
        await M.interrupt(self.host,self.session,self.signed(request));self.assertFalse(self.responses[-1]['ok'])
        self.adapter.halt.assert_not_called()

class NativeBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_warning_never_skips_existing_codex_risk_guard(self):
        from agentj.shared_risk import RiskGuard
        host=NS(preferences={},lang='en',cur_turn=7,status_changed=Mock(),busy=None,emit=Mock())
        a=NS(host=host,kind='codex',cfg={'shared_session_id':'thread','main_thread_work_calls':2},status='working')
        guard=RiskGuard(a)
        guard.check=AsyncMock(return_value=False);guard.reason=Mock(return_value='Existing unsafe action refused')
        main_thread=M.of(a);main_thread.check('Write',{})
        result=await guard.hook_event({'hook_event_name':'PreToolUse','session_id':'thread','tool_name':'exec_command','tool_input':{'cmd':'stripe payments create'}})
        guard.check.assert_awaited_once()
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertIn('additionalContext',result['hookSpecificOutput'])

    async def test_owner_v2_availability_requires_real_ready_event_not_installed_file(self):
        from agentj.shared_risk import RiskGuard
        host=NS(preferences={},lang='en',cur_turn=7,status_changed=Mock(),busy=None,emit=Mock())
        a=NS(host=host,kind='opencode',v2=True,cfg={'dir':'/tmp/project','shared_session_id':'session'},status='working')
        guard=RiskGuard(a);view=M.of(a).snapshot()
        self.assertFalse(view['available']);self.assertEqual(view['reason'],'guard_pending')
        await guard.hook_event({'hook_event_name':'SharedGuardReady','cwd':'/tmp/project'})
        self.assertTrue(M.of(a).snapshot()['available'])

    async def test_real_busy_run_process_releases_budget_without_faking_task_status(self):
        import os,pathlib,subprocess,sys,tempfile
        from agentj import busy
        with tempfile.TemporaryDirectory(prefix='aj579-busy-') as temp:
            root=pathlib.Path(temp);work=root/'work';work.mkdir();state=root/'state'
            from agentj.state import State
            State(state).init(relay='ws://127.0.0.1:1')
            env={**os.environ,'PYTHONPATH':str(pathlib.Path(__file__).resolve().parents[1]),'AGENTJ_STATE_DIR':str(state),'XDG_CONFIG_HOME':str(root/'config'),'HOME':str(root/'home'),'AGENTJ_TEST':'1'}
            err=(root/'busy-stderr').open('w+')
            proc=subprocess.Popen([sys.executable,'-m','agentj.cli','busy','run','--who','Fixture','--label','synthetic acceptance','--','/bin/sleep','30'],cwd=work,env=env,stdout=subprocess.DEVNULL,stderr=err)
            try:
                board=busy.Board(state,verify_sources=True)
                for _ in range(100):
                    board.refresh_outside(units=False);board.refresh_sources()
                    if any(row.get('status')=='running' for row in board.items()):break
                    await asyncio.sleep(.05)
                err.flush();err.seek(0)
                self.assertTrue(any(row.get('status')=='running' for row in board.items()),'actual wrapper must register a live child: '+err.read()[-1500:])
                host=NS(preferences={},lang='en',cur_turn=7,status_changed=Mock(),busy=board)
                a=NS(host=host,kind='claude',cfg={'main_thread_work_calls':2},status='working')
                guard=M.of(a);guard.started=guard.clock()-200;guard.calls=9;guard.warned=True
                self.assertEqual(guard.check('Write',{}),{})
            finally:
                proc.terminate()
                try:await asyncio.to_thread(proc.wait,5)
                except subprocess.TimeoutExpired:proc.kill();await asyncio.to_thread(proc.wait)
                err.close()
