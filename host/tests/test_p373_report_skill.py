"""P373: the built-in skill agentj-report — ships, triggers in both languages, and names only commands / flags the real
`agentj report` parser accepts; agentj-capability points recurring reports and mails to it."""
import _hermetic  # noqa: F401,I001
import argparse
import pathlib
import re
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import personalize, report as R  # noqa: E402

SKILLS = HERE.parent / "agentj" / "skills"
SKILL = SKILLS / "agentj-report" / "SKILL.md"


def _parser():
    p = argparse.ArgumentParser()
    R.add_parser(p.add_subparsers(dest="cmd"))
    return p


class Skill(unittest.TestCase):
    def test_bundled_with_front_matter(self):
        t = SKILL.read_text(encoding="utf-8")
        self.assertTrue(t.startswith("---\nname: agentj-report\ndescription: "))
        self.assertIn("agentj-report", personalize.bundled())
        head = t.split("\n---\n", 1)[0]
        for trigger in ("每天给我发市场调研邮件", "每周一把周报发我邮箱", "用 R14 模板", "自定义报告模板",
                        "send me a daily market-research e-mail", "weekly report by e-mail", "report template"):
            self.assertIn(trigger, head)

    def test_steps_name_the_real_commands(self):
        t = SKILL.read_text(encoding="utf-8")
        for needle in ("https://agentj.app/web-report/", "R15", "R02", "R14", "real data only", "never hand-write HTML",
                       "--to-owner", "brief", "agentj plaza install web-page-report --harness", "agentj report new-template"):
            self.assertIn(needle, t)
        actions = set(re.findall(r"agentj report ([a-z-]+)", t))
        self.assertTrue({"example", "validate", "send", "templates", "new-template"} <= actions, actions)
        choices = next(a for a in _parser()._subparsers._group_actions[0].choices["report"]._actions if a.dest == "action").choices
        self.assertLessEqual(actions, set(choices))
        known = set(_parser()._subparsers._group_actions[0].choices["report"]._option_string_actions)
        for flag in set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+|-[a-z])\b", t)) - {"--harness"}:
            self.assertIn(flag, known, flag)

    def test_final_approvals_and_freeze_are_explicit(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertNotIn("no approval needed", text)
        for phrase in ("final-message passkey approval required", "Workspace template names resolve before approval",
                       "自定义模板在审批期间有变化", "发给主人也需要"):
            self.assertIn(phrase, text)

    def test_capability_points_recurring_reports_here(self):
        cap = (SKILLS / "agentj-capability" / "SKILL.md").read_text(encoding="utf-8")
        line = next(x for x in cap.splitlines() if "agentj-report" in x)
        for needle in ("agentj report validate", "agentj report send", "brief"):
            self.assertIn(needle, line)


if __name__ == "__main__":
    unittest.main()
