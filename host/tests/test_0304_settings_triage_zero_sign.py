"""0304 settings line: a phone mail-triage save whose signed `before` AND `cfg` carry usd_per_day=0.0 verifies.

Regression for the 0.30.1–0.30.3 field failure (bad_signature: host "0.0" vs page canonicalJson "0"). Signs with the
page's real wire.js canonicalJson (test_0304_mail_sign_js.js_texts), so the Python ↔ JS boundary is crossed.
"""
import _hermetic  # noqa: F401,I001
import shutil
import unittest

from agentj import mail
import test_0304_mail_sign_js as base  # module import: the loader must not collect the base class here


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TriageZeroBudgetSave(base.PhoneSignedLikeThePage):
    async def test_triage_save_with_zero_float_budget_verifies(self):
        self._seed()
        before = mail.read_config(self.st)["triage"]
        self.assertIsInstance(before["usd_per_day"], float)
        self.assertEqual(before["usd_per_day"], 0.0)
        cfg = {"triage": {**before, "usd_per_day": 0.0, "every_minutes": 45}}
        prepared, n = await self._prepare("mail_set", cfg)
        await self.h.on_mail(self.session, "mail_set", {"r": "9" * 16, "cfg": cfg, "authorization": self._sign_as_page(prepared, n)})
        self.assertTrue(self.sent[-1][1]["ok"], self.sent[-1][1].get("why"))
        saved = mail.read_config(self.st)["triage"]
        self.assertEqual((saved["usd_per_day"], saved["every_minutes"]), (0.0, 45))
        self.assertEqual(self._refusals(), [])


# Only run the new case here; the inherited cases run in their own file.
for _name in [n for n in dir(base.PhoneSignedLikeThePage) if n.startswith("test_")]:
    setattr(TriageZeroBudgetSave, _name, None)

if __name__ == "__main__":
    unittest.main()
