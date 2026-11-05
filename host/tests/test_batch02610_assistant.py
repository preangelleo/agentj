"""0.26.10: every bundled instruction surface keeps the main session available."""
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import main_identity as identity

ROOT = pathlib.Path(identity.__file__).parent
ZH = '主会话随时接主人消息'
EN = 'keep the main session ready for owner messages'

class AssistantBoundary(unittest.TestCase):
    def test_all_three_harness_prompts_keep_boundary_and_plain_citations(self):
        for lang, needles in [('zh', (ZH, '一句话小事', '不用 harness 引用语法', '只派活、写验收、跑验收、汇报')),
                              ('en', ('Keep the main session ready for owner messages', 'one-sentence task', 'Never use harness citation syntax', 'dispatch, write acceptance checks, run them and report'))]:
            for harness in ('claude', 'codex', 'opencode'):
                with self.subTest(lang=lang, harness=harness):
                    prompt = identity.prompt({'language': lang}, harness=harness)
                    for text in needles:
                        self.assertIn(text, prompt)
                    self.assertTrue(identity.mechanism({}, harness))

    def test_every_packaged_skill_repeats_the_delegate_boundary(self):
        files = list((ROOT/'skills').glob('agentj-*/SKILL.md')) + [ROOT/'wizard/skill/SKILL.md']
        self.assertGreater(len(files), 30)
        for file in files:
            with self.subTest(skill=file.parent.name):
                text = file.read_text()
                for phrase in (ZH, EN, 'resident CEO or temporary sub-Agent', 'write and run acceptance checks', '一句话小事'):
                    self.assertIn(phrase, text)

    def test_role_templates_keep_rule_inside_upgradeable_core(self):
        for lang, needle in [('zh',ZH), ('en',EN)]:
            for name in ('CONSTITUTION','IDENTITY','ROLES','WORKFLOW','SOUL'):
                with self.subTest(lang=lang, name=name):
                    text = (ROOT/f'wizard/rootdocs/{lang}/{name}.md').read_text()
                    a,b = text.index('<!-- agentj:core'), text.index('<!-- /agentj:core -->')
                    self.assertIn(needle,text[a:b])

    def test_turn_reminder_does_not_drift_on_any_harness(self):
        for lang, needle in [('zh','主会话随时能接主人消息'), ('en','stay ready for owner messages')]:
            text = identity.turn_reminder({'language':lang})
            self.assertIn(needle,text)
            self.assertIn('一句话小事' if lang=='zh' else 'one-sentence tasks',text)
            self.assertLess(len(text),600)

    def test_no_direct_maintenance_or_preflight_exception_in_skills(self):
        capability = (ROOT/'skills/agentj-capability/SKILL.md').read_text()
        config = (ROOT/'skills/agentj-config/SKILL.md').read_text()
        self.assertNotIn('resolve permitted checks/install yourself',capability)
        self.assertNotIn("CEO's (and yours) to solve",capability)
        self.assertNotIn('Maintain Agent J package files yourself',config)
        self.assertIn('delegate permitted checks/install',capability)
        self.assertIn('Dispatch Agent J package maintenance',config)

if __name__ == '__main__': unittest.main()
