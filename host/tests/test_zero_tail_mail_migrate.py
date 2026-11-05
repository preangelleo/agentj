"""Legacy mail authority migration keeps signed scope and requires a fresh UV proof."""
import _hermetic
import copy
import os
import time
from types import SimpleNamespace
from unittest.mock import patch
from test_workflow_owner_mail import Delivery
from agentj import workflow_mail as wm


class Migration(Delivery):
    async def legacy(self):
        await self.grant(days=3)
        data = self.service.load()
        c = data['grants']['story']['core']
        for key in ('mail_account', 'never_expires', 'limit_policy'):
            c.pop(key)
        signed = await self.sign(wm.TOOL, {'summary': wm.shown(c)}, always_ask=True, require_passkey=True, grant_info=True)
        data['grants']['story']['proof'] = {k: v for k, v in signed['grant_info'].items() if k != 'sign_pub'}
        data.pop('cards', None)  # The legacy grant predates the renewal-card cooldown.
        self.service.save(data)
        self.ask.reset_mock()
        return copy.deepcopy(c)

    async def test_migration_keeps_absolute_expiry_and_signs_only_once(self):
        old = await self.legacy()
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(source={'kind': 'phone', 'role': 'owner'}))
        result = await self.service.control({'op': 'migrate', 'workflow_id': 'story'}, os.getpid())
        self.assertTrue(result['ok'], result)
        row = self.service.load()['grants']['story']
        self.assertEqual(row['core']['expires'], old['expires'])
        self.assertFalse(row['core']['never_expires'])
        self.assertEqual(row['migrated_from']['core'], old)
        self.assertEqual(row['core']['artifact_root'], old['artifact_root'])
        self.assertTrue(wm.proof_ok(self.st, row['core'], row['proof']))
        self.ask.assert_awaited_once()
        self.ask.reset_mock()
        result = await self.service.control({'op': 'migrate', 'workflow_id': 'story'}, os.getpid())
        self.assertTrue(result['unchanged'])
        self.ask.assert_not_awaited()

    async def test_status_auto_migration_does_not_widen_legacy_expiry(self):
        old = await self.legacy()
        result = await self.service.control({'op': 'status', 'workflow_id': 'story'}, os.getpid())
        self.assertEqual(result['grant']['card'], 'sent')
        await self.service.pending['story']
        self.assertEqual(self.service.load()['grants']['story']['core']['expires'], old['expires'])

    async def test_migration_rejects_foreign_or_unsigned_or_revoked(self):
        await self.legacy()
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(source={'kind': 'email', 'role': 'group'}))
        with patch.object(wm, 'source', return_value=None):
            self.assertEqual((await self.service.control({'op': 'migrate', 'workflow_id': 'story'}, os.getpid()))['why'], 'workflow_source')
        self.host.agent.cur_send.source = {'kind': 'phone', 'role': 'owner'}
        data = self.service.load()
        data['grants']['story']['proof']['sig'] = wm.wire.b64u(b'x' * 64)
        self.service.save(data)
        self.assertEqual((await self.service.control({'op': 'migrate', 'workflow_id': 'story'}, os.getpid()))['why'], 'migration_unavailable')
        self.ask.assert_not_awaited()

    async def test_migration_denial_does_not_mutate_old_grant(self):
        old = await self.legacy()
        self.ask.side_effect = None
        self.ask.return_value = {'behavior': 'deny'}
        result = await self.service.control({'op': 'migrate', 'workflow_id': 'story'}, os.getpid())
        self.assertFalse(result['ok'])
        self.assertEqual(self.service.load()['grants']['story']['core'], old)

    async def test_migration_never_renews_expired_or_revoked_records(self):
        old = await self.legacy()
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(source={'kind': 'phone', 'role': 'owner'}))
        with patch.object(wm.time, 'time', return_value=old['expires'] + 1):
            self.assertEqual((await self.service.control({'op': 'migrate', 'workflow_id': 'story'}, os.getpid()))['why'], 'migration_unavailable')
        await self.service.control({'op': 'revoke', 'workflow_id': 'story'}, None)
        self.assertEqual((await self.service.control({'op': 'migrate', 'workflow_id': 'story'}, os.getpid()))['why'], 'migration_unavailable')
        self.ask.assert_not_awaited()
