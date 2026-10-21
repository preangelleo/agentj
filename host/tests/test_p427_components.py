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
