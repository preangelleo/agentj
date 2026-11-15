"""Owner config → official provider body → WAV; no paid calls or policy scripts."""
import _hermetic  # noqa: F401
import copy
import io
import json
import os
import unittest
import wave
from unittest.mock import patch
from agentj import preferences as p, voice


class OwnerTTS(unittest.TestCase):
    def config(self, **tts):
        return p.validate({'voice': {'tts': dict(mode='cloud', provider='elevenlabs',
                         voice='TestVoice123', **tts)}})

    def speak(self, cfg, text='hello'):
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), \
                patch('agentj.voice._request', return_value=b'\0\0' * 12) as request, \
                patch('agentj.voice.subprocess.run', side_effect=AssertionError('no script')):
            audio = voice.synthesize(text, cfg)
        with wave.open(io.BytesIO(audio), 'rb') as wav:
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.readframes(12), b'\0\0' * 12)
        return request.call_args.args[2]

    def test_default_request_bytes_unchanged(self):
        cfg = self.config()
        self.assertEqual(self.speak(cfg), json.dumps({'text': 'hello', 'model_id': 'eleven_v4',
                         'voice_settings': {'speed': 1.0}}).encode())

    def test_owner_prefix_and_settings_reach_provider_and_count(self):
        cfg = self.config(text_prefix={'elevenlabs': '[calm] '},
                          settings={'elevenlabs': {'stability': .5, 'similarity_boost': .8}})
        original = copy.deepcopy(cfg)
        payload = json.loads(self.speak(cfg))
        self.assertEqual(payload['text'], '[calm] hello')
        self.assertEqual(payload['voice_settings'], {'speed': 1.0, 'stability': .5, 'similarity_boost': .8})
        self.assertEqual(voice.synthesis_chars('hello', cfg), 12)
        self.assertEqual(cfg, original)
        self.assertEqual(json.loads(self.speak(self.config()))['text'], 'hello')

    def test_other_provider_does_not_receive_owner_options(self):
        cfg = self.config(text_prefix={'elevenlabs': '[calm] '}, settings={'elevenlabs': {'stability': .5}})
        cfg['voice']['tts'].update(provider='openai', model='gpt-4o-mini-tts', voice='alloy', key_env='OPENAI_API_KEY')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture-only'}), patch('agentj.voice._request', return_value=b'WAV') as request:
            self.assertEqual(voice.synthesize('hello', cfg), b'WAV')
        self.assertEqual(json.loads(request.call_args.args[2])['input'], 'hello')
        self.assertNotIn('voice_settings', json.loads(request.call_args.args[2]))
        self.assertEqual(voice.synthesis_chars('hello', cfg), 5)

    def test_invalid_options_refused_without_echoing_values(self):
        bad = [dict(text_prefix={'unknown': 'PRIVATE_SENTINEL'}),
               dict(text_prefix={'elevenlabs': 'x'*257}), dict(text_prefix={'elevenlabs': 'bad\ntext'}),
               dict(text_prefix='PRIVATE_SENTINEL'), dict(settings={'elevenlabs': {'key': 'PRIVATE_SENTINEL'}}),
               dict(settings={'elevenlabs': {'stability': True}}), dict(settings={'elevenlabs': {'stability': float('nan')}}),
               dict(settings={'elevenlabs': {'stability': -1}}), dict(settings={'elevenlabs': {'similarity_boost': 1.1}}),
               dict(settings={'elevenlabs': {'speed': .9}}), dict(settings={'elevenlabs': []})]
        for options in bad:
            with self.subTest(options=options), self.assertRaises(p.ConfigError) as raised:
                self.config(**options)
            self.assertNotIn('PRIVATE_SENTINEL', str(raised.exception))

    def test_combined_length_refused_before_provider_request(self):
        cfg = self.config(text_prefix={'elevenlabs': 'x'*256})
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), patch('agentj.voice._request') as request:
            with self.assertRaises(p.ConfigError):
                voice.synthesize('x'*3745, cfg)
        request.assert_not_called()
        self.assertEqual(voice.synthesis_chars('x'*3744, cfg), 4000)

    def test_provider_errors_do_not_echo_owner_text(self):
        cfg = self.config(text_prefix={'elevenlabs': 'PRIVATE_SENTINEL '})
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), \
                patch('agentj.voice._request', side_effect=ValueError('PRIVATE_SENTINEL')):
            with self.assertRaises(p.ConfigError) as raised:
                voice.synthesize('hello', cfg)
        self.assertNotIn('PRIVATE_SENTINEL', str(raised.exception))

    def test_phone_projection_hides_local_options(self):
        from agentj.serve import Host
        from types import SimpleNamespace as NS
        cfg = self.config(text_prefix={'elevenlabs': 'PRIVATE_SENTINEL'}, settings={'elevenlabs': {'stability': .5}})
        host = NS(preferences=cfg, st=NS(agent_name=lambda: 'Agent J'), config_problem=None, host_info=lambda s: {})
        value = Host.preferences_msg(host)['value']['voice']['tts']
        self.assertNotIn('text_prefix', value)
        self.assertNotIn('settings', value)
        self.assertEqual(cfg['voice']['tts']['text_prefix']['elevenlabs'], 'PRIVATE_SENTINEL')


class ProviderJourney(unittest.IsolatedAsyncioTestCase):
    from test_voice_preferences import SpeechTransportTest as _Fixture
    asyncSetUp = _Fixture.asyncSetUp

    async def test_owner_config_http_wav_noise_delivery_and_cache_invalidation(self):
        import asyncio
        import base64
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from agentj import wire
        payloads = []
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                payloads.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'\0\0' * 12)
            def log_message(self, *args):
                pass
        endpoint = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
        thread.start()
        self.host.preferences = p.validate({'voice': {'tts': {'mode': 'cloud', 'provider': 'elevenlabs',
                 'voice': 'TestVoice123', 'spoken': 'off', 'text_prefix': {'elevenlabs': '[calm] '},
                 'settings': {'elevenlabs': {'stability': .5}}}}})
        turn = self.host.hist.add({'k': 'agent'}, reply='hello', end='done')
        try:
            with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), \
                    patch('agentj.voice.TTS_ELEVEN_URL', f'http://127.0.0.1:{endpoint.server_port}/'):
                await self.host.on_tts(self.s, {'r': 'A'*22, 'id': turn['id']})
                await self.host.on_tts(self.s, {'r': 'B'*22, 'id': turn['id']})
                self.assertEqual(len(payloads), 1, 'same config reuses audio')
                self.host.preferences['voice']['tts']['text_prefix']['elevenlabs'] = '[quiet] '
                self.host.preferences['voice']['tts']['settings']['elevenlabs']['stability'] = .7
                await self.host.on_tts(self.s, {'r': 'C'*22, 'id': turn['id']})
            self.assertEqual([x['text'] for x in payloads], ['[calm] hello', '[quiet] hello'])
            self.assertEqual(payloads[-1]['voice_settings']['stability'], .7)
            msgs = [wire.unpad_json(self.recv.decrypt(b'', f[1:]), wire.MAX_JSON_P33) for f in self.frames]
            self.assertEqual(sum(m['t'] == 'tts_end' and m['ok'] for m in msgs), 3)
            chunk = next(m for m in msgs if m['t'] == 'tts_chunk')
            with wave.open(io.BytesIO(base64.b64decode(chunk['data'])), 'rb') as wav:
                self.assertEqual(wav.readframes(12), b'\0\0'*12)
            self.assertFalse(any(b'[calm]' in f or b'hello' in f for f in self.frames))
            usage = [json.loads(line) for path in (self.st.root/'usage').glob('*.jsonl') for line in path.read_text().splitlines()]
            self.assertEqual([x['ch'] for x in usage if x.get('k') == 'voice'], [12, 13])
        finally:
            await asyncio.to_thread(endpoint.shutdown)
            endpoint.server_close()
            thread.join(timeout=2)


class OutboundPrivacy(unittest.IsolatedAsyncioTestCase):
    from test_voice_preferences import SpeechTransportTest as _Fixture
    asyncSetUp = _Fixture.asyncSetUp

    def provider(self, block=False):
        import threading
        from contextlib import contextmanager
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from types import SimpleNamespace
        @contextmanager
        def start():
            probe = SimpleNamespace(payloads=[], started=threading.Event(), release=threading.Event())
            class Endpoint(BaseHTTPRequestHandler):
                def do_POST(self):
                    probe.payloads.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                    probe.started.set()
                    if block:
                        probe.release.wait(5)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'\0\0'*12)
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch('agentj.voice.TTS_ELEVEN_URL', f'http://127.0.0.1:{server.server_port}/'):
                    yield probe
            finally:
                probe.release.set()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
        return start()

    def config(self, prefix):
        return p.validate({'voice': {'tts': {'mode': 'cloud', 'provider': 'elevenlabs',
                          'voice': 'TestVoice123', 'spoken': 'off', 'text_prefix': {'elevenlabs': prefix}}}})

    async def test_known_secret_and_fragment_and_prefix_boundary_never_reach_http(self):
        import asyncio
        from agentj import voice_engines, wire
        secret = 'quietfixturevalueabcdef'
        root = self.st.root.parent
        (root/'.env').write_text('CUSTOM_SECRET='+secret+'\n')
        self.host.agent_cfg = {'dir': str(root)}
        cases = [(secret, 'hello'), (secret[:8], 'hello'), (secret[:4], secret[4:8]+' hello')]
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), self.provider() as probe:
            for i, (prefix, body) in enumerate(cases):
                self.host.preferences = self.config(prefix)
                turn = self.host.hist.add({'k': 'agent'}, reply=body, end='done')
                await self.host.on_tts(self.s, {'r': chr(65+i)*22, 'id': turn['id']})
                with patch.object(self.st, 'agent_config', return_value={'dir': str(root)}):
                    result = await asyncio.to_thread(voice_engines.run_test, self.st, body,
                                                    str(root/f'speech-{i}.wav'), self.config(prefix))
                self.assertFalse(result['ok'])
                self.assertNotIn(secret[:8], json.dumps(result))
                self.assertFalse((root/f'speech-{i}.wav').exists())
            self.assertEqual(probe.payloads, [], 'no complete secret, fragment, or joined boundary reaches HTTP')
        msgs = [wire.unpad_json(self.recv.decrypt(b'', f[1:]), wire.MAX_JSON_P33) for f in self.frames]
        self.assertEqual(len(msgs), 3)
        self.assertTrue(all(m['t'] == 'tts_end' and not m['ok'] for m in msgs))
        self.assertNotIn(secret[:8], json.dumps(msgs))

    async def test_hot_preferences_during_http_keep_payload_cache_and_usage_one_snapshot(self):
        import asyncio
        self.host.preferences = self.config('[calm] ')
        turn = self.host.hist.add({'k': 'agent'}, reply='hello', end='done')
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), self.provider(block=True) as probe:
            task = asyncio.create_task(self.host.on_tts(self.s, {'r': 'A'*22, 'id': turn['id']}))
            try:
                self.assertTrue(await asyncio.to_thread(probe.started.wait, 4))
                self.host.preferences['voice']['tts']['text_prefix']['elevenlabs'] = '[very quiet] '
                probe.release.set()
                await task
                self.host.preferences = self.config('[calm] ')
                await self.host.on_tts(self.s, {'r': 'B'*22, 'id': turn['id']})
                self.assertEqual([x['text'] for x in probe.payloads], ['[calm] hello'])
                self.host.preferences = self.config('[very quiet] ')
                await self.host.on_tts(self.s, {'r': 'C'*22, 'id': turn['id']})
                self.assertEqual([x['text'] for x in probe.payloads], ['[calm] hello', '[very quiet] hello'])
            finally:
                probe.release.set()
                await task
        rows = [json.loads(line) for path in (self.st.root/'usage').glob('*.jsonl') for line in path.read_text().splitlines()]
        self.assertEqual([r['ch'] for r in rows if r.get('k') == 'voice'], [12, 18])

    async def test_hot_preferences_during_rewrite_cannot_store_b_audio_under_a_cache(self):
        import asyncio
        import threading
        from agentj import spoken
        started, release = threading.Event(), threading.Event()
        original = spoken.script
        def pause(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('rewrite fixture timed out')
            return original(*args, **kwargs)
        self.host.preferences = self.config('[calm] ')
        turn = self.host.hist.add({'k': 'agent'}, reply='hello', end='done')
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), self.provider() as probe:
            with patch('agentj.spoken.script', side_effect=pause):
                task = asyncio.create_task(self.host.on_tts(self.s, {'r': 'A'*22, 'id': turn['id']}))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 4))
                    self.host.preferences['voice']['tts']['text_prefix']['elevenlabs'] = '[very quiet] '
                finally:
                    release.set()
                    await task
            self.assertEqual([x['text'] for x in probe.payloads], ['[calm] hello'])
            self.host.preferences = self.config('[calm] ')
            await self.host.on_tts(self.s, {'r': 'B'*22, 'id': turn['id']})
            self.assertEqual(len(probe.payloads), 1, 'returning to A uses only A audio')
            self.host.preferences = self.config('[very quiet] ')
            await self.host.on_tts(self.s, {'r': 'C'*22, 'id': turn['id']})
            self.assertEqual([x['text'] for x in probe.payloads], ['[calm] hello', '[very quiet] hello'])
        rows = [json.loads(line) for path in (self.st.root/'usage').glob('*.jsonl') for line in path.read_text().splitlines()]
        self.assertEqual([r['ch'] for r in rows if r.get('k') == 'voice'], [12, 18])


class CachePrivacy(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = OutboundPrivacy.asyncSetUp
    provider = OutboundPrivacy.provider
    config = OutboundPrivacy.config

    def messages(self):
        from agentj import wire
        return [wire.unpad_json(self.recv.decrypt(b'', f[1:]), wire.MAX_JSON_P33) for f in self.frames]

    async def test_cached_audio_refuses_new_env_file_fragment_environment_and_incomplete_source(self):
        import asyncio
        from agentj import voice_engines
        root = self.st.root.parent
        envfile = root/'.env'
        prefix = 'newlytrustedcontent'
        self.host.agent_cfg = {'dir': str(root)}
        self.host.preferences = self.config(prefix)
        turn = self.host.hist.add({'k': 'agent'}, reply='hello', end='done')
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), self.provider() as probe:
            await self.host.on_tts(self.s, {'r': 'A'*22, 'id': turn['id']})
            self.assertTrue(any(m['t'] == 'tts_chunk' for m in self.messages()))
            self.assertEqual(len(probe.payloads), 1)
            for i, secret in enumerate((prefix, 'zz'+prefix[:8]+'differenttail')):
                envfile.write_text('CUSTOM_SECRET='+secret+'\n')
                self.frames.clear()
                await self.host.on_tts(self.s, {'r': chr(66+i)*22, 'id': turn['id']})
                msgs = self.messages()
                self.assertEqual(len(msgs), 1)
                self.assertEqual((msgs[0]['t'], msgs[0]['ok'], msgs[0]['why']), ('tts_end', False, 'unsafe_text'))
                self.assertNotIn(prefix[:8], json.dumps(msgs))
                self.assertEqual(len(probe.payloads), 1)
            envfile.unlink()
            self.frames.clear()
            with patch.dict(os.environ, {'CUSTOM_SECRET': prefix}):
                await self.host.on_tts(self.s, {'r': 'D'*22, 'id': turn['id']})
            self.assertEqual(self.messages()[0]['why'], 'unsafe_text')
            self.assertEqual(len(probe.payloads), 1)
            envfile.mkdir()  # existing unreadable/non-file source: scan must fail closed
            self.frames.clear()
            try:
                await self.host.on_tts(self.s, {'r': 'E'*22, 'id': turn['id']})
                msgs = self.messages()
                self.assertEqual(len(msgs), 1)
                self.assertEqual((msgs[0]['t'], msgs[0]['ok'], msgs[0]['why']), ('tts_end', False, 'not_ready'))
                with patch.object(self.st, 'agent_config', return_value={'dir': str(root)}):
                    result = await asyncio.to_thread(voice_engines.run_test, self.st, 'hello',
                                                    str(root/'must-not-exist.wav'), self.config(prefix))
                self.assertFalse(result['ok'])
                self.assertFalse((root/'must-not-exist.wav').exists())
                self.assertNotIn(prefix[:8], json.dumps(result))
                self.assertEqual(len(probe.payloads), 1)
            finally:
                envfile.rmdir()
            self.frames.clear()
            await self.host.on_tts(self.s, {'r': 'F'*22, 'id': turn['id']})
            self.assertTrue(any(m['t'] == 'tts_chunk' for m in self.messages()))
            self.assertEqual(len(probe.payloads), 1, 'safe cache still replays without a second paid call')
        rows = [json.loads(line) for path in (self.st.root/'usage').glob('*.jsonl') for line in path.read_text().splitlines()]
        self.assertEqual(len([r for r in rows if r.get('k') == 'voice']), 1)

    async def test_cached_actual_rewritten_body_rechecked_after_secret_rotation(self):
        root = self.st.root.parent
        self.host.agent_cfg = {'dir': str(root)}
        self.host.preferences = self.config('[calm] ')
        turn = self.host.hist.add({'k': 'agent'}, reply='original harmless reply', end='done')
        rewritten = 'rewrittenfixturecontent'
        script = {'text': rewritten, 'spoken': True, 'cached': False, 'lang': 'en', 'model': 'fixture', 'i': 0, 'o': 0}
        with patch.dict(os.environ, {'ELEVENLABS_API_KEY': 'fixture-only'}), self.provider() as probe:
            with patch('agentj.spoken.script', return_value=script):
                await self.host.on_tts(self.s, {'r': 'A'*22, 'id': turn['id']})
            self.assertEqual(probe.payloads[0]['text'], '[calm] '+rewritten)
            self.assertTrue(any(m['t'] == 'tts_chunk' for m in self.messages()))
            (root/'.env').write_text('CUSTOM_SECRET=rotated'+rewritten[:8]+'secretvalue\n')
            self.frames.clear()
            with patch('agentj.spoken.script', side_effect=AssertionError('must use cached exact rewritten text')):
                await self.host.on_tts(self.s, {'r': 'B'*22, 'id': turn['id']})
            msgs = self.messages()
            self.assertEqual(len(msgs), 1)
            self.assertEqual((msgs[0]['t'], msgs[0]['ok'], msgs[0]['why']), ('tts_end', False, 'unsafe_text'))
            self.assertEqual(len(probe.payloads), 1)
