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
        self.assertEqual(main_identity.VERSION, 10)
        self.assertEqual(main_identity.verify_core()["version"], 10)
        self.assertTrue(self.zh.splitlines()[0].endswith("v10"))
        self.assertTrue(self.en.splitlines()[0].endswith("v10"))

    def test_triage_sits_right_after_dispatch(self):
        for text, dispatch, triage in ((self.zh, "你绝不是某个工作流的 CEO", "每条请求先判三类"),
                                       (self.en, "You are never the CEO", "Sort every request into one of three kinds")):
            lines = text.splitlines()
            self.assertTrue(lines[2].startswith(dispatch))
            self.assertIn(triage, lines[3])

    def test_three_way_triage_and_harness_tools(self):
        for needle in ("一定派 CEO", "派子 Agent", "Agent/Task 工具", "OpenCode 用 task 子 Agent", "headless 子会话", "`codex exec`",
                       "只有立等可取的小事、而且没有对应 CEO，你才亲自做"):
            self.assertIn(needle, self.zh)
        for needle in ("always dispatch it to that CEO", "dispatch a sub-Agent", "Agent/Task tool", "OpenCode uses its task sub-Agent",
                       "headless sub-session", "`codex exec`", "only a small thing that is ready in a moment and has no CEO you do yourself"):
            self.assertIn(needle, self.en)

    def test_handed_off_then_standby_and_progress_card(self):
        for needle in ("已交代给 X，有结论再报", "回到待命", "不凭退出码", "同一张卡片", "随时能插话", "不让消息排队", "派活、监督、汇报"):
            self.assertIn(needle, self.zh)
        for needle in ("Handed to X; I will report when there is a result", "back to standby", "not the exit code", "same card",
                       "interrupt at any time", "messages stop queueing", "Dispatch, supervise, report"):
            self.assertIn(needle, self.en)

    def test_address_and_hierarchy(self):
        for needle in ("默认称用户「董事长」", "换称呼", "你能干什么", "董事长 → 董事长助理", "虚拟 CEO", "子 Agent（员工）", "「小助理」"):
            self.assertIn(needle, self.zh)
        for needle in ('call the human "Chairman" by default', "another form of address", "what can you do",
                       "Chairman → chief of staff", "virtual CEO", "sub-Agents (staff)", '"junior assistant"'):
            self.assertIn(needle, self.en)

    def test_delegation_memory_never_lifts_approvals(self):
        for needle in ("这种不用问我", "这种要先问我", "documentation/MEMORY.md", "「放权规则」", "按决策类型", "不再重复问",
                       "不写凭据", "不解除五类危险操作的手机审批"):
            self.assertIn(needle, self.zh)
        for needle in ("don't ask me about this", "ask me first about this", "documentation/MEMORY.md", '"Delegation rules"',
                       "per decision type", "without asking again", "never a credential",
                       "never lift the phone approval for the five dangerous operation types"):
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
