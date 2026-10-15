"""P133: a finished /compact or /clear ends its page on 「✓ …」 — no desktop echo page left 「已送达，等待电脑接手…」."""
import _hermetic  # noqa: F401,I001
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentj import humane, shared, slash
from agentj.history import History
from agentj.serve import Host
from agentj.state import State


class DoneLine(unittest.TestCase):
    def test_one_language_and_once(self):
        done_zh = humane.say("compact.done", "zh")       # P280: the words live in humane.json
        self.assertEqual(slash.with_done("compact", "", "zh"), done_zh)
        self.assertIn(slash.with_done("clear", "", "en"), [x.replace("{title}", "Chairman") for x in humane.variants("clear.greet", "en")])
        self.assertEqual(slash.with_done("compact", "已压缩：21k → 1k tokens\n交接失败", "zh"),
                         "已压缩：21k → 1k tokens\n" + done_zh + "\n交接失败")
        done = slash.with_done("compact", "x", "zh")
        self.assertEqual(slash.with_done("compact", done, "zh"), done, "never twice")
        self.assertEqual(slash.with_done("status", "ok", "zh"), "ok", "other commands untouched")
        self.assertNotIn("Compacted", slash.with_done("compact", "", "zh"))


class Host133:
    """Just enough of Host for desktop_cmd_done / turn_queued_note (hist, hist_update, desktop_end)."""
    desktop_cmd_done = Host.desktop_cmd_done
    turn_queued_note = Host.turn_queued_note
    desktop_end = Host.desktop_end
    desktop_input = Host.desktop_input

    def __init__(self, root, lang="zh"):
        self.lang, self.desktop_turn = lang, None
        self.hist = History(State(Path(root)), on=True)

    def hist_add(self, src, reply="", end="open", card=None):
        return self.hist.add(src, reply, end, card)

    def hist_update(self, tid, **kw):
        return self.hist.update(tid, **kw)

    def media_later(self, tid):
        pass


class DesktopCompact(unittest.TestCase):
    def test_desktop_typed_compact_page_ends_on_the_native_end(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host133(root)
            h.desktop_input("/compact")
            tid = h.desktop_turn
            h.desktop_cmd_done("compact")
            page = h.hist.get(tid)
            self.assertEqual(page["end"], "done")
            self.assertEqual(page["reply"]["text"], humane.say("compact.done", "zh"))
            self.assertIsNone(h.desktop_turn)

    def test_other_desktop_turns_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            h = Host133(root, "en")
            h.desktop_input("fix the build")
            tid = h.desktop_turn
            h.desktop_cmd_done("compact")
            self.assertEqual(h.hist.get(tid)["end"], "open", "a compaction inside a normal turn does not end it")
            self.assertEqual(h.desktop_turn, tid)


class ControlEcho(unittest.TestCase):
    def test_own_native_slash_is_not_a_desktop_turn(self):
        a = shared.SharedClaudeAgent.__new__(shared.SharedClaudeAgent)
        a._control_sent = ("/compact", time.monotonic())
        self.assertFalse(a._control_echo("/clear"))
        self.assertTrue(a._control_echo(" /compact\n"))
        self.assertFalse(a._control_echo("/compact"), "only once: a later /compact typed at the computer is a turn")
        a._control_sent = ("/compact", time.monotonic() - shared.CONTROL_WAIT - 1)
        self.assertFalse(a._control_echo("/compact"), "stale")

    def test_post_compact_closes_a_desktop_compact_but_not_the_phone_control(self):
        calls = []
        a = shared.SharedClaudeAgent.__new__(shared.SharedClaudeAgent)
        a.host = SimpleNamespace(desktop_cmd_done=calls.append)
        a.control_name = None
        src = Path(shared.__file__).read_text()
        self.assertIn("desktop_cmd_done(\"compact\")", src)
        self.assertIn('self.control_name != "compact"', src)
        self.assertIn("if self._control_echo(text):", src)



class BusyIsNotHeld(unittest.TestCase):
    def agent(self, status="working", compacting=False):
        import asyncio
        a = shared.SharedClaudeAgent.__new__(shared.SharedClaudeAgent)
        a.state = SimpleNamespace(status=status, snapshot=lambda: {"compacting": compacting})
        a.status, a.cfg, a.input_seen = "working", {"dir": "/x"}, asyncio.Event()
        a.done, a.q, a.halting, a.session, a.native_turn, a.phone_claim = asyncio.Event(), asyncio.Queue(), False, None, False, False
        a.notes = []
        a.host = SimpleNamespace(turn_queued_note=a.notes.append)
        return a

    def run_wait(self, a, reason, taken_after=None):
        import asyncio
        inbound = SimpleNamespace(hold_reason=lambda d: reason)

        async def go():
            if taken_after is not None:
                asyncio.get_running_loop().call_later(taken_after, a.input_seen.set)
            return await a._wait_busy_input(inbound)
        return asyncio.run(go())

    def test_busy_session_queues_and_says_so(self):
        a = self.agent("working")
        self.assertTrue(self.run_wait(a, None, taken_after=.05))
        self.assertEqual(a.notes, [True, False], "「电脑正忙，消息已排队」 shown, then removed when taken")
        a = self.agent("idle", compacting=True)
        self.assertTrue(self.run_wait(a, None, taken_after=.05), "compacting counts as busy")

    def test_a_real_hold_still_names_the_reason(self):
        a = self.agent("working")
        self.assertFalse(self.run_wait(a, "default"))
        self.assertEqual(a.notes, [], "a settings hold keeps the 「尚未接收」 notice")
        a = self.agent("idle")
        self.assertFalse(self.run_wait(a, None))
        self.assertEqual(a.notes, [], "idle and not taken is not 「正忙」")

    def test_page_note_one_language_and_removed(self):
        with tempfile.TemporaryDirectory() as root:
            for lang, want in (("zh", humane.say("shared.busy_queued", "zh")), ("en", humane.say("shared.busy_queued", "en"))):
                h = Host133(root, lang)
                h.cur_turn = h.hist_add({"k": "phone", "text": "hi"})["id"]
                h.turn_queued_note(True)
                self.assertEqual(h.hist.get(h.cur_turn)["reply"]["text"], want)
                self.assertNotIn("尚未接收", want)
                h.turn_queued_note(False)
                self.assertEqual(h.hist.get(h.cur_turn)["reply"]["text"], "")


class ReconnectReasons(unittest.TestCase):
    def test_p133_reasons_are_logged(self):
        from agentj.serve import _reconnect_reason
        for why in ("visibility", "offline", "stall"):
            self.assertEqual(_reconnect_reason({"why": why, "code": 1011, "down": 5})[0], why)
        self.assertEqual(_reconnect_reason({"why": "nonsense"}), ())


if __name__ == "__main__":
    unittest.main()
