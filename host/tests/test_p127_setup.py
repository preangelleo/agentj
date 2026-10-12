"""P127 setup at Leo's 10-10 switch-over (0.17.4a1 fresh install on a Linux workstation):
① agent.session_mode: shared by default for all three harnesses; an "independent" the owner never chose is never flipped
  silently (terminal: y/N; otherwise a notice and doctor row), a chosen one is kept; --shared / --independent.
② `agentj agent` / serve start-up line follows the session mode (shared is never described as fenced), zh + en.
③ an old ~/.local/state/agentjarvis-alpha is never adopted silently: terminal y/N with what it holds (no key), otherwise
  exit 2 naming --migrate-legacy / --fresh; a serve still running on it keeps using it.
④ shared Claude with several live sessions in the folder: list them (name, last activity, first message), pick one in a
  terminal, otherwise print the list + the config line; one session = attach picks it (no pin written).
⑤ doctor says why it waits for a just-started serve's first relay connection.
Every test uses a throw-away HOME / XDG_CONFIG_HOME / CLAUDE_CONFIG_DIR / state dir.
"""
import _hermetic  # noqa: F401,I001
import contextlib
import io
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent
sys.path.insert(0, str(HOST))
from agentj import cli, doctor, migrate, preferences, session_choice, state  # noqa: E402
from agentj.state import State  # noqa: E402


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="aj-p127s-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.tmp), "XDG_CONFIG_HOME": str(self.tmp / "config"),
                                           "CLAUDE_CONFIG_DIR": str(self.tmp / "cc"), "AGENTJ_STATE_DIR": str(self.tmp / "st"),
                                           "AGENTJ_SKILL_LINK": "off"})
        env.start()
        self.addCleanup(env.stop)
        self.st = State()
        self.st.init(relay="ws://127.0.0.1:1")

    def user_config(self, text: str):
        p = preferences.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def agent(self, *args, answers=None, tty=False):
        """Run `agentj agent …` in-process. answers = what input() returns, in order (None: input must not be called)."""
        out, err = io.StringIO(), io.StringIO()
        calls = []

        def fake_input(prompt=""):
            calls.append(prompt)
            if answers is None:
                raise AssertionError("asked although nothing should be asked: " + prompt)
            return answers.pop(0)
        with mock.patch("builtins.input", fake_input), mock.patch.object(session_choice, "interactive", return_value=tty), \
                mock.patch("agentj.claude_auth.available", return_value=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main(["agent", *args])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue() + err.getvalue(), calls

    def mode(self):
        return preferences.get(preferences.effective(self.st), "agent.session_mode")


class SessionModeDefault(_Env):
    def test_factory_default_is_shared_for_every_harness(self):
        self.assertEqual(preferences.defaults()["agent"]["session_mode"], "shared")
        self.assertEqual(preferences.SCHEMA["agent.session_mode"]["default"], "shared")
        for kind in ("claude", "codex", "opencode"):
            args = [kind, "--dir", str(self.work)] + (["--model", "zhipuai/glm-5.3"] if kind == "opencode" else [])
            code, text, _ = self.agent(*args)
            self.assertEqual(code, 0, text)
            self.assertEqual(self.st.agent_config()["session_mode"], "shared", kind)
            self.assertNotIn("会话模式：", text, "nothing to settle with the default")
        self.assertIsNone(session_choice.user_value(), "the default is never written into the owner's file")

    def test_unchosen_independent_without_terminal_is_kept_and_named(self):
        self.user_config('{version: 1, agent: {session_mode: "independent"}}\n')
        code, text, _ = self.agent("claude", "--dir", str(self.work))
        self.assertEqual(code, 0, text)
        self.assertEqual(self.mode(), "independent", "never flipped silently")
        self.assertIn("agentj agent claude --shared", text)
        self.assertIn("agentj agent claude --independent", text)
        rows = [r for r in doctor.run(offline=True) if r["id"] == "session"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], doctor.WARN)
        self.assertIn("--shared", rows[0]["hint"])

    def test_terminal_yes_switches_to_the_shared_default(self):
        self.user_config('{version: 1, agent: {session_mode: "independent"}}\n')
        code, text, calls = self.agent("claude", "--dir", str(self.work), answers=["y"], tty=True)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(calls), 1)
        self.assertIn("[y/N]", calls[0])
        self.assertEqual(self.mode(), "shared")
        self.assertIsNone(session_choice.user_value(), "back to the default: the override is removed")
        self.assertEqual(session_choice.recorded(self.st), "shared")
        self.assertFalse([r for r in doctor.run(offline=True) if r["id"] == "session"])

    def test_terminal_enter_keeps_it_and_asks_again(self):
        self.user_config('{version: 1, agent: {session_mode: "independent"}}\n')
        self.agent("claude", "--dir", str(self.work), answers=[""], tty=True)
        self.assertEqual(self.mode(), "independent")
        self.assertIsNone(session_choice.recorded(self.st))
        _, _, calls = self.agent("claude", "--dir", str(self.work), answers=["n"], tty=True)
        self.assertEqual(len(calls), 1, "asked again")
        self.assertEqual(session_choice.recorded(self.st), "independent", "an explicit no is the owner's choice")
        _, text, _ = self.agent("claude", "--dir", str(self.work), tty=True)   # answers=None: must not ask
        self.assertEqual(self.mode(), "independent")

    def test_an_owner_choice_through_config_is_kept_silently(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(preferences.command(["set", "agent.session_mode", "independent"]), 0)
        self.assertEqual(session_choice.recorded(self.st), "independent")
        code, text, _ = self.agent("claude", "--dir", str(self.work), tty=True)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.mode(), "independent")
        self.assertNotIn("会话模式：", text)
        with contextlib.redirect_stdout(io.StringIO()):
            preferences.command(["unset", "agent.session_mode"])
        self.assertEqual(session_choice.recorded(self.st), "shared")

    def test_flags(self):
        self.user_config('{version: 1, agent: {session_mode: "independent"}}\n')
        code, text, _ = self.agent("claude", "--dir", str(self.work), "--shared")
        self.assertEqual((code, self.mode()), (0, "shared"), text)
        code, text, _ = self.agent("claude", "--dir", str(self.work), "--independent")
        self.assertEqual((code, self.mode()), (0, "independent"), text)
        self.assertEqual(session_choice.recorded(self.st), "independent")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["agent", "claude", "--shared", "--independent"])


class AgentLine(_Env):
    def test_shared_is_not_described_as_fenced(self):
        self.agent("claude", "--dir", str(self.work))
        zh, en = cli._agent_line(self.st, "zh"), cli._agent_line(self.st, "en")
        self.assertNotIn("隔离运行", zh)
        self.assertIn("共享会话", zh)
        self.assertIn("shared session", en)
        self.assertNotIn("fenced", en.replace("outside Agent J's fence", ""))
        code, text, _ = self.agent()          # `agentj agent` (status)
        self.assertNotIn("隔离运行", text)

    def test_independent_still_says_fenced_or_unfenced(self):
        self.agent("claude", "--dir", str(self.work), "--independent")
        self.assertIn("独立会话 · 隔离运行", cli._agent_line(self.st, "zh"))
        self.assertIn("independent session · fenced", cli._agent_line(self.st, "en"))
        self.agent("claude", "--dir", str(self.work), "--independent", "--unfenced")
        self.assertIn("不隔离运行", cli._agent_line(self.st, "zh"))
        self.assertIn("not fenced", cli._agent_line(self.st, "en"))

    def test_language_follows_the_preference(self):
        self.agent("codex", "--dir", str(self.work))
        with contextlib.redirect_stdout(io.StringIO()):
            preferences.command(["set", "appearance.language", "en"])
        self.assertTrue(cli._agent_line(self.st).startswith("Agent: Codex"))


def _old_state(home: pathlib.Path) -> State:
    st = State(state.legacy_state(str(home)))
    st.init(relay="ws://127.0.0.1:1")
    st.add_device(b"\x01" * 32, "Leo iPhone", sign_pub=b"\x0b" * 32)
    st.set_agent_name("Jarvis Test")
    return st


def _cli(home, *args, env=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTJ_", "AGENTJARVIS_"))}
    e.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "AGENTJ_SKILL_LINK": "off", **(env or {})})
    return subprocess.run([sys.executable, "-m", "agentj.cli", *args], cwd=HOST, env=e, capture_output=True, text=True,
                          timeout=60, stdin=subprocess.DEVNULL)


class LegacyState(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="aj-p127l-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.old, self.new = state.legacy_state(str(self.home)), state.default_state(str(self.home))
        self.legacy = _old_state(self.home)
        self.secrets = [self.legacy.ed25519_path.read_bytes().hex(), self.legacy.x25519_path.read_bytes().hex()]

    def test_no_terminal_means_nothing_moves_and_the_flags_are_named(self):
        for args in (["status"], ["init"], ["config", "get", "agent.session_mode"]):
            r = _cli(self.home, *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("--migrate-legacy", r.stderr)
            self.assertIn("--fresh", r.stderr)
            self.assertIn("Leo iPhone", r.stderr, "the owner can recognise it")
            self.assertIn("Jarvis Test", r.stderr)
            for s in self.secrets:
                self.assertNotIn(s, r.stderr + r.stdout)
            self.assertTrue(self.old.is_dir() and not self.old.is_symlink(), "the old directory is untouched")
            self.assertFalse(os.path.lexists(self.new), "nothing created either")

    def test_migrate_legacy_flag_moves(self):
        r = _cli(self.home, "--migrate-legacy", "devices", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.old.is_symlink())
        self.assertEqual(len(json.loads(r.stdout)), 1)

    def test_fresh_flag_starts_clean_and_never_asks_again(self):
        r = _cli(self.home, "--fresh", "init")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink(), "the old one stays where it is")
        new = State(self.new)
        self.assertTrue(new.exists())
        self.assertNotEqual(new.config()["channel"], self.legacy.config()["channel"], "a new identity")
        self.assertEqual(new.devices(), {})
        r = _cli(self.home, "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("--migrate-legacy", r.stderr)
        r = _cli(self.home, "init", env={"AGENTJ_LEGACY_STATE": "migrate"})
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink(), "decided once; the env answer no longer applies")

    def _main_decision(self, argv, interactive):
        seen = []
        with mock.patch.object(migrate, "auto", lambda decision=None, **k: seen.append(decision) or "none"), \
                mock.patch.object(migrate, "_interactive", return_value=interactive), \
                mock.patch.object(cli, "cmd_serve", lambda a: None), mock.patch.object(cli, "cmd_status", lambda a: None):
            cli.main(argv)
        return seen[0]

    def test_service_restart_of_a_real_old_install_keeps_its_identity(self):
        # auto-update / reboot restarts `serve` with no terminal: stopping with exit 2 would take the owner's phone offline
        self.assertEqual(self._main_decision(["serve"], interactive=False), "migrate")
        self.assertIsNone(self._main_decision(["serve"], interactive=True), "a person at a terminal is asked")
        self.assertIsNone(self._main_decision(["status"], interactive=False), "other commands still stop and ask")
        self.assertEqual(self._main_decision(["--fresh", "serve"], interactive=False), "fresh", "an explicit answer wins")

    def test_environment_answer(self):
        r = _cli(self.home, "status", env={"AGENTJ_LEGACY_STATE": "migrate"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.old.is_symlink())

    def _interactive(self, answer):
        err = io.StringIO()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTJ_STATE_DIR", "AGENTJARVIS_STATE_DIR", "AGENTJ_LEGACY"))}
        env["HOME"] = str(self.home)
        prompts = []
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("builtins.input", lambda p="": prompts.append(p) or answer):
            r = migrate.auto(out=err, interactive=True)
        return r, err.getvalue(), prompts

    def test_terminal_shows_what_it_holds_and_defaults_to_no(self):
        r, text, prompts = self._interactive("")
        self.assertEqual(r, "fresh")
        self.assertIn("[y/N]", prompts[0])
        self.assertIn("Leo iPhone", text)
        for s in self.secrets:
            self.assertNotIn(s, text)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())
        self.assertTrue(self.new.is_dir())
        self.assertEqual(self.new.stat().st_mode & 0o777, 0o700)

    def test_terminal_yes_moves(self):
        r, _, _ = self._interactive("y")
        self.assertEqual(r, "moved")
        self.assertTrue(self.old.is_symlink())

    def test_existing_new_state_is_a_normal_upgrade_and_never_asks(self):
        State(self.new).init(relay="ws://127.0.0.1:1")
        r = _cli(self.home, "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("--migrate-legacy", r.stderr)


def _fake_session(cc: pathlib.Path, work: pathlib.Path, sid: str, name: str, updated: int, first: str, pid=None):
    sock_dir = pathlib.Path(tempfile.mkdtemp(prefix="aj-sock-", dir="/tmp"))
    sock = sock_dir / "s.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock))
    (cc / "sessions").mkdir(parents=True, exist_ok=True)
    pid = pid or os.getpid()
    (cc / "sessions" / f"{sid[:6]}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "cwd": str(work), "kind": "interactive", "name": name, "updatedAt": updated,
        "startedAt": updated - 1000, "status": "idle", "messagingSocketPath": str(sock)}))
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(work))
    t = cc / "projects" / slug / (sid + ".jsonl")
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(json.dumps({"type": "user", "isMeta": True, "message": {"content": "<command>ignored</command>"}}) + "\n"
                 + json.dumps({"type": "user", "message": {"content": first}}) + "\n")
    return s, sock_dir


class SharedSessionPick(_Env):
    def setUp(self):
        super().setUp()
        self.cc = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"])
        now = int(time.time() * 1000)
        self.socks = []
        for sid, name, ago, first in (("aaaaaaaa-1111-2222-3333-444444444444", "billing fix", 60_000, "fix the invoice export"),
                                      ("bbbbbbbb-1111-2222-3333-444444444444", "", 3_600_000, "write the weekly report")):
            s, d = _fake_session(self.cc, self.work, sid, name, now - ago, first)
            self.socks.append(s)
            self.addCleanup(shutil.rmtree, d, True)
        for s in self.socks:
            self.addCleanup(s.close)

    def test_candidates_carry_name_activity_and_first_message(self):
        rows = session_choice.candidates(str(self.work))
        self.assertEqual([r["id"][:8] for r in rows], ["aaaaaaaa", "bbbbbbbb"], "most recently active first")
        self.assertEqual(rows[0]["name"], "billing fix")
        self.assertEqual(rows[0]["first"], "fix the invoice export", "the meta/command entry is skipped")

    def test_terminal_picks_one(self):
        code, text, calls = self.agent("claude", "--dir", str(self.work), answers=["2"], tty=True)
        self.assertEqual(code, 0, text)
        self.assertIn("billing fix", text)
        self.assertIn("write the weekly report", text)
        self.assertEqual(preferences.get(preferences.effective(self.st), "agent.shared_session_id"),
                         "bbbbbbbb-1111-2222-3333-444444444444")
        self.assertIn("bbbbbbbb", cli._agent_line(self.st, "zh"))

    def test_no_terminal_prints_the_list_and_the_command(self):
        code, text, _ = self.agent("claude", "--dir", str(self.work))
        self.assertEqual(code, 0, text)
        self.assertIn("aaaaaaaa-1111-2222-3333-444444444444", text)
        self.assertIn("agentj config set agent.shared_session_id", text)
        self.assertEqual(preferences.get(preferences.effective(self.st), "agent.shared_session_id"), "")

    def test_one_session_is_picked_by_attach_without_a_pin(self):
        self.socks[1].close()
        os.unlink(json.loads(next((self.cc / "sessions").glob("bbbbbb.json")).read_text())["messagingSocketPath"])
        code, text, _ = self.agent("claude", "--dir", str(self.work), tty=True)   # must not ask
        self.assertEqual(code, 0, text)
        self.assertIn("billing fix", text)
        self.assertEqual(preferences.get(preferences.effective(self.st), "agent.shared_session_id"), "")

    def test_independent_lists_nothing(self):
        code, text, _ = self.agent("claude", "--dir", str(self.work), "--independent")
        self.assertNotIn("billing fix", text)


class DoctorWait(unittest.TestCase):
    def test_says_why_it_waits(self):
        st = mock.Mock()
        seq = iter([{"relay_up": False, "relay": "connecting"}, {"relay_up": True, "relay": "up"}])
        err = io.StringIO()
        err.isatty = lambda: True
        with mock.patch.object(doctor.names, "ctl_call", side_effect=lambda *a, **k: next(seq)), \
                mock.patch.dict(os.environ, {"AGENTJ_TEST_DOCTOR_RELAY_WAIT": "5"}), mock.patch.object(doctor.time, "sleep"), \
                mock.patch.object(sys, "stderr", err):
            res = doctor._wait_relay(st, {"relay_up": False, "relay": "connecting"})
        self.assertTrue(res["relay_up"])
        self.assertIn("正在等它第一次连上中继", err.getvalue())
        self.assertIn("waiting for its first relay connection", err.getvalue())


if __name__ == "__main__":
    unittest.main()
