"""P296 (Leo, 10-15): mail to the owner / to the allowlist goes without a phone card; everyone else still needs one.

- the owner's own addresses only → no card (approvals.log reason `owner_direct`); a second, non-allowlisted recipient → card;
- every recipient on the e-mail allowlist (any role) → no card (reason `allowlist`), outward filter unchanged;
- the attachment floor still refuses a credential file, card or no card;
- declined / timed out → `why: denied`, the CLI exits 1 and prints one plain sentence on stderr;
- the card offers non-allowlisted recipients as unticked boxes; ticked ones ride in the SAME signature (allow_add) and join
  the allowlist as guests; picks outside the offer, or unsigned, are refused;
- `agentj mail allow <address>` from inside an Agent waits for its card: not signed / declined → nothing changes, exit 1;
  allowed → applied, exit 0.
Local fake SMTP only (tests/mailfake.py).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import approvals, mail as M, wire  # noqa: E402
from mailfake import FakeSMTP  # noqa: E402
from test_p194_owner_direct import AGENT, ALLOW_ME, CODE, _Svc, body_of  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402

FRIEND = "friend@partner.example"
OTHER = "stranger@else.example"


def reasons(st):
    return [r["reason"] for r in approvals.read_log(st) if r["tool"] == "agentj mail send"]


class NoCard(_Svc):
    def allow(self, *rows):
        cfg = M.read_config(self.st)
        cfg["allow"] = cfg["allow"] + [{"addr": a, "role": "guest"} for a in rows]
        M.save_config(self.st, M.validate(cfg))

    async def test_owner_only_sends_without_a_card(self):
        res = await self.svc.agent_send({"to_owner": True, "subject": "报告", "body": "done"})
        self.assertTrue(res["ok"] and res["owner_direct"] and not res["no_card"], res)
        self.ask.assert_awaited_once()
        self.assertEqual(self.smtp.sent[-1][1], [ALLOW_ME])
        self.assertTrue(self.ask.await_args.kwargs["require_passkey"])

    async def test_a_second_recipient_brings_the_card_back(self):
        res = await self.svc.agent_send({"to": [OTHER], "to_owner": True, "subject": "s", "body": "b"})
        self.assertTrue(res["ok"], res)
        self.ask.assert_awaited_once()
        self.assertTrue(self.ask.await_args.kwargs["require_passkey"])
        self.assertFalse(res["no_card"])

    async def test_allowlisted_recipient_needs_no_card_but_stays_outward(self):
        self.allow(FRIEND)
        res = await self.svc.agent_send({"to": [FRIEND], "subject": "s", "body": "电话 13812345678"})
        self.assertTrue(res["ok"] and not res["no_card"] and not res["owner_direct"], res)
        self.ask.assert_awaited_once()
        self.assertNotIn("13812345678", body_of(self.smtp.sent[-1][2]), "not the owner: still masked")
        self.assertTrue(self.ask.await_args.kwargs["require_passkey"])
        self.ask.reset_mock()
        # one recipient off the list → the card again
        await self.svc.agent_send({"to": [FRIEND, OTHER], "subject": "s", "body": "b"})
        self.ask.assert_awaited_once()
        self.assertTrue(self.ask.await_args.kwargs["require_passkey"])

    async def test_credential_attachment_still_refused_without_a_card(self):
        key = self.work / ".env"                 # a credential file by name (media.check key_file), as in test_p184
        key.write_text("K=v")
        res = await self.svc.agent_send({"to_owner": True, "subject": "s", "body": "b", "files": [str(key)]})
        self.assertEqual((res["ok"], res["why"]), (False, "file"), res)
        self.assertIn("key_file", res["detail"])
        self.ask.assert_not_called()
        self.assertEqual(self.smtp.sent, [])

    async def test_denied_says_so_plainly(self):
        self.ask.return_value = {"behavior": "deny"}
        res = await self.svc.agent_send({"to": [OTHER], "subject": "s", "body": "b"})
        self.assertEqual((res["ok"], res["why"]), (False, "denied"))
        self.assertIn("主人没在手机上确认", res["detail"])
        self.assertEqual(self.smtp.sent, [])

    async def test_ticked_recipients_join_the_allowlist_and_next_time_no_card(self):
        self.ask.return_value = {"behavior": "allow", "allow_add": [OTHER]}
        res = await self.svc.agent_send({"to": [OTHER], "subject": "s", "body": "b"})
        self.assertEqual(res["allowlisted"], [OTHER], res)
        row = next(r for r in M.read_config(self.st)["allow"] if r["addr"] == OTHER)
        self.assertEqual(row["role"], "guest")
        self.ask.reset_mock()
        res = await self.svc.agent_send({"to": [OTHER], "subject": "s2", "body": "b"})
        self.assertFalse(res["no_card"], res)
        self.ask.assert_awaited_once()
        self.assertTrue(self.ask.await_args.kwargs["require_passkey"])

    async def test_unticked_stays_off_the_list(self):
        res = await self.svc.agent_send({"to": [OTHER], "subject": "s", "body": "b"})
        self.assertNotIn("allowlisted", res)
        self.assertFalse(any(r["addr"] == OTHER for r in M.read_config(self.st)["allow"]))


class SignedPicks(unittest.TestCase):
    """The real Host: `allow_add` must be signed with the allow and be an ordered subset of the card's own offer."""

    def setUp(self):
        from test_l1 import _state
        self.tmp = tempfile.TemporaryDirectory(prefix="p296s-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []

    def run_card(self, answer):
        from test_l1 import Phone, _host, _ready
        ph = Phone(self.st)
        host = _host(self.st, self.sent)
        s = _ready(host, ph)
        host.ask_ttl = 1

        async def go():
            t = asyncio.create_task(host.ask("agentj mail send", {"summary": "发邮件给：a@x.example、b@x.example\n标题：s"},
                                             batch=False, allow_offer=["a@x.example", "b@x.example"]))
            while not [o for _, o in self.sent if o.get("t") == "ask"] and not t.done():
                await asyncio.sleep(0.01)
            ask = [o for _, o in self.sent if o.get("t") == "ask"][-1]
            self.assertEqual(ask["allow_offer"], ["a@x.example", "b@x.example"])
            await host._app(s, answer(ph, ask))
            return await t
        return asyncio.run(go())

    @staticmethod
    def signed(ph, ask, picks, sign_picks=None):
        d = approvals.shown_digest(ask["tool"], ask["summary"])
        msg = approvals.signed_message(ph.channel, ph.did, ask["id"], "allow", d)
        msg += ("\n" + approvals.allow_add_line(picks if sign_picks is None else sign_picks)).encode()
        return {"t": "answer", "id": ask["id"], "ok": True, "allow_add": picks, "sig": wire.b64u(ph.sk.sign(msg))}

    def test_signed_picks_come_back_and_verify_in_the_log(self):
        res = self.run_card(lambda ph, ask: self.signed(ph, ask, ["b@x.example"]))
        self.assertEqual((res["behavior"], res["allow_add"]), ("allow", ["b@x.example"]))
        rec = approvals.read_log(self.st)[-1]
        self.assertEqual(rec["scope_sha256"], approvals.allow_add_line(["b@x.example"]))
        self.assertEqual(approvals.check_record(self.st, rec), "ok")

    def test_picks_outside_the_offer_are_refused(self):
        res = self.run_card(lambda ph, ask: self.signed(ph, ask, ["evil@x.example"]))
        self.assertEqual(res["behavior"], "deny", "refused answer → the card times out")

    def test_picks_signed_differently_are_refused(self):
        res = self.run_card(lambda ph, ask: self.signed(ph, ask, ["a@x.example", "b@x.example"], sign_picks=["a@x.example"]))
        self.assertEqual(res["behavior"], "deny")

    def test_plain_allow_without_picks_adds_nobody(self):
        res = self.run_card(lambda ph, ask: ph.answer(ask, True))
        self.assertEqual(res["behavior"], "allow")
        self.assertNotIn("allow_add", res)


class AllowFromAgent(unittest.IsolatedAsyncioTestCase):
    """8': `agentj mail allow <address>` from inside an Agent only takes effect after the owner's signed card."""

    async def asyncSetUp(self):
        from test_l1 import _host, _state
        self.tmp = tempfile.TemporaryDirectory(prefix="p296a-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.host = _host(self.st, [])
        self.host.mail = M.Mail(self.host)
        self.base = M.blank_config()
        self.base.update(address=AGENT, allow=[{"addr": ALLOW_ME, "role": "owner"}])
        self.host.mail.set_config(self.base)
        self.out = []
        out = self.out

        class W:
            def write(self, b): out.append(json.loads(b))
            async def drain(self): pass
        self.w = W()
        self.wider = {**self.base, "allow": self.base["allow"] + [{"addr": FRIEND, "role": "guest"}]}

    def listed(self):
        return [r["addr"] for r in M.read_config(self.st)["allow"]]

    async def test_not_signed_does_not_apply(self):
        for ans in ({"behavior": "deny", "message": "timeout"}, {"behavior": "deny"}):
            self.host.ask = AsyncMock(return_value=ans)
            await self.host._ctl_mail(self.w, {"cfg": self.wider}, claimed=False)
            self.assertEqual((self.out[-1]["ok"], self.out[-1]["why"]), (False, "denied"))
            self.assertNotIn(FRIEND, self.listed())
        params=self.host.ask.await_args.args[1]
        self.assertIn(FRIEND,[r['addr'] for r in params['after']['allow']])
        self.assertTrue(self.host.ask.await_args.kwargs['require_passkey'])

    async def test_signed_applies(self):
        self.host.ask = AsyncMock(return_value={"behavior": "allow"})
        await self.host._ctl_mail(self.w, {"cfg": self.wider}, claimed=False)
        self.assertTrue(self.out[-1]["ok"] and self.out[-1]["applied"], self.out[-1])
        self.assertIn(FRIEND, self.listed())

    async def test_owner_terminal_applies_without_a_card(self):
        self.host.ask = AsyncMock()
        await self.host._ctl_mail(self.w, {"cfg": self.wider}, claimed=True)
        self.assertFalse(self.out[-1]["ok"])
        self.assertNotIn(FRIEND,self.listed())
        self.host.ask.assert_awaited_once()
        self.assertTrue(self.host.ask.await_args.kwargs['require_passkey'])


class CliExit(unittest.TestCase):
    """The CLI: declined → exit 1 + one plain sentence on stderr (both `send` and `allow`)."""

    def run_cli(self, argv, fake):
        from agentj import cli
        outb, errb = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory(prefix="p296c-", dir=TEST_TMP) as d, \
                mock.patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(pathlib.Path(d) / "s")}), \
                contextlib.redirect_stdout(outb), contextlib.redirect_stderr(errb), \
                mock.patch.object(sys, "argv", ["agentj", *argv]), fake:
            try:
                cli.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, json.loads(outb.getvalue()), errb.getvalue()

    def test_send_denied_exits_1_with_a_sentence(self):
        fake = mock.patch("agentj.elevate.client_request", return_value={"ok": False, "why": "denied", "detail": "x"})
        code, res, err = self.run_cli(["mail", "send", "--to", OTHER, "--subject", "s", "--body", "b", "--json"], fake)
        self.assertEqual((code, res["why"]), (1, "denied"))
        self.assertIn("主人没在手机上确认，邮件没有发出", err)

    def test_send_ok_exits_0(self):
        fake = mock.patch("agentj.elevate.client_request", return_value={"ok": True, "sent": [ALLOW_ME], "owner_direct": True})
        code, res, err = self.run_cli(["mail", "send", "--to-owner", "--subject", "s", "--body", "b", "--json"], fake)
        self.assertEqual((code, err), (0, ""))

    def test_allow_denied_exits_1(self):
        fake = mock.patch("agentj.names.ctl_call", return_value={"ok": False, "why": "denied", "keys": ["allow+" + FRIEND]})
        code, res, err = self.run_cli(["mail", "allow", FRIEND, "--guest", "--json"], fake)
        self.assertEqual(code, 1)
        self.assertIn("白名单", err)

    def test_allow_applied_exits_0(self):
        fake = mock.patch("agentj.names.ctl_call", return_value={"ok": True, "applied": True})
        code, res, _ = self.run_cli(["mail", "allow", FRIEND, "--guest", "--json"], fake)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
