"""P453: a recovered service must not be blocked by its previous upgrade receipt."""
import _hermetic  # noqa: F401
import json
from unittest.mock import patch
from agentj import __version__, doctor, service, service_recovery as sr, update
from test_p142_update import Base


class Recovery(Base):
    def row(self, active='active'):
        return doctor.check_upgrade_restart(self.st, {'active': active})

    def test_existing_upgrade_doctor_rejects_stale_rollback(self):
        sr.record(self.st, 'failed', '99.0.0a1', 'rolled_back')
        with patch.object(service, 'status', return_value={'active': 'active'}):
            rows = doctor.run_upgrade(self.st)
        row = next(r for r in rows if r['id'] == 'upgrade-restart')
        self.assertEqual(row['status'], 'warn')
        self.assertNotIn('upgrade-restart', update.upgrade_blockers(rows)[0])

    def test_legacy_rollback_is_warning_in_both_doctors_and_bilingual(self):
        sr.record(self.st, 'failed', '99.0.0a1', 'rolled_back')
        row = self.row()
        self.assertEqual(row['status'], 'warn')
        self.assertIn('已自动退回旧版、服务正常', row['summary'])
        self.assertIn('automatically rolled back', row['summary'])
        self.assertFalse(update.upgrade_blockers([row])[0])
        with patch.object(service, 'status', return_value={'active': 'active'}), \
                patch.object(doctor, 'check_upgrade_restart', wraps=doctor.check_upgrade_restart) as check:
            doctor.run_upgrade(self.st)
        check.assert_called_once()

    def test_rollback_not_active_still_fails(self):
        sr.record(self.st, 'rolled_back', '99.0.0a1', 'rolled_back')
        self.assertEqual(self.row('failed')['status'], 'fail')

    def test_unrelated_failure_is_not_silently_downgraded(self):
        sr.record(self.st, 'failed', '99.0.0a1', 'launch_failed')
        self.assertEqual(self.row()['status'], 'fail')

    def test_manual_start_acknowledges_old_target_and_notice(self):
        for status in ('failed', 'rolled_back'):
            with self.subTest(status=status):
                sr.record(self.st, status, '99.0.0a1', 'rolled_back')
                with patch.object(service, 'status', return_value={'installed': True, 'active': 'active'}), \
                        patch.object(service, '_rescue_start'):
                    service.start(self.st)
                self.assertEqual(sr.read(self.st)['status'], 'ok')
                self.assertFalse((self.st.root / sr.NOTICE).exists())
                self.assertIsNone(self.row())

    def test_inactive_manual_start_and_inflight_receipt_not_acknowledged(self):
        for status, active in (('failed', 'failed'), ('running', 'active'), ('waiting', 'active')):
            sr.record(self.st, status, '99.0.0a1', 'launch_failed')
            with patch.object(service, 'status', return_value={'active': active}):
                service._acknowledge_recovery(self.st)
            self.assertEqual(sr.read(self.st)['status'], status)

    def test_expired_pending_receipt_still_acknowledged(self):
        self.st.write_private(self.st.root / sr.RECORD, json.dumps(
            {'status': 'pending', 'target': __version__, 'at': 1}).encode())
        with patch.object(service, 'status', return_value={'active': 'active'}):
            service._acknowledge_recovery(self.st)
        self.assertEqual(sr.read(self.st)['status'], 'ok')

    def test_successful_recovery_clears_old_rollback(self):
        sr.record(self.st, 'rolled_back', '99.0.0a1', 'rolled_back')
        self.assertEqual(sr.run(self.st, __version__, install=lambda st: None,
                                status=lambda: {'active': 'active'}, ready=lambda st: 'idle'), 0)
        self.assertIsNone(self.row())


from test_p393_upgrade_check import Worker as UpgradeWorker
import io
from contextlib import redirect_stdout


class Automatic(UpgradeWorker):
    def test_health_failure_logs_ids_only_and_successful_rollback_is_marked(self):
        output = io.StringIO()
        with patch.object(service, 'status', return_value={'active': 'active'}), \
                patch.object(self.st, 'log', wraps=self.st.log) as log, redirect_stdout(output):
            result = self.attempt(lambda n: json.dumps({'checks': [
                {'id': 'main-core', 'status': 'fail', 'summary': 'PRIVATE_DETAIL_CANARY'}]}))
        self.assertEqual(result['result'], 'rolled_back')
        self.assertEqual(sr.read(self.st)['status'], 'rolled_back')
        self.assertEqual(doctor.check_upgrade_restart(self.st, {'active': 'active'})['status'], 'warn')
        log.assert_any_call('upgrade_health_failed', reason='doctor_failed', status='upgrade', why='main-core')
        self.assertIn('upgrade_health_failed upgrade: main-core', output.getvalue())
        self.assertNotIn('PRIVATE_DETAIL_CANARY', output.getvalue())

    def test_no_active_service_does_not_claim_healthy_rollback(self):
        with patch.object(service, 'status', return_value={'active': 'failed'}):
            self.attempt(lambda n: json.dumps({'checks': [{'id': 'state', 'status': 'fail'}]}))
        self.assertNotEqual((sr.read(self.st) or {}).get('status'), 'rolled_back')


    def test_rollback_receipt_ends_restart_poll_without_waiting(self):
        from agentj import auto_update
        import hashlib
        from unittest.mock import Mock
        old = self.st.root / 'old.whl'
        old.write_bytes(b'old')
        old.with_suffix('.verified.json').write_text(json.dumps({'sha256': hashlib.sha256(b'old').hexdigest()}))
        auto_update.write(self.st, {'phase': 'installed', 'from': __version__, 'to': '99.0.0a1',
                                  'install': {'kind': 'pip', 'where': str(self.st.root)},
                                  'argv': ['fixture'], 'old': str(old)}, auto_update.JOB)
        def runner(argv):
            return Mock(returncode=0, stdout='agentj 99.0.0a1' if argv[-1] == '--version' else '')
        with patch.object(sr, 'read', return_value={'status': 'rolled_back', 'target': '99.0.0a1'}), \
                patch.object(auto_update.time, 'sleep') as sleep, \
                patch.object(auto_update.time, 'monotonic', side_effect=lambda ticks=iter((0, 0, 10000)): next(ticks, 10000)), \
                patch('agentj.upgrade_diag.after_failure'), patch('agentj.phone_update.release'):
            auto_update.run(self.st, runner=runner)
        sleep.assert_not_called()
