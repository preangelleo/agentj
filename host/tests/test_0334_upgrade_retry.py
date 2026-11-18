"""0334 (paid ticket: 0.30.25 → 0.30.32 `rolled_back / dependency_download_failed / install / ValueError`, nothing else):

a) the nightly / idle worker retries a dependency install that failed on the network (same argv, same verified wheel path),
   INSTALL_RETRIES times with INSTALL_BACKOFF, inside the one INSTALL_BUDGET a single attempt always had; any other installer
   failure is not retried, and the rollback is not retried;
b) the failure carries five closed fields — dependency_package, dependency_phase, dependency_source, retry_count,
   uv_failure_class — in last-upgrade.json, the full support body and the metadata-only body; never a URL, host, credential,
   path or raw stderr in those fields; the heartbeat keeps its five keys (the Dashboard rejects any other shape).
Fake HOME / XDG_* under the test temp dir, systemctl never reached (service.status stubbed), sleep and clock injected."""
import _hermetic  # noqa: F401,I001
import ast
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from agentj import __version__, auto_update, support, upgrade_diag
from agentj.state import State

AJ = pathlib.Path(__file__).resolve().parents[1]
INFO = {"kind": "uv", "where": "/fake/tools/agentj", "legacy": False}
NEW = "9.9.9"
FAKE_TOKEN = "sk" + "-" + "Zq8x" * 10                      # token-looking, built at run time (never a real key)
HOME_PATH = "/ho" "me/custuser/.local/share/uv/tools/agentj"
NET_ERR = ("Resolved 12 packages in 40ms\n"
           "error: Failed to install `agentj` dependencies\n"
           "  Caused by: Failed to download `websockets==15.0.1`\n"
           "  Caused by: Failed to fetch: `https://mirror.corp-example.internal/packages/websockets-15.0.1-py3-none-any.whl`\n"
           "  Caused by: Request failed after 3 retries\n"
           "  Caused by: error sending request for url (https://user:" + FAKE_TOKEN + "@mirror.corp-example.internal/simple/)\n"
           "  Caused by: client error (Connect)\n"
           "  Caused by: dns error: failed to lookup address information: Name or service not known\n"
           f"  cache {HOME_PATH}/cache token={FAKE_TOKEN}\n")
CONFLICT_ERR = ("  × No solution found when resolving dependencies:\n"
                "  ╰─▶ Because websockets>=14,<18 is unsatisfiable and agentj depends on it, we can conclude that the "
                "requirements are unsatisfiable.\n")


def _store_name(version):
    return f"agentj-{version}-py3-none-any.whl"


def _names(argv, version):
    """0334: the worker installs from the private per-version store copy, never the cache path."""
    return any(isinstance(a, str) and a.endswith("/install-artifacts/" + _store_name(version)) for a in argv)


def _sig(data):
    return f"agentj-host/v1 version={NEW} sha256={hashlib.sha256(data).hexdigest()}"


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aj-0334-")
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        env = {"HOME": str(root), "XDG_CONFIG_HOME": str(root / ".config"), "XDG_DATA_HOME": str(root / ".local/share"),
               "XDG_STATE_HOME": str(root / ".local/state"), "XDG_CACHE_HOME": str(root / ".cache"),
               "XDG_RUNTIME_DIR": str(root / "run"), "XDG_CONFIG_DIRS": str(root / "etc-xdg"),
               "AGENTJ_STATE_DIR": str(root / "state"), "AGENTJ_UPGRADE_REPORT": "off"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in [*upgrade_diag.INDEX_ENV, *upgrade_diag.PROXY_ENV, "UV_CONFIG_FILE", "UV_NO_CONFIG"]:
            os.environ.pop(k, None)
        self.root = root
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        for name, val in (("agentj.privacy.local_identity", ("TESTBOX", "custuser")),
                          ("agentj.service_recovery.service.status", {}),          # never the real systemctl --user
                          ("agentj.phone_update.release", None), ("agentj.service_recovery.cleanup_job", None)):
            m = mock.patch(name, return_value=val)
            m.start()
            self.addCleanup(m.stop)

    def attempt(self, outcomes, *, clock=None, runner_extra=None, on_sleep=None):
        """outcomes: what each install of the new wheel returns, in order (an int rc with NET_ERR / str stderr / an exception)."""
        old, new = self.st.root / "old.whl", self.st.root / "new.whl"
        for p, data in ((old, b"old"), (new, b"new")):
            p.write_bytes(data)
            p.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(data).hexdigest(),
                                                                    "signature": "sig"}))
        auto_update.write(self.st, {"attempt": "a", "from": __version__, "to": NEW, "install": INFO, "argv": ["agentj"],
                                    "old": str(old), "new": str(new), "phase": "prepared", "when": "night"}, auto_update.JOB)
        self.new, self.old = str(new), str(old)
        self.installs, self.rollbacks, self.sleeps, self.sent, self.consumed = [], [], [], [], []
        queue = list(outcomes)
        state = {"installed": __version__}

        def runner(argv):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {state['installed']}", "")
            if "doctor" in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"checks": [{"id": "version", "status": "ok"}]}), "")
            if _names(argv, NEW):
                self.installs.append(list(argv))
                whl = next(a for a in argv if isinstance(a, str) and a.endswith(_store_name(NEW)))
                self.consumed.append(pathlib.Path(whl).read_bytes())       # what the installer would really read
                out = queue.pop(0) if queue else 0
                if isinstance(out, BaseException):
                    raise out
                if out == 0:
                    state["installed"] = NEW
                    return subprocess.CompletedProcess(argv, 0, "Installed 1 executable: agentj\n", "")
                rc, err = out if isinstance(out, tuple) else (2, NET_ERR)
                return subprocess.CompletedProcess(argv, rc, "", err)
            if _names(argv, __version__):
                self.rollbacks.append(list(argv))
                state["installed"] = __version__
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 1, "", "unexpected")
        if runner_extra:
            runner = runner_extra(runner)
        t = [0.0]

        def sleep(s):
            self.sleeps.append(s)
            t[0] += s
            if on_sleep:
                on_sleep(self)
        with mock.patch.object(auto_update, "enabled", return_value=True), \
                mock.patch.object(auto_update.minisign, "verify", return_value=_sig(b"new")), \
                mock.patch.object(upgrade_diag, "after_failure", side_effect=lambda st, res, **kw: self.sent.append(res) or res):
            return auto_update.run(self.st, runner=runner, restart=lambda v: None, sleep=sleep,
                                   clock=clock or (lambda: t[0]))


class Retry(_Env):
    def test_network_failure_twice_then_success_upgrades_with_retry_count(self):
        r = self.attempt([1, 1, 0])
        self.assertEqual((r["result"], r["reason"], r["retry_count"]), ("ok", "upgraded", 2))
        self.assertEqual(len(self.installs), 3)
        self.assertEqual(self.sleeps, [5, 15])
        self.assertEqual(self.rollbacks, [])
        # the very same argv (same verified wheel path, same command) every time
        self.assertTrue(all(a == self.installs[0] for a in self.installs))
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["result"], rec["retry_count"]), ("ok", 2))
        self.assertNotIn("stderr_tail", rec)
        self.assertEqual(set(upgrade_diag.heartbeat_field(self.st)), {"from", "to", "result", "reason", "at"})

    def test_network_failure_every_time_rolls_back_with_fields(self):
        r = self.attempt([1, 1, 1, 1, 1])
        self.assertEqual((r["result"], r["reason"]), ("rolled_back", "dependency_download_failed"))
        self.assertEqual(len(self.installs), 1 + auto_update.INSTALL_RETRIES)
        self.assertEqual(self.sleeps, list(auto_update.INSTALL_BACKOFF))
        self.assertEqual(len(self.rollbacks), 1)                     # the rollback itself is never retried
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["result"], rec["reason"], rec["step"]), ("rolled_back", "dependency_download_failed", "install"))
        want = {"dependency_package": "websockets", "dependency_phase": "download", "dependency_source": "pypi",
                "retry_count": 3, "uv_failure_class": "dns"}
        self.assertEqual({k: rec[k] for k in want}, want)
        self.assertEqual({k: self.sent[0][k] for k in want}, want)
        self.assertEqual(set(upgrade_diag.heartbeat_field(self.st)), {"from", "to", "result", "reason", "at"})
        meta = upgrade_diag.metadata_body(self.sent[0], "uv")
        self.assertTrue(support.metadata_ok(meta))
        for k, v in want.items():
            self.assertIn(f"{k}: {v}", meta.split("\n"))
        self.assertIn("dependency_package: websockets", upgrade_diag.body(self.sent[0], "uv"))

    def test_non_network_failures_are_not_retried(self):
        for out, reason in (((1, CONFLICT_ERR), "dependency_conflict"),
                            ((1, "error: Failed to install: x\n  Caused by: Permission denied (os error 13)"), "permission_denied"),
                            ((1, "error: No interpreter found for Python >=3.11 in managed installations"), "python_missing"),
                            ((1, "error: The wheel is invalid: Missing .dist-info directory"), "uv_failed"),
                            (FileNotFoundError("/private/uv"), "installer_missing"),
                            (subprocess.TimeoutExpired(["uv"], 900, stderr=NET_ERR), "installer_timeout")):
            with self.subTest(reason=reason):
                r = self.attempt([out])
                self.assertEqual((r["result"], r["reason"]), ("rolled_back", reason))
                self.assertEqual((len(self.installs), self.sleeps), (1, []))
                self.assertEqual(upgrade_diag.read(self.st).get("retry_count"), 0)

    def test_conflict_fields(self):
        self.attempt([(1, CONFLICT_ERR)])
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["dependency_phase"], rec["uv_failure_class"]), ("resolve", "other"))

    def test_budget_cap_stops_retries(self):
        # each failed attempt takes 450 s on the injected clock: 450 + 5 + 60 ≤ 900 allows one retry, 905 + 15 + 60 does not
        t = [0.0]

        def slow(inner):
            def run(argv):
                if _names(argv, NEW):
                    t[0] += 450
                return inner(argv)
            return run
        r = self.attempt([1, 1, 1, 1], clock=lambda: t[0], runner_extra=slow)
        self.assertEqual(r["result"], "rolled_back")
        self.assertEqual(len(self.installs), 2)
        self.assertEqual(upgrade_diag.read(self.st)["retry_count"], 1)

    def test_retry_timeout_is_remaining_budget(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(kw.get("timeout"))
            return subprocess.CompletedProcess(argv, 1 if len(calls) == 1 else 0, "", NET_ERR)
        with mock.patch.object(auto_update.subprocess, "run", side_effect=fake_run):
            self.assertEqual(auto_update.quiet(["x"]).returncode, 1)
            self.assertEqual(auto_update.quiet(["x"], timeout=12).returncode, 0)
        self.assertEqual(calls, [900, 12])

    def test_worker_gives_a_retry_the_remaining_budget(self):
        # through the real quiet(): attempt 1 has the full INSTALL_TIMEOUT, the retry what is left after the backoff
        seen = []
        installed = [__version__]

        def fake_run(argv, **kw):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {installed[0]}", "")
            if "doctor" in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"checks": [{"id": "version", "status": "ok"}]}), "")
            seen.append(kw["timeout"])
            if len(seen) == 1:
                return subprocess.CompletedProcess(argv, 2, "", NET_ERR)
            installed[0] = NEW
            return subprocess.CompletedProcess(argv, 0, "", "")

        def wrap(inner):
            return auto_update.quiet
        with mock.patch.object(auto_update.subprocess, "run", side_effect=fake_run):
            r = self.attempt([], runner_extra=wrap)
        self.assertEqual((r["result"], r["retry_count"]), ("ok", 1))
        self.assertEqual(seen, [auto_update.INSTALL_TIMEOUT, auto_update.INSTALL_BUDGET - auto_update.ROLLBACK_RESERVE
                                - auto_update.INSTALL_BACKOFF[0]])

    def test_budget_never_exceeds_single_attempt_timeout(self):
        self.assertEqual(auto_update.INSTALL_BUDGET, auto_update.INSTALL_TIMEOUT)
        self.assertGreaterEqual(auto_update.INSTALL_RETRIES, 3)
        self.assertLess(sum(auto_update.INSTALL_BACKOFF) + auto_update.INSTALL_MIN_ATTEMPT, auto_update.INSTALL_BUDGET)


class ManualAndPhone(_Env):
    """The same rule on update.apply (manual CLI, phone /update, local wheel) — the rollback stays un-retried."""
    PLAN = [["uv", "tool", "install", "--force", "/verified/new.whl"]]

    def fake(self, outcomes):
        self.calls, self.timeouts = [], []
        queue = list(outcomes)
        state = {"v": __version__}

        def run(cmd, **kw):
            if cmd[-1] == "--version":
                return subprocess.CompletedProcess(cmd, 0, f"agentj {state['v']}", "")
            if "/verified/new.whl" in cmd:
                self.calls.append(list(cmd))
                self.timeouts.append(kw.get("timeout"))
                out = queue.pop(0) if queue else 0
                if isinstance(out, BaseException):
                    raise out
                if out == 0:
                    state["v"] = NEW
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                rc, err = out if isinstance(out, tuple) else (2, NET_ERR)
                return subprocess.CompletedProcess(cmd, rc, "", err)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return run

    def apply(self, outcomes, *, clock=None):
        self.sleeps = []
        t = [0.0]

        def sleep(s):
            self.sleeps.append(s)
            t[0] += s
        from agentj import update
        run = self.fake(outcomes)
        with mock.patch.object(update, "install_kind", return_value=INFO), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                mock.patch.object(update, "writable", return_value=True), \
                mock.patch.object(update, "_install_sleep", sleep), \
                mock.patch.object(update, "_install_clock", clock or (lambda: t[0])):
            return update.apply(self.st, NEW, fenced=False, svc_on=False, run=run,
                                check_fn=lambda: {"status": "newer", "latest": NEW},
                                plan_fn=lambda st, info, target: [list(c) for c in self.PLAN], rollback_fn=lambda st: None)

    def test_manual_network_twice_then_success(self):
        r = self.apply([1, 1, 0])
        self.assertEqual((r["result"], r["reason"], r["retry_count"]), ("ok", "upgraded", 2))
        self.assertEqual(self.sleeps, [5, 15])
        self.assertTrue(all(c == self.PLAN[0] for c in self.calls))
        self.assertEqual(self.timeouts[0], None)                 # the first attempt runs exactly as before
        self.assertTrue(all(isinstance(x, int) and 0 < x <= auto_update.INSTALL_BUDGET for x in self.timeouts[1:]))
        self.assertEqual(upgrade_diag.read(self.st)["retry_count"], 2)
        from agentj import update
        self.assertIn("retry_count: 2", update.result_block(r).split("\n"))

    def test_manual_network_always_keeps_uv_failed_with_fields(self):
        r = self.apply([1, 1, 1, 1, 1])
        self.assertEqual((r["result"], r["reason"]), ("failed", "uv_failed"))
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.sleeps, list(auto_update.INSTALL_BACKOFF))
        want = {"dependency_package": "websockets", "dependency_phase": "download", "dependency_source": "pypi",
                "retry_count": 3, "uv_failure_class": "dns"}
        self.assertEqual({k: r[k] for k in want}, want)
        rec = upgrade_diag.read(self.st)
        self.assertEqual({k: rec[k] for k in want}, want)
        self.assertEqual(set(upgrade_diag.heartbeat_field(self.st)), {"from", "to", "result", "reason", "at"})
        from agentj import update
        block = update.result_block(r)
        self.assertIn("uv_failure_class: dns", block)
        meta = upgrade_diag.metadata_body(r, "uv")
        self.assertTrue(support.metadata_ok(meta))
        for bad in ("corp-example", FAKE_TOKEN, "custuser", "://"):
            self.assertNotIn(bad, json.dumps(upgrade_diag.dependency_fields(r)) + meta)

    def test_manual_non_network_not_retried(self):
        for out in ((1, CONFLICT_ERR), (1, "error: Failed to install: x\n  Caused by: Permission denied (os error 13)"),
                    FileNotFoundError("/private/uv")):
            with self.subTest(out=str(out)[:30]):
                r = self.apply([out])
                self.assertEqual((r["result"], len(self.calls), self.sleeps), ("failed", 1, []))
                self.assertEqual(r.get("retry_count"), 0)

    def test_manual_budget_cap_and_retry_timeout(self):
        t = [0.0]

        def clock():
            return t[0]
        from agentj import update
        inner = self.fake([1, 1, 1, 1])

        def slow(cmd, **kw):
            if "/verified/new.whl" in cmd:
                t[0] += 450
            return inner(cmd, **kw)
        self.sleeps = []
        with mock.patch.object(update, "install_kind", return_value=INFO), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                mock.patch.object(update, "writable", return_value=True), \
                mock.patch.object(update, "_install_sleep", lambda s: self.sleeps.append(s)), \
                mock.patch.object(update, "_install_clock", clock):
            r = update.apply(self.st, NEW, fenced=False, svc_on=False, run=slow,
                             check_fn=lambda: {"status": "newer", "latest": NEW},
                             plan_fn=lambda st, info, target: [list(c) for c in self.PLAN], rollback_fn=lambda st: None)
        self.assertEqual((r["reason"], len(self.calls), r["retry_count"]), ("uv_failed", 2, 1))
        self.assertEqual(self.timeouts[1], auto_update.INSTALL_BUDGET - auto_update.ROLLBACK_RESERVE - 450)

    def test_retry_timeout_expiry_is_a_failure_not_a_crash(self):
        r = self.apply([1, subprocess.TimeoutExpired(["uv"], 5, stderr=NET_ERR)])
        self.assertEqual((r["result"], r["reason"]), ("failed", "uv_failed"))
        self.assertEqual((len(self.calls), r["retry_count"]), (2, 1))

    def test_phone_job_retries_inside_its_lease(self):
        from agentj import phone_update, update
        queue = [2, 2, 0]
        seen = []
        state = {"v": __version__}

        def fake_run(argv, **kw):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {state['v']}", "")
            if "/verified/new.whl" not in argv:
                return subprocess.CompletedProcess(argv, 0, "", "")
            seen.append(kw["timeout"])
            rc = queue.pop(0)
            if not rc:
                state["v"] = NEW
            return subprocess.CompletedProcess(argv, rc, "", NET_ERR if rc else "")
        t = [0.0]
        with mock.patch.object(phone_update.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(update, "check", return_value={"status": "newer", "latest": NEW}), \
                mock.patch.object(update, "install_kind", return_value=INFO), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                mock.patch.object(update, "writable", return_value=True), \
                mock.patch.object(update, "verified_plan", return_value=[list(self.PLAN[0])]), \
                mock.patch.object(update, "_pin_plan_wheel", side_effect=lambda c, t, st_: (c, None)), \
                mock.patch.object(update, "rollback_wheel", return_value=None), \
                mock.patch.object(update, "_install_sleep", lambda s: t.__setitem__(0, t[0] + s)), \
                mock.patch.object(update, "_install_clock", lambda: t[0]), \
                mock.patch("agentj.doctor.run", return_value=[]):
            phone_update.run(self.st, restart_fn=lambda: None)
        rec = json.loads((self.st.root / phone_update.RESULT).read_text())
        self.assertEqual((rec["result"], rec["reason"], rec["retry_count"]), ("ok", "upgraded", 2))
        self.assertEqual(seen[0], 900)
        self.assertTrue(all(x <= 900 for x in seen))

    def test_fenced_host_record_keeps_only_valid_fields(self):
        from agentj import update
        rec = {"v": 1, "from": __version__, "to": NEW, "result": "failed", "reason": "uv_failed", "at": 1, "step": "install",
               "dependency_package": "websockets", "uv_failure_class": "dns", "retry_count": 3,
               "dependency_source": "https://mirror.corp-example.internal/simple"}
        res = update.host_op(self.st, {"op": "record", "rec": rec}, {})
        self.assertTrue(res.get("ok"), res)
        got = upgrade_diag.read(self.st)
        self.assertEqual((got["dependency_package"], got["uv_failure_class"], got["retry_count"]), ("websockets", "dns", 3))
        self.assertNotIn("dependency_source", got)


def _pinned_new(env):
    return pathlib.Path(next(a for a in env.installs[-1] if isinstance(a, str) and a.endswith(_store_name(NEW))))


class ArtifactPinning(_Env):
    """0334 security r1 (F3/F4): a retry never installs bytes other than the verified ones."""

    def test_installer_never_reads_the_cache_path(self):
        self.attempt([0])
        self.assertNotIn(self.new, self.installs[0])
        self.assertEqual(self.consumed, [b"new"])
        # the stable per-version store copy stays (uv / pipx receipts record this path)
        p = _pinned_new(self)
        self.assertEqual(p, self.st.root / "install-artifacts" / _store_name(NEW))
        self.assertTrue(p.is_file())
        self.assertEqual(p.stat().st_mode & 0o777, 0o400)
        self.assertEqual(p.parent.stat().st_mode & 0o777, 0o700)

    def test_rollback_installs_the_previous_versions_store_copy(self):
        r = self.attempt([(1, CONFLICT_ERR)])
        self.assertEqual(r["result"], "rolled_back")
        store = self.st.root / "install-artifacts"
        self.assertIn(str(store / _store_name(__version__)), self.rollbacks[0])
        self.assertTrue((store / _store_name(__version__)).is_file())

    def test_prune_keeps_current_and_previous_only_after_success(self):
        store = self.st.root / "install-artifacts"
        store.mkdir(mode=0o700)
        for v in ("0.0.1", __version__):
            (store / _store_name(v)).write_bytes(b"x")
        self.attempt([(1, CONFLICT_ERR)])                              # a failure prunes nothing
        self.assertTrue((store / _store_name("0.0.1")).exists())
        self.attempt([0])
        names = sorted(p.name for p in store.iterdir())
        self.assertEqual(names, sorted(n for v in (NEW, __version__) for n in (_store_name(v), _store_name(v) + ".json")))

    def test_store_path_symlink_is_replaced_not_followed_and_store_symlink_refused(self):
        outside = self.root / "outside.whl"
        outside.write_bytes(b"keep me")
        store = self.st.root / "install-artifacts"
        store.mkdir(mode=0o700)
        (store / _store_name(NEW)).symlink_to(outside)
        p = auto_update.pin_artifact(b"verified", NEW, self.st.root).path
        self.assertFalse(p.is_symlink())
        self.assertEqual((p.read_bytes(), outside.read_bytes()), (b"verified", b"keep me"))
        other = self.root / "other-state"
        other.mkdir(mode=0o700)
        (other / "install-artifacts").symlink_to(self.root)
        with self.assertRaises(ValueError):
            auto_update.pin_artifact(b"verified", NEW, other)
        with self.assertRaises(ValueError):
            auto_update.pin_artifact(b"verified", "../../x", self.st.root)

    def test_cache_wheel_replaced_during_backoff_installs_verified_bytes(self):
        def swap(env):
            pathlib.Path(env.new).write_bytes(b"unverified-B")
        r = self.attempt([1, 0], on_sleep=swap)
        self.assertEqual((r["result"], r["retry_count"]), ("ok", 1))
        self.assertEqual(self.consumed, [b"new", b"new"])

    def test_pinned_copy_replaced_during_backoff_stops_without_retry(self):
        def swap(env):
            p = _pinned_new(env)
            p.chmod(0o600)
            p.write_bytes(b"unverified-B")
        r = self.attempt([1, 0, 0], on_sleep=swap)
        self.assertEqual((r["result"], r["reason"]), ("rolled_back", "install_artifact_changed"))
        self.assertEqual(self.consumed, [b"new"])
        self.assertEqual(len(self.rollbacks), 1)
        self.assertEqual(upgrade_diag.read(self.st)["reason"], "install_artifact_changed")
        self.assertTrue(support.metadata_ok(upgrade_diag.metadata_body(self.sent[0])))

    def test_pinned_copy_symlinked_during_backoff_stops_without_retry(self):
        evil = self.root / "evil.whl"
        evil.write_bytes(b"new")                           # even identical bytes behind a symlink are refused

        def swap(env):
            p = _pinned_new(env)
            p.unlink()
            p.symlink_to(evil)
        r = self.attempt([1, 0], on_sleep=swap)
        self.assertEqual((r["result"], r["reason"]), ("rolled_back", "install_artifact_changed"))
        self.assertEqual(len(self.installs), 1)

    # manual / phone path (update.apply): a real wheel + the verifier's receipt beside it
    def manual(self, outcomes, *, on_sleep=None, receipt_sha=None, link=False, swap=None, fenced=False, write_fail=None,
               clock=None, loose=False, extra_patches=()):
        """The published path (plan_fn None → verified_plan, patched to return the plan for a signed cache wheel A);
        `swap(whl, receipt)` runs between verified_plan and install_step."""
        from agentj import update
        cache = self.root / "cache"
        (cache / "upgrade-cache").mkdir(parents=True, exist_ok=True)
        cache.chmod(0o755 if loose else 0o700)
        (cache / "upgrade-cache").chmod(0o755 if loose else 0o700)
        whl = cache / "upgrade-cache" / _store_name(NEW)
        receipt = whl.with_suffix(".verified.json")
        for f, data in ((whl, b"verified-A"), (receipt, json.dumps(
                {"sha256": receipt_sha or hashlib.sha256(b"verified-A").hexdigest(), "signed": True,
                 "signature": "sig"}).encode())):
            f.unlink(missing_ok=True)
            f.write_bytes(data)
            f.chmod(0o600)
        if link:
            real = cache / "upgrade-cache" / "real.whl"
            whl.rename(real)
            whl.symlink_to(real)
        self.consumed, self.sleeps = [], []
        queue = list(outcomes)
        state = {"v": __version__}

        def run(cmd, **kw):
            if cmd[-1] == "--version":
                return subprocess.CompletedProcess(cmd, 0, f"agentj {state['v']}", "")
            path = next((a for a in cmd if isinstance(a, str) and a.endswith(".whl")), None)
            if path:
                self.consumed.append((path, pathlib.Path(path).read_bytes()))
                rc = queue.pop(0) if queue else 0
                if not rc:
                    state["v"] = NEW
                return subprocess.CompletedProcess(cmd, rc, "", NET_ERR if rc else "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def sleep(s):
            self.sleeps.append(s)
            if on_sleep:
                on_sleep(whl, self)

        def plan(st, info, target):
            cmds = [["uv", "tool", "install", "--force", str(whl)]]
            if swap:
                swap(whl, receipt)
            return cmds
        sig = lambda data, signature, keys: _sig(b"verified-A") if data == b"verified-A" else "agentj-host/v1 bad"
        extra = [mock.patch.object(auto_update, "_write_at", side_effect=write_fail)] if write_fail else []
        extra += list(extra_patches)
        import contextlib as _cl
        with _cl.ExitStack() as es:
            for m in [mock.patch.object(update, "install_kind", return_value=INFO),
                      mock.patch.object(update, "new_argv", return_value=["agentj"]),
                      mock.patch.object(update, "writable", return_value=True),
                      mock.patch.object(update, "verified_plan", side_effect=plan),
                      mock.patch.object(update, "host_channel", return_value=None),
                      mock.patch.object(auto_update.minisign, "verify", side_effect=sig),
                      mock.patch.object(update, "_install_sleep", sleep),
                      mock.patch.object(update, "_install_clock", clock or (lambda: 0.0)), *extra]:
                es.enter_context(m)
            return update.apply(self.st, NEW, fenced=fenced, svc_on=False, run=run,
                                check_fn=lambda: {"status": "newer", "latest": NEW},
                                rollback_fn=lambda st: None), whl

    def test_manual_swaps_between_verified_plan_and_install_install_nothing(self):
        """r2 F1 (Claude a/b/c, Codex 'temporarily absent'): the pin trusts only the verifier's own bytes."""
        b_sha = hashlib.sha256(b"unverified-B").hexdigest()

        def a(whl, receipt):
            receipt.unlink()

        def b(whl, receipt):
            whl.write_bytes(b"unverified-B")
            receipt.write_text(json.dumps({"sha256": b_sha, "signed": True}))

        def c(whl, receipt):
            whl.write_bytes(b"unverified-B")
            receipt.write_text(json.dumps({"sha256": b_sha, "signed": False}))

        def absent(whl, receipt):
            whl.rename(whl.with_name("away"))
            self.restore = lambda: whl.with_name("away").rename(whl)
        for swap in (a, b, c, absent):
            with self.subTest(case=swap.__name__):
                r, whl = self.manual([0], swap=swap)
                if swap is absent:
                    self.restore()                       # restored before any installer could run: still nothing
                self.assertEqual((r["result"], r["reason"]), ("failed", "install_artifact_changed"))
                self.assertEqual(self.consumed, [])
                self.assertNotIn("rollback", r)          # nothing ran, so nothing to roll back
                self.assertFalse((self.st.root / "install-artifacts" / _store_name(NEW)).exists())

    def test_manual_published_plan_installs_the_verified_bytes_from_the_store(self):
        r, whl = self.manual([0])
        self.assertEqual(r["result"], "ok")
        store = self.st.root / "install-artifacts" / _store_name(NEW)
        self.assertEqual(self.consumed, [(str(store), b"verified-A")])
        side = json.loads((store.parent / (store.name + ".json")).read_text())
        self.assertEqual(side, {"sha256": hashlib.sha256(b"verified-A").hexdigest(), "signed": True})

    def test_fenced_apply_creates_no_store_and_keeps_the_single_attempt(self):
        """r2 F2: inside the fence nothing is pinned (an Agent-writable store would be worse than none)."""
        from agentj import update
        r, whl = self.manual([1, 0], fenced=True)
        self.assertEqual(len(self.consumed), 1)                  # no retry
        self.assertEqual(self.consumed[0][0], str(whl))          # the plan's own argv, as before 0334
        self.assertEqual(self.sleeps, [])
        self.assertFalse((self.st.root / "install-artifacts").exists())
        self.assertFalse((self.root / "cache" / "install-artifacts").exists())
        self.assertIsNone(update.artifact_store(self.st, fenced=True))

    def test_store_write_failure_is_a_fixed_reason_not_a_traceback(self):
        import errno as _errno
        for code in (_errno.ENOSPC, _errno.EDQUOT, _errno.EFBIG, _errno.EACCES):
            with self.subTest(errno=code):
                r, _ = self.manual([0], write_fail=OSError(code, "x"))
                self.assertEqual((r["result"], r["reason"]), ("failed", "install_artifact_unavailable"))
                self.assertEqual(self.consumed, [])

    def test_manual_cache_swap_during_backoff_still_installs_verified_bytes(self):
        r, whl = self.manual([1, 0], on_sleep=lambda w, env: w.write_bytes(b"unverified-B"))
        self.assertEqual((r["result"], r["retry_count"]), ("ok", 1))
        self.assertEqual([b for _, b in self.consumed], [b"verified-A", b"verified-A"])
        self.assertTrue(all(p != str(whl) for p, _ in self.consumed))
        store = self.st.root / "install-artifacts" / _store_name(NEW)
        self.assertTrue(all(p == str(store) for p, _ in self.consumed))
        self.assertTrue(store.is_file())                     # stays: the uv / pipx receipt records this path

    def test_manual_pinned_swap_or_symlink_during_backoff_fails_distinctly(self):
        def swap(w, env):
            p = pathlib.Path(env.consumed[-1][0])
            p.chmod(0o600)
            p.write_bytes(b"unverified-B")

        def link(w, env):
            p = pathlib.Path(env.consumed[-1][0])
            p.unlink()
            p.symlink_to(w)
        for hook in (swap, link):
            with self.subTest(hook=hook.__name__):
                r, _ = self.manual([1, 0], on_sleep=hook)
                self.assertEqual((r["result"], r["reason"]), ("failed", "install_artifact_changed"))
                self.assertEqual(len(self.consumed), 1)
                self.assertEqual(r["retry_count"], 1)

    def test_manual_receipt_mismatch_or_symlinked_source_installs_nothing(self):
        for kw in ({"receipt_sha": "0" * 64}, {"link": True}):
            with self.subTest(kw=list(kw)):
                r, _ = self.manual([0], **kw)
                self.assertEqual((r["result"], r["reason"]), ("failed", "install_artifact_changed"))
                self.assertEqual(self.consumed, [])


class StableStore(_Env):
    """0334: the manual rollback and the repair reinstall use the stable store too; a real uv records that path."""

    def test_manual_rollback_uses_the_previous_versions_store_copy(self):
        from agentj import update
        cache = self.st.root / "upgrade-cache"
        cache.mkdir(exist_ok=True)
        old = cache / _store_name(__version__)
        old.write_bytes(b"old-bytes")
        old.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(b"old-bytes").hexdigest(),
                                                                 "signed": False}))
        seen = []

        def run(cmd, **kw):
            seen.append(list(cmd))
            if cmd[-1] == "--version":
                return subprocess.CompletedProcess(cmd, 0, f"agentj {__version__}", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.assertTrue(update.roll_back(self.st, INFO, old, __version__, run))
        store = self.st.root / "install-artifacts" / _store_name(__version__)
        self.assertIn(str(store), seen[0])
        self.assertTrue(store.is_file())
        self.assertNotIn(str(old), seen[0])

    @unittest.skipUnless(__import__("shutil").which("uv"), "no uv on PATH")
    def test_real_uv_receipt_records_the_store_path_and_it_survives(self):
        import shutil
        import sys
        import zipfile
        import base64
        files = {"tinyaj/__init__.py": b"def main():\n    print('tiny')\n",
                 "tinyaj-0.0.1.dist-info/METADATA": b"Metadata-Version: 2.1\nName: tinyaj\nVersion: 0.0.1\n",
                 "tinyaj-0.0.1.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: t\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                 "tinyaj-0.0.1.dist-info/entry_points.txt": b"[console_scripts]\ntinyaj = tinyaj:main\n"}
        src = self.root / "tinyaj-0.0.1-py3-none-any.whl"
        rec = []
        with zipfile.ZipFile(src, "w") as z:
            for n, d in files.items():
                z.writestr(n, d)
                rec.append(f"{n},sha256={base64.urlsafe_b64encode(hashlib.sha256(d).digest()).decode().rstrip('=')},{len(d)}")
            z.writestr("tinyaj-0.0.1.dist-info/RECORD", "\n".join(rec + ["tinyaj-0.0.1.dist-info/RECORD,,"]) + "\n")
        data = src.read_bytes()
        # the store names files agentj-<v>-…; for this probe the tiny wheel is pinned under its own standard name
        fd = auto_update._store_fd(self.st.root)
        os.close(fd)
        pinned = self.st.root / "install-artifacts" / src.name
        pinned.write_bytes(data)
        pinned.chmod(0o400)
        h = self.root
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(h), "XDG_DATA_HOME": str(h / ".local/share"),
               "XDG_CONFIG_HOME": str(h / ".config"), "XDG_CACHE_HOME": str(h / ".cache"), "XDG_BIN_HOME": str(h / "bin"),
               "UV_NO_CONFIG": "1", "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "UV_TOOL_DIR": str(h / "tools")}
        uv = shutil.which("uv")
        py = os.path.realpath(getattr(sys, "_base_executable", sys.executable))
        r = subprocess.run([uv, "tool", "install", "--force", str(pinned), "--python", py], env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        receipt = (h / "tools" / "tinyaj" / "uv-receipt.toml").read_text()
        self.assertIn(f'path = "{pinned}"', receipt)
        up = subprocess.run([uv, "tool", "upgrade", "tinyaj"], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(up.returncode, 0, up.stderr[-400:])                 # the recorded path still resolves


class Round2(_Env):
    """0334 security r2: store hygiene (F3), deadlines with a rollback reserve (F4), store-first rollback (F5), fixed
    reason for write errors and tmp pruning (F2 Claude), ASCII-only retry_count (F4 Claude)."""

    def test_reuse_requires_0400_regular_own_file_else_replaced(self):
        store = self.st.root / "install-artifacts"
        store.mkdir(mode=0o700)
        f = store / _store_name(NEW)
        f.write_bytes(b"verified")
        f.chmod(0o644)                                  # identical bytes but writable: not reused as is
        pin = auto_update.pin_artifact(b"verified", NEW, self.st.root)
        self.assertEqual(pin.path.stat().st_mode & 0o777, 0o400)
        auto_update.check_artifact(pin)
        pin.path.chmod(0o600)
        with self.assertRaises(ValueError):
            auto_update.check_artifact(pin)              # check refuses anything but 0400 too

    def test_state_dir_symlink_or_open_state_dir_refused(self):
        link = self.root / "state-link"
        link.symlink_to(self.st.root)
        with self.assertRaises(ValueError):
            auto_update.pin_artifact(b"x", NEW, link)
        loose = self.root / "loose"
        loose.mkdir(mode=0o755)
        loose.chmod(0o755)
        with self.assertRaises(ValueError):
            auto_update.pin_artifact(b"x", NEW, loose)

    def test_worker_retry_keeps_the_rollback_reserve(self):
        t = [0.0]

        def slow(inner):
            def run(argv):
                if _names(argv, NEW):
                    t[0] += 560          # 560 + 5 + 60 > 900 − 300: no retry, the rollback keeps its window
                return inner(argv)
            return run
        r = self.attempt([1, 0], clock=lambda: t[0], runner_extra=slow)
        self.assertEqual((r["result"], len(self.installs), self.sleeps), ("rolled_back", 1, []))

    def test_retry_limit_respects_the_lease(self):
        self.assertEqual(auto_update.retry_limit(100.0), 100.0 + auto_update.INSTALL_BUDGET - auto_update.ROLLBACK_RESERVE)
        self.assertEqual(auto_update.retry_limit(100.0, lease_end=1000.0), 1000.0 - auto_update.INSTALL_TIMEOUT)

    def test_phone_lease_nearly_used_blocks_retry(self):
        from agentj import phone_update, update
        import time as _t
        for left, want in ((auto_update.INSTALL_TIMEOUT + 30, 1), (phone_update.LEASE, 2)):
            with self.subTest(left=left):
                (self.st.root / phone_update.LOCK).write_text(json.dumps({"at": _t.time() - phone_update.LEASE + left}))
                calls = []
                state = {"v": __version__}

                def fake_run(argv, **kw):
                    if argv[-1] == "--version":
                        return subprocess.CompletedProcess(argv, 0, f"agentj {state['v']}", "")
                    if "/verified/new.whl" not in argv:
                        return subprocess.CompletedProcess(argv, 0, "", "")
                    calls.append(kw["timeout"])
                    if len(calls) == 1:
                        return subprocess.CompletedProcess(argv, 2, "", NET_ERR)
                    state["v"] = NEW
                    return subprocess.CompletedProcess(argv, 0, "", "")
                with mock.patch.object(phone_update.subprocess, "run", side_effect=fake_run), \
                        mock.patch.object(update, "check", return_value={"status": "newer", "latest": NEW}), \
                        mock.patch.object(update, "install_kind", return_value=INFO), \
                        mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                        mock.patch.object(update, "writable", return_value=True), \
                        mock.patch.object(update, "verified_plan", return_value=[["uv", "tool", "install", "/verified/new.whl"]]), \
                        mock.patch.object(update, "_pin_plan_wheel", side_effect=lambda c, t, s: (c, None)), \
                        mock.patch.object(update, "rollback_wheel", return_value=None), \
                        mock.patch.object(update, "_install_sleep", lambda s: None), \
                        mock.patch("agentj.doctor.run", return_value=[]):
                    phone_update.run(self.st, restart_fn=lambda: None)
                self.assertEqual(len(calls), want)
                self.assertTrue(all(x <= auto_update.INSTALL_TIMEOUT for x in calls))

    def test_worker_rollback_uses_valid_store_copy_without_the_cache(self):
        def drop_cache(inner):
            def run(argv):
                if _names(argv, NEW):
                    pathlib.Path(self.old).unlink(missing_ok=True)
                    pathlib.Path(self.old).with_suffix(".verified.json").unlink(missing_ok=True)
                return inner(argv)
            return run
        auto_update.pin_artifact(b"old", __version__, self.st.root)
        r = self.attempt([(1, CONFLICT_ERR)], runner_extra=drop_cache)
        self.assertEqual(r["result"], "rolled_back")
        self.assertIn(str(self.st.root / "install-artifacts" / _store_name(__version__)), self.rollbacks[0])

    def test_worker_rollback_with_tampered_store_copy_behaves_as_base(self):
        pin = auto_update.pin_artifact(b"old", __version__, self.st.root)
        side = pin.path.with_name(pin.path.name + ".json")
        side.chmod(0o600)
        side.write_text(json.dumps({"sha256": "0" * 64, "signed": False}))
        side.chmod(0o400)
        r = self.attempt([(1, CONFLICT_ERR)])        # cache still there: base rollback from the verified cache bytes
        self.assertEqual(r["result"], "rolled_back")
        self.assertEqual(json.loads(side.read_text())["sha256"], hashlib.sha256(b"old").hexdigest())  # re-pinned

    def test_manual_rollback_from_store_needs_no_cache_wheel(self):
        from agentj import update
        auto_update.pin_artifact(b"old", __version__, self.st.root, signed=True)
        seen = []

        def run(cmd, **kw):
            seen.append(list(cmd))
            out = f"agentj {__version__}" if cmd[-1] == "--version" else ""
            return subprocess.CompletedProcess(cmd, 0, out, "")
        self.assertTrue(update.roll_back(self.st, INFO, None, __version__, run))
        self.assertIn(str(self.st.root / "install-artifacts" / _store_name(__version__)), seen[0])
        self.assertTrue(update.roll_back(self.st, INFO, None, __version__, run, signed_only=True))
        auto_update.pin_artifact(b"old-unsigned", __version__, self.st.root, signed=False)   # other bytes, no signature
        self.assertFalse(update.roll_back(self.st, INFO, None, __version__, run, signed_only=True))

    def test_worker_store_write_failure_is_fixed_and_installs_nothing(self):
        import errno as _errno
        with mock.patch.object(auto_update, "_write_at", side_effect=OSError(_errno.ENOSPC, "full")):
            r = self.attempt([0])
        self.assertEqual((r["result"], r["reason"]), ("failed", "install_artifact_unavailable"))
        self.assertEqual((self.installs, self.rollbacks), ([], []))

    def test_prune_removes_only_old_tmp_files(self):
        import time as _t
        store = self.st.root / "install-artifacts"
        store.mkdir(mode=0o700)
        fresh, old = store / ".a.whl.1.tmp", store / ".a.whl.2.tmp"
        fresh.write_bytes(b"")
        old.write_bytes(b"")
        os.utime(old, (_t.time() - auto_update.STORE_TMP_AGE - 60,) * 2)
        auto_update.prune_artifacts(self.st.root, (NEW,))
        self.assertEqual((fresh.exists(), old.exists()), (True, False))

    def test_retry_count_accepts_ascii_digits_only(self):
        self.assertTrue(upgrade_diag.meta_line_ok("retry_count: 3"))
        for bad in ("retry_count: \u0663", "retry_count: \u00b3", "retry_count: 10", "retry_count: "):
            self.assertFalse(upgrade_diag.meta_line_ok(bad), bad)


class Round3(_Env):
    """0334 security r3: an expired retry launches nothing (Codex F1); a loose cache folder still upgrades through a
    private re-staging (Claude F1); identical bytes keep `signed: true` (Claude F2)."""
    manual = ArtifactPinning.manual

    def worker_expiring(self, where):
        t = [0.0]
        real_check = auto_update.check_artifact
        n = [0]

        def check(pin):
            n[0] += 1
            real_check(pin)
            if where == "hash" and n[0] == 2:
                t[0] = 10_000.0                      # the re-hash before the retry took past every deadline

        def on_sleep(env):
            if where == "sleep":
                t[0] = 10_000.0
        with mock.patch.object(auto_update, "check_artifact", side_effect=check):
            return self.attempt([1, 0], clock=lambda: t[0], on_sleep=on_sleep)

    def test_worker_expired_during_sleep_or_hash_launches_nothing(self):
        for where in ("sleep", "hash"):
            with self.subTest(where=where):
                r = self.worker_expiring(where)
                self.assertEqual(len(self.installs), 1)
                self.assertEqual((r["result"], r["reason"]), ("rolled_back", "dependency_download_failed"))
                self.assertEqual(upgrade_diag.read(self.st)["retry_count"], 0)

    def test_manual_expired_during_sleep_or_hash_launches_nothing(self):
        for where in ("sleep", "hash"):
            with self.subTest(where=where):
                t = [0.0]
                real_check = auto_update.check_artifact
                n = [0]

                def check(pin):
                    n[0] += 1
                    real_check(pin)
                    if where == "hash" and n[0] == 2:
                        t[0] = 10_000.0

                def on_sleep(w, env):
                    if where == "sleep":
                        t[0] = 10_000.0
                r, _ = self.manual([1, 0], clock=lambda: t[0], on_sleep=on_sleep,
                                   extra_patches=[mock.patch.object(auto_update, "check_artifact", side_effect=check)])
                self.assertEqual(len(self.consumed), 1)
                self.assertEqual((r["result"], r["reason"], r["retry_count"]), ("failed", "uv_failed", 0))
                self.assertEqual(r["uv_failure_class"], "dns")

    def test_retry_with_less_than_the_floor_left_is_not_launched(self):
        from agentj import update
        limit = auto_update.retry_limit(0.0)
        t = [0.0]

        def on_sleep(w, env):
            t[0] = limit - auto_update.RETRY_FLOOR + 1
        r, _ = self.manual([1, 0], clock=lambda: t[0], on_sleep=on_sleep)
        self.assertEqual((len(self.consumed), r["reason"]), (1, "uv_failed"))
        del update

    def test_loose_or_symlinked_cache_folder_still_upgrades_from_the_store(self):
        r, _ = self.manual([0], loose=True)
        self.assertEqual(r["result"], "ok")
        store = self.st.root / "install-artifacts" / _store_name(NEW)
        self.assertEqual(self.consumed, [(str(store), b"verified-A")])
        # and a loose folder holding unsigned / swapped bytes is still refused
        b_sha = hashlib.sha256(b"unverified-B").hexdigest()

        def swap(whl, receipt):
            whl.write_bytes(b"unverified-B")
            receipt.write_text(json.dumps({"sha256": b_sha, "signed": True}))
        r, _ = self.manual([0], loose=True, swap=swap)
        self.assertEqual((r["result"], r["reason"]), ("failed", "install_artifact_changed"))
        self.assertEqual(self.consumed, [])

    def test_symlinked_cache_root_restages(self):
        from agentj import update
        real = self.root / "real-cache"
        (real / "upgrade-cache").mkdir(parents=True)
        real.chmod(0o700)
        (real / "upgrade-cache").chmod(0o700)
        whl = real / "upgrade-cache" / _store_name(NEW)
        whl.write_bytes(b"verified-A")
        whl.chmod(0o600)
        rc = whl.with_suffix(".verified.json")
        rc.write_text(json.dumps({"sha256": hashlib.sha256(b"verified-A").hexdigest(), "signed": True, "signature": "s"}))
        rc.chmod(0o600)
        link = self.root / "link-cache"
        link.symlink_to(real)
        sig = lambda data, signature, keys: _sig(b"verified-A") if data == b"verified-A" else "x"
        with mock.patch.object(auto_update.minisign, "verify", side_effect=sig):
            cmd, pin = update._pin_plan_wheel(["uv", "tool", "install", str(link / "upgrade-cache" / whl.name)], NEW,
                                              self.st.root)
        self.assertEqual(cmd[-1], str(self.st.root / "install-artifacts" / _store_name(NEW)))
        self.assertEqual(pin.path.read_bytes(), b"verified-A")

    def test_identical_bytes_keep_signed_true(self):
        pin = auto_update.pin_artifact(b"same", NEW, self.st.root, signed=True)
        auto_update.pin_artifact(b"same", NEW, self.st.root, signed=False)
        side = json.loads(pin.path.with_name(pin.path.name + ".json").read_text())
        self.assertTrue(side["signed"])
        self.assertIsNotNone(auto_update.stored_artifact(self.st.root, NEW, signed_only=True))
        auto_update.pin_artifact(b"other", NEW, self.st.root, signed=False)       # new bytes: honestly unsigned
        self.assertIsNone(auto_update.stored_artifact(self.st.root, NEW, signed_only=True))

    def test_untouched_refusal_text_claims_no_rollback_or_download(self):
        from agentj import update
        r, _ = self.manual([0], swap=lambda w, rc: rc.unlink())
        block = update.result_block(r)
        self.assertNotIn("rollback:", block)
        for claim in ("a rollback was tried", "downloads and verifies it afresh", "已尝试回滚", "重新下载"):
            self.assertNotIn(claim, block)


HOSTILE = ("sk-short123456", "mirror.corp.internal", "10.0.0.5", "AKIA" + "QZ7X" * 4,
           "q7Zr" "Lm2P" "x9Tb" "Vc4N" "w8Ky" "R5j",            # 23-char opaque token, built at run time
           "acme-internal-billing")


class HostilePackageNames(_Env):
    """0334 security r1 (F2/F5): only agentj's own dependency names leave the parser, everywhere; anything else is `other`."""

    def check_text(self, text, where):
        lines = [ln for ln in text.split("\n") if ln.startswith("dependency_package")]
        self.assertEqual(lines, ["dependency_package: other"], where)

    def test_every_channel_auto_and_manual(self):
        """The dependency field is `other` in every channel. (The redacted installer tail is the existing, separately
        reviewed P277 evidence and is not a dependency field.)"""
        from agentj import update
        for value in HOSTILE:
            err = f"error: Failed to download `{value}`\n  Caused by: dns error: failed to lookup address information\n"
            with self.subTest(value=value, path="auto"):
                self.attempt([(2, err)] * 5)
                for where, rec in (("last-upgrade.json", upgrade_diag.read(self.st)),
                                   ("auto result", auto_update.read(self.st)["result"]), ("support res", self.sent[0])):
                    self.assertEqual(rec.get("dependency_package"), "other", where)
                self.check_text(upgrade_diag.body(self.sent[0], "uv"), "full body")
                self.check_text(upgrade_diag.metadata_body(self.sent[0], "uv"), "metadata")
            with self.subTest(value=value, path="manual"):
                m = ManualAndPhone.fake.__get__(self)
                run = m([(2, err)] * 5)
                with mock.patch.object(update, "install_kind", return_value=INFO), \
                        mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                        mock.patch.object(update, "writable", return_value=True), \
                        mock.patch.object(update, "_install_sleep", lambda s: None), \
                        mock.patch.object(update, "_install_clock", lambda: 0.0):
                    r = update.apply(self.st, NEW, fenced=False, svc_on=False, run=run,
                                     check_fn=lambda: {"status": "newer", "latest": NEW},
                                     plan_fn=lambda st, info, target: [list(c) for c in ManualAndPhone.PLAN],
                                     rollback_fn=lambda st: None)
                for where, rec in (("result / phone result", r), ("last-upgrade.json", upgrade_diag.read(self.st))):
                    self.assertEqual(rec.get("dependency_package"), "other", where)
                    self.assertNotIn(value, json.dumps({k: v for k, v in rec.items() if k != "stderr_tail"}), where)
                for where, text in (("result_block", update.result_block(r)), ("body", upgrade_diag.body(r, "uv")),
                                    ("metadata", upgrade_diag.metadata_body(r, "uv"))):
                    self.check_text(text, where)

    def test_supplied_records_are_mapped_too(self):
        from agentj import update
        for value in HOSTILE:
            with self.subTest(value=value):
                self.assertEqual(upgrade_diag.dependency_fields({"dependency_package": value}), {"dependency_package": "other"})
                rec = {"v": 1, "from": __version__, "to": NEW, "result": "failed", "reason": "uv_failed", "at": 1,
                       "step": "install", "dependency_package": value}
                self.assertEqual(update.host_op(self.st, {"op": "record", "rec": rec}, {}), {"ok": True})
                self.assertEqual(upgrade_diag.read(self.st)["dependency_package"], "other")
                self.assertNotIn(value, upgrade_diag.body({**rec, "rollback": "ok"}))
        self.assertEqual(upgrade_diag.dependency_fields({"dependency_package": "websockets"}),
                         {"dependency_package": "websockets"})


class BoundedParsing(_Env):
    """0334 security r1 (F6): bounded, linear evidence parsing; the deadline covers parsing + backoff + attempts."""

    def test_multi_megabyte_single_line_parses_fast(self):
        import time as _t
        for text in ("Failed to download `" + "A" * 3_000_000 + "`\nDNS error",
                     "x" * 3_000_000, "a" * 1_000_000 + "://" + "b" * 1_000_000,
                     ("failed to fetch " * 200_000) + "`x`", "Failed to download `websockets==1`\n" + "z" * 4_000_000):
            with self.subTest(n=len(text)):
                t0 = _t.perf_counter()
                upgrade_diag.dependency_evidence(text, {})
                self.assertLess(_t.perf_counter() - t0, 0.1)
        self.assertEqual(len(upgrade_diag.bound_evidence("y" * 10_000_000)), upgrade_diag.EVIDENCE_LINE_MAX)

    def test_exhausted_deadline_stops_retries_auto(self):
        t = [0.0]

        def late(inner):
            def run(argv):
                if _names(argv, NEW):
                    t[0] = auto_update.INSTALL_BUDGET - 10       # the first attempt used (almost) the whole budget
                return inner(argv)
            return run
        r = self.attempt([1, 0], clock=lambda: t[0], runner_extra=late)
        self.assertEqual((r["result"], len(self.installs), self.sleeps), ("rolled_back", 1, []))

    def test_exhausted_deadline_stops_retries_manual(self):
        from agentj import update
        clock = iter([0.0] + [auto_update.INSTALL_BUDGET + 1.0] * 20)
        m = ManualAndPhone.fake.__get__(self)
        run = m([1, 0])
        with mock.patch.object(update, "install_kind", return_value=INFO), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]), \
                mock.patch.object(update, "writable", return_value=True), \
                mock.patch.object(update, "_install_sleep", lambda s: self.fail("slept past the deadline")), \
                mock.patch.object(update, "_install_clock", lambda: next(clock)):
            r = update.apply(self.st, NEW, fenced=False, svc_on=False, run=run,
                             check_fn=lambda: {"status": "newer", "latest": NEW},
                             plan_fn=lambda st, info, target: [list(c) for c in ManualAndPhone.PLAN],
                             rollback_fn=lambda st: None)
        self.assertEqual((r["reason"], len(self.calls)), ("uv_failed", 1))


class Redaction(_Env):
    def test_fields_never_carry_url_host_path_token_or_stderr(self):
        os.environ["UV_INDEX_URL"] = "https://user:" + FAKE_TOKEN + "@mirror.corp-example.internal/simple"
        os.environ["HTTPS_PROXY"] = "http://proxy.corp-example.internal:3128"
        self.attempt([1, 1, 1, 1])
        rec = upgrade_diag.read(self.st)
        fields = upgrade_diag.dependency_fields(rec)
        self.assertEqual(fields["dependency_source"], "mirror")
        blob = json.dumps(fields) + upgrade_diag.metadata_body(self.sent[0], "uv")
        for bad in ("http", "://", "corp-example", "mirror.", FAKE_TOKEN, "custuser", "/home", HOME_PATH, "Caused by",
                    "dns error", "Name or service"):
            self.assertNotIn(bad, blob)
        for v in fields.values():
            self.assertRegex(str(v), r"^[A-Za-z0-9._-]{1,64}$")

    def test_package_parser_refuses_hosts_tokens_and_paths(self):
        for text in (f"Failed to download `{FAKE_TOKEN}`", "Failed to fetch: `https://evil.example/`",
                     "Failed to build `/home/x/pkg`", "Failed to download `pypi.org`\nhttps://pypi.org/x", "nothing here", None):
            with self.subTest(text=text):
                self.assertIsNone(upgrade_diag.dependency_package(text))
        self.assertEqual(upgrade_diag.dependency_package(
            "Failed to fetch: `https://files.pythonhosted.org/packages/a/b/Python_Socks-2.7.1-py3-none-any.whl`"), "python-socks")
        self.assertEqual(upgrade_diag.dependency_package("Failed to fetch: `https://pypi.org/simple/tomlkit/`"), "tomlkit")
        self.assertEqual(upgrade_diag.dependency_package("Failed to build `zxing-cpp @ file:///home/x/z.tar.gz`"), "zxing-cpp")

    def test_metadata_form_names_only_agentj_dependencies(self):
        res = {"from": "0.30.25", "to": NEW, "result": "failed", "reason": "dependency_download_failed", "step": "install",
               "rollback": "ok", "dependency_package": "left-pad", "dependency_phase": "download", "retry_count": 3}
        meta = upgrade_diag.metadata_body(res)
        self.assertNotIn("left-pad", meta)
        self.assertIn("retry_count: 3", meta)
        self.assertTrue(support.metadata_ok(meta))
        self.assertFalse(upgrade_diag.meta_line_ok("retry_count: 10"))
        self.assertFalse(upgrade_diag.meta_line_ok("uv_failure_class: https://x"))

    def test_metadata_form_stays_within_line_limit(self):
        full = {"from": "0.30.25", "to": NEW, "result": "failed", "reason": "doctor_failed", "step": "health",
                "failure_stage": "health", "artifact_stage": "wheel", "subreason": "internal_error", "cache_status": "miss",
                "exception_type": "ValueError", "errno": 13, "artifact_sha256": "a" * 64, "stated_sha256": "b" * 64,
                "cache_path": "xdg", "cache_level": "file", "cache_owner": "self", "cache_fallback": "tmp",
                "failed_checks": ["service"], "service_reason": "env_mismatch", "dependency_package": "websockets",
                "dependency_phase": "download", "dependency_source": "pypi", "uv_failure_class": "dns", "retry_count": 3}
        meta = upgrade_diag.metadata_body(full)
        self.assertLessEqual(len(meta.split("\n")), support.META_MAX_LINES)
        self.assertEqual(upgrade_diag.META_MAX_LINES, support.META_MAX_LINES)
        self.assertTrue(support.metadata_ok(meta))

    def test_source_category(self):
        cat = upgrade_diag.source_category
        self.assertEqual(cat({}), "pypi")
        self.assertEqual(cat({"HTTPS_PROXY": "http://p:1"}), "proxy")
        self.assertEqual(cat({"UV_DEFAULT_INDEX": "https://pypi.org/simple", "https_proxy": "x"}), "proxy")
        self.assertEqual(cat({"UV_INDEX": "corp=https://m.example/simple"}), "mirror")
        self.assertEqual(cat({"PIP_INDEX_URL": "https://[::1/simple"}), "unknown")
        cfg = self.root / ".config" / "uv" / "uv.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('[[index]]\nurl = "https://mirrors.example.cn/pypi/simple"\ndefault = true\n')
        self.assertEqual(cat(dict(os.environ)), "mirror")
        self.assertEqual(cat({**os.environ, "UV_NO_CONFIG": "1"}), "pypi")

    def test_uv_failure_classes(self):
        for text, cls in (("dns error: failed to lookup address information", "dns"),
                          ("invalid peer certificate: UnknownIssuer", "tls"),
                          ("Failed to download distribution due to network timeout", "timeout"),
                          ("operation timed out", "timeout"),
                          ("tcp connect error: Connection refused (os error 111)", "connect"),
                          ("HTTP status server error (503 Service Unavailable) for url", "http_status"),
                          ("No solution found when resolving dependencies", "other")):
            with self.subTest(cls=cls):
                self.assertEqual(upgrade_diag.uv_failure_class(text), cls)

    def test_dependency_names_cover_pyproject(self):
        text = (AJ / "pyproject.toml").read_text()
        block = re.search(r"(?ms)^dependencies = \[(.*?)^\]", text).group(1)
        names = {upgrade_diag._norm(re.match(r"[A-Za-z0-9._-]+", s).group(0)) for s in re.findall(r'"([^"]+)"', block)}
        self.assertTrue(names)
        self.assertLessEqual(names, upgrade_diag.DEPENDENCY_NAMES)


# The verification boundary is untouched by 0334: source of these functions (and the hash / signature lines inside the
# worker's install step, and the whole minisign module) is pinned to integrate/0.30.34 before the change. Change a pin only
# with a security review of that verification change.
def _segment(path, name):
    text = path.read_text()
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(name)


def _install_verify_block(path):
    text = path.read_text()
    start = text.index("        p = Path(wheel)\n")
    end = text.index("raise ValueError('artifact_signature_mismatch')", start)
    return text[start:end]


def _digests():
    out = {}
    for f, name in (("auto_update.py", "download"), ("auto_update.py", "cached_wheel"), ("auto_update.py", "_cached_wheel"),
                    ("auto_update.py", "_store"), ("auto_update.py", "signed_cached_bytes"),
                    ("auto_update.py", "signed_cached_wheel"), ("auto_update.py", "installed_wheel"),
                    ("update.py", "verified_plan"), ("update.py", "verified_wheel")):
        out[f"{f}:{name}"] = hashlib.sha256(_segment(AJ / "agentj" / f, name).encode()).hexdigest()
    out["auto_update.py:run.install.verify"] = hashlib.sha256(
        _install_verify_block(AJ / "agentj" / "auto_update.py").encode()).hexdigest()
    out["minisign.py"] = hashlib.sha256((AJ / "agentj" / "minisign.py").read_bytes()).hexdigest()
    return out


class VerificationUntouched(unittest.TestCase):
    PINS = {
        "auto_update.py:download": "36550d260b87a8a8128c92938e3d6a50c8e2349f716acde03d0ef6f04e8e3dc6",
        "auto_update.py:cached_wheel": "f5a84ec1ca179ac51bb2f22657a4e17bb01cb071c7c6eabe1b3e3b27fd6eba43",
        "auto_update.py:_cached_wheel": "e8bd72a278a2e41f119ddbaa0137af9c63856350b237bfcf3f3415b620d5300f",
        "auto_update.py:_store": "abab52912bec5ae7109f537a08e7539fd5426447df3cfc0b23c1b8a6cd0a38dc",
        "auto_update.py:signed_cached_bytes": "4fbc86b2ceafd93e407aec8a76397a3a8254bbf64ca4779140590c4e47a9009c",
        "auto_update.py:signed_cached_wheel": "392f96af0f01a65c1c8fc2a9e616d73b006c76a606abfeadae9c85c68e067b5d",
        "auto_update.py:installed_wheel": "499dd49f5940866dd80e9ce5e4aedb3c46da6fbf5609be83c1e862c9e1407168",
        "update.py:verified_plan": "ca3e5f49105be354adaf0336bd289978c3ec2adac3b17f0695bfb0c36238ae41",
        "update.py:verified_wheel": "99ef1e033df933dbe68d9b5a9a40a8ebdaba25ee9f1d12d788978c844e9bfeff",
        "auto_update.py:run.install.verify": "3e655ba102045942de8bbbee79f95b2026c0e838902c62c8d1be48fd038242d3",
        "minisign.py": "3ab541819e9bb1494f9fb9d2baac06c54092f6ad775e571d86cd7b93323a001b",
    }

    def test_verification_sources_match_base(self):
        self.assertEqual(_digests(), self.PINS)


if __name__ == "__main__":
    unittest.main()
