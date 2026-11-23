"""P558 (support ticket #328): "every Bash call in the main session is declined, no card, the phone
shows full". The customer's state as 0.25.12 left it — a signed `approval.level = full` — replayed on the integrated
P552 + P556 host:

- the signature still verifies → 「全放行」 at once, nothing rewritten;
- the signing remote got a new key (or was unpaired) → the switch is shown as NOT in force with the reason and the owner
  is told once with the way back (「用本机重新确认」); one re-confirmation on the phone restores it.

Since 0.30.7 (ADR-0307-offlist) the level no longer decides an ordinary step: off the card list the owner's Bash runs
without a card (`off_list`) in every one of these states — the ticket's silent decline has no path left — and only a
listed step asks (Face ID card; with no remote that can approve, refused and logged `no_device`). A background request
is trusted only inside a live host-held task scope.
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

    # ADR-0307-offlist (documentation/ARCHITECTURE.md; b50c9a8eeb, 2026-11-11, chairman 11-08 / 11-10) replaced the level
    # model these five vectors were written against: an ordinary (off-list) step is answered by the typed source of its
    # turn — owner / host trust → allow `off_list`, never a card, never `level_harness` — whatever the signed level says
    # or whether it lapsed. What still asks is the list: here the closed real-charge list (#537②, main thread → Face ID
    # card, ADR-0308-fingerprint-scope). The fixture's turn is the owner at the local terminal (`owner_terminal`).
    CHARGE = "stripe charges create --amount 500 --currency usd"

    def ordinary(self, command, **kw):
        n = len(self.cards())
        res = self.ask(command, **kw)
        self.assertEqual(len(self.cards()), n, command)
        return res["behavior"], self.last()["reason"]

    def test_verified_full_runs_bash_without_a_card(self):
        self.released_full()
        for c in ("ls -la", "python3 tools/report.py > reports/today.md", "git commit -am report"):
            self.assertEqual(self.ordinary(c), ("allow", "off_list"), c)
            self.assertNotIn("grant", self.last(), "an off-list answer is the turn's trust, not the level grant")
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.notices, [])
        # the list is not covered by the level: a real charge is a Face ID card (nobody answers within 0.4 s → refused)
        res = self.ask(self.CHARGE)
        self.assertEqual((res["behavior"], self.last()["reason"]), ("deny", "timeout"))
        self.assertEqual([bool(c.get("passkey_required")) for c in self.cards()], [True])

    def test_lapsed_full_is_visible_ordinary_bash_runs_and_a_listed_step_gets_a_card(self):
        self.released_full()
        self.rekey()
        self.assertEqual(approval_level.level(self.host.preferences, self.st), "standard")
        self.assertTrue(self.told_now())
        # the phone's Settings row: not in force + why (the page shows 「未生效：…」 + 「用本机重新确认」)
        self.assertEqual([(i["value"], i["why"]) for i in self.approval_info()["unsigned"]], [("harness", "rekeyed")])
        # the owner, once: the reason and the way back
        self.told.assert_awaited_once()
        text = self.told.await_args.args[1]
        self.assertIn("重新设置了 Face ID / 指纹", text)
        self.assertIn("用本机重新确认", text)
        self.assertIn("security", self.pushes)
        self.assertFalse(self.told_now())
        # the support ticket's symptom — an ordinary Bash step declined without a card — cannot come back: off the list
        # the owner's step runs (no card, no decline), lapsed switch or not
        self.assertEqual(self.ordinary("python3 tools/report.py > reports/today.md"), ("allow", "off_list"))
        # … and a listed step is a card on the phone, never a silent decline
        res = self.ask(self.CHARGE)
        self.assertEqual([bool(c.get("passkey_required")) for c in self.cards()], [True])
        self.assertEqual(res["behavior"], "deny")          # nobody tapped within the test's 0.4 s
        self.assertEqual(self.last()["reason"], "timeout")
        self.assertNotIn("用户在手机上拒绝", res["message"])
        # the phone's line (reason + way back) exactly once, however many steps follow
        self.assertEqual(sum("重新设置了 Face ID / 指纹" in n and "用本机重新确认" in n for n in self.notices), 1, self.notices)
        self.ordinary("ls -la")
        self.assertEqual(len(self.notices), 1, self.notices)

    def test_no_remote_to_approve_is_logged_with_its_reason(self):
        self.released_full()
        self.unpair(self.phone.did)
        self.assertEqual([i["why"] for i in approval_level.unsigned(self.host.preferences, self.st)], ["unpaired"])
        self.assertTrue(self.told.await_count)
        # a step that needs an approval and has no remote to give it: refused, logged with its reason, no card
        res = self.ask(self.CHARGE)
        self.assertEqual(res["behavior"], "deny")
        self.assertIn("No paired remote can approve", res["message"])
        self.assertEqual(self.last()["reason"], "no_device")
        self.assertEqual(self.cards(), [])
        # the owner's ordinary step at the local terminal needs no approval, so there is nothing to refuse
        self.assertEqual(self.ordinary("python3 tools/report.py > reports/today.md"), ("allow", "off_list"))
        self.assertEqual(self.cards(), [])

    def test_scheduled_workflow_steps_get_no_card_while_lapsed(self):
        import time
        from agentj import approval_review, provenance
        self.released_full()
        self.rekey()
        steps = ("python3 tools/report.py > reports/today.md", "mkdir -p reports && cp /tmp/r.md reports/")
        # "background" is no longer a claim the request makes about itself: without a live host-held task scope the
        # request is of unknown source and its write is refused at once (still no card)
        for c in steps:
            self.assertEqual(self.ordinary(c, background="task"), ("deny", "off_list_write_unknown"), c)
        self.host.desktop_turn = 1
        origin = approval_review.remember(self.host, {"id": 1, "ts": int(time.time() * 1000),
                                                      "src": {"k": "terminal", "text": "Owner scheduled the report"}},
                                          provenance.terminal())
        approval_review.register_task_origin(self.host, "fixture-run", "fixture-task", origin)
        with provenance.task_run() as scope:        # the scheduler's own running task: host trust
            scope["approval_task_ref"] = "fixture-run"
            for c in steps:
                self.assertEqual(self.ordinary(c, background="task"), ("allow", "off_list"), c)
        self.assertEqual(self.ordinary(steps[0], background="task"), ("deny", "off_list_write_unknown"), "the scope ended")
        self.assertEqual(self.cards(), [])

    def test_one_reconfirmation_restores_full_access(self):
        self.released_full()
        self.rekey()
        self.told_now()
        self.assertEqual(approval_level.level(self.host.preferences, self.st), "standard")
        self.harness()                                       # 「用本机重新确认」: signed `harness` + Face ID
        self.assertEqual(approval_level.unsigned(self.host.preferences, self.st), [])
        self.assertEqual(approval_level.level(self.host.preferences, self.st), "harness")
        self.assertTrue(approval_level.follow_harness(self.host.preferences, self.st))
        self.assertEqual(self.ordinary("ls -la"), ("allow", "off_list"))


if __name__ == "__main__":
    unittest.main()
