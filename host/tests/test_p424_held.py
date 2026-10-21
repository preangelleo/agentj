"""P424 #75 (owner 10-17: 「既没有 input，怎么会产生 output？……我怎么会发了一个空消息呢？」): the shared Claude Code did not take a
phone message in time. The 「尚未接收」 reason goes on that message's own page (its words right above it), never a system page
of its own that looks like an empty message; the settings diagnosis (layer, crossSessionInbound value) is card.detail for the
phone's 「详情」, never in the text; when the message reaches Claude Code after all (prompt hook, transcript record, queued
attachment) the notice gives way to 「已送达」 and the 「详情」 goes."""
import _hermetic  # noqa: F401,I001
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentj import claude_inbound, held, shared
from agentj.history import History
from agentj.state import State


class Host424:
    """The page side Host gives held.py: history pages, the legacy notice, the turn end."""

    def __init__(self, root, lang="zh"):
        self.lang, self.cur_turn, self.cur_failed, self.failed = lang, None, False, False
        self.st = State(Path(root))
        self.hist = History(self.st, on=True)
        self.notices, self.legacy, self.events, self.logs = [], [], [], []
        self.st.log = lambda ev, **kw: self.logs.append((ev, kw))

    def hist_update(self, tid, **kw):
        return self.hist.update(tid, **kw)

    def emit(self, name, **kw):
        self.events.append((name, kw))

    def _remember(self, kind, text):
        self.legacy.append((kind, text))
        return {"kind": kind, "text": text}

    def _post(self, *a):
        pass

    def _send_legacy(self, *a):
        pass

    def _render(self, *a):
        pass

    def agent_turn_start(self, text, send=None):
        self.cur_turn = send.turn

    def agent_turn_end(self):
        if self.cur_turn is not None:
            self.hist.update(self.cur_turn, end="failed" if self.failed else "done")
        self.cur_turn = None

    def agent_notice(self, text):         # a page of its own (`sys`) — what #75 must not produce here
        self.notices.append(text)

    def turn_failed(self):
        self.failed = True

    def agent_status(self, s):
        pass


def send(h, text):
    page = h.hist.add({"k": "phone", "text": text}, "", "open")["id"]
    s = SimpleNamespace(sid=page, text=text, source=None, turn=page, state="pending", lock=asyncio.Lock(), ready=None)

    async def wait_ready():
        return s.state != "cancelled"
    s.wait_ready = wait_ready
    return s


class Held(unittest.IsolatedAsyncioTestCase):
    def agent(self, h):
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": "/nonexistent", "_workflow_ceo": True, "language": h.lang})
        a.session = {"pid": 1, "sessionId": "aaaaaaaa-1111-2222-3333-444444444444", "messagingSocketPath": "/nonexistent"}
        a.state = SimpleNamespace(status="idle", snapshot=lambda: {}, handle=lambda e: None)
        return a

    async def not_taken(self, h, a, text, reason="default"):
        s = send(h, text)
        a.cur_send = s
        h.agent_turn_start(s.text, s)
        with patch.object(shared, "claude_send"), patch.object(shared, "CLAUDE_INPUT_WAIT", .05), \
                patch.object(claude_inbound, "hold_reason", return_value=reason), \
                patch.object(claude_inbound, "effective", return_value=("user", None, "hold")):
            await asyncio.wait_for(a.turn(text), 2)
        h.agent_turn_end()
        return s.turn

    async def test_notice_on_the_owners_page_details_folded(self):
        for lang in ("zh", "en"):
            with tempfile.TemporaryDirectory() as root:
                h = Host424(root, lang)
                a = self.agent(h)
                tid = await self.not_taken(h, a, "帮我调研一下 PPT 模板")
                page = h.hist.get(tid)
                self.assertEqual(h.notices, [], "no page of its own (the 「空消息」 look)")
                self.assertEqual([t["id"] for t in h.hist.turns.values()], [tid], "one page: the owner's message")
                self.assertEqual(page["src"]["text"], "帮我调研一下 PPT 模板", "the owner's words stay on the page")
                reply = page["reply"]["text"]
                self.assertIn("尚未接收" if lang == "zh" else "has not taken this message", reply)
                self.assertNotIn("crossSessionInbound", reply, "no internal field in the words")
                self.assertEqual(page["card"]["held"], "pending")
                self.assertIn("crossSessionInbound", page["card"]["detail"], "the diagnosis is behind 「详情」")
                self.assertEqual(page["end"], "done", "not a failed turn: no fallback model, no 「再发一次」")
                self.assertFalse(h.failed)
                self.assertEqual(h.legacy, [("notice", reply)], "an older phone still hears the notice")
                self.assertEqual(a.held, [(tid, a.pending_text)])

    async def test_arrives_later_by_prompt_hook(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host424(root)
            a = self.agent(h)
            tid = await self.not_taken(h, a, "第一条：调研 PPT")
            body = a.held[0][1]
            await a.hook_event({"session_id": a.session["sessionId"], "hook_event_name": "UserPromptSubmit",
                                "prompt": "<cross-session from-name=\"Agent J\">" + body + "</cross-session>"})
            page = h.hist.get(tid)
            self.assertEqual(page["card"], {"held": "delivered"}, "「详情」 gone with the notice")
            self.assertIn("后来接收了这条消息", page["reply"]["text"])
            self.assertNotIn("尚未接收", page["reply"]["text"])
            self.assertEqual(a.held, [])
            self.assertIn(("shared_held_delivered", {"turn": tid}), h.logs)

    async def test_arrives_later_in_the_transcript(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host424(root, "en")
            a = self.agent(h)
            tid = await self.not_taken(h, a, "second: the slides")
            body = a.held[0][1]
            a._held_seen("queued: " + body)        # a queued_command attachment / an isMeta or user record carries it
            page = h.hist.get(tid)
            self.assertEqual(page["card"]["held"], "delivered")
            self.assertIn("took this message after all", page["reply"]["text"])
            a._held_seen(body)                     # twice → nothing more
            self.assertEqual(h.hist.get(tid)["reply"]["text"], page["reply"]["text"])

    async def test_unrelated_text_and_running_turn_leave_it(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host424(root)
            a = self.agent(h)
            tid = await self.not_taken(h, a, "第三条")
            body = a.held[0][1]
            a._held_seen("something else entirely")
            a.phone_turn, a.pending_text = True, body     # the same words sent again and running now: that page's, not this
            a._held_seen(body)
            self.assertEqual(h.hist.get(tid)["card"]["held"], "pending")
            a.phone_turn = False
            a._held_seen(body)
            self.assertEqual(h.hist.get(tid)["card"]["held"], "delivered")

    async def test_no_open_page_falls_back_to_the_notice(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host424(root)
            self.assertIsNone(held.note(h, "x", "y"), "no running page")
            tid = h.hist.add({"k": "phone", "text": "a"}, "", "done")["id"]
            h.cur_turn = tid
            self.assertIsNone(held.note(h, "x", "y"), "an ended page is never rewritten")
            held.delivered(h, tid)                         # not held → untouched
            self.assertEqual(h.hist.get(tid)["reply"]["text"], "")


class Contract(unittest.TestCase):
    def test_the_real_host_has_what_held_uses(self):
        from agentj.serve import Host
        for name in ("hist_update", "emit", "_remember", "_post", "_send_legacy", "_render"):
            self.assertTrue(callable(getattr(Host, name, None)), name)


if __name__ == "__main__":
    unittest.main()
