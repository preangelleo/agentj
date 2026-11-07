"""#398: the paired owner can recover AI login without returning to the host."""
import _hermetic
from pathlib import Path
import unittest
from agentj import harness_cmds, main_identity, shared

ROOT = Path(__file__).resolve().parents[2]

class PhoneAuthGuidance(unittest.TestCase):
    def test_login_command_points_to_phone_not_host(self):
        zh,en = harness_cmds._NONE_ALL['login']
        self.assertIn('登录救援',zh)
        self.assertIn('login rescue',en.lower())
        self.assertNotIn("computer's terminal",en)

    def test_identity_directs_recovery_to_phone_and_keeps_inline_budget(self):
        zh=main_identity.prompt({'language':'zh'},harness='claude')
        en=main_identity.prompt({'language':'en'})
        self.assertIn('登录/额度故障',zh)
        self.assertIn('Web Terminal',en)
        self.assertLessEqual(len(zh),shared.SharedClaudeAgent.INLINE_MAX)

    def test_manual_and_packaged_copy_have_phone_quota_recovery(self):
        for lang,token in [('zh','额度升级'),('en','plan upgrade')]:
            for p in [ROOT/'site/content/docs/14-manual'/('zh.src.md' if lang=='zh' else 'en.md'),
                      ROOT/'host/agentj/skills/agentj-manual'/f'manual.{lang}.md']:
                with self.subTest(path=str(p)):
                    text=p.read_text();self.assertIn(token,text)
                    self.assertNotIn('run `codex login` on the Agent J host',text)
                    self.assertNotIn('登录失效时，在Agent J 主机上运行 `codex login`',text)

    def test_faq_login_recovery_does_not_send_owner_back_to_host(self):
        for name in ['en.md','zh.src.md']:
            text=(ROOT/'site/content/docs/08-faq'/name).read_text()
            self.assertNotIn('then run `codex login` on the Agent J host',text)
            self.assertNotIn('然后在Agent J 主机上运行 `codex login`',text)
            self.assertIn('AI login rescue' if name=='en.md' else 'AI 登录救援',text)

class RuntimePhoneGuidance(unittest.TestCase):
    def test_codex_errors_keep_recovery_on_phone(self):
        from unittest.mock import patch
        from agentj import agent
        errors=['HTTP 401 Unauthorized', 'usage not included',
                'Codex is not enabled for your workspace',
                'Country, region, or territory not supported',
                'Codex（app-server）没有启动', 'Codex 退出了（137）']
        with patch('agentj.harness.codex_login',return_value=('warn','not signed in')):
            for lang in ['zh','en']:
                for error in errors:
                    with self.subTest(lang=lang,error=error):
                        message=agent.codex_failure(error,None,lang)[1].split('\n\n')[0]
                        self.assertRegex(message,r'网页终端|登录救援|Web Terminal|login rescue')
                        self.assertNotRegex(message,r'on the Agent J host run `codex login`|在这台Agent J 主机上运行 `codex login`')

    def test_stalled_setup_gives_web_terminal_not_physical_host(self):
        from agentj.agent import stall_text
        for lang in ['zh','en']:
            for reason in ['prompt','quiet']:
                message=stall_text('codex',reason,'setup',30,lang)
                self.assertIn('网页终端' if lang=='zh' else 'Web Terminal',message)

    def test_claude_and_daemon_help_is_available_from_phone(self):
        from agentj import claude_auth, service, codex_daemon
        for lang in ['zh','en']:
            self.assertIn('登录救援' if lang=='zh' else 'login rescue',claude_auth.failure_notice(lang))
        self.assertIn('网页终端',service.token_hint('agentj'))
        for zh in [True,False]:
            fix=codex_daemon.mismatch_text('0.1.0','0.2.0',zh)[1]
            self.assertIn('网页终端' if zh else 'Web Terminal',fix)
