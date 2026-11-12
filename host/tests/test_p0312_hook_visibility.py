"""Metadata-only hook refusal visibility; all state lives under TEST_TMP."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentj import hook_service, hooks
from test_p366_hooks import svc


class HookVisibility(unittest.TestCase):
    def test_host_refusal_is_a_body_free_log_row(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            _, s = svc(tmp)
            h = hooks.build({"preset": "callback", "life": "ttl"}, "main")
            s.store.add(h, hooks.new_secret())
            result = asyncio.run(s.handle({"h": h["id"], "m": "POST", "hd": {}, "ip": ""}, b"private-body"))
            self.assertEqual(result[0], 401)
            rows = asyncio.run(s.cli({"t": "logs", "id": h["id"]}))["deliveries"]
            self.assertEqual(rows[0]["result"], "refused signature")
            self.assertNotIn("private-body", json.dumps(rows))
            self.assertNotIn("body", json.dumps(s.store.delivery(rows[0]["id"])))

    def test_edge_unknown_id_stays_unassigned_and_metadata_only(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            _, s = svc(tmp)
            unknown = hooks.b64u(b"x" * 16)
            s.edge_refusal({"t": "refused", "h": unknown, "m": "POST", "s": 404, "r": "not_found",
                            "body": "private", "hd": {"authorization": "private"}, "ip": "private"})
            self.assertEqual(s.store.deliveries(), [])
            self.assertNotIn("private", json.dumps(s.store.doc))
            self.assertNotIn(unknown, json.dumps(s.store.doc))

    def test_known_edge_refusal_has_only_allowlisted_fields(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            _, s = svc(tmp)
            h = hooks.build({"preset": "callback", "life": "ttl"}, "main")
            s.store.add(h, hooks.new_secret())
            s.edge_refusal({"t": "refused", "h": h["id"], "m": "POST", "s": 429, "r": "rate",
                            "body": "private-body", "ip": "private-ip", "hd": {"x-secret": "private-header"}})
            rows = asyncio.run(s.cli({"t": "logs", "id": h["id"]}))["deliveries"]
            self.assertEqual(rows[0]["result"], "refused rate")
            self.assertEqual(rows[0]["method"], "POST")
            rec = s.store.delivery(rows[0]["id"])
            self.assertEqual(set(rec), {"id", "hook", "at", "method", "bytes", "result", "status"})
            self.assertEqual(rec["status"], 429)
            self.assertNotIn("private", json.dumps(rec))
            shown = asyncio.run(s.cli({"t": "logs", "id": h["id"], "show": rec["id"]}))
            self.assertIn("refused rate", shown["text"])

    def test_local_test_is_labeled_and_relay_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            _, s = svc(tmp)
            h = hooks.build({"preset": "callback", "life": "ttl"}, "main")
            s.store.add(h, hooks.new_secret())
            local = asyncio.run(s._test(h, {}))
            self.assertEqual(local["route"], "local_direct_not_relay")
            async def public_probe(*args):
                return {"ok": False, "why": "offline", "route": "public_relay"}
            with patch.object(s, "_test_via_relay", side_effect=public_probe) as go:
                result = asyncio.run(s.cli({"t": "test", "id": h["id"], "via_relay": True}))
                self.assertEqual(result["route"], "public_relay")
                go.assert_called_once()

    def test_cli_parser_exposes_explicit_public_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        hook_service.add_parser(parser.add_subparsers(dest="command"))
        parsed = parser.parse_args(["hook", "test", "example", "--via-relay"])
        self.assertTrue(parsed.via_relay)
        self.assertFalse(parser.parse_args(["hook", "test", "example"]).via_relay)
