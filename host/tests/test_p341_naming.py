"""P341 (backlog #31 / #25 / #19): the feedback platform is written "Agent J Father" everywhere we ship text, the one
public support mailbox is feedback@agentj.app, and a goal result quotes each blocked command as code."""
import _hermetic  # noqa: F401
import pathlib
import re
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import goal, main_identity
from agentj.agent import CODEX_FEEDBACK

AJ = pathlib.Path(__file__).resolve().parents[2]          # agentjarvis/ here; the public tree's root has host/ and web/
REPO = AJ.parent if AJ.name == "agentjarvis" else AJ
P = "agentjarvis/" if AJ.name == "agentjarvis" else ""
# Everything an owner, a customer or the main Agent reads. Internal history (CHANGELOG, reports, i18n caches / logs,
# ARCHITECTURE ADRs, .jarvis) keeps its old wording; the bridge's own Telegram guard matches unrelated words.
SHIPPED = [P + "host/agentj", P + "web/i18n", P + "web/public", "agentjarvis/site/content",
           "agentjarvis/site/public", "agentjarvis/site/lib", "agentjarvis/site/cli-data.json", "agentjarvis/docs",
           "agentjarvis/parity/features.json", "documentation/product/install.md", "agentjarvis/tools/manual_skill.mjs",
           "agentjarvis/support/mail_prompt.md"]
OLD_NAME = re.compile(r"Agent J ?(?:爸爸|父亲)|Agent J's father|Agent J father")


def shipped_hits(pattern: str) -> list[str]:
    out = subprocess.run(["git", "grep", "-n", "-I", "-E", pattern, "--", *SHIPPED, ":!" + P + "web/public/vendor"],
                         cwd=REPO, capture_output=True, text=True)
    return [line[:200] for line in out.stdout.splitlines()]


class Naming(unittest.TestCase):
    def test_no_old_father_names_in_shipped_text(self):
        self.assertEqual(shipped_hits(r"Agent J ?(爸爸|父亲)|Agent J's father|Agent J father"), [])

    def test_father_followed_by_chinese_has_a_space(self):
        self.assertEqual(shipped_hits(r"Agent J Father[一-龥]"), [])

    def test_identity_core_names_father_and_keeps_spoken_triggers(self):
        main_identity.verify_core()
        for lang in ("zh", "en"):
            core = (main_identity.DATA / f"core.{lang}.md").read_text()
            self.assertIn("Agent J Father", core)
            self.assertIsNone(OLD_NAME.search(core), lang)
            self.assertIn("告诉你爸爸", core)          # the owner's colloquial trigger stays a synonym
            self.assertIn("tell your dad", core)

    def test_feedback_skill_keeps_colloquial_triggers(self):
        skill = (AJ / "host/agentj/skills/feedback-to-agentj-father/SKILL.md").read_text()
        self.assertIn("aliases: [反馈给爸爸]", skill)
        self.assertIn("跟你爸爸说", skill)
        self.assertIsNone(OLD_NAME.search(skill))


class Mailbox(unittest.TestCase):
    def test_codex_error_card_points_at_feedback(self):
        self.assertEqual(CODEX_FEEDBACK, "feedback@agentj.app")

    def test_no_me_mailbox_in_shipped_text(self):
        # the old address still forwards (mail_desk reads it); nothing we publish names it except the desk's own note
        hits = [h for h in shipped_hits(r"me@agentj\.app") if not h.startswith("agentjarvis/support/mail_prompt.md")]
        self.assertEqual(hits, [])


class GoalCommands(unittest.TestCase):
    def test_code_span(self):
        self.assertEqual(goal.code_span("cp $(ls -d a*) $HOME/x"), "`cp $(ls -d a*) $HOME/x`")
        self.assertEqual(goal.code_span("echo `date`"), "`` echo `date` ``")
        self.assertEqual(goal.code_span("a ``b`` c"), "```a ``b`` c```")
        self.assertEqual(goal.code_span("  x\n  y "), "`x y`")
        self.assertEqual(goal.code_span(""), "")

    def test_result_text_quotes_blocked_commands_as_code(self):
        d = {"goal": "ship", "ended": "not_achieved", "grants": [], "used": {}, "counts": {},
             "blocked": [{"summary": "env -i HOME=$HOME sh -c \"cd $(ls -d ~/r*)\"", "why": "irreversible"}]}
        for lang in ("zh", "en"):
            text = goal.result_text(d, lang)
            # P356: P339's plain-words layout — the item line quotes the command as code when there is no gist
            self.assertIn(("Item 1: " if lang == "en" else "第 1 件：") + "`env -i HOME=$HOME sh -c \"cd $(ls -d ~/r*)\"`", text)



class NewIsClear(unittest.TestCase):
    """#50: /new (Codex's word) is /clear on every channel that parses a typed command."""
    def test_parse(self):
        from agentj import slash
        self.assertEqual(slash.parse("/new"), ("clear", ""))
        self.assertEqual(slash.parse(" /NEW "), ("clear", ""))
        self.assertEqual(slash.parse("/newsletter"), ("newsletter", ""))
        self.assertEqual(slash.parse("/clear"), ("clear", ""))
        self.assertNotIn("new", slash.WHITELIST)       # never a command of its own downstream

    def test_help_and_refusal_name_it(self):
        from agentj import slash
        self.assertIn("/new", slash.HELP)
        self.assertIn("/new", slash.REFUSE)

    def test_on_slash_folds_the_alias(self):
        src = (AJ / "host/agentj/serve.py").read_text()
        self.assertIn('name = slash.ALIAS.get(name, name)', src)


if __name__ == "__main__":
    unittest.main()
