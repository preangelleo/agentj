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
        self.assertIn("daily headroom", quota.render_pick(r))

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

    def test_partial_or_stale_pair_still_uses_available_weekly_data(self):
        for value in (None, {}, {"used_pct": 10, "source_at": NOW-999}):
            self.rep["harnesses"][0]["h5"] = value
            self.assertEqual(quota.pick(self.rep, NOW)["harness"], "claude")
        self.rep["harnesses"][0]["source_at"] = NOW-999
        self.assertEqual(quota.pick(self.rep, NOW)["harness"], "claude")
        self.assertIn("降级", quota.pick(self.rep, NOW)["reason"])
        self.assertEqual(quota.pick({"harnesses": [row("codex")]}, NOW)["harness"], "codex")

    def test_invalid_reading_is_not_repaired_but_other_account_can_be_selected(self):
        for pct in (-1, 101, True, float("nan")):
            self.rep["harnesses"][0]["used_pct"] = pct
            self.assertEqual(quota.pick(self.rep, NOW)["harness"], "codex")
        self.assertIsNone(quota.pick({}, NOW)["harness"])

    def test_owner_daily_headroom_example_and_affinity(self):
        rep = {"harnesses": [row("claude", 30, 4), row("codex", 73, 1)]}
        for r in rep["harnesses"]:
            r.pop("h5")
        result = quota.pick(rep, NOW)
        self.assertEqual(result["harness"], "codex")
        self.assertIn("largest daily headroom", quota.render_pick(result,"en"))
        self.assertNotIn("每天", quota.render_pick(result,"en"))
        self.assertEqual([r["per_day"] for r in result["accounts"]], [17.5, 27])
        self.assertTrue(result["accounts"][0]["h5_unlimited"])
        self.assertEqual(quota.pick(rep, NOW, "agentj")["harness"], "claude")
        self.assertEqual(quota.pick(rep, NOW, "content")["harness"], "codex")
        rep["harnesses"][0]["accounts"] = [{"id": "spare", "name": "备用", "active": False,
            "quota": {"pct": 10, "window": "week", "at": NOW-1000, "reset_at": NOW+86400}}]
        result = quota.pick(rep, NOW)
        self.assertEqual((result["account_id"], result["switch_account"]), ("spare", True))

    def test_analysed_stale_weekly_is_not_invalid_and_clamped_values_stay_invalid(self):
        src={**row('claude',10,1),'status':'stale','stale':True}
        analysed=quota.analyse(src,[],NOW)
        self.assertFalse(analysed['reading_valid'])
        self.assertTrue(analysed['weekly_valid'])
        result=quota.pick({'harnesses':[analysed,quota.analyse(row('codex',50,4),[],NOW)]},NOW)
        self.assertEqual(result['harness'],'claude')
        self.assertIn('降级',result['reason'])
        for value in (-1,101,True,float('nan')):
            bad=quota.analyse({**src,'used_pct':value,'status':'ok','stale':False},[],NOW)
            self.assertFalse(bad['weekly_valid'])
            self.assertEqual(quota.pick({'harnesses':[bad,row('codex')]},NOW)['harness'],'codex')
        bad_source=quota.analyse({**src,'source':'untrusted'},[],NOW)
        self.assertIsNone(quota.pick({'harnesses':[bad_source]},NOW)['harness'])
        bad_h5=row('claude',10,1);bad_h5['h5']['used_pct']=-10
        result=quota.pick({'harnesses':[quota.analyse(bad_h5,[],NOW)]},NOW)
        self.assertEqual(result['harness'],'claude')
        self.assertIsNone(result['accounts'][0]['h5_used_pct'])
        self.assertFalse(result['accounts'][0]['h5_unlimited'])

    def test_report_source_failure_uses_only_unexpired_same_account_weekly_cache(self):
        from _testtmp import TEST_TMP
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            accounts={'claude':{'active':'a','switched':NOW-100,'items':[
                {'id':'a','name':'A','last_quota':{'pct':10,'window':'week','at':NOW-1000,'reset_at':NOW+86400}},
                {'id':'b','name':'B','last_quota':None}]}}
            failed=quota.unknown('claude','source_failed')
            with patch.object(quota,'_accounts',return_value=accounts):
                rep=quota.report(tmp,NOW,sources=[failed])
                result=quota.pick(rep,NOW)
                self.assertEqual((result['harness'],result['account_id'],result['switch_account']),('claude','a',False))
                self.assertIn('降级',result['reason'])
                # New native reading is cached against a, never the future b account.
                quota.report(tmp,NOW,sources=[row('claude',8,1)])
                result=quota.pick(quota.report(tmp,NOW,sources=[failed]),NOW)
                self.assertEqual(result['harness'],'claude')
                self.assertIn('降级',result['reason'])
                accounts['claude'].update(active='b',switched=NOW+1)
                result=quota.pick(quota.report(tmp,NOW+2,sources=[row('claude',8,1)]),NOW+2)
                self.assertEqual((result['account_id'],result['switch_account']),('a',True))
                accounts['claude']['items']=accounts['claude']['items'][1:]
                self.assertIsNone(quota.pick(quota.report(tmp,NOW+2,sources=[failed]),NOW+2)['harness'])
            with patch.object(quota,'_accounts',return_value={}):
                quota.report(tmp,NOW,sources=[row('codex',20,1)])
                rep=quota.report(tmp,NOW+10,sources=[quota.unknown('codex','source_failed')])
                self.assertEqual(quota.pick(rep,NOW+10)['harness'],'codex')
                self.assertIn('降级',quota.pick(rep,NOW+10)['reason'])
                # A failed weekly source can still carry a measured current 5h limit; keep that floor.
                current_h5={'harness':'codex','status':'unknown','h5':{'used_pct':95,'reset_at':NOW+3600,'source_at':NOW}}
                result=quota.pick(quota.report(tmp,NOW+10,sources=[current_h5]),NOW+10)
                self.assertIsNone(result['harness'])
                self.assertEqual(next(r for r in result['accounts'] if r['harness']=='codex')['h5_used_pct'],95)
                self.assertIsNone(quota.pick(quota.report(tmp,NOW+2*86400,sources=[failed]),NOW+2*86400)['harness'])

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
            self.assertEqual(quota.pick({"harnesses": [row("claude"), src]}, NOW)["harness"], "codex")
            for stamp in (None, "bad", "2026-10-24T10:00:00"):
                event["timestamp"] = stamp
                p.write_text(json.dumps(event)+'\n')
                self.assertIsNone(quota.codex_source({"CODEX_HOME": tmp}, NOW)["source_at"])


if __name__ == "__main__":
    unittest.main()
