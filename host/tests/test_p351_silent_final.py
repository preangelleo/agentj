"""P351 (Leo 10-16, backlog #61): 〔不回群〕 is judged on the turn's FINAL reply alone.

Codex sends a progress note (an agentMessage with phase "commentary") and then its final answer 〔不回群〕 in the same
turn; joined, the page was no longer exactly the marker and went out as a full card. Now:
- the final reply is exactly 〔不回群〕 (whitespace allowed) → the whole turn is silent: no push, no unread, not in the main
  flow (reply.text = the marker), the earlier notes folded into reply.pre for the silent panel;
- anything else in the final reply → delivered as before (the notes stay in front of it);
- text after the marker → the marker was not the final reply: the notes come back, the page is delivered;
- a Codex heartbeat asks for no progress note first (heartbeat.NO_NOTES), a second layer.
The real adapters (own Claude Code / Codex / OpenCode stand-ins) are run end to end; shared sessions reach the same
serve.agent_text, covered by the host-level cases.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import heartbeat as hb, history, serve  # noqa: E402
from agentj.state import State  # noqa: E402
from test_slash import _Chain  # noqa: E402

M = "〔不回群〕"
NOTE = "我检查一下是否有新的后台结果或未完成事项。"


class FakeAgent:
    kind = "codex"

    def __init__(self):
        self.cfg = {"session_mode": "independent"}
        self.status, self.halting, self.cur_send, self.cur_cmd = "idle", False, None, None
        self.q = asyncio.Queue()
        self.got = []

    def submit(self, x):
        self.got.append(x)

    def is_down(self):
        return False


class HostLevel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.host._post = lambda *a, **k: None
        self.pushes = []
        self.host.push_notify = self.pushes.append
        self.agent = FakeAgent()
        self.host.agent = self.agent
        self.host.telegram, self.host.mail = Mock(), Mock()
        for name, val in (("_night", False),):
            p = patch.object(hb, name, return_value=val)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(type(self.st), "devices", return_value={"d1": {"name": "手机"}})
        p.start()
        self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(pathlib.Path(self.tmp.name) / "cfg")})
        p.start()
        self.addCleanup(p.stop)

    def beat(self, *texts):
        h = self.host.heartbeat
        r = asyncio.run(h.tick(h.next_due() + 1))
        self.assertTrue(r and r["ok"], r)
        send = self.agent.got[-1]
        self.agent.cur_send = None
        self.host.agent_turn_start(send.text, send)
        for t in texts:
            self.host.agent_text(t)
        self.host.agent_turn_end()
        send.state = "delivered"
        return self.host.hist.get(send.turn), send

    def test_codex_note_then_marker_is_silent(self):
        page, _ = self.beat(NOTE, "  " + M + "\n")
        self.assertEqual(page["reply"]["text"], M, "the page is the marker alone: hidden, no unread on the phone")
        self.assertEqual(page["reply"]["pre"], NOTE, "the note folds into the silent turn")
        self.assertEqual(self.pushes, [], "no push")
        self.assertEqual(page["end"], "done")
        self.assertIn('"ev": "silent_folded"', self.st.log_path.read_text())
        self.assertNotIn(NOTE, self.st.log_path.read_text(), "the note's words never go into host.log")
        from agentj import readmark
        self.assertFalse(readmark.ReadMark.visible(page) if hasattr(readmark, "ReadMark") else True, "no unread")
        tg = self.host.telegram.completed.call_args[0][0]
        self.assertEqual(tg["reply"]["text"], M, "Telegram / mail see a silent page (they drop it)")

    def test_several_notes_then_marker(self):
        page, _ = self.beat("先看一下。", "再看一下。", M)
        self.assertEqual(page["reply"]["text"], M)
        self.assertEqual(page["reply"]["pre"], "先看一下。\n\n再看一下。")
        self.assertEqual(self.pushes, [])

    def test_note_then_real_reply_is_delivered(self):
        page, _ = self.beat(NOTE, "备份任务昨晚失败了，已派给守望重跑。")
        self.assertEqual(page["reply"]["text"], NOTE + "\n\n备份任务昨晚失败了，已派给守望重跑。")
        self.assertNotIn("pre", page["reply"])
        self.assertEqual(self.pushes, ["reply"])

    def test_mixed_final_reply_is_delivered(self):
        page, _ = self.beat(NOTE, "answer " + M)
        self.assertIn("answer " + M, page["reply"]["text"])
        self.assertEqual(self.pushes, ["reply"], "text alongside the marker is delivered (rule unchanged)")

    def test_text_after_the_marker_brings_the_notes_back(self):
        page, _ = self.beat(NOTE, M, "其实有一件事：磁盘快满了。")
        self.assertEqual(page["reply"]["text"], NOTE + "\n\n" + M + "\n\n其实有一件事：磁盘快满了。")
        self.assertNotIn("pre", page["reply"])
        self.assertEqual(self.pushes, ["reply"])

    def test_marker_alone_unchanged(self):
        page, _ = self.beat(M)
        self.assertEqual(page["reply"], {"text": M})
        self.assertEqual(self.pushes, [])

    def test_wait_line_is_not_folded(self):
        h = self.host.heartbeat
        asyncio.run(h.tick(h.next_due() + 1))
        send = self.agent.got[-1]
        self.agent.cur_send = None
        self.host.agent_turn_start(send.text, send)
        self.host.turn_wait_note("正在等待 Bash：ls（已 3 分钟）")
        self.assertTrue(self.host.hist.get(send.turn)["reply"]["text"].startswith("正在等待"))
        self.host.agent_text(M)
        self.host.agent_turn_end()
        page = self.host.hist.get(send.turn)
        self.assertEqual(page["reply"]["text"], M)
        self.assertNotIn("pre", page["reply"], "a host wait line is not the Agent's note")
        self.assertEqual(self.pushes, [])

    def test_phone_turn_note_then_marker(self):
        """Same rule on a phone page (a shared Claude session streams its text before a tool, then Stop's final reply)."""
        page = self.host.hist_add({"k": "phone", "text": "看看后台"}, "", "open")
        self.host.cur_turn = page["id"]
        self.host.agent_text("我看一下。")
        self.host.agent_text(M)
        self.host.agent_turn_end()
        page = self.host.hist.get(page["id"])
        self.assertEqual((page["reply"]["text"], page["reply"]["pre"]), (M, "我看一下。"))
        self.assertEqual(self.pushes, [])

    def test_pre_survives_a_restart(self):
        page, _ = self.beat(NOTE, M)
        again = history.History(self.st)
        t = again.get(page["id"])
        self.assertEqual((t["reply"]["text"], t["reply"].get("pre")), (M, NOTE))

    def test_codex_beat_asks_for_no_progress_note(self):
        _, send = self.beat(M)
        self.assertIn(hb.NO_NOTES["zh"], send.text)
        self.agent.kind = "claude"
        _, send = self.beat(M)
        self.assertNotIn(hb.NO_NOTES["zh"], send.text, "only Codex gets the extra line")
        self.assertIn("不要先写过程说明", hb.NO_NOTES["zh"])
        self.assertTrue(hb.NO_NOTES["en"].endswith(M) and hb.NO_NOTES["zh"].endswith(M))


@unittest.skipUnless(sys.platform.startswith(("linux", "darwin")), "stand-ins are shell scripts")
class _Real(_Chain):
    """The real adapter + its stand-in: a note then 〔不回群〕 → silent; a note then a real reply → delivered."""

    def test_note_then_marker_end_to_end(self):
        async def script(c):
            host = c["host"]
            pushes = []
            host.push_notify = pushes.append
            await c["say"]("SAYSEQ: " + NOTE + " ;; " + M)
            await c["wait"](lambda: any(t["reply"]["text"] == M for t in host.hist.turns.values()))
            await c["idle"]()
            [page] = [t for t in host.hist.turns.values() if t["src"].get("k") == "phone"]
            self.assertEqual(page["reply"]["text"], M, page)
            self.assertEqual(page["reply"].get("pre"), NOTE)
            self.assertEqual(pushes, [], "silent: no push")
            await c["say"]("SAYSEQ: " + NOTE + " ;; 备份失败了，已重跑。")
            await c["wait"](lambda: any("备份失败了" in t["reply"]["text"] for t in host.hist.turns.values()))
            await c["idle"]()
            page = [t for t in host.hist.turns.values() if t["src"].get("k") == "phone"][-1]
            self.assertIn(NOTE, page["reply"]["text"])
            self.assertNotIn("pre", page["reply"])
            await c["wait"](lambda: pushes == ["reply"])
        with patch.object(type(self.st), "devices", return_value={"d": {"name": "手机"}}):
            self.run_chain(script)


class ClaudeReal(_Real):
    KIND = "claude"


class CodexReal(_Real):
    KIND = "codex"


class OpenCodeReal(_Real):
    KIND = "opencode"


del _Real

if __name__ == "__main__":
    unittest.main()
