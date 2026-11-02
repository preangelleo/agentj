"""Pinned Olares OpenCode v2: official provider cannot be replaced by a custom API block."""
import _hermetic  # noqa: F401
import unittest
from agentj import harness, opencode_provider

class OlaresOpenCode(unittest.TestCase):
    def test_native_version_output_selects_v2_adapter(self):
        self.assertTrue(harness.opencode_v2('opencode v2.2.5'))
        self.assertFalse(harness.opencode_v2('1.19.5'))

    def test_claude_agent_uses_official_external_login_not_custom_key_provider(self):
        with self.assertRaises(opencode_provider.ProviderError):
            opencode_provider.block('claude-agent','https://example.invalid',['sonnet'])
