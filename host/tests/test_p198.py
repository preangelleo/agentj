"""P198 (P164 precheck): P1 the compaction / clear recovery on a real Claude Code 2.1.296 (a native_turn flag left by the ended
turn's late lines made `_recover` give up as busy); P2 the phone's `/goal <what>` must never reach Claude Code's own `/goal`
(and a session it hijacked is cleaned); P4 host.log keeps `handoff` / `sha` / `full` / `by`; P5 a model's `</think>` reasoning
never reaches the phone."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import socket
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from agentj import goal, handoff, shared, text
from agentj.state import State

SID = "19819819-1981-1981-1981-19819819aaaa"


class Host:
    lang = "zh"

    def __init__(self):
        self.st = Mock()
        self.phone, self.notices = [], []

    def desktop_input(self, text): pass
    def desktop_text(self, text): pass
    def desktop_end(self): pass
    def desktop_cmd_done(self, name): pass
    def proactive_turn(self, via): pass
    def agent_text(self, text): self.phone.append(text)
    def agent_notice(self, text): self.notices.append(text)
    def agent_status(self, status): pass
    def stopped(self): return False
    def turn_failed(self): pass
    def turn_queued_note(self, on): pass
    def shared_clear(self): return True

    def logged(self, ev):
        return [c.kwargs for c in self.st.log.call_args_list if c.args and c.args[0] == ev]


class Recover(unittest.IsolatedAsyncioTestCase):
    """P1: the shape measured on 2.1.296 — /compact (or /clear) typed in the terminal writes assistant lines after the last
    Stop, no Stop follows, our state and Claude Code's registry are both idle, only native_turn is still True."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p198r-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        cc = root / "claude"
        (cc / "sessions").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(cc), "AGENTJ_STATE_DIR": str(root / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(root / "peer.sock"))
        self.addCleanup(self.sock.close)
        self.session = {"pid": os.getpid(), "cwd": str(self.work), "sessionId": SID, "kind": "interactive",
                        "messagingSocketPath": str(root / "peer.sock"), "status": "idle", "updatedAt": 10}
        self.registry = cc / "sessions" / "owner.json"
        self.registry.write_text(json.dumps(self.session))
        self.host = Host()
        for target, attr, val in ((shared.SharedClaudeAgent, "prepare_channel", AsyncMock()),
                                  (handoff, "RECOVER_DELAY", 0.05)):
            p = patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)
        self.sent = []
        p = patch.object(shared, "claude_send", side_effect=lambda s, text, src=None: self.sent.append((text, src)))
        p.start()
        self.addCleanup(p.stop)
        self.a = shared.SharedClaudeAgent(self.host, {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True,
                                                      "session_mode": "shared", "high_risk_warnings": False})
        self.a.session = dict(self.session)
        self.a.loop = asyncio.get_running_loop()

    async def ev(self, name, **kw):
        return await self.a.hook_event({"hook_event_name": name, "session_id": SID, **kw})

    async def test_compact_recovers_although_late_lines_left_native_turn_set(self):
        await self.ev("UserPromptSubmit", prompt="继续")
        await self.ev("Stop", last_assistant_message="好")
        await asyncio.sleep(0.05)                 # the Stop is finished on the loop (call_soon_threadsafe)
        handoff.ensure()
        handoff.path("compact.md").write_text("# 交接\n")
        self.a.native_turn = True              # the ended turn's last lines, read after its Stop
        await self.ev("PreCompact", trigger="manual")
        await self.ev("PostCompact", trigger="manual")
        await asyncio.sleep(1.5)
        self.assertEqual(len(self.sent), 1, self.host.logged("shared_handoff_recover"))
        self.assertTrue(self.sent[0][0].startswith("〔Agent J 系统提示〕压缩完成。"))
        self.assertFalse(self.a.native_turn)
        self.assertEqual(self.host.logged("shared_native_turn_stale")[0]["why"], "recover")
        self.assertEqual(self.host.notices, ["压缩完成，Agent 正在恢复上下文。"])

    async def test_clear_recovers_although_native_turn_was_set(self):
        handoff.ensure()
        handoff.path("clear.md").write_text("# clear 交接\n")
        self.a.native_turn = True
        await self.ev("SessionStart", source="clear")
        await asyncio.sleep(1.5)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("对话已清空。", self.sent[0][0])

    async def test_clear_context_says_the_file_is_renamed_once_read(self):
        handoff.ensure()
        handoff.path("clear.md").write_text("# clear 交接\n")
        out = await self.ev("SessionStart", source="clear")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("clear.md", ctx)
        self.assertIn("改名为 clear-used.md", ctx)

    async def test_a_busy_registry_still_means_busy(self):
        handoff.ensure()
        handoff.path("compact.md").write_text("# 交接\n")
        self.registry.write_text(json.dumps(dict(self.session, status="busy")))
        self.a.native_turn = True               # Claude Code's own automatic compaction inside a running turn
        await self.ev("PostCompact", trigger="auto")
        await asyncio.sleep(6.5)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.a.native_turn)
        self.assertEqual(self.host.logged("shared_handoff_recover")[0]["result"], "busy")

    async def test_line_stamped_before_the_turn_end_does_not_reopen_it(self):
        await self.ev("UserPromptSubmit", prompt="做事")
        await self.ev("Stop", last_assistant_message="好")
        await asyncio.sleep(0.05)                 # the Stop is finished on the loop (call_soon_threadsafe)
        before = datetime.fromtimestamp(time.time() - 2, timezone.utc).isoformat().replace("+00:00", "Z")
        after = datetime.fromtimestamp(time.time() + 2, timezone.utc).isoformat().replace("+00:00", "Z")
        self.assertTrue(self.a._line_of_ended_turn({"timestamp": before}))
        self.assertFalse(self.a._line_of_ended_turn({"timestamp": after}))
        self.assertFalse(self.a._line_of_ended_turn({}))

    def test_registry_idle_reconciles_a_stale_flag_after_a_while(self):
        self.a.native_turn = True
        with patch.object(shared, "NATIVE_STALE_S", 0.0):
            self.a._reconcile_native("idle")       # starts the clock
            self.assertTrue(self.a.native_turn)
            self.a._reconcile_native("idle")
        self.assertFalse(self.a.native_turn)
        self.assertEqual(self.host.logged("shared_native_turn_stale")[0]["why"], "registry_idle")

    def test_a_busy_registry_resets_the_stale_clock(self):
        self.a.native_turn = True
        with patch.object(shared, "NATIVE_STALE_S", 0.0):
            self.a._reconcile_native("idle")
            self.a._reconcile_native("busy")
            self.a._reconcile_native("idle")
        self.assertTrue(self.a.native_turn)


class GoalNotNative(unittest.TestCase):
    """P2: Claude Code 2.1.x runs a stream-json message starting with `/goal` as its own command."""

    def test_agent_words_never_start_with_a_slash(self):
        for lang in ("zh", "en"):
            w = goal.agent_words("  把 notes.txt 写完，今晚 11 点前 ", lang)
            self.assertFalse(w.lstrip().startswith("/"), w)
            self.assertIn("把 notes.txt 写完，今晚 11 点前", w)

    def test_serve_rewrites_the_agents_copy_only(self):
        src = Path(shared.__file__).with_name("serve.py").read_text()
        i = src.index("goal_mod.start_note(gc[1]")
        self.assertIn("text = goal_mod.agent_words(gc[1]", src[i:i + 400])

    def _transcript(self, rows):
        d = tempfile.mkdtemp(prefix="p198g-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = Path(d) / "t.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return p

    def test_native_goal_active_reads_the_last_status(self):
        set_ = {"type": "attachment", "attachment": {"type": "goal_status", "met": False, "sentinel": True, "condition": "x"}}
        nag = {"type": "attachment", "attachment": {"type": "goal_status", "met": False, "condition": "x", "reason": "no"}}
        cleared = {"type": "attachment", "attachment": {"type": "goal_status", "met": True, "sentinel": True, "condition": "x"}}
        chat = {"type": "assistant", "message": {"content": [{"type": "text", "text": "goal_status mentioned"}]}}
        self.assertTrue(goal.native_goal_active(self._transcript([set_, chat])))
        self.assertTrue(goal.native_goal_active(self._transcript([set_, nag, chat])))
        self.assertFalse(goal.native_goal_active(self._transcript([set_, nag, cleared])))
        self.assertFalse(goal.native_goal_active(self._transcript([chat])))
        self.assertFalse(goal.native_goal_active("/nonexistent/p198.jsonl"))


class ClearHijacked(unittest.IsolatedAsyncioTestCase):
    async def test_a_hijacked_session_is_cleared_once_before_the_owners_message(self):
        from agentj.agent import ClaudeAgent
        host = Mock()
        host.st.agent_session.return_value = SID
        a = ClaudeAgent.__new__(ClaudeAgent)
        a.host, a.kind, a.cfg = host, "claude", {"dir": "/tmp"}
        a.capture, a.native_goal_checked, a.cur_send = None, False, "owner-send"
        states = iter([True, False])
        calls = []

        async def local(cmd):
            calls.append((cmd, a.cur_send))
            return {}
        a.local = local
        with patch.object(ClaudeAgent, "transcript_path", return_value="/tmp/x.jsonl"), \
                patch.object(goal, "native_goal_active", side_effect=lambda t: next(states)):
            self.assertTrue(await a.clear_native_goal())
        self.assertEqual(calls, [("/goal clear", None)])
        self.assertEqual(a.cur_send, "owner-send")
        host.st.log.assert_called_with("native_goal_cleared", agent="claude", result="ok")

    async def test_no_native_goal_sends_nothing(self):
        from agentj.agent import ClaudeAgent
        host = Mock()
        a = ClaudeAgent.__new__(ClaudeAgent)
        a.host, a.kind, a.cfg = host, "claude", {"dir": "/tmp"}
        a.local = AsyncMock()
        with patch.object(ClaudeAgent, "transcript_path", return_value="/tmp/x.jsonl"), \
                patch.object(goal, "native_goal_active", return_value=False):
            self.assertFalse(await a.clear_native_goal())
        a.local.assert_not_awaited()


class LogFields(unittest.TestCase):
    """P4: RUNBOOK P152 reads `shared_precompact handoff=…`; P156 heartbeats log sha / full / by."""

    def test_fields_kept(self):
        for k in ("handoff", "sha", "full", "by"):
            self.assertIn(k, State.LOG_FIELDS)

    def test_written_to_host_log(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"AGENTJ_STATE_DIR": d}):
            st = State()
            st.log("shared_precompact", handoff=True)
            st.log("heartbeat_sent", sha="abcd1234", full=False, by="timer")
            lines = [json.loads(x) for x in st.log_path.read_text().splitlines() if x.strip()]
        self.assertEqual(lines[-2]["handoff"], True)
        self.assertEqual((lines[-1]["sha"], lines[-1]["full"], lines[-1]["by"]), ("abcd1234", False, "timer"))


class Reasoning(unittest.TestCase):
    """P5: OpenCode big-pickle hands over 「reasoning…</think>answer」 (opening tag eaten) or whole <think> blocks."""

    def test_shapes(self):
        s = text.strip_reasoning
        self.assertEqual(s("用户想要文件列表，我先想想……\n</think>\n\n好的，三个文件。"), "好的，三个文件。")
        self.assertEqual(s("<think>plan</think>Answer"), "Answer")
        self.assertEqual(s("Answer <thinking>more</thinking> end"), "Answer end")
        self.assertEqual(s("Partial answer <think>still reasoning"), "Partial answer")
        self.assertEqual(s("<think>only reasoning so far"), "")
        self.assertEqual(s("plain reply"), "plain reply")
        self.assertEqual(s("用 `think>` 这个词不受影响"), "用 `think>` 这个词不受影响")
        self.assertIsNone(s(None))

    def test_independent_opencode_text_part(self):
        from agentj.agent_opencode import OpenCodeAgent
        a = OpenCodeAgent.__new__(OpenCodeAgent)
        a.host, a.emitted, a.quiet, a.collect, a.sid = Mock(), set(), False, None, "ses1"
        a._text_part({"type": "text", "sessionID": "ses1", "id": "p1", "time": {"end": 1},
                      "text": "reasoning here</think>真正的回复"})
        a._text_part({"type": "text", "sessionID": "ses1", "id": "p2", "time": {"end": 1}, "text": "<think>only</think>"})
        self.assertEqual([c.args[0] for c in a.host.agent_text.call_args_list], ["真正的回复"])

    def test_every_text_path_strips(self):
        here = Path(shared.__file__).parent
        for name, needle in (("agent_opencode.py", "strip_reasoning(part.get(\"text\"))"),
                             ("agent_opencode2.py", "text = strip_reasoning(text)"),
                             ("shared.py", "public_text(strip_reasoning("),
                             ("shared_opencode2.py", "public_text(strip_reasoning(text))")):
            self.assertIn(needle, (here / name).read_text(), name)


if __name__ == "__main__":
    unittest.main()
