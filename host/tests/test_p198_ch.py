"""P198 (P164 findings C and H) + P3's default guard on every shared harness.

C — shared OpenCode on Agent J's own ordinary server, fenced: the v1 guard plugin's hook could not reach the guard channel at
    <state>/shared-opencode.sock (the fence makes the state directory an empty tmpfs and binds back only agentperm/), so
    SharedGuardReady never came and every turn ended 「Agent 出错（TimeoutError）」 before the message was handed over. The
    channel now lives under agentperm/; a guard that still never comes up fails closed with a named reason.
    Real process: reports/qa/p198/shared_opencode_guard.py (real opencode 1.18.34 + bubblewrap).
H — shared Claude: Claude Code 2.1.296 sends no PostToolUse(Failure) after a PermissionRequest hook denies, so the card our
    state set stayed and the status held at `waiting` past the turn's Stop (240 s+ in P164). A deny clears the card.
    Real process: reports/qa/p198/shared_claude_deny_idle.py (real claude 2.1.296).
P3 — `git push` asks on the phone by default for shared Claude, Codex and OpenCode (RiskGuard); `approvals = native` does not.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import fence, shared, shared_hook, shared_state
from agentj.shared_risk import RiskGuard
from agentj.state import State
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk

PUSH = {"command": "git push origin HEAD:main"}


class _Tmp(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="p198ch-", dir=TEST_TMP)
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.work = self.root / "work"
        (self.work / ".claude").mkdir(parents=True)
        self.st = State(self.root / "state")
        self.st.root.mkdir(mode=0o700)


# ------------------------------------------------------------------ C
class OpenCodeGuardChannel(_Tmp):
    def agent(self, **cfg):
        h = Mock()
        h.st = self.st
        h.lang = "zh"
        return shared.SharedOpenCodeAgent(h, {"kind": "opencode", "dir": str(self.work), "session_mode": "shared", **cfg})

    def test_channel_lives_where_the_fence_binds_it_back(self):
        async def go():
            a = self.agent()
            await a.prepare_risk()
            try:
                path = a.risk_channel.path
                self.assertEqual(path.parent, self.st.perm_dir)
                self.assertEqual(shared_hook.family_of(path), "opencode")
                plugin = (self.work / ".opencode/plugins/agentj-shared.js").read_text()
                self.assertIn(json.dumps(str(path)), plugin)
                self.assertNotIn(json.dumps(str(self.st.root / "shared-opencode.sock")), plugin)
            finally:
                await a.risk_channel.stop()
        asyncio.run(go())

    def test_a_guard_that_never_comes_up_fails_closed_with_a_reason(self):
        async def go():
            a = self.agent()
            a.attach_task = None
            a.proc = Mock(returncode=None)
            a.client = Mock()
            a.risk_channel = Mock(path=self.st.perm_dir / "shared-opencode.sock")
            a._creds_check = AsyncMock()
            a.deliver = AsyncMock()
            with patch.object(shared, "CONNECT_WAIT", 0.05):
                await a.turn("你好")
            a.deliver.assert_not_awaited()                     # never handed over without the guard
            a.host.local_notice.assert_called_once()
            self.assertIn("审批守卫", a.host.local_notice.call_args[0][0])
            a.host.turn_failed.assert_called_once()
            self.assertEqual([json.loads(x)["ev"] for x in self.st.log_path.read_text().splitlines()][-1:],
                             ["shared_guard_not_ready"])
        asyncio.run(go())

    def test_owner_server_stays_opt_in(self):
        async def go():
            a = self.agent()
            await a.prepare_risk(owner=True)
            self.assertIsNone(a.risk_channel)
            a = self.agent(high_risk_warnings=True)
            await a.prepare_risk(owner=True)
            try:
                self.assertEqual(a.risk_channel.path.parent, self.st.perm_dir)
            finally:
                await a.risk_channel.stop()
        asyncio.run(go())

    @unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("bwrap"), "needs bubblewrap")
    def test_the_fenced_hook_reaches_the_channel(self):
        """The plugin's exact spawn (python shared_hook.py <channel>) inside the real fence: SharedGuardReady arrives; the old
        path is not even visible there (the regression that made every turn time out)."""
        if fence.problem(self.st, str(self.work)) is not None:
            self.skipTest("bubblewrap cannot start here")

        async def go():
            a = self.agent()
            await a.prepare_risk()
            try:
                chan = str(a.risk_channel.path)
                old = self.st.root / "shared-opencode.sock"
                old.write_text("")                              # outside, the old path exists...
                probe = (f"import os,sys; print(os.path.exists({str(old)!r}), os.path.exists({chan!r}))")
                argv = fence.wrap(self.st, [sys.executable, "-c", probe], str(self.work))
                r = await asyncio.to_thread(subprocess.run, argv, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.stdout.split(), ["False", "True"], r.stderr[-400:])   # ...inside only the new one
                ready = json.dumps({"hook_event_name": "SharedGuardReady", "cwd": str(self.work)})
                argv = fence.wrap(self.st, [sys.executable, shared_hook.__file__, chan], str(self.work))
                r = await asyncio.to_thread(subprocess.run, argv, input=ready, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr[-400:])
                await asyncio.wait_for(a.risk.ready.wait(), 5)
            finally:
                await a.risk_channel.stop()
        asyncio.run(go())


# ------------------------------------------------------------------ H
class ClaudeDenyGoesIdle(_Tmp):
    def host(self, behavior):
        h = Mock()
        h.st = self.st
        h.lang = "zh"
        h.stopped.return_value = False
        h.ask = AsyncMock(return_value={"behavior": behavior})
        return h

    def agent(self, h, **cfg):
        a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": str(self.work), "_workflow_ceo": True, **cfg})
        a.session = {"sessionId": "owner", "pid": os.getpid()}
        return a

    @staticmethod
    def ev(name, **kw):
        return {"hook_event_name": name, "session_id": "owner", **kw}

    def run_turn(self, behavior, tail):
        async def go():
            a = self.agent(self.host(behavior))
            await a.hook_event(self.ev("UserPromptSubmit", prompt="push"))
            pre = await a.hook_event(self.ev("PreToolUse", tool_name="Bash", tool_input=dict(PUSH), tool_use_id="t1"))
            self.assertEqual(pre["hookSpecificOutput"]["permissionDecision"], "ask")   # P3: the default asks
            out = await a.hook_event(self.ev("PermissionRequest", tool_name="Bash", tool_input=dict(PUSH)))
            for e in tail:                                      # what Claude Code 2.1.296 sends after the decision
                await a.hook_event(self.ev(e, last_assistant_message="没有执行。"))
            await asyncio.sleep(shared_state.config.IDLE_DEBOUNCE_S + 0.05)
            return a, out
        with patch.object(shared_state.config, "IDLE_DEBOUNCE_S", 0.05):
            return asyncio.run(go())

    def test_phone_deny_then_stop_goes_idle(self):
        a, out = self.run_turn("deny", ["Stop"])               # measured: no PostToolUse(Failure) after a hook deny
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "deny")
        self.assertIsNone(a.state.card)
        self.assertEqual(a.state.status, "idle")

    def test_deny_alone_is_working_not_waiting(self):
        a, _ = self.run_turn("deny", [])
        self.assertEqual(a.state.status, "working")            # the turn goes on to its reply; the card is answered

    def test_allow_keeps_the_native_signal(self):
        a, out = self.run_turn("allow", [])
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "allow")
        self.assertEqual(a.state.status, "waiting")            # until the tool's PostToolUse, as before


# ------------------------------------------------------------------ P3 default on the other harnesses
class DefaultGuard(unittest.TestCase):
    def guard(self, **cfg):
        h = Mock()
        h.stopped.return_value = False
        h.ask = AsyncMock(return_value={"behavior": "deny"})
        return RiskGuard(Mock(cfg={"dir": "/tmp", **cfg}, host=h, kind="codex")), h

    def test_codex_and_opencode_guard_ask_for_push_by_default_and_deny_blocks(self):
        g, h = self.guard()
        self.assertFalse(asyncio.run(g.check("Bash", PUSH)))
        h.ask.assert_awaited_once()

    def test_hook_event_denies_for_the_served_session(self):
        g, h = self.guard(shared_session_id="s1")
        out = asyncio.run(g.hook_event({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash",
                                        "tool_input": dict(PUSH)}))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_native_cannot_bypass_registry_floor(self):
        g, h = self.guard(approvals="native")
        self.assertFalse(asyncio.run(g.check("Bash", PUSH)))
        h.ask.assert_awaited_once()

    def test_shared_claude_registry_floor_also_applies_under_native(self):
        h = Mock()
        h.stopped.return_value = False
        for cfg, want in (({}, "ask"), ({"approvals": "native"}, "ask")):
            a = shared.SharedClaudeAgent(h, {"kind": "claude", "dir": "/tmp", "_workflow_ceo": True, **cfg})
            a.session = {"sessionId": "owner"}
            out = asyncio.run(a.hook_event({"session_id": "owner", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                            "tool_input": dict(PUSH)}))
            self.assertEqual((out.get("hookSpecificOutput") or {}).get("permissionDecision"), want, cfg)


if __name__ == "__main__":
    unittest.main()
