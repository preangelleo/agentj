"""P331 (0.22.1a1 掉线): on the owner's loaded Mac serve's event loop came back up to 53 s late — the LaunchAgent ran it as
ProcessType=Background (darwinbg: PRI 4, efficiency cores only), and every serve loop re-parsed config.json5 + defaults.json5
with the pure-Python json5 parser. Phones then missed heartbeats and reconnected (dev_gone / relay_down / backfill storms).
Here: the plist asks for Interactive, a config parse happens once per distinct text, the load probes leave the loop, and a
burst of read receipts under a CPU hog keeps the loop answering."""
import _hermetic
import asyncio
import os
import plistlib
import tempfile
import threading
import sys
import time
import unittest
from unittest import mock

import json5

from test_l1 import Phone, _host, _ready, _state
from agentj import preferences, serve, service


class LaunchAgent(unittest.TestCase):
    def test_serve_is_never_a_background_process(self):
        d = plistlib.loads(service.plist_bytes("net.agentj.host", ["/x/agentj"], {}, "/tmp/service.log"))
        self.assertEqual(d["ProcessType"], "Interactive")
        self.assertTrue(d["KeepAlive"] and d["RunAtLoad"])


class ParseOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, "config.json5")
        self.p = mock.patch.object(preferences, "path", lambda: preferences.Path(self.cfg))
        self.p.start()
        preferences._PARSED.clear()
        self.calls = 0
        real = json5.loads

        def counting(*a, **k):
            self.calls += 1
            return real(*a, **k)
        self.j = mock.patch.object(preferences.json5, "loads", counting)
        self.j.start()

    def tearDown(self):
        self.j.stop()
        self.p.stop()
        self.tmp.cleanup()

    def write(self, text):
        with open(self.cfg, "w") as f:       # in place, same size: a stat key would miss this one
            f.write(text)

    def test_repeated_reads_parse_once_and_see_every_change(self):
        self.write("{version: 1, appearance: {language: 'zh'}}")
        for _ in range(50):
            self.assertEqual(preferences.get(preferences.effective(), "appearance.language"), "zh")
        self.assertLessEqual(self.calls, 2, "defaults.json5 + config.json5, once each")
        self.write("{version: 1, appearance: {language: 'en'}}")
        self.assertEqual(preferences.get(preferences.effective(), "appearance.language"), "en")
        self.assertLessEqual(self.calls, 3)

    def test_callers_get_private_copies(self):
        self.write("{version: 1, appearance: {language: 'zh'}}")
        a = preferences.effective()
        preferences.put(a, "appearance.language", "en")
        raw, doc = preferences.read()
        doc["appearance"]["language"] = "en"
        self.assertEqual(preferences.get(preferences.effective(), "appearance.language"), "zh")
        self.assertEqual(preferences.get(preferences.read()[1], "appearance.language"), "zh")

    def test_a_bad_file_keeps_failing_the_same_way(self):
        self.write("{version: 1, nope: 1}")
        for _ in range(3):
            with self.assertRaises(preferences.ConfigError) as e:
                preferences.read()
            self.assertEqual(e.exception.key, "nope")
        self.assertLessEqual(self.calls, 1)


class LoadProbeOffLoop(unittest.TestCase):
    def test_busy_loop_samples_load_on_a_worker_thread(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        h = _host(_state(tmp.name), [])
        seen = []

        def sample_load():
            seen.append(threading.current_thread() is threading.main_thread())
            return False

        async def go():
            with mock.patch.object(h.busy, "sample_load", sample_load), \
                 mock.patch.object(h.busy, "refresh_outside", lambda *_: None), \
                 mock.patch.object(h, "theme_refresh", lambda: False):
                t = asyncio.create_task(h.busy_loop())
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if seen:
                        break
                t.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await t
        asyncio.run(go())
        self.assertEqual(seen[:1], [False], "the load / memory probe ran on the event loop thread")


class HogAndReadStorm(unittest.TestCase):
    """High load + a phone sending read receipts as fast as it can: the mark is written only when it advances, no receipt
    re-parses the configuration, and next to two CPU-bound worker threads the storm runs several times faster at serve's
    GIL switch interval than at CPython's 5 ms (measured 4.6 s → 0.65 s; alone 0.01 s). Both runs share the machine's
    load, so a busy gate box slows them alike."""

    def storm(self, hogs: int):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = _state(tmp.name)
        h = _host(st, [])
        phone = _ready(h, Phone(st, "iPhone"), 11)
        phone.p33 = True
        turns = [h.hist.add({"k": "agent"}, f"r{i}") for i in range(40)]
        stop = threading.Event()

        def hog():
            x = 0
            while not stop.is_set():
                x += 1
        threads = [threading.Thread(target=hog, daemon=True) for _ in range(hogs)]
        for t in threads:
            t.start()
        writes, parses = [], []
        real_write, real_loads = st.write_private, json5.loads

        async def go():
            h._post = lambda f, *a: None
            t0 = time.monotonic()
            for i in range(400):                     # each id twice + every 10th one again (P321 retries), out of order
                tid = turns[(i // 2) % len(turns)]["id"] if i % 10 else turns[0]["id"]
                await h.on_p33(phone, "hist_read", {"epoch": h.hist.epoch, "id": tid})
            return time.monotonic() - t0
        try:
            with mock.patch.object(st, "write_private", lambda p, b, *a, **k: (writes.append(p.name), real_write(p, b, *a, **k))[1]), \
                 mock.patch.object(preferences.json5, "loads", lambda *a, **k: (parses.append(1), real_loads(*a, **k))[1]):
                took = asyncio.run(go())
        finally:
            stop.set()
            for t in threads:
                t.join()
        self.assertLessEqual(sum(n == "history-read.json" for n in writes), len(turns), "a receipt that does not advance the mark never writes")
        self.assertEqual(h.readmark.value(h.hist), turns[-1]["id"])
        self.assertEqual(parses, [], "a read receipt never parses config")
        return took

    def test_busy_threads_do_not_starve_the_loop(self):
        self.addCleanup(sys.setswitchinterval, sys.getswitchinterval())
        sys.setswitchinterval(0.005)                  # CPython's default: what serve ran with before P331
        before = self.storm(2)
        sys.setswitchinterval(serve.SWITCH_INTERVAL)  # what serve._run sets now
        after = min(self.storm(2) for _ in range(2))
        self.assertLess(after * 2.5, before, f"400 receipts next to two busy threads: {before:.2f}s at 5 ms, {after:.2f}s now")


if __name__ == "__main__":
    unittest.main()
