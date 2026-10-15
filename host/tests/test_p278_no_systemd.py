"""P278 (P277 follow-up; a Windows user on WSL2 whose upgrade failed at the service restart, 2026-10-14): on a Linux / WSL
without systemd, `agentj service install [--deferred]` used to die with a FileNotFoundError traceback (no `systemd-run`).

- detection: `systemctl --user` reachable (the bus inside the fence too); else why — wsl_systemd_off (WSL, /etc/wsl.conf
  does not turn it on) · wsl_systemd_restart (it does, WSL not restarted yet) · no_systemd (PID 1 is another init) ·
  no_user_manager (systemd runs, the user manager not);
- the CLI says it in one plain sentence (zh / en) with the fix and exits NO_MANAGER_EXIT (78), never a traceback;
- `update apply` (manual and authorized): the new CLI's 78 at the service step → reason service_restart_failed,
  service no_systemd, rollback skipped (the old version could not restart either), the marker instead of the pending record,
  a `fix:` line in UPGRADE_RESULT;
- doctor's service row and the recovery notice say the same.
All I/O hermetic (fake runners, fake files). The container runs are the private p278-container.sh script."""
import _hermetic  # noqa: F401,I001
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agentj import doctor, service, service_recovery, update, upgrade_diag
from agentj.state import State

NEW = "9.9.9"
INFO = {"kind": "uv", "where": "/fake", "legacy": False}
NOT_BOOTED = "System has not been booted with systemd as init system (PID 1). Can't operate.\nFailed to connect to bus: Host is down\n"


class Detection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p278-")
        self.addCleanup(self.tmp.cleanup)
        self.d = pathlib.Path(self.tmp.name)
        self.osr = self.d / "osrelease"
        self.osr.write_text("6.6.87.2-microsoft-standard-WSL2\n")
        self.conf = self.d / "wsl.conf"

    def classify(self, pid1=False, env=None):
        with mock.patch.object(service, "pid1_systemd", return_value=pid1):
            return service.classify_no_manager(env if env is not None else {}, str(self.conf), str(self.osr))

    def test_wsl_cases(self):
        self.assertEqual(self.classify(), "wsl_systemd_off")                                    # no wsl.conf at all
        self.conf.write_text("[automount]\nenabled=true\n[boot]\nsystemd=false\n")
        self.assertEqual(self.classify(), "wsl_systemd_off")
        for text in ("[boot]\nsystemd=true\n", "[boot]\nsystemd = True  # on\n", '[boot]\nsystemd="true"\n',
                     "[boot]\ncommand=x\n[boot]\nsystemd=true\n"):
            self.conf.write_text(text)
            self.assertEqual(self.classify(), "wsl_systemd_restart", text)
        self.assertEqual(self.classify(pid1=True), "no_user_manager")

    def test_wsl_from_environment_and_not_wsl(self):
        self.osr.write_text("6.12.0-arch1\n")
        self.assertEqual(self.classify(env={"WSL_DISTRO_NAME": "Ubuntu"}), "wsl_systemd_off")
        self.assertEqual(self.classify(), "no_systemd")
        self.assertEqual(self.classify(pid1=True), "no_user_manager")

    def test_a_broken_wsl_conf_is_off(self):
        self.conf.write_text("systemd=true\n[[[")
        self.assertFalse(service.wsl_systemd_enabled(str(self.conf)))
        self.assertFalse(service.wsl_systemd_enabled(str(self.d / "missing")))

    def test_messages_are_bilingual_with_the_fix(self):
        for r in service.NO_MANAGER:
            m = service.MESSAGES[r]
            self.assertIn(" / ", m)
            self.assertRegex(m, r"[一-鿿]")
        self.assertIn("wsl --shutdown", service.MESSAGES["wsl_systemd_off"])
        self.assertIn("systemd=true", service.MESSAGES["wsl_systemd_off"])
        self.assertIn("wsl --shutdown", service.MESSAGES["wsl_systemd_restart"])
        self.assertIn("agentj serve", service.MESSAGES["no_systemd"])

    def test_systemctl_missing_or_not_booted(self):
        with mock.patch.object(service, "platform", return_value="linux"), \
                mock.patch.object(service, "classify_no_manager", return_value="wsl_systemd_off"), \
                mock.patch("shutil.which", return_value=None):
            with self.assertRaises(service.ServiceError) as e:
                service._systemctl("restart", "x.service", check=True)
            self.assertEqual(e.exception.reason, "wsl_systemd_off")
            self.assertEqual(service.systemd_problem(), "wsl_systemd_off")
        fail = subprocess.CompletedProcess([], 1, "", NOT_BOOTED)
        with mock.patch.object(service, "platform", return_value="linux"), \
                mock.patch.object(service, "classify_no_manager", return_value="no_systemd"), \
                mock.patch("shutil.which", return_value="/usr/bin/systemctl"), mock.patch("subprocess.run", return_value=fail):
            with self.assertRaises(service.ServiceError) as e:
                service._systemctl("restart", "x.service", check=True)
            self.assertEqual(e.exception.reason, "no_systemd")
            self.assertEqual(service.systemd_problem(), "no_systemd")
            self.assertEqual(service._systemctl("is-active", "x.service").returncode, 1, "check=False never raises")

    def test_a_working_user_manager_is_no_problem(self):
        ok = subprocess.CompletedProcess([], 0, "HOME=/x\n", "")
        with mock.patch.object(service, "platform", return_value="linux"), \
                mock.patch("shutil.which", return_value="/usr/bin/systemctl"), mock.patch("subprocess.run", return_value=ok):
            self.assertIsNone(service.systemd_problem())
        with mock.patch.object(service, "platform", return_value="macos"):
            self.assertIsNone(service.systemd_problem())

    def test_install_names_the_case(self):
        fail = subprocess.CompletedProcess([], 1, "", NOT_BOOTED)
        with mock.patch.object(service, "platform", return_value="linux"), \
                mock.patch.object(service, "_systemctl", return_value=fail), \
                mock.patch.object(service, "classify_no_manager", return_value="wsl_systemd_restart"), \
                mock.patch.dict(os.environ, {"HOME": self.tmp.name}):
            with self.assertRaises(service.ServiceError) as e:
                service.install(None)
        self.assertEqual(e.exception.reason, "wsl_systemd_restart")


class LaunchJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p278-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"),
                                         "AGENTJ_STATE_DIR": str(root / "state")})
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")

    def test_no_systemd_run_no_busctl_is_a_reason_not_a_traceback(self):
        def run(cmd, **kw):
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        with mock.patch.object(service, "platform", return_value="linux"), mock.patch("subprocess.run", run), \
                mock.patch.object(service, "systemd_problem", return_value="no_systemd"):
            with self.assertRaises(service.ServiceError) as e:
                service_recovery.launch_job(self.st, ["agentj", "x"])
            self.assertEqual(e.exception.reason, "no_systemd")
            with self.assertRaises(service.ServiceError):
                service_recovery.schedule(self.st, ["agentj"], NEW)
        rec = service_recovery.read(self.st)
        self.assertEqual((rec["status"], rec["reason"]), ("failed", "no_systemd"))
        self.assertIn("agentj serve", service_recovery.failure_text(rec))

    def test_systemd_present_but_launch_fails_keeps_the_old_reason(self):
        with mock.patch.object(service, "platform", return_value="linux"), \
                mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"", b"x")), \
                mock.patch.object(service, "systemd_problem", return_value=None):
            with self.assertRaises(service.ServiceError) as e:
                service_recovery.launch_job(self.st, ["agentj", "x"])
        self.assertEqual(e.exception.reason, "recovery_launch_failed")

    def test_cli_deferred_exits_78_with_the_sentence(self):
        from agentj import __version__, cli
        a = mock.Mock(mode="install", recovery_worker=None, deferred=__version__, json=False)
        err = io.StringIO()
        with mock.patch.object(service_recovery, "launch_job", side_effect=service.ServiceError("wsl_systemd_off")), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as e:
            cli.cmd_service(a)
        self.assertEqual(e.exception.code, service.NO_MANAGER_EXIT)
        self.assertIn("wsl --shutdown", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_cli_os_error_is_a_sentence(self):
        from agentj import cli
        a = mock.Mock(mode="restart", recovery_worker=None, deferred=None, json=False)
        with mock.patch.object(service, "restart", side_effect=FileNotFoundError(2, "x")), self.assertRaises(SystemExit) as e:
            cli.cmd_service(a)
        self.assertIn("FileNotFoundError", str(e.exception.code))

    def test_real_subprocess_without_any_systemd_binary(self):
        """The real CLI in a child with a PATH that has no systemctl / systemd-run / busctl (what a container or a WSL without
        systemd looks like to it): exit 78, the WSL sentence, no traceback."""
        from agentj import __version__
        bindir = pathlib.Path(self.tmp.name) / "bin"
        bindir.mkdir()
        env = {**os.environ, "PATH": str(bindir), "WSL_DISTRO_NAME": "Ubuntu-P278"}
        r = subprocess.run([sys.executable, "-m", "agentj.cli", "service", "install", "--deferred", __version__],
                           capture_output=True, text=True, env=env, timeout=60,
                           cwd=str(pathlib.Path(service.__file__).parent.parent))
        self.assertNotIn("Traceback", r.stderr)
        if service.pid1_systemd():      # this test machine boots systemd: not "no systemd" — the old plain failure (exit 1)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertTrue(r.stderr.startswith("✗ "), r.stderr)
        else:                           # a container / WSL without systemd (also the private p278-container.sh)
            self.assertEqual(r.returncode, service.NO_MANAGER_EXIT, r.stderr)
            self.assertRegex(r.stderr, r"WSL")


class Runner:
    def __init__(self, rc=service.NO_MANAGER_EXIT, err=None):
        self.calls, self.rc = [], rc
        self.err = err if err is not None else "✗ " + service.MESSAGES["wsl_systemd_off"] + "\n"

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, f"agentj {NEW}", "")
        if "--deferred" in argv:
            return subprocess.CompletedProcess(argv, self.rc, "", self.err)
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")


class Upgrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p278-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"),
                                         "AGENTJ_STATE_DIR": str(root / "state"), "XDG_CACHE_HOME": str(root / ".cache")})
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        self.rolled = []
        rb = mock.patch.object(update, "roll_back", side_effect=lambda *a: self.rolled.append(a) or True)
        rb.start()
        self.addCleanup(rb.stop)

    def patches(self):
        s = contextlib.ExitStack()
        s.enter_context(mock.patch.object(update, "install_kind", return_value=INFO))
        s.enter_context(mock.patch.object(update, "new_argv", return_value=["agentj"]))
        s.enter_context(mock.patch.object(update, "writable", return_value=True))
        return s

    def check(self, res, run, reason="wsl_systemd_off"):
        self.assertEqual((res["result"], res["reason"], res["service"], res["rollback"], res["step"], res["exit"]),
                         ("failed", "service_restart_failed", "no_systemd", "skipped", "service", 1))
        self.assertEqual(res["detail"], reason)
        self.assertEqual(self.rolled, [], "no rollback: the old version could not restart either")
        self.assertTrue("--deferred" in run.calls[-1], "nothing runs after the service step")
        self.assertFalse((self.st.root / update.PENDING).exists())
        self.assertEqual(json.loads((self.st.root / update.UPGRADED).read_text())["to"], NEW)
        block = update.result_block(res)
        self.assertIn("reason: service_restart_failed", block)
        self.assertIn("service: no_systemd", block)
        self.assertIn("rollback: skipped", block)
        self.assertIn("fix: " + service.MESSAGES[reason], block)
        self.assertIn("没有回滚", block)
        self.assertNotIn("已尝试回滚", block)
        self.assertIn("systemd", upgrade_diag.owner_sentence(res, {"status": "off"}, "en"))
        self.assertEqual(upgrade_diag.read(self.st)["reason"], "service_restart_failed")

    def test_apply(self):
        run = Runner()
        with self.patches():
            res = update.apply(self.st, NEW, plan_fn=lambda st, info, t: update.commands(info, t),
                               check_fn=lambda: {"status": "newer", "latest": NEW}, run=run, svc_on=True,
                               rollback_fn=lambda st: pathlib.Path("/fake/old.whl"), fenced=False)
        self.check(res, run)

    def test_apply_restart_case_and_unknown_words(self):
        run = Runner(err="✗ " + service.MESSAGES["wsl_systemd_restart"] + "\n")
        with self.patches():
            res = update.apply(self.st, NEW, plan_fn=lambda st, info, t: update.commands(info, t),
                               check_fn=lambda: {"status": "newer", "latest": NEW}, run=run, svc_on=True,
                               rollback_fn=lambda st: None, fenced=False)
        self.check(res, run, "wsl_systemd_restart")
        (self.st.root / update.UPGRADED).unlink()
        run = Runner(err="something else\n")
        with self.patches(), mock.patch.object(service, "systemd_problem", return_value=None):
            res = update.apply(self.st, NEW, plan_fn=lambda st, info, t: update.commands(info, t),
                               check_fn=lambda: {"status": "newer", "latest": NEW}, run=run, svc_on=True,
                               rollback_fn=lambda st: None, fenced=False)
        self.check(res, run, "no_systemd")

    def test_authorized_apply(self):
        from agentj import cloud
        import time
        cloud.write_cloud(self.st, {"api": "http://127.0.0.1:9", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                    "linked_at": int(time.time()), "last_seq": 0})
        run = Runner()
        with self.patches():
            res = update.authorized_apply(self.st, "AJUP-11111-11111-11111-11111", NEW, plan_fn=lambda st, i, t: update.commands(i, t),
                                          check_fn=lambda: {"status": "newer", "latest": NEW}, auth_fn=lambda *a: {"status": "ok"},
                                          run=run, svc_on=True, rollback_fn=lambda st: pathlib.Path("/fake/old.whl"))
        self.check(res, run)

    def test_any_other_service_failure_still_rolls_back(self):
        run = Runner(rc=1, err="Failed to start transient service unit\n")
        with self.patches():
            res = update.apply(self.st, NEW, plan_fn=lambda st, info, t: update.commands(info, t),
                               check_fn=lambda: {"status": "newer", "latest": NEW}, run=run, svc_on=True,
                               rollback_fn=lambda st: pathlib.Path("/fake/old.whl"), fenced=False)
        self.assertEqual((res["reason"], res["service"], res["rollback"]), ("service_restart_failed", "failed", "ok"))
        self.assertEqual(len(self.rolled), 1)
        self.assertNotIn("fix:", update.result_block(res))


class Doctor(unittest.TestCase):
    def test_no_systemctl_row_has_the_fix(self):
        with mock.patch.object(service, "classify_no_manager", return_value="wsl_systemd_off"):
            row = doctor.check_service({"kind": "none", "name": "agentj", "installed": False, "active": "no_systemctl"})
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("wsl --shutdown", json.dumps(row, ensure_ascii=False))

    def test_systemctl_without_a_manager(self):
        with mock.patch.object(service, "systemd_problem", return_value="no_systemd"):
            row = doctor.check_service({"kind": "systemd", "name": "agentj", "installed": False, "active": "unknown"})
        self.assertIn("agentj serve", json.dumps(row, ensure_ascii=False))
        with mock.patch.object(service, "systemd_problem", side_effect=AssertionError("not probed")):
            row = doctor.check_service({"kind": "systemd", "name": "agentj", "installed": False, "active": "inactive"})
        self.assertIn("not installed", json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
