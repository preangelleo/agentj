"""#488: actual hermetic Host admission; sample commands are never executed."""
import json
import unittest
from test_p551_confirm_levels import Base


class LauncherAdmission(unittest.TestCase):
    def setUp(self):
        self.fx = Base()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.harness()
        self.fx.host.ask_ttl = .02

    def rejected_without_card(self, command):
        before = len(self.fx.cards())
        result = self.fx.ask(command)
        cards = self.fx.cards()[before:]
        print(json.dumps({'case': self.id().split('.')[-1], 'behavior': result.get('behavior'),
                          'cards': len(cards), 'uv': bool(cards and cards[-1].get('passkey_required'))}))
        self.assertEqual(result.get('behavior'), 'deny')
        self.assertEqual(cards, [], 'unresolved code cannot be made executable by signing a card')

    def test_deep_busy_shell_is_not_approved(self):
        command = 'rm -rf ~/x'
        for _ in range(9):
            command = 'agentj busy run -- bash -c ' + json.dumps(command)
        self.rejected_without_card(command)

    def test_wrapper_cap_is_not_approved(self):
        self.rejected_without_card('nohup ' * 20 + 'ls')

    def test_malformed_split_string_is_not_approved(self):
        self.rejected_without_card('env -S "rm -rf \'unbalanced"')

    def test_resolved_git_force_still_requires_uv(self):
        before = len(self.fx.cards())
        result = self.fx.ask("env --split-string='git push --force origin main'")
        cards = self.fx.cards()[before:]
        print(json.dumps({'case': 'git_force', 'behavior': result.get('behavior'),
                          'cards': len(cards), 'uv': bool(cards and cards[-1].get('passkey_required'))}))
        self.assertNotEqual(result.get('behavior'), 'allow')
        self.assertTrue(cards and cards[-1].get('passkey_required'))
