"""P553 (FEEDBACK #326): Claude Code's deferred-tool loader ToolSearch (and the MCP resource readers) were not in the policy
table, so every entry denied them as unknown — the shared hook even said 「需要主人确认」 with no card behind it.

- ToolSearch / ListMcpResourcesTool / ReadMcpResourceTool are ① reads: no card, no denial, in the shared session (owner turn
  and sub-agent), the scheduled-task hook (plain and `mode: research`) and the harness level.
- the loaded tool is still judged by its own row (ToolSearch only fetches a schema).
- the P553 inventory: every reviewed native name has a row; unknown names still deny.
- an unknown action's reason names the tool and says it is an Agent J defect (zh / en), never 「需要主人确认」, and leaves an
  approvals.log line (reason unknown_action) and a host.log line.
- registered tools keep their behaviour, except Jarvis's 10-27 calls: TodoWrite / update_plan / request_user_input (and
  OpenCode todowrite / question) are ① like TaskCreate; SendMessage stays ①; RemoteTrigger writes stay ③ by default and, at
  the `harness` level, are not one of the four kinds, so the harness decides.
"""
import _hermetic  # noqa: F401,I001
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from agentj import approvals, confirm_tier, danger, policy_registry as p, shared_risk
from test_p512_policy import P553_UNKNOWN_BEFORE
from test_shared_hooks import ClaudeHooks

LOADERS = ("ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool")
INPUTS = {"ToolSearch": {"query": "select:WebSearch,WebFetch", "max_results": 5},
          "ListMcpResourcesTool": {}, "ReadMcpResourceTool": {"server": "docs", "uri": "docs://readme"}}
OLD_ASK = "需要主人确认"


class FakeSt:
    """Just enough State for approvals.record + st.log."""
    def __init__(self, root):
        self.root, self.logs = Path(root), []
        self.approvals_path = self.root / "approvals.log"

    def config(self):
        return {"channel": "fixture-channel"}

    def append_private(self, path, line):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log(self, ev, **kw):
        self.logs.append((ev, kw))


class Registry(unittest.TestCase):
    def test_loaders_are_first_tier_reads(self):
        for tool in LOADERS:
            with self.subTest(tool=tool):
                self.assertEqual(p.tool_action(tool), "file.read")
                self.assertEqual(p.main_requirement(tool, INPUTS[tool]), "allow")
                self.assertIsNone(confirm_tier.harness_keeps(tool, INPUTS[tool]))
                self.assertTrue(danger.readonly(tool, INPUTS[tool])[0])
                self.assertEqual(confirm_tier.agent_requirement(tool, INPUTS[tool]), confirm_tier.NONE)

    def test_inventory_has_a_row_for_every_reviewed_name(self):
        for tool in P553_UNKNOWN_BEFORE:
            with self.subTest(tool=tool):
                self.assertIsNotNone(p.tool_action(tool, canonical=False))
                self.assertNotEqual(p.main_requirement(tool, {}), "deny")
        self.assertEqual(p.decision("main", "agent.session"), "allow")
        self.assertEqual(p.decision("channel", "agent.session"), "deny")      # an owner channel keeps the old native denial
        self.assertEqual(p.decision("support", "agent.session"), "deny")

    def test_loading_a_schema_does_not_lower_the_loaded_tool(self):
        self.assertEqual(p.main_requirement("ToolSearch", {"query": "select:RemoteTrigger"}), "allow")
        self.assertEqual(p.main_requirement("RemoteTrigger", {"action": "create", "body": {}}), "uv")
        self.assertEqual(p.main_requirement("RemoteTrigger", {"action": "list"}), "allow")
        self.assertEqual(p.main_requirement("DesignSync", {"method": "write_files"}), "hold")
        self.assertEqual(p.main_requirement("DesignSync", {"method": "list_files"}), "allow")
        self.assertEqual(p.main_requirement("Monitor", {"command": "tail -f app.log", "description": "d"}), "allow")
        self.assertEqual(p.main_requirement("Monitor", {"command": "sudo journalctl -f", "description": "d"}), "uv")
        self.assertEqual(p.main_requirement("Monitor", {"ws": {"url": "wss://example.com/s"}, "description": "d"}), "allow")
        self.assertEqual(p.main_requirement("ExitWorktree", {"action": "keep"}), "allow")
        self.assertEqual(p.main_requirement("ExitWorktree", {"action": "remove", "discard_changes": True}), "uv")
        self.assertEqual(confirm_tier.harness_keeps("ExitWorktree", {"action": "remove", "discard_changes": True}), "delete")
        self.assertEqual(p.main_requirement("NotebookRead", {"notebook_path": "~/.aws/credentials"}), "uv")
        self.assertEqual(p.main_requirement("PowerShell", {"command": "dir"}), "uv")

    def test_registered_rows_unchanged(self):
        for tool, want in (("Read", "allow"), ("Write", "allow"), ("Delete", "uv"), ("WebSearch", "allow"),
                           ("WebFetch", "allow"), ("Agent", "allow"), ("AskUserQuestion", "hold"),
                           ("mcp__fs__delete_file", "uv"), ("mcp__unknown", "deny"), ("Invented", "deny")):
            with self.subTest(tool=tool):
                self.assertEqual(p.main_requirement(tool, {"file_path": "fixture.txt"}), want)

    def test_unknown_text(self):
        zh, en = p.unknown_text("Foo\nBar", "zh"), p.unknown_text("Foo", "en-US")
        self.assertEqual(zh, "Agent J：未知动作，已拒绝：Foo Bar（这是 Agent J 的缺陷，请反馈）")
        self.assertEqual(en, "Agent J: unknown action, denied: Foo (this is an Agent J defect, please report it)")
        self.assertIn(" / ", p.unknown_text("Foo"))
        self.assertLessEqual(len(p.unknown_text("x" * 500, "en")), 160)

    def test_log_unknown_writes_approvals_and_host_lines(self):
        with tempfile.TemporaryDirectory() as d:
            st = FakeSt(d)
            p.log_unknown(st, "Invented", {"secret": "never written"}, agent="claude")
            line = json.loads(st.approvals_path.read_text())
            self.assertEqual((line["tool"], line["decision"], line["reason"]), ("Invented", "deny", "unknown_action"))
            self.assertNotIn("never written", st.approvals_path.read_text())
            self.assertEqual(st.logs, [("policy_denied", {"tool": "Invented", "reason": "unknown_action", "agent": "claude"})])
            self.assertEqual(approvals.check_record(st, line, {}), "unsigned")
        p.log_unknown(None, "Invented")                                     # no state: nothing, no error


class JarvisCalls(unittest.TestCase):
    """10-27 decisions on the P553 open questions."""
    def test_session_todo_and_question_tools_are_free(self):
        for tool in ("TodoWrite", "update_plan", "request_user_input", "todowrite", "question", "TaskCreate", "TaskUpdate"):
            with self.subTest(tool=tool):
                self.assertEqual(p.tool_action(tool), "agent.session")
                self.assertEqual(p.main_requirement(tool, {"todos": []}), "allow")
                self.assertEqual(p.decision("channel", p.tool_action(tool)), "deny")       # channels / support still refused
        self.assertEqual(p.main_requirement("AskUserQuestion", {}), "hold")                  # the question card itself unchanged

    def test_send_message_stays_free(self):
        self.assertEqual(p.tool_action("SendMessage"), "agent.dispatch")
        self.assertEqual(p.main_requirement("SendMessage", {"to": "peer", "message": "hi"}), "allow")

    def test_remote_trigger_default_uv_harness_follows_the_harness(self):
        write = {"action": "create", "body": {"prompt": "x"}}
        self.assertEqual(p.main_requirement("RemoteTrigger", write), "uv")
        self.assertIsNone(confirm_tier.harness_keeps("RemoteTrigger", write))
        self.assertIsNone(HookProcess.run_hook(self, [danger.encode_extra([]), "policy-only", "harness"], "RemoteTrigger", write))
        out = HookProcess.run_hook(self, [danger.encode_extra([])], "RemoteTrigger", write)
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIsNone(HookProcess.run_hook(self, [danger.encode_extra([])], "TodoWrite", {"todos": []}))


class HookProcess(unittest.TestCase):
    """danger.hook_main: the scheduled-task / independent Claude Code hook, sub-agent calls included (same hook)."""
    def run_hook(self, argv, tool, inp, **extra):
        out = io.StringIO()
        ev = {"tool_name": tool, "tool_input": inp, **extra}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(ev))), contextlib.redirect_stdout(out):
            self.assertEqual(danger.hook_main(argv), 0)
        return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None

    def test_loaders_pass_every_hook_variant(self):
        x = danger.encode_extra([])
        for argv in ([x], [x, "research"], [x, "policy-only"], [x, "policy-only", "harness"], [x, "harness"]):
            for tool in LOADERS:
                for extra in ({}, {"agent_id": "sub-1", "agent_type": "general-purpose"}):
                    with self.subTest(argv=argv, tool=tool, sub=bool(extra)):
                        self.assertIsNone(self.run_hook(argv, tool, INPUTS[tool], **extra))

    def test_unknown_denies_with_the_new_reason(self):
        x = danger.encode_extra([])
        for argv in ([x], [x, "harness"], [x, "research"]):
            with self.subTest(argv=argv):
                out = self.run_hook(argv, "mcp__unknown_op" if "research" not in argv else "Invented", {})
                self.assertEqual(out["permissionDecision"], "deny")
                if "research" not in argv:
                    self.assertIn("未知动作，已拒绝：mcp__unknown_op", out["permissionDecisionReason"])
                    self.assertIn("this is an Agent J defect", out["permissionDecisionReason"])
                    self.assertNotIn(OLD_ASK, out["permissionDecisionReason"])

    def test_registered_hold_still_asks(self):
        out = self.run_hook([danger.encode_extra([])], "Bash", {"command": "git push origin main"})
        self.assertEqual(out["permissionDecision"], "ask")


class SharedSession(unittest.IsolatedAsyncioTestCase):
    """The shared Claude Code session's own hook: owner turn and sub-agent."""
    async def asyncSetUp(self):
        f = ClaudeHooks()
        await f.asyncSetUp()
        self.f, self.a, self.host = f, f.a, f.host
        self.addCleanup(f.doCleanups)
        self.addCleanup(f.tmp.cleanup)

    async def pre(self, tool, inp, **extra):
        return await self.a.hook_event({"session_id": "owner", "hook_event_name": "PreToolUse", "tool_name": tool,
                                        "tool_input": inp, **extra})

    async def test_owner_turn_and_subagent_loaders_allowed(self):
        for tool in LOADERS:
            for extra in ({}, {"agent_id": "sub-1", "agent_type": "general-purpose"}):
                with self.subTest(tool=tool, sub=bool(extra)):
                    out = await self.pre(tool, INPUTS[tool], **extra)
                    self.assertNotIn(out.get("hookSpecificOutput", {}).get("permissionDecision"), ("deny", "ask"))
        self.host.ask.assert_not_awaited()

    async def test_unknown_is_denied_named_and_logged(self):
        for lang, frag in (("zh", "未知动作，已拒绝：Invented"), ("en-US", "unknown action, denied: Invented")):
            self.host.lang = lang
            self.host.st.log.reset_mock()
            out = (await self.pre("Invented", {"x": 1}))["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "deny")
            self.assertIn(frag, out["permissionDecisionReason"])
            self.assertNotIn(OLD_ASK, out["permissionDecisionReason"])
            self.assertNotIn("owner confirmation required", out["permissionDecisionReason"])
            self.host.st.log.assert_any_call("policy_denied", tool="Invented", reason="unknown_action", agent="claude")
        self.host.ask.assert_not_awaited()

    async def test_registered_ask_keeps_old_text(self):
        self.host.lang = "zh"
        out = (await self.pre("Write", {"file_path": ".env"}))["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn(OLD_ASK, out["permissionDecisionReason"])


class CodexOpenCodeShared(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_reason_names_the_tool(self):
        with tempfile.TemporaryDirectory() as d:
            st = FakeSt(d)
            host = types.SimpleNamespace(ask=mock.AsyncMock(), stopped=lambda: False, emit=lambda *a, **k: None, st=st, lang="zh")
            guard = shared_risk.RiskGuard(types.SimpleNamespace(host=host, kind="codex", cfg={"shared_session_id": "f"}))
            self.assertFalse(await guard.check("mcp__unknown_op", {}))
            self.assertTrue(guard.reason().startswith(p.unknown_text("mcp__unknown_op", "zh")))
            self.assertIn("不是主人拒绝的", guard.reason())                    # P539: why and what next stay
            self.assertTrue(await guard.check("tool_search", {"query": "x"}))
            self.assertEqual(json.loads(st.approvals_path.read_text())["reason"], "unknown_action")
            host.ask.assert_not_awaited()


class IndependentClaudeLog(unittest.TestCase):
    def test_stream_tool_use_denied_by_the_hook_is_logged(self):
        from agentj.agent import ClaudeAgent
        with tempfile.TemporaryDirectory() as d:
            st = FakeSt(d)
            cl = types.SimpleNamespace(host=types.SimpleNamespace(st=st), kind="claude", cfg={"dir": d})
            ClaudeAgent._note_tools(cl, [{"type": "tool_use", "name": "ToolSearch", "input": INPUTS["ToolSearch"]},
                                         {"type": "tool_use", "name": "Invented", "input": {}}], "parent-1")
            self.assertEqual([kw["tool"] for ev, kw in st.logs if ev == "policy_denied"], ["Invented"])


if __name__ == "__main__":
    unittest.main()
