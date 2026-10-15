"""P309: Codex behind a third-party relay (agentsrelay etc.) — a 429 / limit must point at the relay's dashboard, not at
ChatGPT → Settings → Usage; a bare 429 is a momentary limit (one automatic retry), not a used-up allowance; a thread loaded
before config.toml changed is told to start a new conversation.

The relay 429 sample is what Codex 0.162.1's app-server really sends when a custom provider answers 429 (measured against a
local mock relay: the relay's own body is dropped)."""
import _hermetic  # noqa: F401
import asyncio
import os
import pathlib
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import codex_perm
from agentj import agent_codex
from agentj.agent import codex_failure
from agentj.agent_codex import CodexAgent

RELAY_429 = ('Codex 这一轮没有正常完成：exceeded retry limit, last status: 429 Too Many Requests\n'
             '{"responseTooManyFailedAttempts": {"httpStatusCode": 429}}')
OFFICIAL_USAGE = "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), or try again at 3:05 PM."
RELAY = {"id": "agentsrelay", "host": "api.agentsrelay.example", "relay": True}
OPENAI = {"id": "openai", "host": None, "relay": False}


def first(text):
    return text.split("\n\n")[0]


class Provider(unittest.TestCase):
    def home(self, toml, **env):
        d = tempfile.mkdtemp(prefix="p309-codex-")
        if toml is not None:
            pathlib.Path(d, "config.toml").write_text(toml, encoding="utf-8")
        return {"CODEX_HOME": d, **env}

    def test_official_by_default(self):
        self.assertEqual(codex_perm.provider(env=self.home(None)), {"id": "openai", "host": None, "relay": False})
        self.assertFalse(codex_perm.provider(env=self.home('openai_base_url = "https://api.openai.com/v1"'))["relay"])

    def test_custom_provider_is_a_relay_and_only_the_host_comes_back(self):
        env = self.home('model_provider = "ar"\n[model_providers.ar]\nname = "AR"\n'
                        'base_url = "https://api.agentsrelay.example/v1?key=sk-SECRET"\nenv_key = "AR_KEY"\n')
        p = codex_perm.provider(env=env)
        self.assertEqual(p, {"id": "ar", "host": "api.agentsrelay.example", "relay": True})
        self.assertNotIn("SECRET", repr(p))

    def test_profile_and_codex_answer_and_service_env(self):
        env = self.home('profile = "r"\n[profiles.r]\nmodel_provider = "x"\n[model_providers.x]\nbase_url = "https://x.example/v1"\n')
        self.assertEqual(codex_perm.provider(env=env)["host"], "x.example")
        self.assertFalse(codex_perm.provider("openai", env=env)["relay"])        # what Codex itself reported wins
        env = self.home(None, OPENAI_BASE_URL="https://relay.example/v1")         # built-in openai pointed elsewhere
        self.assertEqual(codex_perm.provider(env=env), {"id": "openai", "host": "relay.example", "relay": True})

    def test_broken_toml_is_official_not_a_crash(self):
        self.assertFalse(codex_perm.provider(env=self.home("= = ="))["relay"])


class Wording(unittest.TestCase):
    def test_relay_429_names_the_relay_not_chatgpt(self):
        for lang, want in (("zh", "中转服务 api.agentsrelay.example"), ("en", "relay service api.agentsrelay.example")):
            kind, text, _ = codex_failure(RELAY_429, None, lang, RELAY)
            self.assertEqual(kind, "rate")
            line = first(text)
            self.assertIn(want, line)
            self.assertNotIn("ChatGPT 设置", line); self.assertNotIn("ChatGPT → Settings", line)
            self.assertIn("/clear", line)
            self.assertIn("provider: agentsrelay (api.agentsrelay.example) · relay", text)   # in the Details
            self.assertIn("429 Too Many Requests", text.split("```text")[1])

    def test_official_429_is_momentary_not_used_up(self):
        kind, text, _ = codex_failure(RELAY_429, None, "zh", OPENAI)
        self.assertEqual(kind, "rate")
        self.assertIn("OpenAI 返回了 429", first(text)); self.assertIn("等一两分钟", first(text))
        self.assertNotIn("用量到上限了（", first(text))
        self.assertIn("后台服务没读到中转配置", first(text))     # the "my other computer works" case: serve on OpenAI itself
        self.assertNotIn("/clear", first(text))                 # not stale, not a relay

    def test_retried_says_so(self):
        self.assertIn("已自动重试过一次", first(codex_failure(RELAY_429, None, "zh", RELAY, retried=True)[1]))
        self.assertIn("already retried once", first(codex_failure(RELAY_429, None, "en", OPENAI, retried=True)[1]))

    def test_usage_wording_official_vs_relay(self):
        kind, text, _ = codex_failure(OFFICIAL_USAGE, None, "zh", OPENAI)
        self.assertEqual(kind, "usage"); self.assertIn("Codex 用量到上限", first(text)); self.assertIn("3:05 PM", first(text))
        kind, text, _ = codex_failure("usage_limit_reached: weekly limit, try again in 2 hours", None, "zh", RELAY)
        self.assertEqual(kind, "usage")
        self.assertIn("中转服务 api.agentsrelay.example 返回了用量/限额上限", first(text))
        self.assertIn("2 hours", first(text)); self.assertNotIn("ChatGPT 设置", first(text))

    def test_relay_quota_and_key(self):
        for raw in ('unexpected status 429: {"error":{"code":"insufficient_user_quota","message":"user quota is not enough"}}',
                    "余额不足，请充值"):
            kind, text, _ = codex_failure(raw, None, "zh", RELAY)
            self.assertEqual(kind, "quota"); self.assertIn("中转服务", first(text)); self.assertNotIn("platform.openai.com", first(text))
        self.assertIn("platform.openai.com", first(codex_failure("insufficient_quota", None, "zh", OPENAI)[1]))
        kind, text, _ = codex_failure("unexpected status 401 Unauthorized: invalid api key", None, "zh", RELAY)
        self.assertEqual(kind, "auth"); self.assertIn("[model_providers.agentsrelay]", first(text))
        self.assertNotIn("codex login", first(text))

    def test_relay_throttle_words(self):
        for raw in ("当前分组上游负载已饱和，请稍后再试", "Rate limit reached for requests"):
            self.assertEqual(codex_failure(raw, None, "zh", RELAY)[0], "rate")

    def test_stale_thread_points_to_a_new_conversation(self):
        self.assertIn("/clear", first(codex_failure(OFFICIAL_USAGE, None, "zh", OPENAI, stale=True)[1]))
        self.assertNotIn("/clear", first(codex_failure(OFFICIAL_USAGE, None, "zh", OPENAI)[1]))

    def test_no_provider_keeps_the_old_shape(self):
        kind, text, _ = codex_failure(OFFICIAL_USAGE, None, "zh")
        self.assertEqual(kind, "usage"); self.assertNotIn("provider:", text); self.assertNotIn("中转", first(text))


def bare_agent():
    a = object.__new__(CodexAgent)
    a.cfg = {"model": "gpt-6"}; a.thread = {"modelProvider": "agentsrelay"}; a.human = {}; a.models_cache = []
    a.unavailable_models = set(); a.halting = False; a.tid = "t"; a.meter = Mock(); a.tasks = set(); a.thread_at = None
    a.rate_retry, a.rate_retried = False, False
    a.open_items, a.said, a.last_note, a.tail_cut, a.pending = set(), False, 0.0, None, {}   # P282 turn-tail fields (P290 merge)
    a.host = SimpleNamespace(lang="zh", turn_failed=Mock(), agent_notice=Mock(), models_changed=Mock(),
                             st=SimpleNamespace(log=Mock()))
    return a


FAILED_429 = {"threadId": "t", "turn": {"status": "failed", "error": {
    "message": "exceeded retry limit, last status: 429 Too Many Requests",
    "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}}}}}


class Agent(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(codex_perm, "provider", return_value=RELAY)
        p.start(); self.addCleanup(p.stop)

    def test_notice_uses_the_threads_provider(self):
        a = bare_agent()
        a.turn_done = asyncio.Event()
        a._on_note("turn/completed", FAILED_429)
        codex_perm.provider.assert_called_with("agentsrelay")
        self.assertIn("中转服务 api.agentsrelay.example", a.host.agent_notice.call_args.args[0])

    def test_one_automatic_retry_then_the_notice(self):
        async def go(second_fails):
            a = bare_agent()
            a.proc = a.turn_proc = object()
            a.RATE_RETRY_WAIT = 0
            calls = []

            async def call(method, params, timeout=None):
                calls.append((method, params))
                if method == "turn/start":
                    asyncio.get_running_loop().call_soon(
                        a._on_note, "turn/completed",
                        FAILED_429 if second_fails else {"threadId": "t", "turn": {"status": "completed"}})
                return {"turn": {"id": "x"}}
            a.call = call
            a.rate_retry, a.turn_done = None, asyncio.Event()
            a._on_note("turn/completed", FAILED_429)        # the first 429: held back, no notice yet
            self.assertFalse(a.host.agent_notice.called)
            why, a.rate_retry = a.rate_retry, False
            await a._retry_rate(why)
            return a, calls
        a, calls = asyncio.run(go(second_fails=False))
        self.assertEqual([c[0] for c in calls], ["turn/start"])
        self.assertIn("自动重试", calls[0][1]["input"][0]["text"])
        self.assertFalse(a.host.agent_notice.called)
        self.assertEqual((a.rate_retry, a.rate_retried), (False, False))
        a, calls = asyncio.run(go(second_fails=True))
        self.assertEqual(len(calls), 1)                      # once, never a loop
        self.assertIn("已自动重试过一次", a.host.agent_notice.call_args.args[0])

    def test_non_rate_failure_is_told_at_once(self):
        a = bare_agent()
        a.rate_retry, a.turn_done = None, asyncio.Event()
        a._on_note("turn/completed", {"threadId": "t", "turn": {"status": "failed", "error": {
            "message": "unexpected error", "codexErrorInfo": "usageLimitExceeded"}}})
        self.assertTrue(a.host.agent_notice.called)
        self.assertIsNone(a.rate_retry)

    def test_config_changed_after_the_thread_loaded_is_stale(self):
        d = tempfile.mkdtemp(prefix="p309-stale-")
        cfg = pathlib.Path(d, "config.toml"); cfg.write_text("", encoding="utf-8")
        a = bare_agent()
        with mock.patch.dict(os.environ, {"CODEX_HOME": d}):
            a.thread_at = time.time() + 60
            self.assertFalse(a.config_stale())
            a.thread_at = time.time() - 60
            self.assertTrue(a.config_stale())
            a.tid = None
            self.assertFalse(a.config_stale())

    def test_retry_text_is_constant(self):
        self.assertIn("Agent J", agent_codex.RATE_RETRY_TEXT)


if __name__ == "__main__":
    unittest.main()
