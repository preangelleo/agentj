"""#462: switching / rescuing a Claude Code login must reach the main session itself and be proven there.

Chairman 11-04 (Mac, Herdr): switched to 「备用」, every main-conversation message still answered with the old account's
organization error; after logging the keychain out it said "Not logged in"; the setup-token rescue said 「已恢复」 in the same
minute. Root cause: the switch / rescue only set serve's environment and the service env file, and the check ran in a side
process. Only the P258 stand-in harness (tests/fakep258.py, auth status in Claude Code 2.1.316's order), fake `security`, fake
tokens, isolated HOME; the owner's terminal process is a `sleep` child that must survive.
"""
import _hermetic  # noqa: F401
import asyncio
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import test_harness_accounts as tha
from agentj import accounts, claude_live

GOOD, OTHER = tha.GOOD, tha.OTHER


class _Owned(tha._Agent):
    cfg = {}


class _Shared:
    """An attached shared Claude session: the owner's own process (native None) or one Agent J opened (native + master)."""
    kind = "claude"

    def __init__(self, pid, native=False):
        self.cfg = {"session_mode": "shared"}
        self.session = {"pid": pid}
        self.native = types.SimpleNamespace(pid=pid, returncode=None) if native else None
        self.master = 7 if native else None

    def owns_harness(self):
        return False


class _Host(tha._Host):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.recovery = []

    def agent_recovery_notice(self, t):
        self.recovery.append(t)


class Base(tha.Base):
    def setUp(self):
        super().setUp()
        self.host = _Host(self.st, self.tmp / "work")
        self.kids = []
        self.addCleanup(self._reap)

    def _reap(self):
        for p in self.kids:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()

    def terminal(self, **env):
        """The owner's terminal Claude Code: a process whose environment is whatever its shell had."""
        base = {k: v for k, v in os.environ.items() if k not in claude_live.AUTH}
        p = subprocess.Popen([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"], env={**base, **env},
                             stdout=subprocess.PIPE, text=True)
        self.kids.append(p)
        p.stdout.readline()                  # exec'd: /proc/<pid>/environ is now the child's own
        return p

    def statuses(self):
        return [ln for ln in self.logged().splitlines() if ln.startswith("auth_status")]

    def logged(self):
        return self.log.read_text() if self.log.exists() else ""

    def assert_no_token_anywhere(self, res=None):
        blob = "\n".join([*self.host.notices, *self.host.recovery, str(res), self.everything_text()])
        for t in (GOOD, OTHER):
            self.assertNotIn(t, blob)


class Switch(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.set_service_token(GOOD)
        self.backup = accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))

    async def test_owned_session_restarts_and_is_checked_in_its_own_env(self):
        self.host.agent = _Owned("claude")
        res = await accounts.phone(self.host, tha._Sess(), "acct_use", {"harness": "claude", "id": self.backup["id"]})
        self.assertEqual((res["restart"], res["verified"]), ("next_turn", True), res)
        self.assertEqual(self.host.agent.restarts, ["auth_rescue"])          # the next spawn resumes with the new env
        self.assertEqual(self.statuses(), ["auth_status oauth_token"])        # `claude auth status` ran, with OTHER (not GOOD)
        self.assertIn("主对话自己的环境里核对", self.host.notices[-1])
        self.assert_no_token_anywhere(res)

    async def test_owner_terminal_session_is_not_silently_ok(self):
        term = self.terminal(CLAUDE_CODE_OAUTH_TOKEN=GOOD)                   # started before the switch: the old token
        self.host.agent = _Shared(term.pid)
        res = await accounts.use(self.host, "claude", self.backup["id"], device="dev1")
        self.assertEqual((res["restart"], res["verified"], res["live"]), ("owner_session", False, "stale"), res)
        self.assertEqual(len(self.host.recovery), 1)                         # the one-tap signed 「重启 Agent」 card
        self.assertIn("重启 Agent", self.host.recovery[0])
        self.assertIsNone(term.poll())                                       # never signalled by the switch itself
        self.assertTrue(self.host.__dict__.get("_claude_live_pending"))
        # the owner taps it: P435 reopens the same conversation in Agent J's own PTY from serve's (new) environment
        reopened = self.terminal(CLAUDE_CODE_OAUTH_TOKEN=OTHER)
        self.host.agent = _Shared(reopened.pid, native=True)
        line = await claude_live.after_reopen(self.host)
        self.assertIn("主对话自己的环境里核对", line)
        self.assertIsNone(await claude_live.after_reopen(self.host))          # once per hand-over
        self.assert_no_token_anywhere(res)

    async def test_owner_terminal_without_any_login(self):
        term = self.terminal()
        self.host.agent = _Shared(term.pid)
        res = await accounts.use(self.host, "claude", self.backup["id"])
        self.assertEqual((res["verified"], res["live"]), (False, "stale"))

    async def test_reopen_failure_is_explicit(self):
        bad = self.terminal(CLAUDE_CODE_OAUTH_TOKEN=GOOD)                    # the reopened process still holds the old one
        self.host.agent = _Shared(bad.pid)
        await accounts.use(self.host, "claude", self.backup["id"])
        self.host.agent = _Shared(bad.pid, native=True)
        line = await claude_live.after_reopen(self.host)
        self.assertIn("没有用上新登录", line)

    async def test_native_session_reopens_with_resume_through_p435(self):
        term = self.terminal(CLAUDE_CODE_OAUTH_TOKEN=GOOD)
        self.host.agent = _Shared(term.pid, native=True)
        calls = []

        async def run(host):
            calls.append(host)
            host.agent = _Shared(self.terminal(CLAUDE_CODE_OAUTH_TOKEN=os.environ["CLAUDE_CODE_OAUTH_TOKEN"]).pid, native=True)
            return {"ok": True}
        with mock.patch("agentj.phone_restart.run", run):
            res = await accounts.use(self.host, "claude", self.backup["id"])
            self.assertEqual(res["restart"], "reopening")
            await self.drain()
        self.assertEqual(len(calls), 1)
        self.assertIn("主对话自己的环境里核对", self.host.notices[-1])

    async def drain(self):
        while self.host.tasks:
            await self.host.tasks.pop(0)

    def test_cli_prints_the_failure(self):
        from agentj import accounts as a
        out = []
        with mock.patch.object(a, "_call", return_value={"ok": True, "name": "备用", "restart": "owner_session", "verified": False,
                                                       "live": "stale"}), \
                mock.patch("builtins.print", lambda *x, **k: out.append(" ".join(map(str, x)))):
            a.cmd_account(types.SimpleNamespace(acct_cmd="use", harness="claude", ref="备用"))
        self.assertIn("重启 Agent", out[-1])


class MacKeychain(Base, unittest.IsolatedAsyncioTestCase):
    """macOS: the /login lives in the keychain (fake `security`); the old account's login is there and must not win."""

    def setUp(self):
        super().setUp()
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        self.chain = self.tmp / "keychain.json"
        sec = bin_ / "security"
        sec.write_text(f"#!/bin/sh\n[ -s {self.chain} ] || exit 44\ncat {self.chain}\n")
        sec.chmod(0o700)
        self.chain.write_text('{"email": "old@example.com"}')
        env = mock.patch.dict(os.environ, {"FAKE258_PLATFORM": "darwin", "PATH": f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"})
        env.start()
        self.addCleanup(env.stop)
        self.envs = {}
        p = mock.patch.object(claude_live, "sys", types.SimpleNamespace(platform="darwin"))
        p.start()
        self.addCleanup(p.stop)
        lib = mock.patch("agentj.term_mac.LIB.args", side_effect=lambda pid: (["claude"], self.envs[pid]) if pid in self.envs else None)
        lib.start()
        self.addCleanup(lib.stop)
        self.backup = accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))   # 主号 = login (keychain)

    def mac_terminal(self, **env):
        pid = 4242 + len(self.envs)
        cfg = os.environ["CLAUDE_CONFIG_DIR"]                                # the same config serve uses (L-462a compares it)
        self.envs[pid] = [f"HOME={self.tmp}", f"CLAUDE_CONFIG_DIR={cfg}", *(f"{k}={v}" for k, v in env.items())]
        return pid

    async def test_token_env_beats_the_keychain_old_login(self):
        self.host.agent = _Owned("claude")
        res = await accounts.use(self.host, "claude", self.backup["id"])
        self.assertTrue(res["verified"], res)
        self.assertEqual(self.statuses(), ["auth_status oauth_token"])

    async def test_herdr_session_on_mac_keeps_the_keychain_login_until_reopened(self):
        self.host.agent = _Shared(self.mac_terminal())
        res = await accounts.use(self.host, "claude", self.backup["id"])
        self.assertEqual((res["verified"], res["live"]), (False, "stale"))
        self.host.agent = _Shared(self.mac_terminal(CLAUDE_CODE_OAUTH_TOKEN=OTHER), native=True)
        self.assertIn("核对", await claude_live.after_reopen(self.host))

    async def test_back_to_the_keychain_login_and_logged_out(self):
        self.host.agent = _Owned("claude")
        await accounts.use(self.host, "claude", self.backup["id"])
        main = accounts.load(self.root)["harness"]["claude"]["items"][0]
        self.host.agent = _Shared(self.mac_terminal())
        res = await accounts.use(self.host, "claude", main["id"])           # login: the keychain is the account
        self.assertEqual((res["restart"], res["verified"]), ("none", True), res)
        self.assertEqual(self.statuses()[-1], "auth_status claude.ai")
        self.chain.write_text("")                                            # `claude auth logout` → "Not logged in"
        why = await claude_live.verify(self.host)
        self.assertEqual(why, "not_logged_in")


class RescueInMainSession(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.r = self.host.auth_rescue
        for target, kw in (("agentj.harness_setup.enabled", {"return_value": False}),
                           ("agentj.harness.probe", {"return_value": {"harnesses": [{"name": "claude", "callable": True}]}})):
            p = mock.patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)
        os.environ["FAKE258_SCREENS"] = "ready"

    async def drain(self):
        while self.host.tasks:
            await self.host.tasks.pop(0)

    async def test_setup_token_rescue_with_owner_session_is_not_recovered(self):
        term = self.terminal()                                               # the Herdr pane: never got the new token
        self.host.agent = _Shared(term.pid)
        await self.r.start("claude", "setup_token")
        await self.drain()
        self.assertEqual((self.r.last["claude"]["result"], self.r.last["claude"]["why"]), ("partial", "main_session"), self.r.last)
        self.assertFalse(any("已重新登录并恢复" in t for t in self.host.notices + self.host.recovery))
        self.assertIn("重启 Agent", self.host.recovery[-1])
        self.assertIsNone(term.poll())
        reopened = self.terminal(CLAUDE_CODE_OAUTH_TOKEN=GOOD)               # after the owner's signed reopen
        self.host.agent = _Shared(reopened.pid, native=True)
        await claude_live.after_reopen(self.host)
        self.assertEqual(self.r.last["claude"]["result"], "recovered")
        self.assert_no_token_anywhere()

    async def test_setup_token_rescue_owned_is_recovered_and_proven(self):
        self.host.agent = _Owned("claude")
        await self.r.start("claude", "setup_token")
        await self.drain()
        self.assertEqual(self.r.last["claude"]["result"], "recovered", self.r.last)
        self.assertIn("主对话自己的环境里核对", self.host.notices[-1])
        self.assertIn("auth_status oauth_token good", self.statuses())


class Context(Base, unittest.IsolatedAsyncioTestCase):
    """L-462a (security round 3): HOME / CLAUDE_CONFIG_DIR imported from the target process are never used for the check:
    another config dir (or one inside the workspace) is refused, and `claude auth status` runs in an empty scratch dir."""

    def setUp(self):
        super().setUp()
        self.set_service_token(GOOD)
        self.backup = accounts.add_claude_token(self.root, "备用", bytearray(OTHER.encode()))

    async def test_target_with_another_config_dir_is_refused_not_proven(self):
        serve = mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": OTHER})    # serve already switched to 备用
        serve.start()
        self.addCleanup(serve.stop)
        for env in ({"CLAUDE_CONFIG_DIR": str(self.tmp / "work" / ".cc")}, {"HOME": str(self.tmp / "work")},
                    {"CLAUDE_CONFIG_DIR": str(self.tmp / "elsewhere")}):
            self.host.agent = _Shared(self.terminal(CLAUDE_CODE_OAUTH_TOKEN=OTHER, **env).pid)
            self.assertEqual(await claude_live.verify(self.host), "context", env)
        self.assertEqual(self.statuses(), [], "auth status never ran in a foreign config")
        self.host.agent = _Shared(self.terminal(CLAUDE_CODE_OAUTH_TOKEN=OTHER).pid)
        self.assertIsNone(await claude_live.verify(self.host), "serve's own config still proves the new login")
        self.assertIn("配置目录", claude_live.text("zh", "bad", "context"))

    def test_config_dir_inside_the_workspace_is_refused(self):
        work = self.tmp / "work"
        exp = claude_live.expected({"CLAUDE_CODE_OAUTH_TOKEN": OTHER})
        env = {"CLAUDE_CODE_OAUTH_TOKEN": OTHER, "CLAUDE_CONFIG_DIR": str(work / ".cc")}
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(work / ".cc")}):
            self.assertEqual(claude_live.check(env, exp, str(work)), "context")
            self.assertFalse(claude_live.context_ok(env, str(work / "sub" / "..")))
        home = {"CLAUDE_CODE_OAUTH_TOKEN": OTHER}
        with mock.patch.dict(os.environ, {"HOME": str(work)}):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertTrue(claude_live.context_ok(home, str(work)), "a workspace that is the whole home holds ~/.claude")

    def test_auth_status_runs_in_an_empty_scratch_dir_with_serves_config(self):
        work = self.tmp / "work"
        work.mkdir(exist_ok=True)
        seen = {}

        def run(argv, cwd=None, env=None, **kw):
            seen.update(cwd=cwd, files=os.listdir(cwd), cfg=env.get("CLAUDE_CONFIG_DIR"), home=env.get("HOME"))
            return subprocess.CompletedProcess(argv, 0, '{"loggedIn": true, "authMethod": "oauth_token"}', "")
        with mock.patch.object(claude_live.subprocess, "run", side_effect=run):
            exp = claude_live.expected({"CLAUDE_CODE_OAUTH_TOKEN": OTHER})
            self.assertIsNone(claude_live.check({"CLAUDE_CODE_OAUTH_TOKEN": OTHER, "HOME": os.environ["HOME"],
                                                 "CLAUDE_CONFIG_DIR": os.environ["CLAUDE_CONFIG_DIR"]}, exp, str(work)))
        self.assertNotEqual(os.path.realpath(seen["cwd"]), os.path.realpath(work))
        self.assertEqual(seen["files"], [])
        self.assertFalse(os.path.exists(seen["cwd"]), "scratch dir removed")
        self.assertEqual((seen["cfg"], seen["home"]), (os.environ["CLAUDE_CONFIG_DIR"], os.environ["HOME"]))


class Precedence(unittest.TestCase):
    def test_outranking_names_and_hash_compare(self):
        exp = claude_live.expected({"CLAUDE_CODE_OAUTH_TOKEN": OTHER})
        self.assertEqual(claude_live.check({"CLAUDE_CODE_OAUTH_TOKEN": OTHER, "ANTHROPIC_AUTH_TOKEN": "x"}, exp), "outranked")
        self.assertEqual(claude_live.check({"CLAUDE_CODE_OAUTH_TOKEN": GOOD}, exp), "stale")
        self.assertEqual(claude_live.check(None, exp), "unreadable")
        self.assertEqual(claude_live.check({"CLAUDE_CODE_OAUTH_TOKEN": GOOD}, claude_live.expected({})), "stale")
        self.assertNotIn(OTHER, repr(exp))

    @unittest.skipUnless(os.environ.get("AGENTJ_TEST_REAL_CLAUDE"), "set AGENTJ_TEST_REAL_CLAUDE=<claude binary> to check the real one")
    def test_real_claude_token_beats_stored_login(self):
        exe = os.environ["AGENTJ_TEST_REAL_CLAUDE"]
        home = pathlib.Path(tempfile.mkdtemp(prefix="aj-462-", dir=os.environ.get("TEST_TMP") or None))
        self.addCleanup(shutil.rmtree, home, True)
        (home / ".claude").mkdir()
        (home / ".claude" / ".credentials.json").write_text(
            '{"claudeAiOauth":{"accessToken":"sk-ant-oat01-' + "O" * 44 + '","refreshToken":"x","expiresAt":4102444800000,'
            '"scopes":["user:inference","user:profile"]}}')
        with mock.patch("agentj.auth_rescue.binary", return_value=exe), \
                mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
            self.assertEqual(claude_live.auth_status({"HOME": str(home)})["authMethod"], "claude.ai")
            got = claude_live.auth_status({"HOME": str(home), "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-" + "N" * 44})
            self.assertEqual(got["authMethod"], "oauth_token")


if __name__ == "__main__":
    unittest.main()
