"""A source refusal persists its final quiet recovery snapshot exactly once."""
import _hermetic  # noqa: F401
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest import mock
from agentj import completion_recovery, provenance


class RefusalPersistence(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='refusal-persistence-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.host = N(st=N(root=root,log=mock.Mock(),config=lambda:{'channel':'synthetic'}),
                      agent_cfg={'dir':str(root)}, agent=N(cur_send=N(background_done_ids=[])),
                      cur_turn='synthetic-turn',task_label=None)
        self.book = completion_recovery.Book(self.host)
        self.source = mock.patch.object(provenance,'turn_source',return_value={'kind':'unknown'})
        self.source.start()
        self.addCleanup(self.source.stop)

    def test_refusal_one_locked_atomic_final_snapshot_and_reload(self):
        original = completion_recovery._write_private
        snapshots=[]
        def observed(path,data):
            self.assertTrue(self.book.lock._is_owned())
            snapshots.append(json.loads(data))
            return original(path,data)
        with mock.patch.object(completion_recovery,'_write_private',side_effect=observed):
            self.book.refusal('Read',{'file_path':'ordinary-report.md'},'group_source')
        self.assertEqual(len(snapshots),1)
        reloaded = completion_recovery.Book(self.host)
        self.assertEqual(reloaded.rows,self.book.rows)
        self.assertFalse(reloaded.corrupt)
        row = next(iter(reloaded.rows.values()))
        self.assertEqual((row['quiet'],row['notified'],row['original_error']),('group_source',True,'group_source'))

    def test_default_add_is_still_durable_and_refusal_failure_propagates(self):
        self.book.add('a'*64,{'task_id':'synthetic','source':'unknown','recovery_point':'reconcile_only'})
        before = self.book.path.read_bytes()
        self.assertIn('a'*64,completion_recovery.Book(self.host).rows)
        with mock.patch('agentj.diskq.os.fsync',side_effect=OSError('synthetic fsync failure')):
            with self.assertRaises(OSError):
                self.book.refusal('Read',{'file_path':'ordinary-report.md'},'group_source')
        self.assertEqual(self.book.path.read_bytes(),before)
        self.assertFalse(completion_recovery.Book(self.host).corrupt)
        self.assertFalse(self.book.lock._is_owned())
