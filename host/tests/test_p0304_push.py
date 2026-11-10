"""0304: a reconnected phone learns host subscription state, and an old send cannot delete a new subscription."""
import _hermetic  # noqa: F401
import asyncio
import http.server
import os
import pathlib
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from cryptography.hazmat.primitives.asymmetric import ec
from agentj import migrate, webpush
from agentj.serve import Host
from agentj.state import State as RealState
from test_l1 import Phone, _host, _ready, _state


class State:
    def __init__(self, root):
        self.push_path = pathlib.Path(root) / 'push.json'
        self.vapid_path = pathlib.Path(root) / 'push_vapid.key'
        self.logs = []
    def write_private(self, path, data):
        path.write_bytes(data)
    def log(self, event, **fields):
        self.logs.append((event, fields))
    def devices(self):
        return {'phone': {}}
    def is_allowed(self, pub):
        return True


def sub(endpoint='https://fcm.googleapis.com/fcm/send/first'):
    pub = ec.generate_private_key(ec.SECP256R1()).public_key()
    return {'t': 'push_sub', 'endpoint': endpoint,
            'p256dh': webpush.b64u(webpush._pub_bytes(pub)), 'auth': webpush.b64u(b'0123456789abcdef')}


class PushHost(unittest.TestCase):
    def test_subscription_confirmation_and_off_follow_storage(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            sent = []
            host = SimpleNamespace(st=st, push_generation={}, send_app=mock.AsyncMock(side_effect=lambda _s, msg: sent.append(msg)))
            phone = SimpleNamespace(state='ready', device='phone', p33=True, pub=b'k', cid=1)
            async def deliver(msg):
                await Host._app_frame(host, phone, msg)
            asyncio.run(deliver(sub()))
            self.assertEqual(sent[-1], {'t': 'push_state', 'on': True})
            self.assertIn('phone', webpush.load_subs(st))
            asyncio.run(deliver({'t': 'push_off'}))
            self.assertEqual(sent[-1], {'t': 'push_state', 'on': False})
            self.assertNotIn('phone', webpush.load_subs(st))
            asyncio.run(deliver({'t': 'push_sub', 'endpoint': 'http://invalid'}))
            self.assertEqual(sent[-1], {'t': 'push_state', 'on': False})

    def test_push_off_echoes_only_valid_request_id_after_storage_removal(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            sent = []
            host = SimpleNamespace(st=st, push_generation={},
                                   send_app=mock.AsyncMock(side_effect=lambda _s, msg: sent.append(msg)))
            phone = SimpleNamespace(state='ready', device='phone', p33=True, pub=b'k', cid=1)
            request_id = '246ad9e4-1df3-4ce2-a4f1-2780ca286fd2'
            webpush.save_subs(st, {'phone': webpush.parse_sub(sub())})
            asyncio.run(Host._app_frame(host, phone, {'t': 'push_off', 'r': request_id}))
            self.assertEqual(sent[-1], {'t': 'push_state', 'on': False, 'r': request_id})
            self.assertNotIn('phone', webpush.load_subs(st))
            self.assertNotIn(request_id, str(st.logs), 'ack correlation stays off disk/logs')
            for bad in ('not-uuid', '246ad9e4-1df3-1ce2-a4f1-2780ca286fd2',
                        '246ad9e4-1df3-4ce2-74f1-2780ca286fd2', 'x' * 8192, 7, None, {'r': request_id}):
                sent.clear()
                asyncio.run(Host._app_frame(host, phone, {'t': 'push_off', 'r': bad}))
                self.assertEqual(sent[-1], {'t': 'push_state', 'on': False}, bad)
            sent.clear()
            asyncio.run(Host._app_frame(host, phone, {'t': 'push_off'}))
            self.assertEqual(sent[-1], {'t': 'push_state', 'on': False})

    def test_ready_recovers_persisted_subscription_before_key_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            st = _state(d)
            phone = Phone(st)
            saved = webpush.parse_sub(sub())
            webpush.save_subs(st, {phone.did: dict(saved, kinds=['ask'])})
            # A new State and Host must read the old subscription, not rely on prior host memory.
            restarted = RealState(st.root)
            sent = []
            host = _host(restarted, sent)
            session = _ready(host, phone)
            asyncio.run(host.on_ready(session, 0))
            msgs = [msg for _, msg in sent]
            self.assertLess([m['t'] for m in msgs].index('push_state'), [m['t'] for m in msgs].index('push_key'))
            self.assertEqual(next(m for m in msgs if m['t'] == 'push_state'), {'t': 'push_state', 'on': True})
            self.assertEqual(webpush.load_subs(restarted)[phone.did]['kinds'], ['ask'])
            webpush.save_subs(restarted, {})
            sent.clear()
            asyncio.run(host.on_ready(session, 0))
            self.assertEqual(next(m for _, m in sent if m['t'] == 'push_state'), {'t': 'push_state', 'on': False})

    def test_legacy_state_move_preserves_subscription_and_vapid(self):
        with tempfile.TemporaryDirectory() as d:
            old = pathlib.Path(d) / 'legacy'
            new = pathlib.Path(d) / 'current'
            old.mkdir(mode=0o700)
            st = State(old)
            saved = webpush.parse_sub(sub())
            webpush.save_subs(st, {'phone': dict(saved, kinds=['security'])})
            before = webpush.vapid_public(webpush.load_vapid(st))
            with mock.patch.object(migrate, 'serve_running', return_value=False):
                self.assertEqual(migrate._move(old, new), 'moved')
            moved = State(new)
            self.assertEqual(webpush.load_subs(moved)['phone']['kinds'], ['security'])
            self.assertEqual(webpush.vapid_public(webpush.load_vapid(moved)), before)
            self.assertEqual(webpush.load_subs(State(old))['phone'], webpush.load_subs(moved)['phone'])

    def test_local_push_endpoint_receives_encrypted_kind_only(self):
        received = []
        class Receiver(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, self.headers, self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(201)
                self.end_headers()
            def log_message(self, *_args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            with mock.patch.dict(os.environ, {'AGENTJ_TEST_PUSH_ORIGIN': origin}):
                endpoint = origin + '/push'
                subscription = webpush.parse_sub(sub(endpoint))
                self.assertIsNotNone(subscription)
                with tempfile.TemporaryDirectory() as d:
                    st = State(d)
                    webpush.save_subs(st, {'phone': subscription})
                    key = webpush.load_vapid(st)
                    self.assertEqual(webpush.send(key, webpush.load_subs(st)['phone'], 'reply'), 201)
            self.assertEqual(len(received), 1)
            path, headers, ciphertext = received[0]
            self.assertEqual(path, '/push')
            self.assertEqual(headers['Content-Encoding'], 'aes128gcm')
            self.assertEqual(headers['TTL'], str(webpush.TTL))
            self.assertNotIn(b'reply', ciphertext)
            self.assertNotIn(b'ask', ciphertext)
            self.assertEqual(len(ciphertext), 16 + 4 + 1 + 65 + webpush.PAYLOAD_LEN + 1 + 16)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_same_subscription_off_then_resubscribe_survives_old_410(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            saved = webpush.parse_sub(sub())
            host = SimpleNamespace(st=st, send_app=mock.AsyncMock(), sessions={}, push_last={},
                                   push_generation={}, push_key=object())
            host._push_key = lambda: host.push_key
            host._push_done = lambda *args: Host._push_done(host, *args)
            phone = SimpleNamespace(state='ready', device='phone', p33=True, pub=b'k', cid=1)
            callbacks = []
            def fake_thread(*_args):
                fut = mock.Mock()
                fut.add_done_callback.side_effect = callbacks.append
                fut.result.return_value = 410
                return fut
            with mock.patch('agentj.serve.in_daemon_thread', side_effect=fake_thread):
                asyncio.run(Host._app_frame(host, phone, dict(saved, t='push_sub')))
                Host.push_notify(host, 'reply')
                self.assertEqual(len(callbacks), 1)
                asyncio.run(Host._app_frame(host, phone, {'t': 'push_off'}))
                asyncio.run(Host._app_frame(host, phone, dict(saved, t='push_sub')))
                self.assertEqual(webpush.load_subs(st)['phone'], saved)
                callbacks[0](fake_thread())
                self.assertEqual(webpush.load_subs(st)['phone'], saved, 'old 410 cannot undo the new opt-in')
                host.push_last.clear()
                Host.push_notify(host, 'reply')
                self.assertEqual(len(callbacks), 2)
                callbacks[1](fake_thread())
                self.assertNotIn('phone', webpush.load_subs(st), 'current 410 still expires the subscription')

    def test_old_410_cannot_delete_replaced_subscription(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            old = webpush.parse_sub(sub())
            newer = webpush.parse_sub(sub('https://fcm.googleapis.com/fcm/send/second'))
            webpush.save_subs(st, {'phone': newer})
            h = SimpleNamespace(st=st, push_generation={'phone': 2})
            future = mock.Mock()
            future.result.return_value = 410
            Host._push_done(h, 'phone', future, old, 1)
            self.assertEqual(webpush.load_subs(st)['phone'], newer)
            Host._push_done(h, 'phone', future, newer, 2)
            self.assertNotIn('phone', webpush.load_subs(st))


if __name__ == '__main__':
    unittest.main()
