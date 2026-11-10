"""ADR-0305 (customer M1, 0.30.3, shared Codex): a native sub-agent spawned on a phone turn ended ~3 s later — the phone
turn's end killed the app-server it ran in — and the notice read 「已结束（…退出码 未知）」 like a finished job.

1. The shared Codex app-server is kept while its own native sub-agents run (released when they end, on /stop, at the cap).
2. Every Board ending carries why it ended (outcome + end_reason); an ending without an exit code or a completion event is
   「结果不明」 with a next step, never 「已结束」, and is never replayed; a missing agreed report says so plainly.
Hermetic: real busy.Board on a throw-away state root, the app-server side is stubbed (no process, no ~/.codex, no systemctl).
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agentj import background_done as done, busy, shared_codex
from agentj.agent_codex import CodexAgent
from agentj.shared_codex import SharedCodexAgent

TID = "11111111-1111-1111-1111-111111111111"
SCOPE = "shared-codex"


def notifier(root, lang="zh"):
    a = SimpleNamespace(status="idle", q=asyncio.Queue(), is_down=lambda: False, native_busy=lambda: False,
                        cfg={"session_mode": "shared"})
    a.submit = a.q.put_nowait
    h = SimpleNamespace(st=SimpleNamespace(root=root, log=Mock()), lang=lang, agent=a, preferences={},
                        busy=busy.Board(root), stopped=lambda: False, _open_question=lambda: False,
                        hist_add=Mock(return_value={"id": 1}))
    return h, done.Notifier(h)


def spawn(board, child="c1", label="写调研报告到 report.md"):
    """What the shared Codex app-server sends for spawn_agent: the collab tool call, then the child's own turn."""
    item = {"type": "collabAgentToolCall", "tool": "spawnAgent", "id": "call1", "status": "inProgress", "prompt": label}
    board.codex_event("item/started", {"threadId": TID, "item": item}, SCOPE)
    board.codex_event("item/completed", {"threadId": TID, "item": {**item, "status": "completed", "receiverThreadIds": [child]}}, SCOPE)
    board.codex_event("turn/started", {"threadId": child, "turn": {"id": "ct1"}}, SCOPE)
    return f"{SCOPE}:th:{child}"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p0305-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.h, self.n = notifier(self.root)
        self.b = self.h.busy

    def text(self):
        send = self.n.tick(now=time.time() + 1)
        self.assertIsNotNone(send)
        self.assertEqual(send.by, "background_done")
        page = self.h.hist_add.call_args.args[0]          # what the owner sees on the phone is the same text
        self.assertEqual(page["via"], "background_done")
        return send.text


class Notice(Base):
    def test_1_three_second_early_end_is_unknown_not_ended(self):
        agent = SharedCodexAgent(Mock(busy=self.b), {"kind": "codex", "dir": str(self.root), "shared_session_id": TID})
        agent.proc = SimpleNamespace(pid=os.getpid(), returncode=None)
        item = {"type": "collabAgentToolCall", "tool": "spawnAgent", "id": "call1", "status": "inProgress", "prompt": "写报告"}
        agent._busy("item/started", {"threadId": TID, "item": item})
        agent._busy("item/completed", {"threadId": TID, "item": {**item, "status": "completed", "receiverThreadIds": ["c1"]}})
        agent._busy("turn/started", {"threadId": "c1", "turn": {"id": "ct1"}})
        self.assertIn(f"{SCOPE}:th:c1", self.b.mem)
        self.b.mem[f"{SCOPE}:th:c1"]["since"] -= 3
        agent._busy(None, None)                          # the app-server exited (session_gone)
        rec = self.b.finished[-1]
        self.assertEqual((rec["outcome"], rec["end_reason"], rec["exit_code"]), ("unknown", "process_ended", None))
        text = self.text()
        for needle in ("结果不明", "用时 3 秒", "退出码未知", "它所在的 Codex 进程已结束", "不会自动重放", "下一步：", "「未交付」"):
            self.assertIn(needle, text)
        self.assertNotIn("已结束（", text)
        self.assertNotIn("后台活『写报告』已结束", text)
        self.assertNotRegex(text, "\u8bd5\u7528|t" + "rial")     # glossary: no such wording (spelled so this file has none)

    def test_2_unknown_exit_code_without_outcome(self):
        self.b.finish(busy.item("run:x", None, "temp", "无名之活", since=100), None, 110)
        text = self.text()
        self.assertIn("后台活『无名之活』结果不明（用时 10 秒，退出码未知）：没有收到它的完成事件。不要当作完成，也不会自动重放。", text)
        self.assertIn("下一步：先只读核对派单约定的报告或产物", text)
        self.assertNotIn("已结束（", text)

    def test_2b_english_and_reasons(self):
        other = tempfile.TemporaryDirectory(prefix="p0305-", dir=TEST_TMP)
        self.addCleanup(other.cleanup)
        h, n = notifier(Path(other.name), lang="en")
        h.busy.finish({**busy.item("codex:th:e", None, "temp", "probe", since=100), "harness": "codex"}, None, 104,
                      outcome="unknown", end_reason="process_ended")
        send = n.tick(now=time.time() + 1)
        self.assertIn("Background job 'probe' has an unknown result (4s, exit code unknown): the Codex process it ran in "
                      "has ended without its completion event. Do not treat it as done; it will not be replayed automatically.",
                      send.text)
        self.assertIn("Next: first check, read-only", send.text)
        self.assertNotIn(" ended (", send.text)
        for outcome, why, needle in (("unknown", "thread_idle", "它已停止运行，但没有收到完成事件"),
                                     ("interrupted", "close_agent", "它被中断了"), ("failed", "agent_state", "它报告失败")):
            with self.subTest(why=why), tempfile.TemporaryDirectory(prefix="p0305-", dir=TEST_TMP) as other:
                h2, n2 = notifier(Path(other))
                h2.busy.finish(busy.item("codex:th:" + why, None, "temp", "活", since=100), None, 101, outcome=outcome, end_reason=why)
                self.assertIn(needle, n2.tick(now=time.time() + 1).text)

    def test_3_agreed_report_missing(self):
        missing = self.root / "out" / "report.md"
        busy.write_finished(self.root, busy.item("run:rep", None, "temp", "写报告", since=100, report=str(missing)), 0)
        self.b.refresh_outside(units=False)
        text = self.text()
        self.assertIn("约定的报告没有生成或为空：", text)
        self.assertIn("未交付；先核对退出原因与日志，不自动重放未知结果任务。", text)
        self.assertIn("退出码 0", text)                   # an exit code is known: the original sentence stays

    def test_4_completed_child_keeps_original_wording(self):
        k = spawn(self.b)
        self.b.codex_event("turn/completed", {"threadId": "c1", "turn": {"id": "ct1", "status": "completed"}}, SCOPE)
        self.assertNotIn(k, self.b.mem)
        rec = self.b.finished[-1]
        self.assertEqual((rec["outcome"], rec["end_reason"]), ("completed", "turn_completed"))
        text = self.text()
        self.assertIn("已结束（用时", text)
        self.assertIn("退出码 未知", text)
        self.assertNotIn("结果不明", text)
        self.assertNotIn("下一步：先只读核对", text)

    def test_4b_report_present_is_delivered(self):
        report = self.root / "report.md"
        it = busy.item("run:ok", None, "temp", "写报告", since=int(time.time()) - 5, report=str(report))
        report.write_text("done\n")
        busy.write_finished(self.root, it, 0)
        self.b.refresh_outside(units=False)
        rec = self.b.finished[-1]
        self.assertEqual(rec.get("delivery"), "delivered")
        text = self.text()
        self.assertIn("后台活『写报告』已结束（用时", text)
        self.assertIn("退出码 0", text)
        self.assertNotIn("未交付", text)
        self.assertNotIn("结果不明", text)


class Outcomes(Base):
    def last(self):
        rec = self.b.finished[-1]
        return rec.get("outcome"), rec.get("end_reason")

    def test_codex_turn_status(self):
        for status, want in (("completed", "completed"), ("interrupted", "interrupted"), ("failed", "failed"), ("weird", "unknown")):
            with self.subTest(status=status):
                spawn(self.b, child=status)
                self.b.codex_event("turn/completed", {"threadId": status, "turn": {"status": status}}, SCOPE)
                self.assertEqual(self.last(), (want, "turn_completed"))

    def test_codex_agent_states_and_close_and_activity(self):
        for st, want in (("completed", "completed"), ("errored", "failed"), ("shutdown", "interrupted"),
                         ("interrupted", "interrupted"), ("notFound", "unknown")):
            with self.subTest(state=st):
                spawn(self.b, child="s" + st)
                it = {"type": "collabAgentToolCall", "tool": "wait", "id": "w", "status": "completed",
                      "agentsStates": {"s" + st: {"status": st}}}
                self.b.codex_event("item/completed", {"threadId": TID, "item": it}, SCOPE)
                self.assertEqual(self.last(), (want, "agent_state"))
        spawn(self.b, child="cl")
        self.b.codex_event("item/completed", {"threadId": TID, "item": {"type": "collabAgentToolCall", "tool": "closeAgent",
                                                                         "status": "completed", "receiverThreadIds": ["cl"]}}, SCOPE)
        self.assertEqual(self.last(), ("interrupted", "close_agent"))
        for kind in ("completed", "interrupted"):
            spawn(self.b, child="a" + kind)
            self.b.codex_event("item/completed", {"threadId": TID, "item": {"type": "subAgentActivity", "kind": kind,
                                                                             "agentThreadId": "a" + kind}}, SCOPE)
            self.assertEqual(self.last(), (kind, "subagent_activity"))

    def test_claude_and_opencode(self):
        for status, want in (("completed", "completed"), ("failed", "failed"), ("error", "failed"), ("stopped", "interrupted"),
                             ("killed", "interrupted")):
            with self.subTest(status=status):
                self.b.stream_event({"type": "system", "subtype": "task_started", "task_id": status, "description": "d"}, "own")
                self.b.stream_event({"type": "system", "subtype": "task_notification", "task_id": status, "status": status}, "own")
                self.assertEqual(self.last(), (want, "task_notification"))
        self.b.stream_event({"type": "system", "subtype": "task_started", "task_id": "u", "description": "d"}, "own")
        self.b.stream_event({"type": "system", "subtype": "task_updated", "task_id": "u", "patch": {"status": "cancelled"}}, "own")
        self.assertEqual(self.last(), ("interrupted", "task_updated"))
        for ev, want in (("succeeded", "completed"), ("failed", "failed"), ("interrupted", "interrupted")):
            self.b.child[f"opencode:s:{ev}"] = {"label": "oc"}
            self.b.start(f"opencode:s:{ev}", None, label="oc")
            self.b.opencode2_event({"type": "session.execution." + ev, "data": {"sessionID": ev}}, "parent")
            self.assertEqual(self.last(), (want, "session_execution"))
        part = {"type": "tool", "tool": "task", "sessionID": "parent", "callID": "t1"}
        self.b.opencode_event({"type": "message.part.updated", "properties": {"part": {**part, "state": {"status": "running"}}}}, "parent")
        self.b.opencode_event({"type": "message.part.updated", "properties": {"part": {**part, "state": {"status": "error"}}}}, "parent")
        self.assertEqual(self.last(), ("failed", "task_part"))

    def test_unspecified_callers_have_no_outcome(self):
        self.b.start("x:1", None, label="l")
        self.b.stop("x:1", exit_code=0)
        self.assertEqual(self.last(), (None, None))
        self.b.finish(busy.item("x:2", None, "temp"), outcome="bogus", end_reason="Has Space")
        self.assertEqual(self.last(), (None, None))

    def test_session_gone_is_process_ended(self):
        spawn(self.b)
        self.b.session_gone(SCOPE)
        self.assertEqual(self.last(), ("unknown", "process_ended"))


class RefreshIdle(unittest.IsolatedAsyncioTestCase):
    async def test_thread_idle_is_unknown(self):
        with tempfile.TemporaryDirectory(prefix="p0305-", dir=TEST_TMP) as tmp:
            h, n = notifier(Path(tmp))
            a = SharedCodexAgent(Mock(busy=h.busy), {"kind": "codex", "dir": tmp, "shared_session_id": TID})
            for tid, typ, why in (("i1", "idle", "thread_idle"), ("i2", "notLoaded", "thread_not_loaded"), ("i3", "systemError", "thread_error")):
                spawn(h.busy, child=tid)
                a.call = AsyncMock(return_value={"thread": {"status": {"type": typ}}})
                await CodexAgent.refresh_busy(a)
                self.assertEqual((h.busy.finished[-1]["outcome"], h.busy.finished[-1]["end_reason"]), ("unknown", why))
            text = n.tick(now=time.time() + 1).text
            self.assertIn("它已停止运行，但没有收到完成事件", text)
            self.assertNotIn("已结束（", text)


class KeepForChildren(unittest.IsolatedAsyncioTestCase):
    """The phone turn's end no longer ends the app-server its native sub-agents run in."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p0305-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("KEEP_WAIT", 0.05), ("CHILD_POLL", 0.02)):
            p = patch.object(shared_codex, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.board = busy.Board(Path(self.tmp.name))
        host = Mock(busy=self.board)
        host.st.agent_session.return_value = None
        a = SharedCodexAgent(host, {"kind": "codex", "dir": self.tmp.name, "_workflow_ceo": True, "shared_session_id": TID})
        self.p = SimpleNamespace(returncode=None, pid=4242)
        a.proc, a.tid = self.p, TID
        a._ready = AsyncMock(return_value=True)
        a.remind = AsyncMock()
        a.call = AsyncMock(return_value={"turn": {"id": "t1"}})
        a.await_turn = AsyncMock()
        a.read_desktop = Mock()
        a.meter = Mock()

        async def kill(p):           # as CodexAgent._kill: the agent forgets the process it ended
            if a.proc is p:
                a.proc, a.tid = None, None
        a._kill = AsyncMock(side_effect=kill)
        self.a, self.host = a, host

    def logged(self, ev):
        return [c.kwargs for c in self.host.st.log.call_args_list if c.args and c.args[0] == ev]

    async def test_child_alive_keeps_then_releases_when_it_ends(self):
        spawn(self.board)
        await self.a.turn("派一个子 Agent")
        self.assertTrue(self.a.kept)
        await asyncio.sleep(0.2)                       # past KEEP_WAIT: the sub-agent still runs → not ended
        self.a._kill.assert_not_awaited()
        self.assertEqual(self.logged("shared_keep_children"), [{"agent": "codex", "count": 1}])
        self.board.codex_event("turn/completed", {"threadId": "c1", "turn": {"status": "completed"}}, SCOPE)
        await asyncio.sleep(0.1)
        self.a._kill.assert_awaited_once_with(self.p)
        self.assertFalse(self.a.kept)
        self.assertEqual(self.logged("shared_release_children"), [{"agent": "codex", "reason": "done", "count": 0}])

    async def test_next_phone_turn_reuses_the_kept_app_server(self):
        spawn(self.board)
        await self.a.turn("one")
        self.a.call.reset_mock()
        self.assertTrue(await SharedCodexAgent._thread(self.a))
        self.a.call.assert_not_awaited()             # no second spawn + resume
        await asyncio.sleep(0.15)
        self.a._kill.assert_not_awaited()             # the old release timer belongs to the old keep

    async def test_stop_ends_it_at_once(self):
        spawn(self.board)
        await self.a.turn("one")
        self.assertTrue(self.a.kept)
        await self.a.halt(clear_queue=True)
        self.a._kill.assert_awaited_once_with(self.p)
        self.assertEqual(self.logged("shared_release_children")[-1]["reason"], "stop")

    async def test_stop_during_the_phone_turn_is_not_kept(self):
        spawn(self.board)

        async def stopped():
            self.a.turn_done.set()
            await self.a.halt(clear_queue=True)
        self.a.await_turn = AsyncMock(side_effect=stopped)
        await self.a.turn("one")
        self.assertFalse(self.a.kept)
        self.a._kill.assert_awaited_once_with(self.p)
        self.assertEqual(self.logged("shared_keep_children"), [])

    async def test_cap_ends_it_anyway(self):
        spawn(self.board)
        with patch.object(shared_codex, "CHILD_KEEP_MAX", 0.12):
            await self.a.turn("one")
            await asyncio.sleep(0.05)
            self.a._kill.assert_not_awaited()
            await asyncio.sleep(0.25)
        self.a._kill.assert_awaited_once_with(self.p)
        self.assertEqual(self.logged("shared_release_children")[-1]["reason"], "cap")

    async def test_no_children_nothing_queued_ends_at_once(self):
        self.board.start("shared-claude:th:other", None, label="another scope", session="shared-claude")
        await self.a.turn("one")
        self.assertFalse(self.a.kept)
        self.a._kill.assert_awaited_once_with(self.p)

    async def test_queued_keep_respects_children(self):
        spawn(self.board)
        self.a.q.put_nowait("withdrawn later")
        await self.a.turn("one")
        await asyncio.sleep(0.2)
        self.a._kill.assert_not_awaited()            # P282's 10 s release waits for the sub-agent too
        self.board.session_gone(SCOPE)
        await asyncio.sleep(0.1)
        self.a._kill.assert_awaited_once_with(self.p)


# QA-0305-C1: the shape Codex 0.167 writes into the PARENT rollout when a native sub-agent finishes (ids neutral, no paths)
NOTE_TEXT = ('<subagent_notification>\n{"agent_path":"22222222-2222-2222-2222-222222222222","status":{"completed":"child done: '
             'report written"}}\n</subagent_notification>')
NOTE_LINE = {"timestamp": "2026-11-09T15:12:00.792Z", "ordinal": 26, "type": "response_item", "payload": {
    "type": "message", "id": "msg_x", "role": "user", "content": [{"type": "input_text", "text": NOTE_TEXT}],
    "internal_chat_message_metadata_passthrough": {"turn_id": "auto-compact-1", "create_time": 1794237120.79,
                                                   "content_item_kinds": ["multi_agent.subagent_notification"],
                                                   "content_item_metadata": [{"provenance": {"type": "tool", "namespace": "multi_agent_v1"}}]}}}


class SubagentNotification(unittest.TestCase):
    def test_c1_not_a_human_input(self):
        from agentj import provenance
        from agentj.transcript_input import human_input
        p = NOTE_LINE["payload"]
        self.assertFalse(human_input(p, p["content"], NOTE_TEXT))
        bare = {k: v for k, v in p.items() if k != "internal_chat_message_metadata_passthrough"}
        self.assertFalse(human_input(bare, bare["content"], NOTE_TEXT), "the structure alone is enough")
        self.assertEqual(provenance.terminal_input(p, p["content"], NOTE_TEXT), provenance.normalize(None))
        for prose in ("我看到 " + NOTE_TEXT + " 这一段是什么意思？", NOTE_TEXT + "\n帮我看下", "<subagent_notification>hi</subagent_notification>",
                      '<subagent_notification>{"x":1}</subagent_notification>'):
            with self.subTest(prose=prose[:30]):
                self.assertTrue(human_input(bare, bare["content"], prose), "a person's own text stays a person's")

    def test_c1_desktop_mirror_opens_no_page_and_keeps_no_desktop_turn(self):
        import json
        with tempfile.TemporaryDirectory(prefix="p0305-", dir=TEST_TMP) as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            person = {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                           "content": [{"type": "input_text", "text": "桌面上主人自己打的字"}]}}
            done_ev = {"type": "event_msg", "payload": {"type": "task_complete"}}
            rollout.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in (NOTE_LINE, person, done_ev)))
            host = Mock()
            a = SharedCodexAgent(host, {"kind": "codex", "dir": tmp, "shared_session_id": TID})
            a.rollout, a.rollout_sid, a.offset, a.phone_turn = rollout, TID, 0, False
            a.read_desktop()
            host.desktop_input.assert_called_once_with("桌面上主人自己打的字")
            self.assertEqual(host.desktop_end.call_count, 1)
            host.desktop_input.reset_mock()
            rollout.write_text(json.dumps(NOTE_LINE) + "\n")       # only the notification: nothing at all on the phone
            a.offset = 0
            a.read_desktop()
            host.desktop_input.assert_not_called()
            host.desktop_text.assert_not_called()


class StopWithKeptChildren(unittest.IsolatedAsyncioTestCase):
    asyncSetUp, logged = KeepForChildren.asyncSetUp, KeepForChildren.logged

    async def test_c2_stop_after_the_turn_says_it_stopped_the_sub_agents(self):
        from agentj import humane, serve
        spawn(self.board)
        await self.a.turn("one")
        self.assertTrue(self.a.kept)
        fake = SimpleNamespace(agent=self.a, scheduler=SimpleNamespace(stop_running=AsyncMock(return_value=False)),
                               status_changed=lambda: None, lang="zh", preferences=None)
        res = await serve.Host.stop_turn(fake, "phone")
        self.a._kill.assert_awaited_once_with(self.p)
        self.assertEqual(res.text, "这一轮早已结束；已停止 1 个还在跑的子 Agent。")
        self.assertNotIn("不用停", res.text)
        self.assertEqual(humane.say("stop.children", "en", None, n=2), "The turn had already ended; stopped 2 sub-agent(s) still running.")

    async def test_c2_nothing_kept_still_says_nothing_to_stop(self):
        from agentj import serve
        await self.a.turn("one")
        self.assertFalse(self.a.kept)
        fake = SimpleNamespace(agent=self.a, scheduler=SimpleNamespace(stop_running=AsyncMock(return_value=False)),
                               status_changed=lambda: None, lang="zh", preferences=None)
        self.assertEqual((await serve.Host.stop_turn(fake, "phone")).text, "现在没有在跑的一轮，不用停。")


class ReportPathWords(Base):
    def test_a1_missing_report_names_only_the_checked_redacted_path(self):
        from agentj import privacy
        missing = self.root / "out" / "report.md"
        busy.write_finished(self.root, busy.item("run:rep", None, "temp", "写报告", since=100, report=str(missing)), 0)
        self.b.refresh_outside(units=False)
        text = self.text()
        self.assertIn("约定的报告没有生成或为空：" + privacy.redact(str(missing), paths=True) + "。", text)

    def test_a1_an_unclean_report_field_never_enters_the_host_sentence(self):
        tok = "gh" + "p_" + "Q" * 36                              # synthetic token shape, built at run time
        missing = self.root / tok / "report.md"
        busy.write_finished(self.root, busy.item("run:rep2", None, "temp", "写报告", since=100, report=str(missing)), 0)
        self.b.refresh_outside(units=False)
        text = self.text()
        line = [ln for ln in text.splitlines() if "约定的报告没有生成或为空" in ln][0]
        self.assertTrue(line.startswith("约定的报告没有生成或为空。未交付"), line)
        self.assertNotIn(tok, text)

if __name__ == "__main__":
    unittest.main()
