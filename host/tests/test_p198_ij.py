"""P198 pre-check fixes I and J (FINDINGS-0.20 of the P164 acceptance).

I — the background service did not get OPENROUTER_API_KEY, so Jev (P157 approval layer 2) was off after an upgrade and the
owner would have had to hand-edit the service env file. Now: `agentj service import-key [NAME]` copies it from the invoking
shell in one step (0600, other bytes kept, value never printed), `agentj service install` offers / names that step, and
doctor has a `jev` row. No real key, no network, no systemd: the env file lives under a temporary XDG_CONFIG_HOME.

J — QA-J logged `seat_cert unreachable http_4xx` at every start: the live Dashboard answers its catch-all 404 for
`/v1/host/relay/cert` (the P185 Dashboard is not deployed yet), and the host counted that as "unreachable" → bootstrap grace →
after 72 h "expired" → phone messages and pairing refused. A 404 is now "unsupported" → verdict "unenforced" (allowed, no grace
clock), re-asked every RETRY_REFUSED; once the route answers, the certificate is used as before.
"""
import _hermetic  # noqa: F401,I001
import contextlib
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import cli, cloud, doctor, seatcert, service, wire
from agentj.state import State

KEY = "OPENROUTER_API_KEY"
FAKE = "sk-or-v1-" + "0123456789abcdef" * 4          # a fake, shaped like a real one
H = 3600
T0 = 1_800_000_000


class EnvBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"XDG_CONFIG_HOME": self.tmp.name, "HOME": self.tmp.name}
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name}, clear=False)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop(KEY, None)
        self.plat = mock.patch.object(service, "platform", return_value="linux")
        self.plat.start()
        self.addCleanup(self.plat.stop)
        self.path = pathlib.Path(service.env_file("agentj", self.env))


class ImportKey(EnvBase):
    def test_imports_once_keeps_other_lines_0600_value_parsed_back(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("AGENTJ_CLAUDE_BIN=\"/usr/bin/claude\"\nCLAUDE_CODE_OAUTH_TOKEN=x")   # no final newline
        os.chmod(self.path, 0o644)
        self.assertEqual(service.env_file_has(str(self.path), KEY), False)
        self.assertEqual(service.import_key("agentj", KEY, {**self.env, KEY: FAKE}), "imported")
        raw = self.path.read_text()
        self.assertTrue(raw.startswith("AGENTJ_CLAUDE_BIN=\"/usr/bin/claude\"\nCLAUDE_CODE_OAUTH_TOKEN=x\n"), "other bytes kept")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(service.private_env(str(self.path), {KEY})[KEY], FAKE, "macOS loader (shlex) reads the same value")
        self.assertTrue(service.env_file_has(str(self.path), KEY))
        self.assertEqual(service.import_key("agentj", KEY, {**self.env, KEY: FAKE}), "present", "idempotent")
        self.assertEqual(self.path.read_text().count(KEY), 1)

    def test_creates_the_file_when_missing(self):
        self.assertEqual(service.import_key("agentj", KEY, {**self.env, KEY: FAKE}), "imported")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_absent_in_the_shell_writes_nothing(self):
        self.assertEqual(service.import_key("agentj", KEY, dict(self.env)), "absent")
        self.assertFalse(self.path.exists())

    def test_empty_line_counts_as_missing(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(f"{KEY}=\n")
        os.chmod(self.path, 0o600)
        self.assertFalse(service.env_file_has(str(self.path), KEY))
        self.assertEqual(service.import_key("agentj", KEY, {**self.env, KEY: FAKE}), "imported")
        self.assertTrue(service.env_file_has(str(self.path), KEY), "the last assignment wins (systemd)")
        self.assertEqual(self.path.read_text().splitlines()[-1], f"{KEY}='{FAKE}'")

    def test_refuses_symlink_bad_values_and_names(self):
        self.path.parent.mkdir(parents=True)
        target = pathlib.Path(self.tmp.name) / "elsewhere.env"
        target.write_text("")
        self.path.symlink_to(target)
        with self.assertRaises(service.ServiceError):
            service.import_key("agentj", KEY, {**self.env, KEY: FAKE})
        self.assertEqual(target.read_text(), "", "never written through a symlink")
        self.path.unlink()
        for bad in ("a\nEVIL=1", "it's"):
            with self.assertRaises(service.ServiceError):
                service.import_key("agentj", KEY, {**self.env, KEY: bad})
        self.assertFalse(self.path.exists())
        with self.assertRaises(service.ServiceError) as c:
            service.import_key("agentj", "not a name", {**self.env})
        self.assertEqual(c.exception.reason, "bad_key_name")

    def test_value_never_in_notes(self):
        n1 = service.key_note("agentj", KEY, {**self.env, KEY: FAKE})
        self.assertIn(f"agentj service import-key {KEY}", n1)
        self.assertNotIn(FAKE, n1)
        n2 = service.key_note("agentj", KEY, dict(self.env))
        self.assertIn("agentj secret request --name OPENROUTER_API_KEY", n2)
        self.assertIn(f"#{KEY}", n2)
        service.import_key("agentj", KEY, {**self.env, KEY: FAKE})
        self.assertIsNone(service.key_note("agentj", KEY, dict(self.env)))


class CliImportKey(EnvBase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["agentj", *argv]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            try:
                cli.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_one_step_import_prints_no_value(self):
        os.environ[KEY] = FAKE
        self.addCleanup(os.environ.pop, KEY, None)
        code, out = self.run_cli("service", "import-key")
        self.assertIn(code, (0, None), out)
        self.assertIn("imported", out)
        self.assertNotIn(FAKE, out)
        self.assertEqual(service.private_env(str(self.path), {KEY})[KEY], FAKE)
        code, out = self.run_cli("service", "import-key", KEY)
        self.assertIn("already", out)

    def test_missing_in_shell_exits_with_the_phone_route(self):
        code, out = self.run_cli("service", "import-key")
        self.assertNotIn(code, (0, None))
        self.assertIn("agentj secret request", str(code) + out)

    def test_install_offer_without_a_terminal_names_the_command(self):
        os.environ[KEY] = FAKE
        self.addCleanup(os.environ.pop, KEY, None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._offer_key_import(service)
        self.assertIn(f"! {KEY}", out.getvalue())
        self.assertIn("agentj service import-key", out.getvalue())
        self.assertNotIn(FAKE, out.getvalue())
        self.assertFalse(self.path.exists(), "an Agent (no terminal) is told, nothing written behind the owner's back")

    def test_install_offer_at_a_terminal_imports_on_yes(self):
        os.environ[KEY] = FAKE
        self.addCleanup(os.environ.pop, KEY, None)
        tty = mock.patch.object(sys, "stdin", SimpleNamespace(isatty=lambda: True))
        out = io.StringIO()
        with tty, mock.patch("builtins.input", return_value="y"), contextlib.redirect_stdout(out), \
                mock.patch.object(sys.stdout, "isatty", return_value=True, create=True):
            cli._offer_key_import(service)
        self.assertEqual(service.private_env(str(self.path), {KEY})[KEY], FAKE)
        self.assertNotIn(FAKE, out.getvalue())


class DoctorRow(EnvBase):
    svc = {"installed": True, "name": "agentj"}

    def test_rows(self):
        self.assertIsNone(doctor.check_jev_key({"installed": False}))
        r = doctor.check_jev_key(self.svc)
        self.assertEqual((r["id"], r["status"]), ("jev", doctor.OK), "neither: off, optional — not a warning")
        os.environ[KEY] = FAKE
        self.addCleanup(os.environ.pop, KEY, None)
        r = doctor.check_jev_key(self.svc)
        self.assertEqual(r["status"], doctor.WARN)
        self.assertIn("import-key", r["hint"])
        self.assertNotIn(FAKE, json.dumps(r))
        service.import_key("agentj", KEY)
        r = doctor.check_jev_key(self.svc)
        self.assertEqual(r["status"], doctor.OK)
        self.assertNotIn(FAKE, json.dumps(r))


# ------------------------------------------------------------------ J
class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class OldThenNewDashboard:
    """The live Dashboard today: its catch-all 404 ("Not Found", no JSON) for /v1/host/relay/cert; then the P185 one."""

    def __init__(self):
        self.mode, self.calls = "old", 0

    def __call__(self, url, payload):
        self.calls += 1
        if self.mode == "old":
            return 404, {}
        if self.mode == "down":
            raise cloud.CloudError("network")
        inner = json.loads(wire.unb64u(payload["body"]))
        exp = inner["ts"] + 72 * H
        p = wire.b64u(json.dumps({"v": 1, "ch": inner["channel"], "exp": exp}, separators=(",", ":")).encode())
        return 200, {"cert": p + "." + "A" * 86, "exp": exp}


class SeatOnAnOlderDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="wss://relay.agentj.app")
        cloud.write_cloud(self.st, {"api": "https://agentj.example/api", "host_id": "h_198", "tenant": {"slug": "acme", "name": "Acme"},
                                    "linked_at": 1, "last_seq": 0})
        self.clock, self.dash = Clock(), OldThenNewDashboard()
        self.seat = seatcert.SeatCert(self.st, "wss://relay.agentj.app", post=self.dash, clock=self.clock)
        os.environ.pop("AGENTJ_TEST_SEAT_ENFORCE", None)

    def test_relay_cert_404_is_unsupported_not_unreachable(self):
        res = cloud.relay_cert(self.st, post=lambda u, p: (404, {}), now=self.clock)
        self.assertEqual((res.kind, res.status), ("unsupported", "http_4xx"))
        self.assertEqual(cloud.relay_cert(self.st, post=lambda u, p: (503, {}), now=self.clock).kind, "unreachable")
        self.assertEqual(cloud.relay_cert(self.st, post=lambda u, p: (400, {"error": "bad_request"}), now=self.clock).kind,
                         "fail")

    def test_never_locks_out_while_the_dashboard_lacks_the_route(self):
        self.assertEqual(self.seat.refresh(), "unsupported")
        self.assertIn("unsupported", self.st.log_path.read_text(), "logged as unsupported, not unreachable")
        for hours in (1, 71, 72, 200, 1000):
            self.clock.t = T0 + hours * H
            if self.seat.due():
                self.seat.refresh()
            self.assertEqual(self.seat.verdict(), "unenforced", f"{hours} h")
            self.assertTrue(self.seat.allowed())
        self.assertIsNone(self.seat.view()["exp"])

    def test_retry_spacing_and_pickup_once_deployed(self):
        self.seat.refresh()
        self.assertFalse(self.seat.due(), "a 404 waits RETRY_REFUSED")
        self.clock.t += seatcert.RETRY_REFUSED
        self.assertTrue(self.seat.due())
        self.dash.mode = "new"                       # the P185 Dashboard is deployed
        self.assertEqual(self.seat.refresh(), "ok")
        self.assertEqual(self.seat.verdict(), "ok")
        self.assertTrue(self.seat.cert())
        self.dash.mode = "down"                      # later outage: the normal 72 h rule applies again
        self.clock.t += 73 * H
        self.seat.refresh()
        self.assertEqual(self.seat.verdict(), "expired")

    def test_grace_clock_from_an_earlier_outage_is_dropped(self):
        self.dash.mode = "down"
        self.seat.refresh()
        self.assertIn("first_try", self.seat.read())
        self.dash.mode = "old"
        self.clock.t += seatcert.RETRY_FAIL
        self.seat.refresh()
        self.assertNotIn("first_try", self.seat.read())
        self.clock.t = T0 + 100 * H
        self.assertEqual(self.seat.verdict(), "unenforced")

    def test_definitive_answers_still_win(self):
        self.seat.refresh()
        self.clock.t += seatcert.RETRY_REFUSED
        self.seat.post = lambda u, p: (403, {"error": "not_bound"})
        self.seat.refresh()
        self.assertEqual(self.seat.verdict(), "unbound")

    def test_doctor_row_ok(self):
        self.seat.refresh()
        with mock.patch.object(seatcert, "SeatCert", lambda st, relay: self.seat):
            r = doctor.check_seat(self.st)
        self.assertEqual(r["status"], doctor.OK)
        self.assertIn("seat certificates yet", r["summary"])


if __name__ == "__main__":
    unittest.main()
