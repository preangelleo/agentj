"""Daily health notices use the established Settings rescue with a host-owned target."""
import asyncio
import time
from types import MethodType, SimpleNamespace
from unittest import mock

from test_p0269_login import Base, CODE, GOOD

from agentj import auth_rescue as ar, history, provenance, serve, wire


class HealthNotice(Base):
    def setUp(self):
        super().setUp()
        host = self.host
        host.agent = SimpleNamespace(kind="codex")
        host.hist = history.History(self.st)
        host.kp = SimpleNamespace(pub=b"test-host")
        host.ask_ttl = 30
        host.stopped = lambda: False
        host.stopping = asyncio.Event()
        for name in ("emit", "_post", "_send_legacy", "hist_emit", "_remember"):
            setattr(host, name, mock.Mock())
        for name in ("hist_add", "hist_update", "agent_notice", "_paired_live", "on_auth"):
            setattr(host, name, MethodType(getattr(serve.Host, name), host))
        pub = wire.unb64u(self.st.devices()[self.keys["phoneA"][0]]["pub"])
        self.remote = serve.Session(1, state="ready", pub=pub, device=wire.device_id(pub),
                                    p33=True, provenance=provenance.phone())
        host.sessions = {1: self.remote}
        host.send_app = mock.AsyncMock()
        host.ask = mock.AsyncMock(return_value={"behavior": "allow"})
        self.rescue = host.auth_rescue
        self.store = self.tmp / "config" / "fake-service.env"
        svc = mock.patch.object(ar, "service_env_path", return_value=str(self.store))
        svc.start()
        self.addCleanup(svc.stop)

    async def daily_notice(self):
        with mock.patch.object(ar, "health_targets", return_value=["claude"]):
            result = await self.rescue.health_daily(force=True)
        self.assertEqual(result["claude"]["cls"], "login")
        return self.host.hist.get(self.host.hist.meta()["last"])

    async def request(self, **fields):
        await self.host.on_auth(self.remote, "auth_start", {"r": "card", **fields})
        await self.drain()
        return self.host.send_app.await_args.args[1]

    @staticmethod
    def fields(page):
        hint = page["login"]
        return {"kind": hint["agent"], "notice": page["id"], "health": hint["health"]}

    async def test_daily_card_recovers_non_main_claude_through_settings(self):
        page = await self.daily_notice()
        rescued = []

        async def secret(card):
            self.assertEqual(card["only"], self.remote.device)
            self.assertEqual(card["auth"]["kind"], "claude")
            result = await asyncio.to_thread(card["sink"], bytearray(CODE.encode()))
            rescued.append(result["result"])
            return result

        self.host.elevate = SimpleNamespace(request=secret)
        hint = page["login"]
        if hint.get("health"):
            with mock.patch.object(ar, "screen_check", return_value={"answered": []}), \
                    mock.patch("agentj.harness.probe", return_value={"harnesses": [{"name": "claude", "callable": True}]}):
                answer = await self.request(**self.fields(page))
        else:
            await self.phone("phoneA", {"action": "login", "agent": hint["agent"]})
            answer = self.views("phoneA")[-1]
        self.assertTrue(answer["ok"], f"card refused: {answer.get('why')}")
        self.assertEqual(rescued, ["saved"])
        self.assertEqual(self.rescue.last["claude"]["result"], "recovered")
        self.assertEqual(self.rescue.current(), "codex")
        self.assertTrue(self.store.is_file())
        self.assert_code_nowhere()
        self.host.ask.assert_awaited_once()
        args, kwargs = self.host.ask.await_args
        self.assertEqual(args[0], "AuthRescue")
        self.assertTrue(kwargs["always_ask"] and kwargs["require_passkey"])
        self.assertIs(kwargs["_settings_session"], self.remote)

    async def test_changed_target_and_unissued_notice_are_rejected(self):
        page = await self.daily_notice()
        unrelated = self.host.agent_notice("A normal test notice")
        base = self.fields(page)
        for overrides in ({"kind": "codex"}, {"notice": unrelated}, {"notice": True},
                          {"health": "0" * 16}, {"health": False}, {"route": "login"},
                          {"account": {"name": "Provider"}}):
            with self.subTest(overrides=overrides):
                answer = await self.request(**(base | overrides))
                self.assertEqual((answer["ok"], answer["why"]), (False, "unsupported"))
        self.host.ask.assert_not_awaited()
        self.assertFalse(self.store.exists())

    async def test_fresh_uv_and_live_phone_are_required(self):
        page = await self.daily_notice()
        fields = self.fields(page)
        self.remote.provenance = None
        answer = await self.request(**fields)
        self.assertEqual((answer["ok"], answer["why"]), (False, "session_changed"))
        self.host.ask.assert_not_awaited()
        self.remote.provenance = provenance.phone()
        self.host.ask.return_value = {"behavior": "deny", "reason": "passkey_required"}
        answer = await self.request(**fields)
        self.assertEqual((answer["ok"], answer["why"]), (False, "passkey"))
        self.assertFalse(self.store.exists())

    async def test_same_number_from_restarted_ephemeral_history_is_not_the_same_notice(self):
        self.host.hist = history.History(self.st, on=False)
        first = await self.daily_notice()
        self.host.hist = history.History(self.st, on=False)
        self.rescue.alerted.clear()
        second = await self.daily_notice()
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["login"]["health"], second["login"]["health"])
        answer = await self.request(**self.fields(first))
        self.assertEqual((answer["ok"], answer["why"]), (False, "unsupported"))
        self.host.ask.assert_not_awaited()

    async def test_saved_but_not_verified_never_shows_expiry_countdown(self):
        await self.daily_notice()
        self.rescue.flow = {"kind": "claude", "stage": "verifying", "route": "setup_token"}
        result = await asyncio.to_thread(self.rescue._store_token, bytearray(GOOD.encode()), {})
        self.assertEqual(result["result"], "saved")
        row = self.rescue.view()["harnesses"][0]
        self.assertNotIn("token", row, f"old status {row['state']} alongside a new expiry")
        self.assertNotIn("health", row)
        self.rescue.clear("claude")
        self.rescue._end("claude", "recovered")
        row = self.rescue.view()["harnesses"][0]
        self.assertEqual((row["state"], row["health"]["ok"], row["token"]["days_left"]), ("ok", True, 365))

    async def test_newer_success_makes_daily_action_unavailable(self):
        page = await self.daily_notice()
        self.rescue._health_mark("claude", {"ok": True})
        self.rescue.clear("claude")
        answer = await self.request(**self.fields(page))
        self.assertEqual((answer["ok"], answer["why"]), (False, "unsupported"))
        self.host.ask.assert_not_awaited()

    async def test_newer_success_during_uv_stops_the_notice_before_spawn(self):
        page = await self.daily_notice()

        async def approve(*args, **kwargs):
            self.rescue._health_mark("claude", {"ok": True})
            self.rescue.clear("claude")
            return {"behavior": "allow"}

        self.host.ask.side_effect = approve
        answer = await self.request(**self.fields(page))
        self.assertEqual((answer["ok"], answer["why"]), (False, "unsupported"))
        self.assertFalse(self.host._auth_settings_requests)
        self.assertIsNone(self.rescue.flow)

    async def test_concurrent_successful_uv_does_not_replace_the_started_flow(self):
        page = await self.daily_notice()
        both_asked, approved, both_answered, finish = (asyncio.Event() for _ in range(4))
        answers, launches = [], []

        async def approve(*args, **kwargs):
            if self.host.ask.await_count == 2:
                both_asked.set()
            await approved.wait()
            return {"behavior": "allow"}

        async def send(remote, message):
            answers.append(message)
            if len(answers) == 2:
                both_answered.set()

        async def worker():
            launches.append((self.rescue.flow["kind"], self.rescue.flow["route"]))
            await finish.wait()

        self.host.ask.side_effect = approve
        self.host.send_app.side_effect = send
        with mock.patch.multiple(self.rescue, _codex=worker, _claude_pty=worker):
            try:
                await self.host.on_auth(self.remote, "auth_start", {"r": "card", **self.fields(page)})
                await self.host.on_auth(self.remote, "auth_start", {"r": "settings", "kind": "codex", "route": "device"})
                await asyncio.wait_for(both_asked.wait(), 3)
                approved.set()
                await asyncio.wait_for(both_answered.wait(), 3)
                allowed, = [answer for answer in answers if answer["ok"]]
                blocked, = [answer for answer in answers if not answer["ok"]]
                run = ("claude", "setup_token") if allowed["r"] == "card" else ("codex", "device")
                self.assertEqual(blocked["why"], "busy")
                self.assertEqual(launches, [run])
                self.assertEqual(len(self.host._auth_settings_requests), 1)
            finally:
                approved.set()
                finish.set()
                await self.drain()
        self.assertFalse(self.host._auth_settings_requests)

    async def test_notice_issues_one_bound_row_without_consuming_an_adapter_hint(self):
        pending = ({"agent": "codex", "why": "expired"}, time.monotonic())
        self.rescue.pending_login = pending
        page = await self.daily_notice()
        self.host.hist_emit.assert_called_once_with([page])
        self.assertEqual((page["login"]["agent"], page["login"]["why"]), ("claude", "expired"))
        self.assertRegex(page["login"]["health"], r"^[0-9a-f]{16}$")
        self.assertIs(self.rescue.pending_login, pending)
        self.assertEqual(history.History(self.st).get(page["id"])["login"], page["login"])
        self.rescue.pending_login = None
        self.assertEqual(await self.daily_notice(), page)
        self.assertIsNone(self.rescue.pending_login)
        self.host.hist_emit.assert_called_once()
