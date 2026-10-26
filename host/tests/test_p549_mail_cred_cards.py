"""P549: channel credentials survive an upgrade (#313), one save folds every card for it (#314), the remote's own forward
tap needs no card / passkey and never looks at the Agent's turn (#316, #319). Synthetic addresses and values only."""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP
import asyncio
import json
import secrets
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from agentj import diskq, mail as M, provenance, service, telegram, wechat_store
from agentj.state import State

ADDR = "owner.p549@example.com"
VALUE = b"synthetic-app-value"


def _mail_key(st):
    """The on-disk key as a released host derived it — written out here so a format change cannot pass silently."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"agentj-mail", info=b"agentj-mail-secret-v1").derive(
        st.x25519_path.read_bytes())


def _write_ajm1(st, address, value):
    """≤ 0.25.6 (before P503d): AAD = the address only."""
    iv = secrets.token_bytes(12)
    blob = b"AJM1" + iv + AESGCM(_mail_key(st)).encrypt(iv, value, b"agentj-mail-v1\n" + address.lower().encode())
    (st.root / M.DIR).mkdir(exist_ok=True)
    (st.root / M.DIR / M.SECRET).write_bytes(blob)


def _write_ajm2(st, cfg, value):
    """≥ 0.25.8 (P503d): AAD = address + the destination it may reach (login, IMAP/SMTP host, port, security)."""
    dest = [cfg["address"].lower(), cfg.get("username") or cfg["address"],
            *(cfg[k][f] for k in ("imap", "smtp") for f in ("host", "port", "security"))]
    aad = b"agentj-mail-v2\n" + cfg["address"].lower().encode() + b"\n" + json.dumps(dest, separators=(",", ":")).encode()
    iv = secrets.token_bytes(12)
    (st.root / M.DIR).mkdir(exist_ok=True)
    (st.root / M.DIR / M.SECRET).write_bytes(b"AJM2" + iv + AESGCM(_mail_key(st)).encrypt(iv, value, aad))


def _events(st, ev):
    diskq.flush()
    try:
        lines = st.log_path.read_text().splitlines()
    except FileNotFoundError:
        return []
    return [r for r in map(json.loads, lines) if r.get("ev") == ev]


class _St(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p549-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / "state"); self.st.init()
        self.notes = []
        self.host = SimpleNamespace(st=self.st, lang="zh", stopping=asyncio.Event(),
                                    hist_add=lambda a, text, end: self.notes.append(text))
        self.mail = M.Mail(self.host)

    def gmail(self, **kw):
        cfg = M.apply_preset(M.blank_config(), "gmail", ADDR)
        cfg.update(enabled=True, **kw)
        return M.save_config(self.st, M.validate(cfg))


class MailUpgrade(_St):
    """#313: 10-23 saved under 0.25.3 (AJM1); 10-26's auto-update crossed P503d (AJM2) and the password read as missing,
    with no event. Now a fixed-provider envelope moves over by itself, any other loss is one event + one owner line."""

    def test_released_envelopes_still_open(self):
        cfg = self.gmail()
        _write_ajm2(self.st, cfg, VALUE)                        # what 0.25.8 … 0.25.12 wrote
        self.assertEqual(M.load_secret(self.st, ADDR), VALUE.decode())
        self.assertEqual(self.mail.view()["password_state"], "ok")

    def test_fixed_provider_legacy_envelope_migrates_on_upgrade(self):
        cfg = self.gmail()
        _write_ajm1(self.st, ADDR, VALUE)
        self.assertIsNone(M.load_secret(self.st, ADDR))
        self.assertEqual(M.secret_status(self.st, ADDR), "legacy")
        self.assertEqual(M.migrate_legacy_secret(self.st), "migrated")
        self.assertEqual((self.st.root / M.DIR / M.SECRET).read_bytes()[:4], b"AJM2")
        self.assertEqual(M.load_secret(self.st, ADDR), VALUE.decode())
        self.assertEqual(M.migrate_legacy_secret(self.st), "", "idempotent")
        self.assertEqual(M.credential_destination(M.read_config(self.st)), M.credential_destination(cfg))

    async def test_first_check_after_upgrade_uses_the_migrated_password(self):
        self.gmail()
        _write_ajm1(self.st, ADDR, VALUE)
        with patch.object(M.mail_recovery, "recover") as rec:
            pw = self.mail.password(M.read_config(self.st)) or await self.mail._lost(M.read_config(self.st))
        self.assertEqual(pw, VALUE.decode())
        rec.assert_not_called()
        self.assertEqual([e["result"] for e in _events(self.st, "mail_password")], ["migrated"])
        self.assertEqual(self.notes, [], "nothing lost, nothing to tell")

    async def test_custom_server_legacy_is_kept_and_reported_once(self):
        cfg = M.blank_config()
        cfg.update(address="owner@work.example", enabled=True, preset="custom",
                   imap={"host": "imap.work.example", "port": 993, "security": "ssl"},
                   smtp={"host": "smtp.work.example", "port": 465, "security": "ssl"})
        cfg = M.save_config(self.st, M.validate(cfg))
        _write_ajm1(self.st, cfg["address"], VALUE)
        blob = (self.st.root / M.DIR / M.SECRET).read_bytes()
        self.assertEqual(M.migrate_legacy_secret(self.st), "kept")          # P503d: never opened for a free-form server
        self.assertEqual((self.st.root / M.DIR / M.SECRET).read_bytes(), blob, "the old file stays")
        with patch.object(M.mail_recovery, "recover", return_value=None) as rec:
            self.assertIsNone(await self.mail._lost(cfg))
            self.assertIsNone(await self.mail._lost(cfg))
        self.assertEqual(rec.call_count, 1, "one recovery try per loss, not every poll")
        ev = _events(self.st, "mail_password")
        self.assertEqual([(e["result"], e.get("why")) for e in ev], [("missing", "legacy")])
        self.assertEqual(len(self.notes), 1)
        self.assertIn("设置 → 邮件通道", self.notes[0])
        self.assertNotIn(VALUE.decode(), self.notes[0] + json.dumps(ev))

    async def test_unreadable_password_says_why_and_recovers_when_possible(self):
        cfg = self.gmail()
        _write_ajm2(self.st, dict(cfg, username="someone.else@example.com"), VALUE)   # bound to another login
        self.assertEqual(M.secret_status(self.st, ADDR), "unreadable")
        self.assertEqual(self.mail.view()["password_state"], "unreadable")
        with patch.object(M.mail_recovery, "recover", return_value="registered-value") as rec:
            self.assertEqual(await self.mail._lost(cfg), "registered-value")
        rec.assert_called_once()
        self.assertEqual([(e["result"], e.get("why")) for e in _events(self.st, "mail_password")], [("missing", "unreadable")])
        self.assertEqual(self.notes, [], "recovered: nothing to ask")

    def test_destination_change_forgets_with_an_event(self):
        cfg = self.gmail()
        M.store_secret(self.st, bytearray(VALUE), ADDR)
        self.mail.set_config(dict(cfg, address="new.owner@example.com", username="new.owner@example.com"))
        self.assertFalse((self.st.root / M.DIR / M.SECRET).exists())
        self.assertEqual([(e["result"], e.get("why")) for e in _events(self.st, "mail_password")],
                         [("forgotten", "destination_changed")])
        self.mail.set_config(dict(M.read_config(self.st), poll_minutes=10))
        self.assertEqual(len(_events(self.st, "mail_password")), 1, "no secret, no second event")


class OtherChannelsSurvive(_St):
    """#313 regression, the other two owner channels: their stored credentials read back under this version."""

    def test_wechat_state_written_by_a_released_host_still_reads(self):
        data = {"account": {"token_ref": "synthetic"}, "cursor": "c1", "context": "", "done": [], "records": {},
                "sessions": {}, "counts": {}, "generation": "g" * 32}
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"agentj-wechat-v1",
                   info=b"local-channel-state").derive(self.st.x25519_path.read_bytes())
        iv = secrets.token_bytes(12)
        (self.st.root / "wechat.bin").write_bytes(iv + AESGCM(key).encrypt(iv, json.dumps(data).encode(), b"agentj-wechat-v1"))
        self.assertEqual(wechat_store.Store(self.st).data["account"], data["account"])

    def test_telegram_enrollment_and_key_name_survive_a_unit_rewrite(self):
        self._cfg_telegram()
        self.assertEqual(telegram.enrollment(self.st)["owner_id"], 4242)
        with patch.dict("os.environ", {"AGENTJ_TELEGRAM_BOT_TOKEN": "synthetic"}):
            self.assertIsNotNone(telegram.configuration(self.st))
        unit = service.unit_text("agentj", ["agentj"], {}, {"HOME": self.tmp.name, "XDG_CONFIG_HOME": self.tmp.name + "/cfg"})
        self.assertIn("EnvironmentFile=-", unit, "the key lives in the env file the new unit still reads")
        self.assertNotIn("synthetic", unit)

    def _cfg_telegram(self):
        p = self.st.config_path
        d = json.loads(p.read_text())
        d["telegram"] = {"owner_id": 4242, "key_env": "AGENTJ_TELEGRAM_BOT_TOKEN"}
        p.write_text(json.dumps(d))


class CardsFold(unittest.IsolatedAsyncioTestCase):
    """#314: the Agent's key card is open on the Mac, the owner saves the same key from the phone's Settings."""

    async def asyncSetUp(self):
        from test_l1 import Phone, _owner_host, _ready
        self.tmp = tempfile.TemporaryDirectory(prefix="p549c-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / "s"); self.st.init()
        self.sent = []
        self.h = _owner_host(self.st, self.sent)
        self.h.mail = M.Mail(self.h)
        self.h.mail.set_config(M.apply_preset(M.blank_config(), "gmail", ADDR))
        self.mac, self.phone = Phone(self.st, "Mac"), Phone(self.st, "手机")
        self.s_mac, self.s_phone = _ready(self.h, self.mac, 11), _ready(self.h, self.phone, 12)

    def done_for(self, rid):
        return {cid: o for cid, o in self.sent if o.get("t") == "elev_done" and o.get("id") == rid}

    async def open_card(self, only=None):
        before = set(self.h.elevate.cards)
        task = asyncio.create_task(self.h.mail.secret_card(recover=False, only=only))
        for _ in range(200):
            new = set(self.h.elevate.cards) - before
            if new:
                return task, self.h.elevate.cards[new.pop()]
            await asyncio.sleep(.005)
        self.fail("no card")

    async def test_saved_on_the_phone_folds_the_agents_card_everywhere(self):
        agent_task, agent_card = await self.open_card()                     # the Agent's request: every remote
        phone_task, phone_card = await self.open_card(self.phone.did)       # Settings → 设置邮箱授权码, this phone only
        with patch.object(M, "test_connection", return_value=dict(ok=True, imap="ok", smtp="ok", auth_sample=None)):
            res = await asyncio.to_thread(phone_card["sink"], bytearray(b"abcd efgh ijkl mnop"))
        self.h.elevate._finish(phone_card, res)
        self.assertEqual((await phone_task)["result"], "saved")
        agent_res = await asyncio.wait_for(agent_task, 5)
        self.assertEqual(agent_res["result"], "saved")
        done = self.done_for(agent_card["id"])
        self.assertEqual(set(done), {11, 12}, "every remote folds the Agent's card")
        self.assertTrue(all(o.get("elsewhere") == 1 and o.get("verify") == "ok" for o in done.values()))
        for s in (self.s_mac, self.s_phone):                                # a refresh rebuilds from the host: no card
            self.assertEqual(self.h.elevate.open_msgs(s), [])
        self.assertIn(agent_card["id"], {m["id"] for m in self.h.elevate.completed_msgs()})
        self.assertEqual(M.load_secret(self.st, ADDR), "abcdefghijklmnop")

    async def test_one_remote_answers_every_remote_hears(self):
        for result in ("denied", "timeout"):
            task, card = await self.open_card()
            self.h.elevate._finish(card, {"result": result})
            self.assertEqual((await task)["result"], result)
            self.assertEqual(set(self.done_for(card["id"])), {11, 12})
            self.assertEqual(self.h.elevate.open_msgs(self.s_mac), [])

    async def test_a_refused_or_failed_save_does_not_fold_other_cards(self):
        agent_task, agent_card = await self.open_card()
        phone_task, phone_card = await self.open_card(self.phone.did)
        with patch.object(M, "test_connection", return_value=dict(ok=False, imap="imap_login", smtp="ok", auth_sample=None)):
            res = await asyncio.to_thread(phone_card["sink"], bytearray(b"wrong value"))
        self.h.elevate._finish(phone_card, res)
        await phone_task
        await asyncio.sleep(.05)
        self.assertFalse(agent_task.done(), "a key that failed its login check leaves the Agent's card open")
        self.h.elevate._finish(agent_card, {"result": "denied"})
        await agent_task


class RemoteForward(unittest.IsolatedAsyncioTestCase):
    """#316 / #319: the owner's own tap on a paired remote sends — to self or to others, no passkey on that remote, and
    whatever the main session's last turn was (a system notice, a peer message)."""

    async def asyncSetUp(self):
        from test_p184_contacts import _Svc
        self.fx = _Svc()
        await self.fx.asyncSetUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.mailbox()

    async def test_tap_sends_without_a_card_whatever_the_agents_turn(self):
        from test_l1 import Phone, _host, _ready
        from test_p184_contacts import OWNER
        fx = self.fx
        fx.st.sign_key = State.sign_key.__get__(fx.st, State)
        sent = []
        h = _host(fx.st, sent)
        h.media, h.hist.get = fx.media, fx.host.hist.get
        h.mail = M.Mail(h)
        ph = Phone(fx.st, "Mac Chrome")                                     # no passkey on this remote
        s = _ready(h, ph)
        h.ask = AsyncMock(side_effect=AssertionError("a remote tap never asks"))
        # a 〔Agent J 系统提示〕 turn, a peer session's message, a malformed / unknown source
        for turn in (provenance.system(), {"kind": "same_owner", "role": "task"}, None):
            for to in ([OWNER], ["friend.p549@example.com"]):
                with self.subTest(turn=(turn or {}).get("kind"), to=to):
                    h.agent = SimpleNamespace(kind="claude", native_source=turn, cur_send=None)
                    self.assertFalse(provenance.is_owner(provenance.turn_source(h.agent)))
                    before = len(fx.smtp.sent)
                    sent.clear()
                    await h.elevate.on_phone(s, {"t": "mail_fwd", "r": "r" * 16, "to": to, "turn": 7, "again": True})
                    await asyncio.gather(*list(h.elevate.contacts.bg))
                    res = [o for _, o in sent if o.get("t") == "mail_fwd_res"][-1]
                    self.assertTrue(res["ok"], res)
                    self.assertEqual(len(fx.smtp.sent), before + 1)
                    self.assertEqual(fx.smtp.sent[-1][1], to)
        h.ask.assert_not_awaited()
        self.assertFalse([e for e in _events(fx.st, "ask_done") if e.get("reason") == "group_source"])

    async def test_dedupe_still_holds_and_no_card(self):
        from test_p184_contacts import OWNER
        fx = self.fx
        first = await fx.svc.forward(fx.s, {"to": [OWNER], "turn": 7})
        self.assertTrue(first["ok"], first)
        again = await fx.svc.forward(fx.s, {"to": [OWNER], "turn": 7})
        self.assertEqual(again["why"], "dup")
        fx.ask.assert_not_awaited()
        other = await fx.svc.forward(fx.s, {"to": ["friend.p549@example.com"], "turn": 7})
        self.assertTrue(other["ok"], other)
        fx.ask.assert_not_awaited()

    async def test_group_member_turn_still_cannot_make_the_agent_send(self):
        from test_p184_contacts import OWNER
        fx = self.fx
        fx.host.agent = SimpleNamespace(kind="claude", native_source=provenance.telegram_group(-100, 7, None, 1), cur_send=None)
        res = await fx.svc.agent_send({"to": [OWNER], "subject": "x", "body": "y"})
        self.assertEqual(res["why"], "group_source")
        fx.ask.assert_not_awaited()

    async def test_agent_mail_to_others_still_asks_with_a_card(self):
        # P550: P551 (#318) made the Agent's mail ② — a long-press card for everyone, never silent, no Face ID
        fx = self.fx
        res = await fx.svc.agent_send({"to": ["friend.p549@example.com"], "subject": "x", "body": "y"})
        self.assertTrue(res["ok"], res)
        fx.ask.assert_awaited_once()
        self.assertFalse(fx.ask.await_args.kwargs.get("require_passkey", False))


if __name__ == "__main__":
    unittest.main()
