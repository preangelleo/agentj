"""P256: provider-specific signed cards, grouped Gmail values, private persistence, no send."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import asyncio
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from agentj import mail as M, elevate
from agentj.state import State

class Providers(unittest.TestCase):
    def test_domain_wins_workspace_and_unknown(self):
        for domain, expected in [("gmail.com", "gmail"), ("googlemail.com", "gmail"), ("qq.com", "qq"), ("163.com", "netease"), ("126.com", "netease"), ("outlook.com", "outlook"), ("icloud.com", "icloud"), ("yahoo.com", "yahoo")]:
            cfg = M.blank_config(); cfg.update(address="test@" + domain, preset="custom")
            self.assertEqual(M.password_provider(cfg), expected)
            for lang in ("zh", "en"):
                text = M.password_purpose(cfg, lang)
                self.assertIn(M.PASSWORD_HELP[expected][0 if lang == "zh" else 1], text)
                self.assertIn(M.PASSWORD_HELP[expected][2].split()[0], text)
                self.assertLessEqual(len(text), 2000)
        cfg = M.blank_config(); cfg.update(address="test@work.example", preset="custom")
        self.assertEqual(M.password_provider(cfg), "custom")
        cfg["imap"]["host"] = "imap.gmail.com"
        self.assertEqual(M.password_provider(cfg), "gmail")
        self.assertIn("空格不用删", M.password_purpose(cfg, "zh"))
        a = dict(kind="secret", name="AGENTJ_MAIL_PASSWORD", purpose=M.password_purpose(cfg, "zh"), dest="mail", verify="IMAP+SMTP")
        before = elevate.shown_digest("secret", elevate.shown_fields(a)); a["purpose"] += "changed"
        self.assertNotEqual(before, elevate.shown_digest("secret", elevate.shown_fields(a)))

class Secret(unittest.IsolatedAsyncioTestCase):
    async def test_card_sink_private_grouped_and_stale(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            st = State(Path(tmp)/"state"); st.init()
            host = SimpleNamespace(st=st, lang="zh")
            mail = M.Mail(host)
            cfg = M.apply_preset(M.blank_config(), "gmail", "test@googlemail.com"); M.save_config(st, cfg)
            value = b"abcd efgh ijkl mnop"; cards = []
            class El:
                async def request(self, card):
                    cards.append(card)
                    return card["sink"](bytearray(value))
            host.elevate = El()
            def check(c, pw):
                self.assertEqual(pw, "abcdefghijklmnop")
                self.assertEqual(M.load_secret(st, c["address"]), pw, "saved before check")
                return dict(ok=False, imap="imap_login", smtp="ok", auth_sample=None)
            with patch.object(M, "test_connection", side_effect=check) as verify:
                result = await mail.secret_card()
                self.assertEqual(verify.call_count, 1)
            self.assertEqual(result["result"], "saved")
            self.assertEqual(result["receipt"]["verify"], "fail")
            self.assertEqual((st.root/"mail"/M.SECRET).stat().st_mode & 0o777, 0o600)
            self.assertNotIn(b"abcdefghijklmnop", (st.root/"mail"/M.SECRET).read_bytes())
            self.assertNotIn(value.decode(), str(result))
            self.assertIn("https://myaccount.google.com/apppasswords", cards[0]["purpose"])
            cfg["poll_minutes"] = 10; M.save_config(st, cfg)
            self.assertEqual(cards[0]["sink"](bytearray(value))["why"], "stale")

    async def test_non_google_spaces_preserved(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            st = State(Path(tmp)/"state"); st.init()
            cfg = M.apply_preset(M.blank_config(), "qq", "test@qq.com"); M.save_config(st,cfg)
            host = SimpleNamespace(st=st, lang="en")
            class El:
                async def request(self, card): return card["sink"](bytearray(b"ab cd"))
            host.elevate=El()
            with patch.object(M,"test_connection",return_value=dict(ok=True,imap="ok",smtp="ok",auth_sample=None)) as check:
                result=await M.Mail(host).secret_card()
            self.assertEqual(check.call_args.args[1],"ab cd")
            self.assertEqual(result["receipt"]["verify"],"ok")
