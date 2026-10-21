"""P135: a phone message injected while the native Claude Code session is already in a turn (a background-task
notification, a desktop turn) gets no UserPromptSubmit. Its page stays open until the next committed Stop, which answers
it — never a synthesized turn end with 「尚未接收」. Several phone messages in that turn: the reply lands on the last page,
earlier pages say 「已合并到下一条回复」."""
import _hermetic  # noqa: F401,I001
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentj import claude_inbound, compactprep, humane, shared
from agentj.history import History
from agentj.serve import Host
from agentj.state import State


class Host135:
    """The page side: history pages, the busy note, the merge, the reply append."""
    turn_queued_note = Host.turn_queued_note
    turn_merged = Host.turn_merged

    def __init__(self, root, lang="zh"):
        self.lang, self.cur_turn, self.cur_failed, self.notices, self.failed = lang, None, False, [], False
        self.st = State(Path(root))
        self.hist = History(self.st, on=True)

    def hist_update(self, tid, **kw):
        return self.hist.update(tid, **kw)

    def agent_turn_start(self, text, send=None):
        self.cur_turn = send.turn

    def agent_turn_end(self):
        if self.cur_turn is not None:
            self.hist.update(self.cur_turn, end="failed" if self.failed else "done")
        self.cur_turn = None

    def agent_text(self, text):
        self.hist.update(self.cur_turn, append=text)

    def agent_notice(self, text):
        self.notices.append(text)

    def turn_failed(self):
        self.failed = True

    def agent_status(self, s):
        pass

    def desktop_text(self, text):
        self.notices.append("desktop:" + text)

    def desktop_end(self):
        pass


def send(h, text):
    page = h.hist.add({"k": "phone", "text": text}, "", "open")["id"]
    s = SimpleNamespace(sid=page, text=text, source=None, turn=page, state="pending", lock=asyncio.Lock(), ready=None)

    async def wait_ready():
        return s.state != "cancelled"
    s.wait_ready = wait_ready
    return s


class StopClaim(unittest.IsolatedAsyncioTestCase):
    def agent(self, h, status="working"):
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": "/nonexistent", "_workflow_ceo": True, "language": "zh"})
        a.session = {"pid": 1, "sessionId": "aaaaaaaa-1111-2222-3333-444444444444", "messagingSocketPath": "/nonexistent"}
        a.state = SimpleNamespace(status=status, snapshot=lambda: {})
        return a

    async def run_turns(self, a, h, msgs, stop_after=.4):
        first, *rest = [send(h, m) for m in msgs]
        for s in rest:
            a.q.put_nowait(s)
        a.cur_send = first
        h.agent_turn_start(first.text, first)
        with patch.object(shared, "claude_send") as wrote, patch.object(shared, "CLAUDE_INPUT_WAIT", .05), \
                patch.object(claude_inbound, "hold_reason", return_value=None), \
                patch.object(compactprep, "decorate", side_effect=lambda ag, s: s.text):
            task = asyncio.create_task(a.turn(first.text))
            await asyncio.sleep(stop_after)          # stands for the 30 s the native turn keeps working
            self.assertFalse(task.done(), "the page waits for the native Stop, no synthesized turn end")
            a._finish_stop("两件事都办好了")            # the next committed Stop, no UserPromptSubmit ever came
            await asyncio.wait_for(task, 2)
        h.agent_turn_end()
        return [first, *rest], wrote

    async def test_two_messages_in_a_running_native_turn(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host135(root)
            a = self.agent(h)
            (s1, s2), wrote = await self.run_turns(a, h, ["第一条", "第二条"])
            self.assertEqual(wrote.call_count, 2, "both messages reach the native session")
            p1, p2 = h.hist.get(s1.turn), h.hist.get(s2.turn)
            self.assertEqual(p1["reply"]["text"], humane.say("shared.merged", "zh"))
            self.assertEqual(p1["end"], "done")
            self.assertEqual(p2["reply"]["text"], "两件事都办好了", "the Stop reply on the last page, no busy note left")
            self.assertEqual(p2["end"], "done")
            self.assertEqual(h.notices, [], "no 「尚未接收」, nothing to the desktop page")
            self.assertTrue(a.q.empty())
            self.assertFalse(a.phone_claim)

    async def test_background_task_turn_without_working_status(self):
        """Leo 11:07: the native turn came from a background-task notification — no UserPromptSubmit, status idle; the
        transcript showed it (native_turn)."""
        with tempfile.TemporaryDirectory() as root:
            h = Host135(root)
            a = self.agent(h, status="idle")
            a.native_turn = True
            (s1,), _ = await self.run_turns(a, h, ["你看得到吗"])
            self.assertEqual(h.hist.get(s1.turn)["reply"]["text"], "两件事都办好了")
            self.assertEqual(h.notices, [])
            self.assertFalse(a.native_turn, "the Stop ends the native turn")

    async def test_idle_session_not_taken_still_names_the_reason(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host135(root)
            a = self.agent(h, status="idle")
            s1 = send(h, "hi")
            a.cur_send = s1
            h.agent_turn_start(s1.text, s1)
            with patch.object(shared, "claude_send"), patch.object(shared, "CLAUDE_INPUT_WAIT", .05), \
                    patch.object(claude_inbound, "hold_reason", return_value=None):
                await asyncio.wait_for(a.turn("hi"), 2)
            # P424 #75: the reason sits on the message's own page (no page of its own), not a failed turn
            self.assertFalse(h.failed)
            self.assertEqual(h.notices, [])
            self.assertIn("尚未接收", h.hist.get(s1.turn)["reply"]["text"])
            self.assertEqual(h.hist.get(s1.turn)["card"]["held"], "pending")

    def test_merged_note_one_language(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host135(root, "en")
            h.cur_turn = h.hist.add({"k": "phone", "text": "a"}, "", "open")["id"]
            tid = h.cur_turn
            h.turn_merged()
            self.assertEqual(h.hist.get(tid)["reply"]["text"], "I'll answer this together with the next reply.")
            self.assertIsNone(h.cur_turn)


if __name__ == "__main__":
    unittest.main()
