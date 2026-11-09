"""#477 (chairman 11-04 21:34 / 21:3x): 「董事长助理不能掉到细节里被淹没，验收最好有 CLI 提前设定好能 call」「主 agent 负责设计每一个
sub agent 的职责，能力和验收标准」「也要负责监督，但是绝不能下场干活」「他分担的是董事长这边的责任，他分担的不是 Agent 的责任」.

Gate: the four hard rules (+ the background-check rule) sit in BOTH identity cores, on the same line in zh and en (semantic
pairing, not a translation check), inside the unchanged budgets; the per-turn reminder, the host's background-done notice,
the heartbeat line, the bundled skills and the Workflow Design Bible templates carry the acceptance command and the
never-do-the-work rule. Acceptance: `python -m unittest test_p477_assistant_principle` → OK = PASS."""
import _hermetic  # noqa: F401
import asyncio
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agentj import background_done as done, busy, main_identity, shared, wizard

AGENTJ = pathlib.Path(main_identity.__file__).resolve().parent

# rule → (zh needles, en needles); every needle of a rule must sit on the same core line in both languages
RULES = {
    "owner-load": (("默认分工：分担主人责任",),
                   ("By default, carry the owner's responsibilities",)),
    "design-supervise": (("你设计每个 CEO 或子 Agent 的职责、能力和验收标准并监督",),
                         ("you design each CEO's or sub-Agent's duties, capabilities and acceptance criteria and supervise",)),
    "delegation-default": (("派活不是禁令", "不另验身份"),
                          ("Delegation is a default, not a prohibition", "without an extra identity check")),
    "acceptance-command": (("每张派单带预先写好的验收命令", "只跑验收命令、看结论再报"),
                           ("Every dispatch carries a pre-written acceptance command",
                            "only run the acceptance command and read its verdict")),
    "background-check-is-ours": (("后台活完成由你核对，不推给主人",),
                                 ("Checking finished background work is yours, never the owner's",)),
}


def core(lang):
    return (main_identity.DATA / f"core.{lang}.md").read_text(encoding="utf-8")


def line_of(text, needle):
    for i, line in enumerate(text.splitlines()):
        if needle in line:
            return i
    return None


class Core(unittest.TestCase):
    def test_rules_in_both_languages_on_the_same_line(self):
        zh, en = core("zh"), core("en")
        for rule, (zn, en_n) in RULES.items():
            lines = set()
            for lang, text, needles in (("zh", zh, zn), ("en", en, en_n)):
                for n in needles:
                    with self.subTest(rule=rule, lang=lang, needle=n):
                        i = line_of(text, n)
                        self.assertIsNotNone(i, f"{rule}: missing in core.{lang}.md")
                        lines.add(i)
            with self.subTest(rule=rule, check="zh/en same line"):
                self.assertEqual(len(lines), 1, f"{rule}: zh/en needles on different lines {sorted(lines)}")

    def test_old_do_it_yourself_wording_is_gone(self):
        for lang, gone in (("zh", ("能亲跑验收就亲跑", "派单写清完成证据", "不亲自做工作流的具体业务", "助理常亲自救火")),
                           ("en", ("rerun acceptance when you can", "Every dispatch names its completion evidence",
                                   "never do workflow business work yourself", "repeatedly doing a workflow's work by hand"))):
            for phrase in gone:
                with self.subTest(lang=lang, phrase=phrase):
                    self.assertNotIn(phrase, core(lang))

    def test_budgets_not_raised(self):
        self.assertEqual(main_identity.verify_core()["version"], 28)          # manifest + pinned hashes agree
        for lang, cap in (("zh", 4408), ("en", 13957)):
            self.assertLessEqual(len(core(lang)), cap, lang)
        self.assertEqual((shared.SharedClaudeAgent.INLINE_MAX, shared.SharedClaudeAgent.HOOK_MAX), (9500, 10000))
        self.assertLessEqual(len(main_identity.prompt({"language": "zh"}, harness="claude")),
                             shared.SharedClaudeAgent.INLINE_MAX - 300)
        with tempfile.TemporaryDirectory() as td:                            # the test_p376 measure: whole first hook answer
            host = Mock(); host.st.root = pathlib.Path(td); host.stopped.return_value = False
            host.st.write_private.side_effect = lambda p, b: pathlib.Path(p).write_bytes(b)
            a = shared.SharedClaudeAgent(host, {"kind": "claude", "dir": td, "language": "zh", "session_mode": "shared"})
            a.session = {"sessionId": "owner", "pid": 1}
            a.phone_turn, a.pending_text = True, "hi"
            a.pending_source = {"kind": "phone", "role": "owner"}
            ans = asyncio.run(a.hook_event({"session_id": "owner", "hook_event_name": "UserPromptSubmit", "prompt": "hi"}))
            text = ans["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(a.identity_marker[3], "hook.additionalContext")   # still inline, not the Read fallback
            self.assertLessEqual(len(text), a.HOOK_MAX - 300)
            for n in ("派活不是禁令", "每张派单带预先写好的验收命令", "主人明确让你亲做"):
                self.assertIn(n, text)

    def test_reminder_and_heartbeat_line(self):
        self.assertIn("主人明确让你亲做、别派", main_identity.IDENTITY_REMINDER["zh"])
        self.assertIn("Follow an owner request or personal preference to do the work yourself", main_identity.IDENTITY_REMINDER["en"])
        for lang in ("zh", "en"):
            self.assertLess(len(main_identity.IDENTITY_REMINDER[lang]), 600)
        self.assertIn("后台活已结束：核对归你，跑验收命令看结论", main_identity.HEARTBEAT_LINE["zh"])
        self.assertIn("checking is yours, never the owner's: run the dispatch's acceptance command", main_identity.HEARTBEAT_LINE["en"])


class BackgroundDone(unittest.TestCase):
    """Product mechanism: the host's job-ended notice hands the check to the main Agent, never to the owner."""

    def notice(self, lang):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            a = SimpleNamespace(status="idle", q=asyncio.Queue(), is_down=lambda: False, native_busy=lambda: False,
                                cfg={"session_mode": "shared"})
            a.submit = a.q.put_nowait
            h = SimpleNamespace(st=SimpleNamespace(root=root, log=Mock()), lang=lang, agent=a, preferences={},
                                busy=busy.Board(root), stopped=lambda: False, _open_question=lambda: False,
                                hist_add=Mock(return_value={"id": 1}))
            n = done.Notifier(h)
            h.busy.finish(busy.item("run:one", "掌枢", "ceo", "测试报告", since=100, report="/tmp/report.md"), 0, 125)
            send = n.tick(now=1000)
            self.assertIsNotNone(send)
            return send.text

    def test_notice_wording(self):
        zh = self.notice("zh")
        for n in ("核对归你，不推给主人", "先跑派单写好的验收命令", "派回原执行者补齐，不要自己下场排查", "不凭退出码判断成功"):
            self.assertIn(n, zh)
        en = self.notice("en")
        for n in ("Checking this is your job, not the owner's", "run the acceptance command written into the dispatch",
                  "send it back to whoever ran the job", "do not investigate it yourself"):
            self.assertIn(n, en)


class SkillsAndTemplates(unittest.TestCase):
    """Every place that teaches dispatching names the acceptance command and the never-do-the-work rule."""

    FILES = {
        "skills/agentj-capability/SKILL.md": ("验收命令", "Acceptance command", "agentj capability report-check --dir <workflow> --verdict ok --json",
                                              "never do the work", "绝不下场干活", "never ask the owner to check it"),
        "skills/agentj-automation/SKILL.md": ("acceptance command (验收命令)", "you do not investigate or fix it yourself",
                                              "never handed to the owner"),
        "skills/agentj-heartbeat/SKILL.md": ("acceptance command (验收命令)", "Checking it is yours, never the owner's",
                                             "do not investigate it yourself", "never pick the work up yourself"),
        "wizard/rootdocs/zh/CONSTITUTION.md": ("预先写好的验收命令", "分担的是董事长的责任，不是 CEO 的活", "职责、能力和验收标准",
                                               "绝不下场干活", "不推给董事长"),
        "wizard/rootdocs/en/CONSTITUTION.md": ("pre-written acceptance command", "carries the Chairman's load, not a CEO's",
                                               "duties, capabilities and acceptance criteria", "never does the work itself",
                                               "never the Chairman's"),
        "wizard/rootdocs/zh/WORKFLOW.md": ("**验收命令**", "职责、能力", "验收标准", "不下场排查、诊断、修改或发布", "不推给董事长"),
        "wizard/rootdocs/en/WORKFLOW.md": ("**acceptance command**", "the duty", "acceptance criteria",
                                           "never investigates, diagnoses, fixes or", "never pushed to the Chairman"),
        "wizard/rootdocs/zh/ROLES.md": ("验收命令", "分担的是董事长的责任，不是 CEO 的活", "绝不下场排查、诊断、修改或发布"),
        "wizard/rootdocs/en/ROLES.md": ("acceptance command", "carries the Chairman's load, not a CEO's",
                                        "never investigates, diagnoses, fixes or releases itself"),
        "wizard/rootdocs/zh/IDENTITY.md": ("排查、诊断、修改、发布一律派出去", "验收命令"),
        "wizard/rootdocs/en/IDENTITY.md": ("investigation, diagnosis, fixes and releases are always dispatched", "acceptance command"),
        "wizard/skill/references/files.md": ("acceptance\n  command (验收命令", "## 验收命令",
                                             "never\n  investigates, diagnoses, fixes or releases itself"),
    }

    def test_files_carry_the_field_and_rule(self):
        for rel, needles in self.FILES.items():
            text = (AGENTJ / rel).read_text(encoding="utf-8")
            for n in needles:
                with self.subTest(file=rel, needle=n):
                    self.assertIn(n, text)

    def test_templates_keep_their_core_blocks(self):
        """The rules live inside the marked core block, so refresh_root upgrades installed copies (owner part untouched)."""
        for lang, name, needle in (("zh", "CONSTITUTION.md", "绝不下场干活"), ("en", "CONSTITUTION.md", "never does the work itself"),
                                   ("zh", "WORKFLOW.md", "**验收命令**"), ("en", "WORKFLOW.md", "**acceptance command**"),
                                   ("zh", "ROLES.md", "验收命令"), ("en", "ROLES.md", "acceptance command")):
            blk = wizard.core_block(wizard.BOOT_DOCS[lang][name])
            self.assertIsNotNone(blk)
            self.assertIn(needle, blk[2], (lang, name))


if __name__ == "__main__":
    unittest.main()
