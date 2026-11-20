"""S0300-1: exact login-spawn boundaries after real settings UV; no external login."""
import _hermetic
import asyncio
import copy
import unittest
from unittest.mock import patch, Mock
from test_0300_avon import Settings
from agentj import accounts, auth_rescue


class Window(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Settings.asyncSetUp
    request = Settings.request
    finish = Settings.finish

    def change(self, kind):
        h=self.h
        if kind=='device':h.st.remove_device(self.phone.did)
        elif kind=='session':h.sessions[self.session.cid]=copy.copy(self.session)
        elif kind=='epoch':h.hist.epoch='replacement-epoch'
        elif kind=='signing_key':h.st.add_device(self.session.pub,'Synthetic replacement',b'x'*32)
        elif kind=='passkey':
            from test_p57_passkey import Authenticator, pk_record
            self.assertTrue(h.st.set_passkey(self.session.pub,pk_record(Authenticator()),peer_ok=True))
        elif kind=='estop':h.estop['on']=True

    async def window(self, change=None):
        self.ar._codex=auth_rescue.Rescue._codex.__get__(self.ar,auth_rescue.Rescue)
        allocate=accounts.new_codex_slot
        allocated=[]
        def slot(root):
            iid,path=allocate(root);allocated.append(path)
            self.change(change)
            return iid,path
        started=Mock(side_effect=auth_rescue.RescueError('cancelled'))
        with patch.object(accounts,'new_codex_slot',side_effect=slot),patch.object(auth_rescue.CodexFlow,'start',started):
            task,a=await self.request()
            await self.h._answer(self.session,self.phone.answer(self.h._ask_msg(a,self.session),True))
            self.assertEqual(await task,{'ok':True})
            for _ in range(200):
                if self.ar.flow.get('stage')=='failed':break
                await asyncio.sleep(.005)
        self.assertEqual(started.call_count,0 if change else 1)
        self.assertTrue(allocated)
        self.assertTrue(all(not path.exists() for path in allocated),'only the newly allocated slot must be discarded')
        self.assertFalse(self.h.__dict__.get('_auth_settings_requests'))

    async def test_device_revoked_during_slot_allocation(self):await self.window('device')
    async def test_session_replaced_during_slot_allocation(self):await self.window('session')
    async def test_epoch_replaced_during_slot_allocation(self):await self.window('epoch')
    async def test_signing_key_replaced_during_slot_allocation(self):await self.window('signing_key')
    async def test_passkey_replaced_during_slot_allocation(self):await self.window('passkey')
    async def test_estop_during_slot_allocation(self):await self.window('estop')
    async def test_unchanged_settings_start_once_and_cleanup_own_failed_slot(self):await self.window()

    async def test_actual_codex_spawn_consumes_subject_once_after_environment_preparation(self):
        from agentj.list_approval import SettingsContext
        _,slot=accounts.new_codex_slot(self.h.st.root)
        context=SettingsContext.capture(self.h,self.ar,self.session,{'summary':'Synthetic Codex sign in'})
        spawn=Mock(side_effect=auth_rescue.RescueError('cancelled'))
        async def work():
            await self.ar._start_command(auth_rescue.CodexFlow(home=slot).start)
        with patch.object(auth_rescue,'binary',return_value='/synthetic/codex'), \
                patch.object(auth_rescue,'harness_env',return_value={}),patch.object(auth_rescue.subprocess,'Popen',spawn):
            with self.assertRaises(auth_rescue.RescueError):
                await self.ar._confirmed_start(work(),context,'codex')
        spawn.assert_called_once()
        self.assertFalse(context.live(self.h));self.assertFalse(self.h.__dict__.get('_auth_settings_requests'))

    async def test_all_three_official_flow_spawns_recheck_after_environment_preparation(self):
        from agentj.list_approval import SettingsContext
        _,slot=accounts.new_codex_slot(self.h.st.root)
        for factory in (lambda:auth_rescue.CodexFlow(home=slot),lambda:auth_rescue.KimiFlow(),
                        lambda:auth_rescue.ClaudeFlow('setup_token')):
            self.h.estop['on']=False
            context=SettingsContext.capture(self.h,self.ar,self.session,{'summary':'Synthetic official sign in'})
            def environment(*args):
                self.h.estop['on']=True
                return {}
            async def work():await self.ar._start_command(factory().start)
            with patch.object(auth_rescue,'binary',return_value='/synthetic/official-cli'), \
                    patch.object(auth_rescue,'harness_env',side_effect=environment), \
                    patch.object(auth_rescue.subprocess,'Popen') as spawn,patch.object(auth_rescue,'Pty') as pty:
                with self.assertRaisesRegex(auth_rescue.RescueError,'session_changed'):
                    await self.ar._confirmed_start(work(),context,'codex')
            spawn.assert_not_called();pty.assert_not_called()
            self.assertFalse(self.h.__dict__.get('_auth_settings_requests'))


class ClaudeScreenPress(unittest.IsolatedAsyncioTestCase):
    """Real paired on_auth + signed UV. The screen and its final write are stand-ins."""
    asyncSetUp = Settings.asyncSetUp

    async def exercise(self, change):
        import threading
        from agentj import approvals
        entered, resume = threading.Event(), threading.Event()
        writes = []
        self.h.ask_ttl = 10
        blocker = {"id": "mcp", "action": "confirm", "zh": "本次不启用 MCP", "en": "Skip this MCP server"}
        loop_thread = threading.get_ident()

        def write(key):
            self.assertEqual(threading.get_ident(), loop_thread)
            writes.append(key)

        def check(*args, guarded_send=None):
            if guarded_send is None:
                return {"blocker": blocker}
            entered.set()
            if not resume.wait(5):
                raise AssertionError("fixture not released")
            act = {"id": "mcp", "action": "confirm", "keys": "fixed key"}
            guarded_send(write, act)
            guarded_send(write, act)
            return {"answered": []}

        with patch("agentj.harness_setup.enabled", return_value=False), patch.object(auth_rescue, "conflicts", return_value=[]), \
                patch("agentj.claude_live.deliver", return_value={"verified": True}), \
                patch.object(auth_rescue, "screen_check", side_effect=check), \
                patch("agentj.harness.probe", return_value={"harnesses": [{"name": "claude", "callable": True}]}), \
                patch.object(self.h, "agent_notice"), patch.object(self.ar, "view", return_value={}):
            await self.ar.recover("claude", route="login")
            screen = self.ar.last["claude"]["screen"]
            frame = {"r": "b302press", "kind": "claude", "id": screen["id"]}
            await self.h.on_auth(self.session, "auth_press", frame)
            try:
                async with asyncio.timeout(3):
                    while not self.h.asks:
                        await asyncio.sleep(.005)
                ask = next(iter(self.h.asks.values()))
                self.assertTrue(ask.require_passkey)
                self.assertEqual(ask.only, self.session.device)
                self.assertEqual(ask.input_sha, approvals.input_digest({
                    "summary": screen["label"], "operation": "press_screen", "harness": "claude",
                    "screen_id": screen["id"], "action": "mcp", "request_id": frame["r"]}))
                answer = self.phone.answer(self.h._ask_msg(ask, self.session), True)
                if change == "unsigned":
                    answer.pop("fa")
                    await self.h._answer(self.session, answer)
                    self.assertFalse(ask.fut.done())
                    answer = self.phone.answer(self.h._ask_msg(ask, self.session), False)
                await self.h._answer(self.session, answer)
                if change != "unsigned":
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    if change == "disconnect":
                        self.h.sessions.pop(self.session.cid)
                    elif change == "replace":
                        self.h.sessions[self.session.cid] = copy.copy(self.session)
                    elif change == "revoke":
                        self.h.st.remove_device(self.session.device)
                    elif change == "cancel":
                        await self.h.on_auth(self.session, "auth_cancel", {"r": "b302cancel"})
                        writes.append("cancel receipt")
                    elif change == "cancel_task":
                        active = next(t for t in self.h._bg_tasks if t.get_coro().__name__ == "recover")
                        active.cancel()
                        await asyncio.sleep(0)
                        self.assertFalse(active.done())
                        self.assertIsNotNone(self.ar._claude_screen.claim)
            finally:
                resume.set()
                if self.h.__dict__.get("_bg_tasks"):
                    await asyncio.gather(*self.h._bg_tasks, return_exceptions=True)
            self.assertEqual(writes, ["fixed key"] if change is None else ["cancel receipt"] if change == "cancel" else [])
            self.assertFalse(self.h.__dict__.get("_auth_settings_requests"))

    async def test_valid_bound_confirmation_writes_once(self):
        await self.exercise(None)

    async def test_unsigned_approval_never_reaches_worker(self):
        await self.exercise("unsigned")

    async def test_replaced_session_cannot_write(self):
        await self.exercise("replace")

    async def test_disconnected_session_cannot_write(self):
        await self.exercise("disconnect")

    async def test_revoked_paired_device_cannot_write(self):
        await self.exercise("revoke")

    async def test_worker_cannot_write_after_cancel_receipt(self):
        await self.exercise("cancel")

    async def test_task_cancellation_holds_claim_until_worker_exits(self):
        await self.exercise("cancel_task")
