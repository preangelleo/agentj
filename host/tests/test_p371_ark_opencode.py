"""P371: 「OpenCode · 火山方舟」 — the pay-as-you-go preset (ark.json `payg`, …/api/v3, ARK_API_KEY) beside the Coding Plan,
and the one-step switch `agentj agent switch opencode --preset ark [--plan coding|payg]` (ark.switch): detect OpenCode / arkcli,
stop with the key card when the key is known to be missing, write the block, hand over to switch_harness.request, remove a block
it added when the switch fails. No network, no real service, keys only by name."""
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

from agentj import ark, harness, keys, opencode_provider as op, switch_harness as sh  # noqa: E402
from agentj.bots import provider as bp  # noqa: E402

AJ = HERE.parents[1]
FOUND = {"opencode": "1.18.34", "arkcli": "1.0.39"}


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


class Payg(Home):
    def test_block_address_key_and_models(self):
        r = ark.install(environ=self.env, plan="payg")
        b = self.cfg()["provider"]["ark-api"]
        self.assertEqual(b["options"], {"baseURL": "https://ark.cn-beijing.volces.com/api/v3", "apiKey": "{env:ARK_API_KEY}"})
        self.assertEqual(r["model"], "ark-api/doubao-seed-2-1-pro-260915")
        self.assertTrue(r["vision"])
        self.assertEqual(b["models"]["deepseek-v4-pro-ga-260813"]["modalities"]["input"], ["text"])
        for m in b["models"].values():          # OpenCode needs both limits when it has one
            self.assertTrue({"context", "output"} <= set(m["limit"]))
        self.assertIn("--verify-url https://ark.cn-beijing.volces.com/api/v3/models", r["next"][0])
        self.assertIn("--dest env:/svc/agentj.env#ARK_API_KEY", r["next"][0])
        self.assertEqual(r["next"][2], "agentj agent switch opencode --preset ark --plan payg --model doubao-seed-2-1-pro-260915")
        self.assertNotIn("model", self.cfg())   # never the owner's default model

    def test_payg_never_a_plan_address(self):
        d = ark.data()["payg"]
        self.assertFalse(bp.coding_plan(d["block"]["options"]["baseURL"]))
        self.assertFalse(bp.coding_plan(model="ark-api/doubao-seed-2-1-pro-260915"))
        self.assertNotIn(d["provider"], bp.PLAN_PROVIDERS)
        self.assertNotIn("/api/coding", json.dumps(d))
        self.assertEqual(ark.verify_for("ark-api"), (d["verify_url"], op.HEADER["openai"]))
        self.assertIn("ARK_API_KEY", harness.OPENCODE_KEY_ENV)
        self.assertIn("ARK_API_KEY", keys.CATALOG_NAMES)

    def test_other_model_added_text_only_and_owner_block_kept(self):
        r = ark.install("doubao-seed-2-0-pro-260215", environ=self.env, plan="payg")
        self.assertFalse(r["vision"])
        self.assertIn("doubao-seed-2-0-pro-260215", self.cfg()["provider"]["ark-api"]["models"])
        mine = {"provider": {"ark-api": {"npm": "mine", "models": {"x-1": {}}}}}
        self.file.write_text(json.dumps(mine))
        r = ark.install("doubao-seed-2-1-lite-260915", environ=self.env, plan="payg")
        self.assertEqual((r["kept"], r["added_model"]), (True, True))
        got = self.cfg()["provider"]["ark-api"]
        self.assertEqual(got["npm"], "mine")
        self.assertEqual(set(got["models"]), {"x-1", "doubao-seed-2-1-lite-260915"})
        with self.assertRaises(op.ProviderError):
            ark.install("Bad Model!", environ=self.env, plan="payg")
        with self.assertRaises(op.ProviderError):        # the plan keeps its fixed list
            ark.install("doubao-seed-2-0-pro-260215", environ=self.env)

    def test_notes_and_render(self):
        self.assertIn("按量", ark.model_note("ark-api/doubao-seed-2-1-pro-260915"))
        self.assertIn("cannot read images", ark.model_note("ark-api/glm-5-2-260617", "en"))
        r = ark.install(environ=self.env, plan="payg", dry_run=True)
        self.assertFalse(self.file.exists())
        self.assertIn("预演", ark.render(r, "zh"))
        self.assertIn("pay-as-you-go", ark.render(r, "en"))

    def test_cli_plan_flag(self):
        a = SimpleNamespace(model=None, harness=None, plan="payg", dry_run=True, json=True, lang="zh")
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict("os.environ", self.env):
            self.assertEqual(ark.cmd(a), 0)
        self.assertEqual(json.loads(out.getvalue())["provider"], "ark-api")
        a = SimpleNamespace(model=None, harness="claude", plan="payg", dry_run=True, json=True, lang="zh")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ark.cmd(a), 2)        # Claude Code only on the plan's Anthropic address


class Switch(Home):
    def run_switch(self, plan="payg", key=True, reply=None, found=FOUND, **kw):
        calls = []

        def request(st, target, model, dry_run=False):
            calls.append((target, model, dry_run, self.file.exists() and "ark-api" in json.dumps(self.cfg())))
            return dict(reply or {"ok": True, "scheduled": True, "target": target, "from": "claude"})
        r = ark.switch(None, plan, request=request, detector=lambda: dict(found), key=lambda name, env: key,
                       environ=self.env, **kw)
        return r, calls

    def test_happy_path_writes_then_switches(self):
        r, calls = self.run_switch()
        self.assertTrue(r["ok"])
        self.assertEqual(calls, [("opencode", "ark-api/doubao-seed-2-1-pro-260915", False, True)])   # block written first
        self.assertEqual((r["preset"], r["plan"], r["arkcli"]), ("ark", "payg", "1.0.39"))
        text = ark.say_switch({**r, "privacy": ark.model_note(r["model"])}, "zh")
        self.assertIn("OpenCode · 火山方舟", text)

    def test_missing_key_stops_before_writing(self):
        r, calls = self.run_switch(key=False)
        self.assertEqual((r["ok"], r["reason"], calls), (False, "need_key", []))
        self.assertFalse(self.file.exists())
        self.assertTrue(r["next"][0].startswith("agentj secret request --name ARK_API_KEY"))
        self.assertIn("密钥卡", ark.say_switch(r, "zh"))

    def test_no_opencode(self):
        r, calls = self.run_switch(found={"opencode": None, "arkcli": None})
        self.assertEqual((r["reason"], calls), ("not_installed", []))
        self.assertIn("npm i -g @opencode/cli", ark.say_switch(r, "zh"))
        self.assertIn("arkcli not installed", ark.say_switch(r, "en"))

    def test_failed_probe_removes_the_added_block(self):
        r, _ = self.run_switch(reply={"ok": False, "reason": "call_failed", "target": "opencode", "from": "claude"}, key=None)
        self.assertTrue(r["config_restored"])
        self.assertNotIn("ark-api", self.cfg().get("provider", {}))
        self.assertTrue(r["next"])              # inside the fence: the key card is offered too
        self.assertIn("撤掉", ark.say_switch(r, "zh"))

    def test_failed_probe_keeps_an_owner_block(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"provider": {"ark-coding-plan": {"npm": "mine", "models": {}}}}))
        r, _ = self.run_switch(plan="coding", reply={"ok": False, "reason": "call_failed", "target": "opencode"})
        self.assertNotIn("config_restored", r)
        self.assertEqual(self.cfg()["provider"]["ark-coding-plan"]["npm"], "mine")
        self.assertIn("只给编程 Agent", ark.say_switch(r, "zh"))     # plan terms said with the plan

    def test_already_on_opencode_keeps_block(self):
        r, _ = self.run_switch(reply={"ok": False, "reason": "same", "target": "opencode", "from": "opencode"})
        self.assertIn("ark-api", self.cfg()["provider"])
        self.assertEqual(r["hint"], "agentj agent opencode --model ark-api/doubao-seed-2-1-pro-260915")

    def test_detect_versions(self):
        which = {"opencode": "/bin/opencode"}.get
        run = lambda argv, **k: SimpleNamespace(stdout="1.18.34\n", stderr="")   # noqa: E731
        self.assertEqual(ark.detect(which=which, run=run), {"opencode": "1.18.34", "arkcli": None})

    def test_cli_wiring(self):
        a = SimpleNamespace(result=False, worker=False, scan=False, undo=False, target="opencode", preset="ark", plan="payg",
                            model=None, dry_run=True, json=True)
        seen = {}
        with mock.patch.object(ark, "switch", lambda st, plan, model, dry_run: seen.update(plan=plan, dry=dry_run) or
                               {"ok": True, "dry_run": True}), redirect_stdout(io.StringIO()):
            self.assertEqual(sh.cli(a, None, "zh"), 0)
        self.assertEqual(seen, {"plan": "payg", "dry": True})

    def test_phone_words_route_to_the_preset(self):
        self.assertIn("--preset ark", sh.agent_text("/switch-harness 切到火山方舟"))
        self.assertIn("--preset ark", sh.agent_text("/switch-harness OpenCode doubao", "en"))
        self.assertNotIn("--preset ark", sh.agent_text("/switch-harness 切到 Codex"))
        self.assertIn("OpenCode · 火山方舟", sh.agent_text("/switch-harness"))


class Docs(unittest.TestCase):
    def test_skill_names_the_preset(self):
        skill = (AJ / "host/agentj/skills/agentj-switch-harness/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--preset ark", skill)

    @unittest.skipUnless((AJ / "site").is_dir(), "the site is a private source (not in the public export)")
    def test_site_page_names_the_preset(self):
        pages = AJ / "site/content/docs/45-opencode-ark"
        zh, en = (pages / "zh.src.md").read_text(encoding="utf-8"), (pages / "en.md").read_text(encoding="utf-8")
        for t in (zh, en):
            self.assertIn("agentj agent switch opencode --preset ark", t)
            self.assertIn("/api/v3", t)
            self.assertIn("/api/coding/v3", t)
        self.assertIn("封号", zh)
        self.assertIn("banned", en)


if __name__ == "__main__":
    unittest.main()
