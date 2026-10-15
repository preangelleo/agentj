"""P149 (core v10, 0.18.4): the chief of staff delegates instead of doing heavy work itself.

Owner decision 2026-10-12: three-way triage (CEO → sub-Agent → only instant small jobs yourself), "handed to X" then standby, short
progress lines on one card, the Chairman address and hierarchy, and a delegation boundary learned through memory."""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import main_identity, wizard  # noqa: E402


class CoreV10(unittest.TestCase):
    def setUp(self):
        self.zh = main_identity.prompt({"language": "zh"})
        self.en = main_identity.prompt({"language": "en"})

    def test_version(self):
        self.assertGreaterEqual(main_identity.VERSION, 10)      # P156 raised it to v11; the P149 rules below must stay
        self.assertEqual(main_identity.verify_core()["version"], main_identity.VERSION)
        self.assertTrue(self.zh.splitlines()[0].endswith("v" + str(main_identity.VERSION)))
        self.assertTrue(self.en.splitlines()[0].endswith("v" + str(main_identity.VERSION)))

    def test_triage_sits_right_after_dispatch(self):
        for text, dispatch, triage in ((self.zh, "你绝不是某个工作流的 CEO", "每条请求先判三类"),
                                       (self.en, "You are never the CEO", "Sort each request")):
            lines = text.splitlines()
            self.assertTrue(lines[3].startswith(dispatch))   # P186: line 2 is the chairman philosophy
            self.assertIn(triage, lines[4])

    def test_three_way_triage_and_harness_tools(self):
        for needle in ("一定派 CEO", "派子 Agent", "Agent/Task", "OpenCode 用 task", "headless", "`codex exec`",
                       "立等可取且无 CEO 的小事才亲做"):
            self.assertIn(needle, self.zh)
        for needle in ("always dispatch to the CEO", "dispatch a sub-Agent", "Agent/Task", "OpenCode task",
                       "headless", "`codex exec`", "only immediate small work with no CEO is yours"):
            self.assertIn(needle, self.en)

    def test_handed_off_then_standby_and_progress_card(self):
        for needle in ("已交代给 X，有结论再报", "回到待命", "不凭退出码", "同回合叠同卡", "随时接主人插话", "避免消息排队", "派活、监督、汇报"):
            self.assertIn(needle, self.zh)
        for needle in ("Handed to X; I will report when there is a result", "return to standby", "not exit code", "same card",
                       "stay interruptible", "keep messages from queueing", "Dispatch, supervise and report"):
            self.assertIn(needle, self.en)

    def test_address_and_hierarchy(self):
        for needle in ("默认称用户「董事长」", "改称", "你能干什么", "董事长 → 董事长助理", "工作流 CEO", "子 Agent 员工", "小助理"):
            self.assertIn(needle, self.zh)
        for needle in ('call the human "Chairman" by default', "chosen address", "what can you do",
                       "Chairman → chief of staff", "workflow CEOs", "sub-Agents", 'junior assistant'):
            self.assertIn(needle, self.en)

    def test_delegation_memory_never_lifts_approvals(self):
        for needle in ("这种不用问我", "这种要先问我", "documentation/MEMORY.md", "「放权规则」", "按决策类型", "不重复问",
                       "不写凭据", "不解除五类危险操作的手机审批"):
            self.assertIn(needle, self.zh)
        for needle in ("don't ask me about this", "ask me first", "documentation/MEMORY.md", '"Delegation rules"',
                       "each decision type", "without repeating questions", "never credentials",
                       "never lifts the five dangerous operation phone approvals"):
            self.assertIn(needle, self.en)

    def test_handoff_before_compaction_and_plain_names(self):
        for needle in ("压缩前先用 agentj-handoff 写交接文件", "压缩之后，主机会告诉你交接文件在哪", "不等主人提醒",
                       "不要含随机串", "当密钥打码", "用人话命名"):
            self.assertIn(needle, self.zh)
        for needle in ("Before a compaction, write the handover file with agentj-handoff", "without waiting for the owner to remind you",
                       "Never put random strings", "masks them as secrets", "name things in plain words"):
            self.assertIn(needle, self.en)

    def test_no_private_names(self):
        for text in (self.zh, self.en):
            for bad in ("Leo", "贾维斯", "~/" + "coding"):
                self.assertNotIn(bad, text.split("<Owner private")[0])


class BootstrapMemory(unittest.TestCase):
    def test_fresh_root_memory_has_an_empty_delegation_section(self):
        for lang, head in (("zh", "## 放权规则"), ("en", "## Delegation rules")):
            with tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td)
                wizard.bootstrap_root(root, ["claude", "codex", "opencode"], lang)
                text = (root / "documentation/MEMORY.md").read_text()
                self.assertIn(head, text)
                self.assertIn("不存凭据" if lang == "zh" else "Never store credentials", text)

    def test_existing_memory_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "documentation").mkdir()
            (root / "documentation/MEMORY.md").write_text("mine\n")
            wizard.bootstrap_root(root, ["claude"], "zh")
            self.assertEqual((root / "documentation/MEMORY.md").read_text(), "mine\n")


if __name__ == "__main__":
    unittest.main()
