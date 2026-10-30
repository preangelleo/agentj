"""P559 追加 4 (support st_DlnB…, 0.25.14a1 macOS Codex shared session): Codex 0.162.1's PreToolUse hook names a namespaced
tool namespace + tool with no separator (webrun, image_genimagegen, collaborationspawn_agent …), so every one was denied as an
unknown action before any card. Probed: reports/qa/p559/codex-hook-names/ (real Codex + real hook + Channel, e2e-codex.json).

- the Codex adapter maps those exact names to the existing actions (web.search, image.generate, agent.dispatch, agent.session,
  file.read): no card, no denial, no unknown_action line.
- exact names only: a prefix / suffix / other spelling is still unknown → denied, the reason names the raw tool.
- only the Codex adapter: the same names from another harness's hook (an OpenCode plugin can name a tool anything) stay unknown.
- a sub-agent's own steps (parent session id) are still checked: `sudo` cards with Face ID.
- the Computer Use script tool still needs Codex's plugin binding; its name alone is denied. P569 integration: since P568
  (#361) its name alone is an unregistered main-window MCP script tool → a Face ID card every time (never computer.control,
  never a 免问 grant, never another layer); the owner's refusal is the denial.
"""
import _hermetic  # noqa: F401,I001
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from agentj import policy_registry as p, shared_risk
from test_p553_toolsearch import FakeSt

QA = Path(__file__).resolve().parents[3] / "reports/qa/p559/codex-hook-names"   # absent from the public export
PROBED = [  # (tool_name, tool_input) as Codex 0.162.1 sent them to the hook — reports/qa/p559/codex-hook-names/
    ('collaborationlist_agents', {}),
    ('collaborationspawn_agent', {'message': 'say hi', 'task_name': 't1'}),
    ('collaborationsend_message', {'target': 't1', 'message': 'x'}),
    ('collaborationinterrupt_agent', {'target': 't1'}),
    ('clockcurr_time', {}),
    ('get_goal', {}),
    ('get_context_remaining', {}),
    ('update_goal', {'status': 'complete'}),
    ('create_goal', {'objective': 'probe'}),
    ('view_image', {'path': '/nonexistent.png'}),
    ('list_mcp_resources', {}),
    ('Bash', {'command': 'true'}),
    ('request_permissions', {'reason': 'probe', 'permissions': {'network': {'enabled': True}}}),
    ('collaborationwait_agent', {'timeout_ms': 20000}),
    ('spawn_agent', {'message': 'say hi'}),
]
# The customer's three names (their inputs as Codex sends them) + what the probe saw from Codex 0.162.1
CUSTOMER = {"webrun": {"search_query": [{"q": "Agent J"}]},
            "image_genimagegen": {"prompt": "change the background to blue", "referenced_image_ids": ["img_1"]},
            "collaborationspawn_agent": {"message": "say hi", "task_name": "t1"}}


class Fixture(unittest.IsolatedAsyncioTestCase):
    def guard(self, d, kind="codex"):
        self.st = FakeSt(d)
        self.host = types.SimpleNamespace(ask=mock.AsyncMock(return_value={"behavior": "deny", "message": "no"}),
                                          stopped=lambda: False, emit=lambda *a, **k: None, st=self.st, lang="en")
        return shared_risk.RiskGuard(types.SimpleNamespace(host=self.host, kind=kind, cfg={"shared_session_id": "s", "dir": d}))

    async def pre(self, guard, tool, inp):
        return await guard.hook_event({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp})

    def denied(self):
        return [kw["tool"] for ev, kw in self.st.logs if ev == "policy_denied"]


class CodexNames(Fixture):
    async def test_customer_names_pass_without_card(self):
        with tempfile.TemporaryDirectory() as d:
            g = self.guard(d)
            for tool, inp in CUSTOMER.items():
                with self.subTest(tool=tool):
                    self.assertEqual(await self.pre(g, tool, inp), {})
            self.host.ask.assert_not_awaited()
            self.assertEqual(self.denied(), [])
            self.assertFalse(self.st.approvals_path.exists())

    async def test_probed_hook_events_pass(self):
        """Replays what Codex 0.162.1 actually sent the hook (stand-in model run)."""
        with tempfile.TemporaryDirectory() as d:
            g = self.guard(d)
            seen = set()
            for tool, inp in PROBED:
                if tool in p.CODEX_HOOK_TOOLS:
                    seen.add(tool)
                    self.assertEqual(await self.pre(g, tool, inp), {}, tool)
            self.assertTrue({"collaborationspawn_agent", "collaborationlist_agents", "clockcurr_time", "get_goal"} <= seen)
            self.host.ask.assert_not_awaited()

    def test_table_maps_to_existing_actions(self):
        self.assertEqual(p.codex_hook_tool("webrun"), "web.search")
        self.assertEqual(p.codex_hook_tool("image_genimagegen"), "image.generate")
        self.assertEqual(p.codex_hook_tool("collaborationspawn_agent"), "agent.dispatch")
        for name, action in p.CODEX_HOOK_TOOLS.items():
            with self.subTest(name=name):
                self.assertIn(action, p.REGISTRY)
                self.assertEqual(p.main_requirement(action, {}), "allow")
                self.assertIsNone(p.tool_action(name))          # never a global name: only through the Codex adapter

    async def test_near_names_still_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            g = self.guard(d)
            for tool in ("web", "run", "web.run", "web__run", "webrunx", "xwebrun", "image_gen", "image_genimagegen2",
                         "collaboration", "collaborationspawn", "collaborationspawn_agent_v2", "collaborationdelete_agent"):
                with self.subTest(tool=tool):
                    out = (await self.pre(g, tool, {}))["hookSpecificOutput"]
                    self.assertEqual(out["permissionDecision"], "deny")
                    self.assertTrue(out["permissionDecisionReason"].startswith(p.unknown_text(tool, "en")))
            self.host.ask.assert_not_awaited()

    async def test_other_harness_keeps_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            g = self.guard(d, kind="opencode")
            for tool, inp in CUSTOMER.items():
                with self.subTest(tool=tool):
                    self.assertEqual((await self.pre(g, tool, inp))["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(self.denied(), list(CUSTOMER))

    async def test_sub_agent_steps_still_checked(self):
        with tempfile.TemporaryDirectory() as d:
            g = self.guard(d)
            self.assertEqual(await self.pre(g, "collaborationspawn_agent", CUSTOMER["collaborationspawn_agent"]), {})
            out = (await self.pre(g, "Bash", {"command": "sudo true"}))["hookSpecificOutput"]   # the sub-agent's own call
            self.assertEqual(out["permissionDecision"], "deny")
            self.assertFalse(self.host.ask.await_args.kwargs["require_passkey"])   # P573: sudo is one button (+ password)

    async def test_computer_use_script_needs_binding(self):
        with tempfile.TemporaryDirectory() as d:
            g = self.guard(d)
            out = (await self.pre(g, "mcp__cua_repl__js", {"code": "click()"}))["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "deny")
            # P569 integration: P568 #361 (REPORT-568:18 「取密钥/脚本执行强制UV」) grades an unregistered main-window MCP
            # script tool by effect instead of an unknown-action denial — the name alone still gets no plugin authority:
            # not computer.control, no 免问 scope, no other layer; only a Face ID card the owner refused here.
            self.assertEqual(self.denied(), [])
            self.host.ask.assert_awaited_once()
            self.assertFalse(self.host.ask.await_args.kwargs["require_passkey"])   # P573: a button card, still asked
            self.assertFalse(self.host.ask.await_args.kwargs["batch"])
            self.assertIsNone(p.bound_action("mcp__cua_repl__js", None))
            self.assertIsNone(p.tool_action("mcp__cua_repl__js"))
            self.assertFalse(p.computer_use_grant_op("mcp__cua_repl__js"))
            self.assertEqual(p.main_requirement("mcp__cua_repl__js_reset", {}), "uv")
            self.assertIn(p.channel_tool_decision("mcp__cua_repl__js"), ("deny", "handoff"))

    @unittest.skipUnless((QA / "e2e-codex.json").exists(), "QA evidence is not part of the public export")
    def test_real_codex_e2e_evidence(self):
        fixed = json.loads((QA / "e2e-codex.json").read_text())
        self.assertTrue(fixed["turn_completed"])
        self.assertEqual({t for t, dec, _ in fixed["hooks"] if dec == "pass"},
                         {"collaborationlist_agents", "collaborationspawn_agent", "collaborationwait_agent", "clockcurr_time",
                          "get_goal"})
        self.assertEqual(fixed["asks"], [["Bash", '{"command": "sudo true"}', True]])
        before = json.loads((QA / "e2e-opencode.json").read_text())
        self.assertIn("collaborationspawn_agent", [t for _, t, r in before["logs"] if r == "unknown_action"])


if __name__ == "__main__":
    unittest.main()
