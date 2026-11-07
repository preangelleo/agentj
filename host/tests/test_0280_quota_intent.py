"""The reported upgrade sentence must reach phone recovery while Codex is blocked."""
import unittest
from types import SimpleNamespace as NS
from agentj import quota_refresh


class UpgradeIntent(unittest.TestCase):
    def host(self, state='rate_limit', kind='codex'):
        return NS(agent=NS(kind=kind), auth_rescue=NS(state={'codex':{'cls':state}}))

    def test_actual_owner_report_and_completed_upgrade_phrases(self):
        for text in ('升级了','我已经升级了','我刚刚升级了','升級了','我剛剛升級了',
                     '我們今天Codex額度用完，我剛剛升級了，可是Jarvis還是顯示額度用完，等待重置，怎麼辦？'):
            with self.subTest(text=text):
                self.assertTrue(quota_refresh.upgrade_intent(self.host(),text))

    def test_other_speakers_future_intent_and_system_upgrades_are_not_login_actions(self):
        for text in ('他说他升级了','我明天升级','我说他升级了','我刚刚升级了系统','“我升级了”是什么意思？'):
            with self.subTest(text=text):
                self.assertFalse(quota_refresh.upgrade_intent(self.host(),text))

    def test_only_current_codex_quota_context(self):
        self.assertFalse(quota_refresh.upgrade_intent(self.host('ok'),'我已经升级了'))
        self.assertFalse(quota_refresh.upgrade_intent(self.host(kind='claude'),'我已经升级了'))
