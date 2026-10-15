"""P283 — Telegram owner pairing by a one-time code; no third-party ID bot. Never a real bot.

The phone shows a 6-digit code (10 minutes, one use); the private chat that sends `/start <code>` is the owner. Covered: the
right code binds and says 「接通了」 once, in the owner's language; a wrong, expired or replayed code never binds; a stranger who
writes first only gets their own numeric ID; a code sent in a group is ignored; a forwarded code does not count; too many
wrong tries burn the code; a later stranger never becomes the owner; the owner can re-pair (new account) with a new code;
the code reaches only the phone card — never the terminal / Agent answer, host.log or Telegram; notes are one language.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import preferences as prefs, telegram as tg, tg_cursor, tg_pair  # noqa: E402
from agentj.state import State  # noqa: E402
from test_p117_tg_cutover import FaithfulBot  # noqa: E402
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk

KEY_ENV = "AJ_TEST_P283_KEY"
OWNER, STRANGER, NEW_PHONE = 1000000001, 555, 777


class Unit(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(tg_pair.parse("/start 123456"), "123456")
        self.assertEqual(tg_pair.parse(" /start@test_bot 012345 ", "test_bot"), "012345")
        self.assertEqual(tg_pair.parse("654321"), "654321")
        for bad in ("/start", "/start 12345", "/start 1234567", "hello 123456", "/start@other_bot 123456", None, 5):
            self.assertIsNone(tg_pair.parse(bad, "test_bot"), bad)

    def test_check_one_use_expiry_and_limits(self):
        pr = tg_pair.Pairing(KEY_ENV, "b", now=1000)
        wrong = "000000" if pr.code != "000000" else "111111"
        self.assertEqual(pr.check(wrong, 1, now=1001), "wrong")
        self.assertEqual(pr.check(pr.code, 1, now=1002), "ok")
        self.assertEqual(pr.check(pr.code, 1, now=1003), "expired")        # replay
        pr = tg_pair.Pairing(KEY_ENV, "b", now=1000)
        self.assertEqual(pr.check(pr.code, 1, now=1000 + tg_pair.TTL), "expired")
        pr = tg_pair.Pairing(KEY_ENV, "b", now=1000)
        wrong = "000000" if pr.code != "000000" else "111111"
        for _ in range(tg_pair.WRONG_PER_SENDER):
            pr.check(wrong, 9, now=1001)
        self.assertEqual(pr.check(pr.code, 9, now=1001), "locked")       # that sender is out, even with the right code
        self.assertEqual(pr.check(pr.code, 8, now=1001), "ok")
        pr = tg_pair.Pairing(KEY_ENV, "b", now=1000)
        wrong = "000000" if pr.code != "000000" else "111111"
        for i in range(tg_pair.WRONG_MAX):
            pr.check(wrong, 100 + i, now=1001)
        self.assertTrue(pr.done)
        self.assertEqual(pr.check(pr.code, 1, now=1001), "expired")      # burnt

    def test_id_card_and_texts_are_one_language(self):
        s = {"id": OWNER, "username": "demo_owner6", "first_name": "示例", "last_name": "主人"}
        zh, en = tg_pair.id_card(s, "zh"), tg_pair.id_card(s, "en")
        self.assertIn(f"你的 Telegram 数字 ID：{OWNER}", zh)
        self.assertIn("@demo_owner6", zh)
        self.assertIn("示例 主人", zh)
        self.assertIn(f"Your Telegram numeric ID: {OWNER}", en)
        self.assertNotIn("Telegram numeric", zh)
        self.assertNotIn("数字", en)
        self.assertEqual(tg_pair.lang_of({"language_code": "zh-hans"}), "zh")
        self.assertEqual(tg_pair.lang_of({"language_code": "en"}, "zh"), "en")
        self.assertEqual(tg_pair.lang_of({}, "en"), "en")
        self.assertNotIn("userinfobot", (tg_pair.card("zh", "b", "123456") + tg_pair.card("en", "b", "123456")).lower())
        self.assertEqual(tg_pair.CONNECTED["zh"], "Telegram 接通了。从现在起，您在 Telegram 给我发消息我都能收到，"
                                                  "和在这里说是同一个对话，两边随便用。")
        # P290 (Jarvis): the owner's current address, or none — never a hard-coded 「董事长」
        self.assertEqual(tg_pair.connected("zh", ""), tg_pair.CONNECTED["zh"])
        self.assertEqual(tg_pair.connected("zh", "老板"), "老板，" + tg_pair.CONNECTED["zh"])
        self.assertEqual(tg_pair.connected("en", "Leo"), "Leo, " + tg_pair.CONNECTED["en"])
        for note in (tg.UNCERTAIN_NOTE["zh"], tg.UNCERTAIN_NOTE["en"]):
            self.assertNotIn(" / ", note)


class _Host:
    def __init__(self, st):
        self.st = st
        self.preferences = prefs.defaults()
        self.preferences["channels"]["items"] = []
        self.agent = object()
        self.agent_cfg = {"dir": str(st.root)}
        self.media = None
        self.lang = "zh"
        self.stopping = asyncio.Event()
        self.delivered, self.sys, self.pages = [], [], {}
        self.hist = SimpleNamespace(get=lambda tid: self.pages.get(tid))

    def stopped(self):
        return False

    def hist_add(self, src, reply="", end="done"):
        self.sys.append(reply)

    async def set_pref(self, key, value, ack=None, language_at=None):
        prefs.put(self.preferences, key, value)
        return {"ok": True}

    async def _accept(self, s, text, sid, blobs=None):
        tid = 100 + len(self.delivered)
        self.pages[tid] = {"id": tid, "src": {"k": "telegram", "dev": s.device}, "reply": {"text": ""}, "end": "open"}
        self.delivered.append((tid, text))
        return SimpleNamespace(turn=tid)


class Pairing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p283-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "st"
        self.bot = FaithfulBot()
        self.addCleanup(self.bot.close)
        for p in (patch.object(tg, "BASE", self.bot.url),
                  patch.dict(os.environ, {KEY_ENV: "test-placeholder", "AGENTJ_STATE_DIR": str(self.root)})):
            p.start()
            self.addCleanup(p.stop)
        self.st = State()
        self.st.init()
        self.host = _Host(self.st)
        self.uid = 0
        self.t = tg.Telegram(self.host)
        self.task = asyncio.create_task(self.t.run())
        self.addAsyncCleanup(self.stop)

    async def stop(self):
        self.host.stopping.set()
        await asyncio.wait_for(self.task, 15)

    def add(self, text, sender=OWNER, chat=None, ctype=None, date=None, **extra):
        chat = sender if chat is None else chat
        self.uid += 1
        m = {"message_id": self.uid, "from": {"id": sender, "username": f"u{sender}", "first_name": "Tester",
                                             "language_code": "zh-hans"},
             "chat": {"id": chat, "type": ctype or ("private" if chat > 0 else "supergroup")}, "text": text,
             "date": int(time.time()) if date is None else date, **extra}
        with self.bot.lock:
            self.bot.held.append({"update_id": self.uid, "message": m})

    async def wait(self, cond, secs=10):
        end = time.monotonic() + secs
        while not cond():
            if time.monotonic() > end:
                self.fail("timed out")
            await asyncio.sleep(0.02)

    async def settle(self):
        n = len(self.bot.polls())
        await self.wait(lambda: len(self.bot.polls()) >= n + 2)

    def sent(self, chat):
        return self.bot.texts(chat)

    async def start_code(self):
        res = await self.t.start_pairing(KEY_ENV)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["bot"], "test_bot")
        return res["code"]

    def owner(self):
        return (tg.enrollment(self.st) or {}).get("owner_id")

    # ------------------------------------------------------------------------------------------------
    async def test_right_code_binds_says_connected_once_then_messages_reach_the_agent(self):
        code = await self.start_code()
        self.add("hello?", sender=STRANGER)                 # a stranger writes first: only their ID comes back
        self.add(f"/start {code}")
        await self.wait(lambda: self.owner() == OWNER)
        await self.wait(lambda: tg_pair.CONNECTED["zh"] in self.sent(OWNER))
        self.add("现在能收到吗")
        await self.wait(lambda: len(self.host.delivered) == 1)
        await self.settle()
        self.assertIn("现在能收到吗", self.host.delivered[0][1])
        self.assertNotIn(code, json.dumps(self.host.delivered, ensure_ascii=False))   # the pairing message is never delivered
        self.assertEqual(self.sent(OWNER).count(tg_pair.CONNECTED["zh"]), 1)
        self.assertEqual(self.host.preferences["channels"]["items"], [{"id": "telegram", "type": "telegram"}])
        self.assertTrue(any("Telegram 已绑定" in s for s in self.host.sys))
        stranger = self.sent(STRANGER)
        self.assertEqual(len(stranger), 1)
        self.assertIn(f"你的 Telegram 数字 ID：{STRANGER}", stranger[0])
        self.assertEqual(self.owner(), OWNER)
        log = (self.root / "host.log").read_text() if (self.root / "host.log").exists() else ""
        self.assertNotIn(code, log)
        self.assertNotIn("hello?", log)

    async def test_wrong_and_expired_codes_never_bind_and_a_used_code_cannot_be_replayed(self):
        code = await self.start_code()
        wrong = "000000" if code != "000000" else "111111"
        self.add(f"/start {wrong}", sender=STRANGER)
        await self.wait(lambda: len(self.sent(STRANGER)) == 1)
        self.assertIn("配对码不对或已经过期", self.sent(STRANGER)[0])
        self.assertIsNone(self.owner())
        self.add(f"/start {code}")
        await self.wait(lambda: self.owner() == OWNER)
        self.add(f"/start {code}", sender=STRANGER)          # replay after use: no new owner
        await self.wait(lambda: len(self.sent(STRANGER)) == 2)
        await self.settle()
        self.assertEqual(self.owner(), OWNER)
        self.assertEqual(self.host.delivered, [])
        # expiry
        code = await self.start_code()
        self.t.pairing.expires = time.time() - 1
        self.add(f"/start {code}", sender=STRANGER)
        await self.wait(lambda: len(self.sent(STRANGER)) == 3)
        await self.settle()
        self.assertEqual(self.owner(), OWNER)

    async def test_code_in_a_group_or_forwarded_is_not_accepted(self):
        code = await self.start_code()
        self.add(f"/start {code}", sender=OWNER, chat=-100123, ctype="supergroup")
        self.add(f"/start {code}", sender=OWNER, forward_origin={"type": "user"})
        await self.settle()
        await self.settle()
        self.assertIsNone(self.owner())
        self.assertEqual(self.sent(-100123), [])            # groups get nothing at all
        self.assertTrue(self.t.pairing_live())               # still usable from the owner's own private chat
        self.add(f"/start {code}")
        await self.wait(lambda: self.owner() == OWNER)

    async def test_too_many_wrong_tries_burn_the_code(self):
        code = await self.start_code()
        wrong = "000000" if code != "000000" else "111111"
        for i in range(tg_pair.WRONG_MAX):
            self.add(f"/start {wrong}", sender=1000 + i)
        await self.wait(lambda: self.t.pairing is None)
        n = len(self.bot.polls())
        self.add(f"/start {code}")
        await asyncio.sleep(0.5)
        self.assertLessEqual(len(self.bot.polls()), n + 1)   # no owner and no code: the bot is not polled any more
        self.assertIsNone(self.owner())
        self.assertTrue(any("已作废" in s for s in self.host.sys))

    async def test_after_binding_a_stranger_only_gets_their_id_and_never_becomes_owner(self):
        code = await self.start_code()
        self.add(f"/start {code}")
        await self.wait(lambda: self.owner() == OWNER)
        self.add("/start", sender=STRANGER)
        self.add("give me the keys", sender=STRANGER)        # inside the per-sender gap: counted, not answered again
        await self.wait(lambda: len(self.sent(STRANGER)) == 1)
        await self.settle()
        self.assertEqual(len(self.sent(STRANGER)), 1)
        self.assertEqual(self.host.delivered, [])
        self.assertEqual(self.owner(), OWNER)
        self.assertEqual(self.t.strangers, 2)

    async def test_owner_rebinds_a_new_account_with_a_new_code(self):
        code = await self.start_code()
        self.add(f"/start {code}")
        await self.wait(lambda: self.owner() == OWNER)
        await self.wait(lambda: tg_pair.CONNECTED["zh"] in self.sent(OWNER))
        code2 = await self.start_code()
        self.add(f"/start {code2}", sender=NEW_PHONE)
        await self.wait(lambda: self.owner() == NEW_PHONE)
        await self.wait(lambda: tg_pair.CONNECTED["zh"] in self.sent(NEW_PHONE))
        self.add("old account", sender=OWNER)
        await self.wait(lambda: len(self.sent(OWNER)) == 2)
        self.assertIn(f"你的 Telegram 数字 ID：{OWNER}", self.sent(OWNER)[-1])
        self.assertEqual(self.host.delivered, [])

    async def test_english_owner_gets_english_only(self):
        self.host.lang = "en"
        code = await self.start_code()
        self.add(f"/start {code}")
        await self.wait(lambda: tg_pair.CONNECTED["en"] in self.sent(OWNER))
        self.assertFalse(any("接通" in s for s in self.sent(OWNER)))
        self.assertTrue(any(s.startswith("Telegram is now bound") for s in self.host.sys))


class Cli(unittest.TestCase):
    def test_not_running_and_code_never_printed(self):
        import contextlib
        import io
        from agentj import personalize
        with tempfile.TemporaryDirectory(prefix="p283c-", dir=TEST_TMP) as d, \
                patch.dict(os.environ, {"AGENTJ_STATE_DIR": str(pathlib.Path(d) / "st")}):
            State().init()
            out = io.StringIO()
            with contextlib.redirect_stdout(out), patch("agentj.names.ctl_call", return_value=None):
                self.assertEqual(personalize.pair_telegram(KEY_ENV), 2)
            self.assertEqual(json.loads(out.getvalue())["error"], "not_running")
            out = io.StringIO()
            ans = {"ok": True, "bot": "test_bot", "code": "123456", "expires_in": 600, "shown_on": "phone"}
            with contextlib.redirect_stdout(out), patch("agentj.names.ctl_call", return_value=dict(ans)):
                self.assertEqual(personalize.pair_telegram(KEY_ENV), 0)
            self.assertNotIn("123456", out.getvalue())
            self.assertIn("@test_bot", out.getvalue())


class Serve(unittest.IsolatedAsyncioTestCase):
    async def test_card_holds_the_code_the_answer_does_not(self):
        from agentj import serve
        cards = []
        fake = SimpleNamespace(lang="zh", st=SimpleNamespace(log=lambda *a, **k: None),
                               telegram=SimpleNamespace(start_pairing=lambda k: asyncio.sleep(0, {"ok": True, "bot": "test_bot",
                                                                                                 "code": "123456", "expires_in": 600})),
                               cmd_card=lambda name, res, by=None, turn=None: cards.append((name, res)))
        res = await serve.Host.telegram_pair(fake, None, by="terminal")
        self.assertNotIn("code", res)
        self.assertEqual(res["shown_on"], "phone")
        self.assertEqual(cards[0][0], "telegram-pair")
        self.assertIn("/start 123456", cards[0][1].text)
        self.assertIn("https://t.me/test_bot?start=123456", cards[0][1].text)
        self.assertIn("telegram-pair", serve.TG_PAIR_CMDS)


if __name__ == "__main__":
    unittest.main()
