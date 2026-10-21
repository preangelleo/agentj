"""P397: actionable installer evidence and uv recovery outside a deleted tool env."""
import _hermetic  # noqa: F401
import errno
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_p277_upgrade_diag import _Env, INFO, NEW, UV_ERR, FAKE_GH
from agentj import __version__, auto_update as au, update, upgrade_diag as ud


class Categories(unittest.TestCase):
    def test_exception_categories(self):
        for exc, expected in (
            (subprocess.CalledProcessError(2, ['private']), 'uv_failed'),
            (FileNotFoundError('/private/secret'), 'installer_missing'),
            (PermissionError('/private/secret'), 'permission_denied'),
            (OSError(errno.EROFS, '/private/secret'), 'read_only_filesystem'),
            (subprocess.TimeoutExpired(['private'], 900), 'installer_timeout'),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(ud.category(exc), expected)
                self.assertEqual(ud.category(type(exc).__name__), 'os_error' if type(exc) is OSError else expected)


    def test_metadata_never_accepts_raw_argv_or_recover(self):
        res = {'step': 'install', 'result': 'failed', 'to': NEW, 'reason': 'uv_failed',
               'command': 'uv secret-token /private/path', 'exit': True, 'exception': 'secret',
               'recover': 'private recovery path'}
        rec = ud.entry(res)
        self.assertNotIn('command', rec)
        self.assertNotIn('exit', rec)
        self.assertNotIn('recover', rec)
        self.assertNotIn('private', ud.body(res))


class Worker(_Env):
    def prepare(self, phase='prepared'):
        self.old, self.new = self.st.root/'old.whl', self.st.root/'new.whl'
        digest = hashlib.sha256(b'w').hexdigest()
        for p in (self.old, self.new):
            p.write_bytes(b'w')
            p.with_suffix('.verified.json').write_text(json.dumps({'sha256': digest}))
        self.job = {'from': __version__, 'to': NEW, 'old': str(self.old), 'new': str(self.new),
                    'install': INFO, 'argv': ['fixture-agentj'], 'phase': phase}
        au.write(self.st, self.job, au.JOB)
        self.sent = []
        for p in (patch.object(au, 'enabled', return_value=True),
                  patch.object(au.minisign, 'verify', return_value=f'agentj-host/v1 version={NEW} sha256={digest}'),
                  patch.object(ud, 'after_failure', side_effect=lambda st, res, **kw: self.sent.append(res) or res),
                  patch('agentj.service_recovery.cleanup_job')):
            p.start(); self.addCleanup(p.stop)

    def attempt(self, failure, rollback_ok=False):
        self.prepare()
        def run(argv):
            if str(self.new) in argv:
                if isinstance(failure, BaseException):
                    raise failure
                return subprocess.CompletedProcess(argv, 2, '', failure)
            if '--version' in argv:
                return subprocess.CompletedProcess(argv, 0, 'agentj '+__version__, '')
            if 'doctor' in argv:
                return subprocess.CompletedProcess(argv, 0, '{"checks":[{"id":"version","status":"ok"}]}', '')
            return subprocess.CompletedProcess(argv, 0 if rollback_ok else 1, '', 'rollback second failure')
        return au.run(self.st, runner=run, restart=lambda v: None)

    def test_called_process_error_has_exit_command_and_redacted_tail(self):
        r = self.attempt(subprocess.CalledProcessError(7, ['private-command', FAKE_GH], stderr=UV_ERR))
        rec = ud.read(self.st)
        self.assertEqual((rec['reason'], rec['exit']), ('permission_denied', 7))
        self.assertEqual(rec['command'], 'uv tool install --force <wheel>')
        self.assertIn('Permission denied', rec['stderr_tail'])
        self.assertIn('recover', r)
        body = ud.body(self.sent[0])
        self.assertIn('exit: 7', body)
        self.assertIn('uv tool install', body)
        for value in (FAKE_GH, 'private-command', str(self.old)):
            self.assertNotIn(value, str(rec)+body)
        # P423 #192: the rollback's own output is kept apart; it never replaces the first failure's tail
        self.assertNotIn('rollback second failure', rec['stderr_tail'])
        self.assertIn('rollback second failure', rec['rollback_tail'])
        self.assertNotIn('command', ud.heartbeat_field(self.st))

    def test_readonly_and_missing_installer_are_actionable_with_recover(self):
        for exc, reason in ((OSError(errno.EROFS, 'private path'), 'read_only_filesystem'),
                            (FileNotFoundError('private path'), 'installer_missing')):
            with self.subTest(reason=reason):
                r = self.attempt(exc)
                rec = ud.read(self.st)
                self.assertEqual(rec['reason'], reason)
                self.assertEqual(rec['exception'], type(exc).__name__)
                self.assertIn('recover', r)
                self.assertNotIn('private path', str(rec))

    def test_nonzero_readonly_stderr_and_rollback_success(self):
        r = self.attempt('error: Read-only file system (os error 30)', rollback_ok=True)
        rec = ud.read(self.st)
        self.assertEqual((r['result'], rec['reason'], rec['exit']), ('rolled_back', 'read_only_filesystem', 2))
        self.assertIn('recover', r)  # every failed attempt has an owner-local fallback

    def test_interrupted_job_has_recover_and_concrete_reason(self):
        self.prepare('installing')
        r = au.run(self.st, runner=lambda a: subprocess.CompletedProcess(a, 1, '', ''), restart=lambda v: None)
        self.assertIn('recover', r)
        self.assertEqual(ud.read(self.st)['reason'], 'worker_interrupted')


    def test_real_missing_uv_on_service_path_has_specific_diagnosis(self):
        self.prepare()
        empty = self.st.root/'empty-path'; empty.mkdir()
        with patch.dict(os.environ, {'PATH': str(empty)}):
            r = au.run(self.st, restart=lambda v: None)
        self.assertEqual(r['reason'], 'rollback_failed')
        self.assertIn('old.whl', r['recover'])
        rec = ud.read(self.st)
        self.assertEqual((rec['reason'], rec['exception']), ('installer_missing', 'FileNotFoundError'))
        self.assertNotIn('exit', rec)  # no process started; do not invent an exit code

    def test_old_rollback_journal_without_diagnostic_is_not_unknown(self):
        self.prepare('rollback')
        au.write(self.st, {**self.job, 'failure': 'install_failed'}, au.JOB)
        au.run(self.st, runner=lambda a: subprocess.CompletedProcess(a, 1, '', ''), restart=lambda v: None)
        self.assertEqual(ud.read(self.st)['reason'], 'uv_failed')

    def test_resumed_rollback_keeps_first_diagnostic(self):
        self.prepare('rollback')
        au.write(self.st, {**self.job, 'failure': 'read_only_filesystem',
            'diagnostic': {'failure': 'read_only_filesystem', 'step': 'verify', 'exit': 2,
                           'command': 'uv tool install --force <wheel>', 'stderr_tail': 'Read-only file system'}}, au.JOB)
        au.run(self.st, runner=lambda a: subprocess.CompletedProcess(a, 1, '', 'second failure'), restart=lambda v: None)
        rec = ud.read(self.st)
        self.assertEqual((rec['reason'], rec['exit']), ('read_only_filesystem', 2))
        self.assertEqual(rec['stderr_tail'], 'Read-only file system')
        self.assertEqual(rec['step'], 'verify')

    def test_deleted_package_still_persists_failure_and_local_recover(self):
        lib = self.st.root/'disposable-lib'
        shutil.copytree(Path(au.__file__).parent, lib/'agentj', ignore=shutil.ignore_patterns('__pycache__'))
        script = r"""
import sys, json, hashlib, shutil, subprocess
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agentj import auto_update as au, update, upgrade_diag as ud, __version__
from agentj.state import State
st = State(Path(sys.argv[2])); st.init()
old, new = st.root/'old.whl', st.root/'new.whl'
for p in (old, new):
 p.write_bytes(b'w'); p.with_suffix('.verified.json').write_text(json.dumps({'sha256':hashlib.sha256(b'w').hexdigest()}))
au.write(st, {'from':__version__,'to':'9.9.9','install':{'kind':'uv','where':'/missing'},
 'old':str(old),'new':str(new),'argv':['missing-agentj'],'phase':'prepared'}, au.JOB)
au.minisign.verify = lambda *a: 'agentj-host/v1 version=9.9.9 sha256='+hashlib.sha256(b'w').hexdigest()
au.enabled = lambda st: True
from agentj import service_recovery
service_recovery.cleanup_job = lambda st: None
assert 'agentj.humane' not in sys.modules
assert Path(au.__file__).is_relative_to(sys.argv[1])
def run(argv):
 if str(new) in argv:
  shutil.rmtree(sys.argv[1])
 raise FileNotFoundError('private-error-content')
r = au.run(st, runner=run, restart=lambda v: None)
assert r['reason']=='rollback_failed', r
assert 'recover' in r, r
assert ud.read(st)['reason']=='installer_missing'
assert au.read(st, au.JOB)['phase']=='done'
assert 'private-error-content' not in json.dumps(ud.read(st))
"""
        done = subprocess.run([sys.executable, '-c', script, str(lib), str(self.st.root/'child')],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_real_process_recovers_after_fake_uv_deletes_tool_environment(self):
        self.prepare()
        envdir = self.st.root/'tool-env'
        envdir.mkdir()
        (envdir/'running-python').write_text('present')
        uv = self.st.root/'uv'
        # Executable lives OUTSIDE the disposable tool env, as the real uv binary does.
        uv.write_text('#!'+sys.executable+'\n' +
            'import pathlib, shutil, sys\n' +
            f'env = pathlib.Path({str(envdir)!r})\n' +
            f'new = {str(self.new)!r}\n' +
            'if new in sys.argv:\n shutil.rmtree(env); print("install failed", file=sys.stderr); sys.exit(2)\n' +
            'assert "--python" in sys.argv, "recovery must pin an independent interpreter"\n' +
            'assert pathlib.Path(sys.argv[sys.argv.index("--python")+1]).is_file()\n' +
            'env.mkdir(); (env/"restored").write_text("ok")\n')
        uv.chmod(0o700)
        def run(argv):
            if '--version' in argv or 'doctor' in argv:
                self.assertTrue((envdir/'restored').exists())
                out = 'agentj '+__version__ if '--version' in argv else '{"checks":[{"id":"version","status":"ok"}]}'
                return subprocess.CompletedProcess(argv, 0, out, '')
            return au.quiet(argv)
        with patch.object(sys, "executable", str(envdir/"running-python")), \
                patch.object(update, 'installer_binary', return_value=str(uv)):
            r = au.run(self.st, runner=run, restart=lambda v: None)
        self.assertEqual(r['result'], 'rolled_back')
        self.assertTrue((envdir/'restored').is_file())


if __name__ == '__main__':
    unittest.main()
