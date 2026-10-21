# Synthetic fixtures retain identical runtime bytes; compose them so static hygiene never treats them as shipped keys.
"""P310: Alibaba Cloud Bailian Token Plan — the OpenCode preset (bailian.py: official block, never overwriting the owner's
config, key card verified on the read-only model list), the P279 route next to Kimi (prices + terms), the key kept away from
tasks / bots, and Qwen Code as the fifth harness on P305's ACP adapter (agent_qwen.py) against the stand-in fakeacp.py.
No network: the Bailian model list is a local fake service."""
import _hermetic  # noqa: F401
import asyncio
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
FAKE = str(HERE / "fakeacp.py")
os.chmod(FAKE, os.stat(FAKE).st_mode | 0o111)   # the public export writes files without the exec bit; the stand-in must run
SENTINEL = ('sk-' + 'p310-sentinel-0123456789abcdef')

from agentj import acp, agent_qwen, ai_route, bailian, elevate, fence, harness, opencode_provider as op  # noqa: E402
from test_p305_kimi import AcpCase  # noqa: E402


class ConfigHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"XDG_CONFIG_HOME": self.tmp.name}
        self.file = Path(op.config_file(self.env))

    def install(self, *a, **k):
        with mock.patch("agentj.service.env_file", lambda name: "/svc/agentj.env"):
            return bailian.install(*a, environ=self.env, **k)


class Preset(ConfigHome):
    def test_official_block_with_key_by_name(self):
        r = self.install()
        cfg = json.loads(self.file.read_text())
        b = cfg["provider"]["bailian-token-plan-personal"]
        self.assertEqual(b["npm"], "@ai-sdk/anthropic")
        self.assertEqual(b["options"], {"baseURL": "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic/v1",
                                        "apiKey": "{env:BAILIAN_TOKEN_PLAN_API_KEY}"})
        self.assertEqual(b["models"]["qwen3.8-max"]["modalities"]["input"], ["text", "image"])   # vision, as published
        self.assertNotIn("image", json.dumps(b["models"]["qwen3.7-max"]))                        # no vision on 3.7 Max
        self.assertEqual((r["model"], r["vision"], r["kept"], r["written"]),
                         ("bailian-token-plan-personal/qwen3.8-max", True, False, True))
        self.assertNotIn("model", cfg)                                  # never a top-level default
        self.assertIn("--verify-url https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models", r["next"][0])
        self.assertIn("--dest env:/svc/agentj.env#BAILIAN_TOKEN_PLAN_API_KEY", r["next"][0])
        self.assertEqual(r["next"][1], "agentj agent switch opencode --model bailian-token-plan-personal/qwen3.8-max")

    def test_owner_config_never_overwritten(self):
        self.file.parent.mkdir(parents=True)
        mine = {"$schema": "https://opencode.ai/config.json", "model": "zhipuai/glm-5.3", "provider": {
            "myrelay": {"npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "https://x.example/v1"}}}}
        self.file.write_text(json.dumps(mine))
        self.install(model="qwen3.8-flash")
        cfg = json.loads(self.file.read_text())
        self.assertEqual(cfg["model"], "zhipuai/glm-5.3")
        self.assertEqual(cfg["provider"]["myrelay"], mine["provider"]["myrelay"])
        self.assertEqual(json.loads((self.file.parent / "opencode.json.agentj-bak").read_text()), mine)
        own = {"npm": "@ai-sdk/anthropic", "options": {"apiKey": "{env:MY_OWN}"}, "models": {"x": {}}}
        cfg["provider"]["bailian-token-plan-personal"] = own                  # the owner's own block under the same id
        self.file.write_text(json.dumps(cfg))
        r = self.install()
        self.assertTrue(r["kept"])
        self.assertFalse(r["written"])
        self.assertEqual(json.loads(self.file.read_text())["provider"]["bailian-token-plan-personal"], own)

    def test_dry_run_unknown_model_jsonc_and_coding_plan(self):
        r = self.install(dry_run=True)
        self.assertFalse(r["written"])
        self.assertFalse(self.file.exists())
        with self.assertRaises(op.ProviderError):
            self.install(model="qwen9-ultra")
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{ // a comment\n "provider": {} }')
        with self.assertRaises(op.ProviderError):                       # not plain JSON: left untouched
            self.install()
        self.assertIn("// a comment", self.file.read_text())
        self.file.unlink()
        with self.assertRaises(op.ProviderError):
            self.install("coding-plan")  # retired: no new provider preset
        with self.assertRaises(op.ProviderError):
            self.install("coding-plan", model="qwen3.8-max")             # Coding Plan has no 3.8 Max

    def test_text_carries_prices_terms_and_no_key(self):
        with mock.patch.dict(os.environ, {"BAILIAN_TOKEN_PLAN_API_KEY": SENTINEL}):
            r = self.install()
            zh, en = bailian.render(r, "zh"), bailian.render(r, "en")
        for t in (zh, en, json.dumps(r)):
            self.assertNotIn(SENTINEL, t)
        for s in ("¥39", "¥139", "45,000 Credits", "定时任务", "限流", "模型优化", "不能共享", "不会把 Token Plan 的 Key 交给 bot"):   # P348
            self.assertIn(s, zh)
        for s in ("scheduled tasks", "rate-limit", "improve", "no sharing", "never gives it the Token Plan key"):
            self.assertIn(s, en)

    def test_verify_for_and_model_note(self):
        self.assertEqual(bailian.verify_for("bailian-token-plan-personal"),
                         ("https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models",
                          "Authorization: Bearer {value}"))
        self.assertIsNone(bailian.verify_for("bailian-coding-plan"))
        self.assertIsNone(bailian.verify_for("myrelay"))
        self.assertIsNone(bailian.model_note("zhipuai/glm-5.3"))
        self.assertNotIn("不能看图", bailian.model_note("bailian-token-plan-personal/qwen3.8-flash"))
        self.assertIn("不能看图", bailian.model_note("bailian-token-plan-personal/qwen3.7-max"))

    def test_cli_entry(self):
        out = []
        a = SimpleNamespace(action="bailian", id=None, plan=None, model=None, dry_run=True, json=True, lang=None)
        with mock.patch.dict(os.environ, self.env), mock.patch("builtins.print", lambda *x, **k: out.append(x[0])), \
                mock.patch("agentj.service.env_file", lambda name: "/svc/agentj.env"):
            self.assertEqual(op.cmd(a), 0)
        d = json.loads(out[0])
        self.assertEqual((d["ok"], d["provider"], d["written"]), (True, "bailian-token-plan-personal", False))
        self.assertIn("en", d["terms"])


class FakeBailian(http.server.BaseHTTPRequestHandler):
    """The read-only model list of the Token Plan's OpenAI-compatible base: 200 for the right Bearer key, else 401."""
    def do_GET(self):  # noqa: N802
        ok = self.path == "/compatible-mode/v1/models" and self.headers.get("Authorization") == "Bearer " + SENTINEL
        body = json.dumps({"data": [{"id": "qwen3.8-max"}, {"id": "qwen3.8-flash"}]} if ok else
                          {"code": "InvalidApiKey", "message": "Invalid API-key provided."}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class KeyCard(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeBailian)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/compatible-mode/v1/models"

    def card(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as root:
            return elevate.norm_secret({"name": "BAILIAN_TOKEN_PLAN_API_KEY", "purpose": "Bailian Token Plan",
                                        "dest": f"env:{d}/agentj.env#BAILIAN_TOKEN_PLAN_API_KEY", "verify_url": self.url}, Path(root))

    def test_right_key_passes_wrong_key_fails(self):
        c = self.card()
        self.assertEqual(c["verify_header"], "Authorization: Bearer {value}")
        self.assertEqual(elevate.verify_secret(c, bytearray(SENTINEL.encode())), {"verify": "ok", "detail": "HTTP 200"})
        self.assertEqual(elevate.verify_secret(c, bytearray(b"sk-wrong"))["verify"], "fail")

    def test_phone_retry_card_uses_the_fixed_check(self):
        """provider_runtime.request_key (the phone's key card after a 401) must not guess `<anthropic base>/models` (404)."""
        from agentj import provider_runtime
        cards = []

        async def request(card):
            cards.append(card)
            return {"result": "saved"}
        svc, root = tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()
        self.addCleanup(svc.cleanup)
        self.addCleanup(root.cleanup)
        host = SimpleNamespace(elevate=SimpleNamespace(request=request), st=SimpleNamespace(root=Path(root.name)),
                               lang="zh", agent_notice=lambda t: None)
        conf = {"bailian-token-plan-personal": json.loads(json.dumps(bailian.plan("token-plan")["block"]))}
        with mock.patch.object(provider_runtime, "configured", lambda: conf), \
                mock.patch.object(provider_runtime, "env_path", lambda: svc.name + "/agentj.env"):
            asyncio.run(provider_runtime.request_key(host, "bailian-token-plan-personal"))
            asyncio.run(provider_runtime.request_key(host, "bailian-coding-plan"))
        self.assertEqual(cards[0]["verify_url"], "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models")
        self.assertEqual(cards[0]["verify_header"], "Authorization: Bearer {value}")
        self.assertTrue(cards[0]["verify_before_write"])
        self.assertEqual(len(cards), 1)  # no key card for the retired preset


class Boundaries(unittest.TestCase):
    def test_tasks_and_bots_never_get_the_plan_key(self):
        env = {"PATH": "/usr/bin", "HOME": "/h", "BAILIAN_TOKEN_PLAN_API_KEY": SENTINEL, "BAILIAN_CODING_PLAN_API_KEY": SENTINEL}
        for kind in ("opencode", "qwen", "claude"):
            self.assertNotIn(SENTINEL, json.dumps(fence.auxiliary_environment(env, kind)))
        self.assertIn("BAILIAN_TOKEN_PLAN_API_KEY", harness.OPENCODE_KEY_ENV)        # launchd loads only listed names

    def test_route_lists_bailian_side_by_side_with_kimi(self):
        res = ai_route.run(None, signals={"anthropic": "region", "openai": "region", "kimi": "ok"}, use_platform=False)
        self.assertEqual(res["route"], "kimi")
        self.assertEqual(res["bailian"]["model"], "bailian-token-plan-personal/qwen3.8-max")
        self.assertEqual(res["bailian"]["harness_options"], ["opencode", "qwen"])
        self.assertEqual([t["cny_month"] for t in res["bailian"]["tiers"]], [39, 79, 139, 499])
        zh, en = ai_route.render(res, "zh"), ai_route.render(res, "en")
        self.assertIn("也可以：阿里百炼 Token Plan", zh)                          # P328: an option next to Ark / Kimi
        self.assertLess(zh.index("Kimi 会员"), zh.index("阿里百炼 Token Plan"))
        for s in ("agentj provider bailian", "¥139", "限流", "模型优化", "agentj agent switch qwen"):
            self.assertIn(s, zh)
        self.assertIn("Also possible: Alibaba Cloud Bailian Token Plan", en)
        self.assertIn("这是 Agent J 官方的邀请链接，您和我们各得一次抽奖", zh)        # P279's disclosure unchanged
        off = ai_route.run(None, signals={"anthropic": "ok", "openai": "ok", "kimi": "ok"}, use_platform=False)
        self.assertIsNone(off["bailian"])
        self.assertNotIn("Token Plan", ai_route.render(off, "zh"))


# ------------------------------------------------------------------ Qwen Code over ACP (the stand-in agent)
class QwenKeys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        p = mock.patch("agentj.service.env_file", lambda name: str(self.home / "none.env"))
        p.start()
        self.addCleanup(p.stop)

    def test_token_plan_first_model_and_cron(self):
        env = {"HOME": str(self.home)}
        src = {"DASHSCOPE_API_KEY": "d", "BAILIAN_TOKEN_PLAN_API_KEY": SENTINEL}
        k = agent_qwen.key_environment(env, None, source=src)
        self.assertEqual((k["OPENAI_API_KEY"], k["OPENAI_MODEL"]), (SENTINEL, "qwen3.8-max"))
        self.assertNotIn("QWEN_CODE_DISABLE_CRON", k)                   # P348: the owner's own schedules may use the plan
        self.assertEqual(k["OPENAI_BASE_URL"], "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(agent_qwen.key_environment(env, "qwen3.8-flash", source=src)["OPENAI_MODEL"], "qwen3.8-flash")
        self.assertEqual(agent_qwen.key_environment(env, "kimi-k3", source=src)["OPENAI_MODEL"], "qwen3.8-max")
        k = agent_qwen.key_environment(env, None, source={"DASHSCOPE_API_KEY": "d"})
        self.assertNotIn("QWEN_CODE_DISABLE_CRON", k)
        self.assertEqual(agent_qwen.key_environment(env, None, source={}), {})

    def test_owner_settings_win_and_facts_are_names_only(self):
        (self.home / ".qwen").mkdir()
        (self.home / ".qwen" / "settings.json").write_text(json.dumps({
            "security": {"auth": {"selectedType": "openai", "apiKey": SENTINEL}}, "tools": {"approvalMode": "yolo"},
            "permissions": {"allow": ["Bash(git *)"]}}))
        env = {"HOME": str(self.home)}
        f = agent_qwen.settings_facts(env)
        self.assertEqual(f, {"own_auth": True, "providers": 0, "mode": "yolo", "allow_rules": 1})
        self.assertNotIn(SENTINEL, json.dumps(f))
        self.assertEqual(agent_qwen.key_environment(env, None, source={"BAILIAN_TOKEN_PLAN_API_KEY": SENTINEL}), {})
        self.assertEqual(agent_qwen.login(env)[0], "ok")
        self.assertNotIn(SENTINEL, agent_qwen.login(env)[1])

    def test_login_states(self):
        self.assertEqual(agent_qwen.login({"HOME": str(self.home)})[0], "warn")
        st, where = agent_qwen.login({"HOME": str(self.home), "BAILIAN_TOKEN_PLAN_API_KEY": SENTINEL})
        self.assertEqual(st, "env")
        self.assertNotIn(SENTINEL, where)
        self.assertTrue(agent_qwen.version_ok("0.25.1"))
        self.assertFalse(agent_qwen.version_ok("0.18.0"))


class QwenAcp(AcpCase):
    def setUp(self):
        super().setUp()
        os.environ["AGENTJ_QWEN_BIN"] = FAKE
        for k in ("BAILIAN_TOKEN_PLAN_API_KEY", "BAILIAN_CODING_PLAN_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(k, None)
        p = mock.patch("agentj.service.env_file", lambda name: str(self.d / "none.env"))
        p.start()
        self.addCleanup(p.stop)

    def make(self, session=None, cfg=None, **host):
        from test_p305_kimi import FakeHost, FakeSt
        st = FakeSt(session)
        st.sessions["qwen"] = session
        self.host = FakeHost(st, **host)
        self.host.set_model = lambda m: self.host.__dict__.setdefault("models_set", []).append(m)
        self.agent = agent_qwen.QwenAgent(self.host, {"kind": "qwen", "dir": str(self.d / "work"), "fence": False, **(cfg or {})})
        return self.agent

    async def test_argv_auto_mode_reset_and_qwen_mode_notice(self):
        os.environ["FAKE_ACP_MODE"] = "auto"                    # Qwen starts new sessions in its classifier mode
        a = self.make()
        self.assertEqual(a.argv()[1:], ["--acp"])
        await a.turn("hi")
        self.assertEqual(self.sent("session/set_mode")[0]["params"]["modeId"], "default")
        await a.turn("QYOLO")
        await asyncio.sleep(0.3)
        self.assertEqual([m["params"]["modeId"] for m in self.sent("session/set_mode")], ["default", "default"])
        self.assertIn(("agent_mode_reset", {"agent": "qwen", "mode": "yolo"}), self.host.st.logs)

    async def test_qwen_tool_shape_goes_to_the_phone_once(self):
        a = self.make()
        await a.turn("QTOOL: rm -rf build")
        tool, inp, _ = self.host.asked[0]
        self.assertEqual((tool, inp["command"]), ("Bash", "rm -rf build"))   # title = the command line → kind execute → Bash
        self.assertIn("PICKED: proceed_once", self.host.texts[-1])           # never an allow_always option
        await a.stop()
        b = self.make(allow=False)
        await b.turn("QTOOL: ls")
        self.assertIn("PICKED: cancel", self.host.texts[-1])

    async def test_key_card_key_reaches_qwen_only_in_memory(self):
        os.environ["BAILIAN_TOKEN_PLAN_API_KEY"] = SENTINEL
        a = self.make(cfg={"model": "qwen3.8-flash"})
        await a.turn("ENV")
        got = json.loads(self.host.texts[-1][5:])
        self.assertEqual(got, {"key": True, "base": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                               "model": "qwen3.8-flash", "cron_off": None})   # P348
        self.assertNotIn(SENTINEL, self.log.read_text())
        self.assertFalse((self.d / "home" / ".qwen" / "settings.json").exists())      # nothing written to Qwen's settings
        names = [m["id"] for m in a.models_cache]
        self.assertEqual(names[:2], ["qwen3.8-max", "qwen3.8-flash"])
        r = await a.cmd_model("")
        self.assertTrue(any(m["cur"] and m["id"] == "qwen3.8-flash" for m in r.models))
        r = await a.cmd_model("qwen3.8-max")
        self.assertEqual(self.host.models_set, ["qwen3.8-max"])
        self.assertIsNone(a.proc)                                                      # restarted with the new OPENAI_MODEL
        self.assertEqual((await a.cmd_model("gpt-6")).kind, "error")

    async def test_task_runner_gets_the_plan_key_never_pay_as_you_go(self):   # P348 (was: never gets the key)
        os.environ["DASHSCOPE_API_KEY"] = SENTINEL
        from test_p305_kimi import FakeHost, FakeSt
        self.host = FakeHost(FakeSt())
        self.agent = agent_qwen.QwenAgent(self.host, {"kind": "qwen", "dir": str(self.d / "work"), "fence": False}, persist=False)
        text, code = await self.agent.run_once("ENV")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text[5:])["key"], False)                       # pay as you go: never a task
        os.environ["BAILIAN_TOKEN_PLAN_API_KEY"] = SENTINEL
        self.agent = agent_qwen.QwenAgent(self.host, {"kind": "qwen", "dir": str(self.d / "work"), "fence": False}, persist=False)
        text, code = await self.agent.run_once("ENV")
        got = json.loads(text[5:])
        self.assertEqual((code, got["key"], got["cron_off"]), (0, True, None))     # the owner's own task runs on the plan
        self.assertNotIn(SENTINEL, text)
        self.agent = None

    async def test_no_key_is_a_login_hint(self):
        os.environ["FAKE_ACP_AUTH"] = "1"
        a = self.make()
        await a.turn("hi")
        self.assertTrue(any("BAILIAN_TOKEN_PLAN_API_KEY" in n for n in self.host.notices))


class QwenProbe(unittest.TestCase):
    def test_probe_over_acp(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("agentj.service.env_file", lambda name: os.path.join(d, "n.env")):
            env = {"PATH": os.environ.get("PATH", ""), "HOME": d, "BAILIAN_TOKEN_PLAN_API_KEY": SENTINEL}
            self.assertIn("ECHO", agent_qwen.probe(FAKE, env, timeout=20))
            from agentj import agent_kimi
            with self.assertRaises(agent_kimi.ProbeError) as e:
                agent_qwen.probe(FAKE, {**env, "FAKE_ACP_AUTH": "1"}, timeout=20)
            self.assertEqual(e.exception.args[0], "not_logged_in")

    def test_registered_everywhere(self):
        from agentj import binaries, state, switch_harness, taskspec, workflows, main_identity, docsrule
        self.assertIn("qwen", harness.SUPPORTED)
        self.assertIn("qwen", binaries.NAMES)
        self.assertIn("qwen", state.State.AGENT_KINDS)
        self.assertIn("qwen", switch_harness.KINDS)
        self.assertIn("qwen", taskspec.HARNESS_NAMES)
        self.assertEqual(main_identity.MECHANISMS["qwen"], "acp.resource")
        self.assertEqual(docsrule.MEMORY_FILES["qwen"], "~/.qwen/QWEN.md")
        self.assertTrue(any(p.endswith(".qwen/skills") for p in workflows.global_dirs("qwen")))
        self.assertIn("qwen", switch_harness.LOGIN_HINT["zh"])
        self.assertIs(acp.AcpAgent, agent_qwen.QwenAgent.__mro__[1])


if __name__ == "__main__":
    unittest.main()
