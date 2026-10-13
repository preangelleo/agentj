"""P215: the working-root documents a fresh Agent J starts with carry the chairman philosophy (「人人都能做董事长」).

Owner decision 2026-10-13 (fresh install on a new host: IDENTITY 323 B, CONSTITUTION 359 B, 「待定」 everywhere): split the book's core
framework into the seeded documents, mark what is core (kept by every maintainer, upgraded by Agent J) and what is the owner's,
ship the Workflow Design Bible skill with the package, upgrade installed roots without overwriting the owner's words, and give a
fresh computer a first move (「你每周重复做的三件事是什么？」)."""
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import main_identity, onboarding, wizard  # noqa: E402

SKILLS = pathlib.Path(main_identity.__file__).with_name("skills")
# Content snapshot of every seeded body (frontmatter excluded). A change here is a deliberate change of what a fresh install
# says: update the hash together with the review of the text (the texts live in agentj/wizard/rootdocs/<lang>/).
SNAPSHOT = json.loads((pathlib.Path(__file__).with_name("p215_rootdocs_snapshot.json")).read_text())


def body(text):
    return wizard._split_fm(text)[1]


class Seed(unittest.TestCase):
    def seed(self, lang, harness=("claude",)):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = pathlib.Path(t.name)
        wizard.bootstrap_root(root, list(harness), lang)
        return root

    def test_snapshot(self):
        got = {}
        for lang in ("zh", "en"):
            root = self.seed(lang)
            for name in wizard.BOOT_SET:
                got[f"{lang}/{name}"] = hashlib.sha256(body((root / "documentation" / name).read_text()).encode()).hexdigest()
        self.assertEqual(got, SNAPSHOT)

    def test_core_and_personal_parts(self):
        for lang in ("zh", "en"):
            root = self.seed(lang)
            for name in wizard.BOOT_SET:
                text = (root / "documentation" / name).read_text()
                fm = wizard._frontmatter(text)
                self.assertEqual(fm["status"], "active", (lang, name))          # not 「草稿」 any more
                n_core = text.count("<!-- agentj:core v")
                self.assertEqual(n_core, 0 if name == "NEXT_SESSION.md" else 1, (lang, name))
                for bad in ("待定", "TBD", "status: draft", "依赖", "dependence", "豆包", "ChatGPT", "续费", "{{"):
                    self.assertNotIn(bad, text, (lang, name, bad))
                lines = body(text).count("\n")
                self.assertLessEqual(lines, 70, (lang, name))                    # one to two screens each
                if name != "NEXT_SESSION.md":
                    personal = ("可改" if lang == "zh" else "yours to change") if name not in ("ROLES.md", "MEMORY.md") else \
                        {"ROLES.md": ("CEO 花名册" if lang == "zh" else "CEO roster"),
                         "MEMORY.md": ("## 董事长" if lang == "zh" else "## The Chairman")}[name]
                    blk = wizard.core_block(text)
                    self.assertIn(personal, text[blk[1]:], (lang, name))         # the owner's part sits outside the block

    def test_book_framework_in_the_core_blocks(self):
        need = {
            "zh": {"CONSTITUTION.md": ("人人都能做董事长", "一家公司就是一个工作流", "面向生产", "文档就是公司", "全体员工都会辞职",
                                       "三条沉淀原则", "要重复做的事 → **工作流**", "要重复用的本事 → **技能**",
                                       "能写成命令行函数的确定性步骤 → **命令行工具**", "不写成只靠提示词约束",
                                       "读报告，不看退出码", "放权是长出来的", "派活而不扛活", "中专生", "手机是遥控器"),
                   "IDENTITY.md": ("董事长助理", "「董事长」", "坐到董事长的位置上", "派活而不扛活", "每次醒来"),
                   "SOUL.md": ("晨报只报结果与待决", "引导董事长思维", "不说教，也不藏信息", "三个习惯"),
                   "WORKFLOW.md": ("意图往哪沉淀", "Workflow Design Bible", "agentj-workflow-wizard", "不用联网下载",
                                   "退出码不能证明成功", "所属 CEO 修复并重跑", "晨报", "你每周重复做的三件事是什么？",
                                   "全体员工辞职", "模型升级后复盘"),
                   "ROLES.md": ("董事长 → 助理 → CEO → 员工", "小助理", "董事长 → 助理 → 工作流 → 技能 → 命令行"),
                   "MEMORY.md": ("放权规则怎么长出来", "不存凭据", "## 放权规则")},
            "en": {"CONSTITUTION.md": ("Anyone can be a chairman", "A company is a workflow", "Built for production",
                                       "The documents are the company", "whole staff resigns at every moment",
                                       "Three settling rules", "Work that repeats → a **workflow**",
                                       "A capability used again and again → a **skill**",
                                       "can be a command-line function → a **command-line tool**", "only a prompt enforces",
                                       "Read the report, not the exit code", "Delegation grows", "Dispatch, don't carry",
                                       "trainees", "remote control"),
                   "IDENTITY.md": ("chief of staff", "\"Chairman\"", "takes the chairman's seat", "Dispatch, don't carry",
                                   "Every time I wake up"),
                   "SOUL.md": ("results and pending decisions only", "think like a chairman", "never lecture", "three habits"),
                   "WORKFLOW.md": ("where an intention settles", "Workflow Design Bible", "agentj-workflow-wizard", "no download",
                                   "Exit codes alone prove nothing", "owning CEO for repair and a rerun", "Morning brief",
                                   "What are three things you do again and again every week?", "whole staff resigned",
                                   "Review after a model upgrade"),
                   "ROLES.md": ("Chairman → chief of staff → CEO → staff", "Junior assistant",
                                "Chairman → chief of staff → workflow → skill → command line"),
                   "MEMORY.md": ("How delegation grows", "Never store credentials", "## Delegation rules")},
        }
        for lang, docs in need.items():
            for name, words in docs.items():
                text = wizard.BOOT_DOCS[lang][name]
                blk = wizard.core_block(text)
                for w in words:
                    where = text if w.startswith("## ") else blk[2]               # headings the Agent appends to stay outside
                    self.assertIn(w, where, (lang, name, w))

    def test_tables_and_sections_the_agent_appends_to_stay_last(self):
        for lang, head in (("zh", "## 放权规则"), ("en", "## Delegation rules")):
            root = self.seed(lang)
            self.assertEqual((root / "documentation/ROLES.md").read_text().rstrip("\n").splitlines()[-1], "|---|---|---|---|---|")
            mem = (root / "documentation/MEMORY.md").read_text()
            self.assertGreater(mem.index(head), mem.index("<!-- /agentj:core -->"))
            self.assertFalse([c for c in wizard.doctor(root) if c["status"] == "fail"], lang)


class Upgrade(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        wizard.bootstrap_root(self.root, ["claude"], "zh")
        self.doc = self.root / "documentation"

    def pretend_old_seed(self, name, text, edited=False):
        """The file as an older Agent J seeded it (manifest = these bytes); edited=True: then someone changed it."""
        p = self.doc / name
        p.write_text(text)
        m = wizard.read_manifest(self.root)
        m["files"]["documentation/" + name]["sha256"] = wizard.sha256(text.encode())
        m.get("core", {}).pop("documentation/" + name, None)
        wizard._write_manifest(self.root, m)
        if edited:
            p.write_text(text + "- 董事长：王老板，做跨境电商\n")
        return p

    def rows(self):
        return {r["path"]: r["status"] for r in wizard.upgrade_root_docs(self.root)}

    def test_fresh_root_is_already_current_and_idempotent(self):
        self.assertEqual(self.rows(), {})
        snap = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.rows(), {})
        self.assertEqual(snap, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_untouched_old_seed_is_replaced(self):
        old = "---\ntype: Identity\nstatus: draft\n---\n- 名字与偏好：待定；运行工作流向导确认。\n"
        p = self.pretend_old_seed("IDENTITY.md", old)
        self.assertEqual(self.rows(), {"documentation/IDENTITY.md": "updated"})
        self.assertEqual(body(p.read_text()), wizard.BOOT_DOCS["zh"]["IDENTITY.md"])
        self.assertEqual(self.rows(), {})

    def test_edited_old_seed_keeps_every_owner_word_and_gains_the_core_once(self):
        old = "---\ntype: Roles\nstatus: draft\n---\n# 角色与 CEO 花名册\n\n我自己写的分工说明。\n\n| 工作流 | CEO 入口 |\n|---|---|\n"
        p = self.pretend_old_seed("ROLES.md", old, edited=True)
        mine = p.read_text()
        self.assertEqual(self.rows(), {"documentation/ROLES.md": "core-added"})
        new = p.read_text()
        for line in mine.splitlines():
            self.assertIn(line, new)
        self.assertEqual(new.count("<!-- agentj:core v"), 1)
        self.assertIn(wizard.CORE_NOTE["zh"].strip(), new)
        self.assertTrue(new.startswith("---\ntype: Roles\nstatus: draft\n---\n# 角色与 CEO 花名册\n\n<!-- agentj:core v"))
        self.assertTrue(new.rstrip("\n").endswith("王老板，做跨境电商"))     # the end of the file (the roster) stays last
        self.assertEqual(self.rows(), {})
        self.assertEqual(p.read_text(), new)

    def test_owner_part_survives_a_core_update(self):
        p = self.doc / "SOUL.md"
        text = p.read_text()
        blk = wizard.core_block(text)
        old_block = blk[2].replace("说董事长的语言", "说主人的语言")         # the block an older version shipped
        edited = text[:blk[0]] + old_block + text[blk[1]:] + "- 语气：直接、少客套\n"
        p.write_text(edited)
        m = wizard.read_manifest(self.root)
        m["core"]["documentation/SOUL.md"] = wizard.sha256(old_block.encode())
        wizard._write_manifest(self.root, m)
        self.assertEqual(self.rows(), {"documentation/SOUL.md": "core-updated"})
        now = p.read_text()
        self.assertIn("- 语气：直接、少客套", now)
        self.assertEqual(wizard.core_block(now)[2], wizard.core_block(wizard.BOOT_DOCS["zh"]["SOUL.md"])[2])

    def test_core_block_the_owner_edited_is_never_overwritten(self):
        p = self.doc / "CONSTITUTION.md"
        text = p.read_text().replace("人人都能做董事长。", "人人都能做董事长，我也是。")
        p.write_text(text)
        self.assertEqual(self.rows(), {"documentation/CONSTITUTION.md": "kept"})
        self.assertEqual(p.read_text(), text)
        self.assertIn("人人都能做董事长。主人是董事长", (self.doc / "CONSTITUTION.md.wizard-new").read_text())
        self.assertIn("documentation/CONSTITUTION.md", wizard.pending(self.root))

    def test_documents_we_never_wrote_and_next_session_are_left_alone(self):
        other = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(other))
        (other / "documentation").mkdir()
        (other / "documentation/CONSTITUTION.md").write_text("# 我自己的宪法\n")
        wizard.bootstrap_root(other, ["claude"], "zh")
        self.assertEqual(wizard.upgrade_root_docs(other), [])
        self.assertEqual((other / "documentation/CONSTITUTION.md").read_text(), "# 我自己的宪法\n")
        nxt = self.doc / "NEXT_SESSION.md"
        nxt.write_text("# 下一次会话\n\n在途：周报工作流第一次试跑\n")
        self.assertEqual(self.rows(), {})
        self.assertEqual(nxt.read_text(), "# 下一次会话\n\n在途：周报工作流第一次试跑\n")

    def test_interview_output_gets_only_the_core_block(self):
        """A root document the wizard interview placed (`apply`) is the owner's company in their words: never replaced whole."""
        stage = self.root / wizard.STAGING_REL / "documentation"
        stage.mkdir(parents=True)
        mine = "---\ntype: Pipeline\nstatus: draft\n---\n# 工作流程\n\n每天 8 点日报，周一竞品简报。\n"
        (stage / "WORKFLOW.md").write_text(mine)
        wizard.apply(self.root, self.root / wizard.STAGING_REL)
        self.assertEqual((self.doc / "WORKFLOW.md").read_text(), mine)
        self.assertEqual(self.rows(), {"documentation/WORKFLOW.md": "core-added"})
        now = (self.doc / "WORKFLOW.md").read_text()
        self.assertIn("每天 8 点日报，周一竞品简报。", now)
        self.assertIn("Workflow Design Bible", now)

    def test_english_file_gets_the_english_core(self):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        wizard.bootstrap_root(root, ["codex"], "en")
        p = root / "documentation/IDENTITY.md"
        p.write_text("---\ntype: Identity\n---\n# Identity\n\n- I am Max.\n")
        self.assertEqual({r["path"]: r["status"] for r in wizard.upgrade_root_docs(root)}, {"documentation/IDENTITY.md": "core-added"})
        self.assertIn("I am the **chief of staff**", p.read_text())
        self.assertNotIn("董事长", p.read_text())


class WorkflowDesignBible(unittest.TestCase):
    def test_skill_ships_and_lands_in_the_working_root(self):
        self.assertTrue((wizard.SKILL_SRC / "SKILL.md").is_file())
        skill = (wizard.SKILL_SRC / "SKILL.md").read_text()
        self.assertIn("name: " + wizard.SKILL_NAME, skill)
        self.assertIn("Workflow Design Bible (MIT)", skill)
        self.assertIn("agentj:core", skill)                                    # keep the core blocks when rewriting
        self.assertIn("Core blocks (keep verbatim)", (wizard.SKILL_SRC / "references/files.md").read_text())
        for lang in ("zh", "en"):
            self.assertIn(wizard.SKILL_NAME, wizard.BOOT_DOCS[lang]["WORKFLOW.md"])
            self.assertIn("https://github.com/preangelleo/workflow-design-bible", wizard.BOOT_DOCS[lang]["WORKFLOW.md"])
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            wizard.bootstrap_root(root, ["codex"], "zh")
            wizard.refresh_root(root, ["codex"])
            placed = root / ".agents/skills" / wizard.SKILL_NAME / "SKILL.md"
            self.assertEqual(placed.read_bytes(), (wizard.SKILL_SRC / "SKILL.md").read_bytes())
            self.assertFalse((root / ".claude/skills" / wizard.SKILL_NAME).exists())
            # an edited copy is kept; the shipped version waits next to it
            placed.write_text(placed.read_text() + "\n我的补充\n")
            wizard.refresh_root(root, ["codex"])
            self.assertTrue(placed.read_text().endswith("我的补充\n"))
            self.assertTrue(placed.with_name("SKILL.md.wizard-new").is_file())
            self.assertFalse([c for c in wizard.doctor(root) if c["status"] == "fail"])

    def test_refresh_root_never_raises_on_a_missing_root(self):
        self.assertEqual(wizard.refresh_root(pathlib.Path("/nonexistent/agentj-p215")), [])


class FirstConversation(unittest.TestCase):
    def test_the_three_repeated_things_question_everywhere_it_is_needed(self):
        fr = (SKILLS / "agentj-first-run/SKILL.md").read_text()
        cap = (SKILLS / "agentj-capability/SKILL.md").read_text()
        for text in (fr, cap):
            self.assertIn("你每周重复做的三件事是什么？", text)
            self.assertIn("What are three things you do again and again every week?", text)
        self.assertIn("Workflow Design Bible", fr)
        s = {"computer": True, "phone": True}
        self.assertIn("你每周重复做的三件事是什么？", onboarding.welcome_prompt("phone", "", s, "zh"))
        self.assertIn("three things you do again and again every week", onboarding.welcome_prompt("phone", "", s, "en"))
        for lang in ("zh", "en"):
            nxt = wizard.BOOT_DOCS[lang]["NEXT_SESSION.md"]
            self.assertIn("agentj-first-run", nxt)
            self.assertIn("WORKFLOW.md", nxt)


if __name__ == "__main__":
    unittest.main()
