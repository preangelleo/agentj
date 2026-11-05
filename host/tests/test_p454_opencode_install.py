"""P454: recommend v2 without forcing existing v1 installations to migrate."""
import _hermetic
import tempfile
import unittest
from unittest import mock
from agentj import ark, doctor, binaries, harness
from test_l1 import _state

class InstallAdvice(unittest.TestCase):
    def test_ark_missing_cli_recommends_v2_in_both_languages(self):
        with tempfile.TemporaryDirectory() as root:
            result = ark.switch(_state(root), detector=lambda: {})
        self.assertEqual(result['install'], 'npm i -g @opencode/cli')
        for lang in ('zh', 'en'):
            self.assertIn('npm i -g @opencode/cli', ark.say_switch(result, lang))

    def test_doctor_keeps_v1_usable_and_explains_optional_upgrade(self):
        with tempfile.TemporaryDirectory() as root:
            st = _state(root)
            cfg = {'kind': 'opencode', 'dir': root, 'model': 'fixture/model'}
            with mock.patch.object(st, 'agent_config', return_value=cfg), \
                mock.patch('agentj.service.effective_binary_environment', return_value={}), \
                mock.patch('agentj.binaries.resolve', return_value={'path': '/var/tmp/opencode', 'wrapper': False, 'reason': 'test'}), \
                mock.patch('agentj.doctor._version_of', return_value='1.19.3'), \
                mock.patch('agentj.doctor._agent_bin', return_value='/var/tmp/opencode'), \
                mock.patch('agentj.harness.opencode_login', return_value=('ok', 'fixture')), \
                mock.patch('agentj.names.ctl_call', return_value={'known': True, 'selected_connected': True}):
                row = doctor.check_agent_cli(st, {})
        self.assertEqual(row['status'], 'warn', row)  # Connected metadata never proves key validity.
        self.assertIn('1.x 也能用', row['summary'])
        self.assertIn('no automatic major upgrade', row['summary'])
        self.assertIn('npm i -g @opencode/cli', row['summary'])


class NativeDiscovery(unittest.TestCase):
    def test_installed_native_binary_works_before_shell_path_refresh(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            exe = Path(root) / '.opencode/bin/opencode'
            exe.parent.mkdir(parents=True)
            exe.write_text('#!/bin/sh\necho 2.1.2\n')
            exe.chmod(0o700)
            env = {'HOME': root, 'PATH': '/nonexistent'}
            self.assertIn(str(exe), binaries.installations('opencode', env))
            self.assertEqual(binaries.resolve('opencode', env)['path'], str(exe))
            with mock.patch.dict('os.environ', env, clear=True):
                self.assertEqual(harness.agent_bin('opencode'), str(exe))
                self.assertTrue(harness.opencode_v2(harness.version_of(str(exe))))

    def test_explicit_and_path_choices_remain_authoritative(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            native = Path(root) / '.opencode/bin/opencode'
            other = Path(root) / 'other/opencode'
            for exe in (native, other):
                exe.parent.mkdir(parents=True)
                exe.write_text('#!/bin/sh\nexit 0\n')
                exe.chmod(0o700)
            env = {'HOME': root, 'PATH': str(other.parent)}
            self.assertEqual(binaries.resolve('opencode', env)['path'], str(other))
            env.update(AGENTJ_OPENCODE_BIN=str(Path(root) / 'missing'))
            self.assertIsNone(binaries.resolve('opencode', env)['path'])
            native.chmod(0o600)
            self.assertIsNone(binaries.resolve('opencode', {'HOME':root, 'PATH':'/nonexistent'})['path'])
