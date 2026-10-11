"""macOS launchd private key loading; fixtures only, no Bot API or real env files."""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentj import preferences as p, service, telegram, voice
from agentj.state import State

KEY = 'TELEGRAM_BOT_TOKEN'
VALUE = 'p144-synthetic-value-only'


class PrivateFixture:

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p144-', dir='/var/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / '.config'),
            'AGENTJ_STATE_DIR': str(self.home / 'state'), 'PATH': '/usr/bin:/bin'}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.platform = patch.object(service, 'platform', return_value='macos')
        self.platform.start(); self.addCleanup(self.platform.stop)
        self.file = self.home / 'Library/LaunchAgents/net.agentj.host.env'
        self.file.parent.mkdir(parents=True)
        self.file.write_text(f'{KEY}="{VALUE}"\n')
        self.file.chmod(0o600)
        os.environ['AGENTJ_SERVICE_ENV_FILE'] = str(self.file)
        self.st = State(); self.st.init()


class PrivateKeys(PrivateFixture, unittest.TestCase):
    def test_known_and_all_nested_key_env_names_only(self):
        cfg = {'bots': [{'provider': {'api_key_env': 'P144_PROVIDER'}, 'telegram': {'key_env': 'P144_BOT'}}],
               'voice': {'asr': {'key_env': 'P144_ASR'}}, 'other': {'key_env': 'bad;name'}}
        self.file.write_text(self.file.read_text() + 'P144_PROVIDER="provider fixture"\nP144_BOT=bot-fixture\n'
            'P144_ASR=asr-fixture\nOPENAI_API_KEY=known-fixture\nUNREFERENCED_SECRET=ignored\n'
            'AGENTJ_OPENCODE_BIN=/tmp/fake-bin\nAGENTSRELAY_OPENAI_KEY=relay-fixture\nDEEPSEEK_API_KEY=provider-fixture\n')
        before = self.file.read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            service.load_launch_binary_env(cfg)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(os.environ[KEY], VALUE)
        self.assertEqual(os.environ['P144_PROVIDER'], 'provider fixture')
        for name in ('P144_BOT', 'P144_ASR', 'OPENAI_API_KEY', 'AGENTJ_OPENCODE_BIN', 'AGENTSRELAY_OPENAI_KEY', 'DEEPSEEK_API_KEY'):
            self.assertIn(name, os.environ)
        self.assertNotIn('UNREFERENCED_SECRET', os.environ)
        self.assertEqual(self.file.read_bytes(), before)
        env, _ = service.service_env()
        env['AGENTJ_SERVICE_ENV_FILE'] = str(self.file)
        blob = service.plist_bytes('p144', ['/bin/true'], env, '/tmp/fixture.log')
        self.assertNotIn(VALUE.encode(), blob)
        self.assertNotIn(KEY, plistlib.loads(blob)['EnvironmentVariables'])

    def test_startup_reads_state_and_preferences_and_legacy_ledger(self):
        cfg = self.st.config(); cfg['telegram'] = {'owner_id': 123, 'key_env': 'P144_CUSTOM_BOT'}
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        p.ensure(); p.path().write_text(p.edit(p.read()[0], 'voice.asr.key_env', 'P144_CUSTOM_ASR'))
        self.file.write_text('P144_CUSTOM_BOT=fixture-bot\nP144_CUSTOM_ASR=fixture-asr\n')
        service.load_launch_binary_env()
        self.assertEqual(os.environ['P144_CUSTOM_BOT'], 'fixture-bot')
        self.assertEqual(os.environ['P144_CUSTOM_ASR'], 'fixture-asr')
        from agentj import tg_cursor
        cfg.pop('telegram'); self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        cur = tg_cursor.load(self.st.root); cur['enrollment'] = {'owner_id': 123, 'key_env': 'P144_LEDGER_BOT'}
        tg_cursor.save(self.st.root, cur)
        self.file.write_text('P144_LEDGER_BOT=fixture-ledger\n')
        service.load_launch_binary_env()
        self.assertEqual(os.environ['P144_LEDGER_BOT'], 'fixture-ledger')

    def rejected(self):
        before = dict(os.environ)
        with self.assertRaises(service.ServiceError) as caught:
            service.load_launch_binary_env({'key_env': 'P144_PARSE'})
        self.assertEqual(dict(os.environ), before)
        detail = caught.exception.detail
        self.assertIn('0600', detail); self.assertIn('agentj service restart', detail)
        self.assertNotIn(VALUE, detail)
        return detail

    def test_0644_symlink_fifo_and_wrong_owner_rejected(self):
        self.file.chmod(0o644); self.rejected()
        self.file.chmod(0o600)
        target = self.file.with_name('target'); self.file.rename(target); self.file.symlink_to(target)
        self.rejected(); self.file.unlink()
        os.mkfifo(self.file, 0o600); self.rejected(); self.file.unlink(); target.rename(self.file)
        with patch.object(service.os, 'getuid', return_value=os.getuid() + 1):
            self.rejected()

    def test_malformed_selected_assignment_is_atomic_and_redacted(self):
        self.file.write_text(f'{KEY}={VALUE}\nP144_PARSE="{VALUE}\n')
        self.rejected()

    def test_missing_file_optional_and_linux_without_path_unchanged(self):
        self.file.unlink(); service.load_launch_binary_env()
        self.assertNotIn(KEY, os.environ)
        os.environ.pop('AGENTJ_SERVICE_ENV_FILE')
        with patch.object(service, 'private_env', side_effect=AssertionError('must not read')):
            service.load_launch_binary_env()

    def test_cli_service_missing_key_errors_and_voice(self):
        os.environ.pop('AGENTJ_SERVICE_ENV_FILE')
        for response, context in ((None, 'CLI'), ({'ok': True, 'present': False}, 'service')):
            with patch('agentj.names.ctl_call', return_value=response):
                result = telegram.enroll(123, KEY)
            self.assertFalse(result['ok']); self.assertIn(context + ' environment', result['error'])
            self.assertNotIn(VALUE, json.dumps(result))
            if context == 'service':
                self.assertIn('net.agentj.host.env', result['error'])
                self.assertIn('agentj service restart', result['error'])
        cfg = p.defaults(); cfg['voice']['asr'].update(mode='cloud', provider='openai', key_env='P144_MISSING')
        for context in ('CLI', 'service'):
            with self.assertRaises(p.ConfigError) as caught:
                voice.validate_runtime(cfg, environment=context)
            self.assertIn(context + ' environment', caught.exception.result()['error'])


class MacConfigApply(PrivateFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = None

    async def asyncTearDown(self):
        if self.server:
            self.server.close(); await self.server.wait_closed()

    async def test_mac_startup_then_real_config_apply_cli_without_key(self):
        cfg = self.st.config(); cfg['telegram'] = {'owner_id': 123, 'key_env': KEY}
        self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
        p.ensure(); p.path().write_text(p.edit(p.read()[0], 'channels.items', [{'id': 'tg', 'type': 'telegram'}]))
        candidate = p.validate(p.read()[1])
        with self.assertRaises(p.ConfigError):
            voice.validate_runtime(candidate, environment='service')  # red before launchd loader
        service.load_launch_binary_env()
        from agentj.serve import Host
        host = Host(self.st, read_stdin=False)
        self.assertIsNone(host.config_problem)
        self.server = await asyncio.start_unix_server(host.on_ctl, path=str(self.st.sock_path))
        env = dict(os.environ); env.pop(KEY); env.pop('AGENTJ_SERVICE_ENV_FILE')
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'agentj.cli', 'config', 'set',
            'appearance.theme', 'dark', '--json', env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 20)
        finally:
            if proc.returncode is None:
                proc.kill(); await proc.wait()
        self.assertNotIn(VALUE.encode(), out + err)
        self.assertEqual(proc.returncode, 0, err.decode())
        result = json.loads(out); self.assertTrue(result['ok']); self.assertTrue(result['applied'])
        self.assertEqual(p.get(host.preferences, 'appearance.theme'), 'dark')
        for file in self.st.root.rglob('*.log'):
            self.assertNotIn(VALUE, file.read_text())

    async def test_bad_file_config_apply_receipt_refuses_without_value(self):
        from agentj.serve import Host
        host = Host(self.st, read_stdin=False)
        self.file.chmod(0o644)
        old = p.read()[0]
        result = await host.apply_preferences(p.edit(old, 'appearance.theme', 'dark'))
        self.assertFalse(result['ok']); self.assertIn('0600', result['error'])
        self.assertNotIn(VALUE, json.dumps(result)); self.assertEqual(p.read()[0], old)

    async def test_cli_startup_refusal_is_actionable_and_redacted(self):
        self.file.chmod(0o644)
        env = dict(os.environ); env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'agentj.cli', 'status', '--json', env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 15)
        finally:
            if proc.returncode is None:
                proc.kill(); await proc.wait()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b'0600', out + err); self.assertIn(b'agentj service restart', out + err)
        self.assertNotIn(b'Traceback', out + err); self.assertNotIn(VALUE.encode(), out + err)

    async def test_new_provider_candidate_loads_custom_key_before_validation(self):
        from agentj.serve import Host
        host = Host(self.st, read_stdin=False)
        self.file.write_text('P144_NEW_ASR=synthetic-asr-only\n')
        raw = p.edit(p.read()[0], 'voice.asr', {'mode': 'cloud', 'provider': 'openai',
            'model': 'gpt-4o-mini-transcribe', 'key_env': 'P144_NEW_ASR'})
        self.assertNotIn('P144_NEW_ASR', os.environ)
        result = await host.apply_preferences(raw)
        self.assertTrue(result['ok'], result)
        self.assertNotIn('synthetic-asr-only', json.dumps(result))

    async def test_new_custom_enrollment_name_loaded_by_service_probe(self):
        from agentj.serve import Host
        from agentj.names import ctl_call
        self.file.write_text('P144_NEW_CUSTOM=fixture-only\n')
        host = Host(self.st, read_stdin=False)
        self.server = await asyncio.start_unix_server(host.on_ctl, path=str(self.st.sock_path))
        result = await asyncio.to_thread(ctl_call, self.st, {'cmd': 'env_present', 'name': 'P144_NEW_CUSTOM'}, 5)
        self.assertEqual(result, {'ok': True, 'present': True})
        self.file.chmod(0o644)
        with patch('agentj.names.ctl_call', return_value={'ok': False, 'error': service.MESSAGES['bad_service_env']}):
            result = telegram.enroll(123, 'P144_NEW_CUSTOM')
        self.assertFalse(result['ok']); self.assertIn('0600', result['error'])


if __name__ == '__main__':
    unittest.main()
