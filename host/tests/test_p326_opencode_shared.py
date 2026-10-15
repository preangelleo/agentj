"""P326 (Mac 10-15, 0.22.0a1): shared mode switched from Codex to OpenCode — the gate's probe passed, the real session idled.

agent.shared_session_id held a Claude / Codex session id (one pin for every harness) and no owner port was set: OpenCode took
the "attach the owner's own server" branch, every message ended in one second with 「指定电脑 OpenCode server 的 loopback 端口」,
the switch job waited three minutes for a hello that could never come, then rolled back.
- ForeignPin: a non-OpenCode pin is not ours — Agent J starts its own server and answers (no loopback card); an OpenCode pin
  without a port is opened by our own server (a missing one → a new ordinary session);
- OwnerPortBad: a bad owner port is a failed start (phase failed, the switch job ends at once) with a card the owner can act on;
- StartProbe: the switch gate starts `opencode serve` the way the session will (or reaches the owner's server) before switching;
- HelloFailsFast: a hello turn that ended without a word ends the job's wait at once (detail `empty` / `failed`);
- EmptyTurn: a turn that ends at once with nothing shown is told to the owner as an error, never a silent 「done」;
- RedactLinear: a long run without an "@" no longer makes the e-mail rule quadratic (loop_stall 5.4 s in privacy.redact).
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import socket
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import opencode_start, privacy, shared, switch_harness as sw  # noqa: E402
import test_p59_opencode as p59  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
UUID_PIN = "0b6f3e2a-1c4d-4e5f-8a9b-0c1d2e3f4a5b"      # a Claude / Codex session id shape


def _with(extra):
    orig = p59._host

    def make(st, sent):
        host = orig(st, sent)
        host.agent_cfg.update(extra)
        return host
    return orig, make


class _Extra(p59.Chain):
    shared = True
    extra: dict = {}
    test_key_change_restarts_owned_serve_same_conversation_and_cli_and_proxy = None

    def setUp(self):
        super().setUp()
        self._orig, p59._host = _with(self.extra)

    def tearDown(self):
        p59._host = self._orig
        super().tearDown()

    def events(self):
        return [json.loads(x) for x in self.st.log_path.read_text().splitlines()]


class ForeignPin(_Extra):
    extra = {"shared_session_id": UUID_PIN, "shared_opencode_port": 0}

    def test_claude_or_codex_pin_starts_our_own_server(self):
        async def script(c):
            await c.say("hello")
            await c.wait(lambda: "ECHO: hello" in c.msgs())
            await c.wait(lambda: c.host.agent.status == "idle")
            a = c.host.agent
            self.assertIs(type(a), shared.SharedOpenCodeAgent)
            self.assertTrue(a.owned_server)
            self.assertTrue(a.sid.startswith("ses"), a.sid)
            self.assertEqual(a.cfg["shared_session_id"], a.sid)
            self.assertFalse(any("loopback" in m or "端口" in m for m in c.msgs()), c.msgs())
            ev = self.events()
            self.assertIn("shared_pin_foreign", [e["ev"] for e in ev])
            self.assertNotIn("turn_empty", [e["ev"] for e in ev])
            self.assertNotIn(UUID_PIN, self.st.log_path.read_text())
        self.run_chain(script)


class OpenCodePinNoPort(_Extra):
    extra = {"shared_session_id": "ses_p326missing", "shared_opencode_port": 0}

    def test_opencode_pin_without_port_is_opened_by_our_server(self):
        async def script(c):
            await c.say("hi")
            await c.wait(lambda: "ECHO: hi" in c.msgs())
            a = c.host.agent
            self.assertTrue(a.owned_server)
            self.assertNotEqual(a.sid, "ses_p326missing")       # not in the store → a new ordinary session, never a card
            self.assertFalse(any("loopback" in m or "端口" in m for m in c.msgs()), c.msgs())
            self.assertNotIn("shared_pin_foreign", [e["ev"] for e in self.events()])
        self.run_chain(script)


class OwnerPortBad(_Extra):
    extra = {"shared_session_id": "ses_p326owner", "shared_opencode_port": 70000}

    def test_bad_port_is_a_failed_start_with_an_owner_readable_card(self):
        async def script(c):
            await c.say("hi")
            await c.wait(lambda: any("agent.shared_opencode_port" in m for m in c.msgs()))
            await c.wait(lambda: c.host.agent.status in ("idle", "down"))
            a = c.host.agent
            self.assertTrue(a.is_down())
            self.assertEqual(a.phase(), "failed")                 # the switch job's hello wait ends at once
            card = next(m for m in c.msgs() if "agent.shared_opencode_port" in m)
            self.assertIn("agentj config unset agent.shared_opencode_port", card)
            self.assertNotIn("turn_empty", [e["ev"] for e in self.events()])   # already told: one card, not two
        self.run_chain(script)


class EmptyTurn(_Extra):
    extra = {"shared_opencode_port": 0}

    def test_a_turn_that_ends_at_once_without_a_word_is_an_error(self):
        async def script(c):
            await c.say("hello")
            await c.wait(lambda: "ECHO: hello" in c.msgs())
            await c.wait(lambda: c.host.agent.status == "idle")
            await c.say("EMPTY")
            await c.wait(lambda: any("一个字也没回" in m for m in c.msgs()))
            await c.wait(lambda: c.host.agent.status == "idle")
            ev = [e for e in self.events() if e["ev"] == "turn_empty"]
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["agent"], "opencode")
            start, end, failed = c.host.turn_at
            self.assertTrue(0 < start <= end and failed)
            await c.say("again")                                  # the next one works as before
            await c.wait(lambda: "ECHO: again" in c.msgs())
            await c.wait(lambda: c.host.agent.status == "idle")
            self.assertFalse(c.host.turn_at[2])
            self.assertEqual(len([e for e in self.events() if e["ev"] == "turn_empty"]), 1)
        self.run_chain(script)


class StartProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.st = p59._state(self.tmp.name)
        self.work = root / "work"
        self.work.mkdir()
        self.bin = root / "opencode"
        self.mode("ok")
        self.saved = {k: os.environ.get(k) for k in ("AGENTJ_OPENCODE_BIN", "FAKE_OC_LOG", "XDG_DATA_HOME",
                                                      "OPENCODE_SERVER_PASSWORD")}
        os.environ.update({"AGENTJ_OPENCODE_BIN": str(self.bin), "FAKE_OC_LOG": str(self.work / ".oc.jsonl"),
                           "XDG_DATA_HOME": str(root / "data")})
        os.environ.pop("OPENCODE_SERVER_PASSWORD", None)
        self.cfg = {"kind": "codex", "dir": str(self.work), "fence": False, "session_mode": "shared",
                    "shared_session_id": UUID_PIN, "shared_opencode_port": 0}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def mode(self, m):
        body = {"ok": f'exec {sys.executable} {HERE / "fakeopencode.py"} "$@"',
                "die": "echo boom >&2; exit 1",
                "hang": "exec sleep 30",
                "wrongport": "echo 'opencode server listening on http://127.0.0.1:1'; exec sleep 30"}[m]
        self.bin.write_text(f"#!/bin/sh\n{body}\n")
        self.bin.chmod(0o700)

    def test_our_own_server_is_started_and_answers(self):
        r = sw.start_probe(self.st, "opencode", self.cfg)
        self.assertNotIn("reason", r)
        self.assertIn("secs", r)
        log = (self.work / ".oc.jsonl").read_text()
        self.assertIn('"/path"', log)                            # asked like the session will
        self.assertNotIn('"/session"', log)                      # nothing kept: no session, no turn
        r = sw.start_probe(self.st, "opencode", {**self.cfg, "session_mode": "independent"})
        self.assertNotIn("reason", r)

    def test_failures_are_fixed_codes(self):
        self.mode("die")
        self.assertEqual(sw.start_probe(self.st, "opencode", self.cfg)["reason"], "start_failed")
        self.mode("wrongport")
        self.assertEqual(sw.start_probe(self.st, "opencode", self.cfg, limit=5)["reason"], "start_failed")
        self.mode("hang")
        t0 = time.monotonic()
        self.assertEqual(sw.start_probe(self.st, "opencode", self.cfg, limit=2)["reason"], "start_timeout")
        self.assertLess(time.monotonic() - t0, 10)
        os.environ["AGENTJ_OPENCODE_BIN"] = str(self.work / "missing")
        self.assertEqual(sw.start_probe(self.st, "opencode", self.cfg)["reason"], "start_failed")
        self.assertIsNone(sw.start_probe(self.st, "opencode", {**self.cfg, "dir": "/nonexistent-p326"}))

    def test_owner_server_must_answer_with_the_pinned_session(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]
        r = sw.start_probe(self.st, "opencode", {**self.cfg, "shared_opencode_port": closed, "shared_session_id": "ses_x"})
        self.assertEqual(r["reason"], "owner_unreachable")
        self.assertEqual(opencode_start._owner(self.cfg, 70000)["reason"], "owner_unreachable")

    def test_reason_texts(self):
        for code in ("owner_unreachable", "owner_password", "owner_session"):
            for lang in ("zh", "en"):
                t = sw.reason_text(code, "opencode", lang)
                self.assertIn("OpenCode", t)
                self.assertNotEqual(t, sw.reason_text("call_failed", "opencode", lang))
        self.assertIn("agentj doctor", sw.reason_text("no_reply", "opencode", "zh", "empty"))
        self.assertIn("agentj doctor", sw.reason_text("no_reply", "opencode", "en", "empty"))


class HelloFailsFast(unittest.TestCase):
    def run_hello(self, failed):
        now = [1000.0]
        status = {"ok": True, "agent": "opencode", "agent_status": "idle", "agent_said_at": 0,
                  "agent_phase": "idle", "agent_turn_start_at": 0, "agent_turn_end_at": 0, "agent_turn_failed": False}
        polls = [0]

        def ctl(req):
            if req["cmd"] == "harness_hello":
                return {"ok": True, "at": 500.0}
            polls[0] += 1
            if polls[0] == 2:          # the hello turn ran: one second, no word
                status.update(agent_turn_start_at=501.0, agent_turn_end_at=502.0, agent_turn_failed=failed)
            return dict(status)

        def sleep(s):
            now[0] += s
        r = sw._hello(ctl, {"outcome": "ok", "from": "codex", "to": "opencode"}, sleep, lambda: now[0])
        return r, now[0] - 1000.0

    def test_empty_hello_turn_ends_the_wait_at_once(self):
        r, waited = self.run_hello(False)
        self.assertEqual((r["reason"], r["detail"]), ("no_reply", "empty"))
        self.assertLess(waited, 10)                              # not HELLO_WAIT (180 s)

    def test_failed_hello_turn_is_failed(self):
        r, waited = self.run_hello(True)
        self.assertEqual((r["reason"], r["detail"]), ("no_reply", "failed"))
        self.assertLess(waited, 10)

    def test_an_earlier_turn_does_not_count(self):
        now = [0.0]

        def ctl(req):
            if req["cmd"] == "harness_hello":
                return {"ok": True, "at": 500.0}
            return {"ok": True, "agent_status": "idle", "agent_said_at": 0, "agent_turn_start_at": 400.0,
                    "agent_turn_end_at": 450.0}

        def sleep(s):
            now[0] += s
        r = sw._hello(ctl, {"outcome": "ok", "from": "codex", "to": "opencode"}, sleep, lambda: now[0], wait=20)
        self.assertEqual(r["reason"], "no_reply")
        self.assertGreaterEqual(now[0], 20)


class RedactLinear(unittest.TestCase):
    def test_long_runs_are_linear(self):
        for blob in ("A" * 200_000, "a." * 100_000, ("x9" * 50_000) + "@", "a.b/" * 50_000):
            t0 = time.monotonic()
            privacy.redact(blob)
            self.assertLess(time.monotonic() - t0, 3.0, blob[:10])

    def test_same_matches(self):
        for text, want in (("mail bob.smith+x@corp.io now", "<email>"), ("a.b@c.de", "<email>"),
                           ("x--y@mail.example.org", "x--y@mail.example.org"), ("git@github.com:o/r", "git@github.com")):
            self.assertIn(want, privacy.redact(text), text)
        self.assertEqual(privacy.redact("foo..bar_baz@corp.io"), "<email>")


if __name__ == "__main__":
    unittest.main()
