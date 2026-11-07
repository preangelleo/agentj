"""Real synthetic state/file and signed Runner contracts; no harness or network."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from agentj import preferences, opencode_provider as provider, install_assist, setup_ops
from agentj.state import State


class ModelState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'AGENTJ_STATE_DIR': str(self.root / 'state'),
                                          'XDG_CONFIG_HOME': str(self.root / 'config')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.st = State(); self.st.root.mkdir()
        self.st.config_path.write_text(json.dumps({'agent': {'kind': 'opencode', 'dir': str(self.root), 'model': 'gone/a'}}))
        self.native = Path(provider.config_file()); self.native.parent.mkdir(parents=True)
        self.native.write_text(json.dumps({'provider': {'gone': {}, 'kept': {}}, 'model': 'gone/a',
                                         'small_model': 'gone/b', 'agent': {'aux': {'model': 'gone/c'}}}))

    def command(self, *args):
        with patch('sys.stdout', new_callable=io.StringIO) as out:
            code = preferences.command(list(args))
        return code, json.loads(out.getvalue())

    def test_cli_reads_sets_unsets_single_state_owner(self):
        with patch('agentj.names.ctl_call', return_value=None):
            self.assertEqual(self.command('get', 'agent.model')[1]['value'], 'gone/a')
            self.assertEqual(self.command('set', 'agent.model', 'kept/org/model:1')[0], 0)
            self.assertEqual(self.st.agent_config()['model'], 'kept/org/model:1')
            self.assertEqual(self.command('unset', 'agent.model')[0], 0)
        self.assertIsNone(self.st.agent_config()['model'])
        self.assertFalse(preferences.path().exists())

    def test_cli_invalid_ghost_and_dry_run_never_write(self):
        before = self.st.config_path.read_bytes()
        for model in ('ghost/m', 'bare', 'gone/', 'gone/m\n', ''):
            self.assertNotEqual(self.command('set', 'agent.model', model)[0], 0)
        self.assertEqual(self.command('set', 'agent.model', 'kept/m', '--dry-run')[0], 0)
        self.assertEqual(self.st.config_path.read_bytes(), before)

    def test_json5_cannot_create_second_model_owner(self):
        with self.assertRaises(preferences.ConfigError):
            preferences.parse('{agent: {model: "gone/a"}}')

    def test_offline_remove_clears_host_and_native_auxiliary_models(self):
        with patch('agentj.names.ctl_call', return_value=None):
            result = provider.remove('gone')
        self.assertTrue(result['host_model_unset'])
        self.assertEqual(result['models_unset'], 3)
        self.assertIsNone(self.st.agent_config()['model'])
        native = json.loads(self.native.read_text())
        self.assertEqual(native['provider'], {'kept': {}})
        self.assertNotIn('model', native)
        self.assertNotIn('model', native['agent']['aux'])

    def test_already_missing_provider_clears_dangling_refs(self):
        native = json.loads(self.native.read_text()); del native['provider']['gone']
        self.native.write_text(json.dumps(native))
        with patch('agentj.names.ctl_call', return_value=None):
            result = provider.remove('gone')
        self.assertFalse(result['removed'])
        self.assertTrue(result['host_model_unset'])
        self.assertIsNone(self.st.agent_config()['model'])

    def test_live_host_refuses_with_stop_instruction_and_no_writes(self):
        before = self.st.config_path.read_bytes(), self.native.read_bytes()
        with patch('agentj.names.ctl_call', return_value={'ok': True}):
            with self.assertRaisesRegex(provider.ProviderError, 'stop the Host'):
                provider.remove('gone')
        self.assertEqual((self.st.config_path.read_bytes(), self.native.read_bytes()), before)

    def test_failed_native_write_restores_model(self):
        before = self.st.config_path.read_bytes(), self.native.read_bytes()
        with patch('agentj.names.ctl_call', return_value=None), patch.object(provider, '_write', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError): provider.remove('gone')
        self.assertEqual(self.st.agent_config()['model'], 'gone/a')
        self.assertEqual(self.native.read_bytes(), before[1])

    def test_host_rejection_never_falls_back_to_local_write(self):
        before = self.st.config_path.read_bytes()
        with patch('agentj.names.ctl_call', return_value={'ok': False, 'error': 'busy'}):
            code, result = self.command('unset', 'agent.model')
        self.assertEqual(code, 3)
        self.assertIn('busy', result['error'])
        self.assertEqual(self.st.config_path.read_bytes(), before)


