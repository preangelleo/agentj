"""Actual list adapter terminal futures and signed owner renewal; no tools execute."""
import _hermetic
import asyncio
import unittest
from agentj import approval_review as review, approvals
import test_p541_approval_review as fixture


class TerminalReview(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.Review.asyncSetUp
    open = fixture.Review.open
    stop = fixture.Review.stop
    intent = fixture.Review.intent

    def messages(self, kind):
        return [obj for _, obj in self.sent if obj.get('t') == kind]

    async def test_actual_timeout_signed_renewal_restarts_once(self):
        self.h.ask_ttl = .03
        task, old = await self.open('printf fixture-timeout')
        result = await asyncio.wait_for(task, 2)
        self.assertEqual((result['behavior'], result['reason']), ('deny', 'timeout'))
        self.assertEqual(old.review_state, 'expired')
        self.assertTrue(old.review_waiter_ended)
        self.assertTrue(old.fut.done(), 'terminal adapter must complete its future before signed renewal')
        self.assertEqual(old.fut.result(), ('deny', None, None, None))
        self.assertNotIn(old.rid, self.h.asks)
        obj = self.intent(old, 'renew')
        await review.on_phone(self.h, self.s, obj)
        reply = self.messages('approval_review_result')[-1]
        self.assertEqual((reply['ok'], reply['state']), (True, 'restart_requested'))
        self.assertEqual(self.agent.submit.call_count, 1)
        await review.on_phone(self.h, self.s, obj)
        await review.on_phone(self.h, self.s, self.intent(old, 'renew'))
        self.assertEqual(self.agent.submit.call_count, 1, 'replayed owner intent cannot queue duplicate work')
        self.agent.cur_send = self.agent.submit.call_args.args[0]
        self.h.ask_ttl = 2
        restarted, fresh = await self.open('printf fixture-timeout')
        self.assertNotEqual(fresh.rid, old.rid)
        self.assertFalse(fresh.require_passkey, 'native permission is a signed tap, not heuristic UV')
        self.assertEqual(old.review_state, 'expired')
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(fresh, self.s), True))
        self.assertEqual((await asyncio.wait_for(restarted, 2))['behavior'], 'allow')
        records = [r for r in approvals.read_log(self.st) if r.get('reason') == 'device']
        self.assertEqual(len(records), 1)
        self.assertEqual(approvals.check_record(self.st, records[0]), 'ok')

    async def test_actual_timeout_rejects_late_signed_allow(self):
        self.h.ask_ttl = .03
        task, ask = await self.open('printf fixture-late')
        answer = self.ph.answer(self.h._ask_msg(ask, self.s), True)
        result = await asyncio.wait_for(task, 2)
        await self.h._answer(self.s, answer)
        self.assertEqual(result['behavior'], 'deny')
        self.assertTrue(ask.fut.done())
        self.assertEqual(ask.fut.result(), ('deny', None, None, None))
        self.assertEqual(ask.review_state, 'expired')
        self.assertFalse([r for r in approvals.read_log(self.st) if r.get('decision') == 'allow'])

    async def test_question_remains_pending_until_real_signed_decision(self):
        task, ask = await self.open('printf fixture-question')
        deadline = ask.deadline
        await review.on_phone(self.h, self.s, self.intent(ask, 'question'))
        self.assertEqual((await asyncio.wait_for(task, 2))['behavior'], 'deny')
        self.assertTrue(ask.parked)
        self.assertFalse(ask.fut.done(), 'question is not a terminal cancellation')
        self.assertEqual(ask.review_state, 'questioned')
        await review.on_phone(self.h, self.s, self.intent(ask, 'reopen'))
        self.assertEqual(ask.deadline, deadline)
        self.assertFalse(ask.fut.done())
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask, self.s), True))
        self.assertEqual(ask.fut.result()[0], 'allow')

    async def test_actual_request_gone_is_terminal_not_renewable_expiry(self):
        gone = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self.h.ask('Bash', {'command': 'printf fixture-gone'},
                                            gone=gone, _harness_permission=True))
        for _ in range(200):
            if self.h.asks or task.done():
                break
            await asyncio.sleep(.005)
        self.assertFalse(task.done())
        ask = next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.stop, task, ask)
        gone.set_result(None)
        result = await asyncio.wait_for(task, 2)
        self.assertEqual(result['reason'], 'agent_gone')
        self.assertTrue(ask.fut.done())
        self.assertEqual(ask.fut.result(), ('deny', None, None, None))
        await review.on_phone(self.h, self.s, self.intent(ask, 'renew'))
        self.assertFalse(self.messages('approval_review_result')[-1]['ok'])
        self.agent.submit.assert_not_called()
