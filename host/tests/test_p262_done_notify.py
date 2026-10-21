"""Completion evidence → system Send, including busy deferral, batching and short jobs."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agentj import background_done as done, busy, preferences, provenance, main_identity


class Completion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.a = SimpleNamespace(status="idle", q=asyncio.Queue(), is_down=lambda: False,
                                 native_busy=lambda: False, cfg={"session_mode": "shared"})
        self.a.submit = self.a.q.put_nowait
        self.h = SimpleNamespace(st=SimpleNamespace(root=self.root, log=Mock()), lang="zh", agent=self.a,
                                 preferences={}, busy=busy.Board(self.root), stopped=lambda: False,
                                 _open_question=lambda: False, hist_add=Mock(return_value={"id": 1}))
        self.n = done.Notifier(self.h)

    def finish(self, key="run:one", code=0):
        self.h.busy.finish(busy.item(key, "掌枢", "ceo", "测试报告", since=100, report="/tmp/report.md"), code, 125)

    def ack(self, send):
        send.state = "delivered"
        self.n.complete(send)
        self.a.q.get_nowait()

    def test_unit_ended_delivers_system_prompt(self):
        scans = ["agentj-p262.service loaded activating start 测试 · 替身任务\n", ""]
        def run(argv, **kw):
            if "list-units" in argv:
                return SimpleNamespace(returncode=0, stdout=scans.pop(0))
            if "Environment" in argv:
                return SimpleNamespace(returncode=0, stdout="Environment=AGENTJ_REPORT=/tmp/report.md\n")
            return SimpleNamespace(returncode=0, stdout="ActiveState=inactive\nLoadState=loaded\nExecMainCode=1\nExecMainStatus=7\n")
        self.h.busy.refresh_outside(run=run)
        self.assertEqual(len(self.h.busy.items()), 1)
        self.h.busy.refresh_outside(run=run)
        send = self.n.tick(now=1000)
        self.assertIs(send, self.a.q.get_nowait())
        send.state = "delivered"
        self.n.complete(send)
        self.assertIn("退出码 7", send.text)
        self.assertIn("/tmp/report.md", send.text)
        self.assertEqual(provenance.from_name(send.source), provenance.SYSTEM)
        self.assertIn("实际报告与证据", send.text)
        self.assertEqual(send.by, "background_done")
        self.h.busy.refresh_outside(run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=""))
        self.assertIsNone(self.n.tick(now=1061))

    def test_busy_queues_until_turn_ends(self):
        self.a.status = "working"
        self.finish()
        self.assertIsNone(self.n.tick(now=1000))
        self.assertTrue(self.a.q.empty())
        self.assertEqual(len(self.n.pending), 1)
        self.a.status = "idle"
        self.assertIsNotNone(self.n.tick(now=1002))

    def test_native_busy_dialog_stop_and_down_wait(self):
        for attr, value in (("native_busy", lambda: True), ("is_down", lambda: True)):
            old = getattr(self.a, attr)
            setattr(self.a, attr, value)
            self.finish(attr)
            self.assertIsNone(self.n.tick(now=1000))
            setattr(self.a, attr, old)
        self.h.stopped = lambda: True
        self.assertIsNone(self.n.tick(now=1000))
        self.h.stopped = lambda: False
        self.h._open_question = lambda: True
        self.assertIsNone(self.n.tick(now=1000))
        self.h._open_question = lambda: False
        self.assertIsNotNone(self.n.tick(now=1000))

    def test_coalesces_same_poll_and_within_minute(self):
        self.finish("one")
        self.finish("two", 2)
        send = self.n.tick(now=1000)
        self.assertEqual(len(send.background_done_ids), 2)
        self.ack(send)
        self.finish("three")
        self.assertIsNone(self.n.tick(now=1020))
        self.finish("four")
        self.assertIsNone(self.n.tick(now=1059))
        send = self.n.tick(now=1060)
        self.assertEqual(len(send.background_done_ids), 2)
        self.assertIn("用时 25 秒", send.text)

    def test_off_consumes_without_backlog_on_reenable(self):
        self.h.preferences = {"background_done": {"enabled": False}}
        self.finish()
        self.assertIsNone(self.n.tick(now=1000))
        self.h.preferences = {}
        self.assertIsNone(self.n.tick(now=1060))
        self.assertTrue(self.a.q.empty())

    def test_off_discards_held_work(self):
        self.a.status = "working"
        self.finish()
        self.n.tick(now=1000)
        self.h.preferences = {"background_done": {"enabled": False}}
        self.n.tick(now=1002)
        self.assertFalse(self.n.pending)

    def test_restart_preserves_pending_and_deduplicates(self):
        self.a.status = "working"
        self.finish()
        self.n.tick(now=1000)
        self.n = done.Notifier(self.h)
        self.finish()
        self.a.status = "idle"
        send = self.n.tick(now=1002)
        self.assertEqual(len(send.background_done_ids), 1)
        self.ack(send)
        self.n = done.Notifier(self.h)
        self.finish()
        self.assertIsNone(self.n.tick(now=1065))
        self.assertEqual(self.n.path.stat().st_mode & 0o777, 0o600)

    def test_failed_queue_retries_but_uncertain_is_not_replayed(self):
        self.finish()
        send = self.n.tick(now=1000)
        send.state = "failed"
        self.a.q.get_nowait()
        retry = self.n.tick(now=1061)
        self.assertIsNotNone(retry)
        retry.state = "uncertain"
        self.n.complete(retry)
        self.assertIsNone(self.n.tick(now=1122))

    def test_scan_failure_or_running_unit_is_not_completion(self):
        it = busy.item("unit:agentj-p262.service", "测试", "ceo", "替身")
        self.h.busy.units = [it]
        self.h.busy.outside = [it]
        for run in (lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""),
                    lambda *a, **kw: SimpleNamespace(returncode=0, stdout="ActiveState=active\n"),
                    lambda *a, **kw: (_ for _ in ()).throw(OSError())):
            self.h.busy.refresh_outside(run=run)
            self.assertFalse(self.h.busy.finished)
            self.assertEqual(len(self.h.busy.units), 1)

    def test_collected_unit_unknown_not_zero(self):
        got = busy.unit_end("agentj-p262.service", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="LoadState=not-found\n"))
        self.assertIsNone(got["exit_code"])
        self.finish(code=None)
        self.assertIn("退出码 未知", self.n.tick(now=1000).text)

    def test_short_headless_receipt_real_exit_code(self):
        env = dict(os.environ, AGENTJ_STATE_DIR=str(self.root),
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        r = subprocess.run([sys.executable, "-c", "from agentj.cli import main; import sys; sys.exit(main())", "busy", "run", "--label", "极短的活", "--report", "/tmp/report.md", "--",
                            sys.executable, "-c", "raise SystemExit(3)"], env=env, capture_output=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)  # existing CLI ignores handler returns; receipt must still say 3
        self.h.busy.refresh_outside(units=False)
        send = self.n.tick(now=1000)
        self.assertIn("极短的活", send.text)
        self.assertIn("退出码 3", send.text)
        self.h.busy.refresh_outside(units=False)
        self.ack(send)
        self.assertIsNone(self.n.tick(now=1061))

    def test_all_three_harness_subagent_end_events(self):
        b = self.h.busy
        b.start("shared:s:a", None, label="Claude 报告", session="shared:s")
        b.mark_bg("shared:s:a")    # P426: only a background sub-agent's end wakes the main Agent
        b.hook_event({"hook_event_name": "SubagentStop", "session_id": "s", "agent_id": "a"})
        b.child["codex:th:c"] = {"label": "Codex 报告"}
        b.start("codex:th:c", None, label="Codex 报告")
        b.codex_event("turn/completed", {"threadId": "c"})
        b.child["opencode:s:o"] = {"label": "OpenCode 报告"}
        b.start("opencode:s:o", None, label="OpenCode 报告")
        b.opencode2_event({"type": "session.execution.succeeded", "data": {"sessionID": "o"}}, "parent")
        send = self.n.tick(now=1000)
        self.assertEqual(len(send.background_done_ids), 3)

    def test_sensitive_text_redacted_before_persistence(self):
        secret = "sk-" + "x" * 48
        self.h.busy.finish(busy.item("secret", "test", "ceo", secret, report="/tmp/" + secret))
        self.a.status = "working"
        self.n.tick(now=1000)
        self.assertNotIn(secret, self.n.path.read_text())

    def test_receipt_special_files_are_ignored(self):
        folder = busy.items_dir(self.root) / "finished"
        folder.mkdir(parents=True)
        os.mkfifo(folder / "fifo.json")
        (folder / "link.json").symlink_to(folder / "fifo.json")
        self.assertEqual(busy.finished_items(self.root), [])

    def test_adapter_early_return_does_not_lose_pending_completion(self):
        self.finish()
        send = self.n.tick(now=1000)
        self.a.q.get_nowait()  # a failed attachment returns without a native write
        self.assertEqual(send.state, "pending")
        self.assertIsNotNone(self.n.tick(now=1061))
        self.assertEqual(send.state, "failed")

    def test_corrupt_state_is_not_a_host_startup_failure(self):
        self.n.path.parent.mkdir(exist_ok=True)
        for text in ("[]", '{"pending": [], "seen": false}', "not-json"):
            self.n.path.write_text(text)
            self.assertFalse(done.Notifier(self.h).pending)

    def test_same_child_can_finish_two_turns_in_one_second(self):
        b = self.h.busy
        b.start("codex:th:same", None, label="第一轮")
        b.stop("codex:th:same")
        b.start("codex:th:same", None, label="第二轮")
        b.stop("codex:th:same")
        self.assertEqual(len(self.n.tick(now=1000).background_done_ids), 2)

    def test_idle_after_one_turn_stop_has_no_extra_lock(self):
        self.a.halting = True   # SharedClaude.halt keeps this flag until a new turn starts
        self.finish()
        self.assertIsNotNone(self.n.tick(now=1000))

    def test_off_cancels_queued_notice_and_closes_history(self):
        self.finish()
        send = self.n.tick(now=1000)
        def dropped(item):
            item.state = "cancelled"
        self.h.queue_dropped = Mock(side_effect=dropped)
        self.h.preferences = {"background_done": {"enabled": False}}
        self.n.tick(now=1002)
        self.h.queue_dropped.assert_called_once_with(send)
        self.assertEqual(send.state, "cancelled")
        self.assertFalse(self.n.pending)

    def test_defaults_schema_identity(self):
        self.assertTrue(preferences.defaults()["background_done"]["enabled"])
        self.assertEqual(preferences.SCHEMA["background_done.enabled"]["apply"], "hot")
        preferences.validate({"background_done": {"enabled": False}})
        for harness in ("claude", "codex", "opencode"):
            for lang in ("zh", "en"):
                text = main_identity.prompt({"language": lang}, harness)
                self.assertIn("后台活已结束" if lang == "zh" else "background-job-ended", text)


# Exercise the inherited queue with real adapters and local harness stand-ins.
import test_slash as chain
import test_p156_heartbeat as hbtests
from agentj import shared
from unittest.mock import AsyncMock


class _CompletionChain(chain._Chain):
    def setUp(self):
        config = tempfile.TemporaryDirectory()
        self.addCleanup(config.cleanup)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()

    def test_completion_round_trip(self):
        async def script(c):
            host = c["host"]
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            host.busy.finish(busy.item("run:roundtrip", "助理", "ceo", "替身任务", since=100), 4, 130)
            send = host.background_done.tick()
            self.assertIsNotNone(send)
            await c["wait"](lambda: any("后台活『替身任务』已结束" in m["text"] for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(send.state, "delivered")
            self.assertFalse(host.background_done.pending)
            self.assertEqual(host.hist.get(send.turn)["src"]["via"], "background_done")
            self.assertIn('"ev": "background_done_delivery"', self.st.log_path.read_text())
        self.run_chain(script)


class ClaudeCompletion(_CompletionChain):
    KIND = "claude"


class CodexCompletion(_CompletionChain):
    KIND = "codex"


class OpenCodeCompletion(_CompletionChain):
    KIND = "opencode"


del _CompletionChain


class SharedClaudeCompletion(hbtests.SharedClaude):
    async def test_completion_system_notice_in_shared_session(self):
        send = done.CompletionSend(7, "〔Agent J 系统提示〕后台活已结束，请读实际报告。", ["id"])
        self.a.cur_send = send
        task = asyncio.create_task(self.a.turn(send.text))
        try:
            for _ in range(100):
                if self.sent:
                    break
                await asyncio.sleep(0.01)
            [(text, source)] = self.sent
            self.assertEqual(provenance.from_name(source), provenance.SYSTEM)
            await self.a.hook_event({"hook_event_name": "UserPromptSubmit", "session_id": self.sid, "prompt": text})
            await self.a.hook_event({"hook_event_name": "Stop", "session_id": self.sid, "last_assistant_message": "报告已读，任务未通过验收。"})
            await asyncio.wait_for(task, 5)
            self.assertEqual(self.texts, ["报告已读，任务未通过验收。"])
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class SharedCodexCompletion(unittest.IsolatedAsyncioTestCase):
    async def test_completion_uses_existing_thread_and_original_permissions(self):
        from agentj.shared_codex import SharedCodexAgent
        host = Mock()
        a = SharedCodexAgent(host, {"kind": "codex", "dir": "/tmp", "_workflow_ceo": True,
                                   "shared_session_id": "11111111-1111-1111-1111-111111111111"})
        a.tid = a.cfg["shared_session_id"]
        a.original = {"approvalPolicy": "on-request", "permissions": ":read-only"}
        a.proc = Mock()
        a._ready = AsyncMock(return_value=True)
        a.remind = AsyncMock()
        a._kill = AsyncMock()
        a.read_desktop = Mock()
        send = done.CompletionSend(1, "〔Agent J 系统提示〕后台活已结束，请读实际报告。", ["id"])
        a.cur_send = send
        async def call(method, params):
            a.turn_done.set()
            return {"turn": {"id": "completion"}}
        a.call = AsyncMock(side_effect=call)
        await a.turn(send.text)
        method, params = a.call.call_args.args
        self.assertEqual(method, "turn/start")
        self.assertEqual(params["threadId"], a.tid)
        self.assertEqual(params["permissions"], ":read-only")
        self.assertEqual(params["input"][0]["text"], send.text)
        self.assertEqual(send.state, "delivered")
        host.say_delivered.assert_called_once_with(send)


class SharedOpenCodeCompletion(unittest.TestCase):
    def test_completion_posts_to_owner_existing_session(self):
        import test_p63_opencode_shared as octests
        with tempfile.TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config}):
            fixture = octests.OwnerV2(methodName="test_owner_v2_server_attached_text_cards_desktop_mirror")
            fixture.setUp()
            try:
                async def script(c):
                    await c.say("你好")
                    await c.wait(lambda: "ECHO: 你好" in c.msgs())
                    await c.idle()
                    c.host.busy.finish(busy.item("run:oc-shared", "助理", "ceo", "共享报告", since=100), 9, 140)
                    send = c.host.background_done.tick()
                    self.assertIsNotNone(send)
                    await c.wait(lambda: any("后台活『共享报告』已结束" in m for m in c.msgs()))
                    await c.idle()
                    self.assertEqual(send.state, "delivered")
                    self.assertEqual(c.host.agent.sid, fixture.owner_sid)
                fixture.run_chain(script)
                self.assertFalse([r for r in fixture.requests() if r["m"] == "POST" and r["p"] == "/api/session"])
                self.assertIsNone(fixture.owner.poll(), "owner's server stays running")
            finally:
                fixture.tearDown()
                fixture.doCleanups()
