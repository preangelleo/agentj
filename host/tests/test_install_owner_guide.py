import unittest
from types import SimpleNamespace
from agentj import first_run

class OwnerGuide(unittest.TestCase):
    def test_progress_is_existing_journal_evidence(self):
        rec = first_run._blank(SimpleNamespace(exists=lambda: False))
        out = first_run.owner_guide(rec)
        self.assertEqual(out['next']['id'], 'seat')
        self.assertFalse(out['ready'])
        self.assertEqual(out['verified'], 0)
        self.assertEqual(out['next']['verify'], 'agentj setup checklist --item seat')
        self.assertNotIn('pair --assist', str(out))
        self.assertFalse(out['installer']['available'])
    def test_no_journal_gives_explicit_start_command(self):
        self.assertEqual(first_run.owner_guide(None)['start'], 'agentj setup checklist --resume')
