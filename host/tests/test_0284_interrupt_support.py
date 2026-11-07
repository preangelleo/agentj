"""Only proved new-control native routes are clickable; old stop is unchanged."""
import _hermetic
import asyncio,os,pty,subprocess,tty,unittest
from types import SimpleNamespace as NS
from agentj import main_thread
from agentj.shared import SharedClaudeAgent,SharedOpenCodeAgent
from agentj.shared_opencode2 import OwnerOpenCodeV2Agent
import test_579_main_thread as fixtures

class Support(unittest.IsolatedAsyncioTestCase):
    async def owner(self):
        f=fixtures.SignedInterrupt();await f.asyncSetUp();self.addCleanup(f.temp.cleanup)
        return f
    async def test_opencode_and_owner_v2_are_not_falsely_current_turn_capable(self):
        for cls in (SharedOpenCodeAgent,OwnerOpenCodeV2Agent):
            f=await self.owner();a=cls.__new__(cls)
            a.host=f.host;a.cfg={};a.status='working';a._main_dispatch_available=True
            f.host.agent=a;guard=main_thread.of(a);guard.waiting_at=guard.clock()
            status=guard.snapshot();self.assertFalse(status['can_interrupt'])
            self.assertEqual(status['interrupt_reason'],'native_turn_binding_unavailable')
            await main_thread.interrupt(f.host,f.session,f.signed({'turn':7,'session':guard.session}))
            self.assertFalse(f.responses[-1]['ok']);self.assertEqual(f.responses[-1]['why'],'native_turn_binding_unavailable')
    async def claude(self):
        f=await self.owner();a=SharedClaudeAgent.__new__(SharedClaudeAgent)
        a.host=f.host;a.cfg={};a.status='working';a.q=asyncio.Queue();a.state=NS(status='working')
        a.control_name=None;a.menu_desk=NS(current=None);a.done=asyncio.Event()
        a.native=None;a.master=None;a.session={'pid':0,'sessionId':'synthetic'}
        f.host.agent=a;f.native=a;guard=main_thread.of(a);guard.waiting_at=guard.clock();f.native_guard=guard
        return f
    async def test_claude_herdr_route_is_not_new_turn_bound_control(self):
        f=await self.claude();self.assertFalse(f.native_guard.snapshot()['can_interrupt'])
        await main_thread.interrupt(f.host,f.session,f.signed({'turn':7,'session':f.native_guard.session}))
        self.assertFalse(f.responses[-1]['ok'])
    async def test_owned_real_pty_writes_esc_to_captured_fd_and_keeps_queue(self):
        f=await self.claude();master,slave=pty.openpty();tty.setraw(slave)
        self.addCleanup(os.close,master);self.addCleanup(os.close,slave)
        proc=subprocess.Popen(['/bin/sleep','5'],stdin=slave,stdout=slave,stderr=subprocess.DEVNULL)
        self.addCleanup(lambda:(proc.terminate(),proc.wait(timeout=2)))
        a=f.native;a.master=master;a.native=proc;a.session={'pid':proc.pid,'sessionId':'synthetic'}
        a.q.put_nowait('queued-owner-message')
        self.assertTrue(f.native_guard.snapshot()['can_interrupt'])
        await main_thread.interrupt(f.host,f.session,f.signed({'turn':7,'session':f.native_guard.session}))
        self.assertEqual(os.read(slave,1),b'\x1b');self.assertTrue(f.responses[-1]['ok'])
        self.assertTrue(a.done.is_set());self.assertEqual(a.q.get_nowait(),'queued-owner-message')
    async def test_claude_pending_native_confirmation_has_no_interrupt_capability(self):
        f=await self.claude();f.native.master=1;f.native.native=NS(pid=42);f.native.session={'pid':42}
        f.native.state.status='waiting'
        self.assertFalse(f.native_guard.snapshot()['can_interrupt'])
        self.assertEqual(f.native_guard.snapshot()['interrupt_reason'],'native_confirmation_pending')
