"""P186 (texts updated by P215): the chairman philosophy (「人人都能做董事长」) in the packaged core, the seeded root documents and the wizard guide."""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import main_identity, shared, wizard  # noqa: E402

V10 = {"zh": 4359, "en": 13148}   # core v10 characters (0.18.4): the P186 budget is ≤ 120 % of these


class Core(unittest.TestCase):
    def test_philosophy_every_turn(self):
        needles = {
            "zh": ("人人都能做董事长", "工作流就是董事长的公司", "不为闲聊或搜索", "帮董事长赚钱",
                   "引导主人像董事长一样思考", "一两句讲清原理", "不当场排查", "待定的决定", "不说教也不藏信息",
                   "一个代号", "全局技能", "放它本地", "配齐技能、命令行与 MCP",
                   "文档就是公司", "随时整批换新", "模型大升级后", "documentation/WORKFLOW.md"),
            "en": ("Anyone can be a chairman", "one of the Chairman's companies", "not to chat or search", "help the Chairman earn",
                   "think like a chairman", "in a sentence or two", "without investigating on the spot", "pending decisions", "never lecture, never withhold",
                   "a code name", "a global skill", "a local skill", "command-line tools and MCP",
                   "The documents are the company", "replaced at any moment", "major model upgrade", "documentation/WORKFLOW.md"),
        }
        for lang, words in needles.items():
            text = main_identity.prompt({"language": lang})
            for w in words:
                self.assertIn(w, text, (lang, w))
            # the philosophy line sits right under the positioning line
            self.assertIn(words[0], text.splitlines()[2])

    def test_no_unfriendly_or_competitor_wording(self):
        for lang in ("zh", "en"):   # the core only: a host line names the "ChatGPT App" as a Codex client, which is a fact
            text = (main_identity.DATA / f"core.{lang}.md").read_text()
            for bad in ("依赖", "dependence", "dependency", "豆包", "ChatGPT", "免费版", "free tier", "续费", "renew"):
                self.assertNotIn(bad, text, (lang, bad))

    def test_length_budget_and_zh_inline(self):
        for lang, chars in V10.items():
            self.assertLessEqual(len((main_identity.DATA / f"core.{lang}.md").read_text()), chars * 1.2, lang)
        # zh keeps the shared-session identity inline (P159): the core did not grow past v10
        self.assertLessEqual(len((main_identity.DATA / "core.zh.md").read_text()), V10["zh"])
        self.assertLessEqual(len(main_identity.prompt({"language": "zh"}, harness="claude")), shared.SharedClaudeAgent.INLINE_MAX)   # P165: the shared-Claude hook text

    def test_moved_detail_still_lives_in_the_skills(self):
        skills = pathlib.Path(main_identity.__file__).with_name("skills")
        update = (skills / "agentj-update/SKILL.md").read_text()
        for w in ("03:00–05:00", "roll back", "preserve explicit off"):
            self.assertIn(w, update)
        bots = (skills / "agentj-bots/SKILL.md").read_text()
        for w in ("provider terms", "OpenRouter", "verifies decisions"):
            self.assertIn(w, bots)
        cap = (skills / "agentj-capability/SKILL.md").read_text()
        for w in ("why / how / reuse", "acceptance", "This becomes a workflow", "这件事会建成一个工作流"):
            self.assertIn(w, cap)


class RootDocs(unittest.TestCase):
    def seed(self, lang):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = pathlib.Path(t.name)
        wizard.bootstrap_root(root, lang=lang)
        return {p.name: p.read_text() for p in (root / "documentation").glob("*.md")}, root

    def test_each_principle_in_its_document(self):
        where = {
            "zh": {"CONSTITUTION.md": ("人人都能做董事长", "一家公司就是一个工作流", "面向生产", "文档就是公司", "全体员工都会辞职", "手机是遥控器",
                                       "终端里一样能跑", "先在手机上请董事长批准", "不兼任任何工作流的 CEO"),
                   "IDENTITY.md": ("董事长助理", "「董事长」", "自称「助理」"),
                   "SOUL.md": ("结果在前", "引导董事长思维", "答准、答短", "不说教，也不藏信息", "点头才建"),
                   "WORKFLOW.md": ("退出码不能证明成功", "意图往哪沉淀", "一个代号", "全局技能", "本地技能", "命令行工具",
                                   "全体员工辞职", "NEXT_SESSION", "模型升级后复盘", "中专生"),
                   "ROLES.md": ("董事长（主人）", "董事长助理（主 Agent）", "工作流 CEO", "子 Agent（员工）", "小助理")},
            "en": {"CONSTITUTION.md": ("Anyone can be a chairman", "A company is a workflow", "Built for production",
                                       "The documents are the company", "whole staff resigns at every moment", "remote control",
                                       "runs the same way in a terminal", "Ask the Chairman on the phone", "never a workflow's CEO"),
                   "IDENTITY.md": ("chief of staff", "\"Chairman\""),
                   "SOUL.md": ("results first", "think like a chairman", "accurately and briefly", "never lecture", "after a yes"),
                   "WORKFLOW.md": ("Exit codes alone prove nothing", "where an intention settles", "code name", "global when",
                                   "local inside", "command-line tool", "whole staff resigned", "NEXT_SESSION",
                                   "Review after a model upgrade"),
                   "ROLES.md": ("Chairman (the owner)", "Chief of staff (main Agent)", "Workflow CEO", "Sub-Agents (staff)",
                                "Junior assistant")},
        }
        for lang, docs in where.items():
            got, _ = self.seed(lang)
            for name, words in docs.items():
                for w in words:
                    self.assertIn(w, got[name], (lang, name, w))
            for name, text in got.items():
                for bad in ("依赖", "dependence", "dependency", "豆包", "ChatGPT", "续费", "{{"):
                    self.assertNotIn(bad, text, (lang, name, bad))

    def test_roster_table_is_last_so_wizard_rows_join_it(self):
        for lang in ("zh", "en"):
            got, _ = self.seed(lang)
            last = got["ROLES.md"].rstrip("\n").splitlines()[-1]
            self.assertEqual(last, "|---|---|---|---|---|")

    def test_seeded_root_passes_wizard_doctor(self):
        for lang in ("zh", "en"):
            _, root = self.seed(lang)
            self.assertFalse([c for c in wizard.doctor(root) if c["status"] == "fail"], lang)


class WizardGuide(unittest.TestCase):
    def test_interview_documents_carry_the_philosophy(self):
        files = (wizard.SKILL_SRC / "references/files.md").read_text()
        for w in ("我们相信 / What we hold true", "built for production", "guiding posture", "意图往哪沉淀 / Where an intention settles",
                  "交接标准 / Handover standard", "模型升级复盘 / Model-upgrade review", "分工 / Who does what"):
            self.assertIn(w, files)


if __name__ == "__main__":
    unittest.main()
