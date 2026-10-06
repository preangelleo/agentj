"""P63 (support st_Hd9fqLO1ocDrRM7mJo8bcw): `agentj update apply` when the service re-install fails, and macOS
`service install` / `restart` waiting for launchd's bootout before bootstrap. All I/O hermetic."""
import _hermetic  # noqa: F401,I001
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from agentj import service, update
from agentj.state import State


def _run_factory(fail_on=None):
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "agentj 9.9.9", "")
        rc = 5 if fail_on and argv[-1] == fail_on else 0
        return subprocess.CompletedProcess(argv, rc, "", "")
    return run, calls


class ApplyServiceFailed(unittest.TestCase):
    def apply(self, fail_on):
        run, calls = _run_factory(fail_on)
        with tempfile.TemporaryDirectory() as tmp:
            st = State(pathlib.Path(tmp) / "st")
            st.init()
            with mock.patch.object(update, "install_kind", return_value={"kind": "uv", "where": tmp, "legacy": False}), \
                    mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                    mock.patch.object(update, "writable", return_value=True):
                res = update.apply(st, "9.9.9", check_fn=lambda: {"status": "newer", "latest": "9.9.9"}, run=run,
                                   svc_on=True)
        return res, calls

    def test_service_install_or_restart_failure_is_a_formal_result(self):
        for verb in ("install", "restart"):
            res, calls = self.apply(verb)                      # 0.15.3a1: KeyError: 'service_failed', no block
            self.assertEqual((res["result"], res["reason"], res["service"], res["exit"]),
                             ("failed", "service_failed", "failed", 1), verb)
            block = update.result_block(res)
            self.assertTrue(block.startswith("UPGRADE_RESULT failed\nreason: service_failed\n"), block)
            self.assertIn("agentj service install", block)
            self.assertIn("next: agentj doctor; tell your human this block", block)
            self.assertNotIn("doctor", [c[-1] for c in calls], "no doctor after a failed service step")

    def test_every_reason_either_path_can_return_is_known(self):
        import inspect
        import re
        src = inspect.getsource(update)
        for r in set(re.findall(r'done\("([a-z_]+)"', src)) | set(re.findall(r'"reason": "([a-z_]+)"', src)):
            self.assertTrue(r in update.AUTH_REASONS or r in update.REASON_EXTRA, r)
            if r != "upgraded":
                self.assertTrue(update.result_block({"result": "failed", "reason": r, "from": "1", "to": "2",
                                                     "service": "x"}).startswith("UPGRADE_RESULT"))

    def test_an_unknown_reason_never_raises(self):
        self.assertEqual(update._exit_of("something_new"), 1)
        self.assertIn("reason: something_new", update.result_block(
            {"result": "failed", "reason": "something_new", "from": "1", "to": "2", "service": "x"}))

    def test_cli_prints_the_block_and_exits_1(self):
        import contextlib
        import io
        from agentj import cli
        res = {"from": "1", "to": "9.9.9", "service": "failed", "result": "failed", "reason": "service_failed", "exit": 1}
        out = io.StringIO()
        a = mock.Mock(mode="apply", authorization=None, from_email=None, version="9.9.9", json=False)
        with mock.patch.object(update, "check", return_value={"status": "newer", "latest": "9.9.9"}), \
                mock.patch.object(update, "apply", return_value=res), mock.patch.object(cli, "State"), \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as e:
            cli.cmd_update(a)
        self.assertEqual(e.exception.code, 1)
        self.assertIn("UPGRADE_RESULT failed", out.getvalue())


class Launchd:
    """A launchd stand-in: bootout unloads only after `lag` more `print` polls (macOS returns before it is done)."""
    def __init__(self, lag=3, bootstrap_rcs=(0,)):
        self.loaded, self.lag, self.calls, self.rcs = True, 0, [], list(bootstrap_rcs)
        self.lag0 = lag

    def __call__(self, *args, timeout=30):
        self.calls.append(args[0])
        verb = args[0]
        rc = 0
        if verb == "bootout":
            self.lag = self.lag0
        elif verb == "print":
            if self.lag:
                self.lag -= 1
                if not self.lag:
                    self.loaded = False
            rc = 0 if self.loaded else 113
        elif verb == "bootstrap":
            rc = 5 if self.loaded else (self.rcs.pop(0) if self.rcs else 0)
            if rc == 0:
                self.loaded = True
        elif verb == "kickstart":
            rc = 0 if self.loaded else 113
        return subprocess.CompletedProcess(["launchctl", *args], rc, "", "Bootstrap failed: 5" if rc == 5 else "")


class LaunchdBootstrap(unittest.TestCase):
    LABEL = "aj.p63.label"

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="aj-p63-")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        p = mock.patch.dict(os.environ, {"HOME": self.home, "AGENTJ_SERVICE_NAME": self.LABEL})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop(service.TOKEN_ENV, None)
        self.st = State(pathlib.Path(self.home) / "st")
        self.st.init(relay="ws://127.0.0.1:1")
        for target in (mock.patch.object(service, "platform", return_value="macos"),
                       mock.patch("time.sleep", lambda s: None)):
            target.start()
            self.addCleanup(target.stop)

    def test_install_waits_for_bootout_before_bootstrap(self):
        ld = Launchd(lag=3)
        with mock.patch.object(service, "_launchctl", ld):
            service.install(self.st)
        v = [c for c in ld.calls if c != "print-disabled"]
        self.assertEqual(v[0], "bootout")
        self.assertEqual(v[-1], "bootstrap")
        self.assertEqual(v.count("bootstrap"), 1, "bootstrap only once the label is gone (no error 5)")
        self.assertGreaterEqual(v.count("print"), 3)
        self.assertLess(v.index("bootout"), v.index("bootstrap"))
        self.assertTrue(ld.loaded)

    def test_wait_is_bounded_and_bootstrap_retried_once(self):
        ld = Launchd(lag=10 ** 6)                              # launchd never lets go
        with mock.patch.object(service, "_launchctl", ld), mock.patch.object(service, "BOOTOUT_WAIT", 0.0), \
                self.assertRaises(service.ServiceError) as e:
            service.install(self.st)
        self.assertEqual(e.exception.reason, "launchctl_failed")
        self.assertIn("5", e.exception.detail)
        self.assertEqual(ld.calls.count("bootstrap"), 2)

    def test_a_transient_5_is_retried(self):
        ld = Launchd(lag=1, bootstrap_rcs=(5, 0))
        with mock.patch.object(service, "_launchctl", ld):
            service.install(self.st)
        self.assertEqual(ld.calls.count("bootstrap"), 2)
        self.assertTrue(ld.loaded)

    def test_restart_reloads_an_unloaded_label(self):
        ld = Launchd(lag=1)
        with mock.patch.object(service, "_launchctl", ld):
            service.install(self.st)
            ld.loaded = False                                  # e.g. a failed bootstrap earlier
            ld.calls.clear()
            service.restart()
        self.assertEqual(ld.calls[0], "kickstart")
        self.assertIn("bootout", ld.calls)
        self.assertEqual(ld.calls[-1], "bootstrap")
        self.assertTrue(ld.loaded)

    def test_restart_of_a_loaded_label_is_just_kickstart(self):
        ld = Launchd(lag=1)
        with mock.patch.object(service, "_launchctl", ld):
            service.install(self.st)
            ld.calls.clear()
            service.restart()
        self.assertEqual(ld.calls, ["kickstart"])


if __name__ == "__main__":
    unittest.main()
