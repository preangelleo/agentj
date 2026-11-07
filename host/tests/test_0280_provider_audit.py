"""Offline 138/140/146/179 regressions; fixtures never launch a harness or network."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import harness_cmds as commands, opencode_provider as provider, startup_help


class CommandContracts(unittest.TestCase):
    def test_registered_routes_are_explicit(self):
        for kind, rows in commands.TUI.items():
            for name, _, _ in rows:
                if name not in commands.OWN:
                    self.assertIn(commands.route(kind, name), ('run', 'agent', 'none'))

    def test_delegation_is_controlled_intent(self):
        for kind, names in commands.DELEGATE.items():
            for name in names:
                self.assertEqual(commands.route(kind, name), 'agent')
                for lang in ('zh', 'en'):
                    text = commands.agent_text('/' + name, kind, lang=lang)
                    self.assertFalse(text.startswith('/'))
                    self.assertIn('UV', text)
                    self.assertIn('TUI', text)
                    self.assertIn('shell', text)

    def test_permissions_credentials_and_unsafe_screens_stay_blocked(self):
        for kind, name in (('claude', 'permissions'), ('codex', 'approvals'), ('codex', 'permissions'),
                           ('opencode', 'connect'), ('opencode', 'share'), ('opencode', 'undo'), ('codex', 'resume')):
            self.assertEqual(commands.route(kind, name), 'none')
            self.assertIsNone(commands.agent_text('/' + name, kind))
            self.assertTrue(commands.none_note(kind, name, 'en'))
        self.assertIsNone(commands.route('codex', 'made-up-command'))

    def test_native_execution_contract_stays_native(self):
        self.assertEqual(commands.route('codex', 'review'), 'run')
        self.assertEqual(commands.route('opencode2', 'init'), 'run')
        self.assertIsNone(commands.agent_text('/review', 'codex'))


class DiagnosticContracts(unittest.TestCase):
    def test_only_network_and_region_offer_domestic_routes(self):
        for reason in (None, '401', 'auth', 'access', 'model_missing', 'provider_probe_model_or_endpoint', 'unknown'):
            text = startup_help.guidance('codex', 'start_timeout', 'en', reason=reason)
            self.assertNotIn('reply:', text)
        for reason in ('network', 'region', 'provider_probe_timeout'):
            text = startup_help.guidance('opencode', 'native_failed', reason=reason)
            self.assertEqual(text.count('](reply:'), 3)
            self.assertIn('Token Plan', text)
            self.assertIn('Agent Plan', text)
            self.assertIn('Kimi Code', text)

    def test_auth_model_and_unknown_are_distinct(self):
        self.assertEqual(startup_help.category('provider_probe_auth'), 'auth')
        self.assertEqual(startup_help.category('model_missing'), 'model')
        self.assertEqual(startup_help.category('start_timeout'), 'unknown')


class ProviderContracts(unittest.TestCase):
    def test_unsupported_models_endpoint_is_unverified_not_bad_key(self):
        from agentj import provider_setup
        for code in (0, 403, 404, 405, 429, 500, 501):
            with patch('agentj.ark_plan_fix._models', return_value=code) as request:
                self.assertIsNone(provider_setup._verify('DASHSCOPE_API_KEY', 'synthetic-value'))
                request.assert_called_once()
        with patch('agentj.ark_plan_fix.read_key', return_value='synthetic-value'):
            verify = Mock(return_value=None)
            result = provider_setup.check_key(object(), 'DASHSCOPE_API_KEY', verify=verify)
        self.assertEqual(result, {'ok': True, 'valid': None, 'verification': 'unverified'})
        verify.assert_called_once()

    def test_provider_model_format_and_existence(self):
        self.assertEqual(provider.validate_model('saved/org/model:1', {'saved': {}}), ('saved', 'org/model:1'))
        for model in (None, '', 'bare', 'Bad/model', 'saved/', 'saved/model\n', 'saved/model x', 'ghost/model'):
            with self.subTest(model=model), self.assertRaises(provider.ProviderError):
                provider.validate_model(model, {'saved': {}})

    def test_add_rejects_malformed_values_before_writes(self):
        for pid, models in (('bad\n', ['m']), ('saved', ['m\n']), ('saved', [None])):
            with self.assertRaises(provider.ProviderError):
                provider.block(pid, 'https://example.invalid/v1', models)

    def test_remove_unsets_all_native_references_and_keeps_other_models(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {'XDG_CONFIG_HOME': directory}
            path = Path(provider.config_file(env)); path.parent.mkdir()
            cfg = {'provider': {'gone': {}, 'kept': {}}, 'model': 'gone/a', 'small_model': 'gone/b',
                   'agent': {'a': {'model': 'gone/c'}, 'b': {'model': 'kept/d'}}, 'other': 42}
            path.write_text(json.dumps(cfg))
            with patch('agentj.state.State') as state:
                state.return_value.agent_config.return_value = None
                result = provider.remove('gone', env)
            self.assertEqual(result['models_unset'], 3)
            self.assertEqual(result['selection_status'], 'unset')
            saved = json.loads(path.read_text())
            self.assertNotIn('model', saved)
            self.assertNotIn('small_model', saved)
            self.assertEqual(saved['agent']['b']['model'], 'kept/d')
            self.assertEqual(saved['other'], 42)

    def test_remove_refuses_current_host_reference_without_mutating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {'XDG_CONFIG_HOME': directory}
            path = Path(provider.config_file(env)); path.parent.mkdir()
            original = '{"provider":{"gone":{}},"model":"gone/a"}'
            path.write_text(original)
            with patch('agentj.state.State') as state, patch('agentj.names.ctl_call', return_value={'ok': True}):
                state.return_value.agent_config.return_value = {'kind': 'opencode', 'model': 'gone/a'}
                with self.assertRaisesRegex(provider.ProviderError, 'provider_in_use'):
                    provider.remove('gone', env)
            self.assertEqual(path.read_text(), original)


class AdapterContracts(unittest.IsolatedAsyncioTestCase):
    async def test_claude_model_title_and_ids(self):
        from agentj.agent import ClaudeAgent
        agent = object.__new__(ClaudeAgent)
        agent.cfg = {'model': 'real-model'}; agent.init = {}
        agent._ensure = AsyncMock(return_value=True)
        agent.control = AsyncMock(return_value={'models': [{'value': 'real-model', 'displayName': 'Real Model'}]})
        result = await agent.cmd_model('')
        self.assertIn('Claude Code', result.text)
        self.assertEqual(result.models[0]['id'], 'real-model')
        self.assertEqual(result.models[0]['name'], 'Real Model')

    async def test_opencode_model_title_and_ids(self):
        from agentj.agent_opencode import OpenCodeAgent
        agent = object.__new__(OpenCodeAgent)
        agent.cfg = {'model': 'saved/real-model'}
        agent._ensure_started = AsyncMock(return_value=True)
        agent._providers = AsyncMock(return_value={'providers': [{'id': 'saved', 'models': {'real-model': {'name': 'Real Model'}}}]})
        result = await agent.cmd_model('')
        self.assertIn('OpenCode', result.text)
        self.assertEqual(result.models[0]['id'], 'saved/real-model')
        self.assertEqual(result.models[0]['name'], 'Real Model')

    async def test_codex_failure_actions_respect_reason(self):
        from agentj.agent import codex_failure
        for raw, expected, offers in (
            ('HTTP status 403 Forbidden', 'access', False),
            ('HTTP status 401 Unauthorized', 'auth', False),
            ('HTTP status 404 model not supported', 'model', False),
            ('DNS connection failed', 'network', True),
            ('unsupported_country: region is not supported', 'region', True),
            ('unrecognised native failure', 'other', False),
        ):
            kind, text, _ = codex_failure(raw, 'real-model', 'en', provider={'id': 'saved', 'host': 'example.invalid', 'relay': True})
            self.assertEqual(kind, expected)
            self.assertEqual('](reply:' in text, offers)
            self.assertNotRegex(text.split('\n\n')[0], '[一-鿿]')

    async def test_authorized_model_404_is_not_invalid_key(self):
        from agentj.agent_opencode import provider_error_reason
        self.assertEqual(provider_error_reason('APIError', {'statusCode': 404}, [], 'saved', {'saved'}), 'model')
        self.assertEqual(provider_error_reason('APIError', {'statusCode': 404}, [], 'ghost', {'saved'}), 'model_missing')
        self.assertEqual(provider_error_reason('APIError', {'statusCode': 401}, [], 'saved', {'saved'}), 'login')

    async def test_codex_model_title_and_ids(self):
        from agentj.agent_codex import CodexAgent
        agent = object.__new__(CodexAgent)
        agent.proc = object(); agent.cfg = {'model': 'real-model'}; agent.thread = {}; agent.human = {}
        agent.unavailable_models = set()
        agent.call = AsyncMock(return_value={'data': [{'id': 'real-model', 'displayName': 'Real Model'}]})
        result = await agent.cmd_model('')
        self.assertIn('Codex', result.text)
        self.assertEqual(result.models[0]['id'], 'real-model')
        self.assertEqual(result.models[0]['name'], 'Real Model')

    async def test_opencode_rejects_ghost_without_saving(self):
        from agentj.agent_opencode import OpenCodeAgent
        agent = object.__new__(OpenCodeAgent)
        agent.cfg = {'model': 'saved/model'}; agent.config_providers = []; agent.connected_at_start = []
        agent.usable_models = []; agent.models_cache = []
        with patch('agentj.provider_runtime.configured', return_value={}):
            self.assertEqual(await agent.apply_model('ghost/model', None), 'model_missing')
        for value in ('bad/model\n', 'bad/model x', 'bad/'):
            self.assertEqual(await agent.apply_model(value, None), 'unknown_model')


if __name__ == '__main__':
    unittest.main()
