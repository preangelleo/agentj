"""P418 (#183) 重启后依赖带起 — the shared session comes back after the computer restarts, with nobody at the keyboard.

- RebootResume (①): a pinned Claude Code session whose transcript was last written before this boot is reopened in the
  background (`claude --resume <id>`) and attached; Claude Code answering with a new id moves the pin ("resumed"); a deleted
  transcript of the session Agent J served last → a new session + pin ("new_after_reboot"). Never started: a session the
  owner closed during this boot, an unknown boot time, a second time for the same pin.
- BootTime: Linux /proc/stat btime; macOS `sysctl -n kern.boottime` parsing.
- PrepareBeforeStart (④): our own Claude Code start answers the folder-trust question first (mocked — never the
  developer's ~/.claude.json).
- OwnerDown (②): the owner's OpenCode server not listening → a plain sentence + one card (start it / I'll do it); start
  → `opencode serve --hostname 127.0.0.1 --port N` (mocked), wait for it to listen, attach once; a 401 / wrong session keeps
  the old texts; a host without cards gets the text.
Local fixtures only: sleeping child processes as live pids, local sockets, a loopback test server. No real claude /
opencode / systemctl."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import shared, harness_setup, resource_policy  # noqa: E402

PIN = "aaaaaaaa-1111-1111-1111-111111111111"
NEWID = "bbbbbbbb-2222-2222-2222-222222222222"
OLD_TEXT = "共享 Claude 会话不存在或有多个匹配"


class Host:
    def __init__(self, last=None):
        self.st = Mock()
        self.st.agent_session.side_effect = lambda k: last if k == "claude" else None
        self.notices, self.statuses = [], []
        self.lang = "zh"
        self.preferences = {}
    def desktop_input(self, text): pass
    def desktop_text(self, text): pass
    def desktop_end(self): pass
    def agent_text(self, text): pass
    def agent_notice(self, text): self.notices.append(text)
    def local_notice(self, text): self.notices.append(text)
    def agent_status(self, status): self.statuses.append(status)

    def events(self, name):
        return [c.kwargs for c in self.st.log.call_args_list if c.args and c.args[0] == name]


class ClaudeFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        shared.REBOOT_TRIED.clear()
        self.tmp = tempfile.TemporaryDirectory(prefix="p418-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"; self.work.mkdir()
        self.cc = self.root / "claude"; (self.cc / "sessions").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.cc), "AGENTJ_HARNESS_PREPARE": "off"})
        env.start(); self.addCleanup(env.stop)

        async def channel(agent):
            agent.loop = asyncio.get_running_loop()
        for p in (patch.object(shared.SharedClaudeAgent, "prepare_channel", new=channel),
                  patch.object(shared, "_bin", new=lambda env, name: "/fake/bin/" + name)):
            p.start(); self.addCleanup(p.stop)
        self.cfg = {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True, "session_mode": "shared",
                    "high_risk_warnings": False, "shared_session_id": PIN}
        self.boot = time.time() - 600          # booted ten minutes ago
        self.calls = []
        self.answer_with = PIN                 # the session id the started Claude Code registers (None = it dies)
        self._real_exec = asyncio.create_subprocess_exec

    def transcript(self, sid, mtime=None):
        t = shared.claude_transcript(str(self.work), sid)
        t.parent.mkdir(parents=True, exist_ok=True); t.write_text("")
        if mtime is not None:
            os.utime(t, (mtime, mtime))
        return t

    def register(self, pid, sid):
        sock = socket.socket(socket.AF_UNIX); path = self.root / f"{pid}.sock"; sock.bind(str(path)); sock.listen()
        self.addCleanup(sock.close)
        now = int(time.time() * 1000)
        d = {"pid": pid, "cwd": str(self.work), "sessionId": sid, "kind": "interactive", "entrypoint": "cli",
             "messagingSocketPath": str(path), "startedAt": now, "updatedAt": now}
        (self.cc / "sessions" / f"{pid}.json").write_text(json.dumps(d))

    async def fake_exec(self, *argv, **kw):
        """Stands in for the real `claude`: records argv + kwargs; registers a live session (a sleeping child) under
        `answer_with`, or exits at once when that is None."""
        prefix = resource_policy.work_argv([])
        self.assertEqual(list(argv[:len(prefix)]), prefix)
        self.calls.append((list(argv[len(prefix):]), kw))
        quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.answer_with is None:
            return await self._real_exec("true", **quiet)
        p = await self._real_exec("sleep", "300", **quiet)
        self.addCleanup(lambda: p.returncode is None and p.kill())
        sid = self.answer_with
        if sid == "SAME":
            sid = next((a for i, a in enumerate(argv) if i and argv[i - 1] == "--resume"), NEWID)
        self.register(p.pid, sid)
        return p

    async def attach(self, agent, **kw):
        with patch.object(shared.asyncio, "create_subprocess_exec", new=self.fake_exec), \
                patch.object(shared, "boot_time", new=kw.get("boot", lambda: self.boot)):
            ok = await agent.attach()
        return ok

    async def drop(self, agent):
        await agent._drop_native()


class RebootResume(ClaudeFixture):
    async def test_pinned_session_from_before_the_boot_is_resumed_and_attached(self):
        self.transcript(PIN, mtime=self.boot - 3600)
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        self.answer_with = "SAME"
        self.assertTrue(await self.attach(a))
        try:
            argv, kw = self.calls[0]
            self.assertEqual(argv, ["/fake/bin/claude", "--resume", PIN])
            self.assertTrue(kw["start_new_session"])
            self.assertEqual(a.session["sessionId"], PIN)
            self.assertEqual(a.cfg["shared_session_id"], PIN)
            ev = a.host.events("shared_reboot_resume")
            self.assertEqual(ev, [{"agent": "claude", "how": "resume", "result": "same", "session": PIN[:8]}])
            self.assertNotIn(PIN, json.dumps(ev))                       # never the whole id in the log
            self.assertTrue(any("电脑重启过" in n for n in a.host.notices), a.host.notices)
            self.assertFalse(any(OLD_TEXT in n for n in a.host.notices))
        finally:
            await self.drop(a)

    async def test_a_new_id_from_claude_code_moves_the_pin(self):
        self.transcript(PIN, mtime=self.boot - 3600)
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        self.answer_with = NEWID
        self.assertTrue(await self.attach(a))
        try:
            self.assertEqual(self.calls[0][0][-2:], ["--resume", PIN])
            self.assertEqual(a.session["sessionId"], NEWID)
            self.assertEqual(a.cfg["shared_session_id"], NEWID)
            a.host.st.set_agent_session.assert_any_call("claude.pin", NEWID)     # P330: Claude's own pin
            self.assertEqual(a.host.events("shared_reconnect")[0]["how"], "resumed")
            self.assertEqual(a.host.events("shared_reboot_resume")[0]["result"], "repinned")
            self.assertIn("又接上了，新会话 " + NEWID[:8], a.host.notices[-1])
        finally:
            await self.drop(a)

    async def test_deleted_transcript_of_the_served_session_starts_a_new_one(self):
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))     # no transcript at all
        self.answer_with = NEWID
        self.assertTrue(await self.attach(a))
        try:
            self.assertEqual(self.calls[0][0], ["/fake/bin/claude"])           # no --resume
            self.assertEqual(a.cfg["shared_session_id"], NEWID)
            self.assertEqual(a.host.events("shared_reconnect")[0]["how"], "new_after_reboot")
            self.assertEqual(a.host.events("shared_reboot_resume")[0]["how"], "new_after_reboot")
        finally:
            await self.drop(a)

    async def test_an_unknown_pin_without_transcript_is_not_started(self):
        a = shared.SharedClaudeAgent(Host(last=None), dict(self.cfg))     # Agent J never served it
        self.assertFalse(await self.attach(a))
        self.assertEqual(self.calls, [])
        self.assertIn(OLD_TEXT, a.host.notices[-1])

    async def test_a_session_the_owner_closed_during_this_boot_is_not_started(self):
        self.transcript(PIN, mtime=self.boot + 60)
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        self.assertFalse(await self.attach(a))
        self.assertEqual(self.calls, [])
        self.assertIn(OLD_TEXT, a.host.notices[-1])
        self.assertEqual(a.host.events("shared_reboot_resume"), [])

    async def test_unknown_boot_time_starts_nothing(self):
        self.transcript(PIN, mtime=self.boot - 3600)
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        self.assertFalse(await self.attach(a, boot=lambda: None))
        self.assertEqual(self.calls, [])
        self.assertIn(OLD_TEXT, a.host.notices[-1])

    async def test_tried_once_per_pin_then_the_old_text(self):
        self.transcript(PIN, mtime=self.boot - 3600)
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        self.answer_with = None                                           # the started Claude Code exits at once
        self.assertFalse(await self.attach(a))
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(a.native)                                       # nothing of ours left behind
        self.assertEqual(a.host.events("shared_reboot_resume")[0]["result"], "failed")
        self.assertIn(OLD_TEXT, a.host.notices[-1])
        self.assertFalse(await self.attach(a))
        b = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))     # same serve process, a fresh adapter
        self.assertFalse(await self.attach(b))
        self.assertEqual(len(self.calls), 1)

    async def test_a_live_successor_is_still_followed_first(self):
        """P147's heal wins: a live owner session in the folder is followed, nothing is started."""
        self.transcript(PIN, mtime=self.boot - 3600)
        p = subprocess.Popen(["sleep", "300"]); self.addCleanup(p.wait); self.addCleanup(p.kill)
        self.register(p.pid, NEWID)
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        self.transcript(NEWID)
        self.assertTrue(await self.attach(a))
        self.assertEqual(self.calls, [])
        self.assertEqual(a.session["sessionId"], NEWID)

    async def test_resume_id_is_validated(self):
        a = shared.SharedClaudeAgent(Host(), dict(self.cfg))
        with self.assertRaises(ValueError):
            await a.ordinary_start(resume="--dangerously-skip-permissions")


class BootTime(unittest.TestCase):
    def test_linux_proc_stat(self):
        if not Path("/proc/stat").exists():
            self.skipTest("no procfs")
        b = shared.boot_time()
        self.assertIsNotNone(b)
        self.assertLess(b, time.time())

    def test_macos_sysctl(self):
        self.assertEqual(shared.parse_boottime("{ sec = 1760000000, usec = 123 } Tue Oct 21 08:00:00 2026\n"), 1760000000.0)
        self.assertIsNone(shared.parse_boottime(""))
        run = Mock(return_value=Mock(stdout="{ sec = 1760000001, usec = 5 } Tue"))
        with patch.object(shared.Path, "read_text", side_effect=OSError), patch.object(shared.sys, "platform", "darwin"), \
                patch("subprocess.run", run):
            self.assertEqual(shared.boot_time(), 1760000001.0)
        self.assertIn("kern.boottime", run.call_args.args[0])
        with patch.object(shared.Path, "read_text", side_effect=OSError), patch.object(shared.sys, "platform", "darwin"), \
                patch("subprocess.run", side_effect=OSError):
            self.assertIsNone(shared.boot_time())


class PrepareBeforeStart(ClaudeFixture):
    async def start(self, workflow_ceo=False, prepare=None):
        a = shared.SharedClaudeAgent(Host(), dict(self.cfg, shared_session_id=""))
        if not workflow_ceo:
            a.cfg.pop("_workflow_ceo")
        self.answer_with = NEWID
        prep = prepare or Mock(return_value={"done": ["claude.json"], "skipped": {}})
        with patch.object(harness_setup, "enabled", return_value=True), patch.object(harness_setup, "prepare", prep), \
                patch.object(shared.asyncio, "create_subprocess_exec", new=self.fake_exec):
            await a.ordinary_start()
        await self.drop(a)
        return a, prep

    async def test_trust_is_answered_right_before_our_own_start(self):
        a, prep = await self.start()
        prep.assert_called_once_with("claude", str(self.work))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(a.host.events("harness_prepared")[0]["why"], "ordinary_start")

    async def test_a_failing_prepare_never_blocks_the_start(self):
        a, prep = await self.start(prepare=Mock(side_effect=RuntimeError("x")))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(a.host.events("harness_prepare_failed")[0]["error"], "RuntimeError")

    async def test_workflow_ceo_like_serve_does_not_prepare(self):
        _, prep = await self.start(workflow_ceo=True)
        prep.assert_not_called()


# ---------------------------------------------------------------------------------------------------- ② OpenCode
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OCHost(Host):
    def __init__(self, answer=None):
        super().__init__()
        self.cards = []
        self.answer = answer

    async def question(self, card, gone=None, task=None):
        self.cards.append(card)
        return self.answer


class OCPlain(Host):
    """A host with no question cards (test fakes, older hosts)."""


class OwnerDown(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        shared.OWNER_DOWN_ASKED.clear()
        shared.OWNER_STARTED.clear()
        self.tmp = tempfile.TemporaryDirectory(prefix="p418oc-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.port = free_port()
        self.servers, self.execs = [], []
        p = patch.object(shared, "_bin", new=lambda env, name: "/fake/bin/" + name); p.start(); self.addCleanup(p.stop)
        self._real_exec = asyncio.create_subprocess_exec
        self.home = self.work / "home"; self.home.mkdir()   # owner rc files are looked at: never the real HOME
        env = patch.dict(os.environ, {"HOME": str(self.home)}); env.start(); self.addCleanup(env.stop)
        os.environ.pop("OPENCODE_SERVER_PASSWORD", None)

    async def asyncTearDown(self):
        for s in self.servers:
            s.close()
            await s.wait_closed()

    def agent(self, host):
        host.st.root = self.work
        cfg = {"kind": "opencode", "dir": str(self.work), "_workflow_ceo": True, "session_mode": "shared",
               "shared_opencode_port": self.port, "shared_session_id": "ses_p418owner", "high_risk_warnings": False}
        return shared.SharedOpenCodeAgent(host, cfg)

    async def serve(self, status):
        """A loopback stand-in for an OpenCode server: every request answers `status` with an empty body."""
        async def handle(r, w):
            with __import__("contextlib").suppress(Exception):
                while (await r.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                w.write(f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
                await w.drain()
            w.close()
        self.servers.append(await asyncio.start_server(handle, "127.0.0.1", self.port))

    async def fake_exec(self, *argv, **kw):
        prefix = resource_policy.work_argv([])
        self.assertEqual(list(argv[:len(prefix)]), prefix)
        self.execs.append((list(argv[len(prefix):]), kw))
        await self.serve(404)                    # the "opencode serve" we started now listens (v1-like: no /api/info)

        class Proc:
            returncode = None
            pid = 4242
            def terminate(self): pass
        return Proc()

    def attach_ok(self):
        async def events(agent_self, proc):
            agent_self.connected.set()
        return [patch.object(shared.SharedOpenCodeAgent, "_attach", new=AsyncMock()),
                patch.object(shared.SharedOpenCodeAgent, "_prime", new=AsyncMock()),
                patch.object(shared.SharedOpenCodeAgent, "_resync", new=AsyncMock()),
                patch.object(shared.SharedOpenCodeAgent, "_events", new=events)]

    async def test_refused_port_asks_then_starts_the_server_and_attaches(self):
        host = OCHost(answer=("answer", [[1]]))
        a = self.agent(host)
        ps = self.attach_ok()
        for p in ps:
            p.start(); self.addCleanup(p.stop)
        with patch.object(shared.asyncio, "create_subprocess_exec", new=self.fake_exec):
            self.assertTrue(await a._spawn())
        self.assertEqual(len(host.cards), 1)
        q = host.cards[0][0]
        self.assertIn(f"端口 {self.port}）没在运行——电脑重启后它不会自己起来", q["q"])
        self.assertEqual([o["l"] for o in q["o"]], ["帮我启动它（推荐）", "我自己在电脑上处理"])   # no 「其他」
        argv, kw = self.execs[0]
        self.assertEqual(argv, ["/fake/bin/opencode", "serve", "--hostname", "127.0.0.1", "--port", str(self.port)])
        self.assertTrue(kw["start_new_session"])
        self.assertEqual(kw["cwd"], str(self.work))
        self.assertEqual(kw["stdout"], asyncio.subprocess.DEVNULL)
        self.assertIn(f"已在端口 {self.port} 重新启动你的 OpenCode，并接回了会话", host.notices[-1])
        self.assertFalse(a.failed_start)
        for text in host.notices:
            for word in ("relay", "harness"):
                self.assertNotIn(word, text.lower())
        self.assertEqual(host.events("owner_opencode_started")[0]["result"], "listening")

    async def test_i_will_handle_it_gives_the_text_and_never_asks_again(self):
        host = OCHost(answer=("answer", [[2]]))
        a = self.agent(host)
        with patch.object(shared.asyncio, "create_subprocess_exec", new=self.fake_exec):
            self.assertFalse(await a._spawn())
            self.assertTrue(a.failed_start)
            self.assertIn("Agent J 不动它", host.notices[-1])
            self.assertIn(shared._UNSET, host.notices[-1])
            self.assertFalse(await a._spawn())                  # the next message: text only, no second card
        self.assertEqual(len(host.cards), 1)
        self.assertEqual(self.execs, [])
        self.assertIn("没在运行", host.notices[-1])

    async def test_no_answer_or_no_device_keeps_the_text(self):
        for answer in (("timeout", None), ("no_device", None)):
            shared.OWNER_DOWN_ASKED.clear()
            host = OCHost(answer=answer)
            a = self.agent(host)
            self.assertFalse(await a._spawn())
            self.assertIn("电脑重启后它不会自己起来", host.notices[-1])
            self.assertIn(shared._UNSET, host.notices[-1])

    async def test_host_without_cards_gets_the_text(self):
        host = OCPlain()
        a = self.agent(host)
        self.assertFalse(await a._spawn())
        self.assertTrue(a.failed_start)
        self.assertIn(f"端口 {self.port}）没在运行", host.notices[-1])

    async def test_a_started_server_that_never_listens_is_a_plain_failure(self):
        host = OCHost(answer=("answer", [[1]]))
        a = self.agent(host)

        class Dead:
            returncode = 1
        async def dies(*argv, **kw):
            self.execs.append(argv)
            return Dead()
        with patch.object(shared.asyncio, "create_subprocess_exec", new=dies):
            self.assertFalse(await a._spawn())
        self.assertIn("没能在端口", host.notices[-1])
        self.assertEqual(len(self.execs), 1)
        self.assertEqual(host.events("owner_opencode_started")[0]["result"], "exited")

    async def test_password_wanted_keeps_the_password_note(self):
        await self.serve(401)
        host = OCHost(answer=("answer", [[1]]))
        a = self.agent(host)
        self.assertFalse(await a._spawn())
        self.assertEqual(host.cards, [])
        self.assertIn("OPENCODE_SERVER_PASSWORD", host.notices[-1])

    async def test_reachable_but_wrong_session_keeps_the_attach_text(self):
        await self.serve(404)
        host = OCHost(answer=("answer", [[1]]))
        a = self.agent(host)
        self.assertFalse(await a._spawn())
        self.assertEqual(host.cards, [])
        self.assertIn(f"端口 {self.port}），但 Agent J 连不上它或它里面没有指定的会话", host.notices[-1])


class OwnerDownPassword(OwnerDown):
    """P418 RESUME: the owner's server had a password and serve's environment has none → never a password-less start."""
    async def assert_refuses(self, host):
        a = self.agent(host)
        with patch.object(shared.asyncio, "create_subprocess_exec", new=self.fake_exec):
            self.assertFalse(await a._spawn())
        self.assertEqual(self.execs, [])                     # nothing started
        self.assertTrue(a.failed_start)
        self.assertEqual(host.events("owner_opencode_started"), [])
        self.assertTrue(host.events("owner_opencode_password_needed"))
        return a

    async def test_a_401_seen_before_means_the_card_asks_for_the_password(self):
        await self.serve(401)                                # it was running with a password (before the reboot)
        host = OCHost(answer=("answer", [[1]]))
        self.assertFalse(await self.agent(host)._spawn())
        self.assertEqual(host.cards, [])
        mark = json.loads((self.work / shared.OWNER_PW_MARK).read_text())
        self.assertEqual(mark, {str(self.port): {"password": True}})   # the fact only, never a value
        for srv in self.servers:
            srv.close(); await srv.wait_closed()
        self.servers.clear()                                 # reboot: nothing listens now
        host = OCHost(answer=("answer", [[2]]))
        await self.assert_refuses(host)
        q = host.cards[0][0]
        self.assertIn("需要你的 OpenCode 密码：请在电脑上自己启动它，或用密钥卡把密码交给 Agent J", q["q"])
        self.assertEqual([o["l"] for o in q["o"]], ["用密钥卡提供密码", "我在电脑上自己启动"])
        self.assertNotIn("帮我启动它（推荐）", [o["l"] for o in q["o"]])
        self.assertIn("不会不设密码就替你启动它", host.notices[-1])

    async def test_secret_card_choice_gives_the_secret_request_command(self):
        (self.work / shared.OWNER_PW_MARK).write_text(json.dumps({str(self.port): {"password": True}}))
        host = OCHost(answer=("answer", [[1]]))
        await self.assert_refuses(host)
        self.assertIn("agentj secret request --name OPENCODE_SERVER_PASSWORD", host.notices[-1])

    async def test_password_named_in_the_owner_shell_config_is_a_sign(self):
        (self.home / ".zshrc").write_text("# OPENCODE_SERVER_PASSWORD in a comment is not one\n"
                                         "export OPENCODE_SERVER_PASSWORD=not-read-out\n")
        host = OCHost(answer=("timeout", None))
        await self.assert_refuses(host)
        self.assertNotIn("not-read-out", " ".join(host.notices) + json.dumps(host.cards, ensure_ascii=False))
        logged = json.dumps([c.kwargs for c in host.st.log.call_args_list], ensure_ascii=False, default=str)
        self.assertNotIn("not-read-out", logged)

    async def test_a_commented_out_password_line_is_not_a_sign(self):
        (self.home / ".bashrc").write_text("# export OPENCODE_SERVER_PASSWORD=x\n")
        host = OCHost(answer=("answer", [[1]]))
        a = self.agent(host)
        ps = self.attach_ok()
        for p in ps:
            p.start(); self.addCleanup(p.stop)
        with patch.object(shared.asyncio, "create_subprocess_exec", new=self.fake_exec):
            self.assertTrue(await a._spawn())               # no password before → Agent J may start it
        self.assertEqual(len(self.execs), 1)

    async def test_with_the_password_in_the_service_environment_it_starts(self):
        (self.work / shared.OWNER_PW_MARK).write_text(json.dumps({str(self.port): {"password": True}}))
        os.environ["OPENCODE_SERVER_PASSWORD"] = "p418-test-only"
        host = OCHost(answer=("answer", [[1]]))
        a = self.agent(host)
        ps = self.attach_ok()
        for p in ps:
            p.start(); self.addCleanup(p.stop)
        with patch.object(shared.asyncio, "create_subprocess_exec", new=self.fake_exec):
            self.assertTrue(await a._spawn())
        self.assertEqual(self.execs[0][1]["env"]["OPENCODE_SERVER_PASSWORD"], "p418-test-only")   # same password, kept
        self.assertEqual([o["l"] for o in host.cards[0][0]["o"]], ["帮我启动它（推荐）", "我自己在电脑上处理"])

    async def test_start_guard_refuses_even_if_called_directly(self):
        (self.work / shared.OWNER_PW_MARK).write_text(json.dumps({str(self.port): {"password": True}}))
        a = self.agent(OCHost())
        with patch.object(shared.asyncio, "create_subprocess_exec", new=self.fake_exec):
            self.assertEqual(await a._start_owner_server(self.port), "password_missing")
        self.assertEqual(self.execs, [])

    async def test_attaching_with_a_password_records_the_sign(self):
        os.environ["OPENCODE_SERVER_PASSWORD"] = "p418-test-only"
        await self.serve(404)
        host = OCHost()
        a = self.agent(host)
        ps = self.attach_ok()
        for p in ps:
            p.start(); self.addCleanup(p.stop)
        self.assertTrue(await a._spawn())
        self.assertTrue(a._owner_had_password(self.port))


if __name__ == "__main__":
    unittest.main()
