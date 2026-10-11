"""P144 lost Stop recovery and device-bound withdrawal; entirely local fixtures."""
import _hermetic
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from agentj import shared
from agentj.shared_state import AgentState
from agentj.agent import Cmd
from agentj.serve import Host

class Clock:
    def __init__(self): self.at = 0
    def now(self): return self.at
    def call_later(self, delay, fn): return Mock()

class Recovery(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.warn = Mock(); self.turn = Mock()
        self.s = AgentState(scheduler=self.clock, warn=self.warn, on_turn=self.turn)
        self.s.handle({'hook_event_name':'UserPromptSubmit','session_id':'owner'})
    def ticks(self, count, status='idle'):
        for _ in range(count):
            self.s.validate_against(status); self.clock.at += 1
    def test_lost_stop_ten_seconds_quiet_idle_only_no_fabricated_reply(self):
        self.ticks(10); self.assertEqual(self.s.status,'working')
        self.assertTrue(self.s.validate_against('idle'))
        self.assertEqual(self.s.status,'idle'); self.turn.assert_not_called()
        self.assertEqual(sum('lost Stop reconciled' in c.args[0] for c in self.warn.call_args_list),1)
        self.ticks(15)
        self.assertEqual(sum('lost Stop reconciled' in c.args[0] for c in self.warn.call_args_list),1)
    def test_hook_restarts_grace_even_if_it_does_not_change_status(self):
        self.ticks(9); self.s.handle({'hook_event_name':'PostToolUse','session_id':'owner'})
        self.ticks(9); self.assertEqual(self.s.status,'working')
        self.ticks(2); self.assertEqual(self.s.status,'idle')
    def test_busy_and_unknown_or_missing_reads_reset_continuity(self):
        for status in ('busy','shell',None,'unknown'):
            self.setUp(); self.ticks(9); self.s.validate_against(status)
            self.ticks(9); self.assertEqual(self.s.status,'working')
    def test_poll_gap_does_not_count_as_continuous_idle(self):
        self.ticks(2); self.clock.at += 20; self.ticks(5)
        self.assertEqual(self.s.status,'working')
    def test_normal_working_busy_never_corrected(self):
        self.ticks(50,'busy'); self.assertEqual(self.s.status,'working')
    def test_waiting_approval_and_compaction_never_corrected(self):
        for field,value in (('card',{}),('approval',{'id':'pending'}),('_compacting',{'saw_busy':False})):
            self.setUp(); setattr(self.s,field,value or {'tool_name':'Bash'})
            self.ticks(15); self.assertEqual(self.s.status,'working')
        self.setUp(); self.s.handle({'hook_event_name':'PermissionRequest','tool_name':'Bash'})
        self.ticks(15); self.assertEqual(self.s.status,'waiting')

class Commands(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host=Mock(); self.host.lang='zh'; self.host.stopped.return_value=False
        self.a=shared.SharedClaudeAgent(self.host,{'kind':'claude','dir':'/fixture','_workflow_ceo':True})
        self.a.session={'sessionId':'owner-session','pid':123,'status':'idle'}
        self.a.native=Mock(pid=123); self.a.master=42
        self.a.read_statusline=Mock()
        self.a.cur_cmd=Cmd('clear',turn=9)
        self.clock=Clock(); self.a.state=AgentState(scheduler=self.clock,warn=self.a.state._warn)
        self.a.state.handle({'hook_event_name':'UserPromptSubmit'})
        self.live=patch.object(shared,'claude_sessions',side_effect=lambda *args:[dict(self.a.session)])
        self.live.start(); self.addCleanup(self.live.stop)
    async def test_lost_stop_clear_runs_after_ten_seconds_and_receipt(self):
        real_sleep=asyncio.sleep
        async def tick(delay):
            if delay == .1: self.clock.at += 1
            await real_sleep(0)
        writes=[]
        def write(fd,data):
            self.assertGreaterEqual(self.clock.at,10)
            writes.append(data)
            if data == b'\r': self.a.control_ack.set()
        with patch.object(shared.asyncio,'sleep',side_effect=tick), patch.object(shared.os,'write',side_effect=write):
            res=await self.a.command('clear','')
        self.assertEqual(res.kind,'ok'); self.assertEqual(writes,[b'/clear',b'\r'])
        self.host.cmd_waiting.assert_called_once_with(self.a.cur_cmd)
        self.assertTrue(self.a.cur_cmd.native_started)
    async def test_cancel_while_waiting_never_types_or_interrupts(self):
        self.a.session['status']='busy'
        with patch.object(shared.os,'write') as write:
            task=asyncio.create_task(self.a.command('clear',''))
            try:
                await asyncio.sleep(.02); self.a.cur_cmd.cancelled=True
                res=await asyncio.wait_for(task,1)
            finally:
                if not task.done(): task.cancel()
            write.assert_not_called(); self.assertIn('取消',res.text)
            self.assertFalse(self.a.cur_cmd.native_started)
    async def test_long_wait_warns_once_without_command_arguments(self):
        self.a.session['status']='busy'
        with patch.object(shared,'CONTROL_WARN_S',.01),patch.object(shared,'DESKTOP_IDLE_WAIT',.25),patch.object(shared.os,'write') as write:
            res=await self.a.command('compact','')
        calls=[c for c in self.host.st.log.call_args_list if c.args[0]=='shared_control_wait_warning']
        self.assertEqual(len(calls),1); self.assertEqual(calls[0].kwargs['level'],'warning')
        self.assertEqual(res.kind,'error'); write.assert_not_called()
    async def test_cancelled_queue_item_does_not_block_next_message(self):
        from agentj.agent import Agent
        self.host.turn_lock = None
        self.a.cur_cmd = None
        self.a._identity_refresh = AsyncMock()
        self.a.turn = AsyncMock()
        self.a.q.put_nowait(Cmd('clear',turn=9,cancelled=True))
        self.a.q.put_nowait('the next ordinary message')
        runner = asyncio.create_task(Agent.run(self.a))
        try:
            for _ in range(20):
                if self.a.turn.await_count: break
                await asyncio.sleep(.01)
            self.a.turn.assert_awaited_once_with('the next ordinary message')
            self.host.cmd_done.assert_not_called()
        finally:
            runner.cancel()
            try: await runner
            except asyncio.CancelledError: pass
    async def test_cancelled_last_queued_command_does_not_leave_phone_working(self):
        from agentj.agent import Agent
        self.host.turn_lock = None
        self.a.cur_cmd = None
        self.a.status = 'working'
        self.a.q.put_nowait(Cmd('clear',turn=9,cancelled=True))
        runner = asyncio.create_task(Agent.run(self.a))
        try:
            for _ in range(20):
                if self.a.status == 'idle': break
                await asyncio.sleep(.01)
            self.assertEqual(self.a.status,'idle')
            self.host.cmd_done.assert_not_called()
        finally:
            runner.cancel()
            try: await runner
            except asyncio.CancelledError: pass
    def page(self,device='owner',end='open'):
        self.host.hist.get.return_value={'src':{'dev':device,'k':'cmd'},'end':end}
        self.host.agent=self.a
        return SimpleNamespace(device='owner')
    def test_originating_device_can_cancel_current_and_queued_only_before_write(self):
        s=self.page(); cmd=self.a.cur_cmd
        self.assertTrue(Host.cancel_waiting_cmd(self.host,s,9)); self.assertTrue(cmd.cancelled)
        self.assertFalse(Host.cancel_waiting_cmd(self.host,s,9))
        queued=Cmd('compact',turn=10); self.a.q.put_nowait(queued)
        self.assertTrue(Host.cancel_waiting_cmd(self.host,s,10)); self.assertTrue(queued.cancelled)
        self.host.hist_update.assert_called_with(10,text='已取消，未发送控制。',end='stopped',card={})
    def test_wrong_device_finished_page_sent_control_bad_id_refused(self):
        for device,end,started,turn in [('other','open',False,9),('owner','done',False,9),('owner','open',True,9),('owner','open',False,True)]:
            s=self.page(device,end); self.a.cur_cmd.native_started=started
            self.assertFalse(Host.cancel_waiting_cmd(self.host,s,turn))
        self.assertFalse(self.a.cur_cmd.cancelled)
    def test_waiting_card_and_cancelled_result_not_overwritten(self):
        Host.cmd_waiting(self.host,self.a.cur_cmd)
        self.host.hist_update.assert_called_with(9,text='等电脑空下来再执行',end='open',card={'cmd':'clear','cancel_wait':True})
        self.a.cur_cmd.cancelled=True; self.host.hist_update.reset_mock()
        Host.cmd_done(self.host,self.a.cur_cmd,Mock()); self.host.hist_update.assert_not_called()
