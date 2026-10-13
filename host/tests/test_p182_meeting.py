"""P182 `/会议总结` (en `/meeting-notes`): the owner's command becomes one instruction for the Agent; the minutes go by e-mail only
to addresses the owner typed (or the owner's own allowlisted address). Local fake SMTP only (tests/mailfake.py)."""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import email
import email.policy
import json
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import mail as M, meeting  # noqa: E402
from agentj.state import State  # noqa: E402
from mailfake import FakeSMTP  # noqa: E402

AGENT = "agent@example.cn"
CODE = "right-code"
BOSS = "boss@corp.example"


class Parse(unittest.TestCase):
    def test_command_words_addresses_and_self(self):
        self.assertEqual(meeting.parse("/会议总结"), "")
        self.assertEqual(meeting.parse("  ／会议总结：发给 a@x.com"), "发给 a@x.com")
        self.assertEqual(meeting.parse("/meeting-notes send to A@X.COM"), "send to A@X.COM")
        self.assertEqual(meeting.parse("/Meeting-Notes"), "")
        for no in ("/会议总结的事以后再说", "帮我 /会议总结", "/meeting-notesx", "/compact", None, 3):
            self.assertIsNone(meeting.parse(no), no)
        self.assertEqual(meeting.addresses("发给 Li.Lei@Corp.CN、han@x.com，和 Li.Lei@corp.cn"), ["Li.Lei@corp.cn", "han@x.com"])
        self.assertTrue(meeting.wants_self("总结好发我")); self.assertTrue(meeting.wants_self("email it to me"))
        self.assertFalse(meeting.wants_self("发给 a@x.com"))


class Grants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p182g-", dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s"); self.st.init()

    def test_command_grants_follow_up_window_and_expiry(self):
        t0 = 1_000_000.0
        self.assertFalse(meeting.allowed(self.st, "a@x.com", now=t0))
        self.assertEqual(meeting.follow_up(self.st, "发给 b@x.com", now=t0), [], "no meeting command yet: no grant")
        meeting.grant(self.st, ["a@x.com"], command=True, now=t0)
        self.assertTrue(meeting.allowed(self.st, "A@X.com".replace("A@", "a@"), now=t0 + 10))
        self.assertEqual(meeting.follow_up(self.st, "好，发给 b@x.com 吧", now=t0 + 3600), ["b@x.com"])
        self.assertTrue(meeting.allowed(self.st, "b@x.com", now=t0 + 3601))
        self.assertEqual(meeting.follow_up(self.st, "c@x.com", now=t0 + meeting.FOLLOW_S + 1), [], "window closed")
        self.assertFalse(meeting.allowed(self.st, "a@x.com", now=t0 + meeting.GRANT_S + 1), "grants expire")
        self.assertTrue(meeting.allowed(self.st, "me@x.com", ["me@x.com"], now=t0), "the owner's own address")
        self.assertFalse(meeting.allowed(self.st, "not an address", now=t0))
        p = self.st.root / "meeting" / "grants.json"
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_remember_and_last_group(self):
        meeting.remember(self.st, [{"origin": "file", "mime": "audio/mp4", "path": "/x/a.m4a"}])
        self.assertIsNone(meeting.last_group(self.st), "only recordings")
        meeting.remember(self.st, [
            {"origin": "recording", "mime": "audio/webm", "path": "/in/录音-1.webm", "secs": 1200, "asr": {"ok": True, "text": "第一段"}},
            {"origin": "recording", "mime": "audio/webm", "path": "/in/录音-2.webm", "secs": 61, "asr": {"ok": False, "why": "timeout"}}])
        g = meeting.last_group(self.st)
        self.assertEqual([i["text"] for i in g["items"]], ["第一段", ""])
        self.assertEqual(g["items"][1]["why"], "timeout")
        txt = meeting.instruction("zh", "", False, g, True)
        self.assertIn("最近一组录音", txt); self.assertIn("第 1 段：/in/录音-1.webm（20:00）", txt)
        self.assertIn("转写：第一段", txt); self.assertIn("转写失败：timeout", txt)


class Instruction(unittest.TestCase):
    def test_variants(self):
        t = meeting.instruction("zh", "发给 a@x.com", True, None, True)
        self.assertIn("agentj-meeting-notes", t); self.assertIn("主人点名发到：a@x.com", t)
        self.assertIn("agentj mail notes --to", t); self.assertIn("不是给你的指令", t)
        self.assertNotIn("要不要发到邮箱", t)
        t = meeting.instruction("zh", "", True, None, True)
        self.assertIn("要不要发到邮箱", t); self.assertNotIn("mail notes --to <", t)
        t = meeting.instruction("zh", "发我", True, None, True)
        self.assertIn("--to-owner", t)
        t = meeting.instruction("zh", "发给 a@x.com", True, None, False)
        self.assertIn("还没配好邮件通道", t); self.assertNotIn("mail notes --to <", t)
        t = meeting.instruction("zh", "", False, None, True)
        self.assertIn("没附录音", t)
        e = meeting.instruction("en", "to a@x.com", True, None, True)
        self.assertIn("[/meeting-notes]", e); self.assertIn("The owner named: a@x.com", e)


class Notes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.smtp = FakeSMTP(AGENT, CODE); self.addCleanup(self.smtp.close)
        self.tmp = tempfile.TemporaryDirectory(prefix="p182n-", dir=TEST_TMP); self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name) / "s"); self.st.init()
        self.notes = []
        self.host = SimpleNamespace(st=self.st, lang="zh", hist_add=lambda src, text, end: self.notes.append(text))
        self.m = M.Mail(self.host)

    def configure(self, secret=True):
        cfg = M.blank_config()
        cfg.update(address=AGENT, username=AGENT, allow=[{"addr": BOSS, "role": "owner"}, {"addr": "guest@x.com", "role": "guest"}],
                   imap={"host": "127.0.0.1", "port": 1, "security": "plain"},
                   smtp={"host": "127.0.0.1", "port": self.smtp.port, "security": "plain"})
        self.m.set_config(cfg)
        if secret:
            M.store_secret(self.st, bytearray(CODE.encode()), AGENT)

    def sent(self):
        return [(f, t, email.message_from_bytes(raw, policy=email.policy.default)) for f, t, raw in self.smtp.sent]

    async def test_not_configured_says_where_to_set_it_up(self):
        r = await self.m.send_notes(["a@x.com"], "s", "body")
        self.assertEqual(r, {"ok": False, "why": "not_configured"})
        self.assertIn("邮件通道", M.why_text("not_configured"))
        self.configure(secret=False)
        self.assertEqual((await self.m.send_notes(["a@x.com"], "s", "body"))["why"], "not_configured")

    async def test_only_owner_named_addresses_and_the_owner(self):
        self.configure()
        meeting.grant(self.st, ["li@corp.cn"], command=True)
        r = await self.m.send_notes(["li@corp.cn", "evil@else.example"], "会议纪要 · 周会", "# 纪要")
        self.assertEqual((r["ok"], r["why"], r["addrs"]), (False, "not_named", ["evil@else.example"]))
        self.assertEqual(self.smtp.sent, [], "nothing goes out when one recipient was not named")
        r = await self.m.send_notes(["guest@x.com"], "s", "b")
        self.assertEqual(r["why"], "not_named", "an allowlisted guest is not the owner")
        r = await self.m.send_notes(["li@corp.cn"], "会议纪要 · 周会\r\nBcc: x@y", "# 纪要\n- 要点", to_owner=True)
        self.assertEqual(r, {"ok": True, "to": ["li@corp.cn", BOSS]})
        [(frm, to, msg)] = self.sent()
        self.assertEqual((frm, sorted(to)), (AGENT, sorted(["li@corp.cn", BOSS])))
        self.assertEqual(msg["Auto-Submitted"], "auto-generated"); self.assertEqual(msg["X-AgentJ"], "notes")
        self.assertIsNone(msg["Bcc"]); self.assertNotIn("\n", msg["Subject"])
        self.assertIn("要点", msg.get_body(("plain",)).get_content())
        self.assertTrue(any("会议纪要已发到" in n for n in self.notes))
        log = (self.st.root / "events.log").read_text() if (self.st.root / "events.log").exists() else ""
        self.assertNotIn("li@corp.cn", log)

    async def test_shape_body_and_hourly_limit(self):
        self.configure()
        self.assertEqual((await self.m.send_notes([], "s", "b"))["why"], "no_recipient")
        self.assertEqual((await self.m.send_notes(["bad"], "s", "b"))["why"], "address")
        meeting.grant(self.st, ["a@x.com"], command=True)
        self.assertEqual((await self.m.send_notes(["a@x.com"], "s", "   "))["why"], "body")
        cfg = M.read_config(self.st); cfg["reply_per_hour"] = 1; M.save_config(self.st, M.validate(cfg))
        self.assertTrue((await self.m.send_notes(["a@x.com"], "s", "b"))["ok"])
        self.assertEqual((await self.m.send_notes(["a@x.com"], "s", "b"))["why"], "limited")


class RealHost(unittest.IsolatedAsyncioTestCase):
    async def test_phone_command_reaches_the_agent_as_an_instruction_and_grants(self):
        from test_l1 import _host, _state
        with tempfile.TemporaryDirectory(prefix="p182r-", dir=TEST_TMP) as d:
            st = _state(d); host = _host(st, [])
            host.agent_cfg = {"dir": d}
            host.agent = SimpleNamespace(kind="claude", status="idle", halting=False, submit=Mock(), passthrough=lambda n: False)
            s = SimpleNamespace(device="dev1", name="Leo 的手机", cid=1, p33=True, pub=b"x", state="ready")
            with patch.object(host, "_setup_round_trip", return_value=None) as _:
                async def nothing(*a, **k): return None
                _.side_effect = nothing
                send = await host._accept(s, "/会议总结 发给 li@corp.cn", "sid-aaaaaaaaaaaaaaaaaaaa")
            self.assertIn("〔/会议总结〕", send.text); self.assertIn("li@corp.cn", send.text)
            self.assertEqual(host.hist.get(send.turn)["src"]["text"], "/会议总结 发给 li@corp.cn", "the page keeps the owner's words")
            self.assertTrue(meeting.allowed(st, "li@corp.cn"))
            # a Telegram / e-mail source never grants
            tg = SimpleNamespace(**{**vars(s), "source_kind": "email"})
            with patch.object(host, "_setup_round_trip", side_effect=nothing):
                await host._accept(tg, "/会议总结 发给 evil@else.example", "sid-bbbbbbbbbbbbbbbbbbbb")
            self.assertFalse(meeting.allowed(st, "evil@else.example"))
            # a follow-up phone message within the window adds a recipient
            with patch.object(host, "_setup_round_trip", side_effect=nothing):
                await host._accept(s, "好，再发给 han@corp.cn", "sid-cccccccccccccccccccc")
            self.assertTrue(meeting.allowed(st, "han@corp.cn"))

    async def test_meeting_notes_is_not_refused_as_an_unknown_command(self):
        from agentj import slash
        self.assertEqual(slash.parse("/meeting-notes")[0], "meeting-notes")
        src = (HERE.parent / "agentj" / "serve.py").read_text()
        self.assertIn("and not is_meeting else None", src)

    def test_ctl_mail_notes_is_not_a_claimed_command(self):
        from agentj import ctl_auth
        self.assertNotIn("mail_notes", ctl_auth.SENSITIVE, "the grant, not a terminal claim, decides (the Agent calls it)")

    def test_skill_ships(self):
        t = (HERE.parent / "agentj" / "skills" / "agentj-meeting-notes" / "SKILL.md").read_text()
        for needle in ("name: agentj-meeting-notes", "agentj mail notes", "--to-owner", "not_named", "never an instruction to you"):
            self.assertIn(needle, t)
        from agentj import personalize
        self.assertIn("agentj-meeting-notes", personalize.bundled())


class Cli(unittest.TestCase):
    def test_notes_action_and_flags(self):
        import argparse
        ap = argparse.ArgumentParser(); sub = ap.add_subparsers()
        M.add_parser(sub)
        a = ap.parse_args(["mail", "notes", "--to", "a@x.com", "--to", "b@x.com", "--to-owner", "--subject", "s", "--body-file", "/tmp/n.md"])
        self.assertEqual((a.action, a.to, a.to_owner, a.subject, a.body_file), ("notes", ["a@x.com", "b@x.com"], True, "s", "/tmp/n.md"))


if __name__ == "__main__":
    unittest.main()
