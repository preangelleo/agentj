"""#520: turn kind never adds a human gate; full access retains only UV floors."""
import _hermetic  # noqa: F401
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import approval_level, approval_policy, danger, policy_registry


class GateAudit520Policy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name

    def test_background_kind_does_not_add_mail_confirmation(self):
        for chain in (False, True):
            with self.subTest(chain=chain):
                self.assertEqual(approval_policy.decide('agentj mail send', {}, danger.Verdict(),
                    requirement='hold', known_recipients=True, chain=chain), ('none', 'mail_known'))

    def test_full_access_regular_card_rows_are_automatic(self):
        for cmd in ('git push origin main', 'gh release create v-test', 'sudo apt-get update'):
            with self.subTest(cmd=cmd):
                inp = {'command': cmd}
                tier, _ = approval_policy.decide('Bash', inp, danger.classify('Bash', inp),
                    requirement='hold', workdir=self.cwd, full_access=True)
                self.assertEqual(tier, 'none')

    def test_regular_levels_keep_their_button(self):
        tier, row = approval_policy.decide('agentj mail send', {}, danger.Verdict(), requirement='hold')
        self.assertEqual((tier, row), ('tap', 'send_new'))

    def test_full_access_retains_actual_uv(self):
        for cmd in ('echo "$API_TOKEN"', 'echo x > ~/.claude/settings.json', 'stripe charges create --amount 500'):
            with self.subTest(cmd=cmd):
                inp = {'command': cmd}
                tier, _ = approval_policy.decide('Bash', inp, danger.classify('Bash', inp),
                    requirement=policy_registry.main_requirement('Bash', inp), workdir=self.cwd, full_access=True)
                self.assertEqual(tier, 'face')

    def test_full_access_keep_separates_uv_from_regular_harness_card(self):
        # A follow-up's shape / native request does not itself prove UV.
        with mock.patch('agentj.confirm_tier.harness_keeps', return_value=None), \
                mock.patch.object(approval_level, 'harness_floor', return_value='injection'):
            self.assertIsNone(approval_level.full_access_keep('Bash', {'command': 'true'}, danger.Verdict(), requirement='hold'))
        for reason in ('spend', 'delete', 'seat', 'secret_out', 'hooks', 'settings', 'credentials'):
            with self.subTest(reason=reason), mock.patch('agentj.confirm_tier.harness_keeps', return_value=reason), \
                    mock.patch.object(approval_level, 'harness_floor', return_value=None):
                self.assertEqual(approval_level.full_access_keep('Bash', {}, danger.Verdict(), requirement='hold'), reason)
        self.assertEqual(approval_level.full_access_keep('agentj goal', {}, danger.Verdict(), requirement='uv'), 'uv')
        self.assertEqual(approval_level.full_access_keep('agentj goal', {}, danger.Verdict(), requirement='hold', require_passkey=True), 'uv')

    def test_full_access_inline_secret_reveal_cannot_fall_through_native_classifier(self):
        inp = {'command': 'echo "$API_TOKEN"'}
        self.assertEqual(approval_level.full_access_keep('Bash', inp, danger.Verdict(), requirement='allow'),
                         'credentials')

    def test_unknown_classifier_keeps_safety_floor(self):
        with mock.patch('agentj.confirm_tier.harness_keeps', side_effect=ValueError('bad shape')):
            self.assertEqual(approval_level.full_access_keep('Bash', {}, danger.Verdict(), requirement='hold'), 'credentials')

    def test_denied_unknown_action_is_not_made_automatic(self):
        self.assertEqual(approval_level.full_access_keep('UnknownTool520', {}, danger.Verdict(), requirement='deny'), 'deny')



    def test_native_interpreter_secret_display_keeps_uv(self):
        for tool, inp in [('js_repl', {'code': 'console.log(process.env.API_TOKEN)'}),
                          ('PowerShell', {'command': 'Write-Output $env:API_TOKEN'}),
                          ('write_stdin', {'chars': 'echo "$API_TOKEN"\n'})]:
            with self.subTest(tool=tool):
                self.assertEqual(approval_level.full_access_keep(tool, inp, danger.Verdict()), 'credentials')

if __name__ == '__main__':
    unittest.main()
