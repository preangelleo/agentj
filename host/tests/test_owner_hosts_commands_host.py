"""Commands against a real temporary Host; missing runtime dependencies are ERRORS."""
from test_owner_hosts_host import OwnerHostsHost
from agentj import owner_hosts as oh
from unittest import IsolatedAsyncioTestCase, mock


class CommandsHost(IsolatedAsyncioTestCase):
    asyncSetUp = OwnerHostsHost.asyncSetUp
    target = OwnerHostsHost.target
    signed = OwnerHostsHost.signed
    send = OwnerHostsHost.send
    with_uv = OwnerHostsHost.with_uv
    bind = OwnerHostsHost.bind

    def request(self, **kw):
        return oh.handle_commands(self.host, {'cmd': 'owner_hosts_commands', **kw})

    async def test_live_table_then_unbind(self):
        await self.bind()
        with mock.patch.object(oh, 'discover_config', side_effect=AssertionError('discovery')):
            out = self.request()
        self.assertTrue(out['ok'])
        self.assertIsNone(self.host.owner_hosts._receipt)
        for remote, cat in [('rm -rf /srv/code', 'delete'), ('cat .env', 'credentials')]:
            text = self.request(remote=remote)['rows'][0]['ssh']
            self.assertIn(cat, self.host._classify('Bash', {'command': text}).cats)
        self.st.remove_device(self.phone.did)
        self.assertFalse(self.request()['ok'])

    async def test_key_replacement_and_expiry(self):
        await self.bind()
        with mock.patch.object(self.st, 'sign_key', return_value=b'x'*32):
            self.assertFalse(self.request()['ok'])
        with mock.patch.object(oh.time, 'time', return_value=oh.time.time()+7200):
            self.assertFalse(self.request()['ok'])
