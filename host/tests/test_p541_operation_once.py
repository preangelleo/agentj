"""Durable exact-operation reservations across request ids; no executor or credentials."""
import tempfile
import unittest
from pathlib import Path
from agentj.bounded_plan import OperationOnce

class OperationLedger(unittest.TestCase):
    def test_new_rid_cannot_reserve_any_consumed_outcome(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'once.sqlite3'
            for state in ('executing','completed','unknown','denied','revoked'):
                ledger = OperationOnce(path)
                self.assertTrue(ledger.reserve(state, 'rid-one'))
                ledger.finish(state, 'rid-one', state)
                reopened = OperationOnce(path)
                self.assertFalse(reopened.reserve(state, 'rid-two'))
                self.assertFalse(reopened.reserve(state, 'rid-one'))

    def test_unrelated_exact_operations_remain_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = OperationOnce(Path(folder)/'once.sqlite3')
            self.assertTrue(ledger.reserve('object-action-one', 'rid'))
            self.assertTrue(ledger.reserve('object-action-two', 'rid'))
