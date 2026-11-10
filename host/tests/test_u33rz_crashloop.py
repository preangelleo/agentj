"""Ticket st_U33rz8bsIlz2tLydSRyzFQ (2026-11-09, STARAY 「周末回来就显示无法连接到主机」): a macOS serve in a crash loop
(exit 1 every ~5 s, 11 880 serve_start a day) — five rescue tickets said "no reason in host.log", two said "restart: ok".
Guards: serve_crash in host.log (type + file:line, no message); the rescue report carries service.log's last traceback,
redacted; a restart / an upgrade counts only when serve stays up, not when it answers once in its half-second of life."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402

import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from agentj import cli, rescue_kit as kit, rescue_watchdog as rw, service, service_recovery
from agentj.state import State


def _raise_in_agentj():
    ns = {}
    exec(compile("import json\ndef boom():\n    json.loads('{')\n", "/x/site-packages/agentj/scheduler.py", "exec"), ns)
    ns["boom"]()


class ServeCrashLine(unittest.TestCase):
    def test_where_is_file_and_line_only(self):
        try:
            _raise_in_agentj()
        except ValueError as e:
            w = cli.crash_where(e)
        self.assertRegex(w, r"^agentj/scheduler\.py:3 < json/decoder\.py:\d+$")

    def test_host_log_gets_type_and_place_never_the_message(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as d:
            st = State(pathlib.Path(d))
            try:
                raise PermissionError("/Volumes/test-fixture/private-note.txt credential_fixture_NOT_A_KEY_1234567890")
            except PermissionError as e:
                cli._log_serve_crash(st, e)
            lines = [json.loads(l) for l in st.log_path.read_text().splitlines()]
        self.assertEqual(lines[-1]["ev"], "serve_crash")
        self.assertEqual((lines[-1]["exception_type"], lines[-1]["code"]), ("PermissionError", 1))
        self.assertNotIn("test-fixture", json.dumps(lines))
        self.assertNotIn("credential_fixture_NOT_A_KEY_1234567890", json.dumps(lines))
        # host.log keeps file:line locally; the report line keeps only the fixed origin category (0304 fix1)
        self.assertIn(":", lines[-1]["where"])
        self.assertEqual(json.loads(kit.hostlog_line(lines[-1]))["where"], "other")


TB = """agentj serve 0.26.8a2 · 通道 AbCdEfGhIjKlMnOpQrStUv · service mode (--events quiet)
Traceback (most recent call last):
  File "/Volumes/test-fixture/runtime/lib/python3.11/site-packages/agentj/cli.py", line 3000, in main
    a.fn(a)
  File "/Volumes/test-fixture/runtime/lib/python3.11/site-packages/agentj/serve.py", line 8190, in _run
    self.scheduler.recover()
  File "/opt/homebrew/lib/python3.11/json/decoder.py", line 355, in raw_decode
    raise JSONDecodeError("Expecting value", s, err.value) from None
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0) token=abcdefgh12345678
"""


class RescueReportCarriesTheReason(unittest.TestCase):
    def test_last_traceback_frames_and_redacted_exception(self):
        tb = kit.last_traceback(TB)
        self.assertEqual(tb[:2], ["agentj", "agentj"])
        self.assertEqual(tb[-1], "json.decoder.JSONDecodeError")
        joined = "\n".join(tb)
        for leak in ("test-fixture", "abcdefgh12345678", "/Volumes/", "self.scheduler", "Expecting value"):
            self.assertNotIn(leak, joined)

    def test_no_traceback_is_empty(self):
        self.assertEqual(kit.last_traceback("agentj serve 0.30.2\nall good\n"), [])

    def test_check_crash_adds_the_detail_on_macos(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as d:
            (pathlib.Path(d) / "service.log").write_text(TB)
            with patch.dict(os.environ, {"AGENTJ_STATE_DIR": d}):
                r = kit.Report()
                r.facts["os_kind"] = "macos"
                kit.check_crash(r)
        self.assertTrue(r.facts["crash_traceback"])
        self.assertIsNotNone(r.get("crash_reason"))

    def test_free_text_without_a_traceback_never_reaches_the_report(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as d:
            (pathlib.Path(d) / "service.log").write_text("joined HomeWifi-5G-Leo SSID\nagentj serve is already running\n")
            with patch.dict(os.environ, {"AGENTJ_STATE_DIR": d}):
                r = kit.Report()
                r.facts["os_kind"] = "macos"
                kit.check_crash(r)
        self.assertFalse(r.facts["crash_traceback"])
        self.assertNotIn("HomeWifi", json.dumps(r.details if hasattr(r, "details") else vars(r), default=str))
        self.assertIn("serve_crash", kit.INTERESTING)


class RestartMustStayUp(unittest.TestCase):
    def _wd(self, probe):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as d:
            st = State(pathlib.Path(d))
            st.init(relay="ws://127.0.0.1:9", web="https://m.agentj.app")
            wd = rw.Watchdog(st, probe_fn=probe, restart_fn=lambda: True, restart_wait=1.0)
            return wd._restart("zh", lambda *a: None)

    def test_a_crash_loop_that_answers_once_is_not_back(self):
        n = {"i": 0}

        def flapping():
            n["i"] += 1
            return "ok" if n["i"] % 3 == 0 else "down"
        self.assertFalse(self._wd(flapping)["ok"])

    def test_a_steady_serve_is_back(self):
        self.assertTrue(self._wd(lambda: "ok")["ok"])


class UpgradeRecoveryMustStayUp(unittest.TestCase):
    def test_flapping_service_raises_recovery_inactive(self):
        n = {"i": 0}

        def status():
            n["i"] += 1
            return {"active": "active" if n["i"] % 4 == 0 else "activating"}
        with self.assertRaises(service.ServiceError):
            service_recovery.stays_up(status, timeout=1.5, stable=0.6)

    def test_steady_service_passes(self):
        service_recovery.stays_up(lambda: {"active": "active"}, timeout=1.5, stable=0.5)


if __name__ == "__main__":
    unittest.main()
