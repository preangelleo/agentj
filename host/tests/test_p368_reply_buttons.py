"""P368 (backlog #78 / #79, Leo 10-17): the Agent is told when to use one-tap answers `[选项](reply:选项)`, the question card
(`agentj ask`) or the approval card — on every harness and in both languages, inline under INLINE_MAX on the shared-Claude
hook path — and `agentj ask` refuses an option label over 12 字 or a description / preview over 60 字 (never truncated)."""
import _hermetic  # noqa: F401
import re
import unittest
from pathlib import Path

from agentj import ask, main_identity, shared

AJ = Path(__file__).resolve().parents[2]


class Identity(unittest.TestCase):
    def test_every_harness_and_language_carries_the_line(self):
        for lang in ("zh", "en"):
            for harness in (None, "claude", "codex", "opencode"):
                with self.subTest(lang=lang, harness=harness):
                    line = (main_identity.REPLY_LINE_CLAUDE if harness == "claude" else main_identity.REPLY_LINE)[lang]
                    self.assertIn(line, main_identity.prompt({"language": lang}, harness=harness))
        short = main_identity.REPLY_LINE_CLAUDE["zh"]          # the hook path: the same rule, compact
        for s in ("(reply:", "2–4", "末尾", "必须带"):
            self.assertIn(s, short)

    def test_when_to_use_which(self):
        zh, en = main_identity.REPLY_LINE["zh"], main_identity.REPLY_LINE["en"]
        for s in ("(reply:", "2–4", "不阻塞", "提问卡", "审批卡", "必须带按钮", "12 字", "60 字"):
            self.assertIn(s, zh)
        for s in ("(reply:", "2–4", "without blocking", "question card", "approval card", "must carry the buttons"):
            self.assertIn(s, en)
        self.assertEqual(len(re.findall(r"\[[^\]]+\]\(reply:[^)]+\)", zh)), 7, "the template + three examples, two buttons each")
        self.assertEqual(len(re.findall(r"\[[^\]]+\]\(reply:[^)]+\)", en)), 7)

    def test_zh_stays_inline_on_the_shared_claude_path(self):
        zh = main_identity.prompt({"language": "zh"}, harness="claude")
        self.assertLessEqual(len(zh), shared.SharedClaudeAgent.INLINE_MAX)
        self.assertNotIn("Telegram 和对外渠道仍会打码", zh, "the stale masking sentence is gone (the P-line says TG private is not masked)")
        self.assertIn("TG 私聊", zh)

    def test_core_hash_untouched(self):
        self.assertNotIn("reply:", main_identity.prompt({"language": "zh"}).split(main_identity.LANGUAGE_LINE["zh"])[0])

    def test_manual_says_it_too(self):
        for lang, needle in (("zh", "一键回复按钮"), ("en", "One-tap answer")):
            text = (AJ / "host/agentj/skills/agentj-manual" / f"manual.{lang}.md").read_text(encoding="utf-8")
            self.assertIn(needle, text)
            self.assertIn("Dispatch" if lang == "en" else "派", text)


class AskLimits(unittest.TestCase):
    def q(self, label="好", d="", pv=""):
        return [{"question": "Q", "options": [{"label": label, "description": d, "preview": pv}, {"label": "B"}]}]

    def test_at_the_limit_passes(self):
        ask.build(self.q("一" * 12, "说" * 60, "预" * 60))
        ask.build(self.q("x" * 24, "d" * 120, "p" * 120))
        ask.build(self.q("Use plan A", "ab 中文 mixed"))

    def test_over_is_refused_naming_the_limit(self):
        for args, what in ((("一" * 13,), "label"), (("x" * 25,), "label"), (("好", "说" * 61), "description"),
                           (("好", "", "p" * 121), "preview"), (("好", "", "中" * 50 + "x" * 21), "preview")):
            with self.subTest(what=what, n=len("".join(args))), self.assertRaises(ask.AskError) as cm:
                ask.build(self.q(*args))
            self.assertIn(what, str(cm.exception))
            self.assertIn("shorten", str(cm.exception))

    def test_zi(self):
        self.assertEqual(ask.zi("中文ab"), 3)
        self.assertEqual(ask.zi("ＡＢ"), 2)

    def test_cli_reports_bad_not_shown(self):
        import argparse
        import io
        import json
        from contextlib import redirect_stdout
        from unittest.mock import patch
        a = argparse.Namespace(json=None, question="Q?", option=["一" * 13, "B"], preview=None, header=None, multi=False,
                               no_other=False, wait_id=None, wait_s=1, compact=True)
        out = io.StringIO()
        with patch("agentj.names.ctl_call", side_effect=lambda st, req, **k: {"ok": True, "questions": ask.build(req["questions"])}), \
             patch("agentj.state.State"), redirect_stdout(out), self.assertRaises(SystemExit) as ex:
            ask.cmd_ask(a)
        res = json.loads(out.getvalue())
        self.assertEqual((res["result"], res["shown"], ex.exception.code), ("bad", False, 2))
        self.assertIn("label", res["note"])


if __name__ == "__main__":
    unittest.main()
