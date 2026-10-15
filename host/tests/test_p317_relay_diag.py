"""P317: a host whose relay link never comes up says why — in the journal, in `agentj status --json`, in doctor.

Customer case (WSL, 0.20.2a1): serve stayed "reconnecting" for minutes, doctor's device probe was green, the journal had only
the start banner. The relay_down lines (phase / close code) existed only in host.log; nothing reached the service's stdout
because the link was never up (the "relay down" line only follows an up). Now:
- every failed attempt is classified (relaydiag: phase seat/connect/auth, reason, close code, HTTP status); the first, each
  change and an unchanged streak every RELAY_SAY s reach stdout in every events mode (the journal in service mode);
- the status answer carries `relay_last` (+ `identity_ok`), `agentj status` adds `relay_stuck` in words;
- serve checks at start that the channel in config.json is its signing key's (else every auth is refused, 4003);
- doctor's relay row probes the host leg itself (identity files, then a signed host socket), never while serve's link is up;
- the friends mailbox leg records its own phase / reason / close code (`friends.mbox_last`) and says it in the journal.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import pathlib
import socket
import ssl
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from websockets.asyncio.server import serve as ws_serve  # noqa: E402
from websockets.sync.server import serve as sync_serve  # noqa: E402

from agentj import doctor, peer, reconnect, relaydiag, serve, wire  # noqa: E402
from agentj.state import State  # noqa: E402

NONCE = "B" * 43


class VerifyingRelay:
    """The host path of the real relay, just enough: challenge → auth verified like relay/src/wire.ts verifyAuth (the
    channel is sha256-derived from pk, the signature over the auth message) → `ok`, or close 4003. `after` = a close code
    sent right after a good auth instead of `ok` (4001 replaced, 4002 seat)."""

    def __init__(self, after=None, silent=False):
        self.after, self.silent, self.auths = after, silent, []

    async def handler(self, ws):
        with contextlib.suppress(Exception):
            if self.silent:
                return await ws.wait_closed()
            ch = ws.request.path.rsplit("/", 1)[-1]
            await ws.send(json.dumps({"t": "challenge", "n": NONCE}))
            m = json.loads(await ws.recv())
            self.auths.append(m)
            if not self._ok(m, ch):
                return await ws.close(4003, "auth")
            if self.after:
                return await ws.close(self.after, "x")
            await ws.send(json.dumps({"t": "ok"}))
            await ws.wait_closed()

    @staticmethod
    def _ok(m, ch):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pk = wire.unb64u(m.get("pk", ""))
        if wire.channel_id(pk) != ch:
            return False
        try:
            Ed25519PublicKey.from_public_bytes(pk).verify(wire.unb64u(m["sig"]),
                                                         f"agentjarvis-relay-auth-v1\n{ch}\n{NONCE}".encode())
            return True
        except Exception:  # noqa: BLE001
            return False


def _events(st):
    return [json.loads(x) for x in st.log_path.read_text().splitlines() if x.strip()]


def _break_identity(st):
    """What a half-finished re-init leaves behind: config.json's channel no longer belongs to the signing key."""
    cfg = st.config()
    cfg["channel"] = wire.b64u(b"\x01" * 16)
    st.config_path.write_text(json.dumps(cfg))


class Classify(unittest.TestCase):
    def test_reasons(self):
        class Closed(Exception):
            def __init__(self, code):
                self.rcvd = type("F", (), {"code": code})()

        class Status(Exception):
            def __init__(self, code):
                self.response = type("R", (), {"status_code": code})()

        cases = [
            (socket.gaierror(-2, "Name or service not known"), "connect", "dns"),
            (TimeoutError(), "connect", "timeout"),
            (asyncio.TimeoutError(), "auth", "timeout"),
            (ssl.SSLCertVerificationError("x"), "connect", "tls"),
            (ConnectionRefusedError(), "connect", "refused"),
            (OSError(101, "unreachable"), "connect", "network"),
            (Status(429), "connect", "limit"),
            (Status(404), "connect", "http"),
            (Closed(4001), "auth", "replaced"),
            (Closed(4002), "auth", "seat"),
            (Closed(4003), "auth", "auth_refused"),
            (Closed(4029), "connect", "limit"),
            (Closed(1006), "auth", "closed"),
            (ValueError("relay refused auth"), "auth", "auth_refused"),
            (ValueError("bad challenge"), "auth", "bad_challenge"),
            (RuntimeError("secret-proxy-password"), "connect", "error"),
        ]
        for e, phase, why in cases:
            d = relaydiag.classify(e, phase)
            self.assertEqual(d["why"], why, (e, d))
            self.assertEqual(d["phase"], phase)
            self.assertNotIn("secret", json.dumps(d), "never the exception's message")
        self.assertEqual(relaydiag.classify(Closed(4003), "auth")["code"], 4003)
        self.assertEqual(relaydiag.classify(Status(429), "connect")["http"], 429)

    def test_text(self):
        d = relaydiag.classify(TimeoutError(), "connect")
        self.assertIn("phase=connect why=timeout", relaydiag.text(d, "en"))
        self.assertIn("opening the relay connection timed out", relaydiag.text(d, "en"))
        self.assertIn("打开中继连接超时", relaydiag.text2(d))
        self.assertIn("code=4003", relaydiag.text2(relaydiag.classify(type("E", (Exception,), {"rcvd": type("F", (), {"code": 4003})()})(), "auth")))
        self.assertEqual(relaydiag.text(None), "")
        self.assertIs(serve._close_code, relaydiag.close_code)


class HostLegJournal(unittest.TestCase):
    """The customer's journal: a service-mode serve (events quiet) against a relay that refuses every auth."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "state")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, relay, prep=None, seconds=1.2, events="quiet"):
        out = io.StringIO()

        async def go():
            async with ws_serve(relay.handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                self.st.init(relay=f"ws://127.0.0.1:{port}")
                if prep:
                    prep(self.st)
                host = serve.Host(self.st, events=events, read_stdin=False)
                task = asyncio.create_task(host.relay_loop())
                await asyncio.sleep(seconds)
                snap = (host.relay_up, host.relay_state(), dict(host.relay_last or {}), host.identity_ok)
                host.stopping.set()
                if host.ws is not None:
                    await host.ws.close()
                await asyncio.wait_for(task, 5)
                return snap

        with patch.object(serve, "AUTH_TIMEOUT", 0.4), patch.object(serve, "FAST_RETRY", 0.1), \
                contextlib.redirect_stdout(out):
            snap = asyncio.run(go())
        return snap, out.getvalue()

    def test_refused_auth_reaches_the_journal_once(self):
        snap, out = self._run(VerifyingRelay(), prep=_break_identity)
        up, state, last, identity_ok = snap
        self.assertFalse(up)
        self.assertEqual(state, "connecting")
        self.assertFalse(identity_ok)
        self.assertEqual((last["phase"], last["why"], last["code"]), ("auth", "auth_refused", 4003))
        self.assertGreaterEqual(last["attempts"], 3)
        self.assertIn("retry_in", last)
        lines = [x for x in out.splitlines() if "relay connect failed" in x]
        self.assertEqual(len(lines), 1, f"one line per change, not per attempt: {out!r}")
        self.assertIn("code=4003", lines[0])
        self.assertIn("identity files disagree", out, "the start-up identity check says it first")
        downs = [e for e in _events(self.st) if e["ev"] == "relay_down"]
        self.assertEqual(downs[0]["why"], "auth_refused")
        self.assertIn("relay_identity", [e["ev"] for e in _events(self.st)])

    def test_silent_relay_says_auth_timeout(self):
        snap, out = self._run(VerifyingRelay(silent=True), seconds=0.8)
        self.assertEqual((snap[2]["phase"], snap[2]["why"]), ("auth", "timeout"))
        self.assertTrue(snap[3])
        self.assertIn("phase=auth why=timeout", out)

    def test_change_is_said_again_and_recovery_is_said(self):
        relay = VerifyingRelay(after=4001)
        out = io.StringIO()

        async def go():
            async with ws_serve(relay.handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                self.st.init(relay=f"ws://127.0.0.1:{port}")
                host = serve.Host(self.st, events="quiet", read_stdin=False)
                task = asyncio.create_task(host.relay_loop())
                await asyncio.sleep(0.5)
                relay.after = None                # the other serve went away: the next attempt comes up
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if host.relay_up:
                        break
                up, last = host.relay_up, host.relay_last
                host.stopping.set()
                await host.ws.close()
                await asyncio.wait_for(task, 5)
                return up, last

        with patch.object(serve, "AUTH_TIMEOUT", 0.4), patch.object(serve, "FAST_RETRY", 0.1), contextlib.redirect_stdout(out):
            up, last = asyncio.run(go())
        self.assertTrue(up)
        self.assertIsNone(last, "cleared once up")
        self.assertIn("why=replaced code=4001", out.getvalue())
        self.assertIn("another Agent J is using this identity", out.getvalue())
        self.assertIn("relay up after", out.getvalue())

    def test_parked_seat_read_is_bounded(self):
        relay = VerifyingRelay()

        async def go():
            async with ws_serve(relay.handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                self.st.init(relay=f"ws://127.0.0.1:{port}")
                host = serve.Host(self.st, events="quiet", read_stdin=False)
                host.seat.cert = lambda: time.sleep(1.0)   # a disk that does not answer
                task = asyncio.create_task(host.relay_loop())
                await asyncio.sleep(0.6)
                last = dict(host.relay_last or {})
                host.stopping.set()
                await asyncio.wait_for(task, 5)
                return last

        with patch.object(serve, "RELAY_OPEN_TIMEOUT", 0.3), patch.object(serve, "FAST_RETRY", 0.1), \
                contextlib.redirect_stdout(io.StringIO()):
            last = asyncio.run(go())
        self.assertEqual((last.get("phase"), last.get("why")), ("seat", "timeout"))
        self.assertIn("seat certificate timed out", relaydiag.text(last, "en"))


class DoctorHostLeg(unittest.TestCase):
    """doctor's relay row: the device probe alone was green for the customer; now the host leg is probed too."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "state")

    def tearDown(self):
        self.tmp.cleanup()

    @contextlib.contextmanager
    def relay(self, after=None, refuse=False):
        def handler(ws):
            with contextlib.suppress(Exception):
                if ws.request.path.startswith("/v1/dev/"):
                    ws.send(json.dumps({"t": "host", "up": False}))
                    return ws.recv(timeout=3)
                ch = ws.request.path.rsplit("/", 1)[-1]
                ws.send(json.dumps({"t": "challenge", "n": NONCE}))
                m = json.loads(ws.recv(timeout=3))
                if refuse or not VerifyingRelay._ok(m, ch):
                    return ws.close(4003, "auth")
                if after:
                    return ws.close(after, "x")
                ws.send(json.dumps({"t": "ok"}))
                ws.recv(timeout=3)

        with sync_serve(handler, "127.0.0.1", 0) as server:
            import threading
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                yield f"ws://127.0.0.1:{server.socket.getsockname()[1]}"
            finally:
                server.shutdown()

    def test_probe_ok_identity_and_codes(self):
        with self.relay() as url:
            self.st.init(relay=url)
            self.assertEqual(doctor.probe_host_leg(self.st, url, 3), (True, None))
            _break_identity(self.st)
            ok, d = doctor.probe_host_leg(self.st, url, 3)
            self.assertFalse(ok)
            self.assertEqual(d["why"], "identity", "found locally, before any socket")
        with self.relay(after=4002) as url:
            st = State(pathlib.Path(self.tmp.name) / "fresh")
            st.init(relay=url)
            with patch.object(doctor, "_serve_status", return_value=None):
                row = doctor.check_relay(st)
            self.assertEqual(row["status"], doctor.WARN, "no seat yet (fresh install) is the seat row's fault, not ✗ relay")
            with patch.object(doctor, "_serve_status", return_value={"relay_up": False, "relay_state": "seat"}), \
                    patch.object(doctor, "probe_host_leg", side_effect=AssertionError("no probe")):
                self.assertEqual(doctor.check_relay(st)["status"], doctor.WARN)
        for code, why in ((4001, "replaced"), (4002, "seat")):
            with self.relay(after=code) as url:
                st = State(pathlib.Path(self.tmp.name) / f"s{code}")
                st.init(relay=url)
                ok, d = doctor.probe_host_leg(st, url, 3)
                self.assertFalse(ok)
                self.assertEqual((d["phase"], d["why"], d["code"]), ("auth", why, code))

    def test_relay_row(self):
        with self.relay(refuse=True) as url:
            self.st.init(relay=url)
            with patch.object(doctor, "_serve_status", return_value=None):
                row = doctor.check_relay(self.st)
            self.assertEqual(row["status"], doctor.FAIL, row)
            self.assertIn("host leg", row["summary"])
            self.assertIn("code=4003", row["summary"])
        with self.relay() as url:
            self.st.init(relay=url, force=True)
            with patch.object(doctor, "_serve_status", return_value=None):
                self.assertEqual(doctor.check_relay(self.st)["status"], doctor.OK)
            stuck = {"relay_up": False, "relay_last": {"phase": "auth", "why": "timeout", "code": None, "http": None}}
            with patch.object(doctor, "_serve_status", return_value=stuck):
                row = doctor.check_relay(self.st)
            self.assertEqual(row["status"], doctor.WARN, "a fresh probe connects, the service's own link does not")
            self.assertIn("phase=auth why=timeout", row["summary"])
            with patch.object(doctor, "_serve_status", return_value={"relay_up": True}), \
                    patch.object(doctor, "probe_host_leg", side_effect=AssertionError("would replace serve's link")):
                row = doctor.check_relay(self.st)
            self.assertEqual(row["status"], doctor.OK)
            self.assertIn("host leg up", row["summary"])

    def test_serve_row_says_where(self):
        self.st.init(relay="ws://127.0.0.1:1")
        res = {"ok": True, "relay_up": False, "relay_state": "reconnecting",
               "relay_last": {"phase": "connect", "why": "dns", "code": None, "http": None}}
        with patch.object(doctor.names, "ctl_call", return_value=res), patch.object(doctor, "_fenced", return_value=False):
            row = doctor.check_serve(self.st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("DNS", row["summary"])
        self.assertIn("phase=connect why=dns", row["summary"])


class ReconnectSaysWhere(unittest.TestCase):
    def test_final_sentence_carries_the_stuck_point(self):
        last = {"phase": "auth", "why": "auth_refused", "code": 4003, "http": None}
        res = {"ok": True, "relay_up": False, "relay_state": "reconnecting", "relay_last": last}

        class Svc:
            restarts = 0

            @staticmethod
            def status():
                return {"installed": True, "active": "active"}

            @classmethod
            def restart(cls):
                cls.restarts += 1

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            rc = reconnect.run(st, lang="en", out=out, ctl=lambda *a, **k: dict(res), service=Svc, sleep=lambda s: None,
                               env={}, as_json=True)
        doc = json.loads(out.getvalue())
        self.assertEqual(rc, 1)
        self.assertEqual(doc["broken"], "relay")
        self.assertEqual(doc["relay_last"], last)
        self.assertIn("code=4003", doc["result"])


class MailboxLeg(unittest.TestCase):
    def test_mailbox_records_phase_and_code(self):
        from test_peer import CB, FakeSt, tune
        statuses = []

        async def handler(ws):
            with contextlib.suppress(Exception):
                await ws.send(json.dumps({"t": "challenge", "n": NONCE}))
                await ws.recv()
                await ws.close(4003, "auth")

        async def go():
            async with ws_serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                with tempfile.TemporaryDirectory() as d:
                    st = FakeSt(pathlib.Path(d) / "a")
                    keys = peer.PeerKeys.load_or_create(st)
                    cb = CB()
                    cb.on_status = lambda ev, **kw: statuses.append((ev, kw))

                    async def cert():
                        return peer.self_signed_cert(b"\x02" * 32, keys.mbox)
                    net = peer.PeerNet(st, keys, f"ws://127.0.0.1:{port}", cert, cb)
                    tune(net)
                    task = asyncio.create_task(net.run())
                    for _ in range(100):
                        await asyncio.sleep(0.02)
                        if net.last_down:
                            break
                    last = net.last_down
                    await net.stop()
                    await asyncio.wait_for(task, 5)
                    return last

        last = asyncio.run(go())
        self.assertEqual((last["phase"], last["why"], last["code"]), ("auth", "auth_refused", 4003))
        self.assertIn("mbox_fail", [e for e, _ in statuses])


if __name__ == "__main__":
    unittest.main()
