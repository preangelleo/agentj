"""R-0012 (0.30.29): after a rollback, `agentj doctor` must not keep an `upgrade-restart` ✗ on a host that runs normally.

Customer co-100a50 (ticket st_00bypO4ZOe6qN8N_b3qq6A) and an Intel Mac upgrade report. End-to-end pins through doctor:
(a) a rollback receipt (current `rolled_back` and the legacy `failed / reason=rolled_back` shape, local record or the
    launchd hand-off receipt) with the service active → `upgrade-restart` is a warning and never an upgrade blocker;
(b) the same receipt with the service not active → ✗ with the rollback wording;
(c) a completed `failed` record, then the new serve starts under the service manager → the record is acknowledged and
    doctor shows no `upgrade-restart` row at all (inactive service: the ✗ stays);
(d) an Intel-Mac-like slow restart — the launchd `service` row fails on the first post-upgrade look, ok on the next — on
    both update health paths (nightly worker, phone /update) → upgraded, never doctor_failed.
Manager calls are stubbed; fake HOME / state / XDG dirs; no real systemctl or launchctl.
"""
import _hermetic  # noqa: F401
import json
import unittest
from unittest import mock

from agentj import doctor, service, service_recovery as sr, update
import test_p0328_upgrade_health as p0328
import test_p393_upgrade_check as p393
from test_p142_update import Base

ROLLBACK_SHAPES = (('rolled_back', 'rolled_back'), ('failed', 'rolled_back'))


class _Guard:
    def setUp(self):
        super().setUp()
        for name in ('_systemctl', '_launchctl'):
            p = mock.patch.object(service, name, side_effect=AssertionError('real ' + name))
            p.start()
            self.addCleanup(p.stop)


class DoctorAfterRollback(_Guard, Base):
    def rows(self, active):
        svc = {'kind': 'launchd', 'name': 'agentj', 'installed': True, 'active': active}
        with mock.patch.object(service, 'status', return_value=svc), \
                mock.patch.object(service, 'legacy_status', return_value={'installed': False}), \
                mock.patch.object(service, 'binary_env_mismatches', return_value=[]), \
                mock.patch.object(service, 'path_missing_harnesses', return_value=[]):
            return doctor.run_upgrade(self.st)

    def receipt(self, status, reason, external):
        with mock.patch.object(service, 'platform', return_value='macos'):
            sr.record(self.st, status, '99.0.0a1', reason, external=external)

    def test_a_rollback_with_active_service_is_a_warning_not_a_blocker(self):
        for (status, reason), external in [(s, e) for s in ROLLBACK_SHAPES for e in (False, True)]:
            with self.subTest(status=status, external=external):
                self.receipt(status, reason, external)
                rows = self.rows('active')
                row = next(r for r in rows if r['id'] == 'upgrade-restart')
                self.assertEqual(row['status'], 'warn')
                self.assertIn('automatically rolled back', row['summary'])
                self.assertNotIn('upgrade-restart', update.upgrade_blockers(rows)[0])

    def test_b_rollback_with_service_not_active_fails_with_rollback_text(self):
        for status, reason in ROLLBACK_SHAPES:
            for active in ('not_loaded', 'inactive', 'failed'):
                with self.subTest(status=status, active=active):
                    self.receipt(status, reason, False)
                    rows = self.rows(active)
                    row = next(r for r in rows if r['id'] == 'upgrade-restart')
                    self.assertEqual(row['status'], 'fail')
                    self.assertIn('已退回旧版，但服务尚未恢复正常', row['summary'])
                    self.assertIn('Rolled back, but the service is not running normally', row['summary'])
                    self.assertIn('upgrade-restart', update.upgrade_blockers(rows)[0])


class ServeStartClears(_Guard, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hook = p0328.ServeHook("test_healthy_serve_rewrites_the_record")
        await self.hook.asyncSetUp()
        self.addCleanup(self.hook.doCleanups)
        self.host = self.hook.host

    async def run_notice(self, active):
        await self.hook.run_notice(active)

    def seed_failed(self, reason='recovery_inactive'):
        rec = {'status': 'failed', 'target': '99.0.0a1', 'at': 1, 'reason': reason}
        for p in (self.host.st.root / sr.RECORD, self.host.st.root / sr.NOTICE):
            p.write_text(json.dumps(rec))

    def doctor_row(self, active):
        return doctor.check_upgrade_restart(self.host.st, {'active': active})

    async def test_c_failed_record_cleared_when_serve_starts_under_the_manager(self):
        self.seed_failed()
        self.assertEqual(self.doctor_row('active')['status'], 'fail')      # the customer's ✗ before the fix
        await self.run_notice('active')
        self.assertEqual(sr.read(self.host.st)['status'], 'ok')
        self.assertFalse((self.host.st.root / sr.NOTICE).exists())
        self.assertIsNone(self.doctor_row('active'))
        rows = [r for r in [self.doctor_row('active')] if r]
        self.assertNotIn('upgrade-restart', update.upgrade_blockers(rows + [{'id': 'version', 'status': 'ok'}])[0])

    async def test_c_failed_record_stays_when_the_service_is_not_up(self):
        self.seed_failed()
        await self.run_notice('failed')
        self.assertEqual(sr.read(self.host.st)['status'], 'failed')
        self.assertEqual(self.doctor_row('failed')['status'], 'fail')

    async def test_c_rollback_is_kept_as_a_warning_after_serve_start(self):
        rec = {'status': 'rolled_back', 'target': '99.0.0a1', 'at': 1, 'reason': 'rolled_back'}
        (self.host.st.root / sr.RECORD).write_text(json.dumps(rec))
        await self.run_notice('active')
        self.assertEqual(self.doctor_row('active')['status'], 'warn')


SLOW_SVC = {"id": "service", "status": "fail", "summary": "agentj installed but not_loaded",
            "service_reason": "not_active:not_loaded"}
OK_SVC = {"id": "service", "status": "ok", "summary": "agentj installed, active"}


def _fixture(cls, case):
    """Borrow another module's fixture without re-running its test methods here."""
    t = cls(case)
    t.setUp()
    return t


class IntelMacSlowRestart(_Guard, unittest.TestCase):
    def test_d_slow_launchd_restart_on_the_nightly_path_is_not_doctor_failed(self):
        w = _fixture(p393.Worker, "test_waiting_main_inject_passes_the_upgrade")
        self.addCleanup(w.doCleanups)
        result = w.attempt(lambda n: json.dumps({"checks": p393.OK_ROWS + [SLOW_SVC if n == 1 else OK_SVC]}))
        self.assertEqual((result["result"], result["reason"]), ("ok", "upgraded"))
        self.assertEqual(w.state["doctor"], 2)
        self.assertEqual(w.sent, [])

    def test_d_slow_launchd_restart_on_phone_update_is_not_doctor_failed(self):
        ph = _fixture(p0328.PhoneHealthRetry, "test_service_settling_is_asked_again")
        self.addCleanup(ph.doCleanups)
        rows = [r for r in p0328.OK_ROWS if r["id"] != "service"]
        res = ph.go(lambda n: rows + [SLOW_SVC if n == 1 else OK_SVC])
        self.assertEqual((res["result"], res["reason"]), ("ok", "upgraded"))
        self.assertNotIn("failed_checks", res)
        self.assertEqual(ph.n, 2)
