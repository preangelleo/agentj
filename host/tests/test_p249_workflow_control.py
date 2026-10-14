"""P249: local workflow-lock contention, device ownership and native boundary."""
import _hermetic
import asyncio
import contextlib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from agentj.agent import Agent, Cmd, make
from agentj.serve import Host
from agentj.slash import Result


class WorkflowControl(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pages = {}
        self.host = SimpleNamespace(lang="zh", task_label="日报", turn_lock=asyncio.Lock(),
            st=Mock(), agent_status=Mock(), agent_turn_start=Mock(), agent_turn_end=Mock(),
            cmd_done=Mock(), model_set_done=Mock(), hist_update=Mock(),
            hist=SimpleNamespace(get=lambda turn: self.pages.get(turn)))
        for name in ("cmd_waiting", "workflow_cmd_waiting", "cmd_lock_acquired", "cancel_waiting_cmd"):
            setattr(self.host, name, getattr(Host, name).__get__(self.host))
        self.a = Agent(self.host, {"dir": "/tmp", "_workflow_ceo": True})
        self.host.agent = self.a
        self.a.command = AsyncMock(return_value=Result("ok"))
        self.a.is_down = lambda: False
        self.runner = asyncio.create_task(self.a.run())
        await self.host.turn_lock.acquire()

    async def asyncTearDown(self):
        self.runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.runner
        if self.host.turn_lock.locked():
            self.host.turn_lock.release()

    def cmd(self, name="compact", turn=1):
        self.pages[turn] = {"src": {"k": "cmd", "dev": "owner"}, "end": "open"}
        return Cmd(name, turn=turn)

    async def dequeued(self, cmd):
        self.a.submit(cmd)
        for _ in range(30):
            if getattr(self.a, "lock_cmd", None) is cmd:
                return
            await asyncio.sleep(.001)
        self.fail("command never reached lock")

    async def released(self):
        self.host.task_label = None
        self.host.turn_lock.release()
        for _ in range(30):
            if getattr(self.a, "lock_cmd", None) is None and self.a.q.empty():
                return
            await asyncio.sleep(.001)
        self.fail("queue did not drain")

    async def test_immediate_bilingual_wait_uses_existing_card(self):
        for lang, needle in (("zh", "压缩尚未发送"), ("en", "compaction has not been sent")):
            self.host.lang = lang
            cmd = self.cmd()
            self.assertTrue(self.host.workflow_cmd_waiting(cmd))
            call = self.host.hist_update.call_args
            self.assertIn(needle, call.kwargs["text"])
            self.assertIn("日报", call.kwargs["text"])
            self.assertEqual(call.kwargs["card"], {"cmd": "compact", "cancel_wait": True})
        self.a.command.assert_not_awaited()

    async def test_phone_slash_receipt_does_not_wait_for_lock(self):
        self.host.cmd_turn = Mock(return_value=1)
        self.host.cmd_card = Mock()
        self.host.stopped = lambda: False
        self.host.activity = Mock()
        self.host.cmd_waiting = Host.cmd_waiting.__get__(self.host)
        self.pages[1] = {"src": {"dev": "owner"}, "end": "open"}
        s = SimpleNamespace(device="owner", name="Phone", cid="cid")
        turn = await asyncio.wait_for(Host.on_slash(self.host, s, "compact", "", False, True), .1)
        self.assertEqual(turn, 1)
        self.assertIn("压缩尚未发送", self.host.hist_update.call_args.kwargs["text"])
        self.a.command.assert_not_awaited()

    async def test_dequeued_cancel_survives_unlock_without_stopping_workflow(self):
        cmd = self.cmd()
        await self.dequeued(cmd)
        self.assertTrue(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
        self.assertTrue(self.host.turn_lock.locked())
        self.a.command.assert_not_awaited()
        await self.released()
        self.a.command.assert_not_awaited()
        self.host.cmd_done.assert_not_called()
        self.assertIsNone(self.a.lock_cmd)

    async def test_other_device_bad_id_closed_page_and_repeat_refused(self):
        cmd = self.cmd()
        await self.dequeued(cmd)
        self.assertFalse(self.host.cancel_waiting_cmd(SimpleNamespace(device="other"), 1))
        self.assertFalse(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), True))
        self.pages[1]["end"] = "done"
        self.assertFalse(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
        self.pages[1]["end"] = "open"
        self.assertTrue(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
        self.assertFalse(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
        await self.released()
        self.a.command.assert_not_awaited()

    async def test_queued_clear_can_cancel_and_next_command_runs_under_lock(self):
        first = self.cmd()
        await self.dequeued(first)
        second = self.cmd("clear", 2)
        self.host.workflow_cmd_waiting(second)
        self.a.submit(second)
        self.assertTrue(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 2))
        async def command(*args):
            self.assertTrue(self.host.turn_lock.locked())
            self.assertTrue(first.native_started)
            self.assertEqual(self.host.hist_update.call_args.kwargs["text"], "开始处理命令。")
            self.assertFalse(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
            return Result("not supported by this adapter", "refused")
        self.a.command.side_effect = command
        await self.released()
        self.a.command.assert_awaited_once_with("compact", "")
        self.assertEqual(self.host.cmd_done.call_args.args[1].kind, "refused")

    async def test_no_workflow_no_cancel_offer_for_other_adapters(self):
        self.host.task_label = None
        cmd = self.cmd()
        self.assertFalse(self.host.workflow_cmd_waiting(cmd))
        await self.dequeued(cmd)
        self.assertFalse(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
        self.host.hist_update.assert_not_called()
        await self.released()
        self.a.command.assert_awaited_once()

    async def test_non_control_does_not_offer_withdrawal(self):
        cmd = self.cmd("context")
        await self.dequeued(cmd)
        self.assertFalse(cmd.workflow_wait)
        self.assertFalse(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
        await self.released()

    async def test_runner_shutdown_removes_dequeued_handle(self):
        await self.dequeued(self.cmd())
        self.runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.runner
        self.assertIsNone(self.a.lock_cmd)
        self.a.command.assert_not_awaited()

    async def test_each_real_adapter_inherits_same_lock_wait(self):
        for kind in ("claude", "codex", "opencode"):
            for mode in ("independent", "shared"):
                with self.subTest(kind=kind, mode=mode):
                    a = make(self.host, {"kind": kind, "session_mode": mode, "dir": "/tmp", "fence": False})
                    self.host.agent = a
                    cmd = self.cmd()
                    entered = asyncio.Event()
                    async def waiter():
                        async with a._command_lock(cmd):
                            entered.set()
                    t = asyncio.create_task(waiter())
                    try:
                        for _ in range(30):
                            if getattr(a, "lock_cmd", None) is cmd:
                                break
                            await asyncio.sleep(.001)
                        self.assertFalse(entered.is_set())
                        self.assertTrue(cmd.workflow_wait)
                        self.assertTrue(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
                    finally:
                        t.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await t


    async def test_scheduler_names_lock_owner_before_harness_shutdown(self):
        from agentj import tasks
        from agentj.state import State
        from pathlib import Path
        import tempfile
        # P248 integration adds a persistent run ledger: supply a real isolated State.
        # The workflow-lock/cancellation assertions below remain unchanged.
        self.host.st = State(Path(self.enterContext(tempfile.TemporaryDirectory(prefix="p249-ledger-"))))
        self.host.turn_lock.release()
        self.host.task_label = None
        self.host.agent_cfg = {"kind": "claude", "dir": "/tmp"}
        self.host.stopped = lambda: False
        self.host.activity = Mock()
        e = {"id": "daily", "dir": "/tmp", "tsha": "a" * 64, "problems": [], "task": {"title": {"zh": "日报"}, "mode": "research"}}
        scheduler = tasks.Scheduler(self.host)
        scheduler._exec = AsyncMock(return_value=("VERDICT: ok — done", 0))
        scheduler._kill = AsyncMock()
        scheduler._save = Mock(return_value=None)
        scheduler._check_report = Mock()
        scheduler._finish = Mock()
        async def halt(**kw):
            self.assertEqual(kw, {"clear_queue": False})
            cmd = self.cmd()
            self.assertTrue(self.host.workflow_cmd_waiting(cmd))
            await self.dequeued(cmd)
            self.assertTrue(self.host.cancel_waiting_cmd(SimpleNamespace(device="owner"), 1))
        self.a.halt = AsyncMock(side_effect=halt)
        with patch.object(tasks, "find", return_value=e):
            res = await scheduler.run("daily", "manual")
        self.assertEqual(res["verdict"], "ok")
        self.assertIsNone(self.host.task_label)
        self.assertFalse(self.host.turn_lock.locked())
        for _ in range(30):
            if self.a.lock_cmd is None:
                break
            await asyncio.sleep(.001)
        self.a.command.assert_not_awaited()
