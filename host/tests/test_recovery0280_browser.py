"""#470 and 0.30.47 confirmation through a bound, synthetic browser session."""
import _hermetic  # noqa: F401
import asyncio
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace as NS

from agentj import browser, browser_login as login, browser_sites as sites
from agentj.state import State
from test_p114 import Base, FakeHost, FakeElev


class ScanLanding(Base, unittest.IsolatedAsyncioTestCase):
    def fixture(self, href):
        self.runtime = {"ws": "fixture", "pid": 10, "starttime": "1"}
        check_runtime = patch.object(browser, "runtime", side_effect=lambda st: self.runtime)
        check_runtime.start()
        self.addCleanup(check_runtime.stop)
        sites.add(self.st, "bilibili")
        cdp = Mock()
        cdp.location.return_value = (href, "complete")
        card = {"id": "a" * 32, "nonce": "fixture", "site": "bilibili", "title": "Bilibili", "cdp": cdp,
                "session": "original", "deadline": time.monotonic() + 120, "lock": threading.Lock(),
                "revision": 0, "confirm_lock": asyncio.Lock(), "runtime": login.LoginCards._runtime_id(self.runtime)}
        host = NS(st=self.st, lang="en", hist_add=Mock(), send_app=AsyncMock())
        desk = login.LoginCards(NS(st=self.st, host=host))
        desk.cards[card["id"]] = card
        desk._end = Mock()
        desk._audit = Mock()
        return desk, card

    async def test_landing_requires_trusted_fresh_check(self):
        desk, card = self.fixture("https://www.bilibili.com/")
        def observe(cdp, rule, url, *, fresh):
            self.assertIs(cdp, card["cdp"])
            self.assertTrue(card["lock"].locked())
            self.assertTrue(fresh)
            self.assertEqual(url, rule["check_url"])
            return "logged_in", "ok"
        with patch.object(sites, "probe", side_effect=observe) as check:
            self.assertEqual(desk._tab(card)[0], "landing")
            await desk._check_tap(NS(device="fixture"), card)
        check.assert_called_once()
        desk._end.assert_called_once_with(card, "done")
        desk.host.send_app.assert_not_awaited()

    async def test_scan_tap_reports_failure_without_claiming_success(self):
        desk, card = self.fixture("https://passport.bilibili.com/login?synthetic=private")
        with patch.object(sites, "probe", return_value=("auth_required", "signed_out")):
            await desk._check_tap(NS(device="fixture"), card)
        desk._end.assert_not_called()
        message = desk.host.send_app.await_args.args[1]
        self.assertEqual(message["why"], "other_browser")
        self.assertEqual(message["state"], "waiting")
        self.assertNotIn("private", str(desk.host.hist_add.call_args))

    async def test_actual_phone_tap_retains_pairing_nonce_and_trusted_probe(self):
        desk, card = self.fixture("https://passport.bilibili.com/login")
        msg = {"t": "login_qr_check", "id": card["id"], "n": card["nonce"]}
        with patch.object(sites, "probe", return_value=("auth_required", "signed_out")) as check, \
                patch.object(State, "sign_key", return_value=b"fixture-device-key") as key:
            await desk.on_phone(NS(device="fixture"), msg)
            check.assert_called_once()
            self.assertEqual(desk.host.send_app.await_args.args[1]["why"], "other_browser")
            check.reset_mock()
            key.return_value = None
            await desk.on_phone(NS(device="revoked"), msg)
            check.assert_not_called()
            key.return_value = b"fixture-device-key"
            await desk.on_phone(NS(device="fixture"), dict(msg, n="wrong"))
            check.assert_not_called()
            self.assertEqual(desk.host.send_app.await_args.args[1]["why"], "gone")
        desk._end.assert_not_called()

    async def test_previous_success_cannot_confirm_when_probe_times_out(self):
        desk, card = self.fixture("https://www.bilibili.com/")
        with patch.object(sites, "probe", return_value=("logged_in", "ok")):
            sites.check(self.st, ["bilibili"], _cdp=Mock())
        with patch.object(sites, "probe", return_value=("unknown", "timeout")):
            await desk._check_tap(NS(device="fixture"), card)
        desk._end.assert_not_called()
        self.assertEqual(sites.summary(self.st)[0]["status"], "unknown")

    async def test_source_change_during_probe_discards_positive(self):
        for change in ("new_browser", "closed_tab", "unsupported_page", "signin_form", "site_removed"):
            with self.subTest(change=change):
                desk, card = self.fixture("https://www.bilibili.com/")
                def change_source(*args, **kwargs):
                    if change == "new_browser":
                        self.runtime["ws"] = "different browser on the same port"
                    elif change == "closed_tab":
                        card["cdp"].location.side_effect = login.CDPError()
                    elif change == "site_removed":
                        sites.remove(self.st, card["site"])
                    else:
                        url = "https://external.invalid/" if change == "unsupported_page" else "https://passport.bilibili.com/login"
                        card["cdp"].location.return_value = (url, "complete")
                    return "logged_in", "ok"
                with patch.object(sites, "probe", side_effect=change_source):
                    await desk._check_tap(NS(device="fixture"), card)
                desk._end.assert_not_called()
                self.assertFalse(any(r["status"] == "logged_in" for r in sites.summary(self.st)))

    async def test_late_probe_cannot_complete_or_report_on_invalidated_card(self):
        for change in ("cancel", "expire", "refresh"):
            with self.subTest(change=change):
                desk, card = self.fixture("https://www.bilibili.com/")
                started, release = threading.Event(), threading.Event()
                def observe(*args, **kwargs):
                    started.set()
                    self.assertTrue(release.wait(3))
                    return "logged_in", "ok"
                with patch.object(sites, "probe", side_effect=observe):
                    task = asyncio.create_task(desk._check_tap(NS(device="fixture"), card))
                    try:
                        self.assertTrue(await asyncio.to_thread(started.wait, 3))
                        if change == "cancel":
                            desk.cards.pop(card["id"])
                        elif change == "expire":
                            card["deadline"] = time.monotonic()
                        else:
                            card["revision"] += 1
                    finally:
                        release.set()
                    await task
                desk._end.assert_not_called()
                desk.host.send_app.assert_not_awaited()
                self.assertEqual(sites.summary(self.st)[0]["status"], "unknown")

    async def test_overlapping_requests_share_no_capture_and_host_stop_invalidates_inflight_request(self):
        sites.add(self.st, "bilibili")
        desk = login.LoginCards(FakeElev(FakeHost(self.st)))
        started, release = threading.Event(), threading.Event()
        original_cdp = Mock()

        def capture(*args):
            started.set()
            self.assertTrue(release.wait(3))
            return {"state": "in", "cdp": original_cdp, "session": "original", "target": "tab"}

        with patch.object(browser, "runtime", return_value={"ws": "fixture"}), \
                patch.object(desk, "_open_and_capture", side_effect=capture) as open_and_capture, \
                patch.object(sites, "probe") as probe:
            first = asyncio.create_task(desk.request({"site": "bilibili"}))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                repeated = await desk.request({"site": "bilibili", "refresh": True})
                self.assertEqual(repeated["result"], "busy")
                desk.end_all("gone")
            finally:
                release.set()
            self.assertEqual((await first)["result"], "gone")
            open_and_capture.assert_called_once()
            original_cdp.close.assert_called_once()
            probe.assert_not_called()

    async def test_initial_login_needs_the_observed_status_from_original_session(self):
        sites.add(self.st, "bilibili")
        desk = login.LoginCards(FakeElev(FakeHost(self.st)))
        runtime = {"ws": "fixture"}
        source = Mock()
        source.location.return_value = ("https://www.bilibili.com/", "complete")
        captured = {"state": "in", "cdp": source, "session": "original", "target": "tab"}
        with patch.object(browser, "runtime", return_value=runtime), \
                patch.object(desk, "_open_and_capture", return_value=captured), \
                patch.object(sites, "probe", return_value=("auth_required", "signed_out")) as probe:
            self.assertEqual((await desk.request({"site": "bilibili"}))["result"], "failed")
            self.assertIs(probe.call_args.args[0], source)
            source.close.assert_called_once()
            self.assertEqual(sites.summary(self.st)[0]["status"], "auth_required")
