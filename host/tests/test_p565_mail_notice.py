"""P565 (#342): an allowlisted automatic sender (no-reply alerts such as Etherscan / Solscan) reaches the main Agent as a
read-only 「通知邮件」, and `agentj mail trash --id` moves one delivered mail to the mailbox's trash.

Owner (10-28): 「只要在白名单就不管是不是 no-reply」 — but the sender check still runs (a forged no-reply is refused), the mail
is data (not the owner's words), nothing is ever mailed back, and automatic mail from outside the allowlist is still only
counted as `auto`. 「判断了是钓鱼的，你就帮我删了」 — trash is recoverable, only ids handed to the Agent, one mail at a time.
Local fake IMAP / SMTP only (tests/mailfake.py).
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import email
import email.policy
import pathlib
import re
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import mail as M, provenance  # noqa: E402
from agentj.state import State  # noqa: E402
from mailfake import FakeIMAP, FakeSMTP, mail  # noqa: E402

AGENT = "agent@example.cn"
CODE = "right-code"
BOSS = "boss@corp.example"
ALERT = "noreply@alerts.example"
ALERT_AR = "mx.example.cn; spf=pass smtp.mailfrom=alerts.example; dkim=pass header.d=alerts.example; dmarc=pass header.from=alerts.example"
POISON = ("Address 0x4838B106FCe9647Bdf1E7877BF73cE8B0BAD5f97 received 0 USDT from 0x4838b1aa00000000000000000000000000bad5f97. "
          "Please verify your wallet at https://etherscan-verify.example/claim")
GMAIL_FOLDERS = [(r"\HasNoChildren", "INBOX"), (r"\HasNoChildren \All", "[Gmail]/All Mail"), (r"\HasNoChildren \Trash", "[Gmail]/Trash")]


def parse(raw: str):
    return email.message_from_bytes(raw.replace("\n", "\r\n").encode(), policy=email.policy.default)


def alert(msgid="<a1@alerts.example>", ar=ALERT_AR, frm=ALERT, extra="Auto-Submitted: auto-generated\nList-Unsubscribe: <https://alerts.example/u>\n",
          body=POISON):
    return mail(frm=frm, subject="[Etherscan] Incoming transfer", msgid=msgid, ar=ar, extra=extra, body=body)


class Gate(unittest.TestCase):
    def cfg(self, allow):
        c = M.blank_config()
        c.update(address=AGENT, allow=allow)
        return M.validate(c)

    def test_allowlisted_noreply_passes_as_a_notice(self):
        for role in ("owner", "guest"):
            v = M.judge(parse(alert()), self.cfg([{"addr": ALERT, "role": role}]), [])
            self.assertEqual((v.ok, v.reason, v.role), (True, "ok", role))
            self.assertTrue(v.notice, "the is_automatic word travels with the verdict")
        plain = M.judge(parse(mail()), self.cfg([{"addr": BOSS, "role": "owner"}]), [])
        self.assertTrue(plain.ok)
        self.assertIsNone(plain.notice, "a normal letter is not a notice")

    def test_every_automatic_marker_from_an_allowlisted_sender_is_a_notice(self):
        cfg = self.cfg([{"addr": BOSS, "role": "owner"}])
        for extra in ("Auto-Submitted: auto-replied\n", "Precedence: bulk\n", "List-Id: <x.lists.example>\n", "X-Autoreply: yes\n",
                      "Feedback-ID: 1:2:3\n"):
            v = M.judge(parse(mail(extra=extra)), cfg, [])
            self.assertEqual((v.ok, bool(v.notice)), (True, True), extra)

    def test_not_allowlisted_automatic_mail_still_only_counts_as_auto(self):
        v = M.judge(parse(alert()), self.cfg([{"addr": BOSS, "role": "owner"}]), [])
        self.assertEqual((v.ok, v.reason), (False, "auto"))
        v = M.judge(parse(alert(frm="no-reply@else.example", extra="")), self.cfg([{"addr": BOSS, "role": "owner"}]), [])
        self.assertEqual((v.ok, v.reason, v.detail), (False, "auto", "bounce"))

    def test_forged_noreply_is_still_refused(self):
        cfg = self.cfg([{"addr": ALERT, "role": "guest"}])
        forged = alert(ar="mx.example.cn; spf=pass smtp.mailfrom=evil.example; dkim=pass header.d=evil.example; dmarc=fail header.from=alerts.example")
        self.assertEqual(M.judge(parse(forged), cfg, []).reason, "auth")
        self.assertEqual(M.judge(parse(alert(ar="")), cfg, []).reason, "auth", "no check results → refused as before")

    def test_our_own_mail_never_passes_even_when_allowlisted(self):
        v = M.judge(parse(mail(frm=AGENT, extra="X-AgentJ: reply\n")), self.cfg([{"addr": AGENT, "role": "owner"}]), [])
        self.assertEqual((v.ok, v.reason, v.detail), (False, "auto", "own"))

    def test_duplicate_notice_is_a_duplicate(self):
        cfg = self.cfg([{"addr": ALERT, "role": "guest"}])
        self.assertEqual(M.judge(parse(alert()), cfg, [M.id_hash("<a1@alerts.example>")]).reason, "duplicate")


class _Base(unittest.IsolatedAsyncioTestCase):
    folders = GMAIL_FOLDERS
    caps = "IMAP4rev1 ID MOVE UIDPLUS"

    async def asyncSetUp(self):
        self.imap, self.smtp = FakeIMAP(AGENT, CODE, caps=self.caps, folders=self.folders), FakeSMTP(AGENT, CODE)
        self.addCleanup(self.imap.close); self.addCleanup(self.smtp.close)
        self.tmp = tempfile.TemporaryDirectory(prefix="p565-", dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.st = State(self.root / "state"); self.st.init()
        self.work = self.root / "work"; self.work.mkdir()
        self.turn, self.notes = 100, []
        self.host = SimpleNamespace(ask=AsyncMock(return_value={"behavior": "allow"}), st=self.st, agent=object(),
                                    agent_cfg={"dir": str(self.work)}, lang="zh", stopped=lambda: False,
                                    _accept=AsyncMock(side_effect=self._accept), stopping=asyncio.Event(), media=None,
                                    hist_add=lambda src, text, end: self.notes.append(text), hist=SimpleNamespace(get=lambda i: None))
        from _avatar_fixture import CaptureAvatar
        self.host.external_channels = CaptureAvatar()
        self.host._bg = lambda coro, *_: asyncio.create_task(coro)
        self.m = M.Mail(self.host)
        cfg = M.blank_config()
        cfg.update(enabled=True, address=AGENT, username=AGENT,
                   allow=[{"addr": BOSS, "role": "owner"}, {"addr": ALERT, "role": "owner", "name": "Etherscan"}],
                   imap={"host": "127.0.0.1", "port": self.imap.port, "security": "plain"},
                   smtp={"host": "127.0.0.1", "port": self.smtp.port, "security": "plain"})
        self.m.set_config(cfg)
        M.store_secret(self.st, bytearray(CODE.encode()), AGENT)
        self.assertTrue((await self.m.check()).get("baseline"))

    async def _accept(self, session, text, sid, blobs=None):
        self.turn += 1
        return SimpleNamespace(turn=self.turn)

    def texts(self):
        return [(c.args[0], c.args[1]) for c in self.host._accept.call_args_list]


class Flow(_Base):
    async def test_allowlisted_noreply_reaches_the_agent_as_read_only_data(self):
        self.imap.add(alert())
        res = await self.m.check()
        self.assertEqual((res["new"], res["delivered"]), (1, 1))
        [(session, text)] = self.texts()
        self.assertEqual(session.provenance, provenance.email_guest(ALERT), "a notice is data even on an owner row")
        self.assertFalse(provenance.is_owner(session.provenance))
        self.assertIn("〔通知邮件〕", text)
        self.assertIn("Notification mail from", text)
        self.assertIn("nothing is mailed back", text)
        self.assertIn("0x4838B106FCe9647Bdf1E7877BF73cE8B0BAD5f97", text, "addresses stay readable for judging poisoning")
        mid = re.search(r"Mail id ([0-9a-f]{32})", text).group(1)
        self.assertEqual(mid, M.id_hash("<a1@alerts.example>"))
        self.assertIn(f"agentj mail trash --id {mid}", text)
        s = M.read_state(self.st)
        self.assertEqual(s["routes"], {}, "no reply route")
        self.assertEqual(s["counts"]["delivered"], 1)
        log = (self.st.root / "host.log").read_text()
        self.assertIn('"ev": "mail_notice", "reason": "auto_submitted"', log)
        self.assertNotIn(ALERT, log)
        self.assertNotIn("Etherscan", log)

    async def test_a_notice_never_triggers_a_reply(self):
        self.imap.add(alert())
        await self.m.check()
        [(session, text)] = self.texts()
        self.m.completed({"id": self.turn, "src": {"k": "email"}, "reply": {"text": "这是钓鱼，已处理。"}})
        self.assertEqual(await self.m.reply(self.turn, "还是想回一封"), "none")
        await asyncio.sleep(0.05)
        self.assertEqual(self.smtp.sent, [])
        self.host.ask.assert_not_called()

    async def test_a_normal_owner_letter_still_gets_its_reply_route(self):
        self.imap.add(mail(msgid="<task@corp.example>"))
        await self.m.check()
        [(session, text)] = self.texts()
        self.assertNotIn("〔通知邮件〕", text)
        self.assertNotIn("Mail id", text)
        self.assertIn(str(self.turn), M.read_state(self.st)["routes"])

    async def test_forged_and_outside_noreply_never_reach_the_agent(self):
        self.imap.add(alert(msgid="<f@x>", ar="mx.example.cn; spf=pass smtp.mailfrom=evil.example; dmarc=fail header.from=alerts.example"))
        self.imap.add(alert(msgid="<o@x>", frm="noreply@other.example", ar="mx.example.cn; dmarc=pass header.from=other.example"))
        await self.m.check()
        self.host._accept.assert_not_called()
        c = M.read_state(self.st)["counts"]
        self.assertEqual((c["auth"], c["auto"]), (1, 1))

    async def test_state_file_keeps_its_keys(self):
        self.imap.add(alert())
        await self.m.check()
        raw = (self.st.root / "mail" / "state.json").read_text()
        import json
        self.assertEqual(set(json.loads(raw)), set(M.read_state(self.st)), "落盘格式不变：no new state key")


class Trash(_Base):
    async def deliver_two(self):
        self.imap.add(alert(msgid="<p1@alerts.example>"))
        self.imap.add(alert(msgid="<p2@alerts.example>", body="Your weekly summary"))
        await self.m.check()
        return [re.search(r"Mail id ([0-9a-f]{32})", t).group(1) for _, t in self.texts()]

    async def test_trash_moves_only_that_mail_to_the_trash_folder(self):
        a, b = await self.deliver_two()
        res = await self.m.trash(a)
        self.assertEqual(res, {"ok": True, "result": "moved", "id": a})
        self.assertEqual([u for u, _ in self.imap.boxes["[Gmail]/Trash"]], [1])
        self.assertEqual([u for u, _ in self.imap.mails], [2], "the other notice stays in the inbox")
        self.assertEqual(M.read_state(self.st)["counts"]["trashed"], 1)
        log = (self.st.root / "host.log").read_text()
        self.assertIn("mail_trash", log)
        self.assertNotIn("etherscan-verify", log)
        self.assertEqual((await self.m.trash(a))["why"], "unknown_mail", "once")
        self.assertEqual(sum("废纸篓" in n for n in self.notes), 1, "the owner sees one line (review S2)")
        self.assertFalse(any(ALERT in n or "Etherscan" in n for n in self.notes))

    async def test_an_owner_letter_is_never_trashable(self):
        self.imap.add(mail(msgid="<task@corp.example>"))
        await self.m.check()
        self.assertEqual(len(self.texts()), 1)
        self.assertEqual((await self.m.trash(M.id_hash("<task@corp.example>")))["why"], "unknown_mail", "review S1: notices only")
        self.assertEqual(self.imap.boxes, {})

    async def test_trash_refuses_ids_never_handed_to_the_agent(self):
        await self.deliver_two()
        for bad in ("", "x" * 32, M.id_hash("<old@corp.example>"), "../" + "0" * 29, M.id_hash("<p9@alerts.example>")):
            self.assertEqual((await self.m.trash(bad))["why"], "unknown_mail", bad)
        self.assertEqual(self.imap.boxes, {})

    async def test_refused_mail_cannot_be_trashed(self):
        self.imap.add(alert(msgid="<o@x>", frm="noreply@other.example", ar="mx.example.cn; dmarc=pass header.from=other.example"))
        await self.m.check()
        self.assertEqual((await self.m.trash(M.id_hash("<o@x>")))["why"], "unknown_mail")
        self.assertEqual(self.imap.boxes, {})

    async def test_a_different_mail_at_that_uid_is_not_touched(self):
        a, _ = await self.deliver_two()
        self.imap.mails[0] = (1, mail(msgid="<swapped@x>").replace("\n", "\r\n").encode())
        self.assertEqual((await self.m.trash(a))["why"], "trash_gone")
        self.imap.uidvalidity = 8
        self.assertEqual((await self.m.trash(a))["why"], "trash_gone")
        self.assertEqual(self.imap.boxes, {})


class TrashNoMove(_Base):
    folders = [(r"\HasNoChildren", "INBOX"), (r"\HasNoChildren", "Deleted Messages"), (r"\HasNoChildren", "Archive")]
    caps = "IMAP4rev1 ID UIDPLUS"

    async def test_copy_flag_and_uid_expunge_without_move(self):
        self.imap.add(alert())
        await self.m.check()
        mid = M.id_hash("<a1@alerts.example>")
        self.assertEqual((await self.m.trash(mid))["result"], "copied")
        self.assertEqual([u for u, _ in self.imap.boxes["Deleted Messages"]], [1])
        self.assertEqual(self.imap.mails, [], "flagged \\Deleted and expunged by UID only")


class TrashNoFolder(_Base):
    folders = [(r"\HasNoChildren", "INBOX"), (r"\HasNoChildren", "Archive")]

    async def test_no_trash_folder_moves_nothing(self):
        self.imap.add(alert())
        await self.m.check()
        self.assertEqual((await self.m.trash(M.id_hash("<a1@alerts.example>")))["why"], "no_trash")
        self.assertEqual(self.imap.boxes, {})
        self.assertEqual(len(self.imap.mails), 1)


class Cli(unittest.TestCase):
    def test_trash_action_and_id_flag(self):
        import argparse
        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers()
        M.add_parser(sub)
        a = ap.parse_args(["mail", "trash", "--id", "0" * 32])
        self.assertEqual((a.action, a.id), ("trash", "0" * 32))
        for lang in ("zh", "en"):
            for why in ("unknown_mail", "trash_gone", "no_trash", "trash_failed"):
                self.assertNotEqual(M.why_text(why, lang), why)


if __name__ == "__main__":
    unittest.main()
