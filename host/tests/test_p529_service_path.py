"""P529: App runtime paths must never become daemon executable discovery paths."""
import _hermetic
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agentj import service

class StablePath(unittest.TestCase):
    def test_app_install_with_temporary_home_finds_stable_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            binary = home / '.local/bin/claude'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            env = {'HOME': tmp, 'PATH': f'/Applications/ChatGPT.app/Contents/Resources/codex-path:{tmp}/.codex/tmp/arg0-macos/test:/var/folders/tmp:/usr/bin:/bin'}
            with patch.dict(os.environ, env, clear=True):
                got, _ = service.service_env(env)
            self.assertEqual(got['AGENTJ_CLAUDE_BIN'], str(binary))
            self.assertIn(str(binary.parent), got['PATH'].split(':'))
            self.assertNotIn('.app/Contents/Resources', got['PATH'])
            self.assertNotIn('.codex/tmp', got['PATH'])
            self.assertNotIn('/var/folders', got['PATH'])
            self.assertEqual(len(got['PATH'].split(':')), len(set(got['PATH'].split(':'))))

    def test_doctor_and_scan_use_daemon_path_then_offer_reinstall(self):
        from agentj import doctor
        for plat in ('linux', 'macos'):
            with self.subTest(platform=plat), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                binary = home / '.local/bin/claude'
                binary.parent.mkdir(parents=True)
                binary.write_text('#!/bin/sh\nexit 0\n'); binary.chmod(0o755)
                env = {'HOME': tmp, 'XDG_CONFIG_HOME': tmp+'/cfg', 'PATH': str(binary.parent)+':/usr/bin:/bin'}
                with patch.dict(os.environ, env, clear=True), patch.object(service, 'platform', return_value=plat):
                    path = Path(service.unit_dir())/'p529.service' if plat == 'linux' else Path(service.plist_path('p529'))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    old = {'PATH': '/usr/bin:/bin'}
                    path.write_bytes(service.unit_text('p529', ['/bin/true'], old).encode() if plat == 'linux' else service.plist_bytes('p529', ['/bin/true'], old, '/tmp/log'))
                    svc = {'installed': True, 'name': 'p529', 'kind': 'systemd' if plat == 'linux' else 'launchd', 'active': 'active'}
                    self.assertEqual(service.effective_binary_environment(svc)['PATH'], old['PATH'])
                    self.assertIn('claude', service.path_missing_harnesses(svc))
                    result = doctor.check_service(svc)
                    self.assertEqual(result['status'], 'warn')
                    self.assertIn('agentj service install', str(result))
                    fixed, _ = service.service_env(env)
                    path.write_bytes(service.unit_text('p529', ['/bin/true'], fixed).encode() if plat == 'linux' else service.plist_bytes('p529', ['/bin/true'], fixed, '/tmp/log'))
                    self.assertEqual(service.path_missing_harnesses(svc), [])
                    self.assertEqual(doctor.check_service(svc)['status'], 'ok')

    def test_login_shell_captures_only_path_with_clean_environment(self):
        from agentj import service_path
        from types import SimpleNamespace
        import subprocess
        user = SimpleNamespace(pw_dir='/synthetic', pw_name='fixture', pw_shell='/bin/zsh')
        with patch.object(service_path.pwd, 'getpwuid', return_value=user), patch.object(subprocess, 'run') as run:
            run.return_value = SimpleNamespace(returncode=0, stdout=b'private startup chatter\0agentj-path\0/custom/bin:/usr/bin\0')
            result = service_path.login_path({'HOME':'/synthetic', 'SHELL':'/evil/shell', 'BASH_ENV':'/evil', 'TOKEN':'synthetic'})
            self.assertEqual(result, '/custom/bin:/usr/bin')
            call = run.call_args
            self.assertEqual(call.args[0][0], '/bin/zsh')
            self.assertNotIn('TOKEN', call.kwargs['env'])
            self.assertNotIn('BASH_ENV', call.kwargs['env'])
            self.assertEqual(call.kwargs['timeout'], 3)
            run.side_effect = subprocess.TimeoutExpired('shell', 3)
            self.assertEqual(service_path.login_path({'HOME':'/synthetic'}), '')

    def test_stable_custom_login_directories_and_npm_prefix(self):
        from agentj import service_path
        with patch.object(service_path, 'login_path', return_value='/custom/bin:/usr/bin:/Applications/ChatGPT.app/Contents/Resources/bin'):
            result = service_path.build({'HOME':'/synthetic', 'PATH':'relative::/custom/bin:/var/folders/t/a', 'NPM_CONFIG_PREFIX':'/synthetic/npm-global'})
        self.assertTrue(result.startswith('/custom/bin:/usr/bin:'))
        self.assertIn('/synthetic/npm-global/bin', result.split(':'))
        self.assertNotIn('relative', result)
        self.assertNotIn('ChatGPT', result)

    def test_installer_writes_stable_path_for_both_service_managers(self):
        from types import SimpleNamespace
        import subprocess
        import plistlib
        for plat in ('linux', 'macos'):
            with self.subTest(platform=plat), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                binary = home / '.local/bin/claude'
                binary.parent.mkdir(parents=True)
                binary.write_text('#!/bin/sh\nexit 0\n'); binary.chmod(0o755)
                root = home/'state'; root.mkdir()
                env = {'HOME':tmp, 'XDG_CONFIG_HOME':tmp+'/cfg', 'AGENTJ_SERVICE_NAME':'p529-test',
                       'PATH':'/Applications/ChatGPT.app/Contents/Resources/bin:'+tmp+'/.codex/tmp/arg0/bin'}
                result = subprocess.CompletedProcess([], 0, '', '')
                with patch.dict(os.environ, env, clear=True), patch.object(service, 'platform', return_value=plat), \
                     patch.object(service, '_systemctl', return_value=result), patch.object(service, '_bootstrap', return_value=result), \
                     patch.object(service, 'launchd_disabled', return_value=False), patch.object(service, 'remove_legacy'), \
                     patch.object(service, '_linger', return_value='yes'), patch.object(service, '_register_pair_protocol'):
                    installed = service.install(SimpleNamespace(root=root))
                    path = Path(installed['path'])
                    self.assertTrue(path.exists())
                    saved_path = service.installed_environment('p529-test')['PATH']
                    self.assertIn(str(binary.parent), saved_path.split(':'))
                    self.assertNotIn('ChatGPT.app', saved_path)
                    self.assertNotIn('.codex/tmp', saved_path)
                    self.assertEqual(service.binary_env(service.env_file('p529-test'))['AGENTJ_CLAUDE_BIN'], str(binary))
