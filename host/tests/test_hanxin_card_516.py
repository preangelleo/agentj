"""Workflow retry uses a real signed host card and may only narrow the original CC pool."""
import _hermetic
import asyncio
import copy
import os
from types import SimpleNamespace
from unittest.mock import patch
from agentj import policy_registry, workflow_mail as wm
from test_workflow_fixed_mail import FixedDelivery

class Retry(FixedDelivery):
    async def retry_input(self):
        await self.grant(cc_contacts=self.ids)
        req = self.fixed()
        with patch.object(self.svc, 'smtp_send', side_effect=OSError('unknown')):
            result = await self.service.send(req, os.getpid())
        self.host.scheduler.mail_scope = {'mail_admission': {'run_id':'rid','instance_id':'instance'}}
        req = copy.deepcopy(req)
        req.update(op='retry', workflow_id='story', delivery=result['delivery'], files=[],
                   retry_original_manifest=copy.deepcopy(req['delivery_manifest']))
        req['delivery_manifest']['attachments'] = []
        req['cc'] = self.cc[:2]
        req['delivery_manifest']['cc'] = self.cc[:2]
        self.ask.side_effect = None
        self.ask.return_value = {'behavior':'allow','grant_info':{'rid':'retry-card','device':'dev1'}}
        return req

    async def test_retry_narrows_cc_and_binds_exact_manifest_once(self):
        req = await self.retry_input()
        res = await self.service.retry(req, os.getpid())
        self.assertTrue(res.get('ok'), res)
        self.assertEqual(res['retry_manifest_sha256'], wm.digest(req['delivery_manifest']))
        self.assertTrue(self.ask.await_args.kwargs['always_ask'])
        self.assertTrue(self.ask.await_args.kwargs['require_passkey'])
        sent = await self.service.send(req, os.getpid())
        self.assertTrue(sent.get('ok'), sent)
        self.assertEqual(sent['sent'][1:], self.cc[:2])
        self.assertEqual((await self.service.send(req, os.getpid()))['why'], 'duplicate')

    async def test_retry_rejects_other_changes_without_card(self):
        req = await self.retry_input()
        count = self.ask.await_count
        for field, value in [('cc', self.cc + ['other@example.test']), ('format','html'), ('body_sha256','c'*64), ('binding_sha256','d'*64)]:
            changed = copy.deepcopy(req); changed['delivery_manifest'][field] = value
            res = await self.service.retry(changed, os.getpid())
            self.assertFalse(res.get('ok'), res)
        self.assertEqual(self.ask.await_count, count)

    async def test_retry_reports_pre_card_refusal_separately(self):
        req = await self.retry_input()
        async def refused(*args, **kw):
            kw['_receipt'].update(reason='unknown_action', approval_card_shown=False)
            return {'behavior':'deny','message':'Policy refused before any card'}
        self.ask.side_effect = refused
        result = await self.service.retry(req, os.getpid())
        self.assertEqual(result['why'], 'approval_not_shown')
        self.assertEqual(result['approval_reason'], 'unknown_action')
        self.assertFalse(result['approval_card_shown'])
        self.assertNotIn('retry_allowed', self.service.load()['deliveries'][req['delivery']])

    async def test_real_host_retry_card_reaches_paired_session(self):
        from test_l1 import Phone, _host, _ready
        del self.st.sign_key; del self.st.passkey_of_device
        host = _host(self.st, [])
        phone = Phone(self.st, 'Workflow owner', with_passkey=True)
        session = _ready(host, phone)
        host.agent = SimpleNamespace(kind='codex', native_source=None, status='idle')
        host.agent_cfg = {'dir':str(self.work),'kind':'codex'}
        before = set(asyncio.all_tasks())
        pending = asyncio.create_task(host.ask('agentj mail workflow-retry', {'summary':'exact retry'}, batch=False,
            always_ask=True, require_passkey=True, grant_info=True, _company_proposal=policy_registry.HOST_CARD))
        try:
            for _ in range(100):
                if host.asks or pending.done(): break
                await asyncio.sleep(.005)
            self.assertTrue(host.asks, pending.result() if pending.done() else 'no card')
            msg = host._ask_msg(next(iter(host.asks.values())), session)
            self.assertTrue(msg['passkey_required'])
            await host._answer(session, phone.answer(msg, True))
            self.assertEqual((await asyncio.wait_for(pending, 2))['behavior'], 'allow')
        finally:
            host.stopping.set()
            remaining = set(asyncio.all_tasks()) - before - {asyncio.current_task()}
            for task in remaining: task.cancel()
            if remaining: await asyncio.gather(*remaining, return_exceptions=True)
