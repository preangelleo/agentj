"""0.30.23 regressions: live version handoff and one-shot package drift repair."""
import _hermetic
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from agentj import service, service_recovery, update


class UpgradeDrift(unittest.TestCase):
    def test_package_fingerprint_includes_schema_and_defaults(self):
        with patch.object(Path, "read_bytes", side_effect=[b"v", b"schema-a", b"defaults"]):
            first = update.package_fingerprint()
        with patch.object(Path, "read_bytes", side_effect=[b"v", b"schema-b", b"defaults"]):
            second = update.package_fingerprint()
        self.assertNotEqual(first, second)

    def test_restart_uses_linux_systemd(self):
        with patch.object(service, "platform", return_value="linux"), patch.object(service, "name", return_value="test"), patch.object(service, "_systemctl") as ctl:
            service.restart()
        ctl.assert_called_once_with("restart", "test.service", check=True)

    def test_restart_uses_macos_launchagent(self):
        with patch.object(service, "platform", return_value="macos"), patch.object(service, "name", return_value="test"), patch.object(service, "_launchctl", return_value=Mock(returncode=0)) as ctl:
            service.restart()
        self.assertEqual(ctl.call_args.args[0], "kickstart")

    def test_recovery_rejects_old_live_process_after_new_install(self):
        st = Mock()
        st.root = Path("/tmp/unused-agentj-hotfix-test")
        with patch.object(service_recovery, "record"), patch.object(service_recovery, "wait_ready", return_value="idle"), \
             patch.object(service_recovery, "stays_up"), patch.object(service_recovery, "process_version", return_value="old"), \
             patch.object(service_recovery, "cleanup_job"), patch.object(service_recovery, "read", return_value={}), \
             patch.object(update, "read_pending", return_value=None), patch.object(service_recovery, "restart_siblings"):
            rc = service_recovery.run(st, "new", install=lambda _: None, status=lambda: {"active": "active"})
        self.assertEqual(rc, 1)



PKG = Path(__file__).resolve().parents[1] / "agentj"

# The child process plays the OLD agentj that is still running; between its two halves the "installer" replaces the whole
# package directory it imported from (`uv tool install --force` recreates the tool venv: same path, new files).
COMMON = textwrap.dedent("""
    import json, os, shutil, sys
    from pathlib import Path
    SITE, NEW, STATE, OUT = map(Path, sys.argv[1:5])
    def install_new():
        shutil.rmtree(SITE / "agentj")
        shutil.copytree(NEW, SITE / "agentj")
    def emit(**kw):
        OUT.write_text(json.dumps(kw))
""")

SERVE_CHILD = COMMON + textwrap.dedent("""
    sys.path.insert(0, str(SITE))
    from agentj import preferences, preference_data, update, __version__
    from agentj.state import State
    assert preference_data.__file__.startswith(str(SITE)), preference_data.__file__
    fp0 = update.package_fingerprint()
    preference_data.defaults(); preferences.effective()
    install_new()
    errors = []
    for _ in range(3):                      # every web frame reads the effective preferences
        try:
            preferences.effective(); preference_data.defaults()
        except Exception as e:
            errors.append(type(e).__name__ + ": " + str(e))
    calls = []
    class R:
        returncode = 0; stdout = ""; stderr = ""
    def run(argv, **kw):
        calls.append(argv); return R()
    st = State(STATE)
    first = update.drift_handoff(st, fp0, run=run)
    second = update.drift_handoff(st, fp0, run=run)
    emit(running=__version__, disk=update.installed_version(), errors=errors, first=first, second=second, calls=calls)
""")

PHONE_CHILD = COMMON + textwrap.dedent("""
    FAKE_CLI, MODE = sys.argv[5], sys.argv[6]
    # launch(): the serve (old code) writes the snapshot; the job then starts from that copy, the venv after it on sys.path
    sys.path.insert(0, str(SITE))
    from agentj import auto_update
    from agentj.state import State
    st = State(STATE)
    worker = auto_update.snapshot_worker(st, "phone-lib", "agentj.phone_update")
    for m in [m for m in sys.modules if m == "agentj" or m.startswith("agentj.")]:
        del sys.modules[m]
    sys.path.insert(0, str(worker.parent))
    from agentj import phone_update, update, doctor, preferences, __version__ as old
    assert phone_update.__file__.startswith(str(worker.parent)), phone_update.__file__
    def apply(st, **kw):
        install_new()
        return {"from": old, "to": "9.9.9", "result": "ok", "reason": "upgraded", "exit": 0, "service": "not_installed"}
    def post_install_doctor(st):
        if MODE == "doctor_crash":
            raise RuntimeError("schema/default drift: heartbeat.daily_at")
        preferences.effective()             # the call that crashed the 0.30.21 job on M1 + Olares
        import importlib; importlib.import_module("agentj.humane")
        return []
    doctor.run = post_install_doctor
    update.new_argv = lambda info: [FAKE_CLI]
    update.install_kind = lambda prefix=None: {"kind": "uv"}
    phone_update.run(st, apply_fn=apply)
    rec = json.loads((st.root / phone_update.RESULT).read_text())
    emit(rec=rec, text=phone_update.text(rec, "zh"))
""")

FAKE_CLI = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_CLI_LOG"
exit "${FAKE_CLI_RC:-0}"
"""


class OldProcessNewPackage(unittest.TestCase):
    """Real files: the new package is installed while the old process keeps running (M1 + Olares, 2026-11-15)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aj-p023-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.site, self.new, self.state = self.tmp / "site", self.tmp / "new", self.tmp / "state"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(PKG, self.site / "agentj", ignore=ignore)
        shutil.copytree(PKG, self.new, ignore=ignore)
        self.state.mkdir(mode=0o700)
        # the new release: another version and a changed user-tier default, in its schema and its defaults alike
        import json5
        init = self.new / "__init__.py"
        init.write_text(__import__("re").sub(r'__version__ = "[^"]+"', '__version__ = "9.9.9"', init.read_text()))
        schema = json.loads((self.new / "config" / "schema.json").read_text())
        key = next(k for k, m in schema["keys"].items() if m.get("tier") == "user" and isinstance(m.get("default"), str))
        schema["keys"][key]["default"] += "-new"
        (self.new / "config" / "schema.json").write_text(json.dumps(schema))
        doc = json5.loads((self.new / "config" / "defaults.json5").read_text())
        node = doc
        for part in key.split(".")[:-1]:
            node = node[part]
        node[key.split(".")[-1]] += "-new"
        (self.new / "config" / "defaults.json5").write_text(json.dumps(doc))
        home = self.tmp / "home"
        home.mkdir()
        self.env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                    "XDG_STATE_HOME": str(home / ".state"), "XDG_DATA_HOME": str(home / ".data"),
                    "XDG_CACHE_HOME": str(home / ".cache"), "AGENTJ_STATE_DIR": str(self.state), "AGENTJ_TEST": "1",
                    "PYTHONPATH": "", "FAKE_CLI_LOG": str(self.tmp / "cli.log")}
        self.cli = self.tmp / "agentj"
        self.cli.write_text(FAKE_CLI)
        self.cli.chmod(0o755)

    def child(self, script, *extra, env=None):
        path, out = self.tmp / "child.py", self.tmp / "out.json"
        path.write_text(script)
        r = subprocess.run([sys.executable, "-I", str(path), str(self.site), str(self.new), str(self.state), str(out), *extra],
                           env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return json.loads(out.read_text())

    def cli_calls(self):
        log = self.tmp / "cli.log"
        return log.read_text().splitlines() if log.exists() else []

    def test_old_serve_keeps_web_connections_and_schedules_one_restart(self):
        out = self.child(SERVE_CHILD)
        self.assertEqual(out["disk"], "9.9.9")
        self.assertNotEqual(out["running"], "9.9.9")
        self.assertEqual(out["errors"], [], "no schema/default drift: the web frames keep working")
        self.assertEqual(out["first"], "scheduled")
        self.assertIsNone(out["second"], "one hand-off per package, never a loop")
        self.assertEqual(len(out["calls"]), 1)
        self.assertEqual(out["calls"][0][-4:], ["service", "install", "--deferred", "9.9.9"])

    def test_phone_job_hands_off_restart_after_install(self):
        out = self.child(PHONE_CHILD, str(self.cli), "ok")
        self.assertEqual(self.cli_calls(), ["service install --deferred 9.9.9"])
        self.assertEqual((out["rec"]["result"], out["rec"]["reason"], out["rec"]["to"]), ("ok", "upgraded", "9.9.9"))

    def test_post_install_crash_no_longer_skips_restart_or_misreports(self):
        # 0.30.21 on M1 + Olares: RuntimeError here → {"result": "failed", "reason": "RuntimeError"} → 「升级未完成：升级过程
        # 遇到问题」 although 0.30.22 was installed, and no restart.
        out = self.child(PHONE_CHILD, str(self.cli), "doctor_crash")
        self.assertEqual(self.cli_calls(), ["service install --deferred 9.9.9"])
        self.assertEqual((out["rec"]["result"], out["rec"]["reason"]), ("ok", "upgraded"))
        self.assertNotIn("升级未完成", out["text"])
        self.assertIn("9.9.9", out["text"])

    def test_restart_handoff_failure_is_reported_with_its_reason(self):
        out = self.child(PHONE_CHILD, str(self.cli), "ok", env={"FAKE_CLI_RC": "1"})
        self.assertEqual(out["rec"]["result"], "failed")
        self.assertEqual(out["rec"]["reason"], "service_restart_failed")
        self.assertIn("新版装好了但后台服务没能重启", out["text"])
        self.assertIn("9.9.9 已装好，仍在运行旧版本", out["text"])


class UpdateTexts(unittest.TestCase):
    def test_already_current(self):
        from agentj import phone_update
        self.assertTrue(phone_update.text({"reason": "already_current", "to": "0.30.23"}, "zh").startswith("已是最新版 0.30.23"))
        self.assertIn("0.30.23", phone_update.text({"reason": "already_current", "to": "0.30.23"}, "en"))

    def test_crash_names_its_type_only(self):
        from agentj import phone_update
        t = phone_update.text({"result": "failed", "reason": "apply_crashed", "error": "RuntimeError"}, "zh")
        self.assertIn("升级程序自身出错", t)
        self.assertIn("（RuntimeError）", t)
        t = phone_update.text({"result": "failed", "reason": "apply_crashed", "error": "x y/../secret"}, "zh")
        self.assertNotIn("secret", t)

    def test_recovery_accepts_new_live_process(self):
        st = Mock()
        st.root = Path("/tmp/unused-agentj-hotfix-test")
        with patch.object(service_recovery, "record") as rec, patch.object(service_recovery, "wait_ready", return_value="idle"), \
             patch.object(service_recovery, "stays_up"), patch.object(service_recovery, "process_version", return_value="new"), \
             patch.object(service_recovery, "cleanup_job"), patch.object(service_recovery, "read", return_value={}), \
             patch.object(update, "read_pending", return_value=None), patch.object(service_recovery, "restart_siblings"), \
             patch.object(update, "write_marker"), patch("agentj.__version__", "new"), patch.object(Path, "unlink"):
            rc = service_recovery.run(st, "new", install=lambda _: None, status=lambda: {"active": "active"})
        self.assertEqual(rc, 0)
        self.assertEqual(rec.call_args_list[-1].args[1], "ok")


if __name__ == "__main__":
    unittest.main()
