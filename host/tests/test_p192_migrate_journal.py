"""Synthetic on-disk Host migration failures, using the real State and writers."""
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
from agentj import migrate
from agentj.state import State


class MigrationJournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3])
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name))
        self.cfg = {'relay': migrate.OLD_DEFAULT_RELAY, 'web': 'https://custom.example'}
        self.st.config_path.write_text(json.dumps(self.cfg))
        self.st.cloud_path.write_text(json.dumps({'api': migrate.OLD_API}))

    def test_config_intent_failure_leaves_both_sources_unchanged(self):
        before = {p: p.read_bytes() for p in (self.st.config_path, self.st.cloud_path)}
        with patch.object(migrate, '_write_record', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                migrate.upgrade_urls(self.st)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_crash_after_config_replace_retains_recoverable_intent(self):
        write = self.st.write_private
        def crash(path, content):
            write(path, content)
            if path == self.st.config_path:
                raise OSError('simulated loss after atomic replace')
        with patch.object(self.st, 'write_private', side_effect=crash):
            with self.assertRaises(OSError):
                migrate.upgrade_urls(self.st)
        rec = migrate.read_record(self.st)
        self.assertEqual(rec['urls']['config.relay'], self.cfg['relay'])
        self.assertEqual(json.loads(self.st.cloud_path.read_text())['api'], migrate.OLD_API)
        migrate._restore_urls(self.st)
        self.assertEqual(json.loads(self.st.config_path.read_text()), self.cfg)

    def test_cloud_write_failure_can_retry_and_restore_both_files(self):
        write = self.st.write_private
        def fail_cloud(path, content):
            if path == self.st.cloud_path:
                raise OSError('simulated cloud write failure')
            return write(path, content)
        with patch.object(self.st, 'write_private', side_effect=fail_cloud):
            with self.assertRaises(OSError):
                migrate.upgrade_urls(self.st)
        self.assertEqual(set(migrate.read_record(self.st)['urls']), {'config.relay', 'cloud.api'})
        migrate.upgrade_urls(self.st)
        self.assertEqual(set(migrate._restore_urls(self.st)), {'config.relay', 'cloud.api'})
        self.assertEqual(json.loads(self.st.config_path.read_text()), self.cfg)
        self.assertEqual(json.loads(self.st.cloud_path.read_text())['api'], migrate.OLD_API)

    def test_crash_after_cloud_replace_keeps_both_rollback_values(self):
        write = self.st.write_private
        def crash(path, content):
            write(path, content)
            if path == self.st.cloud_path:
                raise OSError('simulated loss after cloud replace')
        with patch.object(self.st, 'write_private', side_effect=crash):
            with self.assertRaises(OSError):
                migrate.upgrade_urls(self.st)
        self.assertEqual(set(migrate.read_record(self.st)['urls']), {'config.relay', 'cloud.api'})
        migrate._restore_urls(self.st)
        self.assertEqual(json.loads(self.st.config_path.read_text()), self.cfg)

    def test_process_death_after_each_replace_preserves_rollback(self):
        script = """
import os, sys
from pathlib import Path
from agentj import migrate
from agentj.state import State
st = State(Path(sys.argv[1]))
write = st.write_private
def crash(path, content):
    write(path, content)
    if path.name == sys.argv[2]:
        os._exit(73)
st.write_private = crash
migrate.upgrade_urls(st)
"""
        for target in ('config.json', 'cloud.json'):
            with self.subTest(target=target):
                self.st.config_path.write_text(json.dumps(self.cfg))
                self.st.cloud_path.write_text(json.dumps({'api': migrate.OLD_API}))
                self.st.migrated_path.unlink(missing_ok=True)
                child = subprocess.run([sys.executable, '-B', '-c', script,
                                        str(self.st.root), target],
                                       cwd=Path(__file__).resolve().parents[1],
                                       capture_output=True, timeout=15)
                self.assertEqual(child.returncode, 73, child.stderr.decode())
                self.assertIn('config.relay', migrate.read_record(self.st)['urls'])
                migrate._restore_urls(self.st)
                self.assertEqual(json.loads(self.st.config_path.read_text()), self.cfg)
                self.assertEqual(json.loads(self.st.cloud_path.read_text())['api'], migrate.OLD_API)
