"""P385: private verification/reload and copyable remote system messages; synthetic data only."""
import _hermetic  # noqa: F401
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from agentj import cli, elevate, names, personalize, service, system_text, tg_pair
from _testtmp import TEST_TMP


class Verification(unittest.TestCase):
    def test_url_path_and_header_substitution_redacted_receipt(self):
        marker = bytearray(b'synthetic:/?# key')
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        with patch('urllib.request.build_opener', return_value=opener):
            result = elevate.verify_secret({'verify_url': 'https://api.example.test/bot{value}/getMe',
                                            'verify_header': 'Authorization: Bearer {value}'}, marker)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.example.test/botsynthetic%3A%2F%3F%23%20key/getMe')
        self.assertEqual(req.get_header('Authorization'), 'Bearer synthetic:/?# key')
        self.assertEqual(result, {'verify': 'ok', 'detail': 'HTTP 200'})
        self.assertNotIn('synthetic', json.dumps(result))

    def test_transport_error_never_returns_url_or_key(self):
        opener = MagicMock()
        opener.open.side_effect = OSError('synthetic-secret-in-url')
        with patch('urllib.request.build_opener', return_value=opener):
            result = elevate.verify_secret({'verify_url': 'https://api.example.test/{value}',
                                            'verify_header': 'Authorization: Bearer {value}'}, bytearray(b'synthetic'))
        self.assertEqual(result, {'verify': 'fail', 'detail': 'network'})

    def test_placeholder_in_authority_refused(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            with self.assertRaises(elevate.Refused):
                elevate.norm_secret({'name':'TELEGRAM_BOT_TOKEN', 'purpose':'Connect Telegram',
                    'dest':'env:' + root + '/keys', 'verify_url':'https://{value}.example.test/getMe'}, Path(root) / 'state')


class Isolation(unittest.TestCase):
    def test_reload_missing_state_is_unknown_in_fence(self):
        with patch('agentj.fence.inside', return_value=True), patch('agentj.cli.State') as state, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            state.return_value.exists.return_value = False
            with self.assertRaises(SystemExit) as stopped:
                cli.cmd_reload(SimpleNamespace(json=True))
            self.assertEqual(stopped.exception.code, 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result['error'], 'host_unreachable')
            self.assertNotIn('not running', result['detail'])

    def test_channel_pair_hidden_socket_does_not_say_not_running(self):
        with patch('agentj.fence.inside', return_value=True), patch('agentj.state.State') as state, \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            state.return_value.exists.return_value = False
            self.assertEqual(personalize.pair_telegram('TELEGRAM_BOT_TOKEN'), 2)
            self.assertEqual(json.loads(output.getvalue())['error'], 'host_unreachable')


class Reload(unittest.IsolatedAsyncioTestCase):
    async def test_saved_service_file_reloads_without_exposing_names_or_values(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            managed = str(Path(root) / 'service-keys')
            host = SimpleNamespace(preferences={}, telegram=SimpleNamespace(bot_id=42),
                                   _reload_runtime=MagicMock(), st=SimpleNamespace(log=MagicMock()))
            elevator = SimpleNamespace(host=host, st=host.st)
            result = {'receipt':{}}
            with patch.dict(os.environ, {'AGENTJ_SERVICE_ENV_FILE':managed}), \
                 patch('agentj.service.reload_service_env', return_value=['TELEGRAM_BOT_TOKEN']) as reload:
                await elevate.Elevator._reload_saved_env(elevator,
                    {'dest_spec':{'mode':'env', 'path':managed}}, result)
            reload.assert_called_once_with({})
            host._reload_runtime.assert_called_once_with(True)
            self.assertIsNone(host.telegram.bot_id)
            self.assertEqual(result, {'receipt':{'env_reloaded':True}})

    async def test_other_secret_file_does_not_reload_service(self):
        elevator = SimpleNamespace(host=SimpleNamespace(preferences={}))
        with patch.dict(os.environ, {'AGENTJ_SERVICE_ENV_FILE':'/tmp/p385-managed'}), \
             patch('agentj.service.reload_service_env') as reload:
            await elevate.Elevator._reload_saved_env(elevator,
                {'dest_spec':{'mode':'env','path':'/tmp/p385-unrelated'}}, {'receipt':{}})
        reload.assert_not_called()

    async def test_rebind_reads_service_environment_before_getme(self):
        from agentj.serve import Host
        events = []
        async def pair(key):
            events.append('getMe')
            return {'ok':True,'bot':'example_bot','code':'000001'}
        host = SimpleNamespace(preferences={}, lang='en', st=SimpleNamespace(log=MagicMock()),
            telegram=SimpleNamespace(bot_id=42, start_pairing=pair), _reload_runtime=MagicMock(), cmd_card=MagicMock())
        def reload(cfg):
            events.append('reload')
            return ['TELEGRAM_BOT_TOKEN']
        with patch('agentj.service.reload_service_env', side_effect=reload):
            result = await Host.telegram_pair(host)
        self.assertEqual(events, ['reload','getMe'])
        self.assertNotIn('code',result)

    async def test_unsigned_restart_is_refused(self):
        from agentj import reset
        host = SimpleNamespace(st=SimpleNamespace(log=MagicMock()), nonces={}, channel='synthetic', send_app=AsyncMock())
        desk = reset.Desk(host)
        with patch('agentj.controls.check',return_value='signature'), patch('agentj.controls.log'), \
             patch.object(desk, 'start') as start:
            await desk.on_phone(SimpleNamespace(device='synthetic'), 'rescue_do',
                                {'r':'p385restart', 'action':'restart'})
        start.assert_not_called()
        self.assertFalse(host.send_app.call_args.args[1]['ok'])


class Messages(unittest.TestCase):
    def test_pair_codes_and_commands_have_separate_copy_blocks_and_link(self):
        for lang in ('zh','en'):
            text = tg_pair.card(lang,'example_bot','000001')
            self.assertIn('```text\n000001\n```',text)
            self.assertIn('```text\n/start 000001\n```',text)
            self.assertIn('](https://t.me/example_bot?start=000001)',text)

    def test_system_commands_and_bare_urls_but_not_identifiers(self):
        for text in ('Run `agentj service restart` then https://agentj.app/docs/install。继续',
                     '运行 `agentj reload` 然后 https://agentj.app/docs/install，继续'):
            got = system_text.remote(text)
            self.assertIn('```sh\nagentj ',got)
            self.assertIn('](https://agentj.app/docs/install)',got)
            self.assertEqual(system_text.remote(got), got)
        self.assertEqual(system_text.remote('Set `TELEGRAM_BOT_TOKEN`.'), 'Set `TELEGRAM_BOT_TOKEN`.')

    def test_existing_blocks_and_links_preserved(self):
        text = '```sh\nagentj reload\nhttps://example.test\n```\n[Help](https://example.test)'
        self.assertEqual(system_text.remote(text),text)
        self.assertEqual(system_text.remote('javascript:alert(1)'), 'javascript:alert(1)')

    def test_inline_url_and_sentence_boundaries(self):
        self.assertEqual(system_text.remote('`https://example.test`'), '[https://example.test](https://example.test)')
        self.assertEqual(system_text.remote('(https://example.test).'), '([https://example.test](https://example.test)).')
        self.assertEqual(system_text.remote('https://example.test。请重试'), '[https://example.test](https://example.test)。请重试')
