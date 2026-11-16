"""R-0045 (0.30.28): a post-upgrade `service` / `serve` row that has not settled yet is looked at again on every path.

Two upgrade reports (the chairman's /update --wheel job and a customer's nightly upgrade) said doctor_failed /
failed_checks=service while the new version was running fine right after. Contract:
- the phone / --wheel path (update.apply) re-asks the doctor like the nightly worker (P393): only while every blocker is in
  auto_update.TRANSIENT_CHECKS, at most SELF_CHECK_TRIES, SELF_CHECK_WAIT apart; any other blocker fails at once.
- the `service` row says why in a closed word (upgrade_diag.SERVICE_REASONS) that the failure record and the full report
  carry; any other value is dropped (no summary, path or log line).
- the new serve heals an older updater's one-look failure: last-upgrade.json `doctor_failed` on service/serve only, for this
  very version, with the service row ok now → `ok / upgraded, recovered: self_check_late` (local only; nothing is sent).
No new authority, network or state owner.
"""
import _hermetic  # noqa: F401
import json
import subprocess
from unittest import mock

from test_p277_upgrade_diag import _Env, NEW
from agentj import __version__, auto_update, doctor, service, update, upgrade_diag

OK_ROWS = [{"id": "version", "status": "ok"}, {"id": "state", "status": "ok"}, {"id": "serve", "status": "ok"},
           {"id": "service", "status": "ok"}]
SVC_FAIL = [r for r in OK_ROWS if r["id"] != "service"] + [
    {"id": "service", "status": "fail", "summary": "agentj installed but activating", "service_reason": "not_active:activating"}]
VER_FAIL = [r for r in OK_ROWS if r["id"] != "version"] + [{"id": "version", "status": "fail", "summary": "x"}]


class PhoneHealthRetry(_Env):
    def go(self, doctor):
        """doctor(n) → the rows of the n-th `doctor --json` after the install."""
        self.n, self.slept = 0, []

        def run(argv, **kw):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {NEW}", "")
            if argv[-2:] == ["doctor", "--json"]:
                self.n += 1
                rows = doctor(self.n)
                return subprocess.CompletedProcess(argv, int(any(r["status"] == "fail" for r in rows)),
                                                   json.dumps({"checks": rows}), "")
            return subprocess.CompletedProcess(argv, 0, "ok\n", "")
        with mock.patch.object(update.time, "sleep", side_effect=self.slept.append):
            return self.apply(run, svc_on=False)

    def test_service_settling_is_asked_again(self):
        res = self.go(lambda n: SVC_FAIL if n == 1 else OK_ROWS)
        self.assertEqual((res["result"], res["reason"]), ("ok", "upgraded"))
        self.assertEqual(self.n, 2)
        self.assertEqual(self.slept, [auto_update.SELF_CHECK_WAIT])

    def test_service_that_never_settles_still_fails(self):
        res = self.go(lambda n: SVC_FAIL)
        self.assertEqual((res["reason"], res["failed_checks"]), ("doctor_failed", ["service"]))
        self.assertEqual(self.n, auto_update.SELF_CHECK_TRIES)
        self.assertEqual(self.slept, [auto_update.SELF_CHECK_WAIT] * (auto_update.SELF_CHECK_TRIES - 1))

    def test_non_transient_blocker_fails_at_once(self):
        res = self.go(lambda n: VER_FAIL if n == 1 else OK_ROWS)
        self.assertEqual((res["reason"], res["failed_checks"]), ("doctor_failed", ["version"]))
        self.assertEqual((self.n, self.slept), (1, []))

    def test_mixed_blockers_fail_at_once(self):
        both = [r for r in SVC_FAIL if r["id"] != "version"] + [{"id": "version", "status": "fail"}]
        res = self.go(lambda n: both if n == 1 else OK_ROWS)
        self.assertEqual(res["reason"], "doctor_failed")
        self.assertEqual((self.n, self.slept), (1, []))

    def fenced(self, answers):
        """The host's `health` answers in turn (fenced, host channel up)."""
        self.ops, self.slept = [], []
        seq = iter(answers)

        def host(st, op, t=60, **kw):
            self.ops.append(op)
            return {"ok": True, "checks": next(seq)} if op == "health" else {"ok": True}

        def run(argv, **kw):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {NEW}", "")
            return subprocess.CompletedProcess(argv, 0, "ok\n", "")
        with mock.patch.object(update, "install_kind", return_value={"kind": "uv", "where": "/fake", "legacy": False}), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]), mock.patch.object(update, "writable", return_value=True), \
                mock.patch.object(update, "host_channel", return_value=True), mock.patch.object(update, "_host", side_effect=host), \
                mock.patch.object(update.time, "sleep", side_effect=self.slept.append):
            return update.apply(self.st, NEW, plan_fn=lambda st, info, target: update.commands(info, target),
                                check_fn=lambda: {"status": "newer", "latest": NEW}, run=run, svc_on=False, fenced=True)

    def test_fenced_host_health_is_asked_again(self):
        res = self.fenced([SVC_FAIL, OK_ROWS])
        self.assertEqual((res["result"], res["reason"], res["health"]), ("ok", "upgraded", "host"))
        self.assertEqual((self.ops.count("health"), self.slept), (2, [auto_update.SELF_CHECK_WAIT]))

    def test_fenced_host_non_transient_fails_at_once(self):
        res = self.fenced([VER_FAIL, OK_ROWS])
        self.assertEqual((res["reason"], res["failed_checks"]), ("doctor_failed", ["version"]))
        self.assertEqual((self.ops.count("health"), self.slept), (1, []))


PRIVATE = "/Us" "ers/customer/secret-path"


class ServiceReason(_Env):
    def row(self, svc, legacy=None, mismatch=(), missing=(), inspect_error=False, no_mgr=None):
        with mock.patch.object(service, "binary_env_mismatches",
                               side_effect=OSError(PRIVATE) if inspect_error else (lambda n: list(mismatch))), \
                mock.patch.object(service, "path_missing_harnesses", return_value=list(missing)), \
                mock.patch.object(service, "classify_no_manager", return_value=no_mgr), \
                mock.patch.object(service, "systemd_problem", return_value=no_mgr):
            return doctor.check_service(svc, legacy)

    def test_each_reason_maps(self):
        sd = {"kind": "systemd", "name": "agentj", "installed": True}
        cases = [
            (dict(svc={"kind": "systemd", "installed": False}, legacy={"installed": True, "name": "jarvis"}), "warn", "legacy_name"),
            (dict(svc={"kind": "none", "active": "unsupported_os"}), "warn", "no_manager"),
            (dict(svc={**sd, "active": "offline"}, no_mgr=next(iter(service.NO_MANAGER))), "warn", "no_manager"),
            (dict(svc={**sd, "installed": False, "active": "inactive"}), "warn", "not_installed"),
            (dict(svc={**sd, "active": "active"}, inspect_error=True), "fail", "inspect_failed"),
            (dict(svc={**sd, "active": "active"}, mismatch=["CLAUDE"]), "fail", "env_mismatch"),
            (dict(svc={**sd, "active": "active"}, missing=["claude"]), "warn", "path_missing"),
        ] + [(dict(svc={**sd, "active": s}), "fail", "not_active:" + s) for s in ("inactive", "failed", "activating", "deactivating")] + [
            (dict(svc={"kind": "launchd", "name": "agentj", "installed": True, "active": "not_loaded"}), "fail", "not_active:not_loaded"),
            (dict(svc={"kind": "launchd", "name": "agentj", "installed": True, "active": "unknown"}), "fail", "not_active:unknown"),
            (dict(svc={"kind": "launchd", "name": "agentj", "installed": True, "active": PRIVATE}), "fail", "not_active:unknown"),
        ]
        seen = set()
        for kw, status, why in cases:
            with self.subTest(why=why, svc=kw["svc"]):
                r = self.row(**kw)
                self.assertEqual((r["id"], r["status"], r.get("service_reason")), ("service", status, why))
                seen.add(why)
        self.assertEqual(seen, set(upgrade_diag.SERVICE_REASONS))
        ok = self.row({**sd, "active": "active"})
        self.assertEqual((ok["status"], "service_reason" in ok), ("ok", False))

    def test_unexpected_values_are_dropped(self):
        for bad in ("not_active:running", "not_active:" + PRIVATE, PRIVATE, "", None, 3, ["env_mismatch"], "ENV_MISMATCH"):
            with self.subTest(bad=bad):
                self.assertIsNone(upgrade_diag.service_reason(bad))
                res = {"from": "0.30.27", "to": NEW, "result": "failed", "reason": "doctor_failed", "step": "health",
                       "failed_checks": ["service"], "service_reason": bad}
                rec = upgrade_diag.entry(res)
                self.assertNotIn("service_reason", rec)
                self.assertNotIn("service reason", upgrade_diag.body(res))
        self.assertIsNone(upgrade_diag.service_reason_of([{"id": "service", "status": "fail", "service_reason": PRIVATE}]))
        self.assertIsNone(upgrade_diag.service_reason_of("x"))

    def test_phone_path_carries_the_reason(self):
        def run(argv, **kw):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {NEW}", "")
            if argv[-2:] == ["doctor", "--json"]:
                rows = [{**r, "summary": r.get("summary", "") + PRIVATE} for r in SVC_FAIL]
                return subprocess.CompletedProcess(argv, 1, json.dumps({"checks": rows}), "")
            return subprocess.CompletedProcess(argv, 0, "ok\n", "")
        with mock.patch.object(update.time, "sleep"):
            res = self.apply(run, svc_on=False)
        self.assertEqual((res["failed_checks"], res["service_reason"]), (["service"], "not_active:activating"))
        rec = upgrade_diag.remember(self.st, res)
        self.assertEqual(upgrade_diag.read(self.st)["service_reason"], "not_active:activating")
        body = upgrade_diag.body(res)
        self.assertIn("service reason: not_active:activating", body)
        self.assertNotIn(PRIVATE, body + json.dumps(rec))
        self.assertNotIn("service_reason", json.dumps(upgrade_diag.heartbeat_field(self.st)))

    def test_reason_only_with_a_service_blocker(self):
        res = {"from": "0.30.27", "to": NEW, "result": "failed", "reason": "doctor_failed", "step": "health",
               "failed_checks": ["version"], "service_reason": "env_mismatch"}
        self.assertNotIn("service_reason", upgrade_diag.entry(res))

    def test_auto_path_diagnose_carries_the_reason(self):
        sent = []
        result = {"attempt": "a", "from": "0.30.27", "to": NEW, "result": "rolled_back", "reason": "upgrade_self_check_failed"}
        diag = {"step": "verify", "failure": "upgrade_self_check_failed", "failed_checks": ["service"],
                "service_reason": "env_mismatch"}
        with mock.patch.object(upgrade_diag, "after_failure", side_effect=lambda st, res, **kw: sent.append(res) or res):
            auto_update._diagnose(self.st, result, diag)
            auto_update._diagnose(self.st, result, {**diag, "service_reason": PRIVATE})
        self.assertEqual(sent[0]["service_reason"], "env_mismatch")
        self.assertNotIn("service_reason", sent[1])
        self.assertNotIn(PRIVATE, json.dumps(sent))

    def test_host_health_op_keeps_only_closed_reasons(self):
        rows = [{"id": "service", "status": "fail", "summary": "s", "service_reason": "not_active:failed"},
                {"id": "serve", "status": "fail", "summary": "s", "service_reason": PRIVATE}]
        with mock.patch.object(update, "install_kind", return_value={"kind": "uv", "where": "/fake", "legacy": False}), \
                mock.patch.object(update, "new_argv", return_value=["agentj"]):
            out = update.host_op(self.st, {"op": "health"}, {},
                                 run=lambda *a, **kw: subprocess.CompletedProcess([], 1, json.dumps({"checks": rows}), ""))
        self.assertEqual(out["checks"][0]["service_reason"], "not_active:failed")
        self.assertNotIn("service_reason", out["checks"][1])


class RecoverLate(_Env):
    OK_ROW = {"id": "service", "status": "ok", "summary": "agentj installed, active"}
    BAD_ROW = {"id": "service", "status": "fail", "summary": "installed but inactive", "service_reason": "not_active:inactive"}

    def failed(self, **over):
        rec = {"v": 1, "from": "0.30.26", "to": __version__, "result": "failed", "reason": "doctor_failed", "at": 1793800000,
               "step": "health", "failed_checks": ["service"], "reported": "st_" + "A" * 22, "reported_at": 1793800001,
               **over}
        self.assertTrue(upgrade_diag.write(self.st, rec))
        return rec

    def test_recovers_when_healthy(self):
        self.failed()
        new = upgrade_diag.recover_late(self.st, lambda: self.OK_ROW)
        rec = upgrade_diag.read(self.st)
        self.assertEqual(new, rec)
        self.assertEqual((rec["result"], rec["reason"], rec["recovered"], rec["from"], rec["to"], rec["at"]),
                         ("ok", "upgraded", "self_check_late", "0.30.26", __version__, 1793800000))
        self.assertNotIn("failed_checks", rec)
        self.assertEqual(upgrade_diag.heartbeat_field(self.st)["result"], "ok")
        self.assertIsNone(upgrade_diag.recover_late(self.st, lambda: self.OK_ROW), "once")

    def test_serve_only_and_both_recover(self):
        for checks in (["serve"], ["service", "serve"]):
            with self.subTest(checks=checks):
                self.failed(failed_checks=checks)
                self.assertIsNotNone(upgrade_diag.recover_late(self.st, lambda: self.OK_ROW))

    def test_untouched(self):
        cases = {"other version": dict(to="0.30.99"), "other checks": dict(failed_checks=["service", "version"]),
                 "no checks": dict(failed_checks=[]), "other reason": dict(reason="uv_failed"),
                 "rolled back": dict(result="rolled_back")}
        for name, over in cases.items():
            with self.subTest(name):
                before = self.failed(**over)
                self.assertIsNone(upgrade_diag.recover_late(self.st, lambda: self.OK_ROW))
                self.assertEqual(upgrade_diag.read(self.st), before)
        before = self.failed()
        for row in (lambda: self.BAD_ROW, lambda: {"id": "service", "status": "warn"}, lambda: None,
                    lambda: (_ for _ in ()).throw(OSError("x"))):
            self.assertIsNone(upgrade_diag.recover_late(self.st, row))
            self.assertEqual(upgrade_diag.read(self.st), before)

    def test_no_record_and_nothing_sent(self):
        self.assertIsNone(upgrade_diag.recover_late(self.st, lambda: self.OK_ROW))
        self.failed()
        with mock.patch("agentj.support.run_send") as send:
            upgrade_diag.recover_late(self.st, lambda: self.OK_ROW)
        send.assert_not_called()

    def test_live_service_row_shape(self):
        """The serve hook's row: doctor.check_service on names.seen_status(answering=True) — stubbed manager, no systemctl."""
        from agentj import names
        self.failed()
        sd = {"kind": "systemd", "name": "agentj", "installed": True}
        with mock.patch.object(service, "status", return_value={**sd, "active": "active"}), \
                mock.patch.object(service, "legacy_status", return_value={"installed": False}), \
                mock.patch.object(service, "binary_env_mismatches", return_value=[]), \
                mock.patch.object(service, "path_missing_harnesses", return_value=[]):
            row = lambda: doctor.check_service(names.seen_status(self.st, answering=True), service.legacy_status())  # noqa: E731
            self.assertEqual(row()["status"], "ok")
            self.assertIsNotNone(upgrade_diag.recover_late(self.st, row))


class ServeHook(__import__("unittest").IsolatedAsyncioTestCase):
    """The new serve's startup notice path runs recover_late with the in-process service row (manager stubbed)."""
    async def asyncSetUp(self):
        from test_p551_confirm_levels import Base
        self.base = Base()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.host = self.base.host

    async def run_notice(self, active):
        sd = {"kind": "systemd", "name": "agentj", "installed": True, "active": active}
        with mock.patch("agentj.service.sweep_jobs", return_value=[]), mock.patch.object(service, "status", return_value=sd), \
                mock.patch.object(service, "legacy_status", return_value={"installed": False}), \
                mock.patch.object(service, "binary_env_mismatches", return_value=[]), \
                mock.patch.object(service, "path_missing_harnesses", return_value=[]), \
                mock.patch("agentj.service._systemctl", side_effect=AssertionError("real systemctl")):
            await self.host.upgraded_notice()

    def seed(self):
        upgrade_diag.write(self.host.st, {"v": 1, "from": "0.30.26", "to": __version__, "result": "failed",
                                          "reason": "doctor_failed", "at": 1793800000, "step": "health",
                                          "failed_checks": ["service"]})

    async def test_healthy_serve_rewrites_the_record(self):
        self.seed()
        await self.run_notice("active")
        rec = upgrade_diag.read(self.host.st)
        self.assertEqual((rec["result"], rec["reason"], rec.get("recovered")), ("ok", "upgraded", "self_check_late"))

    async def test_failing_service_keeps_the_failure(self):
        self.seed()
        await self.run_notice("failed")
        self.assertEqual(upgrade_diag.read(self.host.st)["result"], "failed")
