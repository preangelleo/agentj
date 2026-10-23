"""P490 RESUME2: a refused rollback must never become a manual downgrade hint."""
import _hermetic  # noqa: F401
import json
import subprocess
from unittest import mock

from agentj import update, upgrade_diag
from test_p277_upgrade_diag import _Env


class Recovery(_Env):
    def attempt(self, entry, kind, journal):
        old = self.st.root / 'agentj-0.25.4a1-py3-none-any.whl'
        old.write_bytes(b'isolated old wheel')
        if journal:
            self.st.write_private(self.st.root / journal, json.dumps({'epoch': 1}).encode())
        info = {'kind': kind, 'where': '/synthetic/tool', 'legacy': False}
        calls, progress = [], []
        def run(argv, **kw):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 2, '', 'isolated installer failure')
        kw = dict(check_fn=lambda: {'status': 'newer', 'latest': '0.25.6a1'},
                  plan_fn=lambda st, info, target: [['synthetic-install', '--force', 'new-wheel']],
                  run=run, say=progress.append, svc_on=False, rollback_fn=lambda st: old)
        with mock.patch.object(update, '__version__', '0.25.4a1'), \
                mock.patch.object(update, 'install_kind', return_value=info), \
                mock.patch.object(update, 'writable', return_value=True), \
                mock.patch('agentj.cloud.read_cloud', return_value={'host_id': 'synthetic'}), \
                mock.patch.object(update, 'installer_binary', side_effect=lambda kind: kind):
            if entry == 'apply':
                res = update.apply(self.st, '0.25.6a1', fenced=False, **kw)
            else:
                res = update.authorized_apply(self.st, 'AJUP-11111-11111-11111-11111', '0.25.6a1',
                                              auth_fn=lambda *a: {'status': 'ok'}, **kw)
        return res, calls, progress, old

    def test_guarded_explicit_paths_emit_reason_instead_of_old_wheel_command(self):
        for entry in ('apply', 'authorized_apply'):
            for kind in ('uv', 'pipx', 'pip'):
                for journal in ('credential_revocations.json', 'security_rotation.json'):
                    with self.subTest(entry=entry, kind=kind, journal=journal):
                        res, calls, progress, old = self.attempt(entry, kind, journal)
                        self.assertEqual(res['rollback'], 'failed')
                        self.assertNotIn('recover', res)
                        self.assertEqual(res.get('recovery_blocked'), 'revocation_downgrade_blocked')
                        text = update.result_block(res)
                        self.assertIn('吊销', text)
                        self.assertIn('revocation', text)
                        for lang in ('zh', 'en'):
                            sentence = upgrade_diag.owner_sentence(res, {'status': 'off'}, lang)
                            self.assertIn('吊销' if lang == 'zh' else 'revocation', sentence)
                        self.assertNotIn(str(old), json.dumps(res) + text + '\n'.join(progress))
                        self.assertEqual(len(calls), 1)  # only the new install, never the blocked rollback
                        (self.st.root / journal).unlink()

    def test_no_journal_preserves_failed_rollback_recovery_command(self):
        for entry in ('apply', 'authorized_apply'):
            with self.subTest(entry=entry):
                res, calls, progress, old = self.attempt(entry, 'uv', None)
                self.assertIn('tool install --force ' + str(old), res['recover'])
                self.assertNotIn('recovery_blocked', res)

    def test_security_journal_suppresses_unknown_and_compatible_cached_wheel_hints(self):
        self.st.write_private(self.st.root / 'credential_revocations.json', b'{"epoch":1}')
        info = {'kind': 'pip', 'where': '/synthetic/tool', 'legacy': False}
        with mock.patch('agentj.auto_update.install_commands') as commands:
            for wheel in ('/unknown.whl', '/agentj-0.25.4a1.whl', '/agentj-0.25.5a1.whl'):
                with self.subTest(wheel=wheel):
                    r = update.recover_of(info, wheel, 'failed', st=self.st)
                    self.assertEqual(r, {'recovery_blocked': 'revocation_downgrade_blocked'})
            commands.assert_not_called()

    def test_automatic_failure_keeps_suppressing_old_wheel_in_both_languages(self):
        from test_p83_auto_update import Worker
        from agentj import auto_update
        case = Worker('test_upgrade_failed_restores_cached_old_wheel')
        case.setUp()
        try:
            case.st.write_private(case.st.root / 'credential_revocations.json', b'{"epoch":1}')
            import test_p83_auto_update as fixture
            # Pin an actually revocation-unaware baseline independently of the candidate version.
            with mock.patch.object(fixture, '__version__', '0.25.4a1'), mock.patch.object(auto_update, '__version__', '0.25.4a1'):
                res = case.run_worker(fail=True)
            self.assertNotIn('recover', res)
            self.assertNotEqual(res['result'], 'rolled_back')
            self.assertEqual(case.installed, ['99.0.0a1'])
            for lang in ('zh', 'en'):
                self.assertNotIn('--force', auto_update.notice(res, lang))
        finally:
            case.doCleanups()
