"""P127 (追加2): the first relay connection. A relay that never sends its challenge, sends it late or closes the socket at its
auth deadline must be detected quickly and retried at once (no 24 s hang, no growing back-off); `status` and doctor say
"connecting" during the start-up grace, not "reconnecting"."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from websockets.asyncio.server import serve as ws_serve  # noqa: E402

from agentj import doctor, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402


def _events(st, name):
    out = []
    for line in st.log_path.read_text().splitlines():
        d = json.loads(line)
        if d.get("ev") == name:
            out.append(d)
    return out


class FirstConnect(unittest.TestCase):
    # P130 integration: P129 owns the first-connect API and diagnostics; every P127 scenario remains.
    @staticmethod
    async def _idle(ws):
        try:
            await asyncio.wait_for(ws.wait_closed(), 30)       # ends with the server, so the test does not wait out 30 s
        except asyncio.TimeoutError:
            pass

    def run_relay(self, behaviours, until_up=True, budget=8.0):
        """behaviours: one per accepted connection — "silent" (no challenge), "late_close" (close 4001 after 0.3 s, like the
        relay's auth deadline), "ok" (challenge → auth → ok). Returns (state, accept times, host)."""
        accepts = []

        async def handler(ws):
            i = len(accepts)
            accepts.append(time.monotonic())
            kind = behaviours[min(i, len(behaviours) - 1)]
            if kind == "silent":
                await self._idle(ws)
            elif kind == "late_close":
                await asyncio.sleep(0.3)
                await ws.close(4001, "auth")
            else:
                await ws.send(json.dumps({"t": "challenge", "n": wire.b64u(os.urandom(32))}))
                msg = json.loads(await ws.recv())
                assert msg["t"] == "auth"
                await ws.send(json.dumps({"t": "ok"}))
                await self._idle(ws)

        async def main(st):
            async with ws_serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                cfg = st.config()
                cfg["relay"] = f"ws://127.0.0.1:{port}"
                st.write_private(st.config_path, json.dumps(cfg).encode())
                host = serve.Host(st, events="jsonl", read_stdin=False)
                self.assertEqual(host.relay_state(), "connecting")
                task = asyncio.create_task(host.relay_loop())
                end = time.monotonic() + budget
                while time.monotonic() < end and not (until_up and host.relay_up):
                    await asyncio.sleep(0.05)
                state = host.relay_state()
                host.stopping.set()
                task.cancel()
                with self.assertRaises((asyncio.CancelledError, Exception)):
                    await task
                return host, state

        d = tempfile.mkdtemp()
        st = State(pathlib.Path(d) / "s")
        st.init(relay="ws://127.0.0.1:1")
        with mock.patch.object(serve, "AUTH_TIMEOUT", 0.6), mock.patch.object(serve, "RELAY_OPEN_TIMEOUT", 2):
            host, state = asyncio.run(main(st))
        return st, accepts, host, state

    def test_silent_relay_is_detected_fast_and_retried_at_once(self):
        st, accepts, host, state = self.run_relay(["silent", "ok"])
        self.assertTrue(host.relay_ever_up)
        self.assertEqual(state, "up")
        down = _events(st, "relay_down")
        self.assertEqual(down[0]["phase"], "auth")
        self.assertLess(down[0]["ms"], 2000)                 # was: open 15 s + auth 10 s before anything happened
        self.assertLess(accepts[1] - accepts[0], 0.6 + 1.2)  # challenge timeout + fast retry, no 1 s → 2 s back-off
        self.assertEqual(len(_events(st, "relay_up")), 1)

    def test_auth_deadline_close_logs_the_code_and_retries_fast(self):
        st, accepts, host, state = self.run_relay(["late_close", "late_close", "ok"])
        down = _events(st, "relay_down")
        self.assertGreaterEqual(len(down), 2)
        self.assertEqual(down[0]["code"], 4001)
        self.assertIn(down[0]["phase"], ("challenge", "auth"))
        self.assertTrue(host.relay_up is False and host.relay_ever_up)
        self.assertLess(accepts[2] - accepts[0], 3.0)

    def test_never_up_within_grace_is_connecting_then_reconnecting(self):
        st, accepts, host, state = self.run_relay(["silent"], until_up=False, budget=1.5)
        self.assertEqual(state, "connecting")
        host.started_at -= serve.RELAY_GRACE + 1
        self.assertEqual(host.relay_state(), "reconnecting")

    def test_doctor_says_connecting_not_reconnecting(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            res = {"ok": True, "relay_up": False, "relay": "connecting", "uptime": 7}
            with mock.patch.object(doctor.names, "ctl_call", return_value=res), \
                    mock.patch.dict(os.environ, {"AGENTJ_TEST_DOCTOR_RELAY_WAIT": "0"}):
                row = doctor.check_serve(st)
            self.assertIn("正在连接中继", row["detail"] if "detail" in row else json.dumps(row, ensure_ascii=False))
            self.assertNotIn("reconnecting", json.dumps(row))
            seq = iter([{"ok": True, "relay_up": True, "relay": "up", "uptime": 9}])
            with mock.patch.object(doctor.names, "ctl_call", side_effect=lambda *a, **k: next(seq, res)), \
                    mock.patch.object(doctor.time, "sleep", lambda s: None):
                first = {"ok": True, "relay_up": False, "relay": "connecting", "uptime": 2}
                self.assertTrue(doctor._wait_relay(st, first)["relay_up"])

    def test_stall_watchdog_logs_metadata_only(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            host = serve.Host(st, events="jsonl", read_stdin=False)

            async def main():
                t = asyncio.create_task(host.loop_watch())
                await asyncio.sleep(0.1)
                time.sleep(2.0)                     # block the loop
                await asyncio.sleep(1.2)
                host.stopping.set()
                t.cancel()
            with mock.patch.object(serve, "LOOP_STALL", 0.5):
                asyncio.run(main())
            ev = _events(st, "loop_stall")
            self.assertTrue(ev and ev[0]["ms"] >= 500)
            self.assertEqual(set(ev[0]), {"ts", "ev", "ms"})


if __name__ == "__main__":
    unittest.main()
