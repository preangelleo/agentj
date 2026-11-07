"""Offline local-debt controls. Fixtures live under the worker directory."""
import _hermetic
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentj import humane, migrate, preference_values, state, state_paths
import jarvis_host
import agentj


class LocalDebtControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.st = state.State(self.root)
        self.env = patch.dict(os.environ, {'AGENTJ_STATE_DIR': str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def config(self, **values):
        self.st.config_path.write_text(json.dumps(values))

    def test_pure_values_sparse_and_negative(self):
        doc = {'agent': {'isolation': False}, 'unrelated': [1]}
        self.assertIs(preference_values.get(doc, 'agent.isolation', True), False)
        self.assertEqual(preference_values.get(doc, 'agent.model', 'default'), 'default')
        self.assertEqual(preference_values.get(doc, 'unrelated.x', 'default'), 'default')
        preference_values.put(doc, 'appearance.language', 'en')
        self.assertEqual(doc['appearance'], {'language': 'en'})
        self.assertEqual(doc['unrelated'], [1])

    def test_path_and_old_version_facades(self):
        self.assertIs(state.default_state, state_paths.default_state)
        self.assertEqual(state.default_state('/fixture'), Path('/fixture/.local/state/agentj'))
        self.assertEqual(state.legacy_state('/fixture'), Path('/fixture/.local/state/agentjarvis-alpha'))
        self.assertEqual(jarvis_host.__version__, agentj.__version__)
        self.assertIs(migrate.serve_running, __import__('agentj.legacy_state_probe', fromlist=['serve_running']).serve_running)

    def test_current_and_custom_never_enter_writer(self):
        for relay in [state.DEFAULT_RELAY, 'wss://custom.invalid/relay']:
            self.config(relay=relay, custom={'keep': [1, 2]})
            before = self.st.config_path.read_bytes()
            with patch.object(migrate, 'upgrade_urls', wraps=migrate.upgrade_urls) as writer:
                for _ in range(3):
                    self.assertEqual(migrate.auto(out=io.StringIO()), 'none')
                writer.assert_not_called()
            self.assertEqual(before, self.st.config_path.read_bytes())
            self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['config.json'])

    def test_old_defaults_once_and_custom_preserved(self):
        self.config(relay=state.OLD_DEFAULT_RELAY, web='https://custom.invalid/', custom={'keep': [1, 2]})
        with patch.object(migrate, 'upgrade_urls', wraps=migrate.upgrade_urls) as writer:
            for _ in range(3):
                migrate.auto(out=io.StringIO())
            self.assertEqual(writer.call_count, 1)
        doc = json.loads(self.st.config_path.read_text())
        self.assertEqual(doc, {'relay': state.DEFAULT_RELAY, 'web': 'https://custom.invalid/', 'custom': {'keep': [1, 2]}})
        self.assertEqual(migrate.read_record(self.st)['urls'], {'config.relay': state.OLD_DEFAULT_RELAY})
        self.assertIn('urls_at', migrate.read_record(self.st))
        self.assertEqual(migrate._restore_urls(self.st), ['config.relay'])
        self.assertTrue(migrate.needs_url_upgrade(self.st))

    def test_failure_keeps_data_and_retries(self):
        self.config(relay=state.OLD_DEFAULT_RELAY)
        before = self.st.config_path.read_bytes()
        with patch.object(migrate, 'upgrade_urls', side_effect=OSError('synthetic failure')):
            migrate.auto(out=io.StringIO())
        self.assertEqual(before, self.st.config_path.read_bytes())
        self.assertFalse(self.st.migrated_path.exists())
        self.assertTrue(migrate.needs_url_upgrade(self.st))
        migrate.auto(out=io.StringIO())
        self.assertFalse(migrate.needs_url_upgrade(self.st))

    def test_missing_malformed_and_repaired_state(self):
        self.assertFalse(migrate.needs_url_upgrade(self.st))
        self.st.config_path.write_text('{broken')
        self.assertFalse(migrate.needs_url_upgrade(self.st))
        self.assertEqual(self.st.config_path.read_text(), '{broken')
        self.config(relay=state.OLD_DEFAULT_RELAY)
        self.assertTrue(migrate.needs_url_upgrade(self.st))

    def test_concurrent_legacy_writers_complete_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.config(relay=state.OLD_DEFAULT_RELAY)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: migrate.upgrade_urls_if_needed(self.st, io.StringIO()), range(4)))
        self.assertEqual(sum(bool(result) for result in results), 1)
        self.assertEqual(migrate.read_record(self.st)['urls'], {'config.relay': state.OLD_DEFAULT_RELAY})

    @unittest.skipUnless(hasattr(os, 'O_NOFOLLOW'), 'platform lacks no-follow open')
    def test_migration_lock_symlink_refused(self):
        self.config(relay=state.OLD_DEFAULT_RELAY)
        target = self.root / 'unchanged'
        target.write_text('sentinel')
        (self.root / 'migrate-urls.lock').symlink_to(target)
        before = self.st.config_path.read_bytes()
        with self.assertRaises(OSError):
            migrate.upgrade_urls_if_needed(self.st, io.StringIO())
        self.assertEqual(target.read_text(), 'sentinel')
        self.assertEqual(self.st.config_path.read_bytes(), before)
        self.assertFalse(self.st.migrated_path.exists())

    def test_host_language_templates(self):
        for lang in ['zh', 'en']:
            line = humane.say('migration.urls_failed', lang=lang, kind='OSError')
            self.assertIn('OSError', line)
            self.assertNotIn('{kind}', line)


if __name__ == '__main__':
    unittest.main()
