"""P497: official local readings only, no defaulting missing measurements to zero."""
import _hermetic  # noqa: F401
import argparse
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentj import quota

NOW = 1792812000


def row(kind, used=20, left=3.5, h5=10):
    return {"harness": kind, "status": "ok", "source": {"claude": "statusline", "codex": "session_log"}[kind],
            "used_pct": used, "reset_at": NOW + left * 86400, "source_at": NOW - 10,
            "h5": {"used_pct": h5, "reset_at": NOW + 3600, "source_at": NOW - 10}}


class Pick(unittest.TestCase):
    def setUp(self):
        self.rep = {"harnesses": [row("claude"), row("codex", 50)]}

    def test_slower_normalized_pace(self):
        r = quota.pick(self.rep, NOW)
        self.assertEqual(r["harness"], "claude")
        self.assertEqual(r["accounts"][0]["speed"], .4)
        self.assertIn("0.40x", quota.render_pick(r))

    def test_only_eligible_and_both_high(self):
        self.rep["harnesses"][0]["h5"]["used_pct"] = 80
        self.assertEqual(quota.pick(self.rep, NOW)["harness"], "codex")
        self.rep["harnesses"][1]["used_pct"] = 100
        self.assertEqual(quota.pick(self.rep, NOW)["why"], "both_quota_high")

    def test_tie_daily_headroom_then_deterministic_name(self):
        self.rep["harnesses"] = [row("claude", 20, 3.5), row("codex", 10, 5.25)]
        self.assertEqual(quota.pick(self.rep, NOW)["harness"], "claude")
        self.rep["harnesses"] = [row("codex"), row("claude")]
        self.assertEqual(quota.pick(self.rep, NOW)["harness"], "claude")

    def test_incomplete_pair_never_guesses(self):
        for key, value in (("source_at", NOW-301), ("source_at", NOW+1), ("source_at", None),
                           ("source", "fixture"), ("used_pct", -1), ("used_pct", 101),
                           ("used_pct", True), ("used_pct", float("nan")), ("reset_at", NOW),
                           ("reset_at", NOW+8*86400), ("status", "partial")):
            rep = copy.deepcopy(self.rep)
            rep["harnesses"][0][key] = value
            with self.subTest(key=key, value=value):
                self.assertIsNone(quota.pick(rep, NOW)["harness"])
        for h5 in (None, {}, {"source_at": NOW, "used_pct": 0, "reset_at": NOW-1}):
            rep = copy.deepcopy(self.rep)
            rep["harnesses"][0]["h5"] = h5
            self.assertIsNone(quota.pick(rep, NOW)["harness"])
        self.assertIsNone(quota.pick({"harnesses": [row("codex")]}, NOW)["harness"])
        self.assertIsNone(quota.pick({"harnesses": [row("claude"), row("claude"), row("codex")]}, NOW)["harness"])

    def test_display_clamping_cannot_make_bad_usage_a_valid_pick(self):
        for pct in (-1, 101):
            src = row("claude", pct)
            rep = {"harnesses": [quota.analyse(src, [], NOW), row("codex")]}
            self.assertIsNone(quota.pick(rep, NOW)["harness"])
            src = row("claude")
            src["h5"]["used_pct"] = pct
            rep["harnesses"][0] = quota.analyse(src, [], NOW)
            self.assertIsNone(quota.pick(rep, NOW)["harness"])

    def test_cli_existing_parser_and_callback(self):
        p = argparse.ArgumentParser()
        quota.add_parser(p.add_subparsers())
        a = p.parse_args(["quota", "--pick", "--json"])
        with patch.object(quota, "call", return_value=self.rep), patch.object(quota.time, "time", return_value=NOW), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            a.fn(a)
        self.assertEqual(json.loads(out.getvalue())["harness"], "claude")
        with patch.object(quota, "call", return_value={}), patch("sys.stdout", new_callable=io.StringIO), self.assertRaises(SystemExit) as err:
            a.fn(a)
        self.assertEqual(err.exception.code, 2)

    def test_pick_with_subcommand_never_writes_preferences_or_resets(self):
        p = argparse.ArgumentParser()
        quota.add_parser(p.add_subparsers())
        for sub in (["off"], ["on"], ["resume"], ["reset", "used", "claude"]):
            a = p.parse_args(["quota", "--pick", *sub])
            with patch.object(quota, "call") as call, self.assertRaises(SystemExit):
                a.fn(a)
            call.assert_not_called()

    def test_codex_event_timestamp_not_later_log_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sessions/2026/10/24/rollout-fixture.jsonl"
            p.parent.mkdir(parents=True)
            limits = {"primary": {"used_percent": 10, "resets_at": NOW+3600, "window_minutes": 300},
                      "secondary": {"used_percent": 20, "resets_at": NOW+3*86400, "window_minutes": 10080}}
            event = {"timestamp": datetime.fromtimestamp(NOW-3600, timezone.utc).isoformat(), "payload": {"rate_limits": limits}}
            p.write_text(json.dumps(event) + '\n' + json.dumps({"timestamp": "fresh ordinary text"})+'\n')
            os.utime(p, (NOW, NOW))
            src = quota.codex_source({"CODEX_HOME": tmp}, NOW)
            self.assertEqual(src["source_at"], NOW-3600)
            self.assertIsNone(quota.pick({"harnesses": [row("claude"), src]}, NOW)["harness"])
            for stamp in (None, "bad", "2026-10-24T10:00:00"):
                event["timestamp"] = stamp
                p.write_text(json.dumps(event)+'\n')
                self.assertIsNone(quota.codex_source({"CODEX_HOME": tmp}, NOW)["source_at"])


if __name__ == "__main__":
    unittest.main()
