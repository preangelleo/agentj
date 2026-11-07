"""Actual Host setup_run dispatch over a local socket; requires standard Host deps."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import test_install_assist_host as boundary

class HostDispatch(unittest.IsolatedAsyncioTestCase):
    setUp = boundary.Boundary.setUp
    tearDown = boundary.Boundary.tearDown
    request = boundary.Boundary.request

    async def test_actual_host_signed_setup_over_local_socket(self):
        from agentj.serve import Host, Session
        from agentj import provenance
        host = Host.__new__(Host)
        host.st, host.sessions = self.h.st, {}
        host.channel, host.nonces = self.h.channel, self.h.nonces
        host.stopped = lambda: False
        owner = Session(cid=1, state='ready', device='owner', pub=b'paired', p33=True)
        owner.provenance = provenance.phone()
        host.sessions[1] = owner
        tasks = []
        host._bg = lambda coro, name: tasks.append(asyncio.create_task(coro))
        async def accept(reader, writer):
            async def result(s, r, action, ok, **extra):
                writer.write((json.dumps({'ok':ok, **extra})+'\n').encode())
                await writer.drain()
            host._ctl_res = result
            try:
                while raw := await reader.readline():
                    await Host._app(host, owner, json.loads(raw))
            finally:
                writer.close()
                await writer.wait_closed()
        path = self.h.st.root/'native.sock'
        server = await asyncio.start_unix_server(accept, path=path)
        async with server:
            reader, writer = await asyncio.open_unix_connection(path)
            try:
                obj = self.request('sys.check', item='desktop', t='setup_run', r='abcdef0123456789')
                writer.write((json.dumps(obj)+'\n').encode())
                await writer.drain()
                result = json.loads(await asyncio.wait_for(reader.readline(), 3))
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['result']['checked'], True)
                self.assertNotIn('/work', str(result))
                writer.write((json.dumps(obj)+'\n').encode())
                await writer.drain()
                replay = json.loads(await asyncio.wait_for(reader.readline(), 3))
                self.assertEqual(replay['why'], 'replay')
            finally:
                writer.close()
                await writer.wait_closed()
        await asyncio.gather(*tasks)
