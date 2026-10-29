"""P269 text-contract regression, not a live model behavior evaluation."""
import _hermetic  # noqa: F401
import json
from pathlib import Path
import unittest
from agentj import main_identity, shared

class GoalPaths(unittest.TestCase):
    def test_prompt_fixtures_keep_all_gap_routes(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/p269_goal_path.json").read_text())
        self.assertEqual({f["id"] for f in fixtures}, {"api", "no-api", "platform", "combined", "danger"})
        for case in fixtures:
            self.assertTrue(case["prompt"])
            for lang, needles in case["needles"].items():
                with self.subTest(case=case["id"], language=lang):
                    text = main_identity.prompt({"language": lang})
                    for needle in needles:
                        self.assertIn(needle, text)

    def test_check_first_and_order(self):
        for lang, start, api, browser, platform in (
            ("zh", "先自己检测现有能力", "①官方 API", "②网站没有 API", "③需平台底层能力"),
            ("en", "Check existing capabilities", "(1) official API", "(2) No API", "(3) Missing platform primitives"),
        ):
            text = main_identity.prompt({"language": lang})
            self.assertLess(text.index(start), text.index(api))
            self.assertLess(text.index(api), text.index(browser))
            self.assertLess(text.index(browser), text.index(platform))

    def test_no_budget_increase_and_verified_manifest(self):
        self.assertEqual(main_identity.verify_core()["version"], 24)
        for lang, prior in (("zh", 4408), ("en", 13957)):   # P290: no growth over the shipped v14 (P269 measured against v12)
            self.assertLessEqual(len((main_identity.DATA / f"core.{lang}.md").read_text()), prior)
        self.assertEqual((shared.SharedClaudeAgent.INLINE_MAX, shared.SharedClaudeAgent.HOOK_MAX), (9500, 10000))
        self.assertLessEqual(len(main_identity.prompt({"language": "zh"}, harness="claude")), 9500)

    def test_skill_and_manual_bridge_existing_cards(self):
        root = main_identity.DATA.parent
        for rel in ("skills/agentj-capability/SKILL.md", "skills/agentj-manual/manual.zh.md", "skills/agentj-manual/manual.en.md"):
            text = (root / rel).read_text()
            for needle in ("/feedback-to-agentj-father", "API"):
                self.assertIn(needle, text)
        cap = (root / "skills/agentj-capability/SKILL.md").read_text()
        for needle in ("agentj browser status --json", "agentj browser check --site", "agentj browser login", "unknown", "unavailable", "disabled", "agentj secret request", "agentj capability prepare", "preview/consent/receipt"):
            self.assertIn(needle, cap)

if __name__ == "__main__":
    unittest.main()
