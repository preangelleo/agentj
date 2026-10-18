"""P379 contract: preserve the first safe installer diagnosis across rollback failure.

No new authority, network or state owner. Recovery command is owner-local only;
support receives fixed categories and bounded redacted installer stderr.
"""
import _hermetic  # noqa: F401
import hashlib
import json
import subprocess
import unittest
from unittest import mock

from test_p277_upgrade_diag import _Env, INFO, NEW, UV_ERR, FAKE_GH
from agentj import __version__, auto_update, upgrade_diag, update


class Tail(unittest.TestCase):
    def test_redacts_private_key_before_choosing_last_lines(self):
        secret = 'private key body line'
        raw = '-----BEGIN ' + 'PRIVATE KEY-----\n' + (secret + '\n') * 30 + '-----END ' + 'PRIVATE KEY-----'
        self.assertNotIn(secret, upgrade_diag.tail(raw))

    def test_redacts_credential_before_line_length_truncation(self):
        # Truncation through the value must not turn an originally identifiable secret into plain text.
        raw = 'x ' * 115 + 'token=' + 'short-secret-value'
        self.assertNotIn('shor', upgrade_diag.tail(raw))


class InstallerPath(_Env):
    def binary(self, directory, name='uv'):
        path = self.st.root.parent / directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o700)
        return str(path)

    def test_missing_service_path_uses_default_uv_for_install_and_recover(self):
        uv = self.binary('.local/bin')
        with mock.patch.dict('os.environ', {'PATH': '/usr/bin:/bin'}), mock.patch('shutil.which', return_value=None):
            cmd = auto_update.install_commands(INFO, '/old.whl')[0]
            self.assertEqual(cmd[0], uv)
            self.assertEqual(update.commands(INFO)[0][0], uv)
            self.assertIn(uv, update.recover_of(INFO, '/old.whl', 'failed')['recover'])
            self.assertEqual(subprocess.run(cmd).returncode, 0)

    def test_path_executable_wins_over_default(self):
        self.binary('.local/bin')
        with mock.patch('shutil.which', return_value='/custom/uv'):
            self.assertEqual(auto_update.install_commands(INFO, '/old.whl')[0][0], '/custom/uv')

    def test_legacy_uv_default_and_pipx_default(self):
        uv = self.binary('.cargo/bin')
        px = self.binary('.local/bin', 'pipx')
        with mock.patch('shutil.which', return_value=None):
            self.assertEqual(update.commands(INFO)[0][0], uv)
            info = {**INFO, 'kind': 'pipx'}
            self.assertEqual(auto_update.install_commands(info, '/old.whl')[0][0], px)
            self.assertEqual(update.commands(info)[0][0], px)

    def test_missing_or_nonexecutable_default_keeps_named_command(self):
        uv = self.binary('.local/bin')
        from pathlib import Path
        Path(uv).chmod(0o600)
        with mock.patch('shutil.which', return_value=None):
            self.assertEqual(update.commands(INFO)[0][0], 'uv')


class ShellApply(_Env):
    def test_spawn_and_rollback_failure_keep_safe_category_and_recover(self):
        old = self.st.root / 'old.whl'
        old.write_bytes(b'w')
        old.with_suffix('.verified.json').write_text(json.dumps({'sha256': hashlib.sha256(b'w').hexdigest()}))
        with mock.patch.object(update, 'install_kind', return_value=INFO), \
                mock.patch.object(update, 'writable', return_value=True):
            result = update.apply(self.st, NEW, fenced=False, svc_on=False,
                check_fn=lambda: {'status': 'newer', 'latest': NEW},
                plan_fn=lambda st, info, target: [['uv', 'tool', 'install', '/new.whl']],
                rollback_fn=lambda st: old,
                run=mock.Mock(side_effect=FileNotFoundError('/private/path/secret')))
        self.assertEqual((result['reason'], result['rollback']), ('uv_failed', 'failed'))
        self.assertIn('recover', result)
        rec = upgrade_diag.read(self.st)
        self.assertIn('FileNotFoundError', rec['stderr_tail'])
        self.assertNotIn('/private/path', str(rec))
        self.assertNotIn(str(self.st.root), upgrade_diag.body(result))


class Worker(_Env):
    def attempt(self, failure):
        old, new = self.st.root / 'old.whl', self.st.root / 'new.whl'
        for p in (old, new):
            p.write_bytes(b'w')
            p.with_suffix('.verified.json').write_text(json.dumps({'sha256': hashlib.sha256(b'w').hexdigest()}))
        auto_update.write(self.st, {'attempt': 'a', 'from': __version__, 'to': NEW, 'install': INFO,
            'argv': ['agentj'], 'old': str(old), 'new': str(new), 'phase': 'prepared'}, auto_update.JOB)
        def runner(argv):
            if str(new) in argv:
                if isinstance(failure, Exception):
                    raise failure
                return subprocess.CompletedProcess(argv, 1, '', UV_ERR)
            return subprocess.CompletedProcess(argv, 1, '', 'rollback second failure')
        self.sent = []
        with mock.patch.object(auto_update, 'enabled', return_value=True), \
                mock.patch.object(auto_update.minisign, 'verify', return_value=f'agentj-host/v1 version={NEW} sha256={hashlib.sha256(b"w").hexdigest()}'), \
                mock.patch.object(upgrade_diag, 'after_failure', side_effect=lambda st, res, **kw: self.sent.append(res) or res), \
                mock.patch('agentj.phone_update.release'), mock.patch('agentj.service_recovery.cleanup_job'):
            return auto_update.run(self.st, runner=runner, restart=lambda v: None)

    def test_rollback_does_not_replace_install_stderr_and_recover_is_local(self):
        result = self.attempt(None)
        self.assertEqual(result['reason'], 'rollback_failed')
        rec = upgrade_diag.read(self.st)
        self.assertEqual(rec['reason'], 'uv_failed')
        self.assertIn('Permission denied', rec['stderr_tail'])
        self.assertNotIn('rollback second failure', rec['stderr_tail'])
        self.assertNotIn(FAKE_GH, rec['stderr_tail'])
        self.assertNotIn('winuser', rec['stderr_tail'])
        self.assertIn('old.whl', result['recover'])
        self.assertIn(result['recover'], auto_update.notice(result))
        self.assertNotIn('recover', upgrade_diag.body(self.sent[0]))
        self.assertNotIn(str(self.st.root), upgrade_diag.body(self.sent[0]))

    def test_install_timeout_captures_partial_stderr_not_exception_command(self):
        result = self.attempt(subprocess.TimeoutExpired(['private-command'], 900, stderr=UV_ERR.encode()))
        rec = upgrade_diag.read(self.st)
        self.assertEqual((rec['reason'], rec['step']), ('uv_failed', 'install'))
        self.assertIn('Permission denied', rec['stderr_tail'])
        self.assertNotIn('private-command', str(rec))
        self.assertIn('recover', result)

    def test_install_spawn_failure_is_install_category_and_safe_type(self):
        self.attempt(FileNotFoundError('/private/path/secret'))
        rec = upgrade_diag.read(self.st)
        self.assertEqual(rec['reason'], 'uv_failed')
        self.assertIn('FileNotFoundError', rec['stderr_tail'])
        self.assertNotIn('/private/path', str(rec))


if __name__ == '__main__':
    unittest.main()
