"""#0302 r5b: hosts with owner mail and no pinned trusted mail server get one doctor line saying what to do."""
import _hermetic  # noqa: F401
import unittest
from unittest import mock
from agentj import doctor, mail


class MailOwnerHint(unittest.TestCase):
    def row(self, **cfg):
        base = {**mail.blank_config(), "enabled": True, "allow": [{"addr": "owner@example.com", "role": "owner"}]}
        with mock.patch.object(mail, "read_config", lambda st: {**base, **cfg}):
            return doctor.check_mail_owner(object())

    def test_unpinned_owner_mail_warns_with_next_step(self):
        r = self.row(auth_servid="")
        self.assertEqual((r["id"], r["status"]), ("mail-owner", doctor.WARN))
        self.assertIn("只认这台服务器写的校验结果", r["hint"])        # the real Settings → Mail button (web i18n mail.pin)
        self.assertIn("Trust only this server's check results", r["hint"])

    def test_pinned_disabled_or_no_owner_is_silent(self):
        self.assertIsNone(self.row(auth_servid="mx.google.com"))
        self.assertIsNone(self.row(enabled=False))
        self.assertIsNone(self.row(allow=[{"addr": "guest@example.com", "role": "guest"}]))


if __name__ == "__main__":
    unittest.main()
