"""Real Host seams using synthetic channels; requires owner's existing full dependencies."""
import unittest
from types import SimpleNamespace
from agentj.serve import Host


class HostContracts(unittest.TestCase):
    def test_models_message_names_registered_harness_without_changing_choices(self):
        for kind, label in (('claude', 'Claude Code'), ('codex', 'Codex'), ('opencode', 'OpenCode')):
            host = object.__new__(Host)
            host.agent = SimpleNamespace(kind=kind, human={}, cur_model=lambda: 'real-model', cur_effort=lambda: None,
                                         models_cache=[{'id': 'real-model', 'name': 'Real Model'}])
            message = host._models_msg()
            self.assertEqual(message['harness'], label)
            self.assertEqual(message['models'][0]['id'], 'real-model')
            self.assertEqual(message['models'][0]['name'], 'Real Model')
            self.assertTrue(message['models'][0]['cur'])

    def test_native_text_uses_existing_agent_mediator(self):
        host = object.__new__(Host); host.lang = 'en'
        host.agent = SimpleNamespace(kind='codex', init={}, passthrough=lambda _: False)
        self.assertIn('[Menu command /mcp]', host.native_text('/mcp'))
        self.assertIsNone(host.native_text('/permissions'))
        self.assertIsNone(host.native_text('/made-up-command'))
        host.agent.passthrough = lambda _: True
        self.assertIsNone(host.native_text('/mcp'))
