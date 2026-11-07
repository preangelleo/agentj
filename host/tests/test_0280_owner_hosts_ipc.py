"""Canonical command metadata through the actual local Unix IPC handler."""
import _hermetic
import asyncio
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from agentj import bounded_plan, owner_hosts
from agentj.serve import Host
from test_owner_hosts_signatures import SignedTable


class SocketCommands(unittest.IsolatedAsyncioTestCase, SignedTable):
    def setUp(self):
        SignedTable.setUp(self)
        self.fixture()
        self.host = NS(owner_hosts=self.desk, st=self.st)
        self.host._ctl_send = Host._ctl_send.__get__(self.host)
        self.origin = patch.object(bounded_plan, 'caller_origin', return_value=None)
        self.origin.start()
        self.addCleanup(self.origin.stop)

    async def request(self, **kwargs):
        socket = str(Path(self.tmp.name)/'metadata.sock')
        if Path(socket).exists():
            Path(socket).unlink()
        server = await asyncio.start_unix_server(lambda r,w:Host.on_ctl(self.host,r,w), path=socket)
        os.chmod(socket, 0o600)
        try:
            reader, writer = await asyncio.open_unix_connection(socket)
            writer.write((json.dumps({'cmd':'owner_hosts_commands',**kwargs})+'\n').encode())
            await writer.drain()
            answer = json.loads(await asyncio.wait_for(reader.readline(), 1))
            writer.close()
            await writer.wait_closed()
            return answer
        finally:
            server.close()
            await server.wait_closed()

    async def test_ipc_canonical_is_read_only(self):
        before = [p.read_bytes()for p in self.desk._paths()]
        with patch.object(owner_hosts, 'discover_config', side_effect=AssertionError('discovery')):
            answer = await self.request(alias='devbox')
        self.assertTrue(answer['ok'])
        self.assertEqual(answer['rows'][0]['ssh'], owner_hosts.command(self.target['rows'][0]))
        self.assertEqual(before,[p.read_bytes()for p in self.desk._paths()])
        self.assertIsNone(self.desk._receipt)

    async def test_ipc_unknown_alias_and_forged_fields(self):
        self.assertFalse((await self.request(alias='unknown'))['ok'])
        self.assertFalse((await self.request(alias='devbox', receipt=True))['ok'])
        self.assertFalse((await self.request(alias='devbox', remote=True))['ok'])

    async def test_ipc_rechecks_live_signature_and_expiry(self):
        with patch.object(owner_hosts.time,'time',return_value=self.target['rows'][0]['expires']+1):
            self.assertFalse((await self.request(alias='devbox'))['ok'])
        self.keys.clear()
        self.assertFalse((await self.request(alias='devbox'))['ok'])
