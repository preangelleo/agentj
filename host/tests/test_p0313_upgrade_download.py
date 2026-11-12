"""0313: truncated published downloads and bounded same-origin hash recovery."""
import _hermetic  # noqa: F401
import hashlib
import http.client
import http.server
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from agentj import auto_update, support, update, upgrade_diag


class ShortServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        payload = self.server.payload
        self.server.requests.append(self.path)
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        if len(self.server.requests) <= self.server.short_count:
            payload = payload[:7]
        self.wfile.write(payload)
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *args):
        pass


class Downloads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            'HOME': self.tmp.name, 'XDG_CONFIG_HOME': self.tmp.name + '/config',
            'XDG_CACHE_HOME': self.tmp.name + '/cache', 'XDG_DATA_HOME': self.tmp.name + '/data',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def server(self, payload, short_count):
        srv = ShortServer(('127.0.0.1', 0), Handler)
        srv.payload, srv.short_count, srv.requests = payload, short_count, []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, f'http://127.0.0.1:{srv.server_port}/wheel'

    def test_short_content_length_retries_then_succeeds(self):
        srv, url = self.server(b'published wheel', 2)
        self.assertEqual(auto_update.download(url, 100, sleep=lambda _: None), srv.payload)
        self.assertEqual(len(srv.requests), 3)

    def test_persistent_truncation_is_download_failure_with_closed_subreason(self):
        srv, url = self.server(b'published wheel', 3)
        with self.assertRaises(http.client.IncompleteRead) as cm:
            auto_update.download(url, 100, sleep=lambda _: None)
        exc = cm.exception
        self.assertEqual(exc.download_attempts, 3)
        self.assertEqual(upgrade_diag.plan_reason(exc), 'download_failed')
        self.assertEqual(upgrade_diag.plan_fields(exc)['subreason'], 'truncated')
        body = upgrade_diag.metadata_body({'from': '0.30.6', 'to': '0.30.12', 'result': 'failed',
            'reason': 'download_failed', 'step': 'download', 'subreason': 'truncated'}, 'uv')
        self.assertTrue(support.metadata_ok(body))
        self.assertFalse(support.metadata_ok(body.replace('truncated', 'truncated /private/key')))

    def test_full_length_wrong_hash_reloads_wheel_and_checksum_once(self):
        from agentj.state import State
        st = State(Path(self.tmp.name) / 'state')
        wheel = b'wrong bytes'
        stated = hashlib.sha256(b'expected bytes').hexdigest().encode() + b'\n'
        urls = []
        def fetch(url, limit):
            urls.append(url)
            return wheel if url.split('?')[0].endswith('.whl') else stated
        with mock.patch.object(auto_update, 'ORIGIN', 'https://agentj.app/dl/'):
            with self.assertRaisesRegex(ValueError, '^artifact_hash_mismatch$') as cm:
                auto_update.cached_wheel(st, '0.30.12', fetch=fetch)
        self.assertEqual(upgrade_diag.plan_reason(cm.exception), 'signature_or_hash_mismatch')
        self.assertEqual(upgrade_diag.plan_fields(cm.exception)['subreason'], 'hash_mismatch')
        self.assertEqual(len(urls), 4)
        self.assertEqual([u.split('?')[0] for u in urls[2:]], urls[:2])
        self.assertTrue(all('?r=' in u for u in urls[2:]))
        self.assertTrue(all(u.startswith('https://agentj.app/dl/') for u in urls))

    def test_cache_bust_can_recover_hash_without_changing_signature_boundary(self):
        from agentj.state import State
        st = State(Path(self.tmp.name) / 'state')
        good = b'correct bytes'
        wrong = b'wrong bytes'
        stated = hashlib.sha256(good).hexdigest().encode()
        values = iter((wrong, stated, good, stated, b'not a valid signature'))
        urls = []
        def fetch(url, limit):
            urls.append(url)
            return next(values)
        with self.assertRaises(Exception) as cm:
            auto_update.cached_wheel(st, '0.30.12', fetch=fetch)
        self.assertNotEqual(str(cm.exception), 'artifact_hash_mismatch')
        self.assertEqual(len(urls), 5)
        self.assertNotIn('?', urls[4])  # existing signature source and verifier unchanged

    def test_latest_short_read_is_not_a_version(self):
        srv, url = self.server(b'0.30.12\n', 1)
        with mock.patch.object(update, 'latest_url', return_value=url):
            self.assertEqual(update.fetch_latest(), (None, 'network'))
        self.assertEqual(len(srv.requests), 1)

    def test_oversized_declared_length_does_not_retry(self):
        srv, url = self.server(b'a' * 128, 0)
        with self.assertRaisesRegex(ValueError, 'artifact_too_large'):
            auto_update.download(url, 10, sleep=lambda _: None)
        self.assertEqual(len(srv.requests), 1)

    def test_manual_upgrade_uses_shared_verified_wheel(self):
        with mock.patch.object(auto_update, 'cached_wheel', return_value=Path(self.tmp.name) / 'signed.whl') as shared:
            self.assertEqual(update.verified_wheel('0.30.12'), Path(self.tmp.name) / 'signed.whl')
        self.assertTrue(shared.call_args.kwargs['require_signature'])
