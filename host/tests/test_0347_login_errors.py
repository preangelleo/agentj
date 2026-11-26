"""Status and recipient regressions with a synthetic clock and no browser, account, or real login material."""
import _hermetic  # noqa: F401
import asyncio
import hashlib
import itertools
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agentj import approval_material, browser, browser_login as bl, browser_sites as bs, provenance, wire
from agentj.serve import Host, Session
from test_p114 import Base


class IssueStages(unittest.TestCase):
    def observe(self, location, observation=None):
        cards = bl.LoginCards(Mock())
        driver = Mock()
        driver.location.return_value = location
        clock = itertools.count()
        rule = {"login_url": "https://example.invalid/login", "origins": ["https://example.invalid"],
                "out_url": [r"^https://example\.invalid/login$"], "qr": {"selectors": []}}
        with patch.object(bl, "CDP", return_value=driver), patch.object(bl, "capture_qr") as capture, \
                patch.object(bl, "QR_WAIT", 2), patch.object(bl.time, "sleep"), \
                patch.object(bl.time, "monotonic", side_effect=lambda: next(clock)):
            capture.return_value = None
            if observation is not None:
                capture.side_effect = observation
            result = cards._open_and_capture({"ws": "fixture"}, "fixture", rule)
        driver.close.assert_called_once()
        return result.get("reason")

    def test_failure_evidence(self):
        self.assertEqual(self.observe(("about:blank", "complete")), "page_not_ready")
        self.assertEqual(self.observe(("https://example.invalid/login", "loading")), "page_not_ready")
        self.assertEqual(self.observe(("https://example.invalid/login", "complete")), "qr_not_found")
        self.assertEqual(self.observe(("https://example.invalid/login", "complete"),
                                      bl.CDPTimeout("ignored")), "capture_timeout")
        self.assertIsNone(self.observe(("https://example.invalid/login", "complete"),
                                       bl.CDPError("untrusted browser text")))


class Errors(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.host = Host.__new__(Host)
        self.host.st = self.st
        self.host.send_lock = asyncio.Lock()
        self.host._op = AsyncMock()
        self.host.hist = SimpleNamespace(epoch=3)
        self.host.sessions = {}
        self.host.elevate = SimpleNamespace(host=self.host, st=self.st)
        self.cards = self.host.elevate.login = bl.LoginCards(self.host.elevate)
        self.phone = self.session(1)
        self.other = self.session(2)
        self.binding = bl.RequestPhone.from_session(self.host, self.phone)
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(turn=7, phone_binding=self.binding))
        self.proof = (self.host.agent, {"session": "3", "message_id": 7})
        bs.add(self.st, bl.CHANNELS)

    def session(self, n):
        pub, sign = bytes([n]) * 32, bytes([n + 2]) * 32
        did = self.st.add_device(pub, "fixture", sign)
        phone = Session(cid=n, device=did, pub=pub, sign_pub=sign, p33=True, state="ready",
                        send=Mock(), provenance=provenance.phone())
        phone.send.encrypt.return_value = b"fixture"
        self.host.sessions[n] = phone
        return phone

    def fixture_card(self):
        c = {"id": "a" * 32, "site": bl.CHANNELS, "restricted": True, "owner": self.binding,
             "deadline": time.monotonic() + 60, "nonce": "fixture"}
        self.cards.cards[c["id"]] = c
        return c

    async def test_first_failure_only_to_verified_origin_and_enum_only(self):
        self.host.send_app = AsyncMock()
        injected = {"site": bl.CHANNELS, "device": self.other.device, "reason": "untrusted"}
        with patch.object(approval_material, "verified_phone_chat", return_value=self.proof) as verify, \
                patch.object(self.host, "stopped", return_value=False), \
                patch.object(browser, "settings", return_value={"enabled": True}), \
                patch.object(browser, "runtime", return_value=None):
            result = await self.cards.request_from_agent(injected, 41)
            self.assertEqual(result, {"result": "no_browser", "reason": "browser_unavailable"})
            verify.assert_called_once_with(self.host, 41)
            self.host.send_app.assert_awaited_once_with(self.phone, {
                "t": "login_qr_err", "op": "request", "id": "", "site": bl.CHANNELS,
                "why": "no_browser", "reason": "browser_unavailable"}, _already_locked=True)
            self.host.send_app.reset_mock()
            verify.return_value = None
            self.assertEqual(await self.cards.request_from_agent(injected, 41),
                             {"result": "refused", "why": "owner_phone_required",
                              "detail": browser.CHANNELS_OWNER_HINT})
            self.host.send_app.assert_not_awaited()
        self.assertEqual(bl._error_frame(bl.CHANNELS, "request", {"result": "arbitrary", "reason": "arbitrary"}),
                         {"t": "login_qr_err", "id": "", "op": "request", "site": bl.CHANNELS, "why": "failed"})

    async def test_first_failure_skips_revoked_during_request(self):
        self.host.send_app = AsyncMock()
        async def fail(*args, **kwargs):
            self.st.add_device(self.phone.pub, "replacement", b'r' * 32)
            return {"result": "failed", "reason": "page_not_ready"}
        with patch.object(approval_material, "verified_phone_chat", return_value=self.proof), \
                patch.object(self.cards, "request", side_effect=fail):
            await self.cards.request_from_agent({"site": bl.CHANNELS}, 41)
        self.host.send_app.assert_not_awaited()

    async def test_refresh_filters_by_card_and_recipient_and_never_forwards_text(self):
        c = self.fixture_card()
        self.cards._remember(c["id"], {"site": bl.CHANNELS, "result": "pending"})
        self.cards.last_recipient[bl.CHANNELS] = (c["id"], self.binding)
        self.host.send_app = AsyncMock()
        refresh = {"t": "login_qr_refresh", "site": bl.CHANNELS, "id": c["id"]}
        with patch.object(self.cards, "request", new=AsyncMock(return_value={
                "result": "no_qr", "reason": "qr_not_found", "detail": "untrusted"})) as request:
            await self.cards.on_phone(self.other, refresh)
            request.assert_not_awaited()
            self.host.send_app.assert_awaited_once_with(self.other, {"t": "login_qr_err", "id": "", "why": "gone"})
            await self.cards.on_phone(self.phone, refresh)
            self.host.send_app.assert_awaited_with(self.phone, {
                "t": "login_qr_err", "id": c["id"], "site": bl.CHANNELS, "op": "refresh",
                "why": "no_qr", "reason": "qr_not_found"}, _already_locked=True)
            request.reset_mock()
            c["deadline"] = 0
            await self.cards.on_phone(self.phone, refresh)
            request.assert_not_awaited()
            self.host.send_app.assert_awaited_with(self.phone, {
                "t": "login_qr_err", "id": c["id"], "site": bl.CHANNELS, "op": "refresh", "why": "gone"}, _already_locked=True)

    async def test_concurrent_and_reconnected_sessions_cannot_replay(self):
        c = self.fixture_card()
        msg = {"t": "login_qr", "id": c["id"], "site": bl.CHANNELS}
        self.assertFalse(await self.host.send_app(self.other, msg))
        self.assertCountEqual(await asyncio.gather(self.host.send_app(self.phone, msg),
                                                   self.host.send_app(self.phone, msg)), [True, False])
        self.assertTrue(c["delivered"])
        self.host._op.assert_awaited_once()
        self.assertEqual(self.cards.open_msgs(self.phone), [])
        self.assertEqual(self.cards.open_msgs(self.other), [])
        self.assertEqual(await self.cards._telegram(c), "none")

    async def test_receipt_needs_actual_write_original_noise_session_and_exact_nonce(self):
        c = self.fixture_card()
        c.update(display="unconfirmed", chat_epoch=str(self.host.hist.epoch))
        seen = {"t": "login_qr_shown", "id": c["id"], "n": c["nonce"]}
        with patch.object(self.host, "stopped", return_value=False):
            await self.cards.on_phone(self.phone, seen)
            self.assertEqual(c["display"], "unconfirmed")
            self.assertTrue(await self.host.send_app(self.phone, {"t": "login_qr", "id": c["id"]}))
            await self.cards.on_phone(self.other, seen)
            await self.cards.on_phone(self.phone, {**seen, "n": "wrong"})
            self.assertEqual(c["display"], "unconfirmed")
            await self.cards.on_phone(self.phone, seen)
            await self.cards.on_phone(self.phone, seen)
            self.assertEqual(c["display"], "shown")
            self.assertEqual(self.cards.result({"id": c["id"]})["display"], "shown")
            self.assertEqual((browser.bdir(self.st) / "login.log").read_text().count('"result": "shown"'), 1)

    async def test_reconnect_can_request_only_a_new_code_with_live_paired_turn(self):
        stale = self.fixture_card()
        stale["display"] = "unconfirmed"
        fresh = Session(cid=10, device=self.phone.device, pub=self.phone.pub, sign_pub=self.phone.sign_pub,
                        state="ready", provenance=provenance.phone())
        self.host.sessions[10] = fresh
        binding = bl.RequestPhone.from_session(self.host, fresh)
        with patch.object(self.host, "stopped", return_value=False), \
                patch.object(browser, "runtime", return_value={"ws": "fixture"}), \
                patch.object(browser, "settings", return_value={"enabled": True}), \
                patch.object(self.host, "_approvers", return_value=[fresh]), \
                patch.object(self.cards, "_end") as end, \
                patch.object(self.cards, "_issue", AsyncMock(return_value={"result": "new-fixture"})) as issue:
            req = {"site": bl.CHANNELS}
            self.assertEqual((await self.cards.request(req, owner=binding, source_live=lambda: True))["result"], "refused")
            del self.host.sessions[self.phone.cid]
            self.assertEqual((await self.cards.request(req, owner=binding))["result"], "refused")
            self.assertEqual((await self.cards.request(req, owner=binding, source_live=lambda: False))["result"], "gone")
            issue.assert_not_awaited()
            self.assertEqual((await self.cards.request(req, owner=binding, source_live=lambda: True))["result"], "new-fixture")
            end.assert_called_once_with(stale, "refreshed")
            self.assertIs(issue.call_args.args[3], True)
            self.assertIsNone(issue.call_args.args[5])

    async def test_checking_progress_reaches_only_the_original_live_pair(self):
        c = self.fixture_card()
        c["delivered"] = True
        with patch.object(self.host, "send_app", new_callable=AsyncMock) as send:
            await self.cards._checking(c)
            await self.cards._checking(c)
            send.assert_awaited_once_with(self.phone, {"t": "login_qr_state", "id": c["id"], "state": "checking"},
                                          _already_locked=True)
            send.reset_mock()
            c.pop("checking")
            self.st.add_device(self.phone.pub, "replacement", b'r' * 32)
            await self.cards._checking(c)
            send.assert_not_awaited()

    async def test_fresh_tab_cannot_redisclose_the_previous_live_code(self):
        image, driver = b"synthetic image", Mock()
        self.cards.qr_history[bl.CHANNELS] = {"generation": (None, None, "old-tab"),
                                             "sha256": hashlib.sha256(image).hexdigest(),
                                             "deadline": time.monotonic() + 60}
        with patch.object(self.cards, "_open_and_capture", return_value={"state": "qr", "png": image, "cdp": driver,
                                                                        "session": "new", "target": "new-tab"}):
            result = await self.cards._issue(bl.CHANNELS, {}, {}, True, "agent", None, lambda: True,
                                             restricted=True, owner=self.binding)
        self.assertEqual(result["result"], "failed")
        self.assertNotIn("img", result)
        self.assertFalse(self.cards.cards)
        driver.close.assert_called_once()

    async def test_reencoded_qr_payload_is_not_a_new_challenge(self):
        from agentj import qrcrop
        zxing = qrcrop._zx()
        if zxing is None:
            self.skipTest("this platform has no packaged QR decoder")

        def png(scale):
            qr = zxing.create_barcode("synthetic login challenge", zxing.BarcodeFormat.QRCode)
            image = zxing.write_barcode_to_image(qr, scale=scale)
            view = memoryview(image)
            height, width = view.shape
            return qrcrop.png_encode(qrcrop.Img(width, height, 1, view.tobytes()))

        first, next_image = png(4), png(7)
        self.assertNotEqual(first, next_image)
        identity = bl._challenge_digest(first)
        self.assertIsNotNone(identity)
        self.assertEqual(bl._challenge_digest(next_image), identity)
        self.cards.qr_history[bl.CHANNELS] = {"generation": (None, None, "old-tab"),
                                             "sha256": hashlib.sha256(first).hexdigest(),
                                             "deadline": time.monotonic() + 60, "verified": True,
                                             "challenges": (identity,)}
        driver = Mock()
        with patch.object(self.cards, "_open_and_capture", return_value={"state": "qr", "png": next_image, "cdp": driver,
                                                                        "session": "new", "target": "new-tab"}):
            result = await self.cards._issue(bl.CHANNELS, {}, {}, True, "agent", None, lambda: True,
                                             restricted=True, owner=self.binding)
        self.assertEqual(result["result"], "failed")
        self.assertFalse(self.cards.cards)
        driver.close.assert_called_once()

    async def test_reconnect_using_identical_pairing_and_cid_cannot_inherit_request(self):
        c = self.fixture_card()
        fresh = self.session(self.phone.cid)
        self.assertEqual((fresh.device, fresh.sign_pub), (self.phone.device, self.phone.sign_pub))
        with patch.object(approval_material, "verified_phone_chat", return_value=self.proof), \
                patch.object(self.cards, "request", new=AsyncMock(return_value={"result": "failed"})), \
                patch.object(self.host, "send_app", new_callable=AsyncMock) as send:
            await self.cards.request_from_agent({"site": bl.CHANNELS}, 41)
            send.assert_not_awaited()
        msg = {"t": "login_qr", "id": c["id"], "site": bl.CHANNELS}
        self.assertFalse(await self.host.send_app(fresh, msg))
        self.host._op.assert_not_awaited()

    async def test_mid_frame_revocation_blocks_remaining_fragments_and_replay(self):
        c = self.fixture_card()
        msg = {"t": "login_qr", "id": c["id"], "site": bl.CHANNELS}
        async def change_pairing(*_):
            self.st.add_device(self.phone.pub, "replacement", b'r' * 32)
        self.host._op.side_effect = change_pairing
        with patch.object(wire, "frag_split", return_value=[{"t": "fixture"}] * 2):
            self.assertFalse(await self.host.send_app(self.phone, msg))
        self.assertTrue(c["attempted"])
        self.assertFalse(c.get("delivered"))
        self.host._op.assert_awaited_once()
        self.assertFalse(await self.host.send_app(self.phone, msg))

    async def test_refresh_receipt_rechecks_pairing_revision_inside_send_lock(self):
        c = self.fixture_card()
        self.cards._remember(c["id"], {"site": bl.CHANNELS, "result": "pending"})
        self.host.send_app = AsyncMock()
        async with self.host.send_lock:
            with patch.object(self.cards, "request", new=AsyncMock(return_value={"result": "no_qr"})):
                task = asyncio.create_task(self.cards.on_phone(self.phone, {
                    "t": "login_qr_refresh", "site": bl.CHANNELS, "id": c["id"]}))
                await asyncio.sleep(0)
                self.st.add_device(self.phone.pub, "same keys", self.phone.sign_pub)
        await task
        self.host.send_app.assert_not_awaited()

    async def test_first_failure_suppressed_if_chat_changes_under_lock(self):
        started = asyncio.Event()
        self.host.send_app = AsyncMock()
        async def fail(*args, **kwargs):
            started.set()
            return {"result": "failed"}
        with patch.object(approval_material, "verified_phone_chat", return_value=self.proof), \
                patch.object(self.cards, "request", side_effect=fail):
            async with self.host.send_lock:
                task = asyncio.create_task(self.cards.request_from_agent({"site": bl.CHANNELS}, 41))
                await asyncio.wait_for(started.wait(), 1)
                self.host.hist.epoch += 1
            await task
        self.host.send_app.assert_not_awaited()

    async def test_wire_replaces_stale_ttl_with_remaining_host_lifetime(self):
        c = self.fixture_card()
        c["deadline"] = time.monotonic() + 3
        msg = {"t": "login_qr", "id": c["id"], "site": bl.CHANNELS, "ttl": 120}
        with patch.object(wire, "frag_split", wraps=wire.frag_split) as split:
            self.assertTrue(await self.host.send_app(self.phone, msg))
        self.assertIn(split.call_args.args[0]["ttl"], (1, 2, 3))

    async def test_expiry_under_send_lock(self):
        c = self.fixture_card()
        msg = {"t": "login_qr", "id": c["id"], "site": bl.CHANNELS}
        async with self.host.send_lock:
            task = asyncio.create_task(self.host.send_app(self.phone, msg))
            await asyncio.sleep(0)
            c["deadline"] = 0
        self.assertFalse(await task)
        self.host._op.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
