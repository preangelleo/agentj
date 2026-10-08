"""Real local HTTP CONNECT/SOCKS5 relay transports, and credential-safe diagnostics."""
import asyncio
import contextlib
import json
import os
import select
import socket
import socketserver
import threading
import unittest
from unittest.mock import patch

from websockets.sync.server import serve
from websockets.asyncio.client import connect
from agentj import doctor, proxy


def exact(s, n):
    data = b''
    while len(data) < n:
        part = s.recv(n - len(data))
        if not part:
            raise EOFError
        data += part
    return data


class Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        s.settimeout(5)
        try:
            if self.server.kind == 'socks5':
                head = exact(s, 2)
                exact(s, head[1])
                s.sendall(b'\x05\x00')
                head = exact(s, 4)
                if head[3] == 1:
                    host = socket.inet_ntoa(exact(s, 4))
                elif head[3] == 3:
                    host = exact(s, exact(s, 1)[0]).decode()
                else:
                    raise ValueError('unsupported address')
                port = int.from_bytes(exact(s, 2), 'big')
                upstream = socket.create_connection((host, port), 5)
                s.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
            else:
                data = b''
                while not data.endswith(b'\r\n\r\n'):
                    data += exact(s, 1)
                    if len(data) > 8192:
                        raise ValueError('header too big')
                method, address, _ = data.split(b'\r\n')[0].decode().split()
                if method != 'CONNECT':
                    raise ValueError('expected CONNECT')
                host, port = address.rsplit(':', 1)
                upstream = socket.create_connection((host, int(port)), 5)
                s.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
            self.server.tunnels += 1
            with upstream:
                while True:
                    ready, _, _ = select.select([s, upstream], [], [], 5)
                    if not ready:
                        return
                    for src in ready:
                        data = src.recv(65536)
                        if not data:
                            return
                        (upstream if src is s else s).sendall(data)
        except (OSError, EOFError):
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class RelayProxy(unittest.TestCase):
    def test_native_http_and_socks_sync_and_async(self):
        def greet(ws):
            ws.send(json.dumps({'t': 'host', 'up': False}))
            for msg in ws:
                ws.send(msg)
        with serve(greet, '127.0.0.1', 0) as relay:
            relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
            relay_thread.start()
            url = f'ws://127.0.0.1:{relay.socket.getsockname()[1]}'
            for kind in ('socks5', 'http'):
                with self.subTest(kind=kind), Server(('127.0.0.1', 0), Tunnel) as server:
                    server.kind, server.tunnels = kind, 0
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    value = f'{kind}://127.0.0.1:{server.server_address[1]}'
                    try:
                        with patch.dict(os.environ, {'HTTPS_PROXY': value, 'NO_PROXY': ''}, clear=True):
                            self.assertEqual(proxy.relay_summary(url), kind + '://127.0.0.1')
                            self.assertEqual(doctor.probe_relay(url), (True, 'ok'))
                            async def roundtrip():
                                async with connect(url, open_timeout=5) as ws:
                                    self.assertEqual(json.loads(await ws.recv())['t'], 'host')
                                    await ws.send('encrypted-fixture')
                                    self.assertEqual(await ws.recv(), 'encrypted-fixture')
                            asyncio.run(roundtrip())
                        self.assertEqual(server.tunnels, 2)
                    finally:
                        server.shutdown()
                        thread.join(5)
            relay.shutdown()
            relay_thread.join(5)

    def test_redaction_system_selection_and_missing_module(self):
        from websockets import proxy as native
        with patch.object(native.urllib.request, 'proxy_bypass', return_value=False), patch.object(
                native.urllib.request, 'getproxies', return_value={'socks': 'http://owner:private@127.0.0.1:7890/path?key=private'}):
            self.assertEqual(proxy.relay_summary('wss://relay.test'), 'socks5h://127.0.0.1')
            why = proxy.relay_failure(ImportError('python-socks private'), 'wss://relay.test')
            self.assertIn('missing python-socks', why)
            self.assertNotIn('private', why)
            self.assertNotIn('owner', why)
            self.assertNotIn('7890', why)
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://owner:private@127.0.0.1:7890', 'NO_PROXY': 'relay.test'}, clear=True):
            self.assertEqual(proxy.relay_summary('wss://relay.test'), '')

    def test_doctor_metadata_on_success_and_failure(self):
        class State:
            def config(self):
                return {'relay': 'wss://relay.test'}
        with patch.object(proxy, 'relay_summary', return_value='socks5://127.0.0.1'), patch.object(
                doctor, 'probe_relay', return_value=(True, 'ok')):
            self.assertIn('socks5://127.0.0.1', doctor.check_relay(State())['summary'])
        with patch('websockets.sync.client.connect', side_effect=ImportError('python-socks secret')), \
                patch.object(proxy, 'relay_summary', return_value='socks5://127.0.0.1'):
            ok, why = doctor.probe_relay('wss://relay.test')
            self.assertFalse(ok)
            self.assertIn('python-socks', why)
            self.assertNotIn('secret', why)
