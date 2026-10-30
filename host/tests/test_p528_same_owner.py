"""P528 real bilateral relationship with synthetic peers, never a real account."""
import test_p405_same_owner as existing
from test_p71_peer_service import pmsg, settle
from unittest.mock import patch


class Lifecycle(existing.SameOwnerTests):
    async def test_visible_task_and_automatic_receipt(self):
        await self.confirm()
        message = pmsg('检查合成测试服务')
        self.assertTrue(await self.svc.same_owner.forward(self.b, message))
        send = self.sends[-1]
        self.assertEqual(self.pages[-1][0]['k'], 'task')
        self.assertTrue(self.pages[-1][0]['name'])
        self.assertEqual(self.pages[-1][0]['text'], message['text'])
        self.host.hist_update = lambda *args, **kw: None
        self.host.hist = type('Hist', (), {'get':lambda _, tid: {'reply':{'text':'合成检查完成'}}})()
        self.host.cur_failed = False
        self.host.agent.halting = False
        with patch.object(self.svc, 'tell', return_value={'ok':True}) as reply:
            await send.completed()
            reply.assert_awaited_once_with(self.b, '合成检查完成', reply_to=message['mid'])
            await send.completed()
            reply.assert_awaited_once()

    async def test_completion_revocation_never_sends(self):
        await self.confirm()
        message = pmsg('检查服务')
        self.assertTrue(await self.svc.same_owner.forward(self.b, message))
        send = self.sends[-1]
        self.svc.same_owner.consent(self.b, False)
        self.host.hist_update = lambda *args, **kw: None
        with patch.object(self.svc, 'tell') as reply:
            await send.completed()
            reply.assert_not_called()


    async def test_real_receipt_uses_durable_guard_and_never_repeats(self):
        await self.confirm()
        message = pmsg('检查服务')
        self.assertTrue(await self.svc.same_owner.forward(self.b, message))
        send = self.sends[-1]
        self.host.hist = type('Hist', (), {'get':lambda _, tid: {'reply':{'text':'检查完成'}}})()
        self.host.cur_failed = False
        self.host.agent.halting = False
        await send.completed()
        replies = self.net.of('pmsg')
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][1]['irt'], message['mid'])
        self.assertTrue(replies[0][1]['end'])
        self.assertEqual(self.svc.guards.row(self.b, message['mid'])['reply_state'], 'done')
        await send.completed()
        self.assertEqual(len(self.net.of('pmsg')), 1)

    async def test_disconnected_agent_has_visible_pending_card(self):
        await self.confirm()
        self.host.agent = None
        updates=[]
        self.host.hist_update=lambda *a,**kw: updates.append((a,kw))
        self.assertFalse(await self.svc.same_owner.forward(self.b, pmsg('检查服务')))
        self.assertNotIn('end', updates[-1][1])
        self.assertTrue(self.svc.same_owner.row(self.b).get('queue'))
        self.assertIn('未连接', updates[-1][1]['text'])

    async def test_failed_turn_returns_failure_not_success(self):
        await self.confirm()
        message=pmsg('检查服务')
        await self.svc.same_owner.forward(self.b,message)
        self.host.hist=type('Hist',(),{'get':lambda _,tid:{'reply':{'text':'partial'}}})()
        self.host.cur_failed=True
        self.host.agent.halting=False
        with patch.object(self.svc,'tell',return_value={'ok':True}) as reply:
            await self.sends[-1].completed()
            self.assertIn('未完成', reply.call_args.args[1])

    async def test_agent_run_calls_receipt_after_turn(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from agentj.agent import Agent
        from agentj.compose import Send
        events=[]
        host=SimpleNamespace(agent_status=lambda _:None, agent_turn_start=lambda *_:events.append('start'),
                             agent_turn_end=lambda:events.append('end'), agent_notice=lambda _:None)
        class TestAgent(Agent):
            async def turn(self, text): events.append('turn')
            async def watched(self, coro): return await coro
            async def _identity_refresh(self): pass
        agent=TestAgent(host, {'_workflow_ceo':True})
        send=Send('same-owner','test',1,text='synthetic task')
        async def completed(): events.append('receipt')
        send.completed=completed
        agent.submit(send)
        with patch('agentj.fallback.after_failure',AsyncMock()), patch('agentj.compactprep.decorate',lambda *a:'synthetic'), patch('agentj.compactprep.proactive',AsyncMock()):
            task=asyncio.create_task(agent.run())
            for _ in range(20):
                if 'end' in events: break
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(events, ['start','turn','receipt','end'])
