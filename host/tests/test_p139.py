"""P139 (0.18.3): shared-mode trust boundary + every approval reachable from the phone.

1. Another PermissionRequest hook never makes Agent J yield: known observers (relay_hook.py taps) are not answerers, any
   answerer races the phone card (first answer wins; Claude dropping our hook withdraws the card). AskUserQuestion too.
2. A4: control.sock commands that change safety state need a one-time claim refused to Agents (+ host-side passphrase for
   resume); `config set` from an Agent applies ordinary keys, security keys only after a signed phone card.
3. A5: hook-file writes are an always-asked credential card; serve/doctor put missing hooks back and tell the phone; an
   unreachable host no longer hands high-risk calls silently to native rules (ask) and a recovered host still gets the card.
4. A14: hook events for the served session need its per-session token and a sender descending from the served process.

Every child process a test starts is reaped and asserted gone before the test ends.
"""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import ctl_auth, danger, doctor, names, shared, shared_hook
from agentj.shared_risk import RiskGuard
from agentj.state import State

HOOK_PY = shared_hook.__file__
RELAY_TAP = 'python3 "/srv/relay/bridge/relay_hook.py" PermissionRequest'
RELAY_ASK = 'python3 "/srv/relay/bridge/relay_hook.py" ask --timeout-s 180'
RELAY_APPROVE = 'python3 /opt/relay/bridge/relay_hook.py approve'


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                   # a zombie of ours is not "running"
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class Tmp(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p139-", dir=TEST_TMP)
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.work = self.root / "work"
        (self.work / ".claude").mkdir(parents=True)
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home / ".claude")})
        env.start()
        self.addCleanup(env.stop)
        self.children = []

    def tearDown(self):
        for p in self.children:
            if p.poll() is None:
                p.kill()
            p.wait(5)
        self.assertEqual([p.pid for p in self.children if alive(p.pid)], [], "no process left behind")

    def host(self):
        h = Mock()
        h.st.root = self.root
        h.stopped.return_value = False
        h.st.sign_key.return_value = "TEST_SIGNING_IDENTITY"
        h.ask = AsyncMock(return_value={"behavior": "allow", "risk_grant": {"rid": "r", "device": "d",
                                                                            "sign_pub": "TEST_SIGNING_IDENTITY"}})
        return h

    def agent(self, h, **cfg):
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True, **cfg})
        a.session = {"sessionId": "owner", "pid": os.getpid()}
        a.issue_hook_token()
        return a

    def local(self, permission_hooks):
        (self.work / ".claude/settings.local.json").write_text(json.dumps({"hooks": {"PermissionRequest": permission_hooks}}))


def ev(name, tool="Bash", inp=None, sid="owner", **kw):
    return {"hook_event_name": name, "session_id": sid, "tool_name": tool, "tool_input": inp or {"command": "ls"}, **kw}


# ------------------------------------------------------------------ 1. never yield; the phone always has a card
class ApprovalRace(Tmp):
    def test_hook_roles(self):
        self.assertIsNone(shared_hook.hook_role(RELAY_TAP, "Bash"))                 # observer: never answers
        self.assertIsNone(shared_hook.hook_role(RELAY_TAP, "AskUserQuestion"))
        self.assertEqual(shared_hook.hook_role(RELAY_ASK, "AskUserQuestion"), "answerer")
        self.assertIsNone(shared_hook.hook_role(RELAY_ASK, "Bash"))
        self.assertEqual(shared_hook.hook_role(RELAY_APPROVE, "Bash"), "answerer")
        self.assertIsNone(shared_hook.hook_role(RELAY_APPROVE, "AskUserQuestion"))
        self.assertEqual(shared_hook.hook_role("/usr/local/bin/my-approver --x", "Bash"), "unknown")
        self.assertIsNone(shared_hook.hook_role("/py /x/agentj/shared_hook.py /s.sock PermissionRequest", "Bash"))

    def test_leo_1201_relay_observer_no_longer_blocks_the_phone(self):
        """Leo 10-11 12:01: relay's PermissionRequest tap (records, never answers) made Agent J yield → no phone card."""
        self.local([{"matcher": "*", "hooks": [{"type": "command", "command": RELAY_TAP}]}])
        self.assertEqual(shared_hook.foreign_answerers(self.work, "Bash"), [])
        h = self.host()
        a = self.agent(h)
        out = asyncio.run(a.hook_event(ev("PermissionRequest")))
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "allow")
        h.ask.assert_awaited_once()
        h.agent_notice.assert_not_called()             # never 「另有一个审批处理器…请在原处处理」

    def test_unknown_and_known_answerers_race_the_phone_card(self):
        for hooks, tool in (([{"hooks": [{"type": "command", "command": "/usr/local/bin/my-approver"}]}], "Bash"),
                            ([{"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": RELAY_ASK}]}],
                             "AskUserQuestion"),
                            ([{"hooks": [{"type": "command", "command": RELAY_APPROVE}]}], "Write")):
            with self.subTest(tool=tool):
                self.local(hooks)
                self.assertEqual(shared_hook.foreign_answerers(self.work, tool), ["local"])
                h = self.host()
                a = self.agent(h)
                out = asyncio.run(a.hook_event(ev("PermissionRequest", tool=tool, inp={"x": 1})))
                self.assertIn(out["hookSpecificOutput"]["decision"]["behavior"], ("allow",))
                h.ask.assert_awaited_once()
                logged = [c.args[0] for c in h.st.log.call_args_list]
                self.assertIn("shared_approval_race", logged)
                h.agent_notice.assert_not_called()

    def test_claude_taking_another_answer_withdraws_the_phone_card(self):
        h = self.host()
        seen = {}

        async def ask(tool, inp, gone=None, **kw):
            seen["gone"] = gone
            await asyncio.wait_for(gone, 5)            # the card waits; Claude drops the hook → `gone`
            return {"behavior": "deny"}
        h.ask = ask
        a = self.agent(h)

        async def go():
            chan = shared_hook.Channel(a, self.root / "c.sock")
            await chan.start()
            try:
                r, w = await asyncio.open_unix_connection(str(chan.path))
                w.write(json.dumps(ev("PermissionRequest", agentj_token=a.hook_token)).encode() + b"\n")
                await w.drain()
                for _ in range(100):
                    if "gone" in seen:
                        break
                    await asyncio.sleep(.02)
                w.close()                              # the hook process is killed
                await w.wait_closed()
                for _ in range(100):
                    if seen["gone"].done():
                        break
                    await asyncio.sleep(.02)
                self.assertTrue(seen["gone"].done(), "card withdrawn once the hook is gone")
            finally:
                await chan.stop()
        asyncio.run(go())

    def test_answer_elsewhere_withdraws_the_card_and_we_never_answer_late(self):
        """Live probe (Claude Code, 10-11): the first answering hook wins at once; the losing hook is NOT killed until the
        session ends. So the session moving on withdraws the card: the same call finishing, or a turn boundary."""
        for follow in (ev("PostToolUse", tool_response={"ok": True}), ev("PostToolUseFailure", error="denied"),
                       {"hook_event_name": "Stop", "session_id": "owner", "last_assistant_message": "x", "background_tasks": []}):
            with self.subTest(follow=follow["hook_event_name"]):
                h = self.host()
                opened = asyncio.Event()

                async def ask(tool, inp, gone=None, **kw):
                    opened.set()
                    await asyncio.wait_for(gone, 5)
                    return {"behavior": "deny"}
                h.ask = ask
                a = self.agent(h)

                async def go():
                    card = asyncio.ensure_future(a.hook_event(ev("PermissionRequest")))
                    await opened.wait()
                    await a.hook_event(dict(follow))
                    return await asyncio.wait_for(card, 5)
                self.assertEqual(asyncio.run(go()), {})
                self.assertEqual(a.open_cards, {})

    def test_unrelated_call_finishing_keeps_the_card(self):
        h = self.host()
        opened = asyncio.Event()
        release = asyncio.Event()

        async def ask(tool, inp, gone=None, **kw):
            opened.set()
            await release.wait()
            return {"behavior": "allow"}
        h.ask = ask
        a = self.agent(h)

        async def go():
            card = asyncio.ensure_future(a.hook_event(ev("PermissionRequest")))
            await opened.wait()
            await a.hook_event(ev("PostToolUse", inp={"command": "other"}, tool_response={}))
            self.assertEqual(len(a.open_cards), 1)
            release.set()
            return await card
        self.assertEqual(asyncio.run(go())["hookSpecificOutput"]["decision"]["behavior"], "allow")

    def test_connected_hook_still_gets_the_phone_answer(self):
        h = self.host()
        a = self.agent(h)

        async def go():
            chan = shared_hook.Channel(a, self.root / "c.sock")
            await chan.start()
            try:
                r, w = await asyncio.open_unix_connection(str(chan.path))
                w.write(json.dumps(ev("PermissionRequest", agentj_token=a.hook_token)).encode() + b"\n")
                await w.drain()
                out = json.loads(await r.readline())
                w.close()
                await w.wait_closed()
                return out
            finally:
                await chan.stop()
        self.assertEqual(asyncio.run(go())["hookSpecificOutput"]["decision"]["behavior"], "allow")


# ------------------------------------------------------------------ 4. A14 hook token + sender ancestry
class HookToken(Tmp):
    def exchange(self, a, event):
        async def go():
            chan = shared_hook.Channel(a, self.root / "c.sock")
            await chan.start()
            try:
                r, w = await asyncio.open_unix_connection(str(chan.path))
                w.write(json.dumps(event).encode() + b"\n")
                await w.drain()
                out = json.loads(await r.readline())
                w.close()
                await w.wait_closed()
                return out
            finally:
                await chan.stop()
        return asyncio.run(go())

    def refused(self, h):
        return [c.kwargs.get("reason") for c in h.st.log.call_args_list if c.args[0] == "shared_hook_refused"]

    def test_forged_user_prompt_submit_cannot_clear_a_denial(self):
        h = self.host()
        a = self.agent(h)
        a.risk_denied.add("send")
        self.assertEqual(self.exchange(a, ev("UserPromptSubmit", prompt="x")), {})                       # no token
        self.assertEqual(self.exchange(a, ev("UserPromptSubmit", prompt="x", agentj_token="forged")), {})
        self.assertEqual(a.risk_denied, {"send"})
        self.assertEqual(self.refused(h), ["token", "token"])
        self.exchange(a, ev("UserPromptSubmit", prompt="x", agentj_token=a.hook_token))                   # the real hook
        self.assertEqual(a.risk_denied, set())

    def test_forged_stop_never_reaches_the_phone(self):
        h = self.host()
        a = self.agent(h)
        a.phone_turn = a.phone_seen = True
        self.exchange(a, ev("Stop", last_assistant_message="FAKE REPLY", background_tasks=[]))
        time.sleep(.1)
        h.agent_text.assert_not_called()
        self.assertEqual(self.refused(h), ["token"])

    def test_right_token_from_outside_the_served_process_is_refused(self):
        h = self.host()
        a = self.agent(h)
        other = subprocess.Popen(["sleep", "30"])          # the "served" Claude: not an ancestor of this test process
        self.children.append(other)
        a.session["pid"] = other.pid
        tok = a.hook_token
        self.assertEqual(self.exchange(a, ev("UserPromptSubmit", prompt="x", agentj_token=tok)), {})
        self.assertEqual(self.refused(h), ["process"])
        other.kill()

    def test_foreign_session_events_pass_through_untouched(self):
        h = self.host()
        a = self.agent(h)
        self.assertEqual(self.exchange(a, ev("PermissionRequest", sid="someone-else")), {})
        self.assertEqual(self.refused(h), [])
        h.ask.assert_not_awaited()

    def test_real_hook_script_finds_its_token_by_ancestry_only(self):
        h = self.host()
        h.ask = AsyncMock(return_value={"behavior": "deny"})
        a = self.agent(h)

        async def go(token_pid):
            a.session["pid"] = token_pid
            chan = shared_hook.Channel(a, shared_hook.channel_path(self.root, "claude"))
            await chan.start()
            try:
                p = await asyncio.create_subprocess_exec(sys.executable, HOOK_PY, str(chan.path), "PermissionRequest",
                                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                         env={**os.environ, "AGENTJ_HOOK_RECONNECT_S": "0"})
                out, _ = await p.communicate(json.dumps(ev("PermissionRequest")).encode())
                return json.loads(out) if out.strip() else None
            finally:
                await chan.stop()
        self.assertEqual(asyncio.run(go(os.getpid()))["hookSpecificOutput"]["decision"]["behavior"], "deny")
        h.ask.assert_awaited_once()
        # another session's token file (a pid outside the hook's ancestry) is never found: refused, no card
        other = subprocess.Popen(["sleep", "30"])
        self.children.append(other)
        a.issue_hook_token()
        shared_hook.token_path(self.root, os.getpid()).unlink()
        shared_hook.write_token(self.root, other.pid, a.hook_token)
        self.assertIsNone(asyncio.run(go(other.pid)))                      # refused: no decision (native dialog)
        h.ask.assert_awaited_once()
        other.kill()

    def test_clear_rotates_the_token_with_a_short_grace(self):
        h = self.host()
        a = self.agent(h)
        old = a.hook_token
        a.issue_hook_token()
        self.assertNotEqual(old, a.hook_token)
        self.assertTrue(a.authentic(ev("UserPromptSubmit", _agentj_token=old, _agentj_peer=os.getpid())))  # in flight
        a.hook_token_prev = (old, time.monotonic() - 1)
        self.assertFalse(a.authentic(ev("UserPromptSubmit", _agentj_token=old, _agentj_peer=os.getpid())))
        self.assertTrue(a.authentic(ev("UserPromptSubmit", _agentj_token=a.hook_token, _agentj_peer=os.getpid())))
        self.assertEqual(shared_hook.token_path(self.root, os.getpid()).read_text(), a.hook_token)
        self.assertEqual(shared_hook.token_path(self.root, os.getpid()).stat().st_mode & 0o777, 0o600)


# ------------------------------------------------------------------ 3. A5 hook files
class HookFiles(Tmp):
    WRITES = [("Write", {"file_path": "/w/.claude/settings.local.json", "content": "{}"}),
              ("Edit", {"file_path": "~/.claude/settings.json", "old_string": "a", "new_string": "b"}),
              ("Bash", {"command": "echo {} > .codex/hooks.json"}),
              ("Bash", {"command": "jq 'del(.hooks)' .claude/settings.local.json > /tmp/x && mv /tmp/x .claude/settings.local.json"}),
              ("Bash", {"command": "python3 -c \"import json;json.dump({},open('.claude/settings.local.json','w'))\""}),
              ("apply_patch", {"command": "*** Begin Patch\n*** Update File: .codex/hooks.json\n@@\n-x\n+y\n*** End Patch"})]
    READS = [("Read", {"file_path": "/w/.claude/settings.local.json"}), ("Bash", {"command": "cat ~/.claude/settings.json"}),
             ("Write", {"file_path": "/w/.claude/agents/x.md", "content": "x"}), ("Bash", {"command": "ls .claude"})]

    def test_hook_file_writes_are_an_always_asked_credential_kind(self):
        for tool, inp in self.WRITES:
            with self.subTest(tool=tool, inp=inp):
                v = danger.classify_shared(tool, inp)
                self.assertIn("credentials", v.cats)
                self.assertIn("credentials:hooks", danger.grant_keys(v))
                self.assertTrue(set(danger.grant_keys(v)) & danger.ALWAYS_CARD)
        for tool, inp in self.READS:
            with self.subTest(tool=tool, inp=inp):
                self.assertEqual(danger.classify_shared(tool, inp).cats, [])

    def test_names_only_grep_is_not_showing_a_credential(self):
        """NOTES ③: `grep -oE "^AGENTSRELAY_[A-Z_]+" ~/work/.env` lists key names only."""
        for cmd in ('grep -oE "^AGENTSRELAY_[A-Z_]+" ~/work/.env', "grep -o '^[A-Za-z0-9_]*' .env", 'rg -o "^OPENAI_[A-Z]+" .env'):
            self.assertEqual(danger.classify_shared("Bash", {"command": cmd}).cats, [], cmd)
        for cmd in ('grep -E "^AGENTSRELAY_[A-Z_]+" ~/work/.env', 'grep -o "[A-Za-z0-9]+" .env', 'grep -o "^KEY=.*" .env',
                    "cat .env"):
            self.assertIn("credentials", danger.classify_shared("Bash", {"command": cmd}).cats, cmd)

    def test_shared_claude_asks_for_hook_writes_even_with_warnings_off_and_every_time(self):
        h = self.host()
        a = self.agent(h, approvals="native")    # P198: native = no five-danger card (the default asks, test_p198)
        tool, inp = self.WRITES[0]
        self.assertEqual(asyncio.run(a.hook_event(ev("PreToolUse", tool=tool, inp=inp)))["hookSpecificOutput"]
                         ["permissionDecision"], "ask")
        self.assertEqual(asyncio.run(a.hook_event(ev("PreToolUse", inp={"command": "git push origin main"}))), {})
        allow = asyncio.run(a.hook_event(ev("PermissionRequest", tool=tool, inp=inp)))
        self.assertEqual(allow["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertNotIn("credentials:hooks", a.permissions)
        tool2, inp2 = self.WRITES[1]
        self.assertEqual(asyncio.run(a.hook_event(ev("PreToolUse", tool=tool2, inp=inp2)))["hookSpecificOutput"]
                         ["permissionDecision"], "ask", "an allowed hook write never pre-approves the next one")

    def test_codex_guard_asks_for_hook_writes_with_warnings_off(self):
        h = self.host()
        ad = Mock(cfg={"high_risk_warnings": False, "approvals": "native", "dir": str(self.work)}, host=h, kind="codex")
        g = RiskGuard(ad)
        self.assertTrue(asyncio.run(g.check("Bash", {"command": "git push origin main"})))
        h.ask.assert_not_awaited()
        self.assertTrue(asyncio.run(g.check("Bash", {"command": "echo {} > .codex/hooks.json"})))
        self.assertTrue(asyncio.run(g.check("Bash", {"command": "echo {} > .codex/hooks.json"})))
        self.assertEqual(h.ask.await_count, 2)
        self.assertEqual(g.grants, {})

    def test_serve_puts_removed_hooks_back_and_tells_the_phone(self):
        h = self.host()
        a = self.agent(h)
        a.channel = Mock(path=self.root / "shared-claude.sock")
        shared_hook.install(self.work, a.channel.path)
        self.assertEqual(a.ensure_hooks(), [])
        h.agent_notice.assert_not_called()
        dest = self.work / ".claude/settings.local.json"
        doc = json.loads(dest.read_text())
        doc["hooks"].pop("PermissionRequest")
        doc["hooks"]["PreToolUse"] = []
        dest.write_text(json.dumps(doc))
        self.assertEqual(sorted(a.ensure_hooks()), ["PermissionRequest", "PreToolUse"])
        self.assertEqual(shared_hook.missing(self.work, a.channel.path), [])
        h.agent_notice.assert_called_once()
        h.push_notify.assert_called_once()
        # the token file is the hook's too: deleted → written back
        shared_hook.token_path(self.root, os.getpid()).unlink()
        a.ensure_hooks()
        self.assertEqual(shared_hook.token_path(self.root, os.getpid()).read_text(), a.hook_token)

    def test_doctor_reports_and_restores_missing_hooks(self):
        # P174: shared is the owner's opt-in — write it into a private settings file (never the real ~/.config)
        from agentj import preferences
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / ".config")})
        env.start()
        self.addCleanup(env.stop)
        preferences.ensure()
        preferences.path().write_text(preferences.edit(preferences.path().read_text(), "agent.session_mode", "shared"))
        st = State(self.root / "state")
        st.init()
        st.set_agent_config("claude", str(self.work))
        self.assertEqual(st.agent_config()["session_mode"], "shared")
        self.assertIsNone(doctor.check_shared_hooks(st))                    # not attached yet: nothing installed
        chan = shared_hook.channel_path(st.root, "claude")
        chan.write_text("")
        shared_hook.install(self.work, chan)
        self.assertEqual(doctor.check_shared_hooks(st)["status"], doctor.OK)
        (self.work / ".claude/settings.local.json").write_text("{}")
        row = doctor.check_shared_hooks(st)
        self.assertEqual(row["status"], doctor.WARN)
        self.assertEqual(shared_hook.missing(self.work, chan), [])

    def run_hook(self, channel, payload, event, wait="0", timeout=60):
        p = subprocess.Popen([sys.executable, HOOK_PY, str(channel), event], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, env={**os.environ, "AGENTJ_HOOK_RECONNECT_S": wait})
        self.children.append(p)
        out, _ = p.communicate(json.dumps(payload), timeout=timeout)
        return json.loads(out)["hookSpecificOutput"] if out.strip() else None

    def test_unreachable_host_asks_for_hook_writes_and_keeps_routine_native(self):
        chan = shared_hook.channel_path(self.root, "claude")
        out = self.run_hook(chan, ev("PreToolUse", *self.WRITES[0]), "PreToolUse")
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIsNone(self.run_hook(chan, ev("PreToolUse", "Write", {"file_path": "/w/notes.md", "content": "x"}),
                                        "PreToolUse"))
        self.assertIsNone(self.run_hook(shared_hook.channel_path(self.root, "codex"), ev("PreToolUse", *self.WRITES[2]),
                                        "PreToolUse"), "Codex cannot ask from PreToolUse: native rules, as before")

    def test_permission_request_reaches_a_recovering_host_and_misses_are_reported(self):
        h = self.host()
        a = self.agent(h)
        chan = shared_hook.channel_path(self.root, "claude")
        p = subprocess.Popen([sys.executable, HOOK_PY, str(chan), "PermissionRequest"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, env={**os.environ, "AGENTJ_HOOK_RECONNECT_S": "20"})
        self.children.append(p)
        p.stdin.write(json.dumps(ev("PermissionRequest", "Bash", {"command": "git push origin main"})))
        p.stdin.close()

        async def go():
            await asyncio.sleep(1.5)                                        # host restarting meanwhile
            c = shared_hook.Channel(a, chan)
            await c.start()
            try:
                for _ in range(200):
                    if p.poll() is not None:
                        break
                    await asyncio.sleep(.1)
            finally:
                await c.stop()
        asyncio.run(go())
        out = json.loads(p.stdout.read())
        p.stdout.close()
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "allow")
        h.ask.assert_awaited_once()
        self.assertFalse((self.root / shared_hook.SPOOL).exists())
        # never recovered: the native dialog handles it, the phone hears about it later (tool name only)
        self.assertIsNone(self.run_hook(chan, ev("PermissionRequest", "Bash", {"command": "secret-arg"}), "PermissionRequest"))
        spool = (self.root / shared_hook.SPOOL).read_text()
        self.assertIn('"tool": "Bash"', spool)
        self.assertNotIn("secret-arg", spool)
        self.assertEqual((self.root / shared_hook.SPOOL).stat().st_mode & 0o777, 0o600)
        self.assertEqual(a.report_missed(), 1)
        self.assertIn("1 个审批", h.agent_notice.call_args.args[0])
        self.assertFalse((self.root / shared_hook.SPOOL).exists())
        self.assertEqual(a.report_missed(), 0)


# ------------------------------------------------------------------ 2. A4 control socket
CLIENT = r"""
import json, os, pathlib, sys
if os.fork():
    os._exit(0)
os.setsid()                                   # reparented away from the test (= serve) process: an owner's terminal
if os.fork():
    os._exit(0)
sys.path.insert(0, sys.argv[1])
from agentj import names
from agentj.state import State
out = []
for req in json.loads(sys.argv[3]):
    try:
        out.append(names.ctl_call(State(pathlib.Path(sys.argv[2])), req, 10))
    except Exception as e:
        out.append({"exc": type(e).__name__})
tmp = sys.argv[4] + ".tmp"
open(tmp, "w").write(json.dumps({"pid": os.getpid(), "out": out}))
os.replace(tmp, sys.argv[4])
"""


class ControlSocket(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        t = tempfile.TemporaryDirectory(prefix="p139c-", dir="/tmp")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / ".config"),
                                           "AGENTJ_STATE_DIR": str(self.root / "s")})
        self.env.start()
        self.addCleanup(self.env.stop)
        from agentj import controls, gate, preferences as p
        self.p = p
        self.st = State()
        self.st.init()
        p.ensure()
        gate.set_passphrase(self.st, "pass-phrase-1")
        controls.set_estop(self.st, True, "terminal")
        from agentj.serve import Host
        self.host = Host(self.st, read_stdin=False)
        self.server = await asyncio.start_unix_server(self.host.on_ctl, str(self.st.sock_path))
        self.pids = []

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        for pid in self.pids:
            for _ in range(50):
                if not alive(pid):
                    break
                await asyncio.sleep(.1)
        self.assertEqual([p for p in self.pids if alive(p)], [], "no client left behind")

    async def raw(self, *reqs):
        r, w = await asyncio.open_unix_connection(str(self.st.sock_path))
        out = []
        for req in reqs:
            w.write((json.dumps(req) + "\n").encode())
            await w.drain()
            out.append(json.loads(await r.readline()))
        w.close()
        await w.wait_closed()
        return out

    async def outside(self, reqs, extra_env=None):
        """names.ctl_call from a process that does not descend from serve (double fork + setsid), like an owner terminal."""
        res = self.root / "client.json"
        res.unlink(missing_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ctl_auth.AGENT_ENV}
        env.update(extra_env or {})
        p = await asyncio.create_subprocess_exec(sys.executable, "-c", CLIENT, str(Path(ctl_auth.__file__).parents[1]),
                                                 str(self.st.root), json.dumps(reqs), str(res), env=env)
        await p.wait()
        for _ in range(200):
            if res.exists():
                break
            await asyncio.sleep(.05)
        d = json.loads(res.read_text())
        self.pids.append(d["pid"])
        return d["out"]

    def refused(self):
        return [json.loads(x) for x in self.st.log_path.read_text().splitlines() if '"ctl_refused"' in x]

    async def test_bare_same_uid_resume_is_refused(self):
        out, = await self.raw({"cmd": "resume"})
        self.assertEqual(out, {"ok": False, "error": "claim_required"})
        self.assertTrue(self.host.stopped())
        for cmd in ({"cmd": "send", "text": "fake host message"}, {"cmd": "revoke", "device": "x" * 16}):
            self.assertEqual((await self.raw(cmd))[0]["error"], "claim_required")
        self.assertEqual([r["cmd"] for r in self.refused()], ["resume", "send", "revoke"])

    async def test_agent_process_cannot_claim(self):
        self.host.agent = Mock(session={"pid": os.getpid()}, proc=None, native=None)    # this process = the served session
        claim, res = await self.raw({"cmd": "claim"}, {"cmd": "resume", "pass": "pass-phrase-1"})
        self.assertEqual(claim["error"], "claim_refused")
        self.assertEqual(claim["why"], "agent")
        self.assertEqual(res["error"], "claim_required")
        self.assertTrue(self.host.stopped())

    async def test_agent_harness_environment_cannot_claim(self):
        out = await self.outside([{"cmd": "resume", "pass": "pass-phrase-1"}], {"CLAUDECODE": "1"})
        self.assertEqual(out[0]["error"], "claim_required")
        self.assertTrue(self.host.stopped())

    async def test_owner_terminal_resumes_with_passphrase_and_claims_are_single_use(self):
        wrong = await self.outside([{"cmd": "resume", "pass": "wrong-phrase"}])
        self.assertEqual(wrong[0]["error"], "passphrase")
        self.assertTrue(self.host.stopped())
        ok = await self.outside([{"cmd": "resume", "pass": "pass-phrase-1"}])
        self.assertTrue(ok[0]["ok"], ok)
        self.assertFalse(self.host.stopped())
        claim, = await self.raw({"cmd": "claim"})                  # this process is serve itself: may claim
        self.assertTrue(claim["ok"])
        first, again = await self.raw({"cmd": "send", "text": "x", "claim": claim["claim"]},
                                      {"cmd": "send", "text": "x", "claim": claim["claim"]})
        self.assertTrue(first["ok"])
        self.assertEqual(again["error"], "claim_required")

    async def test_config_from_an_agent_ordinary_keys_apply_security_keys_need_the_phone(self):
        p = self.p
        raw = p.edit(p.read()[0], "appearance.theme", "dark")
        out, = await self.raw({"cmd": "config_apply", "raw": raw})          # no claim: an Agent's `agentj config set`
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.host.preferences["appearance"]["theme"], "dark")
        self.host.ask = AsyncMock(return_value={"behavior": "deny"})
        raw2 = p.edit(p.read()[0], "agent.high_risk_warnings", True)
        out, = await self.raw({"cmd": "config_apply", "raw": raw2})
        self.assertEqual(out["code"], "owner_approval")
        await asyncio.sleep(.1)
        self.host.ask.assert_awaited_once()
        self.assertEqual(self.host.ask.await_args.args[1], {"changes": {"agent.high_risk_warnings": True}})
        self.assertFalse(p.get(self.host.preferences, "agent.high_risk_warnings"))   # denied: unchanged
        self.host.ask = AsyncMock(return_value={"behavior": "allow"})
        out, = await self.raw({"cmd": "config_apply", "raw": raw2})
        for _ in range(50):
            if p.get(self.host.preferences, "agent.high_risk_warnings"):
                break
            await asyncio.sleep(.05)
        self.assertTrue(p.get(self.host.preferences, "agent.high_risk_warnings"))   # allowed on the phone: applied

    def test_security_changes(self):
        self.assertEqual(ctl_auth.security_changes({"agent": {"isolation": "fenced"}, "appearance": {"theme": "x"}},
                                                   {"agent": {"isolation": "off"}, "appearance": {"theme": "y"}}),
                         ["agent.isolation"])
        self.assertEqual(ctl_auth.security_changes({"appearance": {"theme": "x"}}, {"appearance": {"theme": "y"}}), [])


if __name__ == "__main__":
    unittest.main()
