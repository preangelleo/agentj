"""P558 (support ticket #328): "every Bash call in the main session is declined, no card, the phone
shows full". The customer's state as 0.25.12 left it — a signed `approval.level = full` — replayed on the integrated
P552 + P556 host:

- the signature still verifies → 「全放行」 at once: an ordinary Bash step runs without a card (level_harness), only the
  four kinds ask for Face ID;
- the signing remote got a new key (or was unpaired) → the switch is shown as NOT in force with the reason, the owner is
  told once with the way back (「用本机重新确认」), the Agent is told once, and a Bash step brings a card instead of a
  silent decline; with no remote that can approve, the decline is logged with its reason (`no_device`);
- a scheduled workflow's own steps get no card even while the switch lapsed (they used to be declined unseen);
- one re-confirmation on the phone restores 「全放行」.
"""
import _hermetic  # noqa: F401,I001
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import approval_level, approvals  # noqa: E402

import test_p552_approval_signer as p552  # noqa: E402


class Support328(p552.Merged):
    test_released_full_is_in_force_as_harness = test_released_full_answers_like_harness = None
    test_lapsed_released_full_is_shown = test_a_level_harness_line_does_not_verify_against_another_switch = None
    test_a_reconfirmation_signs_the_merged_switch = test_full_is_no_longer_switched_on = test_one_name = None

    def rekey(self):
        d = approval_level.signers(self.st)
        d["approval.level"]["sk_sha"] = "0" * 64           # the signing remote's approval key is not the one it signed with
        approval_level._path(self.st).write_text(json.dumps(d))

    def test_verified_full_runs_bash_without_a_card(self):
        self.released_full()
        for c in ("ls -la", "python3 tools/report.py > reports/today.md", "git commit -am report"):
            self.assertEqual(self.ask(c)["behavior"], "allow", c)
            self.assertEqual(self.last()["reason"], "level_harness", c)
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.notices, [])

    def test_lapsed_full_is_visible_and_bash_gets_a_card(self):
        self.released_full()
        self.rekey()
        self.assertEqual(approval_level.level(self.host.preferences, self.st), "standard")
        self.assertTrue(self.told_now())
        # the phone's Settings row: not in force + why (the page shows 「未生效：…」 + 「用本机重新确认」)
        self.assertEqual([(i["value"], i["why"]) for i in self.approval_info()["unsigned"]], [("harness", "rekeyed")])
        # the owner, once: the reason and the way back
        self.told.assert_awaited_once()
        text = self.told.await_args.args[1]
        self.assertIn("换了钥匙", text)
        self.assertIn("用本机重新确认", text)
        self.assertIn("security", self.pushes)
        self.assertFalse(self.told_now())
        # the Bash step: a card on the phone, never a silent decline
        res = self.ask("python3 tools/report.py > reports/today.md")
        self.assertEqual(len(self.cards()), 1)
        self.assertEqual(res["behavior"], "deny")          # nobody tapped within the test's 0.4 s
        self.assertEqual(self.last()["reason"], "timeout")
        self.assertIn("没有批准", res["message"])
        # the phone's line (reason + way back) and the Agent's line (Standard applies), each once
        self.assertEqual(sum("换了钥匙" in n and "用本机重新确认" in n for n in self.notices), 1, self.notices)
        self.assertEqual(sum(n.startswith("🔐 配置里写着审批档位「全放行」") and "中等" in n for n in self.notices), 1, self.notices)
        self.ask("ls -la")
        self.assertEqual(len(self.notices), 2, self.notices)

    def test_no_remote_to_approve_is_logged_with_its_reason(self):
        self.released_full()
        self.unpair(self.phone.did)
        self.assertEqual([i["why"] for i in approval_level.unsigned(self.host.preferences, self.st)], ["unpaired"])
        self.assertTrue(self.told.await_count)
        res = self.ask("python3 tools/report.py > reports/today.md")
        self.assertEqual(res["behavior"], "deny")
        self.assertIn("agentj pair", res["message"])
        self.assertEqual(self.last()["reason"], "no_device")
        self.assertEqual(self.cards(), [])

    def test_scheduled_workflow_steps_get_no_card_while_lapsed(self):
        self.released_full()
        self.rekey()
        for c in ("python3 tools/report.py > reports/today.md", "mkdir -p reports && cp /tmp/r.md reports/"):
            self.assertEqual(self.ask(c, background="task")["behavior"], "allow", c)
            self.assertEqual(self.last()["reason"], "background", c)
        self.assertEqual(self.cards(), [])

    def test_one_reconfirmation_restores_full_access(self):
        self.released_full()
        self.rekey()
        self.told_now()
        self.harness()                                       # 「用本机重新确认」: signed `harness` + Face ID
        self.assertEqual(approval_level.unsigned(self.host.preferences, self.st), [])
        self.assertEqual(self.ask("ls -la")["behavior"], "allow")
        self.assertEqual(self.last()["reason"], "level_harness")
        idx = {r["id"]: r for r in approvals.read_log(self.st)}
        self.assertEqual(approvals.check_record(self.st, self.last(), idx), "auto")


if __name__ == "__main__":
    unittest.main()
