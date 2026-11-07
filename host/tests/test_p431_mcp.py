"""P431 C8: the native Harness's MCP client — the owner's server list (no key in a stdio environment, https or loopback
only), a real stdio server and a real Streamable HTTP server (SSE answer, session id, no redirect, key only in its header),
tool definitions under budget, every call through the gate (card unless the owner allowed it, research, order-wide
refusals), the Loop executing an approved call, and `agentj core mcp add` from inside the fence going through a phone card."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import stat
import sys
import tempfile
import textwrap
import threading
import unittest
from types import SimpleNamespace
from agentj import provenance
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agentj.core import hostside, mcp, presets
from agentj.core.loop import Loop
from agentj.core.pipeline import Gate

SENTINEL = "P431" + "-synthetic-value-" * 3

SERVER = textwrap.dedent('''
    import json, os, sys
    tools = [{"name": "echo", "description": "Echo text " * 5, "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
             {"name": "env names", "description": "List environment names", "inputSchema": {"type": "object"}}]
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m:
            continue
        meth = m.get("method")
        if meth == "initialize":
            r = {"protocolVersion": m["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
        elif meth == "tools/list":
            cur = (m.get("params") or {}).get("cursor")
            r = {"tools": tools[:1], "nextCursor": "p2"} if not cur else {"tools": tools[1:]}
        elif meth == "tools/call":
            a = m["params"]["arguments"]
            if m["params"]["name"] == "echo":
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"}) + "\\n"); sys.stdout.flush()
                json.loads(sys.stdin.readline())
                r = {"content": [{"type": "text", "text": "echo:" + a.get("text", "")}, {"type": "image", "data": "x"}]}
            else:
                r = {"content": [{"type": "text", "text": " ".join(sorted(os.environ)) + " " + " ".join(os.environ.values())}]}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "no"}}) + "\\n"); sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}) + "\\n"); sys.stdout.flush()
''')


def fake_server(d):
    p = Path(d) / "srv.py"
    p.write_text(SERVER)
    return {"type": "stdio", "command": [sys.executable, "-I", str(p)], "env": {"MODE": "test"}, "allow": ["echo"]}


class Config(unittest.TestCase):
    def test_owner_list_is_checked_and_private(self):
        bad = [{"type": "stdio", "command": ["x"], "env": {"GITHUB_TOKEN": "v"}},
               {"type": "stdio", "command": "x y"}, {"type": "http", "url": "http://example.com/mcp"},
               {"type": "http", "url": "https://u:p@example.com/mcp"}, {"type": "http", "url": "https://e.com", "key_env": "lower"},
               {"type": "http", "url": "https://e.com", "extra": 1}, {"type": "ftp"}, "x"]
        for spec in bad:
            with self.subTest(spec=spec):
                self.assertTrue(mcp.problems(spec))
        self.assertEqual(mcp.problems({"type": "http", "url": "http://127.0.0.1:9/mcp", "key_env": "MY_MCP_KEY"}), [])
        with tempfile.TemporaryDirectory() as d:
            mcp.add(d, "files", {"type": "stdio", "command": ["srv"], "allow": []})
            with self.assertRaises(mcp.McpError):
                mcp.add(d, "Bad Name", {"type": "stdio", "command": ["srv"]})
            with self.assertRaises(mcp.McpError):
                mcp.add(d, "k", bad[0])
            self.assertEqual(stat.S_IMODE(mcp.path(d).stat().st_mode), 0o600)
            raw = json.loads(mcp.path(d).read_text())
            raw["servers"]["evil"] = bad[0]
            mcp.path(d).write_text(json.dumps(raw))
            self.assertEqual(list(mcp.load(d)), ["files"], "a hand-edited bad entry is ignored, never half-used")
            self.assertTrue(mcp.remove(d, "files"))
            self.assertFalse(mcp.remove(d, "files"))
            self.assertIn("key MY_KEY", mcp.describe("h", {"type": "http", "url": "https://e.com", "key_env": "MY_KEY"}))


class StdioServer(unittest.TestCase):
    def test_real_stdio_server_tools_pagination_ping_and_no_key_in_its_environment(self):
        with tempfile.TemporaryDirectory() as d:
            spec = fake_server(d)
            hub = mcp.Hub({"fs": spec}, d, fenced=False,
                          environ={"PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": SENTINEL, "HOME": d})
            self.addCleanup(hub.close)
            specs = hub.start()
            names = [s["function"]["name"] for s in specs]
            self.assertEqual(names, ["mcp__fs__echo", "mcp__fs__env_names"], "both pages; names made safe")
            self.assertTrue(specs[0]["function"]["description"].startswith("[MCP fs] "))
            self.assertTrue(hub.allowed("mcp__fs__echo"))
            self.assertFalse(hub.allowed("mcp__fs__env_names"))
            self.assertEqual(hub.call("mcp__fs__echo", {"text": "hi"}), "echo:hi\n[image content omitted]")
            env = hub.call("mcp__fs__env_names", {})
            self.assertNotIn(SENTINEL, env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertIn("MODE", env)

    def test_a_server_that_fails_to_start_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            hub = mcp.Hub({"gone": {"type": "stdio", "command": [str(Path(d) / "missing")]}}, d, fenced=False)
            self.assertEqual(hub.start(), [])
            self.assertIn("gone", hub.failed)

    def test_tool_definitions_stay_under_budget(self):
        class Fake:
            def request(self, method, params=None, timeout=0):
                if method == "tools/list":
                    return {"tools": [{"name": f"t{i}", "description": "d " * 400,
                                       "inputSchema": {"type": "object", "properties": {"x": {"description": "y" * 5000}}}}
                                      for i in range(60)]}
                return {}

            def notify(self, *a):
                pass

            def close(self):
                pass
        hub = mcp.Hub({"big": {"type": "stdio", "command": ["x"]}}, "/", make_stdio=lambda s: Fake())
        specs = hub.start()
        from agentj.core.prompt import tokens
        self.assertLessEqual(sum(tokens(json.dumps(s, ensure_ascii=False)) for s in specs), mcp.SPECS_MAX)
        self.assertGreater(len(specs), 3)
        self.assertEqual(specs[0]["function"]["parameters"], {"type": "object"}, "an oversized schema is replaced")
        self.assertLessEqual(len(specs[0]["function"]["description"]), mcp.DESC_MAX + 20)
        self.assertEqual(mcp.offered(hub, "small"), [], "small-model tier: no MCP (spec §11)")
        self.assertEqual(mcp.offered(None, "large"), [])


class HttpServer(unittest.TestCase):
    def test_streamable_http_sse_session_id_key_header_and_no_redirect(self):
        seen = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if self.path == "/redir":
                    self.send_response(307)
                    self.send_header("Location", "/sink")
                    self.end_headers()
                    return
                if "id" not in body:
                    self.send_response(202)
                    self.end_headers()
                    return
                if body["method"] == "initialize":
                    r = {"protocolVersion": mcp.PROTOCOL, "capabilities": {}}
                elif body["method"] == "tools/list":
                    r = {"tools": [{"name": "search", "inputSchema": {"type": "object"}}]}
                else:
                    r = {"content": [{"type": "text", "text": "found"}], "isError": False}
                msg = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": r})
                data = (f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'method': 'notifications/progress'})}\n\n"
                        f"event: message\ndata: {msg}\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Mcp-Session-Id", "sess-1")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            spec = {"type": "http", "url": base + "/mcp", "key_env": "MY_MCP_KEY"}
            self.assertEqual(mcp.problems(spec), [])
            hub = mcp.Hub({"web": spec}, "/", environ={"MY_MCP_KEY": SENTINEL})
            self.assertEqual([s["function"]["name"] for s in hub.start()], ["mcp__web__search"])
            self.assertEqual(hub.call("mcp__web__search", {"q": "x"}), "found")
            heads = [h for _, h, _ in seen]
            self.assertTrue(all(h.get("authorization") == "Bearer " + SENTINEL for h in heads))
            self.assertNotIn("mcp-session-id", heads[0])
            self.assertEqual(heads[-1].get("mcp-session-id"), "sess-1")
            self.assertEqual(heads[-1].get("mcp-protocol-version"), mcp.PROTOCOL)
            self.assertFalse(any(SENTINEL in json.dumps(b) for _, _, b in seen), "the key is never in a body")
            with self.assertRaises(mcp.McpError):
                mcp.Http({"type": "http", "url": base + "/redir"}).request("initialize", {})
            self.assertNotIn("/sink", [p for p, _, _ in seen])
            with self.assertRaises(mcp.McpError):
                mcp.Http(spec, environ={})
        finally:
            srv.shutdown()
            srv.server_close()
            th.join(timeout=2)


class FakeHub:
    def __init__(self, allow=()):
        self.allow, self.calls = set(allow), []

    def owns(self, name):
        return name.startswith("mcp__fs__")

    def allowed(self, name):
        return name in self.allow

    def call(self, name, args):
        self.calls.append((name, args))
        return "ok " + "ghp_" + "Ab3" * 12


class GateAndLoop(unittest.TestCase):
    def test_gate_cards_allow_list_research_and_order_wide_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            asked = []

            async def deny(tool, inp, who):
                asked.append((tool, inp, who))
                return {"behavior": "deny"}
            hub = FakeHub(allow=["mcp__fs__read"])
            g = Gate(d, deny, mcp_allowed=hub.allowed)
            run = lambda *a: asyncio.run(g.check(*a))
            self.assertEqual(run("mcp__fs__read", {"p": 1}), (True, ""))
            self.assertEqual(asked, [], "the owner's allow list needs no card")
            ok, why = run("mcp__fs__delete", {"p": 1}, "sub-agent「x」#1")
            self.assertFalse(ok)
            self.assertEqual(asked, [("mcp__fs__delete", {"p": 1}, "sub-agent「x」#1")], "model arguments verbatim on the card")
            ok, why = run("mcp__fs__delete", {"p": 1})
            self.assertIn("already refused", why)
            self.assertEqual(len(asked), 1)
            self.assertFalse(asyncio.run(Gate(d, None, mcp_allowed=hub.allowed).check("mcp__fs__delete", {}))[0])
            r = Gate(d, deny, research=True, mcp_allowed=hub.allowed)
            self.assertTrue(asyncio.run(r.check("mcp__fs__read", {}))[0])
            self.assertFalse(asyncio.run(r.check("mcp__fs__write", {}))[0])
            self.assertFalse(asyncio.run(Gate(d, deny).check("mcp__fs__read", {}))[0], "no hub: never allowed by name")

    def test_loop_runs_an_approved_mcp_call_and_redacts_its_output(self):
        with tempfile.TemporaryDirectory() as d:
            n = []

            def call(p, msgs, specs, environ=None):
                n.append(msgs)
                if len(n) == 1:
                    return {"text": "", "tool_calls": [{"id": "m1", "name": "mcp__fs__query", "arguments": '{"q": "a"}'}],
                            "finish": None, "reasoning": None, "model": "f", "usage": {"input": 1, "output": 1}}
                return {"text": "done", "tool_calls": [], "finish": None, "reasoning": None, "model": "f",
                        "usage": {"input": 1, "output": 1}}

            async def allow(tool, inp, who):
                return {"behavior": "allow"}
            hub = FakeHub()
            box = mock.Mock()
            lp = Loop(preset=presets.resolve("agentsrelay", environ={}), system="s", specs=[], toolbox=box,
                      gate=Gate(d, allow, mcp_allowed=hub.allowed), call=call, mcp=hub)
            self.assertEqual(asyncio.run(lp.run("go"))["text"], "done")
            self.assertEqual(hub.calls, [("mcp__fs__query", {"q": "a"})])
            box.call.assert_not_called()
            self.assertNotIn("Ab3Ab3", n[1][-1]["content"])
            self.assertIn("ok", n[1][-1]["content"])


class FencedAdd(unittest.TestCase):
    """`agentj core mcp add` inside the fence: serve shows the owner a card; only an allow writes the list."""

    def host(self, d, answer, stopped=False):
        class St:
            root = Path(d)

            def agent_session(self, kind):
                return "owner-test-session"

            def log(self, *a, **k):
                pass

        class Host:
            st = St()
            agent = SimpleNamespace(kind="core", native_source=provenance.phone(),
                              cur_send=None, phone_turn=False, out_src=provenance.phone(),
                              session={"sessionId": "owner-test-session"})
            asked = []

            def stopped(self):
                return stopped

            async def ask(self, tool, inp, gone=None, **kw):
                self.asked.append((tool, inp, kw))
                return {"behavior": answer}
        return Host()

    def test_add_needs_the_owners_card_list_and_remove_do_not(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENTJ_CORE": "1"}):
            spec = {"type": "stdio", "command": ["npx", "-y", "pkg"]}
            h = self.host(d, "deny")
            r = asyncio.run(hostside.handle(h, {"t": "core_mcp", "op": "add", "name": "fs", "spec": spec}))
            self.assertEqual(r["result"], "denied")
            self.assertEqual(mcp.load(d), {})
            self.assertIn("npx -y pkg", json.dumps(h.asked[0][1], ensure_ascii=False))
            self.assertIs(h.asked[0][2].get("batch"), False, "an MCP server is never covered by a batch grant")
            h = self.host(d, "allow")
            self.assertEqual(asyncio.run(hostside.handle(h, {"t": "core_mcp", "op": "add", "name": "fs", "spec": spec}))["result"], "ok")
            self.assertIn("fs", mcp.load(d))
            r = asyncio.run(hostside.handle(h, {"t": "core_mcp", "op": "list"}))
            self.assertIn("npx -y pkg", r["text"])
            bad = asyncio.run(hostside.handle(h, {"t": "core_mcp", "op": "add", "name": "k",
                                                   "spec": {"type": "stdio", "command": ["x"], "env": {"API_KEY": "v"}}}))
            self.assertEqual(bad["result"], "refused")
            self.assertEqual(len(h.asked), 1, "a refused shape never reaches the phone")
            self.assertEqual(asyncio.run(hostside.handle(h, {"t": "core_mcp", "op": "remove", "name": "fs"}))["result"], "ok")
            self.assertEqual(asyncio.run(hostside.handle(self.host(d, "allow", stopped=True),
                                                         {"t": "core_mcp", "op": "add", "name": "fs", "spec": spec}))["result"],
                             "stopped")
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENTJ_CORE": ""}):
            self.assertEqual(asyncio.run(hostside.handle(self.host(d, "allow"), {"t": "core_mcp", "op": "list"}))["result"], "off")

    def test_missing_and_unknown_identity_never_get_a_card(self):
        from types import SimpleNamespace
        from agentj import provenance
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENTJ_CORE": "1"}):
            for agent in (None, SimpleNamespace(native_source=provenance.normalize(None))):
                h = self.host(d, "allow")
                h.agent = agent
                r = asyncio.run(hostside.handle(h, {"t": "core_mcp", "op": "add", "name": "fs",
                    "spec": {"type": "stdio", "command": ["x"]}}))
                self.assertEqual(r["result"], "group_source")
                self.assertEqual(h.asked, [])
                self.assertEqual(mcp.load(d), {})


class Route(unittest.TestCase):
    def test_elevate_socket_routes_harness_requests_to_hostside(self):
        import test_f17 as f17
        from agentj import elevate
        with tempfile.TemporaryDirectory() as d:
            st = f17._state(d)
            host = f17._host(st, [])
            got = []

            async def fake(h, req, *, peer=None):
                got.append((h is host, req, peer))
                return {"result": "ok", "text": "routed"}

            async def go():
                await host.elevate.start()
                try:
                    return await asyncio.to_thread(elevate.client_request, st, {"t": "core_mcp", "op": "list"}, 10)
                finally:
                    await host.elevate.stop()
            with mock.patch.object(hostside, "handle", fake):
                self.assertEqual(asyncio.run(go())["text"], "routed")
            self.assertEqual(got, [(True, {"t": "core_mcp", "op": "list"}, os.getpid())])

    def test_cli_without_serve_or_terminal_never_adds(self):
        from agentj.core import cli
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENTJ_CORE": "1", "AGENTJ_STATE_DIR": d}), \
                mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            a = mock.Mock(mcp_cmd="add", name="fs", stdio="npx -y pkg", url=None, env=["MODE=1"], key_env=None, allow=None)
            a.name = "fs"
            self.assertEqual(cli.cmd_mcp(a), 3)
            self.assertEqual(mcp.load(d), {})
            self.assertEqual(cli._mcp_spec(a), {"type": "stdio", "command": ["npx", "-y", "pkg"], "env": {"MODE": "1"}, "allow": []})


if __name__ == "__main__":
    unittest.main()
