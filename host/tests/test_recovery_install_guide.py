"""P225 metadata only: no authority, disk access, probe or guide execution."""
import unittest
from types import SimpleNamespace
from agentj import first_run


class GuideMetadata(unittest.TestCase):
    def test_required_steps_exclude_desktop_and_leaving_check(self):
        self.assertEqual(set(first_run.GUIDE), set(first_run.REQUIRED) - {'exit', 'desktop'})
        self.assertIn('harness', first_run.GUIDE)
        self.assertIn('phone', first_run.GUIDE)

    def test_snapshot_keeps_pending_local_status_and_exposes_only_metadata(self):
        rec = first_run._blank(SimpleNamespace(exists=lambda: False))
        snap = first_run.snapshot(rec)
        for row in snap['items']:
            self.assertIs(row['guide'], row['id'] in first_run.GUIDE)
            self.assertIs(row['required'], row['need'] == 'required')
            self.assertEqual(row['state'], 'pending')
            self.assertNotIn('ops', row, 'no unwired execution advertised')
        harness = next(i for i in snap['items'] if i['id'] == 'harness')
        self.assertEqual(harness['where'], 'local')
