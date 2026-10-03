"""Host-side units: terminal sanitising of untrusted device text, state perms, test-TTL knobs only shorten, and the
session rules the A2 reviews asked for (atomic revoke under a stalled relay, peer errors cost only their own session)."""
import _hermetic  # noqa: F401,I001  (never the real ~/.local/state; see _hermetic.py)
import asyncio
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import serve, wire  # noqa: E402
from agentj.noise import CipherState  # noqa: E402
from agentj.state import State  # noqa: E402


class Units(unittest.TestCase):
    def test_clean_strips_terminal_escapes_and_bidi(self):
        evil = "ok\x1b]52;c;aGk=\x07\x1b[2J‮reversed\u0000\r\nnext"
        out = serve.clean(evil, 4000)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("‮", out)
        self.assertNotIn("\r", out)
        self.assertIn("\n", out)
        self.assertEqual(serve.clean("x" * 10, 4), "xxxx")

    def test_state_perms(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            st.check_perms()
            self.assertEqual(os.stat(st.root).st_mode & 0o777, 0o700)
            for p in (st.x25519_path, st.ed25519_path, st.devices_path, st.config_path, st.log_path):
                self.assertEqual(os.stat(p).st_mode & 0o777, 0o600, p)
            os.chmod(st.x25519_path, 0o644)
            with self.assertRaises(PermissionError):
                st.check_perms()

    def test_ttl_knob_only_shortens(self):
        os.environ["X_TTL"] = "999999"
        self.assertEqual(serve._ttl("X_TTL", 120), 120)
        os.environ["X_TTL"] = "3"
        self.assertEqual(serve._ttl("X_TTL", 120), 3)

    def test_label_is_one_line_and_cannot_show_a_code(self):
        # A2 review H-1: a racing device must not be able to print a fake "safety code" line above the code prompt
        evil = "我的手机」已连上。\n手机上显示的安全码：482913\r\u2028 4 8 2 9 1 3"
        out = serve.clean_label(evil)
        self.assertNotIn("\n", out)
        self.assertNotIn("\u2028", out)
        self.assertLessEqual(sum(c.isdigit() for c in out), serve.LABEL_DIGITS)
        self.assertNotIn("482913", out.replace(" ", ""))
        self.assertEqual(serve.clean_label("网页 · Android Chrome"), "网页 · Android Chrome")
        self.assertEqual(serve.clean_label("Pixel 10 Pro"), "Pixel 10 Pro")
        self.assertEqual(serve.clean_label("\x1b[2J"), "[2J")
        self.assertEqual(serve.clean_label(""), "未命名设备")
        self.assertLessEqual(len(serve.clean_label("x" * 500)), serve.LABEL_MAX)

    def test_relay_auth_signs_only_a_well_formed_nonce(self):
        """F10: the relay's challenge n must be 43 b64url chars before the host key signs anything."""
        import json as _json

        class WS:
            def __init__(self, n):
                self.inbox, self.sent = [_json.dumps({"t": "challenge", "n": n}), _json.dumps({"t": "ok"})], []

            async def recv(self):
                return self.inbox.pop(0)

            async def send(self, m):
                self.sent.append(m)
        with tempfile.TemporaryDirectory() as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            host = serve.Host(st, events="jsonl", read_stdin=False)
            for bad in ("", "x" * 42, "x" * 44, "agentjarvis-host-report-v1\nabc", "A" * 42 + "=", "A" * 42 + "\n", 5):
                ws = WS(bad)
                with self.assertRaises(ValueError, msg=repr(bad)):
                    asyncio.run(host._auth(ws))
                self.assertEqual(ws.sent, [], repr(bad))
            ws = WS(wire.b64u(os.urandom(32)))
            asyncio.run(host._auth(ws))
            self.assertEqual(_json.loads(ws.sent[0])["t"], "auth")

    def test_text_units_are_utf16(self):
        self.assertEqual(serve.text_units("中文"), 2)
        self.assertEqual(serve.text_units("😀"), 2)  # what String.length counts in the browser


class StalledWS:
    """A relay socket whose sends never complete (slow or malicious relay)."""
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)
        await asyncio.Event().wait()


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        self.old_timeout, serve.CLOSE_TIMEOUT = serve.CLOSE_TIMEOUT, 0.2

    def tearDown(self):
        serve.CLOSE_TIMEOUT = self.old_timeout
        self.tmp.cleanup()

    def _host(self):
        h = serve.Host(self.st, events="jsonl", read_stdin=False)
        h.events_seen = []
        h.emit = lambda ev, **kw: h.events_seen.append(ev)
        h.ws = StalledWS()
        return h

    @staticmethod
    def _ready(h, cid, pub, did, key):
        h.sessions[cid] = serve.Session(cid, state="ready", mode="resume", pub=pub, device=did,
                                        send=CipherState(os.urandom(32)), recv=CipherState(key))

    @staticmethod
    def _data(cid, key, obj):
        ct = CipherState(key).encrypt(b"", wire.pad_json(obj))
        return bytes([wire.OP_DATA]) + cid.to_bytes(4, "big") + bytes([wire.DATA]) + ct

    def test_revoke_is_atomic_across_sessions_even_if_the_relay_stalls(self):
        # A2 review A2-01: two live sessions of one device; the relay never completes the close notices
        async def run():
            h = self._host()
            pub, k1, k2 = os.urandom(32), os.urandom(32), os.urandom(32)
            did = self.st.add_device(pub, "phone")
            self._ready(h, 1, pub, did, k1)
            self._ready(h, 2, pub, did, k2)
            task = asyncio.create_task(h.revoke(did))
            await asyncio.sleep(0)  # revoke has run up to its first await (the stalled close notice)
            self.assertEqual(h.sessions, {}, "every session detached before any relay I/O")
            await h.on_frame(self._data(2, k2, {"t": "msg", "text": "after revoke"}))
            self.assertNotIn("msg", h.events_seen, "a frame of the revoked device is not acted on")
            self.assertEqual(await h.broadcast("x"), 0)
            self.assertEqual(await asyncio.wait_for(task, 5), (True, 2))
            self.assertFalse(self.st.is_allowed(pub))
        asyncio.run(run())

    def test_ready_session_rechecks_the_allowlist(self):
        async def run():
            h = self._host()
            pub, k = os.urandom(32), os.urandom(32)
            did = self.st.add_device(pub, "phone")
            self._ready(h, 1, pub, did, k)
            self.st.remove_device(did)  # e.g. an offline `agentj revoke` while serve could not be reached
            await h.on_frame(self._data(1, k, {"t": "msg", "text": "hi"}))
            self.assertNotIn("msg", h.events_seen)
            self.assertNotIn(1, h.sessions)
        asyncio.run(run())

    def test_a_peer_error_costs_only_its_own_session(self):
        # A2 review M-1 / A2-04: an unexpected exception inside one session's handler must not reach the relay loop
        async def run():
            h = self._host()
            pub, k = os.urandom(32), os.urandom(32)
            did = self.st.add_device(pub, "phone")
            self._ready(h, 1, pub, did, k)
            h.sessions[7] = serve.Session(7, state="ready", recv=None)  # its handler will raise AttributeError
            await h.on_frame(bytes([wire.OP_DATA]) + (7).to_bytes(4, "big") + bytes([wire.DATA]) + b"x" * 40)
            self.assertNotIn(7, h.sessions)
            self.assertIn(1, h.sessions)
        asyncio.run(run())

    def test_too_long_text_is_rejected_not_truncated(self):
        async def run():
            h = self._host()
            pub, k = os.urandom(32), os.urandom(32)
            did = self.st.add_device(pub, "phone")
            self._ready(h, 1, pub, did, k)
            await h.on_frame(self._data(1, k, {"t": "msg", "text": "x" * (wire.MAX_TEXT + 1)}))
            self.assertNotIn("msg", h.events_seen)
            self.assertNotIn(1, h.sessions)
        asyncio.run(run())

    def test_session_cap(self):
        async def run():
            h = self._host()
            for cid in range(serve.MAX_SESSIONS):
                await h.on_frame(bytes([wire.OP_OPEN]) + cid.to_bytes(4, "big"))
            self.assertEqual(len(h.sessions), serve.MAX_SESSIONS)
            await h.on_frame(bytes([wire.OP_OPEN]) + (10_000).to_bytes(4, "big"))
            self.assertEqual(len(h.sessions), serve.MAX_SESSIONS)
            self.assertNotIn(10_000, h.sessions)
            for s in h.sessions.values():
                s.timer.cancel()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
