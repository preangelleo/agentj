"""P315: provider capabilities (OpenCode image input / output limit; Claude Code tier + subagent models, output tokens) and
the Volcano Ark Coding Plan preset (ark.py / ark.json, key only by name). No network."""
import _hermetic  # noqa: F401
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from agentj import ai_route, ark, harness, opencode_provider as op, provider_profiles as pp  # noqa: E402

SENTINEL = "sk-p315-sentinel-0123456789abcdef"
IMG = {"input": ["text", "image"], "output": ["text"]}


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = {"XDG_CONFIG_HOME": str(self.home / ".config")}
        self.file = Path(op.config_file(self.env))


class Capabilities(Home):
    def test_vision_and_limits_written(self):
        op.add("myark", "https://ark.example/v3", ["kimi-k3"], vision=True, environ=self.env)
        op.add("myark", "https://ark.example/v3", ["glm-5.3"], vision=False, max_output=131072, environ=self.env)
        ms = json.loads(self.file.read_text())["provider"]["myark"]["models"]
        self.assertEqual((ms["kimi-k3"]["attachment"], ms["kimi-k3"]["modalities"]), (True, IMG))   # merge kept kimi-k3
        self.assertEqual((ms["glm-5.3"]["attachment"], ms["glm-5.3"]["modalities"]["input"]), (False, ["text"]))
        self.assertEqual(ms["glm-5.3"]["limit"], {"output": 131072, "context": op.DEFAULT_CONTEXT})
        self.assertNotIn("limit", ms["kimi-k3"])

    def test_merge_keeps_owner_keys_and_context(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"provider": {"myark": {"npm": "x", "headers": {"X-Mine": "1"}, "options": {"timeout": 9},
                                                               "models": {"m": {"name": "Mine", "reasoning": True,
                                                                                "limit": {"context": 900000, "output": 32000}}}}}}))
        r = op.add("myark", "https://ark.example/v3", ["m"], max_output=65536, environ=self.env)
        b = json.loads(self.file.read_text())["provider"]["myark"]
        self.assertEqual((b["headers"], b["options"]["timeout"], b["options"]["apiKey"]), ({"X-Mine": "1"}, 9, "{env:MYARK_API_KEY}"))
        self.assertEqual(b["models"]["m"], {"name": "Mine", "reasoning": True, "limit": {"context": 900000, "output": 65536}})
        self.assertEqual(r["vision"], [])

    def test_plain_add_unchanged_and_bad_limits_refused(self):
        self.assertEqual(op.block("p", "https://x/v1", ["m"])["models"], {"m": {"name": "m"}})
        for bad in (0, 100, 10**9, True, "8"):
            with self.assertRaises(op.ProviderError):
                op.add("p", "https://x/v1", ["m"], max_output=bad, environ=self.env)
        self.assertFalse(self.file.exists())

    def test_cli_flags(self):
        import argparse
        ap = argparse.ArgumentParser()
        op.add_parser(ap.add_subparsers())
        a = ap.parse_args(["provider", "add", "myark", "--base-url", "https://x/v3", "--model", "k", "--vision",
                           "--max-output", "131072", "--context", "1000000", "--json"])
        with mock.patch.object(op, "config_file", lambda environ=None: str(self.file)), \
                mock.patch.object(op, "next_steps", lambda *x: ["a", "b", "c"]), redirect_stdout(io.StringIO()):
            self.assertEqual(op.cmd(a), 0)
        m = json.loads(self.file.read_text())["provider"]["myark"]["models"]["k"]
        self.assertEqual((m["attachment"], m["limit"]), (True, {"context": 1000000, "output": 131072}))
        self.assertIsNone(ap.parse_args(["provider", "add", "x"]).vision)
        self.assertFalse(ap.parse_args(["provider", "add", "x", "--no-vision"]).vision)


class ClaudeTiers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        e = mock.patch.dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
                            CODEX_HOME=str(self.home / ".codex"))
        e.start(); self.addCleanup(e.stop)
        s = mock.patch.object(pp, "selection", return_value={"ok": True, "applicable": False, "model": None})
        s.start(); self.addCleanup(s.stop)

    def save(self, pid, harness, url, model, **k):
        r = pp.define(pid, harness, url, model, **k)
        pp.atomic(Path(r["key_destination"][5:]), SENTINEL.encode())
        return pp.target({"harness": harness})

    def env(self, target):
        return json.loads(target.read_text()).get("env", {})

    def test_third_party_points_every_tier_at_the_service(self):
        t = self.save("ark", "claude", pp.preset_url("ark-coding", "anthropic"), "doubao-seed-2.1-pro",
                      tiers={"haiku": "doubao-seed-2.1-lite"}, max_output=65536)
        pp.atomic(t, json.dumps({"env": {"KEEP": "x"}, "permissions": {"allow": []}}).encode())
        pp.use("ark", verify=lambda c, k: None)
        env = self.env(t)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://ark.cn-beijing.volces.com/api/coding")
        self.assertEqual((env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], env["ANTHROPIC_DEFAULT_SONNET_MODEL"],
                          env["ANTHROPIC_DEFAULT_OPUS_MODEL"], env["CLAUDE_CODE_SUBAGENT_MODEL"]),
                         ("doubao-seed-2.1-lite", "doubao-seed-2.1-pro", "doubao-seed-2.1-pro", "doubao-seed-2.1-pro"))
        self.assertEqual((env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], env["KEEP"]), ("65536", "x"))
        self.assertEqual(json.loads(t.read_text())["model"], "doubao-seed-2.1-pro")
        self.assertNotIn(SENTINEL, json.dumps(pp.listing()))
        self.assertEqual(pp.listing()[0]["tiers"], {"haiku": "doubao-seed-2.1-lite"})
        # a later switch to the official API drops the Ark mapping instead of keeping stale model names
        self.save("official", "claude", "https://api.anthropic.com/v1", "claude-sonnet-5-5")
        pp.use("official", verify=lambda c, k: None)
        env = self.env(t)
        self.assertFalse(any(k in env for k in (*pp.TIERS.values(), pp.MAX_OUTPUT_ENV)))
        pp.restore("official")
        self.assertEqual(self.env(t)["CLAUDE_CODE_SUBAGENT_MODEL"], "doubao-seed-2.1-pro")

    def test_defaults_and_refusals(self):
        r = pp.execute({"action": "save", "id": "arkc", "harness": "claude", "preset": "ark-coding", "model": "kimi-k3"})
        self.assertTrue(r["ok"], r)   # api defaults to anthropic for Claude, the preset picks that address
        self.assertEqual(pp.listing()[0]["base_url"], "https://ark.cn-beijing.volces.com/api/coding/v1")
        self.assertEqual(pp.preset_url("ark-coding", "openai"), "https://ark.cn-beijing.volces.com/api/coding/v3")
        self.assertEqual(pp.preset_url("openai", "openai"), "https://api.openai.com/v1")
        for k in ({"tiers": {"haiku": "m"}}, {"vision": True}, {"max_output": 4096}):
            with self.assertRaises(pp.ProviderError, msg=k):
                pp.define("c", "codex", "https://x/v1", "m", **k)
        with self.assertRaises(pp.ProviderError):
            pp.define("c", "claude", "https://x/v1", "m", tiers={"haiku": "bad model!"})
        with self.assertRaises(pp.ProviderError):
            pp.define("c", "claude", "https://x/v1", "m", max_output=5)
        self.assertEqual(pp.claude_env({"base_url": "https://api.anthropic.com/v1", "model": "x"}), {})

    def test_opencode_profile_capabilities(self):
        t = self.save("arko", "opencode", "https://ark.cn-beijing.volces.com/api/coding/v3", "glm-5.3", vision=False,
                      max_output=131072)
        pp.use("arko", verify=lambda c, k: None)
        m = json.loads(t.read_text())["provider"]["arko"]["models"]["glm-5.3"]
        self.assertEqual((m["attachment"], m["limit"]["output"]), (False, 131072))

    def test_cli_parser(self):
        out = io.StringIO()
        with mock.patch.object(pp, "execute", side_effect=lambda spec: {"ok": True, "spec": spec}), redirect_stdout(out):
            pp.command(["save", "a", "--harness", "claude", "--preset", "ark-coding", "--model", "m", "--opus-model", "o",
                        "--subagent-model", "s", "--max-output", "32000"])
        spec = json.loads(out.getvalue())["spec"]
        self.assertEqual((spec["opus_model"], spec["subagent_model"], spec["max_output"], spec["api"]), ("o", "s", 32000, None))


class ArkPreset(Home):
    def install(self, *a, **k):
        with mock.patch("agentj.service.env_file", lambda name: "/svc/agentj.env"):
            return ark.install(*a, environ=self.env, **k)

    def test_block_vision_table_and_limits(self):
        r = self.install()
        cfg = json.loads(self.file.read_text())
        b = cfg["provider"]["ark-coding-plan"]
        self.assertEqual(b["options"], {"baseURL": "https://ark.cn-beijing.volces.com/api/coding/v3",
                                        "apiKey": "{env:ARK_CODING_PLAN_API_KEY}"})
        self.assertEqual(b["npm"], "@ai-sdk/openai-compatible")
        ms = b["models"]
        for m in ("kimi-k3", "doubao-seed-2.1-pro"):            # measured: 32/32 and 31/32
            self.assertEqual((ms[m]["attachment"], ms[m]["modalities"]), (True, IMG), m)
        for m in ("glm-5.3", "deepseek-v4-pro"):                # measured: refuses / never receives images
            self.assertEqual(ms[m]["modalities"]["input"], ["text"], m)
            self.assertNotEqual(ms[m].get("attachment"), True, m)
        self.assertGreater(ms["glm-5.3"]["limit"]["output"], 32000)   # thinking used up the 32k default
        self.assertEqual(len(ms), 16)
        self.assertNotIn("model", cfg)
        self.assertEqual((r["model"], r["vision"], r["kept"], r["written"]), ("ark-coding-plan/doubao-seed-2.1-pro", True, False, True))
        self.assertIn("--dest env:/svc/agentj.env#ARK_CODING_PLAN_API_KEY", r["next"][0])
        self.assertNotIn("--verify-url", r["next"][0])
        self.assertEqual(r["next"][2], "agentj agent switch opencode --model ark-coding-plan/doubao-seed-2.1-pro")
        self.assertIn("ark-coding-plan/kimi-k3", r["vision_models"])

    def test_owner_config_kept_and_unknown_model(self):
        self.file.parent.mkdir(parents=True)
        mine = {"provider": {"ark-coding-plan": {"npm": "mine"}}, "model": "x/y"}
        self.file.write_text(json.dumps(mine))
        r = self.install("kimi-k3")
        self.assertEqual((r["kept"], r["written"]), (True, False))
        self.assertEqual(json.loads(self.file.read_text()), mine)
        with self.assertRaises(op.ProviderError):
            self.install("gpt-9")

    def test_notes_verify_and_claude_route(self):
        self.assertEqual(ark.verify_for("ark-coding-plan"), ("", ""))
        self.assertIsNone(ark.verify_for("other"))
        self.assertIsNone(ark.model_note("zhipuai/glm-5.3"))
        self.assertIn("不能看图", ark.model_note("ark-coding-plan/glm-5.3"))
        self.assertNotIn("cannot read images", ark.model_note("ark-coding-plan/kimi-k3", "en"))
        self.assertIn("Pro", ark.model_note("ark-coding-plan/kimi-k3", "en"))
        s = ark.claude_steps()
        self.assertEqual(s[0], "agentj provider profile save ark-claude --harness claude --preset ark-coding --model doubao-seed-2.1-pro")
        self.assertEqual(s[2], "agentj provider profile use ark-claude")
        a = SimpleNamespace(model=["kimi-k3"], harness="claude", dry_run=False, json=True, lang="en")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ark.cmd(a), 0)
        self.assertIn("--model kimi-k3", json.loads(out.getvalue())["next"][0])
        self.assertIn("ARK_CODING_PLAN_API_KEY", harness.OPENCODE_KEY_ENV)
        # P335: Ark is a main recommendation now (ai_route.ark_steps), no longer one of the alternatives
        self.assertFalse(any(x["how"] == "agentj provider ark" for x in ai_route.alternatives("CN")))
        self.assertIn("agentj provider ark", "\n".join(ai_route.ark_steps("zh")))

    def test_render_both_languages(self):
        r = self.install(dry_run=True)
        self.assertFalse(self.file.exists())
        zh, en = ark.render(r, "zh"), ark.render(r, "en")
        self.assertIn("kimi-k3", zh); self.assertIn("预演", zh); self.assertIn("dry run", en)
        self.assertIn("Claude Code", ark.render({"harness": "claude", "next": ark.claude_steps()}, "en"))


class Docs(unittest.TestCase):
    """The site page and the install manual name the preset, its image models and who it is for, in both languages."""
    ROOT = HERE.parents[2]

    def test_setup_agents_and_install_md(self):
        page = self.ROOT / "agentjarvis/site/content/docs/17-setup-agents"
        manual = self.ROOT / "documentation/product/install.md"
        if not page.is_dir() or not manual.is_file():
            self.skipTest("private docs are not in the public export")
        for lang in ("en", "zh.src", "zh"):
            t = (page / f"{lang}.md").read_text(encoding="utf-8")
            self.assertIn("{#ark}", t, lang)
            self.assertIn("/docs/setup-agents/#ark", t, lang)
            for s in ("agentj provider ark", "Claude Code", "ark-coding-plan/doubao-seed-2.1-pro", "kimi-k3", "glm-5.3"):
                self.assertIn(s, t, (lang, s))
        m = manual.read_text(encoding="utf-8")
        self.assertIn("### 3h.", m)
        for s in ("agentj provider ark --harness claude", "--max-output", "--vision", "CLAUDE_CODE_SUBAGENT_MODEL"):
            self.assertIn(s, m, s)


if __name__ == "__main__":
    unittest.main()
