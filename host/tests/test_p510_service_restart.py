"""#510: service restarts have their own class unproven paths retain approval authority."""
import _hermetic  # noqa: F401
import unittest
from agentj import approval_level, approval_policy, danger, goal, policy_registry


class ServiceRestart(unittest.TestCase):
    commands = (
        'agentj service restart',
        'systemd-run --user --on-active=45 agentj service restart',
        'systemd-run --user --on-active 45 agentj service restart',
        'systemd-run --user --collect --unit agentj-r4-restart --on-active=45 agentj service restart',
        'D=/tmp/aj-p510-acceptance; systemd-run --user --collect --unit agentj-r4-restart '
        '--on-active=45 agentj service restart 2>&1 | tail -1; printf ok > "$D"',
        'env systemd-run --user --on-active=45 agentj service restart',
    )

    def test_restart_is_not_a_credential_change(self):
        for command in self.commands:
            for classify in (danger.classify, danger.classify_shared):
                with self.subTest(command=command, classify=classify.__name__):
                    v = classify('Bash', {'command': command})
                    self.assertEqual(v.cats, ['service'])
                    self.assertEqual(v.cred, '')
                    self.assertIn('service.restart', v.rules)
                    self.assertIn('重启 Agent J 服务', v.why)
                    self.assertIn('Restarts the Agent J service', v.why_en)
                    self.assertIn('重启 Agent J 服务', goal.human('Bash', {'command':command}, v))
                    self.assertIn('restart the Agent J service', goal.human('Bash', {'command':command}, v, 'en'))

    def test_restart_card_row_names_restart_in_both_languages_and_still_requires_face(self):
        for command in self.commands:
            inp = {'command':command}
            v = danger.classify_shared('Bash', inp)
            self.assertEqual(approval_policy.decide('Bash', inp, v, requirement='uv'), ('face', 'service_restart'))
            self.assertEqual(approval_policy.card_row('Bash', v, 'uv', True, inp), 'service_restart')
        for lang, needle in (('zh', '重启 Agent J 服务会短暂断开连接'), ('en', 'briefly disconnects')):
            fields = approval_policy.card_fields('service_restart', lang)
            self.assertIn(needle, fields['why_scope'])
            self.assertEqual(fields['cls'], 'reversible')
            self.assertFalse(fields['policy_provisional'])
        for tail in ('cat /tmp/.env', 'printf x > ~/.claude/settings.json',
                     'printf x > ~/.local/state/agentj/approvals.json', 'echo "$API_KEY"'):
            inp = {'command':self.commands[1] + '; ' + tail}
            v = danger.classify_shared('Bash', inp)
            tier, row = approval_policy.decide('Bash', inp, v, requirement='uv')
            self.assertEqual(tier, 'face')
            self.assertNotEqual(row, 'service_restart')
            self.assertNotEqual(approval_policy.card_row('Bash', v, 'uv', True, inp), 'service_restart')



if __name__ == '__main__':
    unittest.main()
