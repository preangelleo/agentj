"""Raw loopback dispatcher acceptance, not full Noise/websocket Host acceptance."""
import asyncio
import json
import unittest
import test_install_assist_host as boundary

class Loopback(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request
    async def test_signed_plan_replay_bad_signature_and_backend_refusal(self):
        async def accept(reader, writer):
            # The fixture's paired session and real signing key are synthetic.
            async def reply(s, rid, action, ok, **result):
                writer.write((json.dumps({'r':rid, 'action':action, 'ok':ok, **result})+'\n').encode())
                await writer.drain()
            self.h._ctl_res = reply
            try:
                while raw := await reader.readline():
                    await self.runner.on_phone(self.s, json.loads(raw))
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(accept, '127.0.0.1', 0)
        async with server:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            async def send(obj):
                writer.write((json.dumps(obj)+'\n').encode())
                await writer.drain()
                return json.loads(await asyncio.wait_for(reader.readline(), 2))
            obj = self.request(r='abcdef0123456789',t='setup_run')
            self.assertTrue((await send(obj))['ok'])
            self.assertEqual((await send(obj))['why'], 'replay')
            bad = self.request(r='abcdef0123456789', t='setup_run')
            bad['sig']='A'*86
            self.assertEqual((await send(bad))['why'], 'bad_signature')
            install = self.request('ai.install', {'cli':'codex'},r='abcdef0123456789',t='setup_run')
            self.assertEqual((await send(install))['why'], 'unavailable')
            writer.close()
            await writer.wait_closed()
