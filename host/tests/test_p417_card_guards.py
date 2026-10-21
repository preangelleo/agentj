"""P417: P432 card installs retain P426 publisher and audit approvals."""
import io
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from agentj import market


class CardGuards(unittest.TestCase):
    def run_case(self, answer, *, audit=False, changed=True):
        m = {"name": "test-card", "type": "card", "version": "1.0.0"}
        files = {"card/card.json": json.dumps({"id": "probe"}).encode()}
        sha = "a" * 64
        dg = market.install_digest(sha, pathlib.Path("card/probe"), "card", {}, {"replace": False})
        with tempfile.TemporaryDirectory() as d:
            st = SimpleNamespace(root=pathlib.Path(d), log=Mock())
            with patch('agentj.jcards_pkg.preflight', return_value=[]), patch('agentj.jcards.find', return_value=None), \
                 patch('agentj.jcards_pkg.install', return_value={"ok": True}) as install, \
                 patch.object(market.plaza_perms, 'ask_owner', return_value=answer) as ask, \
                 patch.object(market.market_guard, 'record_publisher') as tofu, \
                 patch.object(market, '_record_install') as record, patch.object(market, '_call', return_value={}):
                code = market._install_card(st, m, files, sha, True, None, name=m['name'], version=None,
                    replace=False, accept_unverified=False, owner_confirmed=True, digest=dg, post=None, out=io.StringIO(),
                    pubr={"known": True, "changed": changed, "prev": "old", "fp": "new"},
                    audit_blk=[{"rule": "blocked-test"}] if audit else [])
                return code, install.call_count, ask.call_args, tofu.call_args, record.call_args

    def test_no_serve_or_deny_cannot_bypass_publisher_with_owner_confirmed(self):
        for answer in (None, {"ok": False, "why": "denied"}):
            code, installs, ask, tofu, record = self.run_case(answer)
            self.assertEqual(code, market.EXIT_PERMISSION)
            self.assertEqual(installs, 0)
            self.assertEqual(ask.kwargs['publisher'], {"prev": "old", "new": "new"})
            self.assertIsNone(tofu)
            self.assertIsNone(record)

    def test_signed_allow_records_publisher_and_audit_grant(self):
        code, installs, ask, tofu, record = self.run_case({"ok": True, "rid": "r1", "device": "d1"}, audit=True)
        self.assertEqual((code, installs), (market.EXIT_OK, 1))
        self.assertEqual(ask.kwargs['audit'], [{"rule": "blocked-test"}])
        rec = record.args[1]
        self.assertEqual(rec['publisher_grant'], {"rid": "r1", "device": "d1"})
        self.assertEqual(rec['audit_grant']['findings'], ['blocked-test'])
        self.assertEqual(tofu.args[1:], ('test-card', 'new'))

    def test_audit_only_also_needs_signed_phone_allow(self):
        code, installs, ask, _, _ = self.run_case(None, audit=True, changed=False)
        self.assertEqual((code, installs), (market.EXIT_PERMISSION, 0))
        self.assertIsNone(ask.kwargs['publisher'])
        self.assertEqual(ask.kwargs['audit'], [{"rule": "blocked-test"}])
