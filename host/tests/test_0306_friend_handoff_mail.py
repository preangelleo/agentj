"""ADR-0306-friend-handoff-mail (Jarvis 11-10 01:3x: 「好友卡「交给主 Agent」回合里发邮件：弹卡」).

The owner's signed 「交给主 Agent 来办」 click → serve._friend_main → ONE friend_task Send that carries a host-held handoff
record (friend_handoff.mint, keyed by the F3 fence nonce, bound to that Send object). In that running turn
`agentj mail send` (contacts_serve.agent_send) to a non-owner recipient opens the normal UV approval card through the REAL
Host.ask → list_approval → wire path (uv_list.required sees the host-set `friend_handoff` marker); the card names the
friend by display name, never the AJ-… id; it sends only after a signed allow + passkey from the paired phone. Mail to the
owner alone passes without a card (the 0.30.6 owner-only rule). A friend_task turn without a handoff record keeps today's
no-card refusal; a stale / replayed / forged handoff marker is refused with no card (`friend_handoff_unverified`).
Local fake SMTP only (tests/mailfake.py); synthetic @example.com addresses.
Run through the host-affected gate with P44_HOST_ONLY=test_0306_friend_handoff_mail (isolated HOME / XDG_*).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time

_ISO = tempfile.mkdtemp(prefix="p0306-fcmail-")
for _k, _sub in (("HOME", "home"), ("XDG_DATA_HOME", "xdg-data"), ("XDG_STATE_HOME", "xdg-state"),
                 ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_RUNTIME_DIR", "xdg-run")):
    os.environ[_k] = os.path.join(_ISO, _sub)
    os.makedirs(os.environ[_k], mode=0o700, exist_ok=True)
_BIN = pathlib.Path(_ISO) / "bin"
_BIN.mkdir()
(_BIN / "systemctl").write_text("#!/bin/sh\nexit 1\n")
(_BIN / "systemctl").chmod(stat.S_IRWXU)
os.environ["PATH"] = str(_BIN) + os.pathsep + os.environ.get("PATH", "")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_p71_peer_service import FakeNet, FakeSessions, peer_keys, pmsg, sign_answer, Clock  # noqa: E402
from test_l1 import Phone, _owner_host, _ready, _state  # noqa: E402
from mailfake import FakeSMTP  # noqa: E402
from agentj import approvals, compose, contacts as C, friend_handoff, mail as M, peer, peer_service, uv_list, wire  # noqa: E402
from agentj.contacts_serve import ContactService  # noqa: E402
from agentj.peer_service import HAND_MAIN, PeerService  # noqa: E402

FRONT_JS = pathlib.Path(__file__).resolve().parents[2] / "web" / "test" / "p0306-handoff-front.mjs"
AGENT = "agent.p0306@example.com"
SRC_ZH = "来源：好友「Father TB」的消息，主人点了「交给主 Agent 来办」"
SRC_EN = 'From: a message from friend "Father TB" that the owner handed to the main Agent'


def page_front(card):
    """The card front as the real page paints it (approval541 + approval-source572), zh and en."""
    r = subprocess.run(["node", str(FRONT_JS)], input=json.dumps([{"card": card, "lang": "zh"}, {"card": card, "lang": "en"}]),
                       capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout)
CODE = "right-code"
OWNER = "owner.p0306@example.com"
BOB = "bob.p0306@example.com"


class HandoffMail(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p0306m-", dir=os.environ.get("TEST_TMP") or None)
        self.addCleanup(self.tmp.cleanup)
        os.environ[peer_service.TEST_CERT_ENV] = wire.b64u(os.urandom(32))
        self.addCleanup(os.environ.pop, peer_service.TEST_CERT_ENV, None)
        cfg_home = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(pathlib.Path(self.tmp.name) / "xdg-config")})
        cfg_home.start()
        self.addCleanup(cfg_home.stop)                  # a fresh config home: the working root is set offline (test_p551)
        self.st = _state(self.tmp.name)
        work = pathlib.Path(self.tmp.name) / "work"
        work.mkdir()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(work)
        self.st.set_agent_config("claude", str(work))
        self.sent = []
        self.host = _owner_host(self.st, self.sent)
        self.host.ask_ttl = 5.0
        self.phone = Phone(self.st, name="phone-0306", with_passkey=True)
        self.s = _ready(self.host, self.phone)
        self.submitted = []
        self.host.agent.submit = self.submitted.append
        self.host._setup_round_trip = mock.AsyncMock()
        self.svc = PeerService(self.host, sessions=FakeSessions(), net_factory=FakeNet, clock=Clock())
        self.svc.HOUSEKEEP = 3600
        await self.svc.start()
        self.addAsyncCleanup(self.svc.stop)
        self.host.peers = self.svc
        # mail: a local fake SMTP, slot 0 = OWNER, BOB an ordinary contact
        self.smtp = FakeSMTP(AGENT, CODE)
        self.addCleanup(self.smtp.close)
        cfg = M.blank_config()
        cfg.update(address=AGENT, username=AGENT, imap={"host": "127.0.0.1", "port": 1, "security": "plain"},
                   smtp={"host": "127.0.0.1", "port": self.smtp.port, "security": "plain"})
        M.save_config(self.st, M.validate(cfg))
        M.store_secret(self.st, bytearray(CODE.encode()), AGENT)
        self.assertTrue(C.local(self.st, {"op": "owner_set", "owner_ok": True,
                                          "contact": {"name": "Owner", "emails": [OWNER]}})["ok"])
        self.mail = ContactService(type("E", (), {"host": self.host, "st": self.st})())
        await self.mail.agent({"op": "add", "contact": {"name": "Bob Builder", "emails": [BOB]}})
        self.answer = "uv"

    # ------------------------------------------------------------------ the owner's click → the handed turn
    async def click(self, name="Father TB"):
        k = peer_keys()
        self.fid = k.id
        self.svc.store.add_friend(k.id, wire.b64u(k.x_pub), wire.b64u(k.ed_pub), k.mbox,
                                  card=peer.sign_card(k, {"name": name, "owner": "", "intro": "", "lang": "zh",
                                                          "caps": ["chat"]}), group="default")
        self.svc._question(k.id, pmsg("能把报价单发给 bob 吗？"), "草稿", "超出自动回复范围，由你决定")
        a = next(x for x in self.svc.ask_msgs() if x["pq"]["friend"] == k.id)
        with mock.patch("agentj.report_actions.phone_source", return_value=None):
            await self.host._answer(self.s, sign_answer(self.st, self.phone.did, self.phone.sk, a, True, group=HAND_MAIN))
            for _ in range(5):
                await asyncio.sleep(0.01)
        send = self.submitted[-1]
        self.assertEqual(send.source, {"kind": "friend_task", "role": "task"})
        return send

    def run_turn(self, send):
        self.host.agent.cur_send = send
        self.host.cur_turn = send.turn

    def cards(self):
        return [o for _c, o in self.sent if o.get("t") == "ask"]

    async def mail_send(self, **req):
        """agent_send with the real Host.ask; the paired passkey phone answers any card per self.answer."""
        req.setdefault("subject", "报价单")
        req.setdefault("body", "这是报价单。")
        start = len(self.cards())
        task = asyncio.create_task(self.mail.agent_send(req))
        for _ in range(600):
            await asyncio.sleep(0.005)
            if len(self.cards()) > start or task.done():
                break
        card = None
        if len(self.cards()) > start:
            card = self.cards()[-1]
            await asyncio.sleep(0.02)
            if self.answer == "nouv":
                auth = self.phone.__dict__.pop("auth")
                try:
                    obj = self.phone.answer(card, True)
                finally:
                    self.phone.auth = auth
            else:
                obj = self.phone.answer(card, self.answer == "uv")
            await self.host._app(self.s, obj)
        return await asyncio.wait_for(task, 15), card

    def envelope(self):
        return [a for _f, to, _raw in self.smtp.sent for a in to]

    # ------------------------------------------------------------------ cases
    async def test_handoff_turn_third_party_mail_cards_then_sends_on_uv_allow(self):
        send = await self.click()
        self.assertTrue(getattr(send, "friend_handoff", None))
        self.run_turn(send)
        res, card = await self.mail_send(to=[BOB])
        self.assertIsNotNone(card, res)
        self.assertTrue(card.get("passkey_required"), card)                 # UV card
        self.assertIn(SRC_ZH, card["summary"])
        self.assertNotIn(self.fid, json.dumps(card, ensure_ascii=False))     # display name, never the AJ-… id
        # the fields the page paints on the FRONT (approval541 / approval-layout0281 / approval-source572), host-set only
        e = card["envelope"]
        self.assertEqual((e["reason"], e["reason_en"]), (SRC_ZH, SRC_EN))
        src = e["source"]
        self.assertEqual((src["verified"], src["suspicious"], src["channel"], src["text"], src["message_id"]),
                         (True, True, "phone", "交给主 Agent 来办：好友「Father TB」的消息", send.turn), src)
        # security r2 (0306): the friend's words stay quoted external material; the root is the owner's verified tap
        self.assertEqual((src["root"]["verified"], src["root"]["text"], src["root"]["message_id"]),
                         (True, "交给主 Agent 来办", send.turn), src)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.envelope(), [BOB])
        rec = approvals.read_log(self.st)[-1]
        self.assertEqual((rec["tool"], rec["decision"], rec["reason"], rec["device"]),
                         ("agentj mail send", "allow", "device", self.phone.did))

    @unittest.skipUnless(shutil.which("node") and FRONT_JS.is_file(), "node / web renderer not available")
    async def test_handoff_card_front_as_the_page_paints_it(self):
        self.run_turn(await self.click())
        _res, card = await self.mail_send(to=[BOB])
        zh, en = page_front(card)
        self.assertEqual((zh["warn"], zh["extra"], zh["text"], zh["reason"]),
                         ("含引用的外部材料 · 发起人是你，引用内容来自外部", "", "交给主 Agent 来办：好友「Father TB」的消息", SRC_ZH), zh)
        self.assertNotIn(zh["dict"]["outsider"], zh["sender"])
        self.assertTrue(zh["sender"].startswith("手机 · 你 · "), zh)
        self.assertEqual((en["extra"], en["reason"]), ("", SRC_EN), en)
        self.assertTrue(en["warn"], en)                                       # quoted-material line, not hidden
        self.assertTrue(en["sender"].startswith("Phone · You · "), en)
        for front in (zh, en):
            for word in ("suspicious", "unknown", "missing"):                    # quoted wording, not 「找不到是谁发起的」
                self.assertNotIn(front["dict"][word], json.dumps(front["warn"] + front["extra"] + front["reason"] + front["text"],
                                                                  ensure_ascii=False))
        self.assertFalse(zh["allowDisabled"])

    async def test_other_cards_in_the_handed_turn_keep_the_unverified_source(self):
        """Only the handoff mail card gets the host-attested source: any other card of the turn is unchanged."""
        send = await self.click()
        self.run_turn(send)
        from agentj import approval_review
        src = approval_review.snapshot(self.host)
        self.assertEqual((src["verified"], src["suspicious"]), (False, True))

    async def test_handoff_turn_deny_sends_nothing(self):
        send = await self.click()
        self.run_turn(send)
        self.answer = "deny"
        res, card = await self.mail_send(to=[BOB])
        self.assertIsNotNone(card)
        self.assertEqual((res["ok"], res["why"]), (False, "denied"), res)
        self.assertEqual(self.smtp.sent, [])

    async def test_handoff_turn_allow_without_passkey_sends_nothing(self):
        send = await self.click()
        self.run_turn(send)
        self.answer = "nouv"
        res, card = await self.mail_send(to=[BOB])
        self.assertIsNotNone(card)
        self.assertFalse(res["ok"], res)
        self.assertEqual(self.smtp.sent, [])

    async def test_handoff_turn_owner_only_passes_without_a_card(self):
        send = await self.click()
        self.run_turn(send)
        res, card = await self.mail_send(to=[OWNER])
        self.assertIsNone(card)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["owner_direct"], res["no_card"]), (True, True))
        self.assertEqual(self.envelope(), [OWNER])

    async def test_friend_task_turn_without_handoff_is_refused_without_a_card(self):
        """An ordinary friend_task turn (owner-approved forward, same_owner.ForwardSend approved=True) — as today."""
        page = self.host.hist_add({"k": "friend", "name": "x", "text": "x"}, "", "open")
        send = compose.Send("peer", "f" * 22, page["id"], text="x", source={"kind": "friend_task", "role": "task"})
        self.run_turn(send)
        for to in ([BOB], [OWNER]):
            with self.subTest(to=to):
                res, card = await self.mail_send(to=to, friend_handoff="Father TB")     # a request field is ignored
                self.assertIsNone(card)
                self.assertFalse(res["ok"], res)
        self.assertEqual(self.smtp.sent, [])
        self.assertEqual(self.cards(), [])

    async def test_replayed_stale_or_forged_handoff_is_refused_without_a_card(self):
        send = await self.click()
        nonce = send.friend_handoff
        later = compose.Send("friend-main", "r" * 22, send.turn, text=send.text, source=dict(send.source))
        later.friend_handoff = nonce                                            # replayed onto another Send
        forged = compose.Send("friend-main", "g" * 22, send.turn, text=send.text, source=dict(send.source))
        forged.friend_handoff = "0" * 12                                        # never minted
        cases = [("replayed", later), ("forged", forged)]
        for label, s in cases:
            with self.subTest(label):
                self.run_turn(s)
                res, card = await self.mail_send(to=[BOB])
                self.assertIsNone(card)
                self.assertEqual(res["why"], "friend_handoff_unverified", res)
        self.run_turn(send)                                                     # stale: past its TTL
        self.host._friend_handoffs[nonce]["until"] = time.monotonic() - 1
        res, card = await self.mail_send(to=[OWNER])
        self.assertIsNone(card)
        self.assertEqual(res["why"], "friend_handoff_unverified", res)
        self.host._friend_handoffs[nonce]["until"] = time.monotonic() + 60     # friend blocked meanwhile
        self.svc.store.block(self.fid)
        res, card = await self.mail_send(to=[BOB])
        self.assertIsNone(card)
        self.assertEqual(res["why"], "friend_handoff_unverified", res)
        self.assertEqual(self.smtp.sent, [])
        self.assertEqual(self.cards(), [])

    async def test_marker_only_ever_adds_uv(self):
        self.assertFalse(uv_list.required("agentj mail send", {"recipients": [BOB]}))
        self.assertTrue(uv_list.required("agentj mail send", {"recipients": [BOB], "friend_handoff": "x"}))
        self.assertNotIn("agentj mail send", uv_list.COMMANDS)                  # the published UV list is unchanged
        self.assertIsNone(friend_handoff.mint(self.host, compose.Send("x", "y" * 22, 1, text="no fence"), "no fence"))


if __name__ == "__main__":
    unittest.main()
