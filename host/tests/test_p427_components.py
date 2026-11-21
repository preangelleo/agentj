"""The output skill is discoverable on each supported harness without changing core hashes."""
import _hermetic  # noqa: F401
import unittest
from pathlib import Path
from agentj import main_identity
class Components(unittest.TestCase):
    def test_discovery_on_all_harnesses(self):
        for lang in ('zh','en'):
            for harness in (None,'claude','codex','opencode','kimi','qwen','dsh'):
                text=main_identity.prompt({'language':lang},harness=harness)
                self.assertIn('agentj-reply-components',text)
                self.assertIn('草稿' if lang=='zh' else 'draft',text)
        skill=Path(main_identity.__file__).parent/'skills/agentj-reply-components/SKILL.md'
        self.assertTrue(skill.is_file())

    def test_internal_files_use_plain_text_in_both_languages(self):
        skill = Path(main_identity.__file__).parent / "skills/agentj-reply-components/SKILL.md"
        text = skill.read_text()
        self.assertIn("Mention Agent J’s internal skills by name in plain text.", text)
        self.assertIn("Refer to internal program or state files by filename only, without the path.", text)
        self.assertIn("提到 Agent J 内置技能时，直接用纯文本写技能名。", text)
        self.assertIn("提到程序内部文件或状态文件时，只写文件名，不写路径。", text)
        self.assertIn("agentj file show PATH", text)
