"""P423 #166/#192: an automatic upgrade whose installer cannot start (no uv on the service PATH) must not be reported as
reason unknown + rollback_failed while the previous version is still installed and running. The rollback's own failure is
named (fixed category, its own redacted bounded tail), the installed version is re-checked, and a uv exit is classified
by what its output says."""
import _hermetic  # noqa: F401
import hashlib
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from test_p277_upgrade_diag import _Env, INFO, NEW, FAKE_GH
from agentj import __version__, auto_update as au, upgrade_diag as ud


class UvStderr(unittest.TestCase):
    def test_uv_exit_is_classified_by_its_output(self):
        for text, expected in (
            ("error: No space left on device (os error 28)", "disk_full"),
            ("× No solution found when resolving dependencies:\n  ╰─▶ Because the current Python version (3.10.12) does "
             "not satisfy Python>=3.11 and agentj==9.9.9 depends on Python>=3.11, we can conclude …", "python_incompatible"),
            ("error: No interpreter found for Python >=3.11 in managed installations or search path", "python_missing"),
            ("× No solution found when resolving dependencies:\n  ╰─▶ Because zxing-cpp>=2.3 has no wheels …", "dependency_conflict"),
            ("error: Failed to fetch: `https://pypi.org/simple/websockets/`\n  Caused by: dns error", "dependency_download_failed"),
            ("error: Permission denied (os error 13)", "permission_denied"),
            ("error: Read-only file system (os error 30)", "read_only_filesystem"),
            ("error: something new", "uv_failed"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(ud.installer_reason(stderr=ud.tail(text, ("H", "u"))), expected)
                self.assertIn(expected, ud.REASON_WORDS)


class Rollback(_Env):
    def prepare(self, phase='prepared', receipt=True):
        self.old, self.new = self.st.root/'old.whl', self.st.root/'new.whl'
        digest = hashlib.sha256(b'w').hexdigest()
        for p in (self.old, self.new):
            p.write_bytes(b'w')
            if receipt:
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

    def test_no_uv_on_service_path_is_rolled_back_not_unknown(self):
        """The reported shape: the installer never starts, for the upgrade or the rollback; nothing changed."""
        self.prepare()
        empty = self.st.root/'empty-path'; empty.mkdir()
        seen = []
        def run(argv):
            seen.append(argv)
            if argv[-1] == '--version':
                return subprocess.CompletedProcess(argv, 0, 'agentj '+__version__+'\n', '')
            return au.quiet(argv)          # the real spawn: FileNotFoundError, no uv anywhere
        with patch.dict(os.environ, {'PATH': str(empty)}):
            r = au.run(self.st, runner=run, restart=lambda v: None)
        self.assertEqual((r['result'], r['reason']), ('rolled_back', 'installer_missing'))
        self.assertIn('recover', r)
        rec = ud.read(self.st)
        self.assertEqual((rec['result'], rec['reason'], rec['step']), ('rolled_back', 'installer_missing', 'install'))
        self.assertEqual((rec['rollback_failure'], rec['rollback_check']), ('installer_missing', 'version_unchanged'))
        self.assertEqual(rec['exception'], 'FileNotFoundError')
        body = ud.body(self.sent[0])
        self.assertIn('rollback: ok', body)
        self.assertIn(f'rollback failure: installer_missing · installed version unchanged: still {__version__}', body)
        self.assertNotIn('unknown', body)
        hb = ud.heartbeat_field(self.st)
        self.assertEqual(set(hb), {'from', 'to', 'result', 'reason', 'at'})
        self.assertEqual(au.read(self.st)['result']['result'], 'rolled_back')
        self.assertIn(['fixture-agentj', '--version'], seen)

    def test_rollback_installer_output_is_kept_apart_redacted_and_bounded(self):
        self.prepare()
        noise = ''.join(f'rollback noise line {i} ' + 'x'*200 + '\n' for i in range(40))
        def run(argv):
            if str(self.new) in argv:
                return subprocess.CompletedProcess(argv, 2, '', 'error: first failure (exit 2)')
            if str(self.old) in argv:
                return subprocess.CompletedProcess(argv, 1, '', noise + f'token={FAKE_GH}\nerror: No space left on device (os error 28)\n')
            if argv[-1] == '--version':      # the tool env now answers with neither version: nothing to claim
                return subprocess.CompletedProcess(argv, 1, '', '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        r = au.run(self.st, runner=run, restart=lambda v: None)
        self.assertEqual((r['result'], r['reason']), ('failed', 'rollback_failed'))
        rec = ud.read(self.st)
        self.assertEqual((rec['result'], rec['reason'], rec['exit']), ('failed', 'uv_failed', 2))
        self.assertEqual(rec['stderr_tail'], 'error: first failure (exit 2)')     # the first failure's evidence untouched
        self.assertEqual(rec['rollback_failure'], 'disk_full')
        self.assertNotIn('rollback_check', rec)
        self.assertLessEqual(len(rec['rollback_tail']), ud.ROLLBACK_TAIL_MAX)
        self.assertIn('No space left on device', rec['rollback_tail'])
        body = ud.body(self.sent[0])
        self.assertIn('rollback: failed', body)
        self.assertIn('rollback failure: disk_full', body)
        self.assertIn('rollback installer stderr (last lines, redacted):', body)
        self.assertNotIn(FAKE_GH, json.dumps(rec) + body)
        self.assertNotIn(str(self.old), json.dumps(rec) + body)

    def test_rollback_that_fails_after_reinstalling_is_not_relabelled(self):
        """The old version is back but its self-check failed: that stays rollback_failed (a broken service is not hidden)."""
        self.prepare()
        def run(argv):
            if str(self.new) in argv:
                return subprocess.CompletedProcess(argv, 2, '', 'error: failed')
            if argv[-1] == '--version':
                return subprocess.CompletedProcess(argv, 0, 'agentj '+__version__, '')
            if 'doctor' in argv:
                return subprocess.CompletedProcess(argv, 3, 'not json', '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        r = au.run(self.st, runner=run, restart=lambda v: None)
        self.assertEqual(r['reason'], 'rollback_failed')
        rec = ud.read(self.st)
        self.assertEqual((rec['rollback_failure'], rec.get('rollback_check')), ('doctor_failed', None))

    def test_unreadable_upgrade_cache_has_a_fixed_reason(self):
        self.prepare(receipt=False)
        r = au.run(self.st, runner=lambda a: subprocess.CompletedProcess(a, 0, 'agentj '+__version__, ''),
                   restart=lambda v: None)
        rec = ud.read(self.st)
        self.assertEqual(rec['reason'], 'upgrade_cache_unreadable')
        self.assertEqual(rec['rollback_failure'], 'upgrade_cache_unreadable')
        self.assertEqual(r['result'], 'rolled_back')   # nothing was installed; the previous version still answers


if __name__ == '__main__':
    unittest.main()
