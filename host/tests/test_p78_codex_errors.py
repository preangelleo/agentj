"""P78 native Codex failure guidance and rejected-model controls."""
import _hermetic
import asyncio
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj.agent import codex_failure
from agentj.agent_codex import CodexAgent, RPCError

MODEL_ERROR = "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account."

class Classify(unittest.TestCase):
    def test_account_model_bilingual_and_original_details(self):
        for lang, words in (("zh", "点顶部模型名换一个"), ("en", "Tap the model name")):
            kind, text, model = codex_failure({"detail": MODEL_ERROR}, None, lang)
            self.assertEqual((kind, model), ("model", "gpt-6.1-sol"))
            self.assertIn(words, text); self.assertIn(MODEL_ERROR, text)
            self.assertIn("```text", text)
    def test_network_and_auth_guidance(self):
        for raw in ("workspace routing discovery failed", "connection timed out", "DNS lookup failed"):
            self.assertEqual(codex_failure(raw, None)[0], "network")
            self.assertIn("检查代理/VPN 后重发", codex_failure(raw, None)[1])
        for raw in ("HTTP 401", "Unauthorized", "authentication failed", "token expired"):
            self.assertEqual(codex_failure(raw, None)[0], "auth")
            self.assertIn("codex login", codex_failure(raw, None, "en")[1])
    def test_details_are_bounded_redacted_and_do_not_break_fence(self):
        raw = "workspace routing discovery failed Authorization: Bearer TEST_ONLY_FAKE_SECRET_1234567890\n```" + "x" * 5000
        text = codex_failure(raw, None)[1]
        self.assertNotIn("TEST_ONLY_FAKE_SECRET_1234567890", text)
        self.assertLess(len(text), 4400); self.assertEqual(text.count("```"), 2)
    def test_rpc_nested_detail_not_lost_to_old_200_character_truncation(self):
        err = RPCError({"message": "x" * 250, "data": {"detail": MODEL_ERROR}})
        self.assertEqual(codex_failure(str(err), None)[2], "gpt-6.1-sol")

class NativeFailure(unittest.IsolatedAsyncioTestCase):
    def make_agent(self):
        a = object.__new__(CodexAgent)
        a.cfg = {"model": "gpt-6.1-sol"}; a.thread = {}; a.human = {}; a.models_cache = [{"id":"gpt-6.1-sol","name":"Sol","efforts":["high"]}, {"id":"gpt-6","name":"GPT-6"}]
        a.unavailable_models = set(); a.halting = False; a.tid = "thread"; a.turn_done = asyncio.Event()
        a.host = SimpleNamespace(lang="zh", models_changed=Mock(), turn_failed=Mock(), agent_notice=Mock(), set_model=Mock(), set_effort=Mock(), st=Mock())
        a.proc = object(); a.call = AsyncMock(return_value={"data":[{"id":"gpt-6.1-sol","displayName":"Sol"},{"id":"gpt-6"}]})
        a.tasks = set(); a.meter = Mock(); a.persist = True; a.research = False
        return a
    async def test_failed_turn_marks_model_and_keeps_original(self):
        a = self.make_agent()
        a._on_note("turn/completed", {"threadId":"thread","turn":{"status":"failed","error":{"message":MODEL_ERROR}}})
        self.assertTrue(a.turn_done.is_set())
        a.host.turn_failed.assert_called_once()
        self.assertIn("详情", a.host.agent_notice.call_args.args[0])
        self.assertIn("gpt-6.1-sol", a.unavailable_models)
        self.assertTrue(a.models_cache[0]["disabled"])
        await a.refresh_models()
        self.assertTrue(a.models_cache[0]["disabled"])
        self.assertEqual(await a.apply_model("gpt-6.1-sol", None), "unsupported")
        result = await a.cmd_model("gpt-6.1-sol")
        self.assertEqual(result.kind, "error"); a.host.set_model.assert_not_called()
        result = await a.cmd_model("")
        self.assertTrue(result.models[0]["disabled"])
    async def test_network_does_not_blacklist_model_and_respects_language(self):
        a = self.make_agent(); a.host.lang = "en"
        a.fail_notice("workspace routing discovery failed")
        self.assertEqual(a.unavailable_models, set())
        self.assertIn("Check your proxy/VPN", a.host.agent_notice.call_args.args[0])
    async def test_account_refresh_clears_only_native_rejection_memory(self):
        a = self.make_agent(); a.fail_notice(MODEL_ERROR)
        a._on_note("account/updated", {})
        await asyncio.gather(*list(a.tasks))
        self.assertEqual(a.unavailable_models, set())
        self.assertFalse(a.models_cache[0]["disabled"])
    async def test_default_and_effort_only_do_not_reselect_rejected_model(self):
        a = self.make_agent(); a.fail_notice(MODEL_ERROR); a.human["model"]="gpt-6.1-sol"
        self.assertEqual(await a.apply_model(None, None, default=True), "unsupported")
        self.assertEqual(await a.model_set({"model":"gpt-6.1-sol"}), "unsupported")
        self.assertEqual(await a.apply_model(None, "high"), "unsupported")
        self.assertIsNone(await a.apply_model("gpt-6", None))
        a.host.set_model.assert_called_once_with("gpt-6")
