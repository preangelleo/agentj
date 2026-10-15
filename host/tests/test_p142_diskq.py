"""P142 item 1: no fsync on the event-loop thread (agentj/diskq.py).

(a) A state write made on the loop (State._write_private, tg_cursor, devices/config, memory, provider profiles…) is visible at
    once (tmp → rename on the spot) but its fsync runs on the one ordered writer thread, never the loop's.
(b) The same write off the loop fsyncs inline before the rename (the CLI path; negative control).
(c) host.log: a slow append on the loop switches the loop to buffered lines; the lines land in order and the barrier
    (`flush`/`aflush`) waits for them; off the loop a line never overtakes lines still queued.
(d) A slow disk (fsync parked for 1.5 s) never stalls the loop: a timer due in 50 ms fires on time."""
import _hermetic  # noqa: I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import diskq, state as state_mod, tg_cursor  # noqa: E402
from agentj.state import State  # noqa: E402

_real_fsync = os.fsync


class FsyncSpy:
    def __init__(self, delay=0.0):
        self.calls, self.delay = [], delay

    def __call__(self, fd):
        self.calls.append(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        return _real_fsync(fd)


class DiskQ(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="aj-p142-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, True))
        self.addCleanup(diskq.flush)
        # _hermetic keeps host.log synchronous for every other test; this module exercises the product's buffering window
        self.addCleanup(setattr, diskq, "BUFFER_FOR", diskq.BUFFER_FOR)
        diskq.BUFFER_FOR = _hermetic.DISKQ_BUFFER_FOR

    def test_on_loop_write_visible_now_fsync_on_writer_thread(self):
        spy, p = FsyncSpy(), self.d / "devices.json"

        async def main():
            with patch.object(os, "fsync", spy):
                state_mod._write_private(p, b'{"a":1}')
                seen = p.read_bytes()                      # visible before any fsync finished
                loop_thread = threading.current_thread().name
                self.assertTrue(await diskq.aflush())
                return seen, loop_thread
        seen, loop_thread = asyncio.run(main())
        self.assertEqual(seen, b'{"a":1}')
        self.assertTrue(spy.calls)
        self.assertNotIn(loop_thread, spy.calls)
        self.assertEqual(set(spy.calls), {"agentj-disk"})
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
        self.assertEqual([x.name for x in self.d.iterdir()], ["devices.json"])   # no tmp left

    def test_off_loop_write_fsyncs_inline(self):
        spy, p = FsyncSpy(), self.d / "config.json"
        with patch.object(os, "fsync", spy):
            state_mod._write_private(p, b"{}")
        self.assertEqual(spy.calls, [threading.current_thread().name])
        self.assertEqual(diskq.pending(), 0)

    def test_tg_cursor_and_state_on_loop(self):
        spy = FsyncSpy()
        st = State(self.d / "st")
        st.root.mkdir(parents=True)

        async def main():
            with patch.object(os, "fsync", spy):
                tg_cursor._write(self.d / "telegram-offset.json", {"offset": 7})
                st.write_private(st.devices_path, b'{"dev":{}}')
                await diskq.aflush()
        asyncio.run(main())
        self.assertEqual(json.loads((self.d / "telegram-offset.json").read_text()), {"offset": 7})
        self.assertEqual(st.devices_path.read_bytes(), b'{"dev":{}}')
        self.assertEqual(set(spy.calls), {"agentj-disk"})

    def test_slow_disk_never_stalls_loop(self):
        spy, p = FsyncSpy(delay=1.5), self.d / "slow.json"

        async def main():
            with patch.object(os, "fsync", spy):
                t0 = time.monotonic()
                state_mod._write_private(p, b"1")
                write_s = time.monotonic() - t0
                t1 = time.monotonic()
                await asyncio.sleep(0.05)
                late = time.monotonic() - t1
                await diskq.aflush()
            return write_s, late
        write_s, late = asyncio.run(main())
        self.assertLess(write_s, 0.5)
        self.assertLess(late, 0.5)
        self.assertEqual(spy.calls[0], "agentj-disk")

    def test_log_buffered_after_slow_append_keeps_order(self):
        log = self.d / "host.log"
        orig = diskq._append
        slow_once = {"n": 0}

        def slow(path, lines):
            if slow_once["n"] == 0:
                slow_once["n"] = 1
                time.sleep(diskq.SLOW_APPEND * 3)
            orig(path, lines)

        async def main():
            with patch.object(diskq, "_append", slow):
                diskq.append(log, b"1\n")                 # slow → buffered mode
                self.assertGreater(diskq._buffer_until, time.monotonic())
                for i in range(2, 6):
                    diskq.append(log, f"{i}\n".encode())
                await diskq.aflush()
        try:
            asyncio.run(main())
        finally:
            diskq._buffer_until = 0.0
        self.assertEqual(log.read_text().split(), ["1", "2", "3", "4", "5"])

    def test_off_loop_line_never_overtakes_queued_lines(self):
        log = self.d / "host.log"
        gate = threading.Event()
        diskq._submit(gate.wait)                         # the writer is busy
        with diskq._lk:
            diskq._logbuf[str(log)] = [b"a\n"]
        diskq._submit(lambda: diskq._append(str(log), diskq._logbuf.pop(str(log))))
        threading.Timer(0.2, gate.set).start()
        diskq.append(log, b"b\n")                        # off the loop: waits for "a" first
        self.assertEqual(log.read_text(), "a\nb\n")

    def test_state_log_reaches_disk_by_serve_stop_barrier(self):
        st = State(self.d / "st2")
        st.root.mkdir(parents=True)
        diskq._buffer_until = time.monotonic() + 5

        async def main():
            st.log("serve_stop")
            await diskq.aflush()
        try:
            asyncio.run(main())
        finally:
            diskq._buffer_until = 0.0
        self.assertIn('"serve_stop"', st.log_path.read_text())


if __name__ == "__main__":
    unittest.main()
