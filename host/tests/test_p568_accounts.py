"""#568 regressions. Synthetic credentials only; existing hermetic account fixture."""
import _hermetic  # noqa: F401
import json
from unittest import mock
from test_harness_accounts import CodexSwitch, Rescue, codex_auth
from agentj import accounts


class Registry568(CodexSwitch):
    def test_active_row_does_not_hide_external_login(self):
        (self.home / 'auth.json').write_bytes(codex_auth('carol'))
        self.assertEqual(accounts.switch_sync(self.root, 'codex', '主号').get('why'), 'unregistered_login')

    def test_active_noop_unknown_identity_fails_closed(self):
        d = accounts.load(self.root)
        d['harness']['codex']['items'][0].pop('fp')
        accounts._save(self.root, d)
        (self.home / 'auth.json').write_bytes(b'{"opaque": true}')
        self.assertEqual(accounts.switch_sync(self.root, 'codex', '主号').get('why'), 'unregistered_login')

    def test_deleted_backup_can_be_added_again(self):
        self.assertTrue(accounts.remove_sync(self.root, 'codex', '备用')['ok'])
        iid, slot = accounts.new_codex_slot(self.root)
        (slot / 'auth.json').write_bytes(codex_auth('bob', 'fresh'))
        self.assertEqual(accounts.commit_codex(self.root, iid, '备用')['id'], iid)

    def test_verified_rescue_refreshes_only_matching_registered_account(self):
        (self.home / 'auth.json').write_bytes(codex_auth('bob', 'renewed'))
        self.assertTrue(accounts.on_codex_rescue(self.root))
        hd = accounts.load(self.root)['harness']['codex']
        self.assertEqual(hd['active'], self.bob['id'])
        self.assertEqual((self.slot_dir('codex') / self.bob['id'] / 'auth.json').read_bytes(), codex_auth('bob', 'renewed'))
        self.assertTrue(accounts.switch_sync(self.root, 'codex', '主号')['ok'])

    def test_unknown_rescue_does_not_overwrite_registered_slots(self):
        before = (self.root / accounts.FILE).read_bytes()
        (self.home / 'auth.json').write_bytes(codex_auth('carol'))
        self.assertFalse(accounts.on_codex_rescue(self.root))
        self.assertEqual((self.root / accounts.FILE).read_bytes(), before)
        self.assertEqual((self.slot_dir('codex') / self.alice['id'] / 'auth.json').read_bytes(), codex_auth('alice'))


class Rescue568(Rescue):
    async def test_account_result_does_not_replace_rescue_result(self):
        prior = {'result': 'recovered', 'at': 1}
        self.r.last['codex'] = prior.copy()
        self.r.flow = {'kind': 'codex', 'route': 'device', 'account': '备用'}
        self.r._end('codex', 'failed', 'same_account')
        self.assertEqual(self.r.last['codex'], prior)
        self.assertEqual(self.r.flow['account'], '备用')
        self.r.flow = {'kind': 'codex', 'route': 'device'}
        self.r._end('codex', 'recovered')
        self.assertEqual(self.r.last['codex']['result'], 'recovered')

    async def test_failed_switch_is_metadata_audited(self):
        with mock.patch('agentj.accounts.switch_sync', return_value={'ok': False, 'why': 'slot_missing'}):
            await accounts.use(self.host, 'codex', 'bbbbbbbb', device='test-device')
        rows = [json.loads(l) for l in self.st.log_path.read_text().splitlines()]
        self.assertTrue(any(r.get('ev') == 'account' and r.get('status') == 'failed' and r.get('reason') == 'slot_missing' for r in rows))

    async def test_only_successful_official_rescue_synchronizes_registry(self):
        self.r.flow = {'kind': 'codex', 'route': 'device'}
        async def recovered(*args):
            return None
        with mock.patch('agentj.auth_rescue.CodexFlow') as flow, \
                mock.patch('agentj.auth_rescue.health_check', return_value={'ok': True}), \
                mock.patch('agentj.accounts.on_codex_rescue', return_value=True) as sync, \
                mock.patch.object(self.r, '_health_mark'), mock.patch.object(self.r, 'recover', side_effect=recovered):
            flow.return_value.start.return_value = ('https://example.com', 'FAKE', 60)
            flow.return_value.wait.return_value = 'ok'
            await self.r._codex()
            sync.assert_called_once_with(self.root)
            sync.reset_mock()
            self.r.flow = {'kind': 'codex', 'route': 'device'}
            flow.return_value.wait.return_value = 'failed'
            await self.r._codex()
            sync.assert_not_called()
