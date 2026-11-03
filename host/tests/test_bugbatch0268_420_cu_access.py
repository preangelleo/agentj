"""#420: Computer Use asks for app / browser access (getApp / getState on Chrome) as an empty-form elicitation of its own
— not Codex's mcp_tool_call approval. It used to fall into the generic form path and be declined with reason=fields and a
"form unsupported" line that read like the owner had declined. Now:
- during an open call of the trusted bundled plugin (binding from Codex's own item) → a normal computer.control card;
- anything else empty → still declined, but logged with stage / shape and told plainly: not your decline, how to continue."""
import _hermetic  # noqa: F401,I001
import asyncio
import pathlib
import sys
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import codex_forms  # noqa: E402

PLUGIN = "unified-computer-use@openai-bundled"
BIND = {"kind": "codex_plugin", "id": PLUGIN, "server": "cua_repl"}
JS = "mcp__cua_repl__js"
ACCESS = {"threadId": "t", "turnId": "u", "serverName": "cua_repl", "mode": "form",
          "message": "Allow Codex to use Google Chrome?", "requestedSchema": {"type": "object", "properties": {}}}


class CuAccess(unittest.TestCase):
    def codex(self):
        from agentj.agent_codex import CodexAgent
        cx = CodexAgent(None, {"kind": "codex", "dir": "/tmp", "model": None})
        cx.tid = "t"
        answers, notes = [], []
        cx._bg = lambda coro: coro.close()
        cx._answer = lambda rid, result=None, error=None: answers.append((rid, result))
        cx.local_only = notes.append
        cx.touch = lambda: None
        cx._busy = lambda *a: None
        cx.host = mock.Mock(lang="zh")
        return cx, answers, notes

    def started(self, cx, plugin=PLUGIN, tool="js", turn="u"):
        cx._on_note("item/started", {"threadId": "t", "turnId": turn, "item": {
            "type": "mcpToolCall", "id": "p1", "server": "cua_repl", "tool": tool, "status": "inProgress",
            "arguments": {"code": "await getApp('Google Chrome')"}, "pluginId": plugin}})

    def log_events(self, cx, name):
        return [c.kwargs for c in cx.host.st.log.call_args_list if c.args and c.args[0] == name]

    def test_access_during_plugin_call_becomes_a_card(self):
        cx, answers, notes = self.codex()
        self.started(cx)
        with mock.patch.object(cx, "_decide", wraps=lambda *a, **k: asyncio.sleep(0)) as dec:
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 5, "params": ACCESS})
            dec.assert_called_once_with(5, JS, {"access_request": "Allow Codex to use Google Chrome?"},
                                        {"action": "accept", "content": {}}, {"action": "decline"}, False, bind=BIND)
            # a second access request in the same call (getState after getApp) gets its own card, too
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 6, "params": ACCESS})
            self.assertEqual(dec.call_count, 2)
        self.assertEqual(answers, [])
        self.assertEqual(notes, [])

    def test_no_trusted_plugin_call_is_declined_with_a_clear_reason(self):
        for name, setup, params in (
                ("no open call", lambda cx: None, ACCESS),
                ("config server named cua_repl", lambda cx: self.started(cx, plugin=None), ACCESS),
                ("forged marketplace", lambda cx: self.started(cx, plugin="unified-computer-use@evil"), ACCESS),
                ("lifecycle call only", lambda cx: self.started(cx, tool="turn_ended"), ACCESS),
                ("other turn", lambda cx: self.started(cx, turn="old"), ACCESS),
                ("other server", lambda cx: self.started(cx), {**ACCESS, "serverName": "evil"})):
            cx, answers, notes = self.codex()
            setup(cx)
            with mock.patch.object(cx, "_decide") as dec:
                cx._on_request({"method": "mcpServer/elicitation/request", "id": 1, "params": params})
                dec.assert_not_called()
            self.assertEqual(answers, [(1, {"action": "decline"})], name)
            self.assertEqual(len(notes), 1, name)
            self.assertIn("这不是你拒绝的", notes[0], name)
            self.assertIn("在 Agent J 主机上的 Codex 里允许", notes[0], name)
            ev = self.log_events(cx, "codex_elicitation")
            self.assertEqual(ev[0]["reason"], "fields", name)
            self.assertEqual((ev[0]["phase"], ev[0]["count"], ev[0]["mode"]), ("form", 0, "form"), name)
            from agentj.state import State
            self.assertLessEqual(set(ev[0]), State.LOG_FIELDS, "every field reaches host.log")
            self.assertNotIn("Chrome", repr(ev), "the message itself is never logged")

    def test_codex_tool_call_approval_still_goes_its_own_way(self):
        """the P539 path is unchanged: Codex's own mcp_tool_call approval binds to the open item, once."""
        cx, answers, _ = self.codex()
        self.started(cx)
        eli = {**ACCESS, "message": 'Allow the cua_repl MCP server to run tool "js"?',
               "_meta": {"codex_approval_kind": "mcp_tool_call"}}
        with mock.patch.object(cx, "_decide", wraps=lambda *a, **k: asyncio.sleep(0)) as dec:
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 1, "params": eli})
            self.assertEqual(dec.call_args.args[2], {"code": "await getApp('Google Chrome')"})
            cx._on_request({"method": "mcpServer/elicitation/request", "id": 2, "params": eli})   # used → declined
            self.assertEqual(dec.call_count, 1)
        self.assertEqual(answers, [(2, {"action": "decline"})])

    def test_real_forms_keep_the_old_notice(self):
        cx, answers, notes = self.codex()
        props = {k: {"type": "string"} for k in "abcde"}          # 5 fields: still unsupported, the generic line
        cx._on_request({"method": "mcpServer/elicitation/request", "id": 1,
                        "params": {**ACCESS, "requestedSchema": {"type": "object", "properties": props}}})
        self.assertEqual(answers, [(1, {"action": "decline"})])
        self.assertEqual(notes, [codex_forms.notice("zh", "fields")])
        self.assertEqual(self.log_events(cx, "codex_elicitation")[0]["count"], 5)

    def test_notice_texts(self):
        en = codex_forms.notice("en", "empty", server="cua_repl")
        self.assertIn("you did not decline it", en)
        self.assertIn("(from cua_repl)", en)
        self.assertIn("（来自 cua_repl）", codex_forms.notice("zh", "empty", server="cua_repl"))


if __name__ == "__main__":
    unittest.main()
