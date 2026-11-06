"""#536: backup accounts use the key manager's read-only primary status."""
import _hermetic  # noqa: F401
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentj import accounts, keys


class BackupAccounts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = SimpleNamespace(st=SimpleNamespace(root=self.root))
        self.primary = {'type': 'oauth', 'name': 'claude', 'set': True, 'state': 'in',
                        'use': 'key', 'use_name': 'ANTHROPIC_API_KEY', 'use_src': 'agentj', 'pending': True}
        self.scan = {'base': [{'rows': [dict(self.primary, value='private-sentinel', tail='tail', path='/private')]},
                              {'rows': [{'type': 'oauth', 'name': 'codex', 'set': False, 'state': 'out', 'use': 'login'}]}]}

    async def test_primary_matches_key_manager_and_has_no_extra_fields(self):
        with patch.object(keys, 'scan', return_value=self.scan) as scan:
            result = await accounts.phone(self.host, SimpleNamespace(device='test'), 'acct_get', {})
        scan.assert_called_once_with(self.host)
        self.assertEqual(result['view']['primary']['claude'], self.primary)
        self.assertEqual(result['view']['primary']['codex'], self.scan['base'][1]['rows'][0])
        self.assertNotIn('private-sentinel', json.dumps(result))
        self.assertNotIn('/private', json.dumps(result))
        self.assertNotIn('tail', result['view']['primary']['claude'])

    async def test_old_accounts_and_credentials_remain_byte_exact(self):
        old = accounts._blank()
        old['harness']['claude'] = {'active': 'bbbbbbbb', 'items': [
            {'id': 'aaaaaaaa', 'name': '主号', 'method': 'login', 'added': 1},
            {'id': 'bbbbbbbb', 'name': '备用', 'method': 'token', 'added': 2}]}
        path = self.root / accounts.FILE
        path.write_text(json.dumps(old, ensure_ascii=False))
        slot = self.root / 'untouched.token'
        slot.write_bytes(b'credential-sentinel')
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()}
        with patch.object(keys, 'scan', return_value=self.scan):
            result = await accounts.phone(self.host, SimpleNamespace(device='test'), 'acct_get', {})
        self.assertEqual(result['view']['claude']['active'], 'bbbbbbbb')
        self.assertEqual([x['name'] for x in result['view']['claude']['items']], ['主号', '备用'])
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()})
        self.assertNotIn('credential-sentinel', json.dumps(result))

    def test_cli_view_keeps_existing_contract(self):
        with patch.object(keys, 'scan', side_effect=AssertionError('CLI should not probe keys')):
            self.assertNotIn('primary', accounts.view(self.root))

    async def test_real_paired_route_returns_primary_only_to_live_device(self):
        from agentj import serve
        sent, jobs = [], []
        session = SimpleNamespace(cid='test', state='ready', pub='paired', device='test')
        self.host.sessions = {session.cid: session}
        self.host.st.is_allowed = lambda pub: pub == 'paired'
        self.host._bg = lambda coroutine, label: jobs.append(coroutine)

        async def send_app(s, msg):
            sent.append((s, msg))
        self.host.send_app = send_app
        with patch.object(keys, 'scan', return_value=self.scan):
            serve.Host.on_acct(self.host, session, 'acct_get', {'r': 'r536'})
            await jobs.pop()
            self.assertEqual(sent[0][1]['view']['primary']['claude'], self.primary)
            self.assertIs(sent[0][0], session)
            session.state = 'closing'
            serve.Host.on_acct(self.host, session, 'acct_get', {'r': 'r537'})
            await jobs.pop()
        self.assertEqual(len(sent), 1)

    def test_key_manager_precedence_covers_api_and_subscription_modes(self):
        from agentj import keys_found, provider_profiles
        with patch.object(provider_profiles, 'secure_read', return_value=b'{}'):
            for name, use in [('ANTHROPIC_API_KEY', 'key'), ('CLAUDE_CODE_OAUTH_TOKEN', 'sub')]:
                env = {name: 'opaque-fixture'}
                self.assertEqual(keys_found.claude_active(env), (use, name))
                self.assertEqual(keys._use('claude', env, env, []),
                                 {'use': use, 'use_name': name, 'use_src': 'agentj'})
        env = {'CODEX_API_KEY': 'opaque-fixture'}
        with patch('agentj.peer_session.codex_user_model', return_value={}):
            self.assertEqual(keys_found.codex_active(env), ('key', 'CODEX_API_KEY'))
