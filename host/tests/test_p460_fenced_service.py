"""#460 (0.26.8a2): `agentj update apply` run by the Codex main Agent inside its fence (second seat, instance 2) rolled back
with 「服务管理命令没能运行（OSError）」 and no errno. P591 pins the user unit folder read-only inside the fence and hides the
user bus, so the new CLI's `service install --deferred` could not even write its hand-off receipt (EROFS — a plain OSError)
and never reached systemd. Now:

- inside the fence `--deferred` asks the host outside the fence (elevate.sock upgrade op `service`): the host runs the same
  fixed `service install --deferred <to>` itself; `to` must be newer than the host (no argv crosses);
- a host older than 0.26.8a2 has no op `service`: its `health` op runs this version's doctor outside the fence, whose upgrade
  repair schedules the restart from the host's own pending record (update.resume_handoff) and says so in a fixed row;
- a failure line carries errno, the failed command / file, phase and time; `support: not_sent (exit_3 …)` says what exit 3 is.

The end-to-end classes run the real CLI as child processes (bubblewrap when available: unit folder read-only, state hidden,
agentperm/ put back — like P591's fence); systemd-run / busctl / systemctl are stubs on PATH. The real user manager is never
called.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import errno
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(HERE))
from agentj import __version__, cli, doctor, elevate, fence, service, service_recovery, update, upgrade_diag  # noqa: E402
from agentj.state import State  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402

NEW = "9.9.9"
# The installed (new) CLI as a child: its version is the first argument, set before any module reads it.
SHIM = "import sys, agentj; agentj.__version__ = sys.argv.pop(1); from agentj.cli import main; main()"
ROWS = [{"id": i, "status": "ok", "summary": i, "hint": ""} for i in ("version", "python", "platform", "state", "serve")]


def stubs(d: pathlib.Path, log: pathlib.Path, rc: int) -> pathlib.Path:
    d.mkdir(parents=True, exist_ok=True)
    for t in ("systemd-run", "busctl", "systemctl"):
        (d / t).write_text(f'#!/bin/sh\necho "{t} $*" >> "{log}"\nexit {rc}\n')
        (d / t).chmod(0o755)
    return d


def calls(log: pathlib.Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def _bwrap_ok() -> bool:
    if sys.platform != "linux" or not shutil.which("bwrap"):
        return False
    return subprocess.run(["bwrap", "--bind", "/", "/", "--dev", "/dev", "--", "true"], capture_output=True).returncode == 0


class HostOp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p460h-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        for p in (patch.object(update, "install_kind", return_value={"kind": "uv", "where": "/x", "legacy": False}),
                  patch.object(update, "new_argv", return_value=["agentj"])):
            p.start()
            self.addCleanup(p.stop)

    def test_fixed_argv_only_for_a_newer_target(self):
        seen = []

        def run(argv, **kw):
            seen.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "✓", "")
        for to in (__version__, "0.0.1", None, "x; rm -rf ~"):
            self.assertEqual(update.host_op(self.st, {"op": "service", "to": to}, {}, run)["ok"], False, to)
        self.assertEqual(seen, [])
        update.write_pending(self.st, __version__, NEW, None, None)
        res = update.host_op(self.st, {"op": "service", "to": NEW, "argv": ["sh"]}, {}, run)
        self.assertEqual(res, {"ok": True, "rc": 0, "tail": ""})
        self.assertEqual(seen, [["agentj", "service", "install", "--deferred", NEW]])

    def test_n4_refused_without_an_upgrade_in_progress_for_exactly_to(self):
        seen = []

        def run(argv, **kw):
            seen.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "✓", "")
        req = {"op": "service", "to": NEW}
        self.assertEqual(update.host_op(self.st, req, {}, run), {"ok": False, "why": "pending"}, "no pending record")
        update.write_pending(self.st, __version__, "9.9.8", None, None)
        self.assertEqual(update.host_op(self.st, req, {}, run)["why"], "pending", "a pending record for another target")
        update.write_pending(self.st, "0.0.1", NEW, None, None)
        self.assertEqual(update.host_op(self.st, req, {}, run)["why"], "pending", "recorded from another version")
        update.write_pending(self.st, __version__, NEW, None, None)
        rec = json.loads((self.st.root / update.PENDING).read_text())
        rec["at"] -= update.DAY + 60
        (self.st.root / update.PENDING).write_text(json.dumps(rec))
        self.assertEqual(update.host_op(self.st, req, {}, run)["why"], "pending", "a stale record")
        self.assertEqual(seen, [], "nothing ran")
        update.write_pending(self.st, __version__, NEW, None, None)
        self.assertEqual(update.host_op(self.st, req, {}, run), {"ok": True, "rc": 0, "tail": ""})
        self.assertEqual(len(seen), 1)

    def test_failure_tail_and_os_error(self):
        update.write_pending(self.st, __version__, NEW, None, None)
        res = update.host_op(self.st, {"op": "service", "to": NEW}, {},
                             lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "✗ systemd says no"))
        self.assertEqual((res["rc"], res["tail"]), (1, "✗ systemd says no"))

        def boom(argv, **kw):
            raise OSError(errno.EROFS, "Read-only file system", "/h/.config/systemd/user/x.tmp")
        res = update.host_op(self.st, {"op": "service", "to": NEW}, {}, boom)
        self.assertEqual(res["rc"], 127)
        for s in ("errno=EROFS(30)", "command=x.tmp", "phase=service", "at=20"):
            self.assertIn(s, res["tail"])


class Receipt(unittest.TestCase):
    def test_os_error_note_names_errno_command_phase_time(self):
        e = OSError(errno.EROFS, "Read-only file system")
        e.step = "write agentj-2.recovery.json"
        n = service.os_error_note(e)
        self.assertRegex(n, r"^OSError errno=EROFS\(30\) command=write agentj-2\.recovery\.json phase=service "
                            r"at=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}$")
        n = service.os_error_note(subprocess.TimeoutExpired(["/usr/bin/systemd-run", "--user", "--setenv=VALUE_X"], 15))
        self.assertIn("TimeoutExpired errno=- command=systemd-run phase=service", n)
        self.assertNotIn("VALUE_X", n)

    def test_schedule_names_the_receipt_write(self):
        with tempfile.TemporaryDirectory(prefix="p460r-", dir=TEST_TMP) as d, \
                patch.dict(os.environ, {"XDG_CONFIG_HOME": d, "AGENTJ_SERVICE_NAME": "agentj-2"}), \
                patch.object(service, "_write", side_effect=OSError(errno.EROFS, "Read-only file system")), \
                patch.object(service_recovery, "launch_job") as launch:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            with self.assertRaises(OSError) as cm:
                service_recovery.schedule(st, ["agentj"], NEW)
            self.assertEqual(cm.exception.step, "write agentj-2.recovery.json")
            launch.assert_not_called()

    def test_cli_failure_line(self):
        e = OSError(errno.EROFS, "Read-only file system")
        e.step = "write agentj-2.recovery.json"
        with patch.object(service_recovery, "schedule", side_effect=e), patch.object(fence, "inside", return_value=False), \
                self.assertRaises(SystemExit) as cm:
            cli.main(["service", "install", "--deferred", __version__])
        msg = str(cm.exception.code)
        for s in ("服务管理命令没能运行（OSError errno=EROFS(30)", "command=write agentj-2.recovery.json", "phase=service",
                  "at=", "could not run (OSError errno=EROFS(30)"):
            self.assertIn(s, msg)

    def test_support_exit_3_in_plain_words(self):
        with tempfile.TemporaryDirectory(prefix="p460s-", dir=TEST_TMP) as d:
            st = State(pathlib.Path(d) / "s")
            st.init(relay="ws://127.0.0.1:1")
            res = {"result": "failed", "reason": "service_restart_failed", "step": "service", "from": __version__, "to": NEW,
                   "rollback": "ok", "service": "failed"}
            on = patch.object(upgrade_diag, "enabled", return_value=True)
            on.start()
            self.addCleanup(on.stop)
            for lang, want in (("zh", "隐私检查这会儿用不了"), ("en", "the privacy check was unavailable")):
                out = upgrade_diag.after_failure(st, dict(res), lang=lang, send=lambda text: (3, ""))
                self.assertTrue(out["support"].startswith("not_sent (exit_3 — "), out["support"])
                self.assertIn("第 2 层隐私检查（Jev）", out["support"])
                self.assertIn("--owner-confirmed", out["support"])
                self.assertIn(want, out["tell_owner"])
                self.assertIn("support: not_sent (exit_3 — ", update.result_block(out))
            out = upgrade_diag.after_failure(st, dict(res), send=lambda text: (2, ""))
            self.assertEqual(out["support"], "not_sent (blocked)", "other reasons unchanged")


class Handoff(unittest.TestCase):
    """update.resume_handoff (doctor's upgrade repair) and the doctor row."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p460d-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s")
        self.st.init(relay="ws://127.0.0.1:1")
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "AGENTJ_SERVICE_NAME": "agentj-2"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(fence.FENCED_ENV, None)
        self.sched = patch.object(service_recovery, "schedule").start()
        self.addCleanup(patch.stopall)
        self.status = patch.object(service, "status", return_value={"installed": True, "active": "active"}).start()

    def test_only_a_lost_fenced_handoff(self):
        self.assertFalse(update.resume_handoff(self.st), "no pending record")
        update.write_pending(self.st, "0.26.7a1", "0.0.1", None, None)
        self.assertFalse(update.resume_handoff(self.st), "a pending record for another version")
        update.write_pending(self.st, "0.26.7a1", __version__, None, None)
        with patch.dict(os.environ, {fence.FENCED_ENV: "1"}):
            self.assertFalse(update.resume_handoff(self.st), "never inside the fence")
        self.status.return_value = {"installed": False}
        self.assertFalse(update.resume_handoff(self.st), "no service to restart")
        self.status.return_value = {"installed": True}
        self.sched.assert_not_called()
        self.assertTrue(update.resume_handoff(self.st))
        self.sched.assert_called_once_with(self.st, service.agentj_argv(), __version__)
        service_recovery.record(self.st, "pending", __version__)   # what schedule writes
        self.assertFalse(update.resume_handoff(self.st), "never twice")
        self.assertEqual(self.sched.call_count, 1)

    def test_doctor_row(self):
        update.write_pending(self.st, "0.26.7a1", __version__, None, None)
        out = io.StringIO()
        with patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(self.st.root)}), patch.object(doctor, "run", return_value=list(ROWS)), \
                patch.object(update, "repair_upgrade", wraps=update.repair_upgrade), contextlib.redirect_stdout(out):
            doctor.main(as_json=True)
        rows = json.loads(out.getvalue())["checks"]
        self.assertIn({"id": "upgrade-handoff", "status": "ok", "summary": "升级后的服务重启已安排 / the upgraded service's restart is "
                       "scheduled", "hint": ""}, rows)
        self.assertEqual(self.sched.call_count, 1)


class ViaHost(unittest.TestCase):
    def setUp(self):
        self.st = State(pathlib.Path(TEST_TMP) / "p460-view-unused")

    def answer(self, table):
        def host(st, op, timeout=60, **kw):
            self.ops.append(op)
            return table.get(op)
        self.ops = []
        return patch.object(update, "_host", side_effect=host)

    def test_new_host_op(self):
        out, err = io.StringIO(), io.StringIO()
        with self.answer({"service": {"ok": True, "rc": 0, "tail": ""}}), contextlib.redirect_stdout(out):
            self.assertEqual(service_recovery.via_host(self.st, NEW), 0)
        self.assertEqual(self.ops, ["service"])
        self.assertIn("the host (outside the Agent fence) scheduled", out.getvalue())
        with self.answer({"service": {"ok": True, "rc": 1, "tail": "✗ errno=EROFS(30)"}}), contextlib.redirect_stderr(err):
            self.assertEqual(service_recovery.via_host(self.st, NEW), 1)
        self.assertIn("errno=EROFS(30)", err.getvalue())

    def test_older_host_through_its_health_op(self):
        row = {"id": service_recovery.HANDOFF, "status": "ok", "summary": "", "hint": ""}
        with self.answer({"ping": {"ok": True, "version": "0.26.7a1"}, "health": {"ok": True, "checks": ROWS + [row]}}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(service_recovery.via_host(self.st, NEW), 0)
        self.assertEqual(self.ops, ["service", "ping", "health"])
        err = io.StringIO()
        with self.answer({"ping": {"ok": True}, "health": {"ok": True, "checks": ROWS}}), contextlib.redirect_stderr(err):
            self.assertEqual(service_recovery.via_host(self.st, NEW), 1)
        with self.answer({}), contextlib.redirect_stderr(err):
            self.assertEqual(service_recovery.via_host(self.st, NEW), 1)
        self.assertEqual(self.ops, ["service", "ping"], "no host: no health call")
        for s in ("phase=service", "at=", "/update"):
            self.assertIn(s, err.getvalue())


class _Seat2(unittest.IsolatedAsyncioTestCase):
    """Seat 2 on one OS user: the real host (serve's elevate.sock, outside the fence) and the fenced updater; every
    `agentj` child is this tree's CLI reporting version NEW."""
    OLD_HOST = False

    async def asyncSetUp(self):
        from test_l1 import _host
        self.tmp = tempfile.TemporaryDirectory(prefix="p460", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.home = root / "h"
        self.units = self.home / ".config" / "systemd" / "user"
        self.units.mkdir(parents=True)
        (self.units / "agentj-2.service").write_text("[Service]\n")
        (root / "t").mkdir()
        self.host_log, self.fence_log = root / "host-calls", root / "fence-calls"
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"), "AGENTJ_INSTANCE": "2",
                    "AGENTJ_TEST": "1", "AGENTJ_UPDATE_URL": "off", "PYTHONPATH": str(HOST), "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": str(root / "t"), "LANG": "C.UTF-8"}
        # The manager job's PATH is service_path.build (never the caller's): it starts with ~/.local/bin of this fake home.
        self.host_bin = stubs(self.home / ".local" / "bin", self.host_log, 0)
        self.fence_bin = stubs(root / "fb", self.fence_log, 1)
        self.host_path, self.fence_path = f"{self.host_bin}:/usr/bin:/bin", f"{self.fence_bin}:/usr/bin:/bin"
        from agentj import service_path
        for t in ("systemd-run", "busctl", "systemctl"):     # fail closed: never the real user manager
            self.assertEqual(shutil.which(t, path=service_path.build(self.env)), str(self.host_bin / t))
        envp = patch.dict(os.environ, {**self.env, "AGENTJ_STATE_DIR": str(self.home / ".local/state/agentj-2"),
                                       "AGENTJ_SERVICE_NAME": "agentj-2"})
        envp.start()
        self.addCleanup(envp.stop)
        os.environ.pop(fence.FENCED_ENV, None)
        self.host_st = State(self.home / ".local/state/agentj-2")
        self.host_st.init(relay="ws://127.0.0.1:1")
        self.view = State(root / "v")
        self.view.root.mkdir()
        self.host = _host(self.host_st, [])
        for p in (patch.object(update, "install_kind", return_value={"kind": "uv", "where": "/x", "legacy": False}),
                  patch.object(update, "new_argv", return_value=["agentj"]),
                  patch.object(update, "writable", return_value=True),
                  patch.object(update, "rollback_wheel", return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        real = update.host_op

        def host_op(st, req, mem, run=None):
            if self.OLD_HOST and req.get("op") == "service":     # 0.26.7a1 / 0.26.8a1: not an op they know
                return {"ok": False, "why": "op"}
            return real(st, req, mem, self.host_run)
        patch.object(update, "host_op", side_effect=host_op).start()
        self.addCleanup(patch.stopall)
        await self.host.elevate.start()
        self.addAsyncCleanup(self.host.elevate.stop)
        self.sock = os.environ[elevate.ENV]

    def child(self, argv, path, *, fenced=False, timeout=120):
        cmd = [sys.executable, "-c", SHIM, NEW] + list(argv[1:])
        env = {**self.env, "PATH": path}
        if fenced:
            env.update({fence.FENCED_ENV: "1", elevate.ENV: self.sock})
            if _bwrap_ok():   # P591's fence: unit folder read-only, state hidden, agentperm/ back
                st, perm = str(self.host_st.root), str(self.host_st.perm_dir)
                cmd = ["bwrap", "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--ro-bind", str(self.units),
                       str(self.units), "--ro-bind", str(self.fence_bin), str(self.host_bin), "--tmpfs", st, "--bind", perm,
                       perm, "--"] + cmd
            else:
                self.units.chmod(0o555)
                self.addCleanup(self.units.chmod, 0o755)
        return subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)

    def host_run(self, argv, **kw):
        if argv[-2:] == ["doctor", "--json"] and not self.OLD_HOST:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"checks": ROWS}), "")
        kw.pop("capture_output", None)
        return self.child(argv, self.host_path)

    def fence_run(self, argv, **kw):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, f"agentj {NEW}", "")
        if argv[-2:] == ["doctor", "--json"]:
            raise AssertionError("the fenced updater asked its own doctor")
        self.fenced_argv = list(argv)
        return self.child(argv, self.fence_path, fenced=True)

    async def apply(self):
        with patch.dict(os.environ, {fence.FENCED_ENV: "1"}):
            return await asyncio.to_thread(update.apply, self.view, NEW, plan_fn=lambda st, info, target: [],
                                           check_fn=lambda: {"status": "newer", "latest": NEW}, run=self.fence_run,
                                           svc_on=True, fenced=True)

    def assert_one_restart_job(self):
        self.assertEqual(calls(self.fence_log), [], "nothing inside the fence reached systemd-run / busctl / systemctl")
        jobs = [c for c in calls(self.host_log) if c.startswith("systemd-run ")]
        self.assertEqual(len(jobs), 1, calls(self.host_log))
        self.assertIn("--setenv=AGENTJ_SERVICE_NAME", jobs[0])
        self.assertTrue(jobs[0].endswith(f"service install --recovery-worker {NEW}"), jobs[0])
        rec = json.loads((self.units / "agentj-2.recovery.json").read_text())
        self.assertEqual((rec["status"], rec["target"]), ("pending", NEW))
        pend = json.loads((self.host_st.root / update.PENDING).read_text())
        self.assertEqual((pend["from"], pend["to"]), (__version__, NEW))


class CodexFencedSeat2(_Seat2):
    async def test_upgrade_completes_without_rollback(self):
        res = await self.apply()
        self.assertEqual(self.fenced_argv, ["agentj", "service", "install", "--deferred", NEW])
        self.assertEqual((res["result"], res["reason"], res["service"], res["health"], res["exit"]),
                         ("ok", "upgraded", "restart_scheduled", "host", 0), res)
        self.assertNotIn("rollback", res)
        self.assertNotIn("handoff", res)
        self.assert_one_restart_job()
        self.assertIn("message: 升级成功", update.result_block(res))


class OlderHostSeat2(_Seat2):
    """The updater and serve are 0.26.7a1 / 0.26.8a1 (no op `service`); only the installed CLI is new. Its doctor runs for
    real outside the fence through the host's `health` op."""
    OLD_HOST = True

    async def test_upgrade_completes_without_rollback(self):
        res = await self.apply()
        self.assertNotIn("rollback", res, res)
        self.assertEqual((res["service"], res["health"]), ("restart_scheduled", "host"), res)
        self.assert_one_restart_job()


if __name__ == "__main__":
    unittest.main()
