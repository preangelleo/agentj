"""P129: relay first connect, phone reconnect diagnostics, Telegram hiccups.

- A relay that never sends its auth challenge, sends it late, or closes with 4003 costs one short attempt: the host retries
  after FAST_RETRY (not the doubling ladder) and logs where it failed (phase), the close code and how long it took.
- `relay_state`: "connecting" right after start (doctor / admin say so, not a fault), "reconnecting" after a real drop.
- The page's reconnect reason (hello `rc`) is bounded and logged with resume_ok; nothing off-shape gets through.
- Telegram: one or two failed polls are only logged (with the failure kind); the third (or two minutes) sets the status
  line (`tg: "down"` in the phone's status), never a history page; the next success clears it.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import pathlib
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from websockets.asyncio.server import serve as ws_serve  # noqa: E402

from agentj import doctor, serve, telegram as tg  # noqa: E402
from agentj.state import State  # noqa: E402

NONCE = "A" * 43


class ScriptedRelay:
    """One behaviour per incoming host connection: silent (never challenges), slow (challenges after `delay`), close4003
    (challenges, then closes like the relay's auth deadline), ok (challenge → auth → ok, then stays open)."""

    def __init__(self, script, delay=1.0):
        self.script, self.delay, self.seen, self.times = list(script), delay, [], []

    async def handler(self, ws):
        with contextlib.suppress(Exception):   # the host side closing first is part of every script
            await self._handle(ws)

    async def _handle(self, ws):
        mode = self.script.pop(0) if self.script else "ok"
        self.seen.append(mode)
        self.times.append(time.monotonic())
        if mode == "silent":
            return await ws.wait_closed()
        if mode == "slow":
            await asyncio.sleep(self.delay)
        await ws.send(json.dumps({"t": "challenge", "n": NONCE}))
        if mode == "close4003":
            return await ws.close(4003, "auth")
        await ws.recv()
        await ws.send(json.dumps({"t": "ok"}))
        await ws.wait_closed()


def _events(st):
    return [json.loads(x) for x in st.log_path.read_text().splitlines() if x.strip()]


class RelayFirstConnect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "state")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, script, until_up=1, timeout=8.0, **kw):
        relay = ScriptedRelay(script, **kw)

        async def go():
            async with ws_serve(relay.handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                self.st.init(relay=f"ws://127.0.0.1:{port}")
                host = serve.Host(self.st, events="jsonl", read_stdin=False)
                states = [host.relay_state()]
                t0 = time.monotonic()
                task = asyncio.create_task(host.relay_loop())
                ups = 0
                while time.monotonic() - t0 < timeout:
                    await asyncio.sleep(0.02)
                    ups = sum(1 for e in _events(self.st) if e["ev"] == "relay_up")
                    if ups >= until_up:
                        break
                states.append(host.relay_state())
                elapsed = time.monotonic() - t0
                host.stopping.set()
                if host.ws is not None:
                    await host.ws.close()
                await asyncio.wait_for(task, 5)
                return elapsed, states, host

        with patch.object(serve, "AUTH_TIMEOUT", 0.4), patch.object(serve, "FAST_RETRY", 0.1), \
                contextlib.redirect_stdout(io.StringIO()):
            elapsed, states, host = asyncio.run(go())
        return relay, elapsed, states, host

    def test_silent_relay_is_retried_fast(self):
        relay, elapsed, states, _ = self._run(["silent", "ok"])
        self.assertEqual(relay.seen, ["silent", "ok"])
        self.assertLess(elapsed, 2.5, "a relay that never challenges costs one AUTH_TIMEOUT + FAST_RETRY, not 24 s")
        downs = [e for e in _events(self.st) if e["ev"] == "relay_down"]
        self.assertEqual(downs[0]["phase"], "auth")
        self.assertIn("TimeoutError", downs[0]["reason"])
        self.assertGreaterEqual(downs[0]["ms"], 300)
        up = [e for e in _events(self.st) if e["ev"] == "relay_up"][0]
        self.assertIsInstance(up["ms"], int)
        self.assertEqual(states, ["connecting", "up"])

    def test_slow_challenge_and_auth_deadline_close(self):
        relay, elapsed, _, _ = self._run(["slow", "close4003", "ok"], delay=1.0)
        self.assertEqual(relay.seen, ["slow", "close4003", "ok"])
        self.assertLess(elapsed, 3.0)
        downs = [e for e in _events(self.st) if e["ev"] == "relay_down"]
        self.assertEqual([d["phase"] for d in downs], ["auth", "auth"])
        self.assertEqual(downs[1]["code"], 4003, "the relay's close code is logged (4003 = its auth deadline)")
        gaps = [b - a for a, b in zip(relay.times, relay.times[1:])]
        self.assertTrue(all(g < 1.2 for g in gaps), gaps)

    def test_backs_off_only_after_fast_retries(self):
        relay, _, states, _ = self._run(["close4003"] * 6, timeout=1.6)
        gaps = [b - a for a, b in zip(relay.times, relay.times[1:])]
        self.assertGreaterEqual(len(gaps), serve.FAST_RETRIES)
        self.assertTrue(all(g < 0.6 for g in gaps[:serve.FAST_RETRIES - 1]), gaps)
        if len(gaps) > serve.FAST_RETRIES:
            self.assertGreaterEqual(gaps[serve.FAST_RETRIES], 0.9, "then the 1 → 2 → 4 … ladder")
        self.assertEqual(states[-1], "connecting", "still inside the start-up grace")

    def test_relay_state_after_grace_and_after_drop(self):
        self.st.init(relay="ws://127.0.0.1:1")
        host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.assertEqual(host.relay_state(), "connecting")
        host.started_at -= serve.RELAY_GRACE + 1
        self.assertEqual(host.relay_state(), "reconnecting", "never up after the grace = a real problem")
        host.started_at = time.monotonic()
        host.relay_ever_up = True
        self.assertEqual(host.relay_state(), "reconnecting", "down after being up")
        host.relay_up = True
        self.assertEqual(host.relay_state(), "up")

    def test_doctor_reads_connecting_as_ok(self):
        self.st.init(relay="ws://127.0.0.1:1")
        with patch.object(doctor.names, "ctl_call", return_value={"relay_up": False, "relay_state": "connecting"}):
            c = doctor.check_serve(self.st)
        self.assertEqual(c["status"], doctor.OK)
        self.assertIn("connecting", c["summary"])
        with patch.object(doctor.names, "ctl_call", return_value={"relay_up": False, "relay_state": "reconnecting"}):
            c = doctor.check_serve(self.st)
        self.assertEqual(c["status"], doctor.WARN)
        self.assertIn("reconnecting", c["summary"])


class ReconnectReason(unittest.TestCase):
    def test_bounded(self):
        f = serve._reconnect_reason
        self.assertEqual(f({"why": "hb", "down": 1200}), ("hb", None, 1200))
        self.assertEqual(f({"why": "close", "code": 1006, "down": 5}), ("close", 1006, 5))
        self.assertEqual(f({"why": "load"}), ("load", None, None))
        for bad in (None, "hb", {"why": "x"}, {"why": "<script>"}, [], {"code": 1006}):
            self.assertEqual(f(bad), (), bad)
        self.assertEqual(f({"why": "close", "code": 99999, "down": -1}), ("close", None, None))
        self.assertEqual(f({"why": "close", "code": True, "down": 10 ** 12}), ("close", None, None))

    def test_close_code(self):
        class Frame:
            code = 4003
        class E(Exception):
            rcvd = Frame()
            sent = None
        self.assertEqual(serve._close_code(E()), 4003)
        self.assertIsNone(serve._close_code(ValueError()))


class TelegramHiccups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "state")
        self.st.init(relay="ws://127.0.0.1:1")
        self.host = serve.Host(self.st, events="jsonl", read_stdin=False)
        self.pages, self.changes = [], []
        self.host.hist_add = lambda *a, **k: self.pages.append(a)
        self.host.telegram_changed = lambda: self.changes.append(self.host._status_msg().get("tg"))
        self.t = tg.Telegram(self.host)
        self.host.telegram = self.t

    def tearDown(self):
        self.tmp.cleanup()

    def test_kinds(self):
        self.assertEqual(tg.failure_kind(urllib.error.HTTPError("u", 502, "x", {}, None)), "http_502")
        self.assertEqual(tg.failure_kind(socket.timeout()), "timeout")
        self.assertEqual(tg.failure_kind(TimeoutError()), "timeout")
        self.assertEqual(tg.failure_kind(urllib.error.URLError(socket.timeout())), "timeout")
        self.assertEqual(tg.failure_kind(urllib.error.URLError("refused")), "network")
        self.assertEqual(tg.failure_kind(ConnectionResetError()), "network")
        self.assertEqual(tg.failure_kind(json.JSONDecodeError("x", "", 0)), "bad_answer")
        self.assertEqual(tg.failure_kind(tg.TelegramError("not_ok")), "not_ok")

    def test_api_error_carries_kind_not_url(self):
        with patch.dict("os.environ", {"AJ_TEST_TG_KEY": "123:abc"}), patch.object(tg, "BASE", "http://127.0.0.1:1"):
            with self.assertRaises(tg.TelegramError) as cm:
                tg.api({"key_env": "AJ_TEST_TG_KEY"}, "getMe", {}, timeout=2)
        self.assertEqual(cm.exception.kind, "network")
        self.assertNotIn("123:abc", str(cm.exception))

    def test_one_blip_is_quiet_three_show_status_and_recover(self):
        self.t.failed("timeout")
        self.t.failed("timeout")
        self.assertEqual((self.pages, self.changes, self.t.error), ([], [], False), "two blips: log only")
        self.assertNotIn("tg", self.host._status_msg())
        self.t.failed("http_502")
        self.assertTrue(self.t.error)
        self.assertEqual(self.changes, ["down"], "third failure: one status change, tg=down")
        self.assertEqual(self.host._status_msg()["tg"], "down")
        self.t.failed("http_502")
        self.assertEqual(self.changes, ["down"], "no repeat while down")
        self.t.recovered()
        self.assertEqual(self.changes, ["down", None], "recovery clears it by itself")
        self.assertEqual(self.pages, [], "never a history page")
        ev = [e for e in _events(self.st) if e["ev"].startswith("telegram_")]
        self.assertEqual([(e["ev"], e.get("kind")) for e in ev],
                         [("telegram_fail", "timeout"), ("telegram_fail", "http_502"), ("telegram_down", "http_502"),
                          ("telegram_ok", None)])
        self.assertEqual(ev[-1]["count"], 4)

    def test_two_minutes_of_failure_also_shows(self):
        self.t.failed("timeout")
        self.t.fail_since -= tg.DOWN_SECS
        self.t.failed("timeout")
        self.assertTrue(self.t.error)
        self.assertEqual(self.changes, ["down"])

    def test_run_loop_failure_goes_to_failed(self):
        calls = []

        async def go():
            with patch.object(tg, "configuration", return_value={"owner_id": 1, "key_env": "X", "generation": "g"}), \
                    patch.object(self.t, "enabled", return_value=True), \
                    patch.object(tg, "api", side_effect=tg.TelegramError("timeout")), \
                    patch.object(tg, "RETRY", 0.01), \
                    patch.object(self.t, "failed", side_effect=lambda k: (calls.append(k), len(calls) >= 3 and self.host.stopping.set())):
                await asyncio.wait_for(self.t.run(), 5)
        asyncio.run(go())
        self.assertEqual(calls, ["timeout"] * 3)
        self.assertEqual(self.pages, [])


if __name__ == "__main__":
    unittest.main()
