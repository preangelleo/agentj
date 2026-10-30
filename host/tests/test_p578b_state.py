"""P578b: rollback state, stale producers and native OpenCode default projection; no live provider calls."""
import _hermetic  # noqa: F401
import asyncio
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import agent, agent_opencode as oc, agent_opencode2 as oc2, serve, switch_harness as sw
from test_p155_switch_harness import Clock, mk_state
from test_p423_146 import _agent, PROV


class StateConsistency(unittest.TestCase):
    def setUp(self):
        p = mock.patch("agentj.working_root.record")
        p.start()
        self.addCleanup(p.stop)

    def test_old_opencode_cannot_overwrite_restored_codex_model_or_effort(self):
        with tempfile.TemporaryDirectory() as d:
            st = mk_state(d, "codex")
            raw = sw._raw_agent(st)
            sw._apply(st, "opencode", raw["dir"], "ark-agent-plan/ark", "high", raw)
            old = st.agent_config()
            sw._restore(st, raw)
            h = SimpleNamespace(st=st, agent_cfg=old)
            self.assertFalse(serve.Host.set_model(h, "openrouter/broken"))
            self.assertFalse(serve.Host.set_effort(h, "low"))
            self.assertEqual(sw._raw_agent(st), raw)
            self.assertEqual(old["model"], "ark-agent-plan/ark")

    def test_new_model_write_still_updates_disk_and_runtime_together(self):
        with tempfile.TemporaryDirectory() as d:
            st = mk_state(d, "codex")
            h = SimpleNamespace(st=st, agent_cfg=st.agent_config())
            self.assertTrue(serve.Host.set_model(h, "gpt-6.1"))
            self.assertEqual(st.agent_config()["model"], h.agent_cfg["model"])

    def test_wait_up_refuses_right_harness_wrong_model(self):
        c = Clock()
        ctl = lambda req: {"ok": True, "agent": "opencode", "agent_model": "openrouter/broken", "agent_status": "idle"}
        self.assertEqual(sw._wait_up(ctl, "opencode", c.sleep, c, limit=2, model="ark-agent-plan/ark"), "config_mismatch")

    def test_old_or_workflow_adapter_cannot_publish_topbar(self):
        h = SimpleNamespace(agent=None, meter_update=mock.Mock())
        a = agent.Agent(h, {"dir": str(pathlib.Path.cwd()), "model": "old"})
        h.agent = a
        a.meter(model="old")
        h.meter_update.assert_called_once_with(model="old")
        h.meter_update.reset_mock()
        h.agent = object()
        a.meter(model="old")
        h.meter_update.assert_not_called()
        a.cfg["_workflow_ceo"] = True
        h.agent = None
        a.meter(model="avatar")
        h.meter_update.assert_not_called()

    def test_native_default_model_is_visible_without_persisting_a_different_choice(self):
        a = _agent()
        a.cfg["model"] = None
        a.session_model = {"providerID": "openrouter", "id": "broken"}
        self.assertEqual(a.cur_model(), "openrouter/broken")
        self.assertIsNone(a.cfg["model"])
        a.cfg["model"] = "ark-agent-plan/ark"
        self.assertEqual(a.cur_model(), "ark-agent-plan/ark")

    def test_no_key_has_phone_recovery_even_without_other_provider(self):
        a = _agent()
        a.usable_models = []
        text = a.provider_note("no_key", "openrouter", "")
        self.assertIn("设置 → 紧急修复", text)
        self.assertIn("网页终端", text)
        self.assertIn("Codex", text)
        self.assertIn("[切回 Codex](reply:切回 Codex)", text)

    def test_zero_keys_select_free_before_first_prompt_in_both_protocols(self):
        for cls in (oc.OpenCodeAgent, oc2.OpenCodeV2Agent):
            for lang in ("zh", "en"):
                with self.subTest(cls=cls, lang=lang):
                    a = _agent(lang=lang)
                    a.__class__ = cls
                    a.connected_at_start = []
                    a.host.set_model = mock.Mock(return_value=True)
                    a.host.meter_update = mock.Mock()
                    a.client = SimpleNamespace()
                    catalogue = {"providers": [{"id": "opencode", "models": {
                        **{f"other-{n}": {} for n in range(50)},
                        "other-free": {}, "glm-5.3-flash-free": {}, "paid": {}}}], "connected": [],
                        "anonymous_models": ["opencode/other-free", "opencode/glm-5.3-flash-free"]}
                    a._providers = mock.AsyncMock(return_value=catalogue)
                    asyncio.run(a._free_before_prompt())
                    self.assertEqual(a.cur_model(), "opencode/glm-5.3-flash-free")
                    self.assertIn("API Key", a.notices[-1])
                    self.assertIn("免费模型" if lang == "zh" else "free model", a.notices[-1])
                    self.assertEqual(a.host.meter_update.call_args.kwargs["model"], a.cur_model())

    def test_auto_ark_recovery_survives_final_switch_verification(self):
        from test_p155_switch_harness import UP, FakeServe, fake_probe
        with tempfile.TemporaryDirectory() as d:
            st, c = mk_state(d, "codex"), Clock()
            f = FakeServe(st, "codex")
            def ctl(req):
                result = f(req)
                if req["cmd"] == "harness_hello":
                    st.set_agent_model("ark-agent-plan/ark")
                if req["cmd"] == "status" and f.hellos:
                    result.update(agent_model="ark-agent-plan/ark", harness_readiness={
                        "model_recovery": {"from": "openrouter/broken", "to": "ark-agent-plan/ark"}})
                return result
            with mock.patch.object(sw, "receipt_path", return_value=pathlib.Path(d) / "receipt.json"):
                out = sw.worker(st, "opencode", "openrouter/broken", probe=fake_probe(), ctl=ctl,
                                restart=f.restart, status=UP, ready=lambda st, limit: "idle", sleep=c.sleep, clock=c)
                self.assertEqual(out, 0)
                self.assertEqual(sw.read_receipt(st)["model"], "ark-agent-plan/ark")
            self.assertEqual(st.agent_config()["model"], "ark-agent-plan/ark")
            self.assertEqual(f.restarts, 1)

    def test_malformed_free_catalogue_fails_closed(self):
        catalogue = {"providers": [{"id": "opencode", "models": {"glm-5.3-flash-free": {}}}],
                     "connected": ["opencode"], "anonymous_models": ["opencode/glm-5.3-flash-free"]}
        for field in catalogue:
            for malformed in (None, 1, "opencode", {}):
                with self.subTest(field=field, malformed=malformed):
                    a = _agent()
                    a.connected_at_start = []
                    a.host.set_model = mock.Mock(return_value=True)
                    a._providers = mock.AsyncMock(return_value={**catalogue, field: malformed})
                    asyncio.run(a._free_before_prompt())
                    a.host.set_model.assert_not_called()
                    self.assertEqual(a.notices, [])

    def test_zero_keys_first_turn_posts_once_with_free_model(self):
        for cls in (oc.OpenCodeAgent, oc2.OpenCodeV2Agent):
            async def run():
                a = _agent()
                a.__class__ = cls
                a.cfg["model"] = None
                a.session_model = {"providerID": "openrouter", "id": "broken"}
                a.connected_at_start, a.collect, a.proc = [], [], object()
                a.host.set_model = lambda m: a.cfg.update(model=m)
                a._note_turn = mock.Mock()
                for name, result in (("_protocol", False), ("_creds_check", None), ("_ensure_started", True),
                                     ("_session_ok", True), ("_identity", None), ("_model", None),
                                     ("_reminder", None), ("_catch_up", None)):
                    setattr(a, name, mock.AsyncMock(return_value=result))
                a._providers = mock.AsyncMock(return_value={"providers": [{"id": "opencode", "models": {
                    "glm-5.3-flash-free": {}}}], "connected": ["opencode"],
                    "anonymous_models": ["opencode/glm-5.3-flash-free"]})
                async def request(method, path, body):
                    self.assertEqual(a.cfg["model"], "opencode/glm-5.3-flash-free")
                    if not a.v2:
                        self.assertEqual(body["model"]["modelID"], "glm-5.3-flash-free")
                    return 200, {}
                a.client = SimpleNamespace(request=mock.AsyncMock(side_effect=request))
                async def deliver(post):
                    return await post()
                a.deliver = deliver
                a._wait_turn = mock.AsyncMock(side_effect=lambda: a.notices.append("ECHO: first"))
                with mock.patch("agentj.main_identity.audit"):
                    await a.turn("first")
                self.assertIn("ECHO: first", a.notices)
                a.client.request.assert_awaited_once()
                self.assertIsNone(a.model_recovery["from"])
            with self.subTest(cls=cls):
                asyncio.run(run())

    def test_v2_auth_failure_offers_connected_ark_card_and_projects_failed_default(self):
        a = _agent()
        a.__class__ = oc2.OpenCodeV2Agent
        a.cfg["model"] = None
        a.session_model = {"providerID": "openrouter", "id": "broken"}
        a.host.meter_update = mock.Mock()
        with mock.patch("agentj.provider_runtime.configured", return_value={}):
            a._set_models({"providers": [{"id": "ark-agent-plan", "models": {"ark": {"name": "Ark"}}}],
                           "connected": ["ark-agent-plan"]})
        a._failed({"type": "provider.auth", "message": "missing key", "providerID": "openrouter", "modelID": "broken"})
        self.assertTrue(a.cards)
        self.assertEqual(a.cards[0][1].models[0]["id"], "ark-agent-plan/ark")
        self.assertEqual(a.cur_model(), "openrouter/broken")

    def test_missing_openrouter_automatically_uses_connected_ark_and_next_turn_model(self):
        a = _agent()
        a.__class__ = oc2.OpenCodeV2Agent
        a.cfg["model"] = None
        a.session_model = {"providerID": "openrouter", "id": "broken"}
        a.connected_at_start = ["ark-agent-plan"]
        a.host.set_model = mock.Mock(return_value=True)
        a.host.meter_update = mock.Mock()
        with mock.patch("agentj.provider_runtime.configured", return_value={}):
            a._set_models({"providers": [{"id": "ark-agent-plan", "models": {"ark": {"name": "Ark"}}}],
                           "connected": ["ark-agent-plan"]})
        a._failed({"type": "provider.auth", "message": "missing key", "providerID": "openrouter", "modelID": "broken"})
        a.host.set_model.assert_called_once_with("ark-agent-plan/ark")
        self.assertEqual(a.cur_model(), "ark-agent-plan/ark")
        self.assertIn("已切到", a.notices[-1])
        a.host.meter_update.assert_called_with(model="ark-agent-plan/ark", model_name="Ark", ctx=None, quota_windows=[])
        a.client = SimpleNamespace(request=mock.AsyncMock(return_value=(200, {})))
        asyncio.run(a._model())
        self.assertEqual(a.session_model, {"providerID": "ark-agent-plan", "id": "ark"})
        self.assertEqual(a.client.request.call_count, 1)

    def test_invalid_connected_key_does_not_silently_switch_paid_provider(self):
        a = _agent()
        a.connected_at_start = ["openrouter", "zhipuai"]
        a.host.set_model = mock.Mock()
        self.assertFalse(a._recover_model("login", "openrouter"))
        a.host.set_model.assert_not_called()

    def test_automatic_recovery_requires_owned_main_route_and_successful_cas(self):
        for change in ("unknown_pid", "empty_pid", "attached", "workflow", "transient", "unknown_connections", "cas_failed"):
            with self.subTest(change=change):
                a = _agent()
                a.connected_at_start = ["zhipuai"]
                a.host.set_model = mock.Mock(return_value=change != "cas_failed")
                a.host.meter_update = mock.Mock()
                pid = "openrouter"
                if change == "unknown_pid": pid = None
                if change == "empty_pid": pid = ""
                if change == "attached": a.cfg["session_mode"] = "shared"
                if change == "workflow": a.cfg["_workflow_ceo"] = True
                if change == "transient": a.persist = False
                if change == "unknown_connections": a.connected_at_start = None
                self.assertFalse(a._recover_model("login", pid))
                self.assertEqual(a.notices, [])
                a.host.meter_update.assert_not_called()

    def test_switch_does_not_request_a_second_working_root_ack(self):
        with tempfile.TemporaryDirectory() as d:
            st = mk_state(d, "codex")
            raw = sw._raw_agent(st)
            with mock.patch('agentj.working_root.record', side_effect=RuntimeError('host ACK unavailable')) as record:
                sw._apply(st, "opencode", raw["dir"], "ark-agent-plan/ark", "high", raw)
            record.assert_not_called()
            self.assertEqual(sw._raw_agent(st)["effort"], "high")
            self.assertEqual(st.agent_config()["kind"], "opencode")

    def test_apply_exception_after_disk_write_restores_whole_previous_entry(self):
        from test_p155_switch_harness import UP, FakeServe, fake_probe
        with tempfile.TemporaryDirectory() as d:
            st = mk_state(d, "codex", model="gpt-6.1", effort="high")
            raw = sw._raw_agent(st)
            f, c = FakeServe(st, "codex"), Clock()
            real_apply = sw._apply
            def write_then_raise(*args):
                real_apply(*args)
                raise OSError('post-write failure')
            with mock.patch.object(sw, '_apply', side_effect=write_then_raise), \
                    mock.patch.object(sw, 'receipt_path', return_value=pathlib.Path(d) / 'receipt.json'):
                out = sw.worker(st, "opencode", "ark-agent-plan/ark", probe=fake_probe(), ctl=f,
                                restart=f.restart, status=UP, ready=lambda st, limit: "idle", sleep=c.sleep, clock=c)
            self.assertEqual(out, 1)
            self.assertEqual(sw._raw_agent(st), raw)
            self.assertEqual(f.kind, "codex")
            self.assertEqual(f.restarts, 0)
            self.assertEqual(sw.read_receipt(st)["status"], "failed")
