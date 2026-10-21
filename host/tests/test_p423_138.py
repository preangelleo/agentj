"""P423 #138: a used-up quota / usage limit is never 「没登录」.

Customer seat (WSL Ubuntu, Kimi Code CLI 2.2.0): the Kimi login was fine, but the membership's monthly quota was used up. Kimi's
ACP answered auth_required (-32000) with "[provider.auth_error] 403 You've reached your monthly usage limit for this billing
cycle"; the probe said not_logged_in, the scan / switch / AI login rescue all said "sign in again" — three re-logins for nothing.

- auth_rescue.quota_used: the one used-up-quota test (Kimi / Claude / Codex wording); classify puts it before the login words.
- agent_kimi.probe (Kimi, Qwen, DeepSeek Harness): quota → quota_exhausted; a plain auth_required stays not_logged_in.
- acp.AcpAgent runtime: no login rescue, the quota line (Kimi: its own console link), rescue state "balance".
- harness.probe: Claude / Codex output with a used-up limit → quota_exhausted; switch_harness.reason_text says so.
- OpenCode provider_error_reason: a 403 auth error with a monthly usage limit → quota_window (shared regex).
- --scan: after 「→ 现在能切到」 one copyable `agentj agent switch <kind>` per harness the CLI takes.
Run (in host/tests): python -m unittest test_p423_138
"""
import _hermetic  # noqa: F401,I001
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentj import agent_kimi, agent_opencode, auth_rescue, harness  # noqa: E402
from agentj import switch_harness as sw  # noqa: E402
from test_p305_kimi import FAKE, AcpCase  # noqa: E402

KIMI_403 = "[provider.auth_error] 403 You've reached your monthly usage limit for this billing cycle"
CLAUDE_LIMIT = "Claude AI usage limit reached|1760000000"
CODEX_LIMIT = "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), or try again in 2 days 4 hours."


class Classify(unittest.TestCase):
    def test_quota_words_are_never_login(self):
        for t in (KIMI_403, CLAUDE_LIMIT, CODEX_LIMIT, "insufficient_quota", "401 本月额度已用完，请续费",
                  "Unauthorized: monthly usage limit reached"):
            self.assertTrue(auth_rescue.quota_used(t), t)
            self.assertNotEqual(auth_rescue.classify("kimi", t), "login", t)

    def test_plain_login_words_stay_login(self):
        for t in ("Authentication required", "Not logged in · Please run /login", "401 Unauthorized", "token expired"):
            self.assertFalse(auth_rescue.quota_used(t), t)
            self.assertEqual(auth_rescue.classify("kimi", t), "login", t)
        self.assertFalse(auth_rescue.quota_used("429 Too Many Requests"))


class KimiProbe(unittest.TestCase):
    def probe(self, **env):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
            with self.assertRaises(agent_kimi.ProbeError) as e:
                agent_kimi.probe(FAKE, {"HOME": d, "PATH": os.environ["PATH"], **env}, 20)
            return e.exception.args[0]

    def test_monthly_limit_at_prompt_is_quota(self):
        self.assertEqual(self.probe(FAKE_ACP_PROMPT_ERR=KIMI_403), "quota_exhausted")

    def test_monthly_limit_in_auth_required_data_is_quota(self):
        self.assertEqual(self.probe(FAKE_ACP_AUTH="1", FAKE_ACP_AUTH_DATA=KIMI_403), "quota_exhausted")

    def test_plain_auth_required_is_not_logged_in(self):
        self.assertEqual(self.probe(FAKE_ACP_AUTH="1"), "not_logged_in")
        self.assertEqual(self.probe(FAKE_ACP_PROMPT_ERR="Authentication required"), "not_logged_in")

    def test_rate_limit_is_its_own_code(self):
        self.assertEqual(self.probe(FAKE_ACP_PROMPT_ERR="429 Too Many Requests"), "rate_limited")

    def test_health_check_records_balance_not_login(self):
        with mock.patch.object(auth_rescue, "binary", lambda k: FAKE), \
                mock.patch.dict(os.environ, {"FAKE_ACP_PROMPT_ERR": KIMI_403}):
            res = auth_rescue.health_check("kimi", 20)
        self.assertEqual((res["ok"], res["cls"]), (False, "balance"))


class KimiRuntime(AcpCase):
    def check_quota(self):
        self.host.auth_rescue.signal.assert_called_with("kimi", "balance")
        self.assertNotIn(mock.call("kimi", "login"), self.host.auth_rescue.signal.call_args_list)
        n = "\n".join(self.host.notices)
        self.assertIn("Kimi 本月额度已用完", n)
        self.assertIn("不是没登录", n)
        self.assertIn("https://www.kimi.com/code/console", n)
        self.assertNotIn("kimi login", n)

    async def test_monthly_limit_in_a_turn_is_no_login_rescue(self):
        os.environ["FAKE_ACP_PROMPT_ERR"] = KIMI_403
        a = self.make()
        await a.turn("hi")
        self.check_quota()

    async def test_monthly_limit_on_session_open_is_no_login_rescue(self):
        os.environ["FAKE_ACP_AUTH"] = "1"
        os.environ["FAKE_ACP_AUTH_DATA"] = KIMI_403
        a = self.make()
        await a.turn("hi")
        self.check_quota()

    async def test_english_quota_line(self):
        os.environ["FAKE_ACP_PROMPT_ERR"] = KIMI_403
        a = self.make()
        self.host.lang = "en"
        await a.turn("hi")
        self.assertTrue(any("used up" in n and "no new login" in n for n in self.host.notices), self.host.notices)

    async def test_plain_auth_required_still_lights_the_rescue(self):
        os.environ["FAKE_ACP_AUTH"] = "1"
        a = self.make()
        await a.turn("hi")
        self.host.auth_rescue.signal.assert_called_with("kimi", "login")
        self.assertTrue(any("kimi login" in n for n in self.host.notices))

    def tearDown(self):
        for k in ("FAKE_ACP_PROMPT_ERR", "FAKE_ACP_AUTH_DATA"):
            os.environ.pop(k, None)
        super().tearDown()


LIMITED = r'''#!/usr/bin/env python3
import json, os, sys
kind = os.path.basename(sys.argv[0])
if sys.argv[1:] == ["--version"]:
    print({"claude": "2.3.1 (Claude Code)", "codex": "codex-cli 0.160.0", "opencode": "1.18.40"}[kind]); sys.exit(0)
sys.stdin.read()
if kind == "claude":
    print(json.dumps({"type": "result", "is_error": True, "result": ''' + repr(CLAUDE_LIMIT) + r''',
                      "usage": {"input_tokens": 0, "output_tokens": 0}}))
elif kind == "codex":
    for e in ({"type": "thread.started"}, {"type": "turn.started"}, {"type": "error", "message": ''' + repr(CODEX_LIMIT) + r'''},
              {"type": "turn.failed", "error": {"message": ''' + repr(CODEX_LIMIT) + r'''}}):
        print(json.dumps(e))
sys.exit(1)
'''


class ClaudeCodexProbe(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p423-138-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(t.cleanup)
        root = pathlib.Path(t.name)
        env = {"CLAUDE_CONFIG_DIR": str(root / "claude"), "CODEX_HOME": str(root / "codex"),
               "CLAUDE_CODE_OAUTH_TOKEN": "p423-canary"}             # signed in: the login is not the problem
        (root / "claude").mkdir()
        (root / "codex").mkdir()
        (root / "codex/auth.json").write_text("{}")
        for k in harness.NAMES:
            p = root / "bin" / k
            p.parent.mkdir(exist_ok=True)
            p.write_text(LIMITED)
            p.chmod(0o755)
            env[harness.BIN_ENV[k]] = str(p)
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(harness.sys, "platform", "linux")
        p.start()
        self.addCleanup(p.stop)

    def reason(self, kind):
        d = harness.probe(only=(kind,), timeout=20)
        self.assertNotIn("p423-canary", json.dumps(d))
        return next(h for h in d["harnesses"] if h["name"] == kind)["probe_reason"]

    def test_claude_usage_limit(self):
        self.assertEqual(self.reason("claude"), "quota_exhausted")

    def test_codex_usage_limit(self):
        self.assertEqual(self.reason("codex"), "quota_exhausted")

    def test_limit_code_reads_error_words_only(self):
        self.assertIsNone(harness._limit_code(json.dumps({"type": "result", "is_error": True, "quota": 1,
                                                          "result": "Invalid API key · Please run /login"})))
        self.assertEqual(harness._limit_code(json.dumps({"type": "result", "result": CLAUDE_LIMIT})), "quota_exhausted")


class Texts(unittest.TestCase):
    def test_reason_text_says_quota_not_login(self):
        for kind in ("kimi", "claude", "codex", "qwen"):
            zh, en = sw.reason_text("quota_exhausted", kind, "zh"), sw.reason_text("quota_exhausted", kind, "en")
            self.assertIn("不是没登录", zh)
            self.assertIn("not a sign-in problem", en)
            self.assertNotIn("login --", zh + en)
        self.assertIn("https://www.kimi.com/code/console", sw.reason_text("quota_exhausted", "kimi", "zh"))
        self.assertIn("https://www.kimi.ai/code/console", sw.reason_text("quota_exhausted", "kimi", "en"))
        self.assertIn("不是没登录", sw.reason_text("rate_limited", "kimi", "zh"))

    def test_kimi_quota_text_uses_the_repo_console_links(self):
        self.assertIn("https://www.kimi.ai/code/console", agent_kimi.quota_text("global", "en"))
        self.assertIn("Kimi 本月额度已用完——不是没登录，不用重新登录", agent_kimi.quota_text(None, "zh"))
        page = HERE.parents[1] / "site/content/docs/41-kimi/zh.md"
        if not page.is_file():
            self.skipTest("no site/ (public export ships host/ alone)")
        site = page.read_text()
        for r in agent_kimi.REGIONS:
            self.assertIn(f"https://www.{agent_kimi.SITE[r]}/code/console", site)

    def test_rescue_verify_word(self):
        self.assertIn("不用再重新登录", auth_rescue.WHY["zh"]["quota"])
        self.assertIn("no new sign-in", auth_rescue.WHY["en"]["quota"])


class OpenCode(unittest.TestCase):
    def test_kimi_monthly_limit_as_auth_error_is_quota_window(self):
        r = agent_opencode.provider_error_reason("ProviderAuthError", {"statusCode": 403, "message": KIMI_403})
        self.assertEqual(r, "quota_window")
        self.assertEqual(agent_opencode.provider_error_reason("APIError", {"statusCode": 429, "message": KIMI_403}),
                         "quota_window")
        self.assertEqual(agent_opencode.provider_error_reason("ProviderAuthError", {"statusCode": 401, "message": "bad key"}),
                         "login")


class Scan(unittest.TestCase):
    def scan(self, lang="zh"):
        hs = [{"name": "claude", "installed": True, "callable": False, "version": "2.3.1", "reason": "quota_exhausted"},
              {"name": "kimi", "installed": True, "callable": True, "version": "2.2.0"},
              {"name": "codex", "installed": True, "callable": True, "version": "0.160.0"},
              {"name": "core", "installed": True, "callable": True, "version": None}]
        return sw._say_scan({"ok": True, "harnesses": hs, "current": "codex"}, lang).splitlines()

    def test_copyable_switch_command_after_can_switch_line(self):
        lines = self.scan()
        i = next(i for i, x in enumerate(lines) if x.startswith("→ 现在能切到："))
        self.assertIn("Kimi Code", lines[i])
        self.assertEqual(lines[i + 1:], ["  agentj agent switch kimi"])   # not the current one; core has no switch target
        self.assertTrue(any("不是没登录" in x for x in lines))
        self.assertIn("  agentj agent switch kimi", self.scan("en"))

    def test_switch_cli_names_match_the_parser(self):
        src = (HERE.parent / "agentj/cli.py").read_text()
        self.assertIn('ag.add_argument("target", nargs="?", choices=' + json.dumps(list(sw.SWITCH_CLI)).replace('", "', '", "'),
                      src)


if __name__ == "__main__":
    unittest.main()
