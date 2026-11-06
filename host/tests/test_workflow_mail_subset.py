"""Signed CC pools may narrow per delivery; never broaden or replay implicitly."""
import _hermetic
import copy
import os
from unittest.mock import patch
from agentj import contacts, mail, workflow_mail as wm
from test_workflow_fixed_mail import FixedDelivery


class SubsetDelivery(FixedDelivery):
    def selected(self, cc):
        req = self.fixed()
        req['cc'] = list(cc)
        req['delivery_manifest']['cc'] = list(cc)
        return req

    async def test_signed_pool_accepts_narrowed_and_empty_cc_without_card(self):
        await self.grant(cc_contacts=self.ids)
        original = copy.deepcopy(self.service.load()['grants']['story'])
        for n, selected in enumerate((self.cc[:2], self.cc[2:], [], list(reversed(self.cc)))):
            req = self.selected(selected)
            req['delivery_manifest']['idempotency_key'] = f'subset-{n}'
            res = await self.service.send(req, os.getpid())
            self.assertTrue(res['ok'], res)
            self.assertEqual(res['sent'], [original['core']['binding']['primary']] + selected)
            self.assertEqual(self.smtp.sent[-1][1], res['sent'])
            from email import message_from_bytes
            from email.utils import getaddresses
            message = message_from_bytes(self.smtp.sent[-1][2])
            self.assertEqual(message['To'], original['core']['binding']['primary'])
            self.assertEqual([address for _, address in getaddresses(message.get_all('Cc', []))], selected)
        self.assertEqual(self.service.load()['grants']['story'], original)
        self.ask.assert_awaited_once()

    async def test_narrowed_grant_request_reuses_full_signed_pool(self):
        await self.grant(cc_contacts=self.ids)
        original = copy.deepcopy(self.service.load()['grants']['story'])
        for ids in (self.ids[:2], []):
            res = await self.grant(cc_contacts=ids)
            self.assertTrue(res.get('unchanged'), res)
        self.assertEqual(self.service.load()['grants']['story'], original)
        self.ask.assert_awaited_once()

    async def test_outside_pool_and_missing_owner_never_submit(self):
        await self.grant(cc_contacts=self.ids)
        for cc in (['outside@example.test'], self.cc[:1] * 2, ['OWNER@example.test'], [None]):
            res = await self.service.send(self.selected(cc), os.getpid())
            self.assertFalse(res['ok'], res)
            self.assertFalse(res['provider_acceptance'])
        for value in (False, None, '', 1):
            req = self.selected(self.cc[:1]); req['to_owner'] = value
            self.assertEqual((await self.service.send(req, os.getpid()))['why'], 'recipient')
        book = contacts.read(self.st)
        contacts.owner(book)['emails'] = []
        contacts.write(self.st, book)
        self.assertEqual((await self.service.send(self.selected(self.cc[:1]), os.getpid()))['why'], 'grant_required')
        self.assertEqual(self.smtp.sent, [])

    async def test_subset_capability_advertised_and_legacy_summary_still_verifies(self):
        await self.grant(cc_contacts=self.ids)
        row = self.service.load()['grants']['story']
        self.assertTrue(wm.proof_ok(self.st, row['core'], row['proof']))
        res = await self.service.control({'op': 'status', 'workflow_id': 'story'}, os.getpid())
        self.assertIn('workflow-fixed-recipient-mail-subset-v1', res['capabilities'])
        self.assertEqual(res['grant']['cc_contact_ids'], self.ids)
        self.ask.assert_awaited_once()

    async def test_pre_submit_config_race_is_retryable_but_not_replayed(self):
        await self.grant(cc_contacts=self.ids)
        req = self.selected(self.cc[:2])
        original_cfg = mail.read_config(self.st)
        build = self.svc.build
        def changed(*args, **kw):
            message = build(*args, **kw)
            cfg = mail.read_config(self.st)
            cfg['address'] = 'changed@example.test'
            mail.save_config(self.st, cfg)
            return message
        with patch.object(self.svc, 'build', side_effect=changed):
            res = await self.service.send(req, os.getpid())
        self.assertEqual(res['why'], 'grant_stale')
        self.assertEqual(res['status'], 'blocked')
        self.assertTrue(res['retryable'])
        self.assertTrue(res['retry_requires_explicit_request'])
        self.assertFalse(res['provider_acceptance'])
        self.assertEqual(self.smtp.sent, [])
        mail.save_config(self.st, original_cfg)
        duplicate = await self.service.send(req, os.getpid())
        self.assertEqual(duplicate['why'], 'duplicate')
        self.assertTrue(duplicate['retryable'])
        self.assertEqual(self.smtp.sent, [])
        # Only the existing explicit owner retry operation unlocks the same manifest once.
        from types import SimpleNamespace
        self.host.agent = SimpleNamespace(cur_send=SimpleNamespace(source={'kind': 'phone', 'role': 'owner'}))
        self.ask.side_effect = None
        self.ask.return_value = {'behavior': 'allow'}
        retry = await self.service.control({'op': 'retry', 'workflow_id': 'story', 'delivery': res['delivery']}, os.getpid())
        self.assertTrue(retry['retry_once'], retry)
        self.assertTrue(self.ask.await_args.kwargs['require_passkey'])
        self.assertTrue((await self.service.send(req, os.getpid()))['ok'])
        self.assertEqual(len(self.smtp.sent), 1)
        self.assertEqual((await self.service.send(req, os.getpid()))['why'], 'duplicate')
