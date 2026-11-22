"""Audit 2026-11-22 N-H5, chairman ruling C: the Codex, ACP and OpenCode read loops follow the rule of ClaudeAgent._read.

What they did before (main 065646d3d3):
  Codex / ACP `_read` — one try around the whole loop. ANY exception, from our handler for one message or from the
    stream, was logged and left the loop, then `await p.wait()` — with the app-server alive and nobody reading its
    stdout: the turn (the main one or a dispatched job's) hung on an undrained pipe (the original H7 bug; only the
    Claude adapter had been fixed).
  OpenCode `_events` (v1 and v2 share it) — a handler exception that happened to be OSError / ValueError / timeout was
    taken for a lost stream and swallowed silently (no log, no mark); any other type ended the event task for good and
    the turn never ended.

Now, for each: a failure while handling ONE parsed event is logged (rate-limited), the event is skipped and the turn
takes the external-read mark, the loop keeps draining; what cannot be skipped — a request that needs our answer, the
event that ends the turn, a mark that cannot be set — stops the turn safely (process tree ended, turn reported failed);
a failure of the reader itself does the same. A Codex turn that skipped an event is never reported as `completed`."""
import _hermetic  # noqa: F401
import asyncio
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import acp, agent, agent_codex, agent_opencode, bg, trust_taint
from test_audit1122_claude_reader import _Proc, signal_tree


def base(cls, proc):
    a = object.__new__(cls)
    logged, notices, failed = [], [], []
    a.host = types.SimpleNamespace(st=types.SimpleNamespace(log=lambda ev, **kw: logged.append((ev, kw))),
                                   agent_notice=notices.append, turn_failed=lambda: failed.append(1), busy=None)
    a.kind, a.cfg = "codex", {"dir": "/nonexistent"}
    a.waits, a.pending, a.ended = {}, {}, set()
    a.proc = a.turn_proc = proc
    a.turn_done, a.halting = asyncio.Event(), False
    a._last_err = lambda: ""
    a.logged, a.notices, a.failed, a.handled = logged, notices, failed, []
    return a


def note(method, **params):
    return {"jsonrpc": "2.0", "method": method, "params": params}


class _Readers(unittest.TestCase):
    def setUp(self):
        bg._last.clear()
        for mod in (agent, agent_codex, acp, agent_opencode):
            if hasattr(mod, "_signal_tree"):
                p = mock.patch.object(mod, "_signal_tree", signal_tree)
                p.start()
                self.addCleanup(p.stop)
        p = mock.patch("agentj.codex_procs.forget", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def events(self, a):
        return [ev for ev, _ in a.logged]


class Codex(_Readers):
    def make(self, lines, boom=None, on_note=None, on_request=None):
        proc = _Proc(lines, boom=boom)
        a = base(agent_codex.CodexAgent, proc)
        a.thread = {}
        a.tid, a.turn_id, a.turn_status = "T1", "u1", {}
        a.fail_notice = lambda text, **kw: (a.failed.append(1), a.notices.append(text))    # (the real one renders model names)
        a._busy = lambda method, p: None

        def default_note(method, p):
            a.handled.append(method)
            if method == "turn/completed" and p.get("threadId") == a.tid:
                a.turn_status = p.get("turn") or {}
                a.turn_done.set()
        a._on_note = on_note or default_note
        a._on_request = on_request or (lambda m: a.handled.append("req:" + m["method"]))
        return a, proc

    def test_a_handler_exception_skips_that_event_and_the_loop_keeps_draining(self):
        async def scenario():
            a, proc = self.make([note("item/started", threadId="T1", odd=True), note("item/completed", threadId="T1"),
                                 note("turn/completed", threadId="T1", turn={"id": "u1", "status": "completed"})])
            plain = a._on_note

            def on_note(method, p):
                if p.get("odd"):
                    raise KeyError("an event shape nobody expected")
                plain(method, p)
            a._on_note = on_note
            await asyncio.wait_for(a._read(proc), 5)
            self.assertEqual(proc.signals, [], "one bad event must not end the app-server")
            self.assertEqual(a.handled, ["item/completed", "turn/completed"])
            self.assertTrue(a.turn_done.is_set())
            self.assertEqual(self.events(a).count("agent_event_failed"), 1)
            self.assertEqual(trust_taint.tainted(a), {"state": "external_read", "kind": "external"})
            # main session or dispatched job: a turn that skipped an event is never reported as completed
            self.assertNotEqual(a.turn_status.get("status"), "completed")
            self.assertEqual(a.notices, [])
        asyncio.run(scenario())

    def test_a_turn_with_no_skipped_event_is_still_completed(self):
        async def scenario():
            a, proc = self.make([note("item/completed", threadId="T1"),
                                 note("turn/completed", threadId="T1", turn={"id": "u1", "status": "completed"})])
            await asyncio.wait_for(a._read(proc), 5)
            self.assertEqual((a.turn_status.get("status"), proc.signals, self.events(a).count("agent_event_failed")),
                             ("completed", [], 0))
        asyncio.run(scenario())

    def test_the_event_that_ends_the_turn_cannot_be_skipped(self):
        async def scenario():
            a, proc = self.make([note("turn/completed", threadId="T1", turn={"id": "u1", "status": "completed"}),
                                 note("item/started", threadId="T1")])

            def on_note(method, p):
                if method == "turn/completed":
                    raise TypeError("x")
                a.handled.append(method)
            a._on_note = on_note
            await asyncio.wait_for(a._read(proc), 5)
            self.assertTrue(proc.signals, "the process tree is ended")
            self.assertTrue(a.turn_done.is_set(), "the turn is over, not hanging")
            self.assertEqual((len(a.notices), a.failed), (1, [1]), "reported as failed, once")
            self.assertNotEqual(a.turn_status.get("status"), "completed", "a dispatched job is not reported as succeeded")
            self.assertIsNone(a.proc)
        asyncio.run(scenario())

    def test_a_request_that_needs_our_answer_cannot_be_skipped(self):
        async def scenario():
            a, proc = self.make([{"jsonrpc": "2.0", "id": 7, "method": "item/commandExecution/requestApproval", "params": {"threadId": "T1"}},
                                 note("item/started", threadId="T1")],
                                on_request=lambda m: (_ for _ in ()).throw(KeyError("x")))
            await asyncio.wait_for(a._read(proc), 5)
            self.assertTrue(proc.signals and a.turn_done.is_set())
            self.assertEqual((len(a.notices), a.failed, a.handled), (1, [1], []))
        asyncio.run(scenario())

    def test_a_reader_failure_ends_the_tree_and_reports_the_turn_failed(self):
        async def scenario():
            a, proc = self.make([], boom=ValueError("Separator is not found, and chunk exceed the limit"))
            await asyncio.wait_for(a._read(proc), 5)       # before: hung here for ever (app-server alive, pipe unread)
            self.assertTrue(proc.signals and a.turn_done.is_set())
            self.assertEqual((len(a.notices), a.failed), (1, [1]))
            self.assertIn("agent_read_failed", self.events(a))
            self.assertNotEqual(a.turn_status.get("status"), "completed")
        asyncio.run(scenario())

    def test_a_dispatched_job_runner_gets_the_same_stop(self):
        # busy_native.BusyCodex and SharedCodexAgent are CodexAgent subclasses that do not override _read
        from agentj import shared_codex
        self.assertIs(shared_codex.SharedCodexAgent._read, agent_codex.CodexAgent._read)
        import inspect
        from agentj import busy_native
        self.assertNotIn("def _read", inspect.getsource(busy_native))

    def test_if_the_mark_cannot_be_set_the_turn_stops(self):
        async def scenario():
            a, proc = self.make([note("item/started", threadId="T1", odd=True), note("item/completed", threadId="T1")])

            def on_note(method, p):
                if p.get("odd"):
                    raise KeyError("x")
                a.handled.append(method)
            a._on_note = on_note
            with mock.patch.object(trust_taint, "mark", side_effect=RuntimeError("cannot hold the mark")):
                await asyncio.wait_for(a._read(proc), 5)
            self.assertEqual(a.handled, [])
            self.assertTrue(proc.signals and a.turn_done.is_set())
        asyncio.run(scenario())


class Acp(_Readers):
    def make(self, lines, boom=None):
        proc = _Proc(lines, boom=boom)
        a = base(acp.AcpAgent, proc)
        a.kind, a.sid = "acp", "S1"
        a.prompt_fut = asyncio.get_running_loop().create_future()
        a.waits[1] = a.prompt_fut
        a._on_note = lambda method, p: a.handled.append(method)
        a._on_request = lambda m: a.handled.append("req")
        return a, proc

    def test_a_handler_exception_skips_that_event_and_the_loop_keeps_draining(self):
        async def scenario():
            a, proc = self.make([note("session/update", odd=True), note("session/update"),
                                 {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "end_turn"}}])

            def on_note(method, p):
                if p.get("odd"):
                    raise KeyError("x")
                a.handled.append(method)
            a._on_note = on_note
            await asyncio.wait_for(a._read(proc), 5)
            self.assertEqual((proc.signals, a.handled), ([], ["session/update"]))
            self.assertTrue(a.prompt_fut.done() and a.prompt_fut.result()["result"]["stopReason"] == "end_turn")
            self.assertEqual(self.events(a).count("agent_event_failed"), 1)
            self.assertIsNotNone(trust_taint.tainted(a))
        asyncio.run(scenario())

    def test_a_request_that_needs_our_answer_cannot_be_skipped(self):
        async def scenario():
            a, proc = self.make([{"jsonrpc": "2.0", "id": 9, "method": "session/request_permission", "params": {}},
                                 note("session/update")])
            a._on_request = lambda m: (_ for _ in ()).throw(KeyError("x"))
            await asyncio.wait_for(a._read(proc), 5)
            self.assertTrue(proc.signals)
            self.assertEqual((len(a.notices), a.failed, a.handled), (1, [1], []))
            self.assertIsInstance(a.prompt_fut.exception(), ConnectionError, "the turn ends: its prompt call fails")
        asyncio.run(scenario())

    def test_a_reader_failure_ends_the_tree_and_reports_the_turn_failed(self):
        async def scenario():
            a, proc = self.make([], boom=ValueError("chunk exceed the limit"))
            await asyncio.wait_for(a._read(proc), 5)
            self.assertTrue(proc.signals)
            self.assertEqual((len(a.notices), a.failed), (1, [1]))
            self.assertIsInstance(a.prompt_fut.exception(), ConnectionError)
        asyncio.run(scenario())


class _Client:
    def __init__(self, events):
        self._events = events

    async def events(self, path=None):
        for ev in self._events:
            if isinstance(ev, BaseException):
                raise ev
            yield ev
        await asyncio.sleep(3600)


class OpenCode(_Readers):
    def make(self, events):
        class P:
            returncode, pid = None, 2 ** 22 + 5

            def __init__(self):
                self.signals, self.gone = [], asyncio.Event()

            async def wait(self):
                await self.gone.wait()
                return self.returncode
        proc = P()
        a = base(agent_opencode.OpenCodeAgent, proc)
        a.kind, a.sid = "opencode", "S1"
        a.client = _Client(events)
        a.connected = asyncio.Event()
        a._busy = lambda ev: None
        a.resyncs = []

        async def resync():
            a.resyncs.append(1)
        a._resync = resync
        a._sync_owner = lambda: None
        a._sync_current = lambda owner: True
        a._retire = lambda p: None
        return a, proc

    async def drive(self, a, proc, until):
        task = asyncio.create_task(a._events(proc))
        for _ in range(200):
            if until() or task.done():
                break
            await asyncio.sleep(0.01)
        done = task.done()
        err = task.exception() if done and not task.cancelled() else None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return done, err

    def test_a_handler_exception_skips_that_event_and_the_stream_goes_on(self):
        for exc in (KeyError("unexpected shape"), ValueError("looks like a stream error but is ours")):
            with self.subTest(exc=type(exc).__name__):
                async def scenario():
                    bg._last.clear()
                    a, proc = self.make([{"type": "a", "odd": True}, {"type": "b"}, {"type": "c"}])

                    def on_event(ev):
                        if ev.get("odd"):
                            raise exc
                        a.handled.append(ev["type"])
                    a.on_event = on_event
                    done, err = await self.drive(a, proc, lambda: a.handled == ["b", "c"])
                    self.assertIsNone(err, "the event task must not die of a handler bug (the turn would never end)")
                    self.assertEqual(a.handled, ["b", "c"])
                    self.assertEqual(self.events(a).count("agent_event_failed"), 1, "logged, not swallowed silently")
                    self.assertIsNotNone(trust_taint.tainted(a))
                    self.assertEqual(a.resyncs, [1], "the adapter's own catch-up asks the server whether the turn has ended")
                    self.assertEqual((proc.signals, a.notices), ([], []))
                asyncio.run(scenario())

    def test_if_the_catch_up_after_a_skipped_event_fails_the_turn_stops_safely(self):
        async def scenario():
            a, proc = self.make([{"type": "a", "odd": True}, {"type": "b"}])
            a.on_event = lambda ev: (_ for _ in ()).throw(KeyError("x")) if ev.get("odd") else a.handled.append(ev["type"])

            async def resync():
                raise TypeError("cannot tell whether the turn has ended")
            a._resync = resync
            done, err = await self.drive(a, proc, lambda: bool(proc.signals))
            self.assertTrue(proc.signals, "the process tree is ended (its exit watcher ends the turn)")
            self.assertEqual((len(a.notices), a.failed, a.handled), (1, [1], []))
            self.assertIsNone(err)
        asyncio.run(scenario())

    def test_a_lost_stream_still_reconnects_quietly(self):
        async def scenario():
            a, proc = self.make([{"type": "a"}, OSError("stream reset")])
            a.on_event = lambda ev: a.handled.append(ev["type"])
            done, err = await self.drive(a, proc, lambda: len(a.handled) >= 2)       # one real 0.5 s reconnect delay
            self.assertIsNone(err)
            self.assertGreaterEqual(a.handled.count("a"), 2, "reconnected and reading again")
            self.assertEqual((proc.signals, a.notices, self.events(a).count("agent_event_failed")), ([], [], 0))
            self.assertIn("agent_events", self.events(a))
        asyncio.run(scenario())

    def test_v2_shares_this_loop(self):
        from agentj import agent_opencode2
        self.assertIs(agent_opencode2.OpenCodeV2Agent._events, agent_opencode.OpenCodeAgent._events)


if __name__ == "__main__":
    unittest.main()
