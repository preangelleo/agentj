"""Actual native halt/RPC parameter code, offline transport; target and queued work remain exact."""
import _hermetic
import asyncio,unittest
from agentj import main_thread
from agentj.shared_codex import SharedCodexAgent
import test_579_main_thread as fixtures

class NativeTarget(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        f=fixtures.SignedInterrupt();await f.asyncSetUp();self.addCleanup(f.temp.cleanup)
        a=SharedCodexAgent.__new__(SharedCodexAgent)
        for key,value in dict(host=f.host,cfg={},status='working',q=asyncio.Queue(),desktop_wait=False,
                              phone_turn=False,rollout=None,turn_done=asyncio.Event(),tid='thread',turn_id='old-native',
                              proc=object(),halt_quiet=False).items():setattr(a,key,value)
        a.q.put_nowait('queued-owner-message')
        f.host.agent=a;guard=main_thread.of(a);guard.waiting_at=guard.clock()
        f.native=a;f.native_guard=guard;f.calls=[]
        async def desktop():await asyncio.sleep(0);return False
        async def call(method,params,timeout):f.calls.append((method,dict(params)));return {}
        a._desktop_running=desktop;a.call=call
        f.request=f.signed({'turn':7,'session':guard.session})
        return f
    async def test_valid_target_uses_original_native_ids_and_keeps_queue(self):
        f=await self.fixture();await main_thread.interrupt(f.host,f.session,f.request)
        self.assertEqual(f.calls,[('turn/interrupt',{'threadId':'thread','turnId':'old-native'})])
        self.assertTrue(f.responses[-1]['ok']);self.assertEqual(f.native.q.get_nowait(),'queued-owner-message')
    async def test_native_observer_yield_to_new_turn_never_builds_new_turn_rpc(self):
        f=await self.fixture();a=f.native
        async def desktop():
            await asyncio.sleep(0)
            f.host.cur_turn=8;a.turn_id='new-native';a.turn_done=asyncio.Event()
            f.native_guard.reset();f.native_guard.waiting_at=f.native_guard.clock()
            return False
        a._desktop_running=desktop
        await main_thread.interrupt(f.host,f.session,f.request)
        self.assertEqual(f.calls,[]);self.assertFalse(f.responses[-1]['ok'])
        self.assertFalse(a.turn_done.is_set());self.assertEqual(a.q.get_nowait(),'queued-owner-message')
    async def test_rpc_wait_turn_handoff_keeps_original_parameters_and_new_turn_state(self):
        f=await self.fixture();a=f.native;new_generation=[]
        async def call(method,params,timeout):
            await asyncio.sleep(0)
            f.host.cur_turn=8;a.turn_id='new-native';a.turn_done=asyncio.Event()
            f.native_guard.reset();f.native_guard.waiting_at=f.native_guard.clock();new_generation.append(f.native_guard.session)
            f.calls.append((method,dict(params)));return {}
        a.call=call
        await main_thread.interrupt(f.host,f.session,f.request)
        self.assertEqual(f.calls,[('turn/interrupt',{'threadId':'thread','turnId':'old-native'})])
        self.assertFalse(a.turn_done.is_set());self.assertEqual(f.native_guard.session,new_generation[0])
        self.assertTrue(f.native_guard.snapshot()['owner_waiting']);self.assertEqual(a.q.get_nowait(),'queued-owner-message')
    async def test_revocation_during_native_observation_refuses_rpc(self):
        f=await self.fixture()
        async def desktop():
            await asyncio.sleep(0);f.host._paired_live=lambda session:False;return False
        f.native._desktop_running=desktop
        await main_thread.interrupt(f.host,f.session,f.request)
        self.assertEqual(f.calls,[]);self.assertFalse(f.responses[-1]['ok'])
