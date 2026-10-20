"""P395 (#141, 2026-10-19): Volcano Ark's Agent Plan — `agentj provider ark --plan agent` (the default and the
recommendation; the Coding Plan stays as `--plan coding`). Its own addresses (OpenAI …/api/plan/v3, Anthropic …/api/plan),
provider `ark-agent-plan`, key ARK_AGENT_PLAN_API_KEY — the plan's own key from 订阅 → Agent Plan → 使用配置 (one from API Key
management is refused there, and the plan key is refused on …/api/coding). Never …/api/v3. No network, keys only by name."""
import _hermetic  # noqa: F401
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from agentj import ark, fence, harness, keys, opencode_provider as op, provider_profiles as pp, switch_harness as sh  # noqa: E402
from agentj.bots import provider as bp  # noqa: E402

PLAN_V3 = "https://ark.cn-beijing.volces.com/api/plan/v3"


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"XDG_CONFIG_HOME": str(Path(self.tmp.name) / ".config")}
        self.file = Path(op.config_file(self.env))
        p = mock.patch("agentj.service.env_file", lambda name: "/svc/agentj.env")
        p.start()
        self.addCleanup(p.stop)

    def cfg(self):
        return json.loads(self.file.read_text())

    def cli(self, *args):
        a = SimpleNamespace(action="ark", model=None, plan=None, harness=None, lang="zh", dry_run=False, json=True)
        for k, v in args:
            setattr(a, k, v)
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(op, "config_file", lambda environ=None: str(self.file)):
            rc = ark.cmd(a)
        return rc, json.loads(out.getvalue())


class Preset(Home):
    def test_agent_block_addresses_key_and_models(self):
        r = ark.install(environ=self.env, plan="agent")
        b = self.cfg()["provider"]["ark-agent-plan"]
        self.assertEqual(b["options"], {"baseURL": PLAN_V3, "apiKey": "{env:ARK_AGENT_PLAN_API_KEY}"})
        self.assertNotIn("/api/v3", b["options"]["baseURL"].replace("/api/plan/v3", ""))
        ms = b["models"]
        for m in ("ark-code-latest", "doubao-seed-2.1-pro", "kimi-k3", "glm-5.3", "deepseek-v4-pro", "minimax-m3"):
            self.assertIn(m, ms)
        self.assertNotIn("doubao-seed-2.0-lite", ms)       # measured 2026-10-19: UnsupportedModel on the Agent Plan
        self.assertEqual(ms["kimi-k3"]["modalities"]["input"], ["text", "image"])
        self.assertEqual(ms["glm-5.3"]["modalities"]["input"], ["text"])
        self.assertEqual((r["model"], r["plan"], r["key_env"], r["vision"]),
                         ("ark-agent-plan/doubao-seed-2.1-pro", "agent", "ARK_AGENT_PLAN_API_KEY", True))
        self.assertIn("--dest env:/svc/agentj.env#ARK_AGENT_PLAN_API_KEY", r["next"][0])
        self.assertIn("订阅 → Agent Plan → 使用配置", r["next"][0])      # where the key is, on the card
        self.assertNotIn("--verify-url", r["next"][0])
        self.assertEqual(r["next"][2], "agentj agent switch opencode --model ark-agent-plan/doubao-seed-2.1-pro")
        self.assertNotIn("model", self.cfg())

    def test_coding_plan_kept_beside_it(self):
        ark.install(environ=self.env, plan="coding")
        ark.install(environ=self.env, plan="agent")
        prov = self.cfg()["provider"]
        self.assertEqual(prov["ark-coding-plan"]["options"]["baseURL"], "https://ark.cn-beijing.volces.com/api/coding/v3")
        self.assertEqual(prov["ark-agent-plan"]["options"]["baseURL"], PLAN_V3)
        self.assertIn("doubao-seed-2.0-lite", prov["ark-coding-plan"]["models"])

    def test_unsupported_model_refused(self):
        with self.assertRaises(op.ProviderError) as e:
            ark.install("doubao-seed-2.0-lite", environ=self.env, plan="agent")
        self.assertIn("Agent Plan", str(e.exception))

    def test_cli_default_is_agent_and_coding_still_works(self):
        rc, r = self.cli()
        self.assertEqual((rc, r["plan"], r["provider"]), (0, "agent", "ark-agent-plan"))
        self.assertIn("Agent Plan", r["terms"]["zh"])
        rc, r = self.cli(("plan", "coding"))
        self.assertEqual((rc, r["plan"], r["provider"]), (0, "coding", "ark-coding-plan"))
        rc, r = self.cli(("plan", "coding-plan"))
        self.assertEqual(r["provider"], "ark-coding-plan")
        rc, r = self.cli(("plan", "agent"), ("harness", "claude"))
        self.assertEqual(rc, 0)
        self.assertIn("--preset ark-agent", r["next"][0])
        self.assertIn("ARK_AGENT_CLAUDE_API_KEY", r["next"][1])

    def test_parser_accepts_agent(self):
        import argparse
        root = argparse.ArgumentParser()
        op.add_parser(root.add_subparsers())
        a = root.parse_args(["provider", "ark", "--plan", "agent"])
        self.assertEqual(a.plan, "agent")

    def test_render_says_where_the_key_is(self):
        r = ark.install(environ=self.env, plan="agent", dry_run=True)
        zh, en = ark.render(r, "zh"), ark.render(r, "en")
        self.assertIn("订阅 → Agent Plan → 使用配置 → 专属 API Key", zh)
        self.assertIn("不是「API Key 管理」", zh)
        self.assertIn("not one from API Key management", en)
        self.assertIn("…/api/plan/v3", zh)
        self.assertIn("--plan coding", zh)
        self.assertIn("Medium", zh)
        self.assertIn("¥49.9", en)
        self.assertNotIn("/api/coding", zh)

    def test_model_note_and_verify(self):
        self.assertIn("Agent Plan", ark.model_note("ark-agent-plan/doubao-seed-2.1-pro"))
        self.assertIn("Medium", ark.model_note("ark-agent-plan/kimi-k3", "en"))
        self.assertIn("不能看图", ark.model_note("ark-agent-plan/glm-5.3"))
        self.assertEqual(ark.verify_for("ark-agent-plan"), ("", ""))
        self.assertEqual(ark.plan_of("ark-agent-plan"), "agent")
        self.assertEqual(ark.plan_of("ark-coding-plan"), "coding")


class Elsewhere(unittest.TestCase):
    def test_claude_profile_preset(self):
        self.assertEqual(pp.preset_url("ark-agent", "anthropic"), "https://ark.cn-beijing.volces.com/api/plan/v1")
        self.assertEqual(pp.preset_url("ark-agent", "openai"), PLAN_V3)

    def test_key_names_known(self):
        self.assertIn("ARK_AGENT_PLAN_API_KEY", harness.OPENCODE_KEY_ENV)
        self.assertIn("ARK_AGENT_PLAN_API_KEY", fence.PLAN_KEY_ENV)        # the owner's tasks may use it …
        ark_row = next(r for r in keys.CATALOG if r[0] == "ark")
        self.assertIn(("sub", "ARK_AGENT_PLAN_API_KEY"), ark_row[2])

    def test_bots_never_get_the_agent_plan(self):                         # … a support bot never does
        for u in (PLAN_V3, "https://ark.cn-beijing.volces.com/api/plan", "https://ark.cn-beijing.volces.com/api/plan/v1",
                  *pp.PRESETS["ark-agent"].values()):
            self.assertTrue(bp.coding_plan(u), u)
        self.assertFalse(bp.coding_plan("https://ark.cn-beijing.volces.com/api/planx"))
        self.assertFalse(bp.coding_plan("https://ark.cn-beijing.volces.com/api/v3"))
        self.assertTrue(bp.coding_plan(model="ark-agent-plan/kimi-k3"))

    def test_switch_defaults_to_agent_plan(self):
        seen = {}

        def request(st, target, model, dry_run=False):
            seen["model"] = model
            return {"ok": True, "target": target}
        with tempfile.TemporaryDirectory() as t:
            env = {"XDG_CONFIG_HOME": t}
            with mock.patch("agentj.service.env_file", lambda name: "/svc/agentj.env"):
                r = ark.switch(None, request=request, detector=lambda: {"opencode": "2.0.24", "arkcli": None},
                               key=lambda name, environ=None: True, environ=env)
        self.assertEqual((r["ok"], r["plan"], seen["model"]), (True, "agent", "ark-agent-plan/doubao-seed-2.1-pro"))
        self.assertIn("Agent Plan", ark.say_switch(r))
        self.assertIn("…/api/plan", ark.say_switch(r))

    def test_switch_need_key_names_the_console_path(self):
        r = ark.switch(None, detector=lambda: {"opencode": "1.19.3", "arkcli": None}, key=lambda n, e=None: False)
        self.assertEqual((r["reason"], r["key_env"]), ("need_key", "ARK_AGENT_PLAN_API_KEY"))
        self.assertIn("使用配置", ark.say_switch(r))

    def test_agent_switch_parser_plan(self):
        from agentj import cli
        src = Path(cli.__file__).read_text()
        self.assertIn('ag.add_argument("--plan", choices=["agent", "coding", "payg"]', src)
        self.assertIn("ark.DEFAULT_PLAN", Path(sh.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
