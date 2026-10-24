"""P412 (#175): the phone's 「/」 list mirrors the current harness's own slash commands.

The REAL `menu_get` handler answers for Claude Code (before / after its init, which reports `slash_commands`), Codex and
OpenCode: `kind` names the harness, `native` lists its commands minus Agent J's own names and the skills (同名只显示一次),
each row with its route (#175 定稿, Leo 20:2x): `run` (straight to the harness — Claude Code's prompt commands and the owner's
own custom ones), `agent` (settings / terminal screens: the main Agent gets the owner's wish, never the bare "/x"), `none` (no
web page can — the reason is in the row and on the refusal card). A switch of harness changes the answer. ADR-A71 stands:
`passthrough` is unchanged (skills only); the routing is the host's, and what the Agent gets asks for its normal path.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import pathlib
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import agent, harness_cmds, preferences, serve, slash  # noqa: E402
from agentj.state import State  # noqa: E402

INIT = {"skills": ["review-pr", "deploy"],
        "slash_commands": ["review-pr", "deploy", "clear", "compact", "goal", "init", "security-review", "config",
                           "__remote-workflow", "my-cmd"]}


def _host(d):
    st = State(pathlib.Path(d) / "s")
    st.init(relay="ws://127.0.0.1:1")
    host = serve.Host(st, events="jsonl", read_stdin=False)
    pub = b"\x01" * 32
    did = st.add_device(pub, "手机", None)
    sent = []

    async def send_app(s, obj):
        sent.append(obj)
        return True
    host.send_app = send_app
    s = serve.Session(cid=7, state="ready", device=did, name="手机", pub=pub)
    s.p33 = True
    host.sessions[7] = s
    host.preferences = preferences.defaults()
    return host, s, sent


def _ask(host, s, sent, kind, init, lang="zh", work="."):
    host.lang = lang
    host.agent_cfg = {"kind": kind, "dir": str(work), "session_mode": "independent"}
    host.agent = types.SimpleNamespace(kind=kind, init=init)
    sent.clear()
    asyncio.run(host._app(s, {"t": "menu_get", "r": "r1"}))
    (m,) = [x for x in sent if x.get("t") == "menu"]
    return m


class MenuAnswer(unittest.TestCase):
    def test_three_harnesses(self):
        with tempfile.TemporaryDirectory() as d:
            host, s, sent = _host(d)
            seen = {}
            for kind, init in (("claude", {}), ("claude", INIT), ("codex", {}), ("opencode", {})):
                for lang in ("zh", "en"):
                    with self.subTest(kind=kind, init=bool(init), lang=lang):
                        m = _ask(host, s, sent, kind, init, lang, d)
                        self.assertEqual(m["kind"], kind)
                        cmds = [x["cmd"] for x in m["native"]]
                        self.assertEqual(len(cmds), len(set(cmds)), "each command once")
                        skills = {x["cmd"] for x in m["skills"]}
                        own = {"/" + c for c in harness_cmds.OWN}
                        self.assertFalse(set(cmds) & (own | skills), "Agent J's own names and skills are not repeated")
                        for x in m["native"]:
                            self.assertIn(x["state"], ("run", "agent", "none"))
                            self.assertEqual(x["state"], harness_cmds.route(kind, x["cmd"][1:], init) or "agent")
                            self.assertRegex(x["cmd"], r"^/[A-Za-z0-9][A-Za-z0-9_:.\-]{0,62}$")
                            if x["state"] == "none":
                                self.assertIn(harness_cmds.none_note(kind, x["cmd"][1:], lang), x["desc"])
                        seen[(kind, bool(init), lang)] = m["native"]
            # Claude Code after init: what the CLI reported (minus own / skills / private names) first, then the table
            after = [x["cmd"] for x in seen[("claude", True, "zh")]]
            self.assertEqual(after[:4], ["/init", "/security-review", "/config", "/my-cmd"])
            self.assertEqual(after.count("/config"), 1)
            self.assertIn("生成本项目的 CLAUDE.md", seen[("claude", True, "zh")][0]["desc"])
            # before init: the built-in table
            before = [x["cmd"] for x in seen[("claude", False, "zh")]]
            self.assertIn("/init", before)
            self.assertIn("/review", before)
            self.assertIn("/approvals", [x["cmd"] for x in seen[("codex", False, "zh")]])
            self.assertIn("/sessions", [x["cmd"] for x in seen[("opencode", False, "zh")]])
            self.assertNotEqual(seen[("codex", False, "en")], seen[("opencode", False, "en")], "a switch changes the list")

    def test_no_agent_yet(self):
        with tempfile.TemporaryDirectory() as d:
            host, s, sent = _host(d)
            host.agent = None
            host.agent_cfg = {"kind": "codex", "dir": d, "session_mode": "independent"}
            sent.clear()
            asyncio.run(host._app(s, {"t": "menu_get", "r": "r1"}))
            (m,) = [x for x in sent if x.get("t") == "menu"]
            self.assertEqual(m["kind"], "codex")
            self.assertTrue(m["native"])


class Tables(unittest.TestCase):
    def test_passthrough_unchanged(self):
        a = agent.ClaudeAgent.__new__(agent.ClaudeAgent)
        a.init = dict(INIT)
        self.assertTrue(a.passthrough("deploy"))              # a skill, as before
        for name in ("init", "config", "my-cmd"):
            self.assertFalse(a.passthrough(name), name)       # ADR-A71: the agent's own gate is the same; serve routes

    def test_routes(self):
        R = harness_cmds.route
        # Claude Code: prompt commands and its reported custom commands run; settings / screens go to the Agent
        for n in ("init", "review", "security-review", "pr-comments"):
            self.assertEqual(R("claude", n, INIT), "run", n)
        self.assertEqual(R("claude", "my-cmd", INIT), "agent", "the owner's own custom command: the Agent runs it")
        self.assertIsNone(R("claude", "my-cmd", {}), "not reported → not a command (refused as before)")
        # P412 security review: built-ins a real Claude Code (2.1.x) reports in init that a default-allow would have run —
        # none of them may ever go straight through
        real = {"skills": [], "slash_commands": ["auto-mode-setup", "design-consent", "design-revoke", "heapdump",
                                                 "workflow-launch-exec", "team-onboarding", "permissions", "login"]}
        for n in real["slash_commands"]:
            self.assertNotEqual(R("claude", n, real), "run", n)
        for n in ("heapdump", "design-consent", "workflow-launch-exec"):
            self.assertEqual(R("claude", n, real), "none", n)
        self.assertEqual({n for n in harness_cmds.RUN["claude"]}, {"init", "review", "security-review", "pr-comments",
                                                                  "release-notes", "recap", "insights"}, "run = a fixed list")
        for n in ("config", "mcp", "effort", "permissions", "hooks", "agents", "fast", "output-style", "resume"):
            self.assertEqual(R("claude", n, INIT), "agent", n)
        for n in ("exit", "login", "logout", "vim", "ide", "terminal-setup", "ultrareview", "bug"):
            self.assertEqual(R("claude", n, INIT), "none", n)
            self.assertTrue(harness_cmds.none_note("claude", n, "zh") and harness_cmds.none_note("claude", n, "en"))
        # P495: dedicated headless APIs run; terminal-only commands are explicitly unavailable.
        self.assertEqual(R("codex", "review"), "run")
        for n in ("init", "approvals", "mcp", "diff"):
            self.assertEqual(R("codex", n), "none", n)
        for n in ("init", "review"):
            self.assertEqual(R("opencode", n), "run", n)
        for n in ("sessions", "undo", "share"):
            self.assertEqual(R("opencode", n), "none", n)
        for kind, n in (("codex", "quit"), ("codex", "mention"), ("codex", "feedback"), ("opencode", "editor"),
                        ("opencode", "themes"), ("opencode", "connect")):
            self.assertEqual(R(kind, n), "none", (kind, n))
        # Agent J's own names, skills and nonsense are never routed here
        for n in ("clear", "compact", "model", "switch-harness", "goal", "reload", "nonsense"):
            self.assertIsNone(R("claude", n, INIT), n)
            self.assertIsNone(R("codex", n), n)
        self.assertIsNone(R("kimi", "config"))

    def test_agent_text(self):
        for lang, head in (("zh", "〔菜单命令 /mcp〕"), ("en", "[Menu command /mcp]")):
            t = harness_cmds.agent_text("/mcp add github", "claude", INIT, lang)
            self.assertTrue(t.startswith(head), t)
            self.assertFalse(t.startswith("/"), "never a bare command the harness would take for its own")
            self.assertIn("add github", t)
            self.assertIn("agentj-config", t)
        self.assertIsNone(harness_cmds.agent_text("/approvals", "codex"))
        for text in ("/init", "/exit", "/clear", "你好 /mcp", "/nonsense"):
            self.assertIsNone(harness_cmds.agent_text(text, "claude", INIT), text)

    def test_serve_routes(self):
        with tempfile.TemporaryDirectory() as d:
            host, _, _ = _host(d)
            host.agent = types.SimpleNamespace(kind="claude", init=INIT, passthrough=lambda n: n in INIT["skills"])
            self.assertEqual(host.native_route("config"), "agent")
            self.assertTrue(host.native_text("/config").startswith("〔菜单命令 /config〕"))
            from agentj import compose
            self.assertTrue(compose.render("/config", [], "zh", None, native=host.native_text).startswith("〔菜单命令"))
            self.assertEqual(compose.render("/init", [], "zh", None, native=host.native_text), "/init")
            # a skill with the name of a delegated built-in stays the skill (serve passes it through first)
            host.agent = types.SimpleNamespace(kind="claude", init={"skills": ["focus"]}, passthrough=lambda n: n == "focus")
            self.assertIsNone(host.native_text("/focus"))
            self.assertTrue(host.native_text("/config").startswith("〔菜单命令"))
            # a shared session already hands every /name to the owner's own session (relay parity): no routing there
            from agentj.shared import SharedClaudeAgent
            host.agent = SharedClaudeAgent.__new__(SharedClaudeAgent)
            self.assertIsNone(host.native_route("config"))
            self.assertIsNone(host.native_text("/config"))

    def test_own_names_cover_whitelist(self):
        self.assertTrue(set(slash.WHITELIST) <= harness_cmds.OWN)
        self.assertTrue(set(slash.AGENT_CMDS) <= harness_cmds.OWN)
        self.assertEqual(harness_cmds.for_menu("kimi", None, [], "zh"), [])
        self.assertTrue(harness_cmds.for_menu("opencode2", None, [], "zh"))


if __name__ == "__main__":
    unittest.main()
