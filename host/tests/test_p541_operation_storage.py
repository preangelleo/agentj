"""Private once-ledger storage boundaries; synthetic temporary SQLite only."""
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from agentj.bounded_plan import OperationOnce


@unittest.skipUnless(os.name == 'posix', 'POSIX filesystem ownership boundary')
class StorageBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_new_ledger_is_private_and_regular(self):
        path = self.root / 'once.sqlite3'
        ledger = OperationOnce(path)
        self.assertTrue(ledger.reserve('operation', 'request'))
        info = path.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)

    def victim(self):
        path = self.root / 'other.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE untouched (value TEXT)')
        return path

    def assert_victim_unchanged(self, path):
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('untouched',)])

    def test_ledger_rejects_a_symlink_without_touching_its_target(self):
        victim = self.victim()
        path = self.root / 'once.sqlite3'
        path.symlink_to(victim)
        with self.assertRaises((ValueError, OSError)):
            OperationOnce(path).reserve('operation', 'request')
        self.assert_victim_unchanged(victim)

    def test_ledger_rejects_a_hardlink_without_touching_its_target(self):
        victim = self.victim()
        path = self.root / 'once.sqlite3'
        os.link(victim, path)
        with self.assertRaises((ValueError, OSError)):
            OperationOnce(path).reserve('operation', 'request')
        self.assert_victim_unchanged(victim)
