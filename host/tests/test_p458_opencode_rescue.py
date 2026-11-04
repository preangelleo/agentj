"""#458 (0.26.8a2 hotfix, paid ticket) contract: AI login rescue's 「现在检查」 works on OpenCode 2.x.

Before: auth_rescue.health_check ran the bots' v1 one-shot `opencode run --pure --agent agentj-bot --format json` for every
OpenCode; 2.2.5 refuses `--pure` ("Unrecognized flag") → the row was always 「暂未确认登录状态 / 没通过」, even with one of
OpenCode's own free models (no key). Now:
- the generation is read from `opencode --version`; a line naming neither → `opencode run --help` (no --pure = 2.x);
- 2.x is checked through agent_opencode2's serve + session probe (its read-only rules), never `run`;
- 1.x keeps the v1 command; a v1 command whose flags are refused falls back to the 2.x probe, else "cli";
- a free model (opencode/…, no key) passes, and its failure is never a login problem;
- "cli" (CLI incompatible) and "config" (model / provider config) are their own health results, never login / quota.
The 2.x stand-in rejects `--pure` like the real 2.2.5. No network, no real keys.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import agent_opencode2 as oc2, auth_rescue as ar, opencode_free as of

HERE = pathlib.Path(__file__).resolve().parent

# One executable file (shebang + body): a stand-in that a fence can run as is.
FAKE = r'''#!{py}
import json, os, sys
a = sys.argv[1:]
gen = os.environ.get("FAKE_OC_GEN", "2")
if a[:1] in (["--version"], ["-v"]):
    print(os.environ.get("FAKE_OC_VERSION_LINE") or ("2.2.5" if gen == "2" else "1.18.34")); sys.exit(0)
if a[:1] == ["run"]:
    open(os.environ["FAKE_RUN_LOG"], "a").write(" ".join(a) + "\n")
    if "--help" in a:
        print("opencode run [message..]\n\n  --model  -m\n  --agent\n  --format  json|default"
              + ("\n  --pure  run without plugins" if gen == "1" else "")); sys.exit(0)
    if gen == "2" and "--pure" in a:            # real 2.2.5 (#458 screenshot): the v1 flag is refused
        print("Error: Unrecognized flag: --pure", file=sys.stderr); sys.exit(1)
    sys.stdin.read()
    print(json.dumps({"type": "text", "part": {"text": "Hi there"}})); sys.exit(0)
if a[:1] == ["serve"] and gen == "2" and not os.environ.get("FAKE_OC_NO_SERVE"):
    sys.path.insert(0, {here!r})
    import fakeopencode2
    fakeopencode2.main(); sys.exit(0)
print("Error: unknown command", file=sys.stderr); sys.exit(2)
'''


class _Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p458-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(t.cleanup)
        root = pathlib.Path(t.name)
        self.root = root
        exe = root / "bin" / "opencode"
        exe.parent.mkdir()
        exe.write_text(FAKE.replace("{py}", sys.executable).replace("{here!r}", repr(str(HERE))))
        exe.chmod(0o700)
        self.exe = str(exe)
        self.runlog, self.apilog = root / "run.log", root / "api.jsonl"
        none = str(root / "none")
        env = {"AGENTJ_OPENCODE_BIN": self.exe, "FAKE_RUN_LOG": str(self.runlog), "FAKE_OC_LOG": str(self.apilog),
               "XDG_DATA_HOME": str(root / "data"), "XDG_CONFIG_HOME": str(root / "cfg"),
               "AGENTJ_STATE_DIR": str(root / "state"), "AGENTJ_OC_FREE_LIST": "off", "FAKE_OC2_CONNECTED": "deepseek,opencode",
               "OPENCODE_CONFIG_CONTENT": json.dumps({"model": "deepseek/chat"}),
               "AGENTJ_CLAUDE_BIN": none, "AGENTJ_CODEX_BIN": none, "AGENTJ_KIMI_BIN": none}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for k in ("FAKE_OC_GEN", "FAKE_OC_VERSION_LINE", "FAKE_OC_NO_SERVE", "FAKE_OC2_FAIL", "FAKE_OC2_ERROR_MSG"):
            os.environ.pop(k, None)
        of._cache.clear()
        self.addCleanup(of._cache.clear)
        for obj, name, v in ((oc2, "PROBE_POLL", 0.05), (of, "WARM_WAITS", (0.1,))):
            w = mock.patch.object(obj, name, v)
            w.start()
            self.addCleanup(w.stop)

    def check(self, gen="2", **extra):
        os.environ["FAKE_OC_GEN"] = gen
        os.environ.update(extra)
        return ar.health_check("opencode", 20)

    def runs(self):
        return self.runlog.read_text().splitlines() if self.runlog.exists() else []

    def api(self):
        return [json.loads(x) for x in self.apilog.read_text().splitlines()] if self.apilog.exists() else []


class Generations(_Base):
    def test_stand_in_refuses_pure_like_2_2_5(self):
        """The bug: the v1 command against 2.x is refused before any model is asked."""
        import subprocess
        os.environ["FAKE_OC_GEN"] = "2"
        r = subprocess.run([self.exe, "run", "--pure", "--agent", "agentj-bot", "--format", "json"], input="hi",
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unrecognized flag: --pure", r.stderr)

    def test_v2_passes_through_its_api_never_run_pure(self):
        res = self.check("2")
        self.assertEqual(res, {"ok": True, "cls": None, "why": "ok"})
        self.assertFalse([x for x in self.runs() if "--pure" in x], "2.x must never get `run --pure`")
        reqs = self.api()
        made = next(r for r in reqs if r["m"] == "POST" and r["p"] == "/api/session")
        rules = made["b"]["permissions"]
        self.assertEqual(rules[-1]["effect"], "deny")        # agent_opencode2's read-only rules, unchanged
        self.assertTrue(any(r["m"] == "DELETE" and r["p"].startswith("/api/session/") for r in reqs))

    def test_v1_keeps_its_command(self):
        res = self.check("1")
        self.assertTrue(res["ok"], res)
        self.assertIn("run --pure --agent agentj-bot --format json", "\n".join(self.runs()))
        self.assertFalse([r for r in self.api() if r["p"].startswith("/api/")])

    def test_unreadable_version_line_asks_run_help(self):
        res = self.check("2", FAKE_OC_VERSION_LINE="opencode dev-build")
        self.assertTrue(res["ok"], res)
        self.assertIn("run --help", "\n".join(self.runs()))
        self.assertFalse([x for x in self.runs() if "--pure" in x])
        self.assertEqual(ar._oc_generation(self.exe, dict(os.environ))[0], 2)
        os.environ.update(FAKE_OC_GEN="1", FAKE_OC_VERSION_LINE="opencode dev-build")
        self.assertEqual(ar._oc_generation(self.exe, dict(os.environ))[0], 1)

    def test_run_help_has_no_stdin(self):
        os.environ.update(FAKE_OC_GEN="2", FAKE_OC_VERSION_LINE="?")
        with mock.patch.object(ar.subprocess, "run", wraps=ar.subprocess.run) as run:
            ar._oc_generation(self.exe, dict(os.environ))
        call = next(c for c in run.call_args_list if c.args[0][1:3] == ["run", "--help"])
        self.assertEqual(call.kwargs["stdin"], ar.subprocess.DEVNULL)

    def test_refused_v1_flags_fall_back_to_the_api_probe(self):
        res = self.check("2", FAKE_OC_VERSION_LINE="1.99.0")    # a version line that lies: the refusal decides
        self.assertTrue(res["ok"], res)
        self.assertTrue([x for x in self.runs() if "--pure" in x])
        self.assertTrue(any(r["p"] == "/api/session" for r in self.api()))


class Categories(_Base):
    def test_bad_key_is_login(self):
        self.assertEqual(self.check("2", FAKE_OC2_FAIL="BADKEY"), {"ok": False, "cls": "login", "why": "login"})

    def test_unroutable_model_is_config_not_login(self):
        self.assertEqual(self.check("2", FAKE_OC2_FAIL="NOKEY"), {"ok": False, "cls": None, "why": "config"})

    def test_cli_that_speaks_neither_is_cli(self):
        self.assertEqual(self.check("2", FAKE_OC_NO_SERVE="1"), {"ok": False, "cls": None, "why": "cli"})
        self.assertEqual(self.check("2", FAKE_OC_NO_SERVE="1", FAKE_OC_VERSION_LINE="1.99.0"),
                         {"ok": False, "cls": None, "why": "cli"})

    def test_quota_words_stay_quota(self):
        res = ar._oc_result(of.ProbeError("native_failed", "APIError 402 Insufficient balance"), False)
        self.assertEqual(res["cls"], "balance")
        self.assertEqual(ar._oc_result(of.ProbeError("free_tier", None), True)["cls"], "rate_limit")


class FreeModels(_Base):
    def setUp(self):
        super().setUp()
        os.environ["OPENCODE_CONFIG_CONTENT"] = json.dumps({"model": "opencode/glm-5.3-flash-free"})
        os.environ["FAKE_OC2_CONNECTED"] = "opencode"

    def test_v2_free_model_without_key_passes(self):
        res = self.check("2")
        self.assertTrue(res["ok"], res)
        made = next(r for r in self.api() if r["m"] == "POST" and r["p"] == "/api/session")
        self.assertEqual(made["b"]["model"], {"id": "glm-5.3-flash-free", "providerID": "opencode"})

    def test_v1_free_model_takes_the_formal_path(self):
        with mock.patch.object(of, "probe", return_value="Hello!") as p:
            res = self.check("1")
        self.assertTrue(res["ok"], res)
        self.assertEqual(p.call_args.args[1], "opencode/glm-5.3-flash-free")
        self.assertFalse(self.runs(), "a free model is refused as the bots' tool-free agent: no `run`")

    def test_free_model_failure_is_never_login(self):
        self.assertNotEqual(self.check("2", FAKE_OC2_FAIL="BADKEY")["cls"], "login")


class Phone(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p458-view-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(t.cleanup)
        self.host = SimpleNamespace(st=SimpleNamespace(root=t.name, log=lambda *a, **k: None), lang="zh", agent=None,
                                    agent_notice=lambda *_: None)
        p = mock.patch.object(ar, "binary", return_value="/x/opencode")
        p.start()
        self.addCleanup(p.stop)

    def row(self, res, lang="zh"):
        self.host.lang = lang
        r = ar.Rescue(self.host)
        r._health_mark("opencode", res)
        with mock.patch("agentj.harness.claude_login", return_value=("ok", "")):
            v = r.view()
        return v, next(x for x in v["harnesses"] if x["kind"] == "opencode")

    def test_cli_and_config_have_their_own_line(self):
        v, row = self.row({"ok": False, "cls": None, "why": "cli"})
        self.assertEqual(row["health"]["why"], "cli")
        self.assertIn("命令行不兼容", row["health"]["detail"])
        self.assertIn("OpenCode", row["health"]["detail"])
        self.assertNotIn(row["state"], ("login", "balance", "rate_limit"))
        self.assertFalse(v["dot"])
        _, row = self.row({"ok": False, "cls": None, "why": "config"}, "en")
        self.assertTrue(row["health"]["detail"].startswith("Configuration error"))

    def test_login_keeps_no_detail(self):
        _, row = self.row({"ok": False, "cls": "login", "why": "login"})
        self.assertNotIn("why", row["health"])
        self.assertNotIn("detail", row["health"])

    def test_pass_is_ok(self):
        _, row = self.row({"ok": True, "cls": None, "why": "ok"})
        self.assertEqual((row["state"], row["health"]["ok"]), ("ok", True))

    def test_ready_is_not_empty(self):
        self.assertTrue(ar.READY["opencode"])
        self.assertTrue(ar.screen_check("opencode", "/nonexistent")["ready"])

    def test_check_now_on_2x_paints_ok(self):
        """「现在检查」 = refresh_health → health_check per target → the row is 正常."""
        r = ar.Rescue(self.host)
        with mock.patch.object(ar, "health_targets", return_value=["opencode"]), \
                mock.patch.object(ar, "health_check", return_value={"ok": True, "cls": None, "why": "ok"}), \
                mock.patch("agentj.harness.claude_login", return_value=("ok", "")):
            async def go():
                await r.refresh_health()
                await r.health_task
                return r.view()
            v = asyncio.run(go())
        row = next(x for x in v["harnesses"] if x["kind"] == "opencode")
        self.assertEqual(row["state"], "ok")


if __name__ == "__main__":
    unittest.main()
