"""P487 #262/#263: untouched installs and runnable rollbacks tell the truth."""
import _hermetic  # noqa: F401
import json
import os
import subprocess
import urllib.error
from unittest.mock import Mock, patch

from test_p277_upgrade_diag import _Env, INFO, NEW, UV_ERR, FAKE_GH
import test_p397_upgrade_recovery as recovery_fixture
from agentj import __version__, auto_update as au, doctor, upgrade_diag as ud, update


class Failures(_Env):
    prepare = recovery_fixture.Worker.prepare

    def test_download_failure_after_old_cache_never_offers_recover(self):
        calls = []
        def cache(st, version, **kw):
            calls.append(version)
            if version == NEW:
                raise urllib.error.URLError('private proxy URL')
            return self.st.root/'old.whl'
        with patch.object(update, 'install_kind', return_value=INFO), \
                patch.object(update, 'new_argv', return_value=['fixture-agentj']), \
                patch.object(ud, 'after_failure'), patch('agentj.service_recovery.cleanup_job'):
            runner = Mock(side_effect=AssertionError('no mutation before download succeeds'))
            result = au.run(self.st, check_fn=lambda: {'status': 'newer', 'latest': NEW}, cache=cache,
                            runner=runner, restart=lambda v: None)
        self.assertEqual(calls, [__version__, NEW])
        self.assertEqual(result['reason'], 'download_failed')
        self.assertNotIn('recover', result)
        self.assertIn('仍是 '+__version__, au.notice(result))
        self.assertIn('still on '+__version__, au.notice(result, 'en'))
        runner.assert_not_called()

    def test_prepared_signature_refusal_preserves_old_install(self):
        self.prepare()
        self.new.write_bytes(b'tampered')
        runner = Mock(side_effect=AssertionError('must not install'))
        result = au.run(self.st, runner=runner, restart=lambda v: None)
        self.assertNotIn('recover', result)
        self.assertIn('仍是 '+__version__, au.notice(result))
        runner.assert_not_called()

    def test_unsupported_install_is_not_rollback(self):
        with patch.object(update, 'install_kind', return_value={'kind': 'checkout', 'where': '/owner/repo'}), \
                patch.object(ud, 'after_failure'), patch('agentj.service_recovery.cleanup_job'):
            cache = Mock(side_effect=AssertionError('unsupported installs cannot download'))
            result = au.run(self.st, check_fn=lambda: {'status': 'newer', 'latest': NEW}, cache=cache)
        self.assertEqual(result['reason'], 'unsupported_install')
        self.assertIn('仍是 '+__version__, au.notice(result))
        cache.assert_not_called()

    def test_legacy_download_record_doctor_row_ignores_spurious_recover(self):
        au.write(self.st, {'result': {'result': 'failed', 'from': __version__, 'to': NEW,
                                    'reason': 'download_failed', 'recover': 'legacy-reinstall-command'}})
        # Execute the real doctor aggregator with unrelated probes replaced by fixed rows.
        probes = [name for name in vars(doctor) if name.startswith('check_') and callable(getattr(doctor, name))]
        patches = [patch.object(doctor, name, return_value=[] if name in
                   ('check_asr', 'check_preferences', 'check_main_identity') else None) for name in probes]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        with patch.object(doctor.service, 'status', return_value={}), \
                patch.object(doctor.service, 'legacy_status', return_value={}), \
                patch.object(doctor, '_serve_status', return_value={}), patch.object(doctor, '_seen', return_value={}):
            rows = doctor.run(self.st, offline=True)
        row = next(r for r in rows if r and r['id'] == 'auto-update')
        self.assertIn('仍是 '+__version__, str(row))
        self.assertNotIn('回滚也没成功', str(row))
        self.assertNotIn('legacy-reinstall-command', str(row))

    def rollback_verify(self, broken=False, failure='health'):
        self.prepare()
        def runner(argv):
            if str(self.new) in argv:
                return subprocess.CompletedProcess(argv, 1, '', 'error: No space left on device')
            if '--version' in argv:
                return subprocess.CompletedProcess(argv, int(broken), '' if broken else 'agentj '+__version__, '')
            if 'doctor' in argv:
                if failure == 'spawn':
                    raise FileNotFoundError('private executable')
                return subprocess.CompletedProcess(argv, 1, '{"checks":[{"id":"main-core","status":"fail"}]}', '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        def restart(version):
            if failure == 'service':
                raise ValueError('restart_failed')
        return au.run(self.st, runner=runner, restart=restart)

    def test_rollback_verify_failure_accepts_runnable_old_version(self):
        result = self.rollback_verify()
        self.assertEqual(result['result'], 'rolled_back')
        self.assertEqual(result['reason'], 'disk_full')
        rec = ud.read(self.st)
        self.assertEqual(rec['result'], 'rolled_back')
        self.assertEqual(rec['rollback_failure'], 'doctor_failed')
        self.assertEqual(rec['rollback_check'], 'version_unchanged')
        self.assertNotIn('回滚也没成功', au.notice(result))

    def test_rollback_verify_spawn_or_service_failure_accepts_old(self):
        for failure in ('spawn', 'service'):
            with self.subTest(failure=failure):
                result = self.rollback_verify(failure=failure)
                self.assertEqual(result['result'], 'rolled_back')
                self.assertEqual(ud.read(self.st)['rollback_failure'],
                                 'installer_missing' if failure == 'spawn' else 'service_restart_failed')

    def test_broken_old_version_still_requires_recovery(self):
        result = self.rollback_verify(broken=True)
        self.assertEqual((result['result'], result['reason']), ('failed', 'rollback_failed'))
        self.assertIn('recover', result)
        self.assertIn('回滚也没成功', au.notice(result))

    def test_installer_unclassified_exception_is_fixed_and_redacted(self):
        self.prepare()
        def runner(argv):
            if str(self.new) in argv:
                raise subprocess.SubprocessError('private argv and URL')
            if '--version' in argv:
                return subprocess.CompletedProcess(argv, 0, 'agentj '+__version__, '')
            return subprocess.CompletedProcess(argv, 1, '', UV_ERR)
        result = au.run(self.st, runner=runner, restart=lambda v: None)
        self.assertEqual(result['result'], 'rolled_back')
        rec = ud.read(self.st)
        self.assertEqual(rec['reason'], 'uv_failed')
        self.assertEqual(rec['rollback_failure'], 'permission_denied')
        body = ud.body(self.sent[-1])
        self.assertIn('rollback failure: permission_denied', body)
        self.assertIn('Permission denied', body)
        for secret in ('private argv', FAKE_GH, str(self.old), 'winuser'):
            self.assertNotIn(secret, body)
        self.assertNotIn('rollback_tail', ud.heartbeat_field(self.st))

    def test_old_rollback_journal_exception_type_is_classified(self):
        self.prepare('rollback')
        au.write(self.st, {**self.job, 'failure': 'CalledProcessError'}, au.JOB)
        result = au.run(self.st, runner=lambda a: subprocess.CompletedProcess(a, 1, '', ''), restart=lambda v: None)
        self.assertEqual(result['reason'], 'rollback_failed')
        self.assertEqual(ud.read(self.st)['reason'], 'uv_failed')

    def test_prepared_minisign_exception_never_installs_or_recovers(self):
        self.prepare()
        with patch.object(au.minisign, 'verify', side_effect=au.minisign.MinisignError('private signature detail')):
            runner = Mock(side_effect=AssertionError('must not install'))
            result = au.run(self.st, runner=runner, restart=lambda v: None)
        self.assertNotIn('recover', result)
        self.assertEqual(ud.read(self.st)['reason'], 'signature_or_hash_mismatch')
        self.assertEqual(result['phase'], 'prepared')
        runner.assert_not_called()

    def test_legacy_exception_journal_022_to_025_and_real_report_sender(self):
        self.prepare('rollback')
        au.write(self.st, {**self.job, 'from': '0.22.1a1', 'to': '0.25.4a1', 'failure': 'FileNotFoundError'}, au.JOB)
        def runner(argv):
            if '--version' in argv:
                return subprocess.CompletedProcess(argv, 0, 'agentj 0.22.1a1', '')
            return subprocess.CompletedProcess(argv, 2, '', UV_ERR)
        result = au.run(self.st, runner=runner, restart=lambda v: None)
        self.assertEqual((result['result'], result['reason']), ('rolled_back', 'installer_missing'))
        sent = []
        def sender(body):
            sent.append(body)
            return 0, 'st_' + 'a'*22
        with patch.dict(os.environ, {ud.REPORT_ENV: ''}):
            rep = ud.report(self.st, self.sent[-1], send=sender, kind='uv')
        self.assertEqual(rep['status'], 'sent')
        self.assertIn('from: 0.22.1a1 · to: 0.25.4a1', sent[0])
        self.assertIn('reason: installer_missing', sent[0])
        self.assertIn('rollback failure: permission_denied', sent[0])
        self.assertIn('Permission denied', sent[0])
        self.assertNotIn(FAKE_GH, sent[0])
        self.assertNotIn(str(self.old), sent[0])
        self.assertNotIn('recover:', sent[0])

    def test_phase_wins_over_reason_for_owner_sentence(self):
        res = {'from': '0.22.1a1', 'to': '0.25.4a1', 'reason': 'signature_or_hash_mismatch',
               'result': 'failed', 'phase': 'rollback', 'recover': 'local command'}
        self.assertNotIn('仍是', ud.owner_sentence(res, {'status': 'off'}))
        self.assertIn('回滚也没成功', ud.owner_sentence(res, {'status': 'off'}))


    def test_resumed_rollback_keeps_supported_installer_category(self):
        self.prepare('rollback')
        for reason in ('disk_full', 'python_incompatible', 'python_missing', 'dependency_conflict',
                       'dependency_download_failed', 'self_check_failed'):
            with self.subTest(reason=reason):
                au.write(self.st, {**self.job, 'failure': reason, 'diagnostic':
                         {'failure': reason, 'step': 'install', 'stderr_tail': 'first diagnostic'}}, au.JOB)
                au.run(self.st, runner=lambda a: subprocess.CompletedProcess(a, 1, '', 'rollback failed'),
                       restart=lambda v: None)
                rec = ud.read(self.st)
                self.assertEqual(rec['reason'], reason)
                self.assertEqual(rec['stderr_tail'], 'first diagnostic')
