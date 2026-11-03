"""Only the trusted Olares image may advertise its private terminal entrance."""
import _hermetic  # noqa: F401
import os
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentj import olares_terminal


class Entrance(unittest.TestCase):
    def test_no_capability_on_ordinary_host_even_with_environment(self):
        with patch.dict(os.environ, {'AGENTJ_OLARES_TERMINAL_URL': 'https://terminal.owner.example'}), \
                patch('agentj.olares_supervisor.enabled', return_value=False):
            self.assertIsNone(olares_terminal.external_url())

    def test_trusted_image_url_is_exact_origin(self):
        with patch.dict(os.environ, {'AGENTJ_OLARES_TERMINAL_URL': 'https://terminal.owner.example'}), \
                patch('agentj.olares_supervisor.enabled', return_value=True):
            self.assertEqual(olares_terminal.external_url(), 'https://terminal.owner.example')

    def test_missing_or_malformed_configuration_omits_capability(self):
        with patch('agentj.olares_supervisor.enabled', return_value=True):
            for value in ['', 'https://127.0.0.1', 'https://Terminal.owner.example', 'javascript:alert(1)', 'http://terminal.owner.example',
                          'https://user:pass@terminal.owner.example', 'https://terminal.owner.example/path',
                          'https://terminal.owner.example?token=bad', 'https://terminal.owner.example#fragment',
                          'https://terminal.owner.example\n', 'https://terminal.owner.example\\@other.invalid']:
                with self.subTest(value=value), patch.dict(os.environ, {'AGENTJ_OLARES_TERMINAL_URL': value}):
                    self.assertIsNone(olares_terminal.external_url())

    def test_capability_reaches_existing_per_device_terminal_block(self):
        from agentj import terminal
        with patch('agentj.terminal.store.read_config', return_value={'on': False, 'minutes': 15}), \
                patch('agentj.terminal.store.entry', return_value=None), \
                patch('agentj.terminal.store.device_kind', return_value='phone'), \
                patch('agentj.olares_terminal.external_url', return_value='https://terminal.owner.example'):
            desk = terminal.Desk(SimpleNamespace(st=SimpleNamespace(passkey_of_device=lambda _: None)))
            info = desk.info('test-device')
            self.assertEqual(info['external_url'], 'https://terminal.owner.example')
            self.assertFalse(info['on'])
            self.assertFalse(info['pk'])
            self.assertEqual(info['kind'], 'phone')
        with patch('agentj.terminal.store.read_config', return_value={'on': False, 'minutes': 15}), \
                patch('agentj.terminal.store.entry', return_value=None), \
                patch('agentj.terminal.store.device_kind', return_value='unknown'), \
                patch('agentj.olares_terminal.external_url', return_value=None):
            self.assertNotIn('external_url', desk.info(None))

    def test_supervisor_keeps_chart_url_across_serve_restart(self):
        from agentj import olares_supervisor, switch_harness
        from test_p155_switch_harness import mk_state
        with tempfile.TemporaryDirectory(prefix='olares-terminal-') as folder:
            home = Path(folder)
            st = mk_state(home / 'state', 'opencode', model='test-model')
            with patch.dict(os.environ, {'AGENTJ_OLARES_TERMINAL_URL': 'https://terminal.owner.example'}), \
                    patch.object(switch_harness, 'receipt_path', side_effect=lambda name=switch_harness.RECEIPT: home / 'units' / name):
                manager = olares_supervisor.Supervisor(st, home)
                self.addCleanup(manager.close)
                self.assertEqual(manager.base_env['AGENTJ_OLARES_TERMINAL_URL'], 'https://terminal.owner.example')
