"""ASR retry, provenance and wire contracts using isolated State and synthetic audio."""
import _hermetic  # noqa: F401
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
import tempfile
import time
import unittest
import wave
from urllib.error import HTTPError, URLError
from unittest.mock import AsyncMock, Mock, patch

from _testtmp import TEST_TMP
from agentj import asr, asr_usage, compose, voice
from agentj.serve import Host
from agentj.state import State
from test_p440_custom_asr import config


class ASRRoute(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.audio = self.root / 'private.wav'
        self.audio.write_bytes(b'private audio')
        self.local = Mock()
        self.local.transcribe.return_value = {'ok': True, 'engine': 'sherpa', 'text': 'private transcript'}

    def transcribe(self, cfg=None, timeout=60):
        return voice.transcribe(self.audio, cfg or config(), timeout, state_dir=self.root, local_asr=self.local)

    def history(self):
        return [json.loads(line) for line in (self.root / 'asr' / 'history.jsonl').read_text().splitlines()]

    def test_connection_refusal_retries_then_reports_remote_recovery(self):
        refused = URLError(ConnectionRefusedError(111, 'private endpoint'))
        with patch.object(voice, '_request', side_effect=[refused, b'{"text":"private transcript"}']) as request:
            result = self.transcribe()
        self.assertEqual(request.call_count, 2)
        self.assertLessEqual(request.call_args.args[4], 2)
        self.assertTrue(result['remote'])
        self.local.transcribe.assert_not_called()
        row = self.history()[-1]
        self.assertEqual((row['engine'], row['fallback'], row['remote_failure'], row['result']),
                         ('remote', False, 'connection_refused', 'ok'))
        self.assertEqual(asr_usage.last_used(self.root)['engine'], 'custom')
        log = (self.root / 'host.log').read_text()
        self.assertNotIn('private', log)
        self.assertEqual(json.loads(log)['remote_failure'], 'connection_refused')

    def test_timeout_retry_is_wall_clock_bounded_and_falls_back(self):
        release = Event()

        def request(*args):
            if args[4] > 2:
                raise TimeoutError('private endpoint')
            release.wait(3)
            return b'{"text":"too late"}'

        try:
            started = time.monotonic()
            with patch.object(voice, '_request', side_effect=request) as transport:
                result = self.transcribe()
            elapsed = time.monotonic() - started
        finally:
            release.set()
        self.assertLess(elapsed, 2.2)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(result['notice'], 'local_fallback')
        row = self.history()[-1]
        self.assertEqual((row['engine'], row['fallback'], row['remote_failure']),
                         ('local', True, 'timeout'))
        self.assertGreater(self.local.transcribe.call_args.kwargs['timeout_s'], 55)

    def test_primary_http_body_stall_is_bounded_before_retry(self):
        stalled, release = Event(), Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                first = not stalled.is_set()
                stalled.set()
                body = b'{"text":"synthetic words"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if first:
                    self.wfile.write(body[:1])
                    release.wait(3)
                    body = body[1:]
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
            thread.start()
            try:
                cfg = config(base_url=f'http://127.0.0.1:{server.server_port}/v1')
                started = time.monotonic()
                with patch.object(asr, 'remote_timeout', return_value=0.3):
                    result = self.transcribe(cfg, timeout=5)
                self.assertTrue(stalled.is_set())
                self.assertTrue(result['remote'])
                self.assertEqual(result['text'], 'synthetic words')
                self.assertLess(time.monotonic() - started, 2)
                row = self.history()[-1]
                self.assertEqual((row['remote_failure'], row['retried']), ('timeout', True))
            finally:
                release.set()
                server.shutdown()
                thread.join()

    def test_provider_rejection_is_not_retried_and_failed_takes_are_retained(self):
        self.local.transcribe.return_value = {'ok': False, 'reason': 'not_installed'}
        cases = {401: 'authentication', 402: 'quota', 429: 'rate_limit', 404: 'endpoint'}
        for code, failure in cases.items():
            error = HTTPError('http://private.endpoint', code, 'private body', {}, io.BytesIO(b'private body'))
            with self.subTest(code=code), patch.object(voice, '_request', side_effect=error) as request:
                result = self.transcribe()
                request.assert_called_once()
                self.assertEqual(result['notice'], 'remote_unavailable')
                row = self.history()[-1]
                self.assertEqual((row['result'], row['fallback'], row['remote_failure'], row['http_status']),
                                 ('not_installed', True, failure, code))
        self.assertEqual(len(self.history()), len(cases))
        self.assertNotIn('private', (self.root / 'asr' / 'history.jsonl').read_text())
        self.assertIsNone(asr_usage.last_used(self.root))

    def test_keyed_service_does_not_replay_an_ambiguous_timeout(self):
        for cfg in (config(key_env='AJ_ASR_FIXTURE'), config(mode='cloud', key_env='AJ_ASR_FIXTURE')):
            with self.subTest(mode=cfg['voice']['asr']['mode']), patch.dict('os.environ', {'AJ_ASR_FIXTURE': 'synthetic'}):
                with patch.object(voice, '_request', side_effect=TimeoutError('private endpoint')) as request:
                    result = self.transcribe(cfg)
                self.assertEqual(result['notice'], 'local_fallback')
                request.assert_called_once()
                self.assertFalse(self.history()[-1]['retried'])

    def test_tight_budget_keeps_half_for_local(self):
        with patch.object(voice, '_request', side_effect=URLError(ConnectionRefusedError(111, 'test'))) as request:
            self.transcribe(timeout=0.1)
        request.assert_called_once()
        self.assertGreater(self.local.transcribe.call_args.kwargs['timeout_s'], 0)


class ASRWire(unittest.IsolatedAsyncioTestCase):
    async def test_short_remote_marker_is_boolean_and_not_present_on_failure_or_local(self):
        choices = [
            ({'ok': True, 'remote': True, 'text': 'fixture', 'engine': 'custom'}, True),
            ({'ok': True, 'text': 'fixture', 'engine': 'sherpa'}, None),
            ({'ok': False, 'remote': True, 'reason': 'timeout'}, None),
            ({'ok': True, 'notice': 'local_fallback', 'text': '   '}, None),
            ({'ok': False, 'reason': 'timeout', 'route_unknown': True}, None),
        ]
        for result, expected in choices:
            host = Host.__new__(Host)
            host.asr_state = lambda: 'ready'
            host.asr_waiting = {'fixture': 0}
            host.uploads = Mock()
            host.st = Mock()
            host._transcribe = AsyncMock(return_value=result)
            host._send_device = AsyncMock()
            with patch('agentj.serve.uploads.wav_ok', return_value=True):
                await host._asr_take('fixture', SimpleNamespace(bid='fixture', secs=1))
            frame = host._send_device.await_args.args[1]
            self.assertIs(frame.get('remote'), expected)
            if not frame['ok']:
                self.assertNotEqual(frame.get('notice'), 'local_fallback')
            if result.get('route_unknown'):
                self.assertNotIn('engine', host.st.log.call_args.kwargs)

    async def test_withdrawing_long_send_stops_pending_remote_retry(self):
        started, release = Event(), Event()
        host = Host.__new__(Host)
        host.asr_lock = None
        host.asr = Mock()
        host.asr_state = lambda: 'ready'
        host.lang, host.native_text = 'en', None
        host.preferences = config()
        host._post = Mock()
        host.hist_update = Mock()
        sends = compose.Sends()
        send = compose.Send('origin', 'fixture', 1)
        send.ready = asyncio.get_running_loop().create_future()
        self.assertTrue(sends.add(send))

        def request(*args):
            started.set()
            release.wait(4)
            raise TimeoutError('fixture failure')

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp)
            host.st = State(root)
            path = root / 'synthetic.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0\0' * 16000)
            files = [{'voice': str(path), 'mime': 'audio/wav', 'path': str(path), 'bytes': path.stat().st_size}]
            with patch.object(voice, '_request', side_effect=request) as transport:
                send.prep = asyncio.create_task(host._prep_say(send, '', files, files, None))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    self.assertEqual(await sends.cancel(send.device, send.sid), ('cancelled', send))
                    with self.assertRaises(asyncio.CancelledError):
                        await send.prep
                finally:
                    release.set()
                log = root / 'asr' / 'history.jsonl'
                async with asyncio.timeout(2):
                    while not log.is_file():
                        await asyncio.sleep(0.01)
                row = json.loads(log.read_text())
                self.assertEqual((row['result'], row['fallback'], row['retried']), ('cancelled', False, False))
                transport.assert_called_once()
                host.asr.transcribe.assert_not_called()
                host._post.assert_not_called()

    async def test_newer_opposite_route_from_another_take_suppresses_queued_long_signal(self):
        remote = {'ok': True, 'text': 'remote fixture', 'remote': True}
        fallback = {'ok': True, 'text': 'local fixture', 'notice': 'local_fallback'}
        for before, after in ((fallback, remote), (remote, fallback)):
            host = Host.__new__(Host)
            host.asr_lock = None
            host.asr = Mock()
            host.preferences = config()
            host.st = SimpleNamespace(root=Path('/fixture'))
            host._send_device = AsyncMock()
            send = compose.Send('origin', 'fixture', 1)
            with patch.object(voice, 'transcribe', side_effect=[before, after]):
                earlier = await host._transcribe('/fixture.wav', 5)
                result = host._asr_result(earlier, 60)
                order = result['_asr_order']
                route = host._asr_route(result)
                self.assertEqual(order, 1)
                await host._say_asr_route(send, route, order)
                host._send_device.assert_awaited_once()

                host._send_device.reset_mock()
                later = await host._transcribe('/short.wav', 5)
                self.assertEqual(later['_asr_order'], 2)
                await host._say_asr_route(send, route, order)
                host._send_device.assert_not_awaited()

    async def test_segment_order_drives_live_route_and_retains_transcript_provenance(self):
        host = Host.__new__(Host)
        host.asr_state = lambda: 'ready'
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            path = Path(tmp) / 'synthetic.webm'
            path.touch()
            host.uploads = SimpleNamespace(root=Path(tmp))

            async def decode(ff, demux, path, dst, timeout, start, secs):
                with wave.open(dst, 'wb') as stream:
                    stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    stream.writeframes(b'\0\0' * (64000 if start == 0 else 24000))
                return 'ok'

            host._ffmpeg = decode
            remote = {'ok': True, 'text': 'remote fixture', 'remote': True, '_asr_order': 11}
            fallback = {'ok': True, 'text': 'local fixture', 'notice': 'local_fallback', '_asr_order': 12}
            for first, second, latest in ((fallback, remote, 'remote'), (remote, fallback, 'local_fallback')):
                host._transcribe = AsyncMock(side_effect=[first, second])
                with patch('agentj.serve.FF_SEG_SECS', 4), patch('agentj.serve.shutil.which', return_value='ffmpeg'):
                    result = await host._transcribe_file({'voice': str(path), 'mime': 'audio/webm'}, 10, 'origin')
                self.assertEqual(host._transcribe.await_count, 2)
                self.assertEqual(result['text'], first['text'] + '\n' + second['text'])
                self.assertEqual((result['notice'], result['asr_route']), ('local_fallback', latest))
                self.assertEqual(result['_asr_order'], second['_asr_order'])

    async def test_long_route_is_ephemeral_and_only_sent_to_its_origin(self):
        host = Host.__new__(Host)
        host.lang, host.native_text = 'en', None
        host._post = Mock()
        host.hist_update = Mock()
        host.st = SimpleNamespace()
        host._send_device = AsyncMock()
        for transcripts, expected in [
            ([{'ok': True, 'text': 'fixture', 'remote': True}], {'remote': True}),
            ([{'ok': True, 'text': 'fixture', 'remote': True},
              {'ok': True, 'text': 'fallback', 'notice': 'local_fallback'}], {'notice': 'local_fallback'}),
            ([{'ok': True, 'text': 'fallback', 'notice': 'local_fallback'},
              {'ok': True, 'text': 'fixture', 'remote': True}], {'remote': True}),
            ([{'ok': True, 'text': 'fallback', 'notice': 'local_fallback'},
              {'ok': False, 'why': 'broken', 'notice': 'remote_unavailable'}], None),
            ([{'ok': True, 'text': 'fallback', 'notice': 'local_fallback', 'asr_route': 'remote'}], {'remote': True}),
            ([{'ok': False, 'text': '', 'notice': 'local_fallback'}], None),
            ([{'ok': True, 'text': 'local'}], None),
        ]:
            host._post.reset_mock()
            host._send_device.reset_mock()
            host._transcribe_file = AsyncMock(side_effect=transcripts)
            send = compose.Send('origin', 'fixture', 1)
            send.ready = asyncio.get_running_loop().create_future()
            files = [{'mime': 'audio/wav', 'path': 'fixture.wav', 'bytes': 100} for _ in transcripts]
            await host._prep_say(send, '', files, files, None)
            self.assertTrue(send.ready.done())
            self.assertTrue(all('remote' not in row for row in host.hist_update.call_args.kwargs['att_asr'].values()))
            if expected is None:
                host._post.assert_not_called()
                continue
            route = 'remote' if expected.get('remote') else 'local_fallback'
            host._post.assert_called_once_with(host._say_asr_route, send, route, 0)
            await host._say_asr_route(send, route, 0)
            host._send_device.assert_awaited_once_with('origin', {
                't': 'say_state', 'sid': 'fixture', 's': 'asr_route', **expected,
            })
            send.state = 'cancelled'
            host._send_device.reset_mock()
            await host._say_asr_route(send, route, 0)
            host._send_device.assert_not_awaited()
