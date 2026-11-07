"""Normal CLI settings requests retain their own task origin; no real settings apply or external calls."""
import _hermetic
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest import mock

import test_p318_approval_merge as config_fixtures
from agentj import approval_review as review, provenance, mail


class CtlOrigins(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=config_fixtures.ConfigCards.asyncSetUp
    asyncTearDown=config_fixtures.ConfigCards.asyncTearDown
    ctl=config_fixtures.ConfigCards.ctl
    settle=config_fixtures.ConfigCards.settle

    def origin(self,label):
        turn=self.host.hist_add({'k':'phone','name':'你','text':label},'','done')
        return review.remember(self.host,turn,provenance.phone())

    async def prepare(self):
        self.origins=[]
        self.main=self.origin('主线程任务99')
        self.host.agent=SimpleNamespace(cur_send=SimpleNamespace(approval_origin=self.main))
        async def ask(tool,inp,gone=None,**kw):
            self.origins.append((tool,review.snapshot(self.host)))
            fut=asyncio.get_running_loop().create_future()
            self.cards.append((inp['changes'],fut,gone))
            done,_=await asyncio.wait({fut,gone},return_when=asyncio.FIRST_COMPLETED)
            return fut.result() if fut in done else {'behavior':'deny'}
        self.host.ask=ask
        self.addAsyncCleanup(self.cancel_cards)

    async def cancel_cards(self):
        for _,fut,gone in self.cards:
            if not fut.done():fut.set_result({'behavior':'deny'})
        await asyncio.gather(*list(getattr(self.host,'_ctl_bg',())),return_exceptions=True)

    async def request(self,origin,changes):
        raw=self.p.read()[0]
        for key,value in changes.items():raw=self.p.edit(raw,key,value)
        with mock.patch('agentj.bounded_plan.caller_origin',return_value=copy.deepcopy(origin)):
            return await self.ctl({'cmd':'config_apply','raw':raw})

    async def test_different_tasks_replace_overlap_without_carrying_old_patch(self):
        await self.prepare();first=self.origin('任务A');second=self.origin('任务B')
        await self.request(first,{'agent.approvals':'native','agent.allow_docker':True})
        await self.settle(lambda:len(self.cards)==1)
        await self.request(second,{'agent.approvals':'strict'})
        self.assertTrue(await self.settle(lambda:len(self.cards)==2))
        self.assertTrue(self.cards[0][2].done())
        self.assertEqual(self.cards[-1][0],{'agent.approvals':'strict'})
        self.assertEqual([origin['root_message_id'] for _,origin in self.origins],[first['root_message_id'],second['root_message_id']])
        self.assertIsNone(review.ORIGIN.get(),'connection context must reset after response')

    async def test_same_task_can_merge_and_identical_repeat_keeps_card(self):
        await self.prepare();origin=self.origin('同一任务')
        await self.request(origin,{'agent.approvals':'native','agent.allow_docker':True})
        await self.settle(lambda:len(self.cards)==1)
        await self.request(origin,{'agent.approvals':'native','agent.allow_docker':True})
        self.assertEqual(len(self.cards),1)
        await self.request(origin,{'agent.approvals':'strict'})
        self.assertTrue(await self.settle(lambda:len(self.cards)==2))
        self.assertEqual(self.cards[-1][0],{'agent.approvals':'strict','agent.allow_docker':True})
        self.assertTrue(all(src['root_message_id']==origin['root_message_id'] for _,src in self.origins))

    async def test_unidentified_cli_request_does_not_take_main_task_source(self):
        await self.prepare()
        await self.request(None,{'agent.approvals':'native'})
        self.assertTrue(await self.settle(lambda:len(self.cards)==1))
        self.assertEqual(self.origins[-1][1],{})
        self.assertEqual(review.snapshot(self.host)['root_message_id'],self.main['root_message_id'])

    async def test_mail_cli_uses_connection_origin_and_does_not_apply(self):
        await self.prepare();origin=self.origin('独立邮件设置任务')
        base=mail.blank_config();base['address']='agent@example.invalid'
        self.host.mail=SimpleNamespace(set_config=mock.Mock())
        seen=[]
        async def ask(tool,inp,**kw):
            seen.append((tool,review.snapshot(self.host)));return {'behavior':'deny'}
        self.host.ask=ask
        with mock.patch('agentj.bounded_plan.caller_origin',return_value=origin),mock.patch.object(mail,'read_config',return_value=base):
            result=await self.ctl({'cmd':'mail_cfg','cfg':{'allow':[{'addr':'owner@example.invalid','role':'owner'}]}})
        self.assertFalse(result['ok']);self.assertEqual(seen[0][0],'agentj mail')
        self.assertEqual(seen[0][1]['root_message_id'],origin['root_message_id'])
        self.host.mail.set_config.assert_not_called()

    async def test_independent_task_scope_retains_registered_origin_and_old_scope_is_unknown(self):
        await self.prepare();origin=self.origin('独立任务42')
        review.register_task_origin(self.host,'run-42','business-task',origin)
        with provenance.task_run() as scope:
            scope['approval_task_ref']='run-42'
            source=review.snapshot(self.host)
            self.assertEqual(source['root_message_id'],origin['root_message_id'])
            self.assertEqual(source['task_record'],'workflow:business-task/run:run-42')
        with provenance.task_request({'live':True}):
            old=review.snapshot(self.host)
            self.assertFalse(old['verified']);self.assertIsNone(old['root_message_id'])
            self.assertNotEqual(old['root_message_id'],self.main['root_message_id'])
        review.register_task_origin(self.host,'run-recovered','daily-task')
        with provenance.task_request({'live':True,'approval_task_ref':'run-recovered'}):
            recovered=review.snapshot(self.host)
            self.assertFalse(recovered['verified'])
            self.assertEqual(recovered['task_record'],'workflow:daily-task/run:run-recovered')
        self.assertEqual(review.snapshot(self.host)['root_message_id'],self.main['root_message_id'])
