"""#0303 (chairman 2026-11-09 15:3x): after a compaction the phone said 「压缩好了，我在翻交接笔记，马上接上思路。」 while the
page was green — the host had only written the recovery to Claude Code's socket; nothing proved a turn had started, and an
idle owned Agent got its handover pointer only with the NEXT message.

- Shared Claude: compaction done → no inbound → the host's 〔Agent J 系统提示〕 handoff-read turn starts by itself (within 60 s;
  short timers here); the 「在翻交接笔记」 line only after the native turn started (UserPromptSubmit); a write that starts no
  turn or fails is re-sent (bounded, logged), then a truthful 「没能自动送到」 line; a manual /compact waits past the old 5 s
  busy window.
- Owned conversation (Claude stand-in): /compact then silence → the handover-read host turn runs without a new message.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import os
import pathlib
import sys
import time
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import compactprep, handoff, humane, provenance, shared  # noqa: E402
import test_p151_handoff as p151  # noqa: E402
from test_p57_agent import _Prep  # noqa: E402

READING = humane.say("recover.compact", "zh")
STALLED = humane.say("recover.stalled", "zh")


class _Shared(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = p151.Handoff.asyncSetUp
    ev = p151.Handoff.ev
    write_handoff = p151.Handoff.write_handoff

    async def asyncSetUp(self):  # noqa: F811
        await p151.Handoff.asyncSetUp(self)
        for attr, val in (("RECOVER_START_WAIT", 0.4), ("RECOVER_GAP", 0.05), ("RECOVER_BUSY_WAIT", 60.0)):
            p = patch.object(handoff, attr, val)
            p.start()
            self.addCleanup(p.stop)
        self.timeline = []              # (event, agent status) in order
        notice = self.host.agent_notice

        def seen(text):
            self.timeline.append(("notice:" + text, self.a.state.status))
            notice(text)
        self.host.agent_notice = seen

    def take_later(self, delay, prompt):
        """Claude Code takes the message `delay` s after the write (UserPromptSubmit)."""
        async def go():
            await asyncio.sleep(delay)
            self.timeline.append(("before_start", list(self.host.notices)))
            await self.ev("UserPromptSubmit", prompt=prompt)
            self.timeline.append(("started", self.a.state.status))
        self.a.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(go()))

    async def compact_done(self, trigger="manual"):
        await self.ev("UserPromptSubmit", prompt="继续")
        await self.ev("Stop", last_assistant_message="好")
        self.write_handoff()
        await self.ev("PreCompact", trigger=trigger)
        await self.ev("SessionStart", source="compact")
        await self.ev("PostCompact", trigger=trigger)

    async def until(self, pred, limit=60.0):
        t0 = time.monotonic()
        while not pred():
            if time.monotonic() - t0 > limit:
                self.fail("not within %.0f s" % limit)
            await asyncio.sleep(0.05)
        return time.monotonic() - t0

    def results(self):
        return [x["result"] for x in self.host.logged("shared_handoff_recover")]


class SharedRecovery(_Shared):
    async def test_compaction_then_silence_starts_the_handoff_turn_within_60s(self):
        self.takes = False

        def send(s, text, src=None):
            self.sent.append((text, src))
            self.take_later(0.3, text)
        with patch.object(shared, "claude_send", side_effect=send):
            t0 = time.monotonic()
            await self.compact_done()
            await self.until(lambda: READING in self.host.notices)
            took = time.monotonic() - t0
        self.assertLess(took, 60.0)
        self.assertEqual(len(self.sent), 1, "no inbound message was needed and nothing was sent twice")
        text, src = self.sent[0]
        self.assertTrue(text.startswith("〔Agent J 系统提示〕压缩完成。先读交接文件 "), text)
        self.assertEqual(provenance.from_name(src), provenance.SYSTEM)
        before = [x for x in self.timeline if x[0] == "before_start"]
        self.assertEqual(before, [("before_start", [])], "nothing said before the native turn started")
        notice = [x for x in self.timeline if x[0] == "notice:" + READING]
        self.assertEqual(len(notice), 1)
        self.assertNotIn(notice[0][1], ("idle", "none"), "the reading line only while the turn runs (blue)")
        started = [i for i, x in enumerate(self.timeline) if x[0] == "started"]
        self.assertEqual(len(started), 1)
        self.assertLess(started[0], self.timeline.index(notice[0]), "turn start first, then the reading line")
        self.assertEqual(self.results(), ["sent", "started"])

    async def test_a_write_that_starts_no_turn_never_claims_reading_and_is_resent_bounded(self):
        self.takes = False
        await self.compact_done()
        await self.until(lambda: STALLED in self.host.notices, limit=30)
        self.assertNotIn(READING, self.host.notices)
        self.assertEqual(self.host.notices, [STALLED])
        self.assertEqual(len(self.sent), handoff.RECOVER_TRIES)
        self.assertEqual(self.results(), ["sent", "not_started"] * handoff.RECOVER_TRIES + ["gave_up"])
        self.assertEqual([x["attempt"] for x in self.host.logged("shared_handoff_recover") if x["result"] == "sent"],
                         list(range(1, handoff.RECOVER_TRIES + 1)))

    async def test_a_failed_write_is_retried(self):
        calls = []

        def send(s, text, src=None):
            calls.append(text)
            if len(calls) == 1:
                raise ConnectionError("peer socket refused")
            self.sent.append((text, src))
            self.take_later(0.05, text)
        with patch.object(shared, "claude_send", side_effect=send):
            await self.compact_done()
            await self.until(lambda: READING in self.host.notices, limit=30)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.results(), ["send_failed", "sent", "started"])

    async def test_a_late_take_is_not_sent_twice(self):
        self.takes = False

        def send(s, text, src=None):
            self.sent.append((text, src))
            if len(self.sent) == 1:
                self.take_later(handoff.RECOVER_START_WAIT + 0.02, text)    # after the start wait, inside the gap
        with patch.object(handoff, "RECOVER_GAP", 0.5), patch.object(shared, "claude_send", side_effect=send):
            await self.compact_done()
            await self.until(lambda: READING in self.host.notices, limit=30)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.results(), ["sent", "not_started", "started"])

    async def test_a_manual_compaction_waits_past_the_old_5s_busy_window(self):
        polls = []

        def quiet():
            polls.append(1)
            return len(polls) > 24                  # 25 × 0.25 s > the old 20-poll limit
        with patch.object(self.a, "_registry_quiet", side_effect=quiet):
            await self.compact_done()
            await self.until(lambda: READING in self.host.notices, limit=30)
        self.assertGreater(len(polls), 20)
        self.assertEqual(self.results(), ["sent", "started"])

    async def test_automatic_compaction_inside_a_turn_still_left_alone(self):
        self.write_handoff()
        await self.ev("UserPromptSubmit", prompt="长任务")
        self.a.native_turn = True
        await self.ev("PreCompact", trigger="auto")
        await self.ev("SessionStart", source="compact")     # no trigger on this one: PreCompact's counts
        await asyncio.sleep(6.5)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.results(), ["busy"])
        self.assertEqual(self.host.notices, [])


class OwnedClaudeResume(_Prep):
    KIND = "claude"

    def test_compact_then_silence_runs_the_handover_turn(self):
        async def script(c):
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            await c["idle"]()
            t0 = time.monotonic()
            comp = await c["cmd"]("compact")
            self.assertTrue(comp["text"].startswith("已压缩"), comp["text"])
            [ho] = self.handovers()
            note = compactprep.NOTE["zh"].format(path=ho)
            await c["wait"](lambda: self.notes() == [note])         # no new message from anyone
            self.assertLess(time.monotonic() - t0, 60.0)
            await c["idle"]()
            pages = [t for t in self.hist_lines() if t["src"].get("k") == "agent" and t["src"].get("via") == "compact"]
            self.assertEqual(len({t["id"] for t in pages}), 1, "one host page for the handover turn")
            await c["say"]("二")
            await c["wait"](lambda: any(m["text"] == "ECHO: 二" for m in c["msgs"]()))
            await c["idle"]()
            self.assertEqual(self.notes(), [note], "the next message does not repeat it")
        self.run_chain(script)
        log = self.st.log_path.read_text()
        self.assertIn('"ev": "compact_resume", "agent": "claude", "result": "queued"', log)
        self.assertIn('"compact_resume_delivery"', log)
        self.assertNotIn(".agentj/handover", log)


class Words(unittest.TestCase):
    def test_both_languages(self):
        for key in ("recover.compact", "recover.stalled"):
            self.assertTrue(humane.say(key, "zh") and humane.say(key, "en"))
        self.assertNotIn("翻交接笔记", STALLED)
        self.assertNotIn("reading", humane.say("recover.stalled", "en").lower())


if __name__ == "__main__":
    unittest.main()
