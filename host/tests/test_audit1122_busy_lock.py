"""Audit 2026-11-22 N-H1 / N-H7 / H2b: the Board lock is never held across a subprocess, a file scan or a probe, and a
change raised in a worker thread reaches the Host on the event loop (call_soon_threadsafe), never by a put from the thread."""
import _hermetic  # noqa: F401
import asyncio
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import busy, serve

HOLD = 3.0          # how long the stand-in probe stays inside its "subprocess"
PROMPT = 0.5        # a loop-side Board call must come back well inside the hold


class _Slow:
    """A stand-in for a slow D-Bus / vm_stat / /proc pass: says when it was entered, then waits for the test."""
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def wait(self):
        self.entered.set()
        self.release.wait(HOLD)


def _loop_side(board):
    """What the event loop does on every harness event and every busy message."""
    t0 = time.monotonic()
    board.stream_event({"type": "assistant", "message": {"content": []}}, "own")
    board.msg("agent")
    return time.monotonic() - t0


class BoardLockNotHeldAcrossProbes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-audit1122-busy-")
        self.addCleanup(self.tmp.cleanup)
        self.board = busy.Board(self.tmp.name)
        self.slow = _Slow()

    def _during(self, work):
        t = threading.Thread(target=work, daemon=True)
        t.start()
        self.assertTrue(self.slow.entered.wait(5), "the stand-in probe was never reached")
        try:
            took = _loop_side(self.board)
        finally:
            self.slow.release.set()
            t.join(10)
        self.assertFalse(t.is_alive())
        self.assertLess(took, PROMPT, f"a loop-side Board call waited {took:.2f}s for a worker-thread probe")

    def test_slow_unit_scan_does_not_block_the_loop_side(self):
        def run(argv, **kw):
            self.slow.wait()
            return types.SimpleNamespace(stdout="", stderr="", returncode=0)
        self._during(lambda: self.board.refresh_outside(True, run=run))

    def test_slow_unit_end_lookup_does_not_block_the_loop_side(self):
        self.board.units = [busy.item("unit:agentj-p1-x.service", "P1", "ceo", "x")]

        def run(argv, **kw):
            if "list-units" not in argv:
                self.slow.wait()
            return types.SimpleNamespace(stdout="", stderr="", returncode=0)
        self._during(lambda: self.board.refresh_outside(True, run=run))

    def test_slow_process_probe_does_not_block_the_loop_side(self):
        self.board.start("own:tu:1", "worker", session="own", pid=1)
        slow = self.slow

        class Processes:
            def identity(self, pid):
                slow.wait()
                return None
            namespace_pid = lambda self, pid, ns: pid
        self._during(lambda: self.board.refresh_sources(Processes()))

    def test_slow_load_probe_does_not_block_the_loop_side(self):
        def read():
            self.slow.wait()
            return 1.0
        self._during(lambda: self.board.sample_load(read=read, cap_read=lambda: 3))

    def test_slow_capacity_probe_does_not_block_the_loop_side(self):
        def cap():
            self.slow.wait()
            return 3
        self._during(lambda: self.board.sample_load(read=lambda: 1.0, cap_read=cap))

    def test_results_are_still_applied(self):
        # the snapshot gathered outside the lock lands: a unit appears, then ends through unit_end, load / cap are kept
        listed = ["agentj-p7-job.service loaded active running P7 job"]

        def run(argv, **kw):
            if "list-units" in argv:
                return types.SimpleNamespace(stdout="\n".join(listed), stderr="", returncode=0)
            if "ActiveState" in argv:
                return types.SimpleNamespace(stdout="ActiveState=inactive\nLoadState=loaded\nExecMainStatus=0\nExecMainCode=1\n",
                                             stderr="", returncode=0)
            return types.SimpleNamespace(stdout="", stderr="", returncode=0)
        self.assertTrue(self.board.refresh_outside(True, run=run))
        self.assertEqual([u["key"] for u in self.board.units], ["unit:agentj-p7-job.service"])
        self.assertEqual([u["key"] for u in self.board.outside], ["unit:agentj-p7-job.service"])
        listed.clear()
        self.assertTrue(self.board.refresh_outside(True, run=run))
        self.assertEqual(self.board.units, [])
        self.assertEqual([(f["key"], f["exit_code"]) for f in self.board.finished], [("unit:agentj-p7-job.service", 0)])
        self.assertTrue(self.board.sample_load(read=lambda: 2.5, cap_read=lambda: 4))
        self.assertEqual((self.board.load, self.board.cap), (2.5, 4))

    def test_an_item_replaced_during_the_probe_keeps_its_evidence_by_key(self):
        import os
        self.board.start("own:tu:1", "worker", session="own", pid=os.getpid())
        board = self.board

        class Processes:
            def identity(self, pid):
                board.annotate("own:tu:1", model="m-1")      # the loop replaces the dict while the probe runs
                return "77"
            def descendants(self, pid): return [pid]
            def probe(self, pids): return {}
            def inspect(self, pid): return True
        self.board.refresh_sources(Processes())
        self.assertIn("own:tu:1", self.board.evidence)
        self.assertEqual(self.board.mem["own:tu:1"].get("proc_start"), "77")


class WorkerThreadChangeReachesTheLoop(unittest.TestCase):
    def test_busy_changed_from_a_worker_thread_is_delivered_on_the_loop(self):
        async def scenario():
            h = object.__new__(serve.Host)
            loop_thread = threading.get_ident()
            calls = []

            class Q:
                def put_nowait(self, item):
                    calls.append(("put", threading.get_ident()))

            h.post_q, h.busy_sent = Q(), None
            h.loop = asyncio.get_running_loop()
            h.wf = types.SimpleNamespace(last=None)

            def busy_msg():
                calls.append(("msg", threading.get_ident()))
                return {"tasks": [], "by": "a"}
            h.busy_msg = busy_msg
            worker = await asyncio.to_thread(lambda: (h.busy_changed(), threading.get_ident())[1])
            self.assertNotEqual(worker, loop_thread)
            for _ in range(20):
                if any(k == "put" for k, _ in calls):
                    break
                await asyncio.sleep(0.01)
            self.assertIn("put", [k for k, _ in calls], "the change was lost")
            self.assertEqual({t for _, t in calls}, {loop_thread},
                             "Host state was read / the post queue was written from a worker thread")

        asyncio.run(scenario())

    def test_board_change_in_a_worker_thread_runs_the_host_hook_on_the_loop(self):
        async def scenario():
            h = object.__new__(serve.Host)
            loop_thread, seen = threading.get_ident(), []
            h.loop = asyncio.get_running_loop()
            h._busy_changed_now = lambda: seen.append(threading.get_ident())
            with tempfile.TemporaryDirectory(prefix="aj-audit1122-busy-") as d:
                board = busy.Board(d, on_change=h.busy_changed)
                board.start("own:tu:9", "w", session="own")
                await asyncio.to_thread(board.stop, "own:tu:9")       # what reconcile_claude does from its thread
                await asyncio.sleep(0.05)
            self.assertTrue(seen)
            self.assertEqual(set(seen), {loop_thread})

        asyncio.run(scenario())

    def test_on_the_loop_it_is_still_synchronous(self):
        async def scenario():
            h = object.__new__(serve.Host)
            h.loop = asyncio.get_running_loop()
            seen = []
            h._busy_changed_now = lambda: seen.append(1)
            h.busy_changed()
            self.assertEqual(seen, [1])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
