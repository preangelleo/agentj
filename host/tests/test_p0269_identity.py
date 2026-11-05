"""0.26.9 selfheal E5 (backlog #473 + #469, chairman 11-04 「尽可能少的让用户来操作…直接开干」 and 18:4x 「必须让我回复一个字…非常的
stupid」): identity core v25. The owner's task is the authorisation — follow-up steps (dispatching, installing dependencies, changing
Agent J's own configuration) are done directly, the Agent never asks the owner to reply 「开 / 确认 / 再发」, only real business
trade-offs go to the owner, an owner-authorised follow-up also runs in a background-completion / system-notice turn (the host cards a
dangerous one), and a host card gets one line saying what it is for. Security wording stays: the five dangerous categories keep their
phone approvals, credentials never enter chat, a preflight or confirmed brief lifts no host approval or hard limit."""
import _hermetic  # noqa: F401
import unittest
from pathlib import Path

from agentj import main_identity

AJ = Path(__file__).resolve().parents[2]


def core(lang):
    return (main_identity.DATA / f"core.{lang}.md").read_text()


class CoreV25(unittest.TestCase):
    def test_version_and_manifest(self):
        self.assertEqual((main_identity.VERSION, main_identity.verify_core()["version"]), (25, 25))
        for lang in ("zh", "en"):
            self.assertTrue(core(lang).splitlines()[0].endswith("v25"))

    def test_owner_task_is_the_authorisation(self):
        rules = {
            "zh": ("主人交代的事即授权", "派活、补装工具、改自己的配置", "直接做", "不让主人回「开/确认/再发」", "只问真正的业务取舍",
                   "技术细节自定", "主人原话已授权的后续动作", "后台完成或系统提示那一轮也照做", "危险的由主机弹卡",
                   "主机弹卡就一句话说明用途"),
            "en": ("The owner's task is the authorization", "dispatching, installing missing tools, changing your own configuration",
                   'never make the owner reply "go", "confirm" or "send again"', "Ask only about real business trade-offs",
                   "decide technical details yourself", "authorized in their own words",
                   "background-completion or system-notice turn", "the host cards a dangerous one",
                   "When the host shows a card, say in one line what it is for"),
        }
        for lang, needles in rules.items():
            text = core(lang)
            for needle in needles:
                with self.subTest(lang=lang, needle=needle):
                    self.assertIn(needle, text)

    def test_old_ask_more_wording_is_gone(self):
        for lang, gone in (("zh", ("新装可多请示", "只有没把握或规则要求才请示", "一句话确认就开工")),
                           ("en", ("Fresh installs may ask more", "Ask only when truly unsure",
                                   "confirm in one line and start"))):
            for phrase in gone:
                with self.subTest(lang=lang, phrase=phrase):
                    self.assertNotIn(phrase, core(lang))

    def test_security_wording_kept(self):
        """Negative control: fewer questions never widens a red line."""
        for lang, needles in (
            ("zh", ("不解除五类危险操作的手机审批、凭据规则或底层硬限制", "预检、任务确认或想少被打断，都不授权付款、删除、对外发送、改凭据或改价，不解除硬限制",   # security-r4 F5
                    "凭据不进对话、日志、git 或手机通道", "密钥只走手机密钥卡", "登录不等于授权", "不写凭据")),
            ("en", ("never lifts the five dangerous operation phone approvals, credential rules or hard harness limits",
                    "Preflight, a confirmed brief or wanting fewer interruptions never authorizes spending, deleting, sending externally, changing credentials or changing prices, and never lifts hard limits",
                    "Credentials never enter chat, logs, git or the phone channel", "keys only use the phone secret card",
                    "Sign-in is not authorization", "never credentials")),
        ):
            for needle in needles:
                with self.subTest(lang=lang, needle=needle):
                    self.assertIn(needle, core(lang))

    def test_per_turn_reminder_carries_it(self):
        self.assertIn("交代的事直接做完，不要主人回「开/确认/再发」", main_identity.IDENTITY_REMINDER["zh"])
        self.assertIn('without making them reply "go", "confirm" or "send again"', main_identity.IDENTITY_REMINDER["en"])
        for lang in ("zh", "en"):
            self.assertLess(len(main_identity.IDENTITY_REMINDER[lang]), 600)

    def test_reply_buttons_only_for_business_trade_offs(self):
        self.assertIn("只问业务取舍", main_identity.REPLY_LINE_CLAUDE["zh"])
        self.assertIn("只为真正的业务取舍问主人", main_identity.REPLY_LINE["zh"])
        self.assertIn("only for real business trade-offs", main_identity.REPLY_LINE["en"])
        for lang, example in (("zh", "派不派"), ("en", "Dispatch it?")):   # the old example taught asking whether to dispatch
            self.assertNotIn(example, main_identity.REPLY_LINE[lang])
            self.assertNotIn(example, main_identity.REPLY_LINE_CLAUDE[lang])

    def test_switch_harness_installs_what_the_owner_asked_for(self):
        text = (AJ / "host/agentj/skills/agentj-switch-harness/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("Install only after the owner says yes", text)
        self.assertNotIn("没装的经主人", text)
        self.assertIn("install it directly, no extra yes", text)
        self.assertIn("直接按\nsetup-agents 文档帮装", text)
        self.assertIn("Login is the owner's own act", text)   # login stays the owner's


if __name__ == "__main__":
    unittest.main()
