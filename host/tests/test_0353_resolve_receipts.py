"""Closed upgrade reporting fields reach manual and idle receipts, including old records."""
import time
from unittest import mock

from agentj import __version__, auto_update, phone_update, upgrade_diag
from test_p277_upgrade_diag import _Env


class ResolveReceipts(_Env):
    def setUp(self):
        super().setUp()
        self.failure = dict(result="failed", reason="uv_failed", dependency_phase="resolve",
                            step="install", **{"from": __version__, "to": "9.9.9"})
        self.thread = "st_" + "x" * 22

    def test_report_contents_and_recovery(self):
        for support, zh, en in (
            (f"sent ({self.thread}) metadata only", "不含日志", "no logs"),
            (f"sent ({self.thread})", "脱敏故障报告", "redacted failure report"),
            (f"already sent ({self.thread})", "已有", "already"),
            ("off", "自动上报已关闭", "reporting is off"),
            ("not_sent", "未能发给客服", "could not be sent"),
            ("", "尚未确认客服", "Delivery to support is unconfirmed"),
        ):
            for lang, expected in (("zh", zh), ("en", en)):
                with self.subTest(lang=lang, support=support):
                    text = phone_update.text({**self.failure, "support": support}, lang)
                    status, recovery = text.split("\n\n")
                    self.assertIn(__version__, status)
                    self.assertIn("9.9.9", status)
                    self.assertIn(expected, status)
                    self.assertIn("/update", recovery)
                    self.assertIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, recovery)
                    self.assertNotIn("agentj doctor", text)
                    self.assertNotIn("will retry", text)

    def test_only_confirm_current_version_from_journal(self):
        for phase, wording in (("prepared", "still on"), ("installing", "unconfirmed")):
            text = phone_update.text({**self.failure, "phase": phase}, "en")
            self.assertIn(wording, text)

    def test_idle_worker_delivers_its_own_support_receipt_once(self):
        rec = {**self.failure, "phase": "prepared", "attempt": "synthetic"}
        def send(st, result, **_):
            self.assertIsNone(auto_update.take_notice(st, lang="en"))
            result["support"] = f"sent ({self.thread}) metadata only"
        with mock.patch.object(upgrade_diag, "after_failure", side_effect=send):
            auto_update.finish(self.st, rec)
            text = auto_update.take_notice(self.st, lang="en")
            self.assertIn("no logs", text)
            self.assertIn(self.thread, text)
            with mock.patch.object(upgrade_diag, "after_failure"):
                auto_update.finish(self.st, rec)
            self.assertIsNone(auto_update.take_notice(self.st))

    def test_interrupted_report_still_yields_result(self):
        with mock.patch.object(auto_update, "_diagnose", return_value=None):
            auto_update.finish(self.st, self.failure)
        with mock.patch.object(time, "time", return_value=time.time() + 120):
            self.assertIn("Delivery to support is unconfirmed", auto_update.take_notice(self.st, "en"))
