"""P424 (#145, owner 10-19 17:46): the computer at load 36 (a browser test), serve slow but alive — the rescue watchdog took
the channel after 3 slow `status` answers (45 s) and the phone showed 「…没响应，救援守护在线」. On an overloaded computer a
slow answer is not yet "no response": each probe waits longer and a stall counts half (twice the grace); a missing
control socket ("down") is judged as before."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402

import asyncio
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agentj import names, rescue_watchdog as rw
from agentj.state import State


class Grace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p424-", dir=TEST_TMP)
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.tmp]))
        self.st = State(pathlib.Path(self.tmp) / "st")
        self.st.init(relay="ws://127.0.0.1:9", web="https://m.agentj.app")
        self.wd = rw.Watchdog(self.st, probe_fn=lambda: "stall")

    def test_overloaded_stalls_need_twice_the_failures(self):
        for _ in range(rw.TAKEOVER * 2 - 1):
            self.assertIsNone(self.wd.observe("stall", True))
        self.assertEqual(self.wd.observe("stall", True), "takeover")

    def test_normal_load_and_down_are_unchanged(self):
        for _ in range(rw.TAKEOVER - 1):
            self.assertIsNone(self.wd.observe("stall"))
        self.assertEqual(self.wd.observe("stall"), "takeover")
        wd = rw.Watchdog(self.st, probe_fn=lambda: "down")
        for _ in range(rw.TAKEOVER - 1):
            self.assertIsNone(wd.observe("down", True))
        self.assertEqual(wd.observe("down", True), "takeover", "no control socket at all: no extra grace")

    def test_loaded_reads_the_load_average_per_cpu(self):
        self.assertTrue(rw.loaded(lambda: (36.0, 20.0, 10.0), lambda: 8))
        self.assertFalse(rw.loaded(lambda: (3.0, 2.0, 1.0), lambda: 8))
        self.assertFalse(rw.loaded(lambda: (_ for _ in ()).throw(OSError()), lambda: 8), "no load average: not loaded")
        self.assertTrue(rw.loaded(lambda: (2.0, 1, 1), lambda: None), "unknown CPU count = 1")

    def test_probe_waits_longer_when_overloaded(self):
        seen = []
        with patch.object(names, "ctl_call", lambda st, obj, timeout: seen.append(timeout) or {"ok": True}), \
                patch.object(rw, "loaded", lambda: True):
            self.assertEqual(rw.probe(self.st), "ok")
        with patch.object(names, "ctl_call", lambda st, obj, timeout: seen.append(timeout) or {"ok": True}), \
                patch.object(rw, "loaded", lambda: False):
            self.assertEqual(rw.probe(self.st), "ok")
        self.assertEqual(seen, [rw.CTL_TIMEOUT_LOADED, rw.CTL_TIMEOUT])

    def test_run_loop_passes_the_load_to_the_counter(self):
        calls = []
        wd = rw.Watchdog(self.st, poll=0.01, probe_fn=lambda: "stall", stopped_fn=lambda: False, load_fn=lambda: True)
        real = wd.observe
        wd.observe = lambda res, heavy=False: (calls.append((res, heavy)), real(res, heavy))[1] if len(calls) < 3 \
            else wd.stopping.set()

        async def go():
            await asyncio.wait_for(wd.run(), 5)
        asyncio.run(go())
        self.assertEqual(calls[:3], [("stall", True)] * 3)
        self.assertFalse(wd.active, "three slow answers on an overloaded computer: still no takeover")


if __name__ == "__main__":
    unittest.main()
