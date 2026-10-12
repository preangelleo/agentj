"""P167: the owner's own E2E channel (paired phone / web page) shows e-mail addresses and phone numbers as written — masking
them only stops the owner from copying them. Secret shapes (keys, tokens, private keys, passwords, high-entropy strings) are
still redacted. Every outward channel (feedback / plaza / support / friends / bots / Telegram groups and the owner's Telegram
DM) keeps the strict rule."""
import _hermetic  # noqa: F401,I001
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agentj import privacy, session_choice, shared, shared_opencode2, telegram  # noqa: E402
from agentj.shared_state import AgentState  # noqa: E402

R = privacy.R
MAIL = "boss@owner.example"
REPLY = f"以后您说发给助理，就从您的主邮箱发到 {MAIL}，急事打 +81 90-1234-5678 或 138 1234 5678。"
KEY = privacy.FAKE_SECRETS["openrouter"]
TOKEN = privacy.FAKE_SECRETS["keyseg"]                               # 36-char mixed-case high-entropy string
PEM = "-----" + "BEGIN OPENSSH PRIVATE " + "KEY-----\nabc\n-----" + "END OPENSSH PRIVATE " + "KEY-----"   # assembled: no key-shaped literal
OWNER_FNS = (privacy.owner_text, shared.public_text, shared_opencode2.public_text)


class OwnerChannel(unittest.TestCase):
    def test_contacts_shown_as_written(self):
        for fn in OWNER_FNS:
            with self.subTest(fn.__module__):
                self.assertEqual(fn(REPLY), REPLY)

    def test_secrets_still_redacted(self):
        text = (f"{MAIL} 的 key: OPENROUTER_API_KEY={KEY}；session {TOKEN}；password: 'hunter2hunter2'；" +
                PEM)
        for fn in OWNER_FNS:
            with self.subTest(fn.__module__):
                out = fn(text)
                self.assertIn(MAIL, out)
                for gone in (KEY, TOKEN, "hunter2hunter2", "abc\n"):
                    self.assertNotIn(gone, out)
                self.assertEqual(fn(out), out)                         # idempotent

    def test_stop_hook_reply_keeps_contacts(self):
        """The Claude Stop hook's last_assistant_message is the first scrub on the shared-mode reply path."""
        got = []
        s = AgentState(on_turn=lambda event, reply: got.append(reply))
        s._commit_stop({"last_assistant_message": REPLY + f" token {KEY}"})
        self.assertIn(MAIL, got[0])
        self.assertIn("+81 90-1234-5678", got[0])
        self.assertNotIn(KEY, got[0])
        self.assertIn(MAIL, s.last_message)

    def test_session_picker_label(self):
        self.assertIn(MAIL, session_choice._short(f"发到 {MAIL}", 80))


class OutwardStrict(unittest.TestCase):
    def test_strict_default_masks_contacts(self):
        out = privacy.redact(REPLY)
        self.assertNotIn(MAIL, out)
        self.assertIn("<email>", out)
        self.assertIn("<phone>", out)
        self.assertEqual(privacy.redact_draft({"t": REPLY})["t"], out)  # feedback / plaza / support drafts

    def test_telegram_groups_and_owner_dm(self):
        self.assertNotIn(MAIL, telegram.group_filter(REPLY))
        self.assertNotIn(MAIL, privacy.redact(REPLY))               # telegram.py owner DM: privacy.redact (strict)


if __name__ == "__main__":
    unittest.main()
