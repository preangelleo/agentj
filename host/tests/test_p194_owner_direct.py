"""P194: slot 0 of the address book = the seat's owner, and owner-directed outbound is judged in code by the destination.

- privacy owner mode: e-mail, phone, links (with their tokens), access codes, one-time codes, private IPs as written; every
  credential shape still hidden. `privacy.credentials` finds exactly what that mode hides (keys, passwords, seed words), never
  a low-risk code.
- contacts: slot 0 always first, found as 「我」/ me, never merged into, never removed or edited by the Agent; set only by the
  owner (card / phone / terminal); its e-mails + the 「我本人」 allowlist rows are the owner's addresses.
- `agentj mail send` (Leo's four cases): owner mail with an access code → as written; owner mail with an API key → the key
  leaves the mail and becomes a secret pickup card; mail to contact #3 → masked as before; a Telegram group → filtered as
  before. Mixed recipients → masked. The approval card says which filter applies.
- the owner card after the first welcome; the owner's Telegram private chat; mail replies to the owner.
Local fake SMTP only (tests/mailfake.py).
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import email
import email.policy
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import contacts as C, mail as M, owner_route, privacy  # noqa: E402
from agentj.contacts_serve import ContactService  # noqa: E402
from agentj.state import State  # noqa: E402
from mailfake import FakeSMTP  # noqa: E402

AGENT = "agent@example.cn"
CODE = "right-code"
ME = "leo@corp.example"
ALLOW_ME = "leo.alt@corp.example"
KEY = privacy.FAKE_SECRETS["openrouter"]
ACCESS = "访问码 7F3K-29QX，验证码 481920，报告授权码 AJUP-K3M9Q-7XW2P-R8T4N-B6Y1Z"
LINK_Q = "Zx9Kq2Lm" + "8Np4Rt6Vw0Yb3Cd5"   # split: the shared credential scanner reads a literal ?token=… as a live token
LINK = "https://r.agentj.app/view/AbCdEf0123456789XyZaBcDeF0123456?token=" + LINK_Q


def body_of(raw: bytes) -> str:
    m = email.message_from_bytes(raw, policy=email.policy.default)
    return m.get_body(preferencelist=("plain",)).get_content()


class Privacy(unittest.TestCase):
    def test_owner_mode_keeps_codes_links_contacts(self):
        t = f"{ACCESS}，报告 {LINK} 发到 zs@qq.com 电话 13812345678 内网 10.20.30.40"
        out = privacy.owner_text(t)
        self.assertEqual(out, t)
        strict = privacy.redact(t)
        for gone in ("zs@qq.com", "13812345678", "10.20.30.40", LINK_Q):
            self.assertNotIn(gone, strict)

    def test_credentials_hidden_and_found(self):
        seed = "abandon ability able about above absent absorb abstract absurd abuse access accident"
        t = f"{ACCESS} key: {KEY} password=\"hunter2hunter\" 助记词: {seed} {LINK}"
        out = privacy.owner_text(t)
        for gone in (KEY, "hunter2hunter", seed):
            self.assertNotIn(gone, out)
        self.assertIn("7F3K-29QX", out)
        self.assertIn(LINK, out)
        self.assertEqual(privacy.owner_text(out), out)                    # idempotent
        kinds = [(k, v) for k, v, _s, _e in privacy.credentials(t)]
        self.assertEqual([k for k, _ in kinds], ["openrouter_key", "credential_assignment", "mnemonic"])
        self.assertEqual(kinds[0][1], KEY)
        self.assertEqual(privacy.credentials(f"{ACCESS} {LINK} zs@qq.com"), [])
        # a key inside a link still counts
        self.assertEqual([k for k, *_ in privacy.credentials("https://x.example/api?key=AIza" + "B" * 35)], ["google_api_key"])
        self.assertNotIn(seed, privacy.redact(t), "strict hides seed words too")


class _Svc(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.smtp = FakeSMTP(AGENT, CODE)
        self.addCleanup(self.smtp.close)
        self.tmp = tempfile.TemporaryDirectory(prefix="p194-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.st = State(root / "state"); self.st.init()
        self.st.sign_key = lambda d: b"k" * 32 if d == "dev1" else None
        self.work = root / "work"; self.work.mkdir()
        self.sent, self.bcast, self.acts = [], [], []

        async def send_app(s, obj):
            self.sent.append(obj)
            return True

        async def send_ready(fn):
            self.bcast.append(fn(None))
        self.ask = AsyncMock(return_value={"behavior": "allow"})
        self.out = SimpleNamespace(send=AsyncMock(return_value={"result": "sent"}))
        self.host = SimpleNamespace(st=self.st, lang="zh", agent=SimpleNamespace(kind="claude", native_source={"kind": "terminal", "role": "owner"}), agent_cfg={"dir": str(self.work)},
                                    media=None, hist=SimpleNamespace(get=lambda i: None), stopped=lambda: False,
                                    send_app=send_app, _send_ready=send_ready, ask=self.ask, _approvers=lambda: ["dev1"],
                                    elevate=SimpleNamespace(out=self.out),
                                    activity=lambda kind, **kw: self.acts.append((kind, kw)))   # P296: no-card sends log here
        self.svc = ContactService(SimpleNamespace(host=self.host, st=self.st))
        self.s = SimpleNamespace(device="dev1")
        cfg = M.blank_config()
        cfg.update(address=AGENT, username=AGENT, allow=[{"addr": ALLOW_ME, "role": "owner"}],
                   imap={"host": "127.0.0.1", "port": 1, "security": "plain"},
                   smtp={"host": "127.0.0.1", "port": self.smtp.port, "security": "plain"})
        M.save_config(self.st, M.validate(cfg))
        M.store_secret(self.st, bytearray(CODE.encode()), AGENT)

    def set_me(self):
        return C.local(self.st, {"op": "owner_set", "owner_ok": True,
                                 "contact": {"name": "王董", "emails": [ME], "phones": ["13800001111"]}})

    async def card(self, req):
        task = asyncio.ensure_future(self.svc.card(req))
        for _ in range(50):
            await asyncio.sleep(0)
            if self.bcast:
                break
        return task, self.bcast[-1]


class Slot0(_Svc):
    async def test_slot0_first_found_as_me_never_merged_or_removed(self):
        for n, a in (("张三", "zs@qq.com"), ("李四", "l4@163.com"), ("王五", "w5@x.cn")):
            await self.svc.agent({"op": "add", "contact": {"name": n, "emails": [a]}})
        res = self.set_me()
        self.assertEqual((res["ok"], res["result"], res["contact"]["id"], res["contact"]["slot"]), (True, "added", C.OWNER_ID, 0))
        for how in C.SORTS:
            self.assertEqual(C.local(self.st, {"op": "list", "sort": how})["contacts"][0]["id"], C.OWNER_ID, how)
        for q in ("我", "我自己", "me"):
            self.assertEqual(C.local(self.st, {"op": "find", "q": q})["contacts"][0]["id"], C.OWNER_ID)
        self.assertEqual(C.owner_emails(self.st), [ME, ALLOW_ME])
        # the Agent: adding a contact that shares the owner's e-mail never merges an address into slot 0
        await self.svc.agent({"op": "add", "contact": {"name": "王董", "emails": [ME, "evil@x.cn"]}})
        self.assertEqual([e["v"] for e in C.owner(C.read(self.st))["emails"]], [ME])
        # the Agent cannot set, edit or remove slot 0 — not even by passing owner_ok
        for req in ({"op": "owner_set", "owner_ok": True, "contact": {"emails": ["evil@x.cn"]}},
                    {"op": "edit", "id": C.OWNER_ID, "owner_ok": True, "changes": {"add_email": ["evil@x.cn"]}},
                    {"op": "rm", "id": C.OWNER_ID, "owner_ok": True}):
            res = await self.svc.agent(req)
            self.assertFalse(res["ok"], req)
        self.assertEqual(C.owner_emails(self.st), [ME, ALLOW_ME])
        with self.assertRaises(C.ContactError):
            C.remove(C.read(self.st), C.OWNER_ID)
        self.assertEqual(C.local(self.st, {"op": "owner_set", "owner_ok": True, "contact": {"name": "x"}})["why"], "no_email")

    async def test_owner_card_prefilled_saved_only_by_the_tap(self):
        task, m = await self.card({"op": "card", "owner": True, "contact": {}})
        self.assertEqual((m["mode"], m["contact"]["emails"][0]["v"]), ("owner", ALLOW_ME), "suggestion = the 「我本人」 address")
        self.assertIn("0 号位", m["why"])
        self.assertIsNone(C.owner(C.read(self.st)), "nothing saved before the tap")
        await self.svc.on_phone(self.s, {"t": "ct_card_answer", "id": m["id"], "n": m["n"], "ok": True,
                                         "contact": {"name": "王董", "emails": [{"v": ME}], "phones": []}})
        self.assertEqual((await task)["result"], "saved")
        self.assertEqual(C.owner_emails(self.st)[0], ME)
        # skipped card: nothing changes
        self.bcast.clear()
        task, m = await self.card({"op": "card", "owner": True, "contact": {"emails": ["new@x.cn"]}})
        await self.svc.on_phone(self.s, {"t": "ct_card_answer", "id": m["id"], "n": m["n"], "ok": False})
        self.assertEqual((await task)["result"], "cancelled")
        self.assertEqual(C.owner_emails(self.st)[0], ME)
        # Settings on the phone: ct_save with slot 0's id
        self.sent.clear()
        await self.svc.on_phone(self.s, {"t": "ct_save", "r": "r" * 16, "id": C.OWNER_ID,
                                         "contact": {"name": "王董", "emails": ["boss@corp.example"]}})
        self.assertEqual((self.sent[-1]["ok"], C.owner_emails(self.st)[0]), (True, "boss@corp.example"))
        self.sent.clear()
        await self.svc.on_phone(self.s, {"t": "ct_list", "r": "r" * 16})
        self.assertEqual((self.sent[-1]["owner"]["id"], self.sent[-1]["me"][0]), (C.OWNER_ID, "boss@corp.example"))


class AgentMail(_Svc):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.set_me()
        await self.svc.agent({"op": "add", "contact": {"name": "张三", "emails": ["zs@qq.com"]}})
        await self.svc.agent({"op": "add", "contact": {"name": "李四", "emails": ["l4@163.com"]}})
        await self.svc.agent({"op": "add", "contact": {"name": "王五", "emails": ["w5@x.cn"]}})    # 3 号位

    def summary(self):
        if self.ask.call_args is None:          # P296: owner-direct goes without a card; the same text is in activity
            return [kw for k, kw in self.acts if k == "auto"][-1]["summary"]
        return self.ask.call_args.args[1]["summary"]

    async def test_owner_mail_with_access_code_goes_as_written(self):
        body = f"{ACCESS}\n报告：{LINK}\n张三的邮箱 zs@qq.com，电话 13812345678"
        res = await self.svc.agent_send({"to": ["我"], "subject": "访问码 7F3K-29QX", "body": body})
        self.assertTrue(res["ok"] and res["owner_direct"], res)
        _frm, to, raw = self.smtp.sent[-1]
        self.assertEqual(to, [ME])
        got = body_of(raw)
        for keep in ("7F3K-29QX", "481920", "AJUP-K3M9Q-7XW2P-R8T4N-B6Y1Z", LINK, "zs@qq.com", "13812345678"):
            self.assertIn(keep, got)
        self.assertIn("7F3K-29QX", email.message_from_bytes(raw, policy=email.policy.default)["Subject"])
        self.assertIn("发给你自己：原样发出", self.summary())
        self.ask.assert_not_awaited()  # P573 (#386-3, replaces P503 / P551 here): mail to the owner's own address — no card
        self.out.send.assert_not_called()

    async def test_owner_mail_with_api_key_becomes_a_secret_card(self):
        res = await self.svc.agent_send({"to": [ME], "subject": "OpenRouter key", "body": f"你要的 key：{KEY}\n访问码 7F3K-29QX"})
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["secret_cards"], res["withheld"]), (1, 0))
        got = body_of(self.smtp.sent[-1][2])
        self.assertNotIn(KEY, got)
        self.assertNotIn(KEY[:12], got)
        self.assertIn("密钥领取卡", got)
        self.assertIn("7F3K-29QX", got)
        card = self.out.send.call_args.args[0]
        self.assertEqual((card["kind"], card["value"]), ("text", KEY))
        self.assertIn("1 个密钥不随邮件发出", self.summary())

    async def test_no_paired_device_key_withheld(self):
        self.host._approvers = lambda: []
        res = await self.svc.agent_send({"to": ["me"], "subject": "k", "body": f"key {KEY}"})
        self.assertEqual((res["secret_cards"], res["withheld"]), (0, 1))
        got = body_of(self.smtp.sent[-1][2])
        self.assertNotIn(KEY, got)
        self.assertIn("已遮住", got)

    async def test_mail_to_contact_three_is_masked_as_before(self):
        res = await self.svc.agent_send({"to": ["王五"], "subject": "联系 zs@qq.com",
                                         "body": f"张三的邮箱 zs@qq.com，电话 13812345678，key {KEY}"})
        self.assertTrue(res["ok"] and not res["owner_direct"], res)
        _frm, to, raw = self.smtp.sent[-1]
        self.assertEqual(to, ["w5@x.cn"])
        got = body_of(raw)
        for gone in ("zs@qq.com", "13812345678", KEY):
            self.assertNotIn(gone, got)
        self.assertNotIn("zs@qq.com", email.message_from_bytes(raw, policy=email.policy.default)["Subject"])
        self.out.send.assert_not_called()
        self.assertIn("发给外人", self.summary())

    async def test_owner_plus_someone_else_is_masked(self):
        res = await self.svc.agent_send({"to": ["我", "张三"], "subject": "x", "body": "电话 13812345678"})
        self.assertFalse(res["owner_direct"])
        self.assertNotIn("13812345678", body_of(self.smtp.sent[-1][2]))

    async def test_allowlist_owner_row_counts_as_me(self):
        res = await self.svc.agent_send({"to": [ALLOW_ME], "subject": "x", "body": "电话 13812345678"})
        self.assertTrue(res["owner_direct"])
        self.assertIn("13812345678", body_of(self.smtp.sent[-1][2]))


class Telegram(unittest.IsolatedAsyncioTestCase):
    def tg(self, cards=True):
        from agentj import telegram
        st = SimpleNamespace(log=lambda *a, **k: None)
        out = SimpleNamespace(send=AsyncMock(return_value={"result": "sent"}))
        host = SimpleNamespace(st=st, lang="zh", stopped=lambda: False, _approvers=lambda: ["dev1"] if cards else [],
                               elevate=SimpleNamespace(out=out), media=None)
        bot = telegram.Telegram.__new__(telegram.Telegram)
        bot.host = host
        bot.out = asyncio.Queue(maxsize=20)
        bot.group = lambda chat_id, uid: {"profile": "proxy"} if chat_id == -100 else None
        bot.private_domains = lambda: ()
        return bot, out

    async def test_owner_private_chat_as_written_keys_to_card(self):
        bot, out = self.tg()
        enrolled = {"owner_id": 42}
        self.assertTrue(bot.queue_reply(enrolled, 1, 42, 42, f"zs@qq.com 13812345678 {ACCESS} key {KEY}"))
        _e, payload = bot.out.get_nowait()
        for keep in ("zs@qq.com", "13812345678", "7F3K-29QX"):
            self.assertIn(keep, payload)
        self.assertNotIn(KEY, payload)
        self.assertIn("密钥领取卡", payload)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(out.send.call_args.args[0]["value"], KEY)

    async def test_group_still_filtered(self):
        bot, out = self.tg()
        self.assertTrue(bot.queue_reply({"owner_id": 42}, 2, -100, 7, f"zs@qq.com 13812345678 {LINK} key {KEY}"))
        _e, payload = bot.out.get_nowait()
        for gone in ("zs@qq.com", "13812345678", LINK, KEY):
            self.assertNotIn(gone, payload["text"])
        out.send.assert_not_called()


import test_p170_mail as P170  # noqa: E402


class MailReply(P170._Base):
    async def test_owner_reply_as_written_key_withheld_guest_still_filtered(self):
        self.imap.add(P170.mail(msgid="<o1@corp.example>", body="把 zs@qq.com 的报价发我，电话 13812345678"))
        await self.m.check()
        [(_s, text, _b)] = self.delivered()
        self.assertIn("zs@qq.com", text, "the owner's own words reach the Agent as written")
        self.assertIn("13812345678", text)
        await self.finish(101, f"zs@qq.com 的访问码 7F3K-29QX，{LINK}，key {KEY}")
        [(_f, to, msg)] = self.sent()
        got = msg.get_body(("plain",)).get_content()
        self.assertEqual(to, [P170.BOSS])
        for keep in ("zs@qq.com", "7F3K-29QX", LINK):
            self.assertIn(keep, got)
        self.assertNotIn(KEY, got)
        self.assertIn("已遮住", got, "no paired device in this host: the key is withheld, not mailed")


class Divert(unittest.IsolatedAsyncioTestCase):
    async def test_at_most_three_cards_rest_withheld(self):
        out = SimpleNamespace(send=AsyncMock(return_value={"result": "sent"}))
        host = SimpleNamespace(st=SimpleNamespace(log=lambda *a, **k: None), stopped=lambda: False, _approvers=lambda: ["d"],
                               elevate=SimpleNamespace(out=out))
        keys = [privacy.FAKE_SECRETS["openrouter"][:-1] + c for c in "ABCD"]
        text, rep = await owner_route.divert(host, " / ".join(keys), "mail", "en", "Keys")
        self.assertEqual(rep, {"cards": 3, "withheld": 1})
        self.assertEqual(text.count("secret pickup card is waiting"), 3)
        self.assertEqual(text.count("is hidden"), 1)
        for k in keys:
            self.assertNotIn(k, text)

    async def test_welcome_pops_owner_card_once(self):
        from agentj import serve
        tmp = tempfile.TemporaryDirectory(prefix="p194w-", dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name) / "s"); st.init()
        card = AsyncMock(return_value={"result": "cancelled"})
        fake = SimpleNamespace(OWNER_CARD_DELAY=0, st=st, stopped=lambda: False,
                               elevate=SimpleNamespace(contacts=SimpleNamespace(card=card)))
        await serve.Host._owner_card_after_welcome(fake)
        self.assertTrue(card.call_args.args[0]["owner"])
        C.local(st, {"op": "owner_set", "owner_ok": True, "contact": {"emails": [ME]}})
        card.reset_mock()
        await serve.Host._owner_card_after_welcome(fake)
        card.assert_not_called()


# 追加 1 / 2 (Leo 01:1x / 01:2x): the reply that showed 「~<redacted>.html 找不到了」 ×3 and R01–R03 pictures as
# `~<redacted>.webp` — 0.18.4's shared-session Stop text went through the STRICT redact, whose high-entropy rule judged the
# whole `/coding/…/gallery/R0n-name` run (≥ 32 path chars, entropy 4.32 / 4.32 / 4.34 ≥ 4.3; R04-story 4.29 slipped under).
GALLERY = ["R01-briefing", "R02-tech", "R03-pitch", "R04-story", "R05-gallery", "R06-audio", "R07-video", "R08-data",
           "R09-howto", "R10-decision", "R11-timeline", "R12-faq", "R13-launch", "R14-research"]
PAGES = ["R01-briefing", "R02-tech", "R03-pitch"]


def leo_reply(root: str) -> str:
    g = f"{root}/coding/agentjarvis-catalog/skills/web-page-report/gallery"
    lines = ["14 个模板的效果图："] + [f"![{n} 效果]({g}/{n}.webp)" for n in GALLERY]
    lines += ["其中三个可以直接打开看网页："] + [f"- [{n} 网页]({g}/{n}/index.html)" for n in PAGES]
    return "\n".join(lines)


class GalleryPaths(unittest.TestCase):
    def test_owner_channel_keeps_every_path(self):
        from agentj import shared, shared_state
        text = leo_reply("/home/someone")
        for f in (privacy.owner_text, shared.public_text, shared_state.redact.scrub_message):
            out = f(text)
            self.assertNotIn("<redacted>", out, f)
            self.assertEqual(out, text.replace("/home/someone/", "~/"), f)
        strict = privacy.redact(text)                           # the outward filter is unchanged — and this was the bug
        self.assertIn("~<redacted>.webp", strict)
        self.assertIn("~<redacted>.html", strict)
        self.assertEqual(privacy.credentials(text), [], "a path is not a credential")

    def test_owner_path_keeps_its_uuid_but_a_key_inside_still_goes(self):
        p = "/home/u/proj/sessions/9fK2xQ7LmA3vR8tY1wZ5cB6nD0pE4hJ/report.html"
        self.assertEqual(privacy.owner_text(p), "~/proj/sessions/9fK2xQ7LmA3vR8tY1wZ5cB6nD0pE4hJ/report.html")
        self.assertNotIn(KEY, privacy.owner_text(f"/home/u/x/{KEY}/a.txt"))

    def test_media_scan_offers_all_17_and_says_why_otherwise(self):
        from unittest import mock
        from agentj import media
        tmp = tempfile.TemporaryDirectory(prefix="p194g-", dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        home = pathlib.Path(tmp.name) / "home"
        g = home / "coding/agentjarvis-catalog/skills/web-page-report/gallery"
        for i, n in enumerate(GALLERY):
            (g / n).mkdir(parents=True)
            (g / f"{n}.webp").write_bytes(b"RIFF" + (100 + i).to_bytes(4, "little") + b"WEBPVP8 " + bytes(100 + i))
            (g / n / "index.html").write_text(f"<!doctype html><title>{n}</title><p>{n}</p>")
        st = State(pathlib.Path(tmp.name) / "s"); st.init()
        m = media.Media(st, str(home / "coding"))
        with mock.patch.dict("os.environ", {"HOME": str(home)}):
            items, skips = m.scan(privacy.owner_text(leo_reply("/home/someone").replace("/home/someone", "~")), 3)
            self.assertEqual(skips, [])
            self.assertEqual([x["name"] for x in items], [f"{n}.webp" for n in GALLERY] + ["index.html"] * 3)
            self.assertEqual([x["kind"] for x in items[-3:]], ["html"] * 3)
            # the same reply as 0.18.4 masked it: never 「找不到了」 — the note says the path was masked
            items, skips = m.scan(privacy.redact(leo_reply("/home/someone")), 4)
            self.assertEqual({x["why"] for x in skips}, {"masked"})
            self.assertIn("R04-story.webp", [x["name"] for x in items], "what the strict rule left readable still goes")


if __name__ == "__main__":
    unittest.main()
