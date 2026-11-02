"""Explicit fixed-recipient grants, fake local SMTP and synthetic paired signing roots only."""
import _hermetic
import copy
import os
from agentj import contacts
from test_workflow_owner_mail import Delivery


class FixedDelivery(Delivery):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.cc = [f'person{i}@example.test' for i in range(4)]
        self.ids = [f'contact{i}' for i in range(4)]
        b = contacts.read(self.st)
        b['contacts'] += [{'id': i, 'name': i, 'emails': [{'v': a}]} for i, a in zip(self.ids, self.cc)]
        contacts.write(self.st, b)

    def fixed(self):
        req = copy.deepcopy(self.req)
        req['cc'] = self.cc
        req['delivery_manifest'].update(v=2, purpose='fixed_recipient_artifact_delivery', cc=self.cc)
        return req

    async def test_fixed_four_consumes_single_signature_across_deliveries(self):
        await self.grant(cc_contacts=self.ids)
        req = self.fixed()
        for episode in ('episode-1', 'episode-2'):
            req['delivery_manifest']['idempotency_key'] = episode
            res = await self.service.send(req, os.getpid())
            self.assertTrue(res['ok'], res)
            self.assertEqual(res['sent'], [self.service.load()['grants']['story']['core']['binding']['primary']] + self.cc)
        self.ask.assert_awaited_once()
        self.assertEqual(len(self.smtp.sent), 2)

    async def test_v1_does_not_cover_fixed_cc_and_fixed_does_not_cover_v1(self):
        await self.grant()
        self.assertFalse((await self.service.send(self.fixed(), os.getpid()))['ok'])
        await self.grant(cc_contacts=self.ids)
        self.assertFalse((await self.service.send(self.req, os.getpid()))['ok'])
        self.assertEqual(self.smtp.sent, [])

    async def test_changed_recipient_manifest_or_bcc_refused(self):
        await self.grant(cc_contacts=self.ids)
        for field, value in [('cc', self.cc[:3]), ('bcc', self.cc[:1]), ('to', self.cc[:1])]:
            req = self.fixed(); req[field] = value
            self.assertFalse((await self.service.send(req, os.getpid()))['ok'])
        req = self.fixed(); req['delivery_manifest']['cc'] = self.cc[:3]
        self.assertFalse((await self.service.send(req, os.getpid()))['ok'])
        self.assertEqual(self.smtp.sent, [])

    async def test_contact_drift_invalidates_and_new_list_requires_card(self):
        await self.grant(cc_contacts=self.ids)
        await self.grant(cc_contacts=self.ids[:3])
        self.assertEqual(self.ask.await_count, 2)
        b = contacts.read(self.st); contacts.get(b, self.ids[0])['emails'][0]['v'] = 'changed@example.test'
        contacts.write(self.st, b)
        self.assertEqual((await self.service.send(self.fixed(), os.getpid()))['why'], 'grant_required')
        self.assertEqual(self.smtp.sent, [])

    async def test_bad_contact_sets_never_prompt(self):
        for ids in (['missing'], self.ids + ['extra'], self.ids[:1]*2, ['owner']):
            res = await self.service.control({'op':'grant','workflow_id':'story','cc_contacts':ids}, os.getpid())
            self.assertFalse(res['ok'], res)
        self.ask.assert_not_awaited()

    async def test_deleted_contact_while_card_waits_returns_refusal(self):
        async def changed(*args, **kw):
            ans = await self.sign(*args, **kw)
            b = contacts.read(self.st)
            b['contacts'] = [x for x in b['contacts'] if x['id'] != self.ids[0]]
            contacts.write(self.st, b)
            return ans
        self.ask.side_effect = changed
        res = await self.service.control({'op':'grant','workflow_id':'story','cc_contacts':self.ids}, os.getpid())
        self.assertEqual(res['why'], 'grant_required')
        self.assertEqual(self.smtp.sent, [])
