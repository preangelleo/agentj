"""P219: a newly installed computer's first message never waits silently at a terminal nobody sees (Leo 10-13, a friend's
WSL2 Ubuntu), and Agent J's Claude Code defaults to Opus 5.5 · medium.

① harness_setup answers the harness's one-time terminal questions ahead (Claude Code: onboarding, theme, folder trust,
  bypass-mode confirmation; Codex: folder trust) — merge only, the owner's values and unrelated keys untouched, symlinks /
  invalid files left alone, 0600, idempotent; root + bypassPermissions detected.
② `agentj agent claude` writes the default model claude-opus-5-5 + effort medium unless the owner chose one, and prepares
  the folder; codex / opencode keep their own defaults.
③ The Claude adapter: `api_retry` (the model service unreachable) → one phone card per turn; a 400 "model not supported"
  → switches to the default model and sends the message again, once, with a card and no error reply; a terminal question
  printed outside stream-json → a 「在Agent J 主机上处理」 card quoting it (redacted); nothing at all on a fresh process → a
  "still no answer" card; root + bypassPermissions → `--permission-mode default` (stricter, never wider).
④ The phone's model label names the version ("Opus 5.5"), aliases resolved.
⑤ doctor: harness first-run setup, folder trust, socat.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import contextlib
import io
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agentj import agent as agents, cli, doctor, harness_setup, main_identity, preferences, session_choice  # noqa: E402
from agentj.state import State  # noqa: E402


def _secret():
    return "sk-ant-" + "api03-" + "Q" * 20 + "w" * 20      # built at run time: no key-shaped literal in the repo


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-p219-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.env = {"HOME": str(self.home)}

    def cj(self):
        return json.loads((self.home / ".claude.json").read_text())

    def settings(self):
        return json.loads((self.home / ".claude" / "settings.json").read_text())


# ------------------------------------------------------------------ ① harness_setup
class ClaudeFirstRun(_Home):
    def test_fresh_home_gets_every_answer_once(self):
        r = harness_setup.prepare("claude", str(self.work), self.env)
        self.assertEqual(sorted(r["done"]), ["bypass_confirm", "onboarding", "theme", "trust"])
        self.assertEqual(r["skipped"], {})
        d = self.cj()
        self.assertIs(d["hasCompletedOnboarding"], True)
        self.assertEqual(d["theme"], "dark")
        self.assertIs(d["projects"][str(self.work.resolve())]["hasTrustDialogAccepted"], True)
        self.assertIs(self.settings()["skipDangerousModePermissionPrompt"], True)
        for p in (self.home / ".claude.json", self.home / ".claude" / "settings.json"):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600, p)
        self.assertEqual(harness_setup.prepare("claude", str(self.work), self.env), {"done": [], "skipped": {}}, "idempotent")
        s = harness_setup.claude_status(str(self.work), self.env)
        self.assertEqual((s["onboarding"], s["trusted"], s["bypass_confirm"]), (True, True, True))

    def test_merge_only_the_owners_values_and_secrets_stay(self):
        key = _secret()
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text(json.dumps(
            {"env": {"ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_BASE_URL": "https://relay.example"}, "model": "opus",
             "skipDangerousModePermissionPrompt": False}))
        other = str(self.tmp / "elsewhere")
        (self.home / ".claude.json").write_text(json.dumps(
            {"theme": "light", "numStartups": 7, "hasCompletedOnboarding": False,
             "projects": {other: {"hasTrustDialogAccepted": False, "allowedTools": ["x"]},
                          str(self.work.resolve()): {"hasTrustDialogAccepted": False, "lastCost": 1.5}}}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r = harness_setup.prepare("claude", str(self.work), self.env)
        self.assertEqual(sorted(r["done"]), ["onboarding", "trust"])
        self.assertNotIn(key, out.getvalue())
        d = self.cj()
        self.assertEqual((d["theme"], d["numStartups"]), ("light", 7), "the owner's theme stays")
        self.assertEqual(d["projects"][other], {"hasTrustDialogAccepted": False, "allowedTools": ["x"]}, "other folders untouched")
        self.assertEqual(d["projects"][str(self.work.resolve())]["lastCost"], 1.5)
        s = self.settings()
        self.assertEqual(s["env"]["ANTHROPIC_AUTH_TOKEN"], key, "the key is carried through unchanged, never dropped")
        self.assertIs(s["skipDangerousModePermissionPrompt"], False, "an explicit owner value is never changed")
        self.assertEqual(s["model"], "opus")

    def test_parent_trust_counts_and_symlink_or_invalid_files_are_left_alone(self):
        (self.home / ".claude.json").write_text(json.dumps({"projects": {str(self.tmp.resolve()): {"hasTrustDialogAccepted": True}}}))
        self.assertTrue(harness_setup.claude_status(str(self.work), self.env)["trusted"], "a trusted parent covers the folder")
        real = self.tmp / "real.json"
        real.write_text("{}")
        (self.home / ".claude.json").unlink()
        (self.home / ".claude.json").symlink_to(real)
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text("{not json")
        r = harness_setup.prepare("claude", str(self.work), self.env)
        self.assertEqual(r["done"], [])
        self.assertEqual(r["skipped"], {"claude.json": "symlink", "settings.json": "invalid"})
        self.assertEqual(real.read_text(), "{}")
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(), "{not json")

    def test_claude_config_dir_is_honoured(self):
        env = {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.tmp / "cc")}
        harness_setup.prepare("claude", str(self.work), env)
        self.assertTrue((self.tmp / "cc" / ".claude.json").is_file())
        self.assertTrue((self.tmp / "cc" / "settings.json").is_file())
        self.assertFalse((self.home / ".claude.json").exists())

    def test_root_with_bypass_is_detected(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}))
        with mock.patch.object(harness_setup, "_is_root", return_value=True):
            self.assertTrue(harness_setup.root_bypass(str(self.work), self.env))
            self.assertFalse(harness_setup.root_bypass(str(self.work), {**self.env, "IS_SANDBOX": "1"}))
            (self.work / ".claude").mkdir()
            (self.work / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"defaultMode": "default"}}))
            self.assertFalse(harness_setup.root_bypass(str(self.work), self.env), "the folder's local setting is more specific")
        with mock.patch.object(harness_setup, "_is_root", return_value=False):
            self.assertFalse(harness_setup.root_bypass(str(self.work), self.env))


class CodexFirstRun(_Home):
    def test_trust_added_comments_kept_owner_answer_kept(self):
        cfg = self.home / ".codex" / "config.toml"
        cfg.parent.mkdir()
        cfg.write_text('# mine\nmodel = "gpt-6"\n\n[projects."/somewhere"]\ntrust_level = "untrusted"\n')
        self.assertEqual(harness_setup.codex_trusted(str(self.work), self.env), False)
        r = harness_setup.prepare("codex", str(self.work), self.env)
        self.assertEqual(r["done"], ["trust"])
        text = cfg.read_text()
        self.assertIn("# mine", text)
        self.assertIn('trust_level = "untrusted"', text)
        self.assertTrue(harness_setup.codex_trusted(str(self.work), self.env))
        self.assertEqual(harness_setup.prepare("codex", str(self.work), self.env)["done"], [])
        cfg.write_text(f'[projects."{self.work.resolve()}"]\ntrust_level = "untrusted"\n')
        self.assertEqual(harness_setup.prepare("codex", str(self.work), self.env)["done"], [], "an explicit no stays")
        self.assertFalse(harness_setup.codex_trusted(str(self.work), self.env))

    def test_opencode_needs_nothing(self):
        self.assertEqual(harness_setup.prepare("opencode", str(self.work), self.env), {"done": [], "skipped": {}})


class FenceDeps(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only")
    def test_missing_tools_name_the_command(self):
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("builtins.open", mock.mock_open(read_data='ID=ubuntu\nID_LIKE=debian\n')):
            d = harness_setup.fence_deps()
        self.assertEqual((d["bwrap"], d["socat"]), (False, False))
        self.assertEqual(d["hint"], "sudo apt-get install -y bubblewrap socat")


# ------------------------------------------------------------------ ② agentj agent
class AgentCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-p219c-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "config"),
                                           "CLAUDE_CONFIG_DIR": str(self.tmp / "cc"), "CODEX_HOME": str(self.tmp / "cx"),
                                           "AGENTJ_STATE_DIR": str(self.tmp / "st"), "AGENTJ_SKILL_LINK": "off",
                                           "AGENTJ_HARNESS_PREPARE": "on"})
        env.start()
        self.addCleanup(env.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")

    def agent(self, *args):
        out = io.StringIO()
        with mock.patch.object(session_choice, "interactive", return_value=False), \
                mock.patch("agentj.claude_auth.available", return_value=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                cli.main(["agent", *args])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_default_model_and_effort_then_the_owners_choice_stays(self):
        code, text = self.agent("claude", "--dir", str(self.work))
        self.assertEqual(code, 0, text)
        c = self.st.agent_config()
        self.assertEqual((c["model"], c["effort"]), ("claude-opus-5-5", "medium"))
        self.assertIn("信任工作目录", text)
        d = json.loads((self.tmp / "cc" / ".claude.json").read_text())
        self.assertIs(d["projects"][str(self.work.resolve())]["hasTrustDialogAccepted"], True)
        self.st.set_agent_model("sonnet")        # the owner picks another on the phone
        self.st.set_agent_effort("high")
        code, text = self.agent("claude", "--dir", str(self.work))
        c = self.st.agent_config()
        self.assertEqual((c["model"], c["effort"]), ("sonnet", "high"), "re-running keeps the owner's choice")
        code, text = self.agent("claude", "--dir", str(self.work), "--model", "claude-sonnet-5-5")
        self.assertEqual(self.st.agent_config()["model"], "claude-sonnet-5-5")

    def test_codex_keeps_its_own_default_and_gets_folder_trust(self):
        code, text = self.agent("codex", "--dir", str(self.work))
        self.assertEqual(code, 0, text)
        c = self.st.agent_config()
        self.assertEqual((c["model"], c["effort"]), (None, None))
        self.assertIn('trust_level = "trusted"', (self.tmp / "cx" / "config.toml").read_text())

    def test_doctor_row(self):
        self.st.set_agent_config("claude", str(self.work))
        row = doctor.check_harness_setup(self.st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("agentj agent claude", row["hint"])
        harness_setup.prepare("claude", str(self.work))
        with mock.patch.object(harness_setup, "fence_deps", return_value={"bwrap": True, "socat": True, "hint": ""}):
            self.assertEqual(doctor.check_harness_setup(self.st)["status"], doctor.OK)
        with mock.patch.object(harness_setup, "fence_deps", return_value={"bwrap": True, "socat": False,
                                                                           "hint": "sudo apt-get install -y socat"}):
            row = doctor.check_harness_setup(self.st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertIn("sudo apt-get install -y socat", row["hint"])


# ------------------------------------------------------------------ ③ the adapter
class _Host:
    def __init__(self, st):
        self.st, self.lang, self.ask_ttl = st, "zh", 30
        self.preferences = preferences.defaults()
        self.perm_claimed = asyncio.Event()
        self.perm_claimed.set()
        self.notices, self.local, self.texts, self.failed, self.asks = [], [], [], 0, {}

    def new_perm_env(self):
        return {}

    def agent_status(self, s):
        pass

    def agent_notice(self, t):
        self.notices.append(t)

    def local_notice(self, t):
        self.local.append(t)

    def agent_text(self, t):
        self.texts.append(t)

    def turn_failed(self):
        self.failed += 1

    def meter_update(self, **kw):
        pass


class Adapter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-p219a-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.log = self.tmp / "argv.jsonl"
        wrapper = self.tmp / "claude"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fakep219.py'} \"$@\"\n")
        wrapper.chmod(0o700)
        env = mock.patch.dict(os.environ, {"AGENTJ_CLAUDE_BIN": str(wrapper), "FAKE219_LOG": str(self.log),
                                           "AGENTJ_STATE_DIR": str(self.tmp / "st"), "HOME": str(self.tmp),
                                           "XDG_CONFIG_HOME": str(self.tmp / "config"), "CLAUDE_CONFIG_DIR": str(self.tmp / "cc")})
        env.start()
        self.addCleanup(env.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")
        self.st.set_agent_config("claude", str(self.work), None, fence=False)
        for p in (mock.patch.object(agents.ClaudeAgent, "authorized_launch_argv", new=self._launch),
                  mock.patch.object(main_identity, "audit", return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        self.host = _Host(self.st)

    @staticmethod
    async def _launch(self, argv):
        return argv

    def make(self, model=None, mode=""):
        os.environ["FAKE219_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE219_MODE", None)
        cfg = {**self.st.agent_config(), "model": model}
        a = agents.ClaudeAgent(self.host, cfg)
        self.addAsyncCleanup(a.stop)
        return a

    def starts(self):
        return [json.loads(x)["argv"] for x in self.log.read_text().splitlines()] if self.log.exists() else []

    async def test_model_service_retries_reach_the_phone_once(self):
        a = self.make("claude-opus-5-5", "retry")
        await asyncio.wait_for(a.watched(a.turn("hi")), 20)
        cards = [n for n in self.host.notices if "连不上模型服务" in n]
        self.assertEqual(len(cards), 1, self.host.notices)
        self.assertIn("第 5/10 次", cards[0])
        self.assertEqual(self.host.texts, ["ECHO: hi"])

    async def test_unsupported_model_falls_back_to_opus_once(self):
        a = self.make(None, "model400")
        await asyncio.wait_for(a.watched(a.turn("hi")), 30)
        self.assertEqual(self.host.texts, ["ECHO: hi"], "no 'API Error' reply bubble; the message answered on the default")
        self.assertEqual(self.host.failed, 0)
        card = [n for n in self.host.notices if "Opus 5.5" in n]
        self.assertTrue(card and "Claude Fable 5.1 is not supported" in card[0], self.host.notices)
        self.assertEqual(self.st.agent_config()["model"], "claude-opus-5-5", "the default stays selected")
        argvs = self.starts()
        self.assertEqual(len(argvs), 2)
        self.assertNotIn("--model", argvs[0])
        self.assertEqual(argvs[1][argvs[1].index("--model") + 1], "claude-opus-5-5")

    async def test_default_model_failing_is_reported_not_retried(self):
        with mock.patch.object(harness_setup, "DEFAULT_CLAUDE_MODEL", "claude-fable-5-1"):
            a = self.make("claude-fable-5-1", "model400")
            await asyncio.wait_for(a.watched(a.turn("hi")), 20)
        self.assertEqual(len(self.starts()), 1)
        self.assertEqual(self.host.failed, 1)
        self.assertTrue(any("not supported" in n for n in self.host.notices))

    async def test_terminal_question_becomes_a_card_with_its_words(self):
        a = self.make("claude-opus-5-5", "prompt")
        with mock.patch.object(agents, "STALL_SECS", 1), mock.patch.object(agents, "STALL_TICK", 0.2):
            t = asyncio.create_task(a.watched(a.turn("hi")))
            for _ in range(100):
                if self.host.local:
                    break
                await asyncio.sleep(0.1)
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self.assertEqual(len(self.host.local), 1, self.host.local)
        card = self.host.local[0]
        self.assertIn("卡在Agent J 主机终端", card)
        self.assertIn("Do you trust the files in this folder?", card)
        self.assertIn("agentj agent claude", card)
        self.assertNotIn(_secret()[:20], card)
        self.assertNotIn("Z" * 30, card, "redacted before it reaches the phone")

    async def test_nothing_at_all_on_a_fresh_process_is_said(self):
        a = self.make("claude-opus-5-5", "quiet")
        with mock.patch.object(agents, "STALL_SECS", 0.5), mock.patch.object(agents, "STALL_QUIET_SECS", 1), \
                mock.patch.object(agents, "STALL_TICK", 0.2):
            t = asyncio.create_task(a.watched(a.turn("hi")))
            for _ in range(100):
                if self.host.local:
                    break
                await asyncio.sleep(0.1)
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self.assertEqual(len(self.host.local), 1)
        self.assertIn("没有任何回应", self.host.local[0])

    async def test_an_open_phone_card_is_the_wait_not_a_stall(self):
        a = self.make("claude-opus-5-5", "quiet")
        fut = asyncio.get_running_loop().create_future()
        self.host.asks = {"r": mock.Mock(fut=fut)}
        with mock.patch.object(agents, "STALL_SECS", 0.3), mock.patch.object(agents, "STALL_QUIET_SECS", 0.5), \
                mock.patch.object(agents, "STALL_TICK", 0.1):
            t = asyncio.create_task(a.watched(a.turn("hi")))
            await asyncio.sleep(1.5)
            self.assertEqual(self.host.local, [])
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        fut.cancel()

    def test_root_bypass_starts_stricter(self):
        a = agents.ClaudeAgent(None, {"kind": "claude", "dir": str(self.work), "model": None})
        with mock.patch.object(harness_setup, "root_bypass", return_value=True):
            argv = a.argv(None)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "default")
        with mock.patch.object(harness_setup, "root_bypass", return_value=False):
            self.assertNotIn("--permission-mode", a.argv(None))


# ------------------------------------------------------------------ ④ the label
class Label(unittest.TestCase):
    def test_versions(self):
        p = agents.pretty_model
        self.assertEqual(p("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(p("claude-fable-5-1"), "Fable 5.1")
        self.assertEqual(p("claude-sonnet-5-5[1m]"), "Sonnet 5.5 (1M)")
        self.assertIsNone(p("opus"))
        self.assertIsNone(p("gpt-6.1-sol"))

    def test_alias_and_default_resolve(self):
        a = agents.ClaudeAgent(None, {"kind": "claude", "dir": "/tmp", "model": None})
        a.models_cache = [{"id": "default", "name": "Default (recommended)", "resolved": "claude-opus-5-5"},
                          {"id": "opus", "name": "Opus", "resolved": "claude-opus-5-5"},
                          {"id": "x-custom", "name": "Custom", "resolved": ""}]
        self.assertEqual(a.model_name("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(a.model_name("opus"), "Opus 5.5")
        self.assertEqual(a.model_name("default"), "Opus 5.5")
        self.assertEqual(a.model_name("x-custom"), "Custom")


if __name__ == "__main__":
    unittest.main()
