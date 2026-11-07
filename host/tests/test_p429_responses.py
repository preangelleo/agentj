"""P429 C9: stateless Responses codec, local tool loop and private-key boundary."""
import _hermetic  # noqa: F401
import asyncio
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agentj.core import llm, presets, tools
from agentj.core.loop import Loop
from agentj.core.pipeline import Gate
from agentj.core.session import Session

SENTINEL = 'P429' + '-synthetic-value-' * 3


def response(output, **extra):
    return {'status': 'completed', 'output': output, 'usage': {'input_tokens': 12, 'output_tokens': 3}, **extra}


def message(text):
    return {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}


def call(cid='c1', name='ls', args='{}'):
    return {'type': 'function_call', 'call_id': cid, 'name': name, 'arguments': args}


class Responses(unittest.TestCase):
    def setUp(self):
        self.p = presets.resolve('agentsrelay-responses', environ={})
        self.env = {self.p.key_env: SENTINEL}

    def invoke(self, output, **extra):
        seen = []

        def transport(url, headers, body, timeout):
            seen.append((url, headers, json.loads(body)))
            return 200, json.dumps(response(output, **extra)).encode()

        r = llm.chat(self.p, [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'list'}],
                     [tools.SPECS['ls']], environ=self.env, transport=transport)
        return r, seen

    def test_request_keys_tools_usage_and_explicit_presets(self):
        r, seen = self.invoke([message('hello'), call()])
        url, headers, body = seen[0]
        self.assertTrue(url.endswith('/responses'))
        self.assertEqual(headers['Authorization'], 'Bearer ' + SENTINEL)
        self.assertNotIn(SENTINEL, json.dumps(body))
        self.assertFalse(body['store'])
        self.assertEqual(body['include'], ['reasoning.encrypted_content'])
        self.assertEqual(body['tools'][0]['name'], 'ls')
        self.assertNotIn('function', body['tools'][0])
        self.assertEqual((r['text'], r['usage']), ('hello', {'input': 12, 'output': 3}))
        self.assertEqual(r['tool_calls'][0]['id'], 'c1')
        self.assertEqual(presets.resolve('openai', environ={}).key_env, 'OPENAI_API_KEY')
        self.assertEqual(presets.resolve('agentsrelay', environ={}).protocol, 'openai-chat')

    def test_raw_reasoning_replay_and_session_restart(self):
        reasoning = {'type': 'reasoning', 'id': 'rs_local', 'summary': [], 'encrypted_content': 'opaque-local-fixture'}
        with tempfile.TemporaryDirectory() as d:
            sess = Session(Path(d) / 'session.jsonl')
            bodies = []

            def transport(url, headers, body, timeout):
                bodies.append(json.loads(body))
                out = [reasoning, call()] if len(bodies) == 1 else [message('done')]
                return 200, json.dumps(response(out)).encode()

            class Box:
                def call(self, name, args):
                    return True, 'files'

            def model(*a, **k):
                return llm.chat(*a, **k, transport=transport)

            lp = Loop(preset=self.p, system='rules', specs=[tools.SPECS['ls']], gate=Gate(d), toolbox=Box(),
                      session=sess, environ=self.env, call=model)
            result = asyncio.run(lp.run('list'))
            self.assertEqual(result['text'], 'done')
            self.assertEqual(bodies[1]['input'][2], reasoning)
            self.assertEqual(bodies[1]['input'][-1], {'type': 'function_call_output', 'call_id': 'c1', 'output': 'files'})
            from agentj.core.responses import inputs
            restored = inputs(sess.load(), self.p)
            self.assertIn(reasoning, restored)
            # A different provider/model never receives the old encrypted reasoning blob.
            other = replace(self.p, model='different-model')
            self.assertNotIn('opaque-local-fixture', json.dumps(inputs(sess.load(), other)))
            self.assertNotIn('_response_items', json.dumps(llm.wire_messages(sess.load(), presets.resolve('agentsrelay', environ={}))))

    def test_incomplete_failed_unknown_or_malformed_output_never_executes(self):
        for status in ('incomplete', 'failed', 'in_progress', None):
            with self.subTest(status=status), self.assertRaises(llm.LLMError) as e:
                self.invoke([call()], status=status)
            self.assertEqual(e.exception.kind, 'bad_reply')
        for items in ([call(args='{"unfinished":')], [call(args='[]')], [call(), call()],
                      [{'type': 'web_search_call'}], [{'type': 'function_call'}],
                      [message(123)], [{'type': 'reasoning', 'summary': [123]}]):
            with self.subTest(items=items), self.assertRaises(llm.LLMError):
                self.invoke(items)

    def test_refusal_and_empty_completed_reply(self):
        r, _ = self.invoke([{'type': 'message', 'role': 'assistant', 'content': [{'type': 'refusal', 'refusal': 'cannot'}]}])
        self.assertEqual(r['text'], 'cannot')
        self.assertEqual(r['tool_calls'], [])
        self.assertEqual(self.invoke([])[0]['text'], '')

    def test_error_retry_fallback_and_auth_no_retry(self):
        seen = []

        def transport(url, headers, body, timeout):
            seen.append(json.loads(body)['model'])
            if len(seen) == 1:
                return 429, b'rate limited'
            if seen[-1] == self.p.model:
                return 404, b'model unavailable'
            return 200, json.dumps(response([message('fallback')])).encode()

        r = llm.chat(self.p, [], environ=self.env, transport=transport, sleep=lambda _: None)
        self.assertEqual(r['text'], 'fallback')
        self.assertEqual(len(seen), 3)
        seen.clear()
        with self.assertRaises(llm.LLMError) as e:
            llm.chat(self.p, [], environ=self.env, transport=lambda *a: (seen.append(1) or (401, b'auth')), sleep=lambda _: None)
        self.assertEqual((e.exception.kind, len(seen)), ('auth', 1))

    def test_unknown_protocol_is_rejected_before_transport(self):
        with mock.patch.object(llm, '_transport') as transport, self.assertRaises(llm.LLMError):
            llm.chat(replace(self.p, protocol='unknown'), [], environ=self.env)
        transport.assert_not_called()

    def test_max_output_and_local_metadata_stay_separate(self):
        from agentj.core.responses import request
        body = request(self.p, [{'role': 'user', 'content': 'a', '_private': 'hidden'}], None, 20)
        self.assertEqual(body['max_output_tokens'], 20)
        self.assertNotIn('_private', json.dumps(body))
        self.assertNotIn('previous_response_id', body)

    def test_production_transport_never_follows_redirect_or_reads_unbounded_body(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append(self.path)
                self.send_response(307)
                self.send_header('Location', '/sink')
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, _ = llm._transport(f'http://127.0.0.1:{server.server_port}/origin',
                                      {'Authorization': 'Bearer ' + SENTINEL}, b'{}', 2)
            self.assertEqual(status, 307)
            self.assertEqual(seen, ['/origin'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        with mock.patch.object(llm, 'MAX_REPLY', 8), self.assertRaises(llm.LLMError):
            llm._bounded(io.BytesIO(b'x' * 9))

    def test_no_tools_response_cannot_inject_a_call(self):
        for specs, name in ((None, 'ls'), ([tools.SPECS['ls']], 'bash')):
            with self.subTest(name=name), self.assertRaises(llm.LLMError):
                llm.chat(self.p, [], specs, environ=self.env, transport=lambda *a: (200, json.dumps(response([call(name=name)])).encode()))
        with self.assertRaises(llm.LLMError):
            llm.chat(self.p, [], environ=self.env, transport=lambda *a: (200, ('[' * 2000 + ']' * 2000).encode()))

    def test_responses_tool_still_goes_through_original_approval_gate(self):
        with tempfile.TemporaryDirectory() as d:
            seen = []

            def transport(url, headers, body, timeout):
                body = json.loads(body)
                seen.append(body)
                output = [call(name='bash', args=json.dumps({'command': 'rm -rf build'}))] if len(seen) == 1 else [message('refused')]
                return 200, json.dumps(response(output)).encode()

            box = mock.Mock()
            lp = Loop(preset=self.p, system='rules', specs=[tools.SPECS['bash']], gate=Gate(d), toolbox=box,
                      environ=self.env, call=lambda *a, **k: llm.chat(*a, **k, transport=transport))
            self.assertEqual(asyncio.run(lp.run('test'))['text'], 'refused')
            box.call.assert_not_called()
            self.assertIn('Refused:', seen[1]['input'][-1]['output'])
