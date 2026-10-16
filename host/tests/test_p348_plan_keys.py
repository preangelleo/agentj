"""P348 (2026-10-16, backlog #58): every plan key (Volcano Ark / Bailian Coding Plan, Bailian Token Plan, Kimi For Coding)
may run the owner's own scheduled tasks and workflow CEOs; a support bot serves other people, so it never runs on a plan
and says in plain words where to add a pay-as-you-go key instead."""
import _hermetic  # noqa: F401
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentj import agent_kimi, agent_qwen, ark, bailian, fence, provider_profiles, provider_runtime  # noqa: E402
from agentj.bots import provider as bp  # noqa: E402
from agentj.bots.store import BotError, config  # noqa: E402

ROOT = HERE.parents[1]
ARK = "sk-p348-ark-sentinel-0123456789"
BL_CODING = "sk-sp-p348-coding-sentinel-0123"
BL_TOKEN = "sk-p348-token-sentinel-0123456789"
PAYG = "sk-p348-payg-sentinel-0123456789"
KIMI = "sk-kimi-p348-member-sentinel-0123"
MOONSHOT = "sk-p348-moonshot-payg-sentinel-01"


def env():
    return {"PATH": "/usr/bin", "HOME": "/h", "ARK_CODING_PLAN_API_KEY": ARK, "BAILIAN_CODING_PLAN_API_KEY": BL_CODING,
            "BAILIAN_TOKEN_PLAN_API_KEY": BL_TOKEN, "DEEPSEEK_API_KEY": PAYG, "OPENROUTER_API_KEY": PAYG,
            "KIMI_API_KEY": KIMI, "MOONSHOT_API_KEY": MOONSHOT, "DASHSCOPE_API_KEY": PAYG}


class TaskEnvironment(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(provider_runtime, "fresh_environment", lambda e: e)   # never the real service env file
        p.start()
        self.addCleanup(p.stop)

    def test_owner_tasks_get_every_plan_key_and_no_pay_as_you_go_key(self):
        for kind in ("opencode", "qwen", "kimi", "claude"):
            with self.subTest(kind=kind):
                out = fence.auxiliary_environment(env(), kind, plan_keys=True)
                self.assertEqual(out.get("ARK_CODING_PLAN_API_KEY"), ARK)
                self.assertEqual(out.get("BAILIAN_CODING_PLAN_API_KEY"), BL_CODING)
                self.assertEqual(out.get("BAILIAN_TOKEN_PLAN_API_KEY"), BL_TOKEN)   # 贾维斯 22:2x: all plans
                self.assertEqual(out.get("KIMI_API_KEY"), KIMI)
                blob = json.dumps(out)
                self.assertNotIn(PAYG, blob)          # pay-as-you-go keys stay native-storage only, as before
                self.assertNotIn(MOONSHOT, blob)
        self.assertEqual(set(fence.PLAN_KEY_ENV), {"ARK_CODING_PLAN_API_KEY", "BAILIAN_CODING_PLAN_API_KEY",
                                                   "BAILIAN_TOKEN_PLAN_API_KEY", "KIMI_API_KEY"})

    def test_bots_and_default_get_no_plan_key(self):
        for kind in ("opencode", "qwen", "claude", "codex"):
            blob = json.dumps(fence.auxiliary_environment(env(), kind))
            for v in (ARK, BL_CODING, BL_TOKEN, PAYG, KIMI, MOONSHOT):
                self.assertNotIn(v, blob)
        src = (ROOT / "host/agentj/bots/native.py").read_text()
        self.assertIn("auxiliary_environment(os.environ,p.name)", src)
        self.assertNotIn("plan_keys", src)

    def test_opencode_task_launch_keeps_the_ark_key(self):
        aux = fence.auxiliary_environment(env(), "opencode", plan_keys=True)
        self.assertEqual(fence.launch_environment(aux, False, "opencode").get("ARK_CODING_PLAN_API_KEY"), ARK)

    def test_task_launchers_pass_plan_keys(self):
        for f in ("agent_opencode.py", "agent.py", "acp.py"):
            src = (ROOT / "host/agentj" / f).read_text()
            self.assertIn("plan_keys=True", src, f)

    def test_qwen_task_uses_the_plan_keys_never_pay_as_you_go(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("agentj.service.env_file", lambda name: str(Path(d) / "none.env")):
            e = dict(env(), HOME=d)
            task = SimpleNamespace(cfg={"_workflow_ceo": True})
            chat = SimpleNamespace(cfg={})
            got = agent_qwen.key_environment(e, None, source=e, names=agent_qwen.QwenAgent.names(task))
            self.assertEqual(got["OPENAI_API_KEY"], BL_TOKEN)             # Token Plan first, as in the chat
            self.assertNotIn("QWEN_CODE_DISABLE_CRON", got)               # Qwen's own scheduler no longer switched off
            self.assertEqual(agent_qwen.key_environment(e, None, source=e, names=agent_qwen.QwenAgent.names(chat))["OPENAI_API_KEY"],
                             BL_TOKEN)
            only_coding = {k: v for k, v in e.items() if k != "BAILIAN_TOKEN_PLAN_API_KEY"}
            self.assertEqual(agent_qwen.key_environment(only_coding, None, source=only_coding,
                                                        names=agent_qwen.QwenAgent.names(task))["OPENAI_API_KEY"], BL_CODING)
            only_payg = {"HOME": d, "DASHSCOPE_API_KEY": PAYG}
            self.assertEqual(agent_qwen.key_environment(only_payg, None, source=only_payg,
                                                        names=agent_qwen.QwenAgent.names(task)), {})
            self.assertEqual(agent_qwen.key_environment(only_payg, None, source=only_payg,
                                                        names=agent_qwen.QwenAgent.names(chat))["OPENAI_API_KEY"], PAYG)

    def test_kimi_task_uses_the_membership_key_never_moonshot(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("agentj.service.env_file", lambda name: str(Path(d) / "none.env")), \
                mock.patch.dict(os.environ, {"KIMI_API_KEY": KIMI, "MOONSHOT_API_KEY": MOONSHOT}):
            home = {"HOME": d, "KIMI_CODE_HOME": str(Path(d) / "k")}
            task = agent_kimi.KimiAgent.__new__(agent_kimi.KimiAgent)
            task.cfg = {"_workflow_ceo": True}
            out = task.child_env(dict(home))
            self.assertEqual((out["KIMI_MODEL_API_KEY"], out["KIMI_MODEL_NAME"]), (KIMI, agent_kimi.CODING_MODEL))
            self.assertEqual(out["KIMI_MODEL_BASE_URL"], agent_kimi.CODING_BASE["mainland-cn"])
            os.environ.pop("KIMI_API_KEY")
            out = task.child_env(dict(home))
            self.assertNotIn("KIMI_MODEL_API_KEY", out)                   # Moonshot pay-as-you-go: never a task
            self.assertNotIn(MOONSHOT, json.dumps(out))
            chat = agent_kimi.KimiAgent.__new__(agent_kimi.KimiAgent)
            chat.cfg = {}
            self.assertEqual(chat.child_env(dict(home))["KIMI_MODEL_API_KEY"], MOONSHOT)   # the chat as before


class BotsRefusePlans(unittest.TestCase):
    def test_every_plan_address_is_caught(self):
        urls = [ark.data()["anthropic_base"], ark.data()["block"]["options"]["baseURL"],
                *provider_profiles.PRESETS["ark-coding"].values()]
        for p in bailian.data().values():
            urls += [p["openai_base"], p["block"]["options"]["baseURL"]]
        for u in urls:
            self.assertTrue(bp.coding_plan(u), u)
        for u in ("https://ark.cn-beijing.volces.com/api/v3", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "https://openrouter.ai/api/v1", "https://api.deepseek.com/v1", "https://ark.cn-beijing.volces.com/api/codingx"):
            self.assertFalse(bp.coding_plan(u), u)
        self.assertTrue(bp.coding_plan(model="ark-coding-plan/doubao-seed-2.1-pro"))
        self.assertTrue(bp.coding_plan(model="bailian-coding-plan/qwen3.7-plus"))
        for u in agent_kimi.CODING_BASE.values():                        # Kimi For Coding membership
            self.assertTrue(bp.coding_plan(u), u)
        for u in agent_kimi.PLATFORM_BASE.values():                      # Moonshot pay-as-you-go stays allowed
            self.assertFalse(bp.coding_plan(u), u)
        self.assertTrue(bp.coding_plan(model="kimi-code-plan-cn/k3"))
        self.assertTrue(bp.coding_plan(model="kimi-code-plan-global/kimi-for-coding"))
        self.assertFalse(bp.coding_plan(model="moonshot/kimi-k2-turbo-preview"))
        self.assertFalse(bp.coding_plan(model="deepseek/deepseek-chat"))
        self.assertIn(ark.data()["provider"], bp.PLAN_PROVIDERS)
        self.assertTrue({p["provider"] for p in bailian.data().values()} <= set(bp.PLAN_PROVIDERS))

    def test_main_plan_bot_is_refused_without_reading_native_auth(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}), \
                mock.patch("agentj.preferences.guard", lambda path: path), \
                mock.patch.object(bp, "_load", side_effect=AssertionError("must not read native auth")):
            for m in ("ark-coding-plan/doubao-seed-2.1-pro", "bailian-token-plan-personal/qwen3.8-max", "kimi-code-plan-cn/k3"):
                with self.assertRaisesRegex(BotError, "coding_plan_bot"):
                    bp.resolve({"source": "main"}, "opencode", m)
            self.assertEqual(bp.resolve({"source": "main"}, "opencode", "deepseek/deepseek-chat").api, "native")
            self.assertEqual(bp.resolve({"source": "main"}, "claude", None).api, "native")
            root = provider_profiles.root()
            for pid, harness, url, model in (("mine", "opencode", "https://ark.cn-beijing.volces.com/api/coding/v3", "doubao"),
                                              ("ark-claude", "claude", ark.data()["anthropic_base"], "doubao-seed-2.1-pro"),
                                              ("relay", "claude", "https://agentsrelay.net/v1", "m")):
                provider_profiles.private_dir(root / pid)
                provider_profiles.atomic(root / pid / "profile.json",
                                         json.dumps({"id": pid, "harness": harness, "base_url": url, "model": model}).encode())
            with self.assertRaisesRegex(BotError, "coding_plan_bot"):     # a hand-named OpenCode profile on the Ark address
                bp.resolve({"source": "main"}, "opencode", "mine/doubao")
            self.assertEqual(bp.resolve({"source": "main"}, "claude", None).api, "native")   # profile defined, never used
            provider_profiles.private_dir(root / "ark-claude/backups/100")
            provider_profiles.atomic(root / "ark-claude/backups/100/record.json", b"{}")
            with self.assertRaisesRegex(BotError, "coding_plan_bot"):     # Claude Code switched to the Ark profile
                bp.resolve({"source": "main"}, "claude", None)
            provider_profiles.private_dir(root / "relay/backups/200")
            provider_profiles.atomic(root / "relay/backups/200/record.json", b"{}")
            self.assertEqual(bp.resolve({"source": "main"}, "claude", None).api, "native")   # later switched away

    def test_own_key_on_a_plan_address_is_refused_at_save(self):
        for url in ("https://coding.dashscope.aliyuncs.com/v1", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                    "https://api.kimi.com/coding/v1"):
            with self.assertRaisesRegex(BotError, "coding_plan_bot"):
                config({"slug": "p348-test", "provider": {"source": "own"},
                        "own_model": {"base_url": url, "model": "qwen3.7-plus", "daily_tokens": 100}})
        config({"slug": "p348-test", "provider": {"source": "own"},
                "own_model": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen3.7-plus", "daily_tokens": 100}})

    def test_phone_says_where_to_add_a_pay_as_you_go_key(self):
        js = (ROOT / "web/public/js/bots.js").read_text()
        self.assertIn("coding_plan_bot:planProblem", js)
        self.assertIn("e.message==='coding_plan_bot'", js)
        self.assertIn("主人自带模型 key", js[js.index("const planProblem"):js.index("async function act")])
        self.assertIn("按量付费", js)


class BotVendors(unittest.IsolatedAsyncioTestCase):
    """Leo 22:0x: the bot's own key starts with the provider — endpoint, API shape, cheap model and a key name of its own
    (apart from the main Agent's), and the secret card says whose pay-as-you-go key and where to create it."""
    def setUp(self):
        from agentj.bots.store import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / "bots")

    def tearDown(self):
        self.s.close(); self.tmp.cleanup()

    def test_presets(self):
        keys = [v[5] for v in bp.VENDORS.values()]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue({"ark", "dashscope", "deepseek", "moonshot", "zhipu", "minimax", "openrouter", "agentsrelay", "openai",
                         "anthropic"} <= set(bp.VENDORS))
        main_names = set(fence.PLAN_KEY_ENV) | {"ARK_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY",
                                                 "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
        for vid, v in bp.VENDORS.items():
            with self.subTest(vid=vid):
                self.assertTrue(v[5].startswith("BOT_") and v[5] not in main_names)
                self.assertFalse(bp.coding_plan(v[2]))                     # every preset is pay as you go
                bp._valid(v[2], v[4], v[3], "validation", vid)
                card = bp.vendor_card({"vendor": vid})
                self.assertIn(v[6], card); self.assertIn(v[0], card); self.assertIn(v[1], card); self.assertLessEqual(len(card), 300)
        self.assertEqual(bp.vendor_key(None), "BOT_MODEL_KEY")
        self.assertEqual(bp.vendor_key({"vendor": "other", "base_url": "https://x.example/v1"}), "BOT_MODEL_KEY")
        self.assertEqual(bp.vendor_key({"base_url": "https://openrouter.ai/api/v1"}), "BOT_MODEL_KEY")   # bots saved before P348

    def test_config_takes_endpoint_and_api_from_the_preset(self):
        c = config({"slug": "p348-v", "provider": {"source": "own"},
                    "own_model": {"vendor": "dashscope", "base_url": "https://evil.example/v1", "daily_tokens": 100}})
        self.assertEqual(c["own_model"], {"vendor": "dashscope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                          "api": "chat", "model": "qwen-flash", "daily_tokens": 100})
        a = config({"slug": "p348-v", "provider": {"source": "own"},
                    "own_model": {"vendor": "anthropic", "model": "claude-haiku-5-5", "daily_tokens": 100}})["own_model"]
        self.assertEqual((a["base_url"], a["api"]), ("https://api.anthropic.com/v1", "anthropic"))
        for bad in ({"vendor": "nope", "base_url": "https://x.example/v1", "model": "m", "daily_tokens": 1},
                    {"vendor": "other", "model": "m", "daily_tokens": 1},
                    {"vendor": "other", "base_url": "https://x.example/v1", "model": "m", "daily_tokens": 1, "api": "anthropic"}):
            with self.assertRaisesRegex(BotError, "invalid_own_model"):
                config({"slug": "p348-v", "provider": {"source": "own"}, "own_model": bad})

    def test_each_provider_reads_its_own_key(self):
        from agentj.bots.provider import resolve_bot
        from agentj.provider_profiles import atomic
        bid = self.s.create({"slug": "p348-v", "provider": {"source": "own"},
                             "own_model": {"vendor": "deepseek", "daily_tokens": 100}})["id"]
        atomic(self.s.secrets(bid) / "BOT_MODEL_KEY", b"wrong-key-file")
        with self.assertRaisesRegex(BotError, "key_provider_required"):
            resolve_bot(self.s, bid, "codex")                              # never another provider's file
        atomic(self.s.secrets(bid) / "BOT_DEEPSEEK_API_KEY", PAYG.encode())
        p = resolve_bot(self.s, bid, "codex")
        self.assertEqual((p.base_url, p.model, p.api, p.key, p.name), ("https://api.deepseek.com/v1", "deepseek-chat", "chat", PAYG, "deepseek"))
        self.assertNotIn(PAYG, json.dumps(p.public()))

    async def test_secret_card_names_the_provider(self):
        from unittest.mock import AsyncMock
        from agentj.bots.owner import Manager
        root = Path(self.tmp.name) / "state"; root.mkdir()
        el = SimpleNamespace(request=AsyncMock(return_value={"result": "saved"}))
        host = SimpleNamespace(st=SimpleNamespace(root=root, perm_dir=root / "perm"), channel="p348", elevate=el, agent=None,
                               agent_cfg={"kind": "codex"})
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name + "/config"}):
            m = Manager(host); s = m.state()
            try:
                bid = s.create({"slug": "p348-card", "provider": {"source": "own"},
                                "own_model": {"vendor": "dashscope", "daily_tokens": 100}})["id"]
                self.assertTrue(any(v["id"] == "ark" and v["key"] == "BOT_ARK_API_KEY" for v in m.read({"op": "list"})["vendors"]))
                await m.write({"op": "model_key", "id": bid})
                card = el.request.call_args.args[0]
                self.assertEqual(card["name"], "BOT_DASHSCOPE_API_KEY"); self.assertTrue(card["dest"].endswith("/BOT_DASHSCOPE_API_KEY"))
                self.assertIn("百炼", card["purpose"]); self.assertIn("bailian.console.aliyun.com", card["purpose"])
                self.assertNotIn("value", card)
                self.assertEqual(m.read({"op": "detail", "id": bid})["own_key_name"], "BOT_DASHSCOPE_API_KEY")
            finally:
                s.close()


class Texts(unittest.TestCase):
    OLD = ("不会把 Coding Plan 的 Key 交给定时任务", "不会把 Coding Plan 的 key 交给定时任务", "never gives the Coding Plan key to tasks",
           "Coding Plan key to scheduled tasks", "Token Plan 的 Key 交给定时任务", "Token Plan 的 key 交给定时任务",
           "Token Plan key to scheduled tasks", "Token Plan key to tasks", "只允许你本人交互使用", "只能你本人在编程工具里交互使用",
           "only your own interactive use", "You may only use it yourself", "this key to scheduled tasks")

    def test_terms_open_tasks_and_keep_bots_out(self):
        for zh, en in ((ark.TERMS["zh"], ark.TERMS["en"]), (bailian.terms("coding-plan", "zh"), bailian.terms("coding-plan", "en")),
                       (bailian.terms("token-plan", "zh"), bailian.terms("token-plan", "en"))):
            self.assertIn("定时任务", zh); self.assertIn("限流", zh); self.assertIn("按量", zh); self.assertIn("bot", zh)
            self.assertIn("scheduled tasks", en); self.assertIn("rate-limit", en); self.assertIn("support bot", en)
            for o in self.OLD:
                self.assertNotIn(o, zh + en)
        self.assertIn("不会把 Token Plan 的 Key 交给 bot", bailian.terms("token-plan", "zh"))

    def test_docs_and_skills(self):
        files = [f"site/content/docs/{d}/{n}" for d in ("17-setup-agents", "29-switch-harness")
                 for n in ("en.md", "zh.src.md", "zh.md", "zh.override.json")]
        if not (ROOT / "site").is_dir():
            self.skipTest("site sources are private (not in the public tree)")
        for f in files:
            s = (ROOT / f).read_text()
            for o in self.OLD:
                self.assertNotIn(o, s, f)
            self.assertTrue("限流" in s or "rate-limit" in s, f)
        self.assertIn("coding_plan_bot", (ROOT / "host/agentj/skills/agentj-bots/SKILL.md").read_text())
        self.assertIn("rate-limit", (ROOT / "host/agentj/skills/agentj-config/SKILL.md").read_text())
        for s in ("agentj-config", "agentj-switch-harness", "agentj-first-run", "agentj-capability"):
            txt = (ROOT / f"host/agentj/skills/{s}/SKILL.md").read_text()
            for o in ("interactive use only", "never runs in scheduled tasks", "never gives this key to tasks", "只限本人交互使用"):
                self.assertNotIn(o, txt, s)


if __name__ == "__main__":
    unittest.main()
