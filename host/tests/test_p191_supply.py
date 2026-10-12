"""P191 supply chain: update redirects never leave HTTPS; recovery jobs keep env values off the systemd-run command line."""
import _hermetic  # noqa: F401
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import auto_update, service, service_recovery, update
from agentj.state import State


class Redirects(unittest.TestCase):
    def follow(self, origin, target):
        return update.SecureRedirect().redirect_request(urllib.request.Request(origin), None, 302, 'Found', {}, target)

    def test_https_targets_followed(self):
        for origin in ('https://agentj.app/latest', 'http://127.0.0.1:8000/latest'):
            self.assertEqual(self.follow(origin, 'https://cdn.example.invalid/x').full_url, 'https://cdn.example.invalid/x')

    def test_every_non_https_target_refused(self):
        for origin, target in [('https://agentj.app/latest', 'http://agentj.app/latest'),
                               ('https://agentj.app/latest', 'http://127.0.0.1/latest'),
                               ('https://agentj.app/latest', 'ftp://agentj.app/latest'),
                               ('https://agentj.app/latest', 'file:///etc/passwd'),
                               ('http://127.0.0.1:8000/latest', 'http://evil.example.invalid/latest'),   # dev origin must not leave loopback
                               ('http://localhost/latest', 'http://agentj.app/latest')]:
            with self.subTest(origin=origin, target=target), self.assertRaisesRegex(ValueError, '^insecure_update_redirect$'):
                self.follow(origin, target)

    def test_local_development_origin_may_stay_on_loopback(self):
        self.assertEqual(self.follow('http://localhost:8000/a', 'http://127.0.0.1:8000/b').full_url, 'http://127.0.0.1:8000/b')

    def test_both_fetch_paths_share_the_one_opener(self):
        with patch.object(update, 'open_download', side_effect=ValueError('insecure_update_redirect')) as opener:
            with self.assertRaisesRegex(ValueError, 'insecure_update_redirect'):
                auto_update.download('https://agentj.app/dl/x.whl', 10)
            with patch.object(update, 'latest_url', return_value='https://agentj.app/latest'):
                self.assertEqual(update.fetch_latest(), (None, 'network'))
        self.assertEqual(opener.call_count, 2)


class RecoveryCommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / 'st'); self.st.init()
        env = patch.dict(os.environ, {'HOME': self.tmp.name, 'AGENTJ_STATE_DIR': str(self.st.root), 'AGENTJ_SERVICE_NAME': 'aj.p191.test',
                                      'XDG_CONFIG_HOME': self.tmp.name + '/cfg', 'HTTPS_PROXY': 'http://proxy.invalid:3128',
                                      'P191_UNRELATED_SECRET': 'must-not-be-forwarded'})
        env.start(); self.addCleanup(env.stop)

    def test_systemd_run_gets_names_only_and_values_through_its_environment(self):
        with patch.object(service, 'platform', return_value='linux'), \
             patch.object(service_recovery.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            service_recovery.launch_job(self.st, ['agentj', 'service', 'install'])
        argv, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(argv[0], 'systemd-run')
        sets = [a for a in argv if a.startswith('--setenv=')]
        self.assertTrue(sets)
        self.assertTrue(all('=' not in a[len('--setenv='):] for a in sets), sets)   # no value on the command line
        names = {a[len('--setenv='):] for a in sets}
        for name in ('PATH', 'HOME', 'AGENTJ_STATE_DIR', 'AGENTJ_JOB_LABEL', 'XDG_CONFIG_HOME', 'HTTPS_PROXY'):
            self.assertIn(name, names)
        self.assertNotIn('P191_UNRELATED_SECRET', names)
        joined = ' '.join(argv)
        for value in (self.tmp.name, 'proxy.invalid', 'must-not-be-forwarded'):
            self.assertNotIn(value, joined)
        child = kw['env']   # systemd-run copies each named value from here
        self.assertEqual(child['XDG_CONFIG_HOME'], self.tmp.name + '/cfg')
        self.assertEqual(child['AGENTJ_STATE_DIR'], str(self.st.root))
        self.assertTrue(child['AGENTJ_JOB_LABEL'].startswith('net.agentj.recovery.'))
        self.assertEqual(argv[-3:], ['agentj', 'service', 'install'])

    def test_credential_proxy_never_reaches_the_job(self):
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://user:pw@proxy.invalid:3128'}), \
             patch.object(service, 'platform', return_value='linux'), \
             patch.object(service_recovery.subprocess, 'run', side_effect=[Mock(returncode=1), Mock(returncode=0)]) as run:
            service_recovery.launch_job(self.st, ['agentj'])
        first, bus = run.call_args_list[0], run.call_args_list[1]
        self.assertNotIn('--setenv=HTTPS_PROXY', first.args[0])
        self.assertFalse(any('proxy.invalid' in a for a in bus.args[0]))   # busctl fallback still carries only non-secret values


if __name__ == '__main__':
    unittest.main()
