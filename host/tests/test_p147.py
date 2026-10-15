"""P147 起死回生: a shared Claude session that went away is followed to the owner's new one; `agentj doctor --reconnect`
repairs serve / relay / agent; every new session learns the self-check. Local fixtures only (fake registry, sleeping
child processes as live pids, local sockets) — never the owner's sessions."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import shared, shared_hook, reconnect, main_identity, names, cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "agentj"


class Host:
    def __init__(self):
        self.st = Mock()
        self.st.agent_session.return_value = None
        self.notices, self.statuses, self.prefs = [], [], []
        self.lang = "zh"
    def desktop_input(self, text): pass
    def desktop_text(self, text): pass
    def desktop_end(self): pass
    def agent_text(self, text): pass
    def agent_notice(self, text): self.notices.append(text)
    def local_notice(self, text): self.notices.append(text)
    def agent_status(self, status): self.statuses.append(status)
    async def set_pref(self, key, value): self.prefs.append((key, value))


class Fixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="p147-", dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"; self.work.mkdir()
        self.cc = self.root / "claude"; (self.cc / "sessions").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.cc)}); env.start(); self.addCleanup(env.stop)
        ch = patch.object(shared.SharedClaudeAgent, "prepare_channel", new=AsyncMock()); ch.start(); self.addCleanup(ch.stop)
        self.procs, self.socks = {}, []
        self.host = Host()
        self.cfg = {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True, "session_mode": "shared",
                    "high_risk_warnings": False}

    def session(self, tag, started, **extra):
        """A live registry entry: a real child process (pid alive) + a real listening socket + an empty transcript."""
        p = subprocess.Popen(["sleep", "300"])
        self.addCleanup(p.wait); self.addCleanup(p.kill)
        sock = socket.socket(socket.AF_UNIX); path = self.root / f"{tag}.sock"; sock.bind(str(path)); sock.listen()
        self.addCleanup(sock.close)
        sid = (tag * 8)[:8] + "-1111-1111-1111-111111111111"
        d = {"pid": p.pid, "cwd": str(self.work), "sessionId": sid, "kind": "interactive", "entrypoint": "cli",
             "messagingSocketPath": str(path), "startedAt": started, "updatedAt": started, **extra}
        (self.cc / "sessions" / f"{p.pid}.json").write_text(json.dumps(d))
        slug = __import__("re").sub(r"[^A-Za-z0-9]", "-", str(self.work))
        t = self.cc / "projects" / slug / (sid + ".jsonl"); t.parent.mkdir(parents=True, exist_ok=True); t.write_text("")
        self.procs[sid] = (p, d)
        return sid

    def close(self, sid):
        p, d = self.procs[sid]
        p.kill(); p.wait()
        (self.cc / "sessions" / f"{d['pid']}.json").unlink()

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)


class Registry(Fixture):
    async def test_headless_and_foreign_namespace_and_reused_pid_are_never_owner_sessions(self):
        own = self.session("a", 1)
        self.session("b", 2, entrypoint="sdk-cli")                       # `claude -p` registers as interactive
        self.session("c", 3, pidDomain="linux:x:pid:[1]")                 # another pid namespace
        self.session("d", 4, procStart="1")                               # pid now belongs to another process
        self.session("e", 5, kind="bg")
        ids = [d["sessionId"] for d in shared.claude_sessions(str(self.work), "", True)]
        self.assertEqual(ids, [own])
        # an explicit pin may still name the headless one; liveness guards apply to every path
        self.assertEqual(len(shared.claude_sessions(str(self.work), "bbbbbbbb-1111-1111-1111-111111111111")), 1)
        self.assertEqual(shared.claude_sessions(str(self.work), "dddddddd-1111-1111-1111-111111111111"), [])

    async def test_real_proc_start_matches(self):
        # this process's own start time from /proc → the same value Claude Code writes as procStart
        stat = Path("/proc/self/stat").read_text()
        mine = stat[stat.rfind(")") + 2:].split()[19]
        self.assertTrue(shared._same_process({"pid": os.getpid(), "procStart": mine}))
        self.assertFalse(shared._same_process({"pid": os.getpid(), "procStart": str(int(mine) + 1)}))
        ns = os.readlink("/proc/self/ns/pid").split(":", 1)[1]
        self.assertTrue(shared._same_process({"pid": os.getpid(), "pidDomain": "linux:abc:pid:" + ns}))


class Heal(Fixture):
    async def test_dead_pin_follows_the_only_owner_session_not_the_headless_one(self):
        old = self.session("a", 100)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": old})
        self.assertTrue(await a.attach())
        self.close(old)
        a.read_transcript()                                   # the observer's tick notices the dead process
        self.assertIsNone(a.session)
        new = self.session("b", 200)
        self.session("c", 300, entrypoint="sdk-cli")          # newer, but a headless `claude -p`
        self.assertTrue(await a.attach())
        await self.settle()
        self.assertEqual(a.session["sessionId"], new)
        self.assertEqual(a.cfg["shared_session_id"], new)
        self.assertEqual(self.host.prefs, [("agent.shared_session_id", new)])
        self.assertIn("已接上新会话 " + new[:8], self.host.notices[-1])
        self.assertEqual(self.host.statuses[-1], "idle")

    async def test_two_equally_likely_sessions_ask_once_then_the_announced_one_wins(self):
        dead = "99999999-1111-1111-1111-111111111111"          # pinned before this serve started; nothing known about it
        one, two = self.session("a", 100), self.session("b", 200)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": dead})
        self.assertFalse(await a.attach())
        self.assertFalse(await a.attach())
        asks = [n for n in self.host.notices if "不确定接哪个" in n]
        self.assertEqual(len(asks), 1)
        self.assertIn(one[:8], asks[0]); self.assertIn(two[:8], asks[0])
        res = await a.hook_event({"hook_event_name": "SessionStart", "source": "startup", "session_id": two})
        self.assertEqual(a.session["sessionId"], two)
        self.assertIsInstance(res, dict)

    async def test_the_one_started_after_the_lost_session_wins(self):
        older = self.session("a", 50)
        served = self.session("b", 100)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": served})
        self.assertTrue(await a.attach())
        self.close(served); a.read_transcript()
        newer = self.session("c", 150)
        self.assertTrue(await a.attach())
        self.assertEqual(a.session["sessionId"], newer)
        self.assertNotEqual(a.session["sessionId"], older)

    async def test_session_start_from_a_new_window_replaces_a_dead_one_at_once(self):
        served = self.session("a", 100)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": served})
        self.assertTrue(await a.attach())
        self.close(served)                                    # not yet noticed by the observer
        new = self.session("b", 200)
        await a.hook_event({"hook_event_name": "SessionStart", "source": "startup", "session_id": new})
        self.assertEqual(a.session["sessionId"], new)

    async def test_a_live_served_session_is_never_replaced_by_another_window(self):
        served = self.session("a", 100)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": served})
        self.assertTrue(await a.attach())
        other = self.session("b", 200)
        self.assertEqual(await a.hook_event({"hook_event_name": "SessionStart", "source": "startup", "session_id": other}), {})
        self.assertEqual(a.session["sessionId"], served)
        self.assertEqual(self.host.prefs, [])

    async def test_unattached_owner_session_is_told_how_to_reconnect(self):
        one, two = self.session("a", 100), self.session("b", 200)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": "99999999-1111-1111-1111-111111111111"})
        a.heal_seen = {one: 0.0}
        hint = a._unattached_hint(two)
        self.assertIn("agentj doctor --reconnect", hint["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(a._unattached_hint("00000000-0000-0000-0000-000000000000"), {})

    async def test_reconnect_with_a_named_session_prefix(self):
        dead = "99999999-1111-1111-1111-111111111111"
        one, two = self.session("a", 100), self.session("b", 200)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": dead})
        r = await a.reconnect()
        self.assertEqual((r["ok"], r["why"]), (False, "ambiguous"))
        self.assertEqual(sorted(r["choices"]), sorted([one[:8], two[:8]]))
        r = await a.reconnect(one[:8])
        self.assertEqual((r["ok"], r["session"]), (True, one[:8]))
        self.assertEqual(a.cfg["shared_session_id"], one)
        self.assertEqual((await a.reconnect("12345678"))["why"], "not_found")
        r = await a.reconnect()
        self.assertEqual((r["ok"], r["changed"]), (True, False))


class Regressions(Fixture):
    async def test_observer_survives_the_served_session_going_away(self):
        """Before P147 the observer read the transcript of a session already dropped → TypeError → the loop died and
        Agent J never re-attached by itself (found by the real e2e)."""
        served = self.session("a", 100)
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": served})
        self.assertTrue(await a.attach())
        a.read_transcript()
        self.close(served)
        a.read_transcript()
        self.assertIsNone(a.session)
        a.read_transcript()                                   # a later tick: no crash, nothing to do
        new = self.session("b", 200)
        with patch.object(shared.asyncio, "sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):
                await a.observe()
        self.assertEqual(a.session["sessionId"], new)

    async def test_a_window_without_its_first_message_yet_is_attachable(self):
        import time
        old = "99999999-1111-1111-1111-111111111111"
        new = self.session("b", int(time.time() * 1000))
        slug = __import__("re").sub(r"[^A-Za-z0-9]", "-", str(self.work))
        (self.cc / "projects" / slug / (new + ".jsonl")).unlink()
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": old})
        self.assertTrue(await a.attach())
        self.assertIn("已接上新会话 " + new[:8], self.host.notices[-1])

    async def test_an_old_session_without_transcript_is_still_refused(self):
        sid = self.session("b", 1000)
        slug = __import__("re").sub(r"[^A-Za-z0-9]", "-", str(self.work))
        (self.cc / "projects" / slug / (sid + ".jsonl")).unlink()
        a = shared.SharedClaudeAgent(self.host, {**self.cfg, "shared_session_id": sid})
        self.assertFalse(await a.attach())


class HookFallback(unittest.TestCase):
    def test_session_start_without_agentj_says_how_to_reconnect_and_never_decides(self):
        r = shared_hook.fallback("SessionStart", "claude", {}, False)
        self.assertIn("agentj doctor --reconnect", r["additionalContext"])
        self.assertNotIn("decision", json.dumps(r))
        self.assertIsNone(shared_hook.fallback("PreToolUse", "claude", {"tool_name": "Bash"}, True))
        self.assertIsNone(shared_hook.fallback("SessionStart", "codex", {}, False))


class FakeService:
    def __init__(self, installed=True, active="inactive"):
        self.s = {"installed": installed, "active": active}
        self.calls = []
    def status(self): return dict(self.s)
    def start(self, st): self.calls.append("start")
    def restart(self): self.calls.append("restart")


class Doctor(unittest.TestCase):
    def run_it(self, answers, service=None, env=None, session=None, as_json=True):
        seq, sent = list(answers), []
        def ctl(st, req, timeout=None):
            sent.append(req)
            if req["cmd"] == "status":
                v = seq.pop(0) if len(seq) > 1 else seq[0]
            else:
                v = seq.pop(0)
            if isinstance(v, Exception):
                raise v
            return v
        out = io.StringIO()
        code = reconnect.run(Mock(), "zh", session=session, ask=False, out=out, ctl=ctl, service=service or FakeService(),
                             sleep=lambda s: None, env=env or {}, as_json=as_json)
        return code, (json.loads(out.getvalue()) if as_json else out.getvalue()), sent

    UP = {"relay_state": "up", "agent": "claude", "agent_status": "idle"}

    def test_all_up_is_one_sentence_and_no_repair(self):
        code, res, sent = self.run_it([self.UP])
        self.assertEqual(code, 0); self.assertTrue(res["ok"]); self.assertEqual(res["steps"], [])
        self.assertEqual([r["cmd"] for r in sent], ["status"])

    def test_serve_not_running_is_started(self):
        svc = FakeService()
        code, res, _ = self.run_it([None, None, self.UP], service=svc)
        self.assertEqual((code, svc.calls), (0, ["start"]))
        self.assertIn("已启动", res["steps"][0])

    def test_not_installed_says_service_install(self):
        code, res, _ = self.run_it([None], service=FakeService(installed=False))
        self.assertEqual(code, 1); self.assertIn("agentj service install", res["result"]); self.assertEqual(res["broken"], "serve")

    def test_relay_down_waits_then_restarts_once(self):
        svc = FakeService(active="active")
        down = {**self.UP, "relay_state": "reconnecting"}
        code, res, _ = self.run_it([down] * 25 + [self.UP], service=svc)
        self.assertEqual(code, 0); self.assertEqual(svc.calls, ["restart"])

    def test_agent_down_attaches_the_calling_session(self):
        down = {**self.UP, "agent_status": "down"}
        sid = "abcdefab-1111-1111-1111-111111111111"
        code, res, sent = self.run_it([down, {"ok": True, "changed": True, "session": sid[:8]}],
                                      env={"CLAUDE_CODE_SESSION_ID": sid})
        self.assertEqual(code, 0)
        self.assertEqual(sent[1], {"cmd": "agent_reconnect", "session": sid})
        self.assertIn(sid[:8], res["steps"][-1])

    def test_ambiguous_without_terminal_names_the_choices(self):
        down = {**self.UP, "agent_status": "down"}
        code, res, _ = self.run_it([down, {"ok": False, "why": "ambiguous", "choices": ["aaaaaaaa", "bbbbbbbb"]}])
        self.assertEqual(code, 1); self.assertEqual(res["choices"], ["aaaaaaaa", "bbbbbbbb"])
        self.assertIn("--session", res["result"])

    def test_serve_busy_is_restarted(self):
        svc = FakeService(active="active")
        code, _, _ = self.run_it([names.ServeBusy("x"), self.UP], service=svc)
        self.assertEqual((code, svc.calls), (0, ["restart"]))


class Surfaces(unittest.TestCase):
    def test_identity_line_both_languages(self):
        for lang, needles in (("zh", ("agentj status --json", "agentj doctor --reconnect", "手机变灰不等于中继坏了")),
                              ("en", ("agentj status --json", "agentj doctor --reconnect", "does not mean the relay is broken"))):
            text = main_identity.prompt({"language": lang})
            for n in needles:
                self.assertIn(n, text)

    def test_skill_and_docs_ship(self):
        s = (ROOT / "skills/agentj-reconnect/SKILL.md").read_text()
        for n in ("name: agentj-reconnect", "agentj status --json", "agentj doctor --reconnect", "--session", "手机灰"):
            self.assertIn(n, s)
        docs = ROOT.parents[1] / "site/content/docs/27-reconnect"
        if not docs.parent.is_dir():
            self.skipTest("no site/ (public export ships host/ alone)")
        for f in ("zh.src.md", "zh.md", "en.md"):
            self.assertIn("agentj doctor --reconnect", (docs / f).read_text())

    def test_cli_flags(self):
        captured = {}
        with patch.object(cli, "cmd_doctor", side_effect=lambda a: captured.update(vars(a))):
            cli.main(["doctor", "--reconnect", "--session", "abcdefab"])
        self.assertTrue(captured.get("reconnect")); self.assertEqual(captured.get("session"), "abcdefab")


if __name__ == "__main__":
    unittest.main()
