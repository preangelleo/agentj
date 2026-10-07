"""P73 F30 (ADR-A175): the main Agent's Codex runs directly on the system unless the owner explicitly restricted it.

- unset `sandbox_mode` → thread/start|resume `sandbox: danger-full-access`, ADR-A73 no longer refuses `curl`;
- explicit `sandbox_mode = "workspace-write"` → ADR-A73 still refuses network commands (with the config.toml way out);
- research tasks stay read-only; friends' peer sessions stay read-only / tool-less whatever config.toml says;
- thread/resume carries the current sandbox: change config → restart → the same thread follows at once;
- a `sandbox_mode` appended inside the last [table] → doctor and /status say "written but not in effect"; writes go top level;
- shared mode: desktop thread permissions / config.toml / Agent J default, by what the thread actually recorded.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import agent_codex as cx, codex_perm, doctor  # noqa: E402
from agentj.shared_codex import SharedCodexAgent  # noqa: E402

from test_slash import _Chain  # noqa: E402   (the stand-in Codex chain inside the fence; no tests of its own)
from test_p51_codex_shared import Base as SharedBase, SID  # noqa: E402

TABLE_APPENDED = 'model = "gpt-x"\n\n[tui]\nnotifications = true\nsandbox_mode = "danger-full-access"\n'


class _CodexHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name) / "codex"
        self.home.mkdir()
        p = patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)

    def write(self, text):
        (self.home / "config.toml").write_text(text)


class ConfigToml(_CodexHome):
    def test_parse_top_level_and_misplaced(self):
        self.assertEqual(codex_perm.parse(""), {"top": None, "misplaced": [], "error": None})
        self.assertEqual(codex_perm.parse('sandbox_mode = "workspace-write"\n[tui]\nx = 1\n')["top"], "workspace-write")
        r = codex_perm.parse(TABLE_APPENDED)
        self.assertEqual((r["top"], r["misplaced"]), (None, ["tui"]))
        r = codex_perm.parse('[projects."/w"]\ntrust_level = "trusted"\nsandbox_mode = "danger-full-access"\n')
        self.assertEqual(r["misplaced"], ["projects./w"])
        self.assertEqual(codex_perm.parse('[profiles.p]\nsandbox_mode = "read-only"\n')["misplaced"], [], "profiles are their own feature")
        self.assertEqual(codex_perm.parse("sandbox_mode = \n")["error"], "invalid")

    def test_config_info_respects_codex_home(self):
        self.assertFalse(codex_perm.config_info()["exists"])
        self.write(TABLE_APPENDED)
        info = codex_perm.config_info()
        self.assertEqual((info["path"], info["top"], info["misplaced"]), (str(self.home / "config.toml"), None, ["tui"]))

    def test_writes_always_land_at_the_top_level_with_comments_kept(self):
        self.write("# mine\n" + TABLE_APPENDED)
        r = codex_perm.fix()
        text = (self.home / "config.toml").read_text()
        self.assertEqual((r["top"], r["misplaced"]), ("danger-full-access", []))
        self.assertLess(text.index("sandbox_mode"), text.index("[tui]"), text)
        self.assertIn("# mine", text)
        self.assertIn("notifications = true", text)
        self.assertTrue(r["backup"] and pathlib.Path(r["backup"]).read_text().endswith(TABLE_APPENDED))
        self.assertEqual(oct(pathlib.Path(r["backup"]).stat().st_mode & 0o777), "0o600")
        r = codex_perm.set_mode("workspace-write")
        self.assertEqual(codex_perm.parse((self.home / "config.toml").read_text())["top"], "workspace-write")
        r = codex_perm.set_mode(None)
        self.assertIsNone(codex_perm.parse((self.home / "config.toml").read_text())["top"])
        self.assertEqual(len(list(self.home.glob("config.toml.agentj-*.bak"))), 3, "every original kept")
        with self.assertRaises(ValueError):
            codex_perm.edit("", "everything")

    def test_set_mode_on_a_file_ending_in_a_table(self):
        self.write("[mcp_servers.x]\ncommand = \"y\"\n")
        codex_perm.set_mode("read-only")
        text = (self.home / "config.toml").read_text()
        self.assertTrue(text.startswith('sandbox_mode = "read-only"'), text)
        self.assertEqual(codex_perm.parse(text)["misplaced"], [])

    def test_provider_profile_merge_writes_top_level_keys(self):
        """The other Agent J path that edits config.toml (provider profiles) also writes top-level keys above every table."""
        from agentj import provider_profiles
        old = b'[tui]\nnotifications = true\n'
        new = provider_profiles.merge({"harness": "codex", "id": "relay", "model": "gpt-x", "base_url": "https://x.invalid/v1"},
                                      "k", old).decode()
        import tomllib
        d = tomllib.loads(new)
        self.assertEqual((d["model_provider"], d["model"]), ("relay", "gpt-x"))
        self.assertNotIn("model", d["tui"])

    @unittest.skipUnless(shutil.which("codex"), "real Codex not installed")
    def test_real_codex_reads_what_fix_wrote(self):
        """Codex itself (config/read) ignores the table key and honours the fixed top-level one."""
        def effective():
            p = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 env=dict(os.environ, CODEX_HOME=str(self.home)), cwd=self.tmp.name)
            try:
                p.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"t","title":"t","version":"1"},'
                              b'"capabilities":{}}}\n{"jsonrpc":"2.0","method":"initialized"}\n'
                              + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "config/read", "params": {"cwd": self.tmp.name}}).encode() + b"\n")
                p.stdin.flush()
                while True:
                    m = json.loads(p.stdout.readline())
                    if m.get("id") == 2:
                        return m["result"]["config"].get("sandbox_mode")
            finally:
                p.kill()
                p.wait()
        self.write(TABLE_APPENDED)
        self.assertIsNone(effective(), "a sandbox_mode inside [tui] is not in effect")
        codex_perm.fix()
        self.assertEqual(effective(), "danger-full-access")


class Policy(unittest.TestCase):
    def agent(self, human=None, research=False, high=False):
        a = cx.CodexAgent(Mock(), {"kind": "codex", "dir": "/w", "_workflow_ceo": True, "high_risk_warnings": high},
                          persist=not research, research=research)
        a.human = human or {}
        return a

    def test_unset_runs_directly_on_the_system(self):
        a = self.agent()
        p = a.policy()
        self.assertEqual(p, {"sandbox": "danger-full-access"}, "nothing else imposed (F14): approvals stay Codex's own")
        self.assertEqual(a.perm_source, "agentj")

    def test_explicit_setting_is_kept(self):
        for mode in ("workspace-write", "read-only", "danger-full-access"):
            a = self.agent({"sandbox_mode": mode, "approval_policy": "never"})
            p = a.policy()
            self.assertEqual((p["sandbox"], p["approvalPolicy"], a.perm_source), (mode, "never", "config"))

    def test_research_stays_read_only(self):
        a = self.agent({"sandbox_mode": "danger-full-access"}, research=True)
        self.assertEqual((a.policy()["sandbox"], a.perm_source), ("read-only", "research"))

    def test_high_risk_warnings_still_ask_for_untrusted(self):
        a = self.agent(high=True)
        p = a.policy()
        self.assertEqual((p["approvalPolicy"], p["approvalsReviewer"], p["sandbox"]), ("untrusted", "user", "danger-full-access"))

    def test_beyond_sandbox_by_source(self):
        req = lambda sb: cx.beyond_sandbox(sb, "item/commandExecution/requestApproval", "Bash",  # noqa: E731
                                           {"command": "curl -s https://awscli.amazonaws.com"}, {}, None, "/w")
        self.assertIsNone(req({"type": "dangerFullAccess"}), "Agent J default: curl gets the usual card, not a refusal")
        self.assertEqual(req({"type": "workspaceWrite", "networkAccess": False}), "Codex 的沙箱不允许联网")

    def test_notice_names_the_way_out(self):
        a = self.agent({"sandbox_mode": "workspace-write"})
        a.policy()
        a.thread = {"sandbox": {"type": "workspaceWrite"}}
        n = a.policy_notice()
        self.assertTrue(n.startswith(cx.POLICY_NOTICE))
        self.assertIn("agentj codex-sandbox default", n)
        a.perm_source = "desktop"
        self.assertIn("完全访问", a.policy_notice())
        self.assertNotIn("改 Codex 的设置", a.policy_notice())


class PeerSessionUnaffected(_CodexHome):
    def test_friend_codex_stays_read_only_and_tool_less(self):
        from agentj import peer_session
        self.write('sandbox_mode = "danger-full-access"\napproval_policy = "never"\n')
        ps = peer_session.PeerSessions.__new__(peer_session.PeerSessions)
        fn = peer_session.PeerSessions._codex_argv
        with patch.object(peer_session, "codex_features", return_value=set(peer_session.CODEX_REQUIRED)):
            argv = fn(ps, "codex", "/tmp/p.md", None, {}, dict(os.environ))
        self.assertIn('sandbox_mode="read-only"', argv)
        self.assertIn('approval_policy="never"', argv)
        self.assertIn("--ignore-user-config", argv, "the owner's danger-full-access never reaches a friend's session")
        self.assertNotIn("danger-full-access", " ".join(argv))


class DoctorAndStatus(_CodexHome):
    def st(self, mode="independent"):
        st = Mock()
        st.exists.return_value = True
        st.agent_config.return_value = {"kind": "codex", "dir": self.tmp.name, "session_mode": mode}
        return st

    def test_rows(self):
        r = doctor.check_codex_perm(self.st())
        self.assertEqual(r["status"], "ok")
        self.assertIn("底层直跑", r["summary"])
        self.write('sandbox_mode = "workspace-write"\n')
        r = doctor.check_codex_perm(self.st())
        self.assertIn("你的沙箱设置：workspace-write（config.toml）", r["summary"])
        self.write(TABLE_APPENDED)
        r = doctor.check_codex_perm(self.st())
        self.assertEqual(r["status"], "warn")
        self.assertIn("写了但没生效", r["summary"])
        self.assertIn("[tui]", r["summary"])
        self.assertIn("第一个 [ ] 之前", r["summary"])
        self.assertIn("agentj codex-sandbox fix", r["hint"])
        r = doctor.check_codex_perm(self.st("shared"))
        self.assertIn("共享模式", r["hint"])
        st = self.st()
        st.agent_config.return_value = {"kind": "claude", "dir": self.tmp.name}
        self.assertIsNone(doctor.check_codex_perm(st))

    def test_status_lines(self):
        a = cx.CodexAgent(Mock(), {"kind": "codex", "dir": self.tmp.name, "_workflow_ceo": True})
        a.proc = Mock(returncode=None)
        a.human = {}
        a.policy()
        a.thread = {"sandbox": {"type": "dangerFullAccess"}}
        text = asyncio.run(a.cmd_status("")).text
        self.assertIn("权限：底层直跑（Agent J 默认：danger-full-access", text)
        self.write(TABLE_APPENDED)
        a.human = {"sandbox_mode": None}
        text = asyncio.run(a.cmd_status("")).text
        self.assertIn("写了但没生效", text)
        a.human = {"sandbox_mode": "workspace-write"}
        a.policy()
        a.thread = {"sandbox": {"type": "workspaceWrite"}}
        self.assertIn("权限：你的沙箱设置：workspace-write（config.toml）", asyncio.run(a.cmd_status("")).text)
        self.assertIn("桌面线程权限：read-only", codex_perm.label("desktop", "read-only"))


# ------------------------------------------------------------------ the whole chain: stand-in Codex inside the fence
class Chain(_Chain):
    KIND = "codex"

    def setUp(self):
        super().setUp()
        p = patch.dict(os.environ, {"CODEX_HOME": str(pathlib.Path(self.tmp.name) / "codex-home")})
        p.start()
        self.addCleanup(p.stop)

    def curl(self, refused):
        async def script(c):
            notices = lambda: [m["text"] for m in c["msgs"](("notice",))]  # noqa: E731
            await c["say"]("RUN: curl -s https://awscli.amazonaws.com")
            if refused:
                await c["wait"](lambda: any(m["text"].startswith("declined: curl") for m in c["msgs"]()))
                await c["idle"]()
                self.assertEqual(c["asks"](), [], "explicitly restricted: refused without a card (ADR-A73)")
                self.assertTrue(any("agentj codex-sandbox default" in n for n in notices()), notices())
            else:
                await c["wait"](lambda: len(c["asks"]()) == 1)
                a = c["asks"]()[-1]
                self.assertEqual(a["summary"], "curl -s https://awscli.amazonaws.com")
                await c["host"]._app(c["s"], c["ph"].answer(a, False))     # the phone decides; no real network in tests
                await c["wait"](lambda: any(m["text"].startswith("declined: curl") for m in c["msgs"]()))
                self.assertFalse(any("沙箱" in n for n in notices()))
        self.run_chain(script)

    def test_unset_curl_gets_a_card_not_a_refusal(self):
        self.curl(refused=False)
        start = [x["params"] for x in self.logged() if x.get("method") == "thread/start"][0]
        self.assertEqual(start["sandbox"], "danger-full-access")

    def test_explicit_workspace_write_still_refuses_network(self):
        os.environ["FAKE_CX_SANDBOX"] = "workspace-write"
        self.curl(refused=True)

    def test_change_setting_restart_same_thread_follows_at_once(self):
        """10-07: config.toml changed + `agentj service restart`, the resumed old thread kept refusing. Resume now names the
        current sandbox, so the same thread follows the new setting at once."""
        os.environ["FAKE_CX_THREADS"] = json.dumps(["old-thread"])
        os.environ["FAKE_CX_THREAD_SANDBOX"] = "workspace-write"     # what the old thread itself kept
        self.st.set_agent_session("codex", "old-thread")
        os.environ["FAKE_CX_SANDBOX"] = "workspace-write"             # before: explicit workspace-write
        self.curl(refused=True)
        os.environ.pop("FAKE_CX_SANDBOX")                             # the owner removed it (agentj codex-sandbox default)
        self.curl(refused=False)                                      # a new serve = the restart
        resumes = [x["params"] for x in self.logged() if x.get("method") == "thread/resume"]
        self.assertEqual([r["threadId"] for r in resumes], ["old-thread", "old-thread"], "the same conversation both times")
        self.assertEqual([r.get("sandbox") for r in resumes], ["workspace-write", "danger-full-access"])
        self.assertTrue(all("approvalPolicy" in r for r in resumes), "approvals are named on resume too")
        self.assertEqual(self.st.agent_session("codex"), "old-thread")

    def test_pidfile_follows_the_app_server(self):
        from agentj import codex_procs
        seen = []

        async def script(c):
            await c["say"]("你好")
            await c["wait"](lambda: any(m["text"] == "ECHO: 你好" for m in c["msgs"]()))
            seen.extend(codex_procs.entries(self.st))
        self.run_chain(script)
        self.assertEqual(len(seen), 1, "the running app-server is recorded with its start time")
        self.assertIsNotNone(seen[0]["start"])
        self.assertEqual(codex_procs.entries(self.st), [], "ended with serve: forgotten")


# ------------------------------------------------------------------ shared mode: three sources
DEFAULT_CTX = {"approval_policy": "on-request", "approvals_reviewer": "user",
               "sandbox_policy": {"type": "workspace-write", "writable_roots": [], "network_access": False,
                                  "exclude_tmpdir_env_var": False, "exclude_slash_tmp": False},
               "permission_profile": {"type": "managed", "file_system": {"type": "restricted", "entries": []}, "network": "restricted"},
               "active_permission_profile": {"id": ":workspace"}}


class SharedSources(SharedBase):
    def ctx(self, **kw):
        return {"cwd": str(self.proj), **DEFAULT_CTX, **kw}

    async def test_codex_default_record_follows_agentj_default(self):
        self.rollout(SID, ctx=self.ctx())
        a = self.agent(SID)
        a.human = {}
        await a._thread()
        m, p = self.calls[-1]
        self.assertEqual((m, p["sandbox"], p["approvalPolicy"]), ("thread/resume", "danger-full-access", "on-request"))
        self.assertNotIn("permissions", p)
        self.assertEqual(a.perm_source, "agentj")
        self.assertEqual(a.original["sandboxPolicy"], {"type": "dangerFullAccess"}, "every phone turn too")
        self.assertNotIn("默认权限", self.notices(), "not a degraded continuation")

    async def test_codex_default_record_follows_explicit_config(self):
        self.rollout(SID, ctx=self.ctx())
        a = self.agent(SID)
        a.human = {"sandbox_mode": "workspace-write"}
        await a._thread()
        self.assertEqual(self.calls[-1][1]["sandbox"], "workspace-write")
        self.assertEqual(a.perm_source, "config")

    async def test_explicit_desktop_choices_stay(self):
        for ctx, want in ((self.ctx(sandbox_policy={"type": "danger-full-access"}, permission_profile={"type": "disabled"},
                                    active_permission_profile=None, approval_policy="never"), "danger-full-access"),
                          (self.ctx(sandbox_policy={"type": "read-only"},
                                    permission_profile={"type": "managed", "file_system": {"type": "restricted", "entries": [
                                        {"path": {"type": "special", "value": {"kind": "root"}}, "access": "read"}]}, "network": "restricted"},
                                    active_permission_profile={"id": ":read-only"}), None)):
            self.calls.clear()
            self.rollout(SID, ctx=ctx)
            a = self.agent(SID)
            a.human = {}
            await a._thread()
            p = self.calls[-1][1]
            self.assertEqual(p.get("sandbox"), want)
            self.assertEqual(a.perm_source, "desktop")
        a.thread = {"sandbox": {"type": "readOnly"}}
        n = a.policy_notice()
        self.assertIn("在 Codex 桌面 App 里把这条对话的权限改成「完全访问」", n)

    def test_default_record_shape_is_strict(self):
        self.assertTrue(codex_perm.default_desktop_record(DEFAULT_CTX))
        self.assertTrue(codex_perm.default_desktop_record({**DEFAULT_CTX, "permission_profile": None, "active_permission_profile": None}))
        for change in ({"approval_policy": "untrusted"}, {"approval_policy": "never"},
                       {"sandbox_policy": {**DEFAULT_CTX["sandbox_policy"], "network_access": True}},
                       {"sandbox_policy": {**DEFAULT_CTX["sandbox_policy"], "writable_roots": ["/x"]}},
                       {"sandbox_policy": {"type": "read-only"}},
                       {"active_permission_profile": {"id": "my-profile"}},
                       {"active_permission_profile": {"id": None}}):
            self.assertFalse(codex_perm.default_desktop_record({**DEFAULT_CTX, **change}), change)

    async def test_status_shows_desktop_source(self):
        self.rollout(SID, ctx=self.ctx(sandbox_policy={"type": "read-only"},
                                       permission_profile={"type": "managed", "file_system": {"type": "restricted", "entries": [
                                           {"path": {"type": "special", "value": {"kind": "root"}}, "access": "read"}]}, "network": "restricted"},
                                       active_permission_profile={"id": ":read-only"}))
        a = self.agent(SID)
        a.human = {}
        self.assertEqual(a.perm_source, "pending")
        await a._thread()
        a.proc = Mock(returncode=None)
        a.thread = {"sandbox": {"type": "readOnly"}}
        a.version = "0"
        a.cfg["dir"] = str(self.proj)
        text = (await a.cmd_status("")).text
        self.assertIn("权限：桌面线程权限：read-only", text)


if __name__ == "__main__":
    unittest.main()
