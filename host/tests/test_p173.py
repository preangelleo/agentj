"""P173: install Step 9 asks only what the owner must decide — no "is your browser signed in to a bank?" question.
Paying / ordering / moving money on any website always asks on the phone first, so that answer would change nothing."""
import _hermetic  # noqa: F401
import re
import unittest
from pathlib import Path

from agentj import main_identity

ROOT = Path(__file__).resolve().parents[1] / "agentj"
REPO = ROOT.parents[2]


def step(text: str, n: int) -> str:
    m = re.search(rf"^## {n}\. .*?(?=^## {n + 1}\. )", text, re.S | re.M)
    assert m, n
    return m.group(0)


class InstallStep9(unittest.TestCase):
    def install(self) -> str:
        p = REPO / "documentation/product/install.md"
        if not p.exists():
            self.skipTest("no documentation/ (public export ships host/ alone)")
        return p.read_text()

    def test_no_browser_signin_question(self):
        s9 = step(self.install(), 9)
        self.assertNotIn("ask whether browsers", s9)
        self.assertIn("Do not ask whether their browsers are signed in to email, shops or banking", s9)
        self.assertIn("always stops and asks them on the phone first", s9)

    def test_one_message_with_defaults(self):
        s9 = step(self.install(), 9)
        self.assertIn("Detect first, then ask once, with defaults", s9)
        self.assertIn("Mark one as recommended", s9)
        self.assertIn("never pick it for them", s9)          # the tool line stays without a default

    def test_phone_type_moves_to_step_11(self):
        t = self.install()
        self.assertNotIn("ask your human: iPhone or Android?", step(t, 3))
        self.assertIn("iPhone or Android?", step(t, 11))

    def test_public_copy_matches(self):
        t = self.install()
        pub = REPO / "agentjarvis/site/public/install.md"
        self.assertEqual(pub.read_text(), t)

    def test_docs_explain_why(self):
        d = REPO / "agentjarvis/site/content/docs/02-install"
        if not d.exists():
            self.skipTest("no site/ (public export ships host/ alone)")
        en, zh = (d / "en.md").read_text(), (d / "zh.src.md").read_text()
        self.assertIn("It doesn't ask whether your browsers are signed in to email, shops or your bank", en)
        self.assertIn("它不会问你浏览器里有没有登录邮箱、购物网站或银行", zh)
        self.assertIn("第 11 步", zh)


class BrowserPaymentRule(unittest.TestCase):
    def test_skill_says_payment_always_asks(self):
        s = (ROOT / "skills/agentj-browser/SKILL.md").read_text()
        self.assertIn("Paying, placing an order or moving money on any website", s)
        self.assertIn("never ask the owner whether their browsers hold bank sign-ins", s)

    def test_identity_line(self):
        self.assertIn("付款、下单、转账，一律先在手机上问主人", main_identity.BROWSER_LINE["zh"])
        self.assertIn("always asks the owner on the phone first", main_identity.BROWSER_LINE["en"])
        main_identity.verify_core()                       # the hashed core is untouched


if __name__ == "__main__":
    unittest.main()
