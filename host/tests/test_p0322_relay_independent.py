"""Independent Relay route uses real CLI/UDS while preserving the native ceiling."""
import _hermetic
import json
import os
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
import test_p0322_relay_native as native
from agentj import busy_probe

class Independent(IsolatedAsyncioTestCase):
    asyncSetUp = native.Journey.asyncSetUp
    close_server = native.Journey.close_server
    configure = native.Journey.configure
    cli = native.Journey.cli

    def official(self):
        self.config.write_text('model_provider="openai"\nmodel="official-model"\n')
        self.agent.thread['modelProvider'] = 'openai'
        return self.config.read_bytes()

    async def test_actual_cli_independent_route_never_rewrites_official_config(self):
        before = self.official()
        rc, out, err = await self.cli('codex', '--base-url', 'https://api.agentsrelay.net/v1',
            '--model', 'independent-model', '--sandbox', 'danger-full-access', 'one isolated task')
        self.assertEqual(rc, 0, out + err)
        result = json.loads(out)
        self.assertEqual(result['route'], 'agentsrelay-independent-profile')
        self.assertEqual(result['sandbox'], 'read-only')
        await self.host._busy_native[result['key']]['task']
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.agent.thread['modelProvider'], 'openai')
        self.assertEqual(self.started, [('read-only', True, 'one isolated task')])

    async def test_independent_route_requires_explicit_model_and_exact_https_origin(self):
        self.official()
        for url in ('http://agentsrelay.net/v1', 'https://agentsrelay.net.evil.example/v1',
                    'https://agentsrelay.net/v1?key=synthetic', 'https://user@agentsrelay.net/v1',
                    'https://api.openai.com/v1'):
            rc, out, _ = await self.cli('codex', '--base-url', url, '--model', 'fixture-model', 'work')
            self.assertNotEqual(rc, 0)
            self.assertEqual(json.loads(out)['error'], 'relay_profile_required')
        self.agent.cfg['model'] = 'existing-official-model'
        rc, out, _ = await self.cli('codex', '--base-url', 'https://agentsrelay.net/v1', 'work')
        self.assertNotEqual(rc, 0)
        self.assertEqual(json.loads(out)['error'], 'relay_profile_required')
        self.assertFalse(self.started)

    async def test_explicit_route_does_not_promote_unknown_or_stopped_source(self):
        self.official()
        self.agent.session = {'pid': 1, 'procStart': 'unknown'}
        rc, out, _ = await self.cli('codex', '--base-url', 'https://agentsrelay.net/v1', '--model', 'fixture-model', 'work')
        self.assertNotEqual(rc, 0)
        self.assertEqual(json.loads(out)['error'], 'relay_dispatch_refused')
        self.agent.session = {'pid': os.getpid(), 'procStart': busy_probe.start_id(os.getpid())}
        self.host.stopped = lambda: True
        rc, out, _ = await self.cli('codex', '--base-url', 'https://agentsrelay.net/v1', '--model', 'fixture-model', 'work')
        self.assertNotEqual(rc, 0)
        self.assertFalse(self.started)

    async def test_independent_model_and_prompt_still_use_fresh_secret_scan(self):
        self.official()
        secret = 'relay-independent-private-model-35791'
        with patch.dict(os.environ, {'CUSTOM_SECRET': secret}):
            for model, prompt in ((secret, 'safe task'), ('fixture-model', 'use ' + secret)):
                rc, out, err = await self.cli('codex', '--base-url', 'https://agentsrelay.net/v1', '--model', model, prompt)
                self.assertNotEqual(rc, 0)
                self.assertEqual(json.loads(out)['error'], 'relay_text_refused')
                self.assertNotIn(secret, out + err)
        self.assertFalse(self.started)


class Health(IsolatedAsyncioTestCase):
    asyncSetUp = native.Journey.asyncSetUp
    close_server = native.Journey.close_server
    configure = native.Journey.configure
    cli = native.Journey.cli
    official = Independent.official
    # Reuse fixture lifecycle without rediscovering the prior suite.
    async def _probe(self, status=200, body=None, redirect=False, independent=True, config_preserved=True):
        import http.client
        import http.server
        import threading
        import urllib.request
        from agentj import agentsrelay_health
        if independent:
            before = self.official()
        else:
            self.configure();self.agent.thread['modelProvider'] = 'relay'
            before = self.config.read_bytes()
        requests = []
        response_body = body if body is not None else {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'OK'}]}]}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(handler):
                requests.append((handler.path, dict(handler.headers), json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))))
                handler.send_response(302 if redirect else status)
                if redirect:
                    handler.send_header('Location', 'http://127.0.0.1:' + str(server.server_port) + '/redirect-target')
                handler.end_headers()
                handler.wfile.write(json.dumps(response_body).encode())
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        class LocalConnection(http.client.HTTPConnection):
            def __init__(self, host, timeout=8, **kw):
                self_test.assertEqual(host, 'agentsrelay.net')
                super().__init__('127.0.0.1', server.server_port, timeout=timeout)
        self_test = self
        def local(handler, request):
            return handler.do_open(LocalConnection, request)
        try:
            with patch.dict(os.environ, {'AGENTSRELAY_OPENAI_KEY': 'synthetic-model-only-fixture'}), patch.object(urllib.request.HTTPSHandler, 'https_open', local):
                rc, out, err = await self.cli('check', *(['--base-url', 'https://agentsrelay.net/v1'] if independent else []), '--model', 'fixture-model')
            self.assertNotIn('synthetic-model-only-fixture', out + err)
            self.assertNotIn('output', out)
            if config_preserved:
                self.assertEqual(self.config.read_bytes(), before)
            self.assertFalse(self.started)
            return rc, json.loads(out), requests
        finally:
            await __import__('asyncio').to_thread(server.shutdown)
            server.server_close();thread.join()

    async def test_actual_cli_http_check_has_fixed_minimal_no_tool_body_and_no_execution(self):
        rc, result, requests = await self._probe()
        self.assertEqual(rc, 0)
        self.assertEqual(result, {'ok': True, 'health': 'verified_now'})
        self.assertEqual(len(requests), 1)
        path, headers, body = requests[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-model-only-fixture')
        self.assertEqual(body, {'model': 'fixture-model', 'input': 'Reply OK.', 'max_output_tokens': 8,
                                'tools': [], 'tool_choice': 'none', 'store': False})

    async def test_redirect_never_forwards_key_or_retries(self):
        rc, result, requests = await self._probe(redirect=True)
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['health'], 'provider_refused')
        self.assertEqual(len(requests), 1)

    async def test_error_response_is_closed_metadata_without_provider_body(self):
        rc, result, requests = await self._probe(status=429, body={'error': {'message': 'private-provider-error-never-emit'}})
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['health'], 'provider_refused')
        self.assertEqual(len(requests), 1)

    async def test_non_completed_response_is_not_verified(self):
        rc, result, requests = await self._probe(body={'status': 'in_progress', 'output': [{'type': 'message'}]})
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['health'], 'response_invalid')


    async def test_missing_key_does_not_make_any_http_request(self):
        from agentj import agentsrelay_health
        from agentj.agentsrelay_dispatch import _RelayProfile
        profile = _RelayProfile('agentj_relay_' + 'a' * 32, 'https://agentsrelay.net/v1', 'fixture-model')
        import urllib.request
        with patch('agentj.agent_codex.primary_environment', return_value={}), patch.object(urllib.request, 'build_opener') as opener:
            prepared = await __import__('asyncio').to_thread(agentsrelay_health.prepare, profile)
            result = await __import__('asyncio').to_thread(agentsrelay_health.check, prepared, lambda: True)
        self.assertEqual(result, {'ok': False, 'health': 'key_unavailable'})
        opener.assert_not_called()


    async def test_tool_only_completed_response_is_not_fixed_text_health(self):
        rc, result, requests = await self._probe(body={'status': 'completed', 'output': [{'type': 'function_call'}]})
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['health'], 'response_invalid')

    async def test_key_preparation_wait_does_not_authorize_replaced_subject_or_stop(self):
        import asyncio
        from agentj import agentsrelay_health
        original = agentsrelay_health.prepare
        for change in ('session', 'stop', 'ceiling', 'turn'):
            with self.subTest(change=change):
                self.host.stopped = lambda: False
                self.agent.session = {'pid': os.getpid(), 'procStart': busy_probe.start_id(os.getpid())}
                self.agent.thread['sandbox'] = {'type': 'readOnly'}
                self.agent.cur_send = None
                def prepare(profile):
                    result = original(profile)
                    if change == 'session':
                        self.agent.session = {'pid': 1, 'procStart': 'revoked'}
                    elif change == 'stop':
                        self.host.stopped = lambda: True
                    elif change == 'ceiling':
                        self.agent.thread['approvalPolicy'] = 'untrusted'
                    else:
                        self.agent.cur_send = object()
                    return result
                with patch.object(agentsrelay_health, 'prepare', side_effect=prepare):
                    rc, result, requests = await self._probe()
                self.assertNotEqual(rc, 0)
                self.assertEqual(result['health'], 'source_changed')
                self.assertEqual(requests, [])
                self.agent.thread.pop('approvalPolicy', None)


    async def test_gate_wait_replacement_is_refused_before_any_http(self):
        import asyncio
        from agentj import agentsrelay_dispatch
        gate = asyncio.Semaphore(0)
        agentsrelay_dispatch.runtime(self.host)['gate'] = gate
        async def replace_waiting():
            for _ in range(1000):
                if gate._waiters:
                    self.agent.session = dict(self.agent.session)
                    gate.release()
                    return
                await asyncio.sleep(.001)
            self.fail('healthcheck never entered its capacity wait')
        task = asyncio.create_task(replace_waiting())
        rc, result, requests = await self._probe()
        await task
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['error'], 'relay_dispatch_refused')
        self.assertEqual(requests, [])

    async def test_stop_after_final_scan_refuses_before_http(self):
        from agentj import agentsrelay_dispatch
        original = agentsrelay_dispatch._text
        count = 0
        def scan(*args, **kw):
            nonlocal count
            original(*args, **kw)
            count += 1
            if count == 2:
                self.host.stopped = lambda: True
        with patch.object(agentsrelay_dispatch, '_text', side_effect=scan):
            rc, result, requests = await self._probe()
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['error'], 'relay_dispatch_refused')
        self.assertEqual(requests, [])


    async def test_new_secret_after_key_prepare_refuses_model_disclosure(self):
        from agentj import agentsrelay_health
        original = agentsrelay_health.prepare
        def prepare(profile):
            result = original(profile)
            os.environ['CUSTOM_SECRET'] = 'fixture-model'
            return result
        with patch.object(agentsrelay_health, 'prepare', side_effect=prepare):
            rc, result, requests = await self._probe()
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['health'], 'source_changed')
        self.assertEqual(requests, [])

    async def test_current_route_change_after_key_prepare_never_sends(self):
        from agentj import agentsrelay_health
        original = agentsrelay_health.prepare
        def prepare(profile):
            result = original(profile)
            self.configure('https://api.openai.com/v1')
            return result
        with patch.object(agentsrelay_health, 'prepare', side_effect=prepare):
            rc, result, requests = await self._probe(independent=False, config_preserved=False)
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['health'], 'source_changed')
        self.assertEqual(requests, [])
