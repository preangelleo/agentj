"""P292: a saturated disk never stalls the event loop (2026-10-14, Leo's Mac seat: one host.log append on the loop took 58.8 s
→ peer mailbox / relay timeouts, hs_timeout, dev_gone).

(a) End to end through the real serve: every host.log append, every fsync and every read moved off the loop parks for up to
    30 s (released at teardown — the test never waits for it); a paired phone still resumes, every heartbeat ping gets its
    pong well inside the page's deadline, loop_watch records no stall and the phone is never `dev_gone`.
(b) diskq: the per-file queue is bounded (lines and bytes) and drops its oldest lines, leaving one `log_dropped` {count};
    an off-loop line never overtakes lines queued on the loop; every flush barrier is bounded.
(c) The status-line read (shared Claude's meter / `/context` / `/cost`) and the phone-activity touch run off the loop, one at a
    time."""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import json
import os
import pathlib
import secrets
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentj import auto_update, claude_statusline, diskq, seatcert, serve, shared, wire  # noqa: E402
from agentj.noise import IK, Handshake, Keypair  # noqa: E402
from agentj.state import State  # noqa: E402

STALL = 30.0                 # how long one disk operation would park without the teardown release
PAGE_PONG_DEADLINE = 10.0    # web/public/js/session.js HEARTBEAT_TIMEOUT_MS


class SlowDisk:
    """While `on`: every host.log append, every fsync and every patched read waits for `release` (≤ STALL s)."""

    def __init__(self):
        self.on, self.release, self.hits = threading.Event(), threading.Event(), []
        self._append, self._fsync = diskq._append, os.fsync

    def park(self, what):
        if self.on.is_set() and not self.release.is_set():
            self.hits.append((what, threading.current_thread().name))
            self.release.wait(STALL)

    def append(self, path, lines):
        self.park("append")
        return self._append(path, lines)

    def fsync(self, fd):
        self.park("fsync")
        return self._fsync(fd)

    def wrap(self, fn, what):
        def slow(*a, **kw):
            self.park(what)
            return fn(*a, **kw)
        return slow

    @contextlib.contextmanager
    def installed(self):
        with patch.object(diskq, "_append", self.append), patch.object(os, "fsync", self.fsync), \
                patch.object(seatcert.SeatCert, "cert", self.wrap(seatcert.SeatCert.cert, "seat_cert")), \
                patch.object(auto_update, "touch", self.wrap(auto_update.touch, "touch")), \
                patch.object(diskq, "LOOP_WAIT", _hermetic.DISKQ_LOOP_WAIT), \
                patch.object(diskq, "BUFFER_FOR", _hermetic.DISKQ_BUFFER_FOR), \
                patch.object(diskq, "LOOP_END_FLUSH", 0.5), patch.object(serve, "STOP_FLUSH", 0.5):
            try:
                yield self
            finally:
                self.release.set()
                diskq._buffer_until = 0.0


class Phone:
    """A paired remote's reconnect (IK resume) and its heartbeat, like web/public/js/session.js."""

    def __init__(self, relay_url, st):
        self.relay_url, self.st, self.kp = relay_url, st, Keypair.generate()

    def resume_and_ping(self, pings, gap, done=lambda: None):
        from websockets.sync.client import connect
        ch = self.st.config()["channel"]
        ws = connect(f"{self.relay_url}/v1/dev/{ch}", open_timeout=10).__enter__()
        try:
            while True:
                m = ws.recv(10)
                if isinstance(m, str) and json.loads(m) == {"t": "host", "up": True}:
                    break
            t0 = time.monotonic()
            hs = Handshake(IK, True, self.kp, prologue=wire.resume_prologue(ch), rs=self.st.host_keypair().pub)
            ws.send(bytes([wire.RESUME_INIT]) + hs.write_message(b"{}"))
            m = self._bin(ws, 10)
            hs.read_message(m[1:])
            send, recv, _ = hs.split()
            ws.send(bytes([wire.DATA]) + send.encrypt(b"", wire.pad_json({"t": "hello", "caps": [wire.CAP_P33]})))
            while self._app(ws, recv, 10).get("t") != "ready":
                pass
            ready_s, rtts = time.monotonic() - t0, []
            for _ in range(pings):
                r = secrets.token_hex(8)
                sent = time.monotonic()
                ws.send(bytes([wire.DATA]) + send.encrypt(b"", wire.pad_json({"t": "ping", "r": r})))
                while True:
                    left = PAGE_PONG_DEADLINE - (time.monotonic() - sent)
                    if left <= 0:
                        raise AssertionError("no pong within the page's deadline")
                    obj = self._app(ws, recv, left)
                    if obj.get("t") == "pong" and obj.get("r") == r:
                        break
                rtts.append(time.monotonic() - sent)
                time.sleep(gap)
            done()                                             # before this phone itself closes (its own dev_gone)
            return ready_s, rtts
        finally:
            ws.close()

    @staticmethod
    def _bin(ws, timeout):
        while True:
            m = ws.recv(timeout)
            if isinstance(m, bytes):
                return m

    def _app(self, ws, recv, timeout):
        m = self._bin(ws, timeout)
        return wire.unpad_json(recv.decrypt(b"", m[1:]), wire.MAX_JSON_P33)


class ServeOnSlowDisk(unittest.TestCase):
    def setUp(self):
        from fakerelay import FakeRelay
        self.tmp = tempfile.mkdtemp(prefix="aj292-", dir="/tmp")           # short: AF_UNIX paths ≤ ~107 bytes
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.relay = FakeRelay().__enter__()
        self.addCleanup(self.relay.__exit__)
        self.st = State(pathlib.Path(self.tmp) / "st")
        self.st.init(relay=self.relay.url, web="https://m.agentj.app")

    def test_phone_resumes_and_every_ping_is_answered_while_the_disk_is_stuck(self):
        phone = Phone(self.relay.url, self.st)
        self.st.pair_device(phone.kp.pub, "慢盘手机")
        events, orig_log = [], State.log

        def log(st, ev, **kw):
            events.append((ev, kw))
            return orig_log(st, ev, **kw)

        disk = SlowDisk()

        async def go():
            host = serve.Host(self.st, events="quiet", read_stdin=False)
            run = asyncio.create_task(host.run())
            try:
                end = time.monotonic() + 20
                while not host.relay_up and time.monotonic() < end:
                    await asyncio.sleep(0.05)
                self.assertTrue(host.relay_up, "serve never reached the fake relay")
                await asyncio.sleep(1.5)                       # loop_watch has ticked: its probe is running
                self.mark = len(events)                        # start-up (fast disk) is not what is measured
                disk.on.set()                                  # from here every disk operation parks
                ready_s, rtts = await asyncio.to_thread(phone.resume_and_ping, 6, 0.3,
                                                        lambda: setattr(self, "upto", len(events)))
                await asyncio.sleep(1.2)                       # one more loop_watch tick after the last pong
                return ready_s, rtts
            finally:
                host.stopping.set()
                t0 = time.monotonic()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(run, 20)
                self.stop_s = time.monotonic() - t0
                disk.release.set()                             # the threads parked in the disk may now finish

        with disk.installed(), patch.object(State, "log", log), patch.object(serve, "LOOP_STALL", 1.0):
            ready_s, rtts = asyncio.run(go())
        names = [e for e, _ in events[self.mark:self.upto]]
        self.assertTrue(any(w == "append" for w, _ in disk.hits), "the slow disk was really in the way of host.log")
        self.assertTrue(all(t != "MainThread" for _, t in disk.hits), disk.hits)   # nothing parked on the loop's thread
        self.assertLess(ready_s, 3.0, "resume answered with the disk stuck")
        self.assertEqual(len(rtts), 6)
        self.assertLess(max(rtts), 1.0, rtts)                  # far inside the page's 10 s
        self.assertIn("resume_ok", names)
        stalls = [kw for e, kw in events[self.mark:] if e == "loop_stall"]   # up to serve's stop
        self.assertEqual(stalls, [])
        self.assertNotIn("dev_gone", names)
        self.assertNotIn("hs_timeout", names)
        self.assertLess(self.stop_s, 10.0, "serve's stop is bounded on a stuck disk")


def _lines(p):
    return [json.loads(x) if x.startswith("{") else x for x in pathlib.Path(p).read_text().splitlines()]


class LogQueue(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="aj292q-"))
        self.addCleanup(__import__("shutil").rmtree, self.d, True)
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)
        self.addCleanup(setattr, diskq, "_buffer_until", 0.0)
        orig = diskq._append
        self.writes = []

        def held(path, lines):
            self.writes.append(len(lines))
            self.gate.wait(STALL)
            orig(path, lines)
        p = patch.object(diskq, "_append", held)
        p.start()
        self.addCleanup(p.stop)
        for k, v in (("LOOP_WAIT", 0.0), ("LOOP_END_FLUSH", 0.2)):
            q = patch.object(diskq, k, v)
            q.start()
            self.addCleanup(q.stop)

    def on_loop(self, fn):
        async def main():
            return fn()
        return asyncio.run(main())

    def test_full_queue_drops_oldest_lines_and_counts_them(self):
        log = self.d / "host.log"

        def many():
            diskq.append(log, b"0\n")
            end = time.monotonic() + 5
            while not self.writes and time.monotonic() < end:   # the log thread holds "0" in its (stuck) write
                time.sleep(0.01)
            t0 = time.monotonic()
            for i in range(1, 13):
                diskq.append(log, f"{i}\n".encode())
            return time.monotonic() - t0
        with patch.object(diskq, "LOG_MAX_LINES", 5):
            took = self.on_loop(many)
            self.assertLess(took, 0.5, "the loop never waits on a stuck disk")
            self.assertLessEqual(diskq.log_pending(), 5)
        self.gate.set()
        self.assertTrue(diskq.flush(5))
        got = _lines(log)
        self.assertEqual(got[0], "0")
        self.assertEqual(got[1]["ev"], "log_dropped")
        self.assertEqual(got[1]["count"], 7)                    # 1…7 dropped, the newest five kept
        self.assertEqual(got[2:], [str(i) for i in range(8, 13)])
        self.assertLessEqual(set(got[1]) - {"ts", "ev"}, State.LOG_FIELDS)
        self.assertTrue(all(isinstance(v, int) for k, v in got[1].items() if k != "ev"))

    def test_byte_cap(self):
        log = self.d / "host.log"
        line = b"x" * 1000 + b"\n"

        def many():
            diskq.append(log, b"0\n")
            end = time.monotonic() + 5
            while not self.writes and time.monotonic() < end:
                time.sleep(0.01)
            for _ in range(50):
                diskq.append(log, line)
        with patch.object(diskq, "LOG_MAX_BYTES", 10_000):
            self.on_loop(many)
            with diskq._llk:
                self.assertLessEqual(diskq._pbytes.get(str(log), 0), 10_000)
        self.gate.set()
        self.assertTrue(diskq.flush(5))
        got = _lines(log)
        self.assertEqual(got[1], {"ts": got[1]["ts"], "ev": "log_dropped", "count": 41})
        self.assertEqual(len(got), 2 + 9)

    def test_off_loop_line_waits_for_lines_queued_on_the_loop(self):
        log = self.d / "host.log"

        async def main():
            diskq.append(log, b"a\n")
            diskq.append(log, b"b\n")
            t = asyncio.create_task(asyncio.to_thread(diskq.append, log, b"c\n"))   # a worker thread's st.log
            await asyncio.sleep(0.2)
            self.assertFalse(t.done(), "the thread waits behind a and b")
            self.gate.set()
            await asyncio.wait_for(t, 5)
        asyncio.run(main())
        self.assertTrue(diskq.flush(5))
        self.assertEqual(log.read_text(), "a\nb\nc\n")

    def test_flush_barriers_are_bounded(self):
        log = self.d / "host.log"

        async def main():
            diskq.append(log, b"a\n")
            t0 = time.monotonic()
            ok = await diskq.aflush(0.3)
            return ok, time.monotonic() - t0
        t0 = time.monotonic()
        ok, took = asyncio.run(main())                       # + the loop's own end guard (LOOP_END_FLUSH)
        self.assertFalse(ok)
        self.assertLess(took, 1.0)
        self.assertLess(time.monotonic() - t0, 2.0, "asyncio.run returns although the disk never answered")
        t1 = time.monotonic()
        self.assertFalse(diskq.flush(0.2))
        self.assertLess(time.monotonic() - t1, 1.0)
        self.gate.set()
        self.assertTrue(diskq.flush(5))
        self.assertEqual(log.read_text(), "a\n")

    def test_serve_stop_flush_is_bounded(self):
        self.assertLessEqual(serve.STOP_FLUSH, 5.0)
        from agentj import cli
        self.assertLess(cli.SERVE_EXIT_GRACE, 20.0, "below launchd's ExitTimeOut / systemd's TimeoutStopSec")


class OffLoopReads(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.calls = []

    async def lags(self, seconds):
        out, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            t = time.monotonic()
            await asyncio.sleep(0.02)
            out.append(time.monotonic() - t - 0.02)
        return max(out)

    def agent(self):
        host = Mock()
        host.st.root = pathlib.Path(self.tmp.name)
        host.lang = "zh"
        a = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": self.tmp.name, "session_mode": "shared", "_workflow_ceo": True})
        a.session = {"sessionId": "sess-292", "pid": 1, "status": "idle"}
        return a, host

    def slow_read(self, *a, **kw):
        self.calls.append(threading.current_thread().name)
        self.release.wait(STALL)
        return {"model": None, "model_name": "Opus 5.5", "effort": "high", "ctx": {"pct": 42}, "h5": None, "week": None,
                "source_at": 1.0}

    async def test_context_command_reads_the_status_line_off_the_loop(self):
        a, _ = self.agent()
        with patch.object(claude_statusline, "read", self.slow_read):
            t = asyncio.create_task(a.command("context", ""))
            self.assertLess(await self.lags(0.4), 0.2)
            self.release.set()
            res = await asyncio.wait_for(t, 5)
        self.assertIn("42%", res.text)
        self.assertNotIn("MainThread", self.calls)

    async def test_meter_refresh_runs_one_read_at_a_time(self):
        a, host = self.agent()
        with patch.object(claude_statusline, "read", self.slow_read):
            for _ in range(10):                               # observe passes while the read is stuck
                a.read_statusline()
                await asyncio.sleep(0.01)
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(a.cur_model(), None)             # on the loop: the last reading (none yet), never a read
            self.release.set()
            end = time.monotonic() + 5
            while a._native_reading is not None and time.monotonic() < end:
                await asyncio.sleep(0.01)
        host.meter_update.assert_called_with(**self.slow_read())
        self.assertEqual(a.cur_effort(), "high")

    async def test_cost_reads_off_the_loop(self):
        a, _ = self.agent()

        def slow_cost(*_a):
            self.calls.append(threading.current_thread().name)
            self.release.wait(STALL)
            return 1.25
        with patch.object(claude_statusline, "read", lambda *a, **k: {}), patch.object(claude_statusline, "cost", slow_cost):
            t = asyncio.create_task(a.command("cost", ""))
            self.assertLess(await self.lags(0.3), 0.2)
            self.release.set()
            res = await asyncio.wait_for(t, 5)
        self.assertIn("$1.25", res.text)
        self.assertNotIn("MainThread", self.calls)

    async def test_phone_touch_off_the_loop_and_coalesced(self):
        st = State(pathlib.Path(self.tmp.name) / "st")
        st.init()
        host = serve.Host(st, events="quiet", read_stdin=False)

        def slow_touch(_st, now=None):
            self.calls.append(threading.current_thread().name)
            self.release.wait(STALL)
        with patch.object(auto_update, "touch", slow_touch):
            host._phone_touch()
            await asyncio.sleep(0.1)                          # the first touch is parked in the disk
            for _ in range(4):
                host._phone_touch()
            self.assertLess(await self.lags(0.3), 0.2)
            self.assertEqual(len(self.calls), 1)
            self.release.set()
            end = time.monotonic() + 5
            while host._touching is not None and time.monotonic() < end:
                await asyncio.sleep(0.01)
        self.assertEqual(len(self.calls), 2, "the touches asked for meanwhile run once, after the first")
        self.assertNotIn("MainThread", self.calls)


class AllowlistCache(unittest.TestCase):
    def test_devices_read_once_and_every_change_seen(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "st")
            st.init()
            kp, other = Keypair.generate(), State(st.root)        # `other` = the CLI / another process's writer
            other.pair_device(kp.pub, "手机")
            self.assertTrue(st.is_allowed(kp.pub))
            reads = []
            orig = pathlib.Path.read_text

            def spy(p, *a, **kw):
                if p.name == "devices.json":
                    reads.append(1)
                return orig(p, *a, **kw)
            with patch.object(pathlib.Path, "read_text", spy):
                for _ in range(20):
                    self.assertTrue(st.is_allowed(kp.pub))
                self.assertEqual(reads, [])
                st.devices()[wire.device_id(kp.pub)]["name"] = "mutated"   # callers get a copy
                self.assertNotEqual(st.devices()[wire.device_id(kp.pub)].get("name"), "mutated")
                other.remove_device(wire.device_id(kp.pub))
                reads.clear()
                self.assertFalse(st.is_allowed(kp.pub), "a revoke by another writer is seen at once")
                self.assertEqual(len(reads), 1)


if __name__ == "__main__":
    unittest.main()
