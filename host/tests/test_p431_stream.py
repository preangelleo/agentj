"""P431 C1: streamed OpenAI-compatible Chat and Anthropic Messages for the native Harness — tool arguments joined from
deltas, reasoning / thinking replay bound to the producing model, usage, truncated or foreign output never executed, the key
only in its header, a real loopback SSE stream read as it arrives (no redirect), and the Loop → Gate path unchanged."""
import _hermetic  # noqa: F401
import asyncio
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agentj.core import anthropic, llm, presets, tools
from agentj.core.loop import Loop
from agentj.core.pipeline import Gate
from agentj.core.session import Session

SENTINEL = "P431" + "-synthetic-value-" * 3


def sse_bytes(events, done=False):
    out = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return (out + ("data: [DONE]\n\n" if done else "")).encode()


def chunk(delta=None, finish=None, usage=None):
    d = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        d["usage"] = usage
    return d


def anth_events(blocks, stop="end_turn", usage=None):
    """Messages stream events for content blocks (text / thinking / tool_use with its input split in two deltas)."""
    u = usage or {"input_tokens": 7, "cache_read_input_tokens": 3, "output_tokens": 1}
    ev = [{"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "content": [],
                                                 "usage": u}}, {"type": "ping"}]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            ev += [{"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"][:2]}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"][2:]}}]
        elif b["type"] == "thinking":
            ev += [{"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": ""}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "thinking_delta", "thinking": b["thinking"]}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": b["signature"]}}]
        elif b["type"] == "tool_use":
            j = json.dumps(b["input"])
            ev += [{"type": "content_block_start", "index": i,
                    "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": j[:3]}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": j[3:]}}]
        else:
            ev += [{"type": "content_block_start", "index": i, "content_block": b}]
        ev.append({"type": "content_block_stop", "index": i})
    ev += [{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 9}}, {"type": "message_stop"}]
    return ev


def anth_sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


class ChatStream(unittest.TestCase):
    def setUp(self):
        self.p = presets.resolve("ark", environ={})
        self.env = {self.p.key_env: SENTINEL}

    def test_tool_arguments_joined_by_index_with_reasoning_and_usage(self):
        seen = []

        def transport(url, headers, body, timeout):
            seen.append((url, headers, json.loads(body)))
            return 200, sse_bytes([
                chunk({"role": "assistant", "reasoning_content": "thi"}), chunk({"reasoning_content": "nk"}),
                chunk({"content": "Look"}),
                chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "ls", "arguments": '{"pa'}}]}),
                chunk({"tool_calls": [{"index": 1, "id": "b", "function": {"name": "read", "arguments": '{"path":'}}]}),
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'th":"."}'}}]}),
                chunk({"tool_calls": [{"index": 1, "function": {"arguments": '"x"}'}}]}),
                chunk(finish="tool_calls"), {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 4}}], done=True)
        ticks = []
        r = llm.chat(self.p, [{"role": "user", "content": "x"}], [tools.SPECS["ls"], tools.SPECS["read"]], environ=self.env,
                     transport=transport, on_delta=lambda: ticks.append(1))
        self.assertEqual([(c["id"], c["name"], json.loads(c["arguments"])) for c in r["tool_calls"]],
                         [("a", "ls", {"path": "."}), ("b", "read", {"path": "x"})])
        self.assertEqual((r["text"], r["reasoning"], r["usage"]), ("Look", "think", {"input": 11, "output": 4}))
        url, h, body = seen[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual((body["stream"], body["stream_options"]), (True, {"include_usage": True}))
        self.assertEqual(h["Authorization"], "Bearer " + SENTINEL)
        self.assertNotIn(SENTINEL, json.dumps(body))
        self.assertGreaterEqual(len(ticks), 8, "every streamed event shows liveness")

    def test_truncated_or_length_cut_streams_are_refused(self):
        cases = {"no finish": sse_bytes([chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "ls", "arguments": "{"}}]})]),
                 "length": sse_bytes([chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "ls", "arguments": "{"}}]}),
                                      chunk(finish="length")], done=True),
                 "garbage": b"data: {not json\n\n",
                 "foreign tool": sse_bytes([chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "bash", "arguments": "{}"}}]}),
                                            chunk(finish="tool_calls")], done=True)}
        for name, raw in cases.items():
            with self.subTest(name), self.assertRaises(llm.LLMError) as e:
                llm.chat(self.p, [], [tools.SPECS["ls"]], environ=self.env, transport=lambda *a, raw=raw: (200, raw))
            self.assertEqual(e.exception.kind, "bad_reply")

    def test_error_event_inside_the_stream_is_classified_and_retried(self):
        n = []

        def transport(*a):
            n.append(1)
            if len(n) == 1:
                return 200, sse_bytes([{"error": {"type": "rate_limit_exceeded", "message": "slow"}}])
            return 200, sse_bytes([chunk({"content": "ok"}), chunk(finish="stop")], done=True)
        r = llm.chat(self.p, [], environ=self.env, transport=transport, sleep=lambda s: None)
        self.assertEqual((r["text"], len(n)), ("ok", 2))

    def test_a_server_that_ignores_stream_answers_whole(self):
        whole = json.dumps({"choices": [{"message": {"content": "whole"}, "finish_reason": "stop"}], "usage": {}}).encode()
        self.assertEqual(llm.chat(self.p, [], environ=self.env, transport=lambda *a: (200, whole))["text"], "whole")
        self.assertEqual(llm.chat(self.p, [], environ=self.env, transport=lambda *a: (200, iter([b"\n", whole])))["text"], "whole")

    def test_verified_chat_preset_stays_whole(self):
        self.assertFalse(presets.resolve("agentsrelay", environ={}).stream)
        self.assertTrue(all(presets.resolve(n, environ={}).stream for n in ("ark", "byteplus", "ark-coding-plan")))


class Messages(unittest.TestCase):
    def setUp(self):
        self.p = presets.resolve("anthropic", environ={})
        self.env = {"ANTHROPIC_API_KEY": SENTINEL}

    def test_presets_by_name_unverified_and_known_to_the_service_environment(self):
        from agentj.harness import OPENCODE_KEY_ENV
        for name in ("anthropic", "bailian-token-plan", "bailian-coding-plan"):
            p = presets.resolve(name, environ={})
            self.assertEqual((p.protocol, p.stream), ("anthropic-messages", True))
            self.assertIn(p.key_env, OPENCODE_KEY_ENV, "launchd loads only listed key names")
            self.assertFalse(presets.providers()[name]["verified"])
            self.assertTrue(p.base_url.endswith("/v1"))
        self.assertEqual(presets.resolve(None, environ={"ARK_API_KEY": "x", "ANTHROPIC_API_KEY": "y"}).provider, "ark",
                         "new presets come after the recommended order")
        self.assertEqual(presets.resolve("anthropic", "sub", environ={}).model, "claude-sonnet-5-5")

    def test_request_shape_tool_results_merged_cache_and_key_header(self):
        msgs = [{"role": "system", "content": "rules"}, {"role": "user", "content": "go"},
                {"role": "assistant", "content": "doing", "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "ls", "arguments": '{"path":"."}'}},
                    {"id": "t2", "type": "function", "function": {"name": "read", "arguments": "not json"}}]},
                {"role": "tool", "tool_call_id": "t1", "content": "a b"}, {"role": "tool", "tool_call_id": "t2", "content": ""},
                {"role": "user", "content": "〔追加 #1〕more"}]
        seen = []

        def transport(url, headers, body, timeout):
            seen.append((url, headers, json.loads(body)))
            return 200, anth_sse(anth_events([{"type": "text", "text": "fine"}]))
        r = llm.chat(self.p, msgs, [tools.SPECS["ls"], tools.SPECS["read"]], environ=self.env, transport=transport)
        self.assertEqual(r["text"], "fine")
        self.assertEqual(r["usage"], {"input": 10, "output": 9}, "cache reads count as input")
        url, h, b = seen[0]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual((h["x-api-key"], h["anthropic-version"]), (SENTINEL, "2023-06-01"))
        self.assertNotIn("Authorization", h)
        self.assertNotIn(SENTINEL, json.dumps(b))
        self.assertEqual((b["model"], b["max_tokens"], b["stream"]), ("claude-opus-5-5", 64000, True))
        self.assertEqual(b["system"][0]["text"], "rules")
        self.assertEqual(b["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(b["tools"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(b["tools"][0]["input_schema"], tools.SPECS["ls"]["function"]["parameters"])
        self.assertEqual([m["role"] for m in b["messages"]], ["user", "assistant", "user"])
        a = b["messages"][1]["content"]
        self.assertEqual(a[1], {"type": "tool_use", "id": "t1", "name": "ls", "input": {"path": "."}})
        self.assertEqual(a[2]["input"], {}, "unparsable arguments become an empty object")
        u = b["messages"][2]["content"]
        self.assertEqual([x["type"] for x in u], ["tool_result", "tool_result", "text"], "tool results first, one message")
        self.assertEqual(u[1]["content"], "(no output)")
        gw = presets.resolve("bailian-coding-plan", environ={})
        self.assertNotIn("cache_control", json.dumps(anthropic.request(gw, msgs, None, None, True)))

    def test_stream_thinking_signature_tool_use_and_replay_to_the_same_model_only(self):
        blocks = [{"type": "thinking", "thinking": "plan", "signature": "sig-1"}, {"type": "text", "text": "Listing"},
                  {"type": "tool_use", "id": "tu1", "name": "ls", "input": {"path": "src"}}]
        r = anthropic.parse_stream(llm.sse(anth_sse(anth_events(blocks, "tool_use"))), self.p)
        self.assertEqual(r["tool_calls"], [{"id": "tu1", "name": "ls", "arguments": '{"path": "src"}'}])
        self.assertEqual(r["anthropic_blocks"], blocks)
        m = {"role": "assistant", "content": "Listing", "tool_calls": [{"id": "tu1", "type": "function",
             "function": {"name": "ls", "arguments": r["tool_calls"][0]["arguments"]}}],
             "_anthropic_blocks": r["anthropic_blocks"], "_anthropic_model": self.p.name}
        _, same = anthropic.messages([{"role": "user", "content": "x"}, m], self.p)
        self.assertEqual(same[1]["content"], blocks, "unchanged, signature included")
        _, other = anthropic.messages([{"role": "user", "content": "x"}, m], replace(self.p, model="claude-haiku-5-5"))
        self.assertNotIn("sig-1", json.dumps(other))
        self.assertEqual(other[1]["content"][-1]["type"], "tool_use")
        self.assertNotIn("_anthropic", json.dumps(llm.wire_messages([m], presets.resolve("ark", environ={}))))

    def test_truncated_foreign_or_cut_messages_never_execute(self):
        good = anth_events([{"type": "tool_use", "id": "a", "name": "ls", "input": {}}], "tool_use")
        cases = {"no message_stop": good[:-1], "unfinished block": [e for e in good if e["type"] != "content_block_stop"],
                 "max_tokens tool": anth_events([{"type": "tool_use", "id": "a", "name": "ls", "input": {}}], "max_tokens"),
                 "server tool": anth_events([{"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}}]),
                 "duplicate id": anth_events([{"type": "tool_use", "id": "a", "name": "ls", "input": {}}] * 2, "tool_use"),
                 "not an object": [e if e.get("delta", {}).get("type") != "input_json_delta" else
                                   {**e, "delta": {"type": "input_json_delta", "partial_json": "[]"[:1] if e["delta"]["partial_json"].startswith("{") else "]"}}
                                   for e in good]}
        for name, ev in cases.items():
            with self.subTest(name), self.assertRaises(llm.LLMError) as e:
                llm.chat(self.p, [], [tools.SPECS["ls"]], environ=self.env, transport=lambda *a, ev=ev: (200, anth_sse(ev)))
            self.assertEqual(e.exception.kind, "bad_reply")
        with self.assertRaises(llm.LLMError) as e:
            anthropic.parse_stream(llm.sse(anth_sse([{"type": "error", "error": {"type": "overloaded_error"}}])), self.p)
        self.assertEqual(e.exception.kind, "server")

    def test_whole_reply_and_refusal(self):
        data = {"type": "message", "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1}}
        p = replace(self.p, stream=False)
        r = llm.chat(p, [], environ=self.env, transport=lambda *a: (200, json.dumps(data).encode()))
        self.assertEqual((r["text"], r["usage"]), ("hi", {"input": 2, "output": 1}))
        ref = anthropic.parse({**data, "content": [], "stop_reason": "refusal"}, p)
        self.assertEqual((ref["tool_calls"], ref["finish"]), ([], "refusal"))

    def test_refused_thinking_replay_retries_once_without_it(self):
        bodies = []

        def transport(url, headers, body, timeout):
            bodies.append(json.loads(body))
            if len(bodies) == 1:
                return 400, b'{"type":"error","error":{"type":"invalid_request_error","message":"thinking block invalid"}}'
            return 200, anth_sse(anth_events([{"type": "text", "text": "ok"}]))
        m = {"role": "assistant", "content": "a", "_anthropic_model": self.p.name,
             "_anthropic_blocks": [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "a"}]}
        r = llm.chat(self.p, [{"role": "user", "content": "x"}, m, {"role": "user", "content": "y"}], environ=self.env,
                     transport=transport)
        self.assertEqual((r["text"], len(bodies)), ("ok", 2))
        self.assertIn("thinking", json.dumps(bodies[0]))
        self.assertNotIn("thinking", json.dumps(bodies[1]))
        with self.assertRaises(llm.LLMError):          # without replayed thinking a 400 is a plain bad_request
            llm.chat(self.p, [], environ=self.env, transport=lambda *a: (400, b"thinking"))

    def test_loop_gate_and_session_with_messages(self):
        with tempfile.TemporaryDirectory() as d:
            bodies = []
            blocks = [{"type": "thinking", "thinking": "t", "signature": "sig-x"},
                      {"type": "tool_use", "id": "tu1", "name": "bash", "input": {"command": "rm -rf build"}}]

            def transport(url, headers, body, timeout):
                bodies.append(json.loads(body))
                ev = anth_events(blocks, "tool_use") if len(bodies) == 1 else anth_events([{"type": "text", "text": "done"}])
                return 200, anth_sse(ev)
            box = mock.Mock()
            sess = Session(Path(d) / "s.jsonl")
            lp = Loop(preset=self.p, system="rules", specs=[tools.SPECS["bash"]], gate=Gate(d), toolbox=box, session=sess,
                      environ=self.env, call=lambda *a, **k: llm.chat(*a, **k, transport=transport))
            self.assertEqual(asyncio.run(lp.run("clean"))["text"], "done")
            box.call.assert_not_called()                # no approver: C14 refuses bash, the toolbox never runs
            second = bodies[1]["messages"]
            self.assertEqual(second[1]["content"], blocks)
            self.assertEqual(second[2]["content"][0]["type"], "tool_result")
            self.assertIn("Refused:", second[2]["content"][0]["content"])
            self.assertEqual(sess.load()[1]["_anthropic_blocks"], blocks, "the session keeps the blocks for a restart")


class LiveStream(unittest.TestCase):
    def test_real_loopback_stream_is_read_as_it_arrives_and_redirects_are_not_followed(self):
        gate, seen = threading.Event(), []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                seen.append((self.path, self.headers.get("Authorization")))
                if self.path.endswith("/redir/chat/completions"):
                    self.send_response(302)
                    self.send_header("Location", "/sink")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(sse_bytes([chunk({"content": "he"})]))
                self.wfile.flush()
                gate.wait(5)                             # the client must already have the first event
                self.wfile.write(sse_bytes([chunk({"content": "llo"}), chunk(finish="stop")], done=True))
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}/v3"
            p = replace(presets.resolve("ark", environ={}), base_url=base)
            r = llm.chat(p, [], environ={"ARK_API_KEY": SENTINEL}, on_delta=gate.set, timeout=5)
            self.assertEqual(r["text"], "hello")
            with self.assertRaises(llm.LLMError) as e:
                llm.chat(replace(p, base_url=base + "/redir", fallback=()), [], environ={"ARK_API_KEY": SENTINEL},
                         timeout=5)
            self.assertEqual(e.exception.status, 302)
            self.assertNotIn("/sink", [s[0] for s in seen])
            with mock.patch.object(llm, "MAX_REPLY", 8), self.assertRaises(llm.LLMError):
                gate.set()
                llm.chat(p, [], environ={"ARK_API_KEY": SENTINEL}, timeout=5)
        finally:
            gate.set()
            srv.shutdown()
            srv.server_close()
            th.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
