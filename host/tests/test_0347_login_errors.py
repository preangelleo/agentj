"""Status and recipient regressions with a synthetic clock and no browser, account, or real login material."""
import _hermetic  # noqa: F401
import asyncio
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
