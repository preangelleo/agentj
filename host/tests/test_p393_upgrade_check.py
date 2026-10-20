"""P393 (#121) contract: the post-upgrade self-check names what blocked it.

A paying customer's nightly upgrade rolled back with only `doctor_failed` in the automatic report. Now:
- the main Agent still waiting for its first phone message after the restart (main-inject) never blocks, even from a
  doctor that marks it failed;
- a real blocking check rolls back and the report / last-upgrade record carry its fixed id (no summary, path, stderr);
- doctor output that cannot be parsed is its own id (doctor_unparsable), not a bare exception type;
- a serve / service row that has not answered yet right after the restart is looked at again before rolling back.
No new authority, network or state owner.
"""
import _hermetic  # noqa: F401
import hashlib
import json
import subprocess
from unittest import mock

from test_p277_upgrade_diag import _Env, INFO, NEW
from agentj import __version__, auto_update, update, upgrade_diag

WAITING = {"id": "main-inject", "status": "fail", "summary": "no verified main Agent launch yet; start a conversation"}
WAITING_ZH = {"id": "main-inject", "status": "warn",
              "summary": "服务重启后还没收到手机消息，从手机发一句话即可完成验证 / No phone message since the service restarted"}
OK_ROWS = [{"id": "version", "status": "ok"}, {"id": "state", "status": "ok"}, {"id": "serve", "status": "ok"}]
PRIVATE = "/Us" "ers/customer/secret-path"


class Classify(_Env):
    def test_waiting_main_inject_is_not_a_blocker(self):
        self.assertEqual(update.upgrade_blockers(OK_ROWS + [WAITING]), ([], []))
        self.assertEqual(update.upgrade_blockers(OK_ROWS + [WAITING_ZH]), ([], []))
        self.assertEqual(update.upgrade_health(OK_ROWS + [WAITING]), (False, []))

    def test_real_main_inject_failure_still_blocks(self):
        bad = {"id": "main-inject", "status": "fail", "summary": "identity injection broken"}
        self.assertEqual(update.upgrade_blockers(OK_ROWS + [bad])[0], ["main-inject"])

    def test_ids_unnamed_and_unparsable(self):
        self.assertEqual(update.upgrade_blockers([{"status": "fail"}])[0], [update.DOCTOR_UNNAMED])
        for rows in (None, [], "x", [{"id": "version", "status": "maybe"}]):
            self.assertEqual(update.upgrade_blockers(rows)[0], [update.DOCTOR_UNPARSABLE])
        self.assertEqual(update.check_ids(["serve", PRIVATE, "Serve", "serve", "a" * 40]), ["serve"])


class Worker(_Env):
    def attempt(self, doctor, tries=3):
        """doctor(n, current) → stdout of the n-th post-upgrade doctor (current: the version now installed)."""
        old, new = self.st.root / "old.whl", self.st.root / "new.whl"
        for p in (old, new):
            p.write_bytes(b"w")
            p.with_suffix(".verified.json").write_text(json.dumps({"sha256": hashlib.sha256(b"w").hexdigest()}))
        auto_update.write(self.st, {"attempt": "a", "from": __version__, "to": NEW, "install": INFO, "argv": ["agentj"],
                                    "old": str(old), "new": str(new), "phase": "prepared"}, auto_update.JOB)
        state = {"current": __version__, "doctor": 0}

        def runner(argv):
            if str(new) in argv:
                state["current"] = NEW
            elif str(old) in argv:
                state["current"] = __version__
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {state['current']}", "")
            if "doctor" in argv:
                if state["current"] != NEW:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"checks": OK_ROWS}), "")
                state["doctor"] += 1
                out = doctor(state["doctor"])
                return subprocess.CompletedProcess(argv, 0 if out.startswith("{") else 1, out, "Traceback " + PRIVATE)
            return subprocess.CompletedProcess(argv, 0, "ok\n", "")
        self.sent, self.state = [], state
        with mock.patch.object(auto_update, "enabled", return_value=True), \
                mock.patch.object(auto_update, "SELF_CHECK_WAIT", 0), mock.patch.object(auto_update, "SELF_CHECK_TRIES", tries), \
                mock.patch.object(auto_update.minisign, "verify",
                                  return_value=f"agentj-host/v1 version={NEW} sha256={hashlib.sha256(b'w').hexdigest()}"), \
                mock.patch.object(upgrade_diag, "after_failure", side_effect=lambda st, res, **kw: self.sent.append(res) or res), \
                mock.patch("agentj.phone_update.release"), mock.patch("agentj.service_recovery.cleanup_job"):
            return auto_update.run(self.st, runner=runner, restart=lambda v: None)

    def test_waiting_main_inject_passes_the_upgrade(self):
        result = self.attempt(lambda n: json.dumps({"checks": OK_ROWS + [WAITING]}))
        self.assertEqual((result["result"], result["reason"]), ("ok", "upgraded"))
        self.assertEqual(self.sent, [])

    def test_real_failing_check_rolls_back_and_report_names_it(self):
        rows = OK_ROWS + [WAITING, {"id": "upgrade-restart", "status": "fail", "summary": "Upgrade restart failed " + PRIVATE}]
        result = self.attempt(lambda n: json.dumps({"checks": rows}))
        self.assertEqual((result["result"], result["reason"]), ("rolled_back", "upgrade_self_check_failed"))
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec["reason"], rec["step"], rec["failed_checks"]), ("doctor_failed", "verify", ["upgrade-restart"]))
        body = upgrade_diag.body(self.sent[0])
        self.assertIn("failed checks: upgrade-restart", body)
        self.assertNotIn("main-inject", body)
        self.assertNotIn(PRIVATE, body + json.dumps(rec))
        self.assertNotIn("failed_checks", json.dumps(upgrade_diag.heartbeat_field(self.st)))

    def test_unparsable_doctor_is_its_own_id(self):
        result = self.attempt(lambda n: "Traceback (most recent call last): " + PRIVATE)
        self.assertEqual(result["result"], "rolled_back")
        rec = upgrade_diag.read(self.st)
        self.assertEqual(rec["failed_checks"], ["doctor_unparsable"])
        self.assertEqual(rec["detail"], "doctor exit 1")
        body = upgrade_diag.body(self.sent[0])
        self.assertIn("failed checks: doctor_unparsable", body)
        self.assertNotIn(PRIVATE, body)
        self.assertNotIn("Traceback", body)

    def test_slow_serve_is_asked_again_before_rollback(self):
        busy = OK_ROWS[:2] + [{"id": "serve", "status": "fail", "summary": "running but not answering"}]
        result = self.attempt(lambda n: json.dumps({"checks": busy if n == 1 else OK_ROWS + [WAITING]}))
        self.assertEqual(result["result"], "ok")
        self.assertEqual(self.state["doctor"], 2)

    def test_serve_that_never_answers_still_rolls_back(self):
        busy = OK_ROWS[:2] + [{"id": "serve", "status": "fail", "summary": "running but not answering"}]
        result = self.attempt(lambda n: json.dumps({"checks": busy}))
        self.assertEqual(result["result"], "rolled_back")
        self.assertEqual(self.state["doctor"], 3)
        self.assertEqual(upgrade_diag.read(self.st)["failed_checks"], ["serve"])


class ManualApply(_Env):
    def test_manual_apply_names_blocking_check(self):
        def run(argv, **kw):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {NEW}", "")
            if argv[-2:] == ["doctor", "--json"]:
                rows = OK_ROWS + [WAITING, {"id": "state", "status": "fail", "summary": PRIVATE}]
                return subprocess.CompletedProcess(argv, 1, json.dumps({"checks": rows}), "")
            return subprocess.CompletedProcess(argv, 0, "ok\n", "")
        res = self.apply(run, svc_on=False)
        self.assertEqual((res["reason"], res["failed_checks"]), ("doctor_failed", ["state"]))
        self.assertIn("failed_checks: state", "\n".join(update.result_block(res).splitlines()))

    def test_manual_apply_waiting_only_passes(self):
        def run(argv, **kw):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, f"agentj {NEW}", "")
            if argv[-2:] == ["doctor", "--json"]:
                return subprocess.CompletedProcess(argv, 1, json.dumps({"checks": OK_ROWS + [WAITING]}), "")
            return subprocess.CompletedProcess(argv, 0, "ok\n", "")
        self.assertEqual(self.apply(run, svc_on=False)["result"], "ok")


if __name__ == "__main__":
    import unittest
    unittest.main()
