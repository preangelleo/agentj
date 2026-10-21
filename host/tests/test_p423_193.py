"""P423 #193 (uv tool install, Linux, Python 3.14.7, bubblewrap on; 0.24.1a2 → 0.25.0a1 rolled back: not_upgraded at verify).

Root cause: `uv tool install --force <wheel>` installs into uv's *current* tool directory (UV_TOOL_DIR, else
$XDG_DATA_HOME/uv/tools, else ~/.local/share/uv/tools). When the service / fence environment differs from the shell agentj
was first installed from, uv exits 0 having put a second copy elsewhere; the running venv (`agentj_argv`) still says the
old version → not_upgraded, and the rollback "succeeds" because the running venv was never touched.

- the uv install / rollback / recover command is pinned to the running venv's tools directory (UV_TOOL_DIR);
- not_upgraded carries a fixed sub-kind (old_version / other_version / no_version / exit_nonzero / not_found / not_runnable),
  whether this environment's uv tool dir is the running venv's (same / differs, never a path), and the redacted, bounded
  `--version` output — in UPGRADE_RESULT, last-upgrade.json and the P277 support report.
All I/O hermetic: fake installers, nothing is installed."""
import _hermetic  # noqa: F401,I001
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from agentj import __version__, auto_update, update, upgrade_diag
from agentj.state import State

NEW = "9.9.9"
IDENTITY = ("box-193", "owner193")


class Runner:
    """Fake installer: records (argv, kwargs); `--version` answers `version_out` (rc `version_rc`) or raises `version_exc`."""
    def __init__(self, version_out=None, version_rc=0, version_exc=None):
        self.calls = []
        self.version_out = f"agentj {__version__}\n" if version_out is None else version_out
        self.version_rc, self.version_exc = version_rc, version_exc

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if argv[-1] == "--version":
            if self.version_exc:
                raise self.version_exc
            return subprocess.CompletedProcess(argv, self.version_rc, self.version_out, "")
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")

    def installs(self):
        return [(a, kw) for a, kw in self.calls if "install" in a and "--force" in a]


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-p423-193-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.tools_a = root / "data-a" / "uv" / "tools"          # where agentj was installed (the running venv)
        self.tools_b = root / "data-b" / "uv" / "tools"          # what the service's uv would use by itself
        (self.tools_a / "agentj").mkdir(parents=True)
        (self.tools_a / "agentj" / "uv-receipt.toml").write_text("[tool]\nrequirements = [{ name = \"agentj\" }]\n")
        self.info = {"kind": "uv", "where": str(self.tools_a / "agentj"), "legacy": False}
        p = mock.patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"),
                                         "AGENTJ_STATE_DIR": str(root / "state"), "XDG_CACHE_HOME": str(root / ".cache"),
                                         "UV_TOOL_DIR": str(self.tools_b)})
        p.start()
        self.addCleanup(p.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        ident = mock.patch("agentj.privacy.local_identity", return_value=IDENTITY)
        ident.start()
        self.addCleanup(ident.stop)
        self.wheel = root / f"agentj-{NEW}-py3-none-any.whl"
        self.wheel.write_bytes(b"new")

    def plan(self, st, info, target):
        return auto_update.install_commands(info, self.wheel)

    def old_wheel(self):
        old = self.st.root / "upgrade-cache" / f"agentj-{__version__}-py3-none-any.whl"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"old")
        old.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(b"old").hexdigest()}))
        return old

    def apply(self, run, old=None, svc_on=False):
        with mock.patch.object(update, "install_kind", return_value=self.info), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]), mock.patch.object(update, "writable", return_value=True):
            return update.apply(self.st, NEW, plan_fn=self.plan, check_fn=lambda: {"status": "newer", "latest": NEW}, run=run,
                                svc_on=svc_on, rollback_fn=lambda st: old, fenced=False)


class PinnedToolDir(_Env):
    def test_uv_command_carries_the_running_venvs_tool_dir(self):
        cmd, = auto_update.install_commands(self.info, self.wheel)
        self.assertEqual(cmd[1:], ["tool", "install", "--force", str(self.wheel)], "the argv itself is unchanged")
        self.assertEqual(cmd.env, {"UV_TOOL_DIR": str(self.tools_a)})
        env = auto_update.run_env(cmd)
        self.assertEqual(env["UV_TOOL_DIR"], str(self.tools_a), "pinned over the service's own UV_TOOL_DIR")
        self.assertEqual(env["HOME"], os.environ["HOME"], "the rest of the environment is inherited")
        self.assertTrue(auto_update.command_line(cmd).startswith(f"UV_TOOL_DIR={self.tools_a} "))
        rec, = auto_update.recovery_commands(self.info, self.wheel)
        self.assertEqual((rec.env, rec[-2]), ({"UV_TOOL_DIR": str(self.tools_a)}, "--python"), "recovery keeps the pin")
        self.assertEqual(auto_update.install_template(self.info), "uv tool install --force <wheel>", "the reported template has no path")

    def test_pipx_and_pip_are_plain(self):
        for info in ({"kind": "pipx", "where": "/x/venvs/agentj", "legacy": False}, {"kind": "pip", "where": "/x/venv", "legacy": False}):
            cmd, = auto_update.install_commands(info, self.wheel)
            self.assertIsNone(auto_update.run_env(cmd))
            self.assertNotIn("UV_TOOL_DIR", auto_update.command_line(cmd))

    def test_nightly_runner_passes_the_env(self):
        cmd, = auto_update.install_commands(self.info, self.wheel)
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(cmd, 0, "", "")) as r:
            auto_update.quiet(cmd)
            auto_update.quiet(["agentj", "--version"])
        self.assertEqual(r.call_args_list[0].kwargs["env"]["UV_TOOL_DIR"], str(self.tools_a))
        self.assertNotIn("env", r.call_args_list[1].kwargs, "a plain argv inherits the environment as before")

    def test_apply_installs_into_the_running_tools_dir(self):
        run = Runner(version_out=f"agentj {NEW}\n")
        res = self.apply(run)
        self.assertEqual(res["reason"], "upgraded")
        (argv, kw), = run.installs()
        self.assertEqual(kw["env"]["UV_TOOL_DIR"], str(self.tools_a))
        ver = [kw for a, kw in run.calls if a[-1] == "--version"]
        self.assertTrue(ver and all("env" not in kw for kw in ver))

    def test_rollback_and_recover_are_pinned_too(self):
        old = self.old_wheel()
        run = Runner()                                       # --version keeps saying the old version
        res = self.apply(run, old=old)
        self.assertEqual((res["reason"], res["rollback"]), ("not_upgraded", "ok"))
        self.assertEqual([kw["env"]["UV_TOOL_DIR"] for _, kw in run.installs()], [str(self.tools_a)] * 2)
        rec = update.recover_of(self.info, old, "failed")["recover"]
        self.assertTrue(rec.startswith(f"UV_TOOL_DIR={self.tools_a} ") and rec.endswith(f"--force {old}"))

    def test_authorized_apply_is_pinned(self):
        from agentj import cloud
        cloud.write_cloud(self.st, {"api": "http://127.0.0.1:9", "host_id": "h_1", "tenant": {"slug": "acme-co", "name": "Acme"},
                                    "linked_at": 1, "last_seq": 0})
        run = Runner(version_exc=FileNotFoundError(2, "No such file"))
        with mock.patch.object(update, "install_kind", return_value=self.info), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]), mock.patch.object(update, "writable", return_value=True):
            res = update.authorized_apply(self.st, "AJUP-11111-11111-11111-11111", NEW, plan_fn=self.plan,
                                          check_fn=lambda: {"status": "newer", "latest": NEW}, auth_fn=lambda *a: {"status": "ok"},
                                          run=run, svc_on=False, rollback_fn=lambda st: None)
        self.assertEqual(run.installs()[0][1]["env"]["UV_TOOL_DIR"], str(self.tools_a))
        self.assertEqual((res["reason"], res["step"], res["verify"]), ("not_upgraded", "verify", "not_found"))
        self.assertEqual(res["detail"], "verify: not_found · uv tool dir: differs")
        self.assertNotIn("stderr_tail", res)
        self.assertEqual(upgrade_diag.read(self.st)["detail"], res["detail"])


class NotUpgradedDiagnostics(_Env):
    def test_old_version_block_record_and_report(self):
        res = self.apply(Runner(), old=self.old_wheel())
        self.assertEqual((res["result"], res["reason"], res["step"], res["verify"], res["tool_dir"]),
                         ("failed", "not_upgraded", "verify", "old_version", "differs"))
        self.assertEqual(res["stderr_tail"], f"agentj {__version__}")
        block = update.result_block(res)
        self.assertIn("detail: verify: old_version · uv tool dir: differs\n", block)
        self.assertIn(f"stderr: agentj {__version__}\n", block)
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["result"], rec["reason"], rec["step"]), ("rolled_back", "not_upgraded", "verify"))
        self.assertEqual((rec["detail"], rec["stderr_tail"]), (res["detail"], res["stderr_tail"]))
        body = upgrade_diag.body(res, "uv")
        self.assertIn("detail: verify: old_version · uv tool dir: differs", body)
        self.assertIn(f"agentj {__version__}", body)
        self.assertNotIn(self.tmp.name, body + block + json.dumps(rec), "no owner path leaves the host")

    def test_kinds(self):
        cp = subprocess.CompletedProcess
        home = os.environ["HOME"]
        crash = (f"Traceback (most recent call last):\n  File \"{home}/x/agentj/cli.py\", line 1\n"
                 + "".join(f"  frame {i}\n" for i in range(40)) + "ModuleNotFoundError: No module named 'audioop'\n")
        for v, kind in ((cp([], 1, "", crash), "exit_nonzero"), (cp([], 0, "agentj 9.9.8\n", ""), "other_version"),
                        (cp([], 0, "", ""), "no_version"), (cp([], 0, "hello\n", ""), "no_version"),
                        (FileNotFoundError(2, "x"), "not_found"), (PermissionError(13, "x"), "not_runnable"),
                        (cp([], 0, f"agentj {__version__}\n", ""), "old_version")):
            d = update.version_diag(v, NEW, __version__, self.info)
            self.assertEqual(d["verify"], kind, v)
            self.assertIn(kind, update.VERIFY_KINDS)
            self.assertTrue(d["detail"].startswith("verify: " + kind))
            self.assertLessEqual(len(d["detail"]), 120)
        d = update.version_diag(cp([], 1, "", crash), NEW, __version__, self.info)
        self.assertEqual(d["detail"], "verify: exit_nonzero (exit 1) · uv tool dir: differs")
        self.assertIn("No module named 'audioop'", d["stderr_tail"])
        self.assertLessEqual(len(d["stderr_tail"].splitlines()), upgrade_diag.TAIL_LINES)
        self.assertNotIn(home, d["stderr_tail"], "redacted: the home path is gone")
        self.assertNotIn("frame 0\n", d["stderr_tail"], "bounded: the last lines only")

    def test_tool_dir_same_and_non_uv(self):
        with mock.patch.dict(os.environ, {"UV_TOOL_DIR": str(self.tools_a)}):
            self.assertEqual(update.version_diag(subprocess.CompletedProcess([], 0, "", ""), NEW, None, self.info)["tool_dir"], "same")
        env = {"XDG_DATA_HOME": str(pathlib.Path(self.tmp.name) / "data-a")}
        self.assertEqual(update.uv_tool_dir(env), str(self.tools_a))
        self.assertEqual(update.uv_tool_dir({}), os.path.join(os.path.expanduser("~/.local/share"), "uv", "tools"))
        d = update.version_diag(subprocess.CompletedProcess([], 0, "", ""), NEW, None, {"kind": "pipx", "where": "/x"})
        self.assertNotIn("tool_dir", d)
        self.assertEqual(d["detail"], "verify: no_version")

    def test_nightly_worker_reports_the_kind_and_tail(self):
        old, new = self.st.root / "old.whl", self.st.root / "new.whl"
        for p in (old, new):
            p.write_bytes(b"w")
            p.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(b"w").hexdigest()}))
        job = {"attempt": "a", "from": __version__, "to": NEW, "install": self.info, "argv": ["agentj"], "old": str(old),
               "new": str(new), "phase": "installed", "when": "night"}
        auto_update.write(self.st, job, auto_update.JOB)
        seen = []

        def runner(argv):
            seen.append(argv)
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {__version__}", "")
            if "doctor" in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"checks": [{"id": "version", "status": "ok"}]}), "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        sent = []
        with mock.patch.object(upgrade_diag, "after_failure", side_effect=lambda st, res, **kw: sent.append(res) or res), \
                mock.patch("agentj.phone_update.release"), mock.patch("agentj.service_recovery.cleanup_job"):
            r = auto_update.run(self.st, runner=runner, restart=lambda v: None)
        self.assertEqual(r["result"], "rolled_back")
        self.assertTrue(r["recover"].startswith(f"UV_TOOL_DIR={self.tools_a} "))
        reinstall = [a for a in seen if "install" in a and str(old) in a]
        self.assertTrue(reinstall and all(getattr(a, "env", {}).get("UV_TOOL_DIR") == str(self.tools_a) for a in reinstall))
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["reason"], rec["step"], rec["detail"]),
                         ("not_upgraded", "verify", "verify: old_version · uv tool dir: differs"))
        self.assertEqual(sent[0]["stderr_tail"], f"agentj {__version__}")


if __name__ == "__main__":
    unittest.main()
