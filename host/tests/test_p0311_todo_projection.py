"""The phone receives only explicitly structured, internal todo metadata."""
import _hermetic  # noqa: F401 — isolate state/config before importing agentj
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentj import jcards, jcards_todo


class TodoProjection(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix='p0311-home-')
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        patch = mock.patch.dict('os.environ', {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home / 'config'),
                                              'AGENTJ_STATE_DIR': str(self.home / 'state')})
        patch.start()
        self.addCleanup(patch.stop)

    def rows(self, **extra):
        raw = json.dumps({'items': [{'id': 't1', 'text': 'Ship', 'owner': 'agent', **extra}]}).encode()
        return jcards_todo.phone_items(raw, 'en')[0][0]

    def test_explicit_metadata_survives_and_is_phone_only(self):
        row = self.rows(plan=True, task={'jlink': 'workflow/ship-12'}, report={'jlink': 'doc/reports/ship.md'})
        self.assertIs(row['plan'], True)
        self.assertEqual(row['task'], {'jlink': 'workflow/ship-12'})
        self.assertEqual(row['report'], {'jlink': 'doc/reports/ship.md'})
        self.assertNotIn('plan', jcards_todo.load(json.dumps({'items': [{'text': 'Ship', 'plan': True}]}).encode())[0][0])

    def test_untrusted_text_and_wrong_link_kinds_never_become_destinations(self):
        row = self.rows(source='workflow/ship-12', note='doc/reports/ship.md', plan='true',
                        task={'jlink': 'doc/reports/ship.md'}, report={'jlink': 'workflow/ship-12'})
        self.assertNotIn('plan', row)
        self.assertNotIn('task', row)
        self.assertNotIn('report', row)
        self.assertEqual(row['source'], 'workflow/ship-12')
        for value in ('https://example.com/report', '/tmp/report.md', 'doc/../report.md',
                      'doc/reports/ship.md?download=1', 'doc/reports/ship.md#anchor',
                      'doc/reports/ship.md\npage/chat'):
            with self.subTest(value=value):
                self.assertNotIn('report', self.rows(report={'jlink': value}))
        self.assertNotIn('task', self.rows(task='workflow/ship-12'))
        self.assertNotIn('report', self.rows(report={'jlink': 'doc/reports/ship.md', 'href': 'https://example.com'}))
        self.assertNotIn('plan', self.rows(plan=1))

    def test_owner_and_closed_items_do_not_claim_agent_plan(self):
        self.assertNotIn('plan', self.rows(owner='owner', plan=True))
        self.assertNotIn('plan', self.rows(status='done', plan=True))


if __name__ == '__main__':
    unittest.main()
