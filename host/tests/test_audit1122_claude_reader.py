"""Audit 2026-11-22 N-H5: the Claude read loop.

The H7 fix (never leave Claude Code blocked on a dead reader) put every per-event handler under one `kill the session`
branch, so one unexpected event shape — a bug in OUR handler — ended the owner's main session mid-turn.

Now: an exception while HANDLING one parsed event is logged (rate-limited) and that event is skipped; the loop keeps
draining stdout. Skipping is made safe: the turn takes the external-read mark (the skipped event may have been a tool
call the mark would have seen — more cards, never fewer), and the one event that cannot be skipped — the main `result`
that ends the turn — stops the turn safely instead of leaving it open. A failure of the READER itself (stream error,
oversize line) still stops safely: process tree killed, turn reported as failed, never an undrained pipe (H7 stays fixed)."""
import _hermetic  # noqa: F401
import asyncio
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import agent, bg, trust_taint


class _Proc:
    pid = 2 ** 22 + 12345          # never signalled: agent._signal_tree is replaced in every test

    def __init__(self, lines, boom=None):
        self.stdout = asyncio.StreamReader()
        for ln in lines:
            self.stdout.feed_data((json.dumps(ln) if isinstance(ln, dict) else ln).encode() + b"\n")
        if boom is None:
            self.stdout.feed_eof()
        else:
            self.stdout.set_exception(boom)
        self.returncode = None
        self.signals = []
        self.gone = asyncio.Event()

    async def wait(self):
        if self.returncode is None:
            if not self.signals:
                self.returncode = 0          # ended by itself after its last line
            else:
                await self.gone.wait()
        return self.returncode


def make(proc):
    a = object.__new__(agent.ClaudeAgent)
    logged, notices, failed = [], [], []
    a.host = types.SimpleNamespace(st=types.SimpleNamespace(log=lambda ev, **kw: logged.append((ev, kw)),
                                                            set_agent_session=lambda *x: None),
                                   agent_notice=notices.append, turn_failed=lambda: failed.append(1), busy=None)
    a.kind, a.cfg = "claude", {"dir": "/nonexistent", "language": "zh"}
    a.ctl, a.ended, a.err_tail, a.raw_tail = {}, set(), "", ""
    a.proc = a.turn_proc = proc
    a.turn_done, a.halting, a.got_init, a.capture = asyncio.Event(), False, True, None
    a._restart_had_tools, a._restart_result = False, None
    a.logged, a.notices, a.failed, a.handled = logged, notices, failed, []
    return a


def signal_tree(p, sig):
    p.signals.append(sig)
    p.returncode = -int(sig)
    p.gone.set()


class ClaudeReader(unittest.TestCase):
    def setUp(self):
        bg._last.clear()
        p = mock.patch.object(agent, "_signal_tree", signal_tree)
        p.start()
        self.addCleanup(p.stop)

    def test_a_handler_exception_skips_that_event_and_the_session_lives(self):
        async def scenario():
            proc = _Proc([{"type": "assistant", "n": 1}, {"type": "assistant", "n": 2, "odd": True},
                          {"type": "assistant", "n": 3}, {"type": "result", "subtype": "success"}])
            a = make(proc)

            def on_event(ev):
                if ev.get("odd"):
                    raise KeyError("an event shape nobody expected")
                a.handled.append(ev.get("n") or ev["type"])
                if ev["type"] == "result":
                    a.turn_done.set()
            a.on_event = on_event
            await asyncio.wait_for(a._read(proc, resumed=False), 5)
            self.assertEqual(proc.signals, [], "one bad event must not kill the owner's main session")
            self.assertEqual(a.handled, [1, 3, "result"], "the loop kept draining stdout")
            self.assertTrue(a.turn_done.is_set())
            self.assertEqual(a.notices, [])
            self.assertEqual([(ev, kw.get("exception_type")) for ev, kw in a.logged if ev == "agent_event_failed"],
                             [("agent_event_failed", "KeyError")])
            # skipped ≠ unseen: the skipped event may have been an external read — the turn takes the mark (more cards)
            self.assertEqual(trust_taint.tainted(a), {"state": "external_read", "kind": "external"})

        asyncio.run(scenario())

    def test_repeated_handler_exceptions_are_rate_limited_in_the_log(self):
        async def scenario():
            proc = _Proc([{"type": "assistant", "odd": True}] * 50 + [{"type": "result"}])
            a = make(proc)

            def on_event(ev):
                if ev.get("odd"):
                    raise KeyError("x")
                a.turn_done.set()
            a.on_event = on_event
            await asyncio.wait_for(a._read(proc, resumed=False), 5)
            self.assertEqual(len([1 for ev, _ in a.logged if ev == "agent_event_failed"]), 1)
            self.assertEqual(proc.signals, [])

        asyncio.run(scenario())

    def test_a_main_result_that_cannot_be_handled_stops_the_turn_safely(self):
        # the one event that cannot be skipped: without it the turn would stay open for ever
        async def scenario():
            proc = _Proc([{"type": "assistant", "n": 1}, {"type": "result", "subtype": "success"}, {"type": "assistant", "n": 9}])
            a = make(proc)

            def on_event(ev):
                if ev["type"] == "result":
                    raise TypeError("x")
                a.handled.append(ev.get("n"))
            a.on_event = on_event
            await asyncio.wait_for(a._read(proc, resumed=False), 5)
            self.assertTrue(proc.signals, "the process tree is ended")
            self.assertTrue(a.turn_done.is_set(), "the turn is over, not hanging")
            self.assertEqual((len(a.notices), a.failed), (1, [1]), "and reported as failed, once")
            self.assertIsNone(a.proc)

        asyncio.run(scenario())

    def test_a_child_result_that_cannot_be_handled_is_just_skipped(self):
        async def scenario():
            proc = _Proc([{"type": "result", "parent_tool_use_id": "tu1"}, {"type": "result", "subtype": "success"}])
            a = make(proc)

            def on_event(ev):
                if ev.get("parent_tool_use_id"):
                    raise TypeError("x")
                a.turn_done.set()
            a.on_event = on_event
            await asyncio.wait_for(a._read(proc, resumed=False), 5)
            self.assertEqual((proc.signals, a.notices), ([], []))

        asyncio.run(scenario())

    def test_a_reader_failure_kills_the_tree_and_reports_the_turn_failed(self):
        async def scenario():
            proc = _Proc([{"type": "assistant", "n": 1}], boom=ValueError("Separator is not found, and chunk exceed the limit"))
            a = make(proc)
            a.on_event = lambda ev: a.handled.append(ev.get("n"))
            await asyncio.wait_for(a._read(proc, resumed=False), 5)
            self.assertTrue(proc.signals, "H7: the producer is never left writing into a pipe nobody reads")
            self.assertIsNone(a.proc)
            self.assertTrue(a.turn_done.is_set(), "the turn is over, not hanging")
            self.assertEqual((len(a.notices), a.failed), (1, [1]), "and it is reported as failed")
            self.assertIn("agent_read_failed", [ev for ev, _ in a.logged])

        asyncio.run(scenario())

    def test_a_reader_failure_during_an_owner_stop_stays_quiet(self):
        async def scenario():
            proc = _Proc([], boom=ConnectionResetError("x"))
            a = make(proc)
            a.halting = True
            a.on_event = lambda ev: None
            await asyncio.wait_for(a._read(proc, resumed=False), 5)
            self.assertTrue(a.turn_done.is_set())
            self.assertEqual(a.notices, [])

        asyncio.run(scenario())


    def test_if_the_mark_cannot_be_set_the_turn_stops_instead_of_going_on_unmarked(self):
        async def scenario():
            proc = _Proc([{"type": "assistant", "odd": True}, {"type": "assistant", "n": 2}, {"type": "result"}])
            a = make(proc)

            def on_event(ev):
                if ev.get("odd"):
                    raise KeyError("x")
                a.handled.append(ev.get("n"))
            a.on_event = on_event
            with mock.patch.object(trust_taint, "mark", side_effect=RuntimeError("cannot hold the mark")):
                await asyncio.wait_for(a._read(proc, resumed=False), 5)
            self.assertEqual(a.handled, [], "nothing after the unmarked skip is handled")
            self.assertTrue(proc.signals and a.turn_done.is_set())
            self.assertEqual((len(a.notices), a.failed), (1, [1]))
            self.assertIn("trust_taint_unheld", [ev for ev, _ in a.logged])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
