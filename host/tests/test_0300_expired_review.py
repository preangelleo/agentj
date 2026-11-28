"""Actual list adapter terminal futures and signed owner renewal; no tools execute."""
import _hermetic
import asyncio
import unittest
from agentj import approval_review as review, approvals, wire
import test_p541_approval_review as fixture
from _listed_cards import listed


class TerminalReview(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 0.30.7 ADR-0307-offlist: off-list Bash no longer cards; these tests are about review/expiry card mechanics,
        # so the vehicle is a listed (tap) card again.
        listed(self, 'Bash')
        await fixture.Review.asyncSetUp(self)
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
        another = self.intent(old, 'renew')
        await review.on_phone(self.h, self.s, another)
        second_reply = self.messages('approval_review_result')[-1]
        self.assertEqual(second_reply['event_id'], another['event_id'])
        self.assertEqual(second_reply['queued_event_id'], obj['event_id'])
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

    async def test_signed_renew_refuses_changed_native_thread_and_echoes_its_receipt_id(self):
        self.h.ask_ttl = .03
        self.agent.sid = 'native-original'
        task, old = await self.open('printf fixture-other-thread')
        self.assertEqual((await asyncio.wait_for(task, 2))['reason'], 'timeout')
        self.agent.sid = 'native-replacement'
        obj = self.intent(old, 'renew')
        await review.on_phone(self.h, self.s, obj)
        reply = self.messages('approval_review_result')[-1]
        self.assertEqual((reply['ok'], reply.get('why'), reply.get('event_id')),
                         (False, 'authority_changed', obj['event_id']))
        self.agent.submit.assert_not_called()

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

    async def test_late_signed_allow_gets_the_actual_terminal_receipt(self):
        self.h.ask_ttl = .03
        gone = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self.h.ask('Bash', {'command': 'printf fixture-expiry-receipt'},
                                            gone=gone, _harness_permission=True))
        for _ in range(200):
            if self.h.asks or task.done():
                break
            await asyncio.sleep(.005)
        self.assertFalse(task.done())
        ask = next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.stop, task, ask)
        answer = self.ph.answer(self.h._ask_msg(ask, self.s), True)
        self.assertEqual((await asyncio.wait_for(task, 2))['reason'], 'timeout')
        self.sent.clear()
        await self.h._answer(self.s, answer)
        self.assertEqual(self.messages('ask_done'), [
            {'t': 'ask_done', 'id': ask.rid, 'result': 'deny', 'reason': 'timeout', 'renewable': True}])
        self.assertFalse([r for r in approvals.read_log(self.st) if r.get('decision') == 'allow'])

    async def test_signed_question_options_only_answer_a_live_native_request(self):
        self.s.p33 = True
        qs = [{'q': 'Is this correct?', 'h': '', 'm': False,
               'o': [{'l': '正确', 'd': ''}, {'l': '不对', 'd': ''}]}]
        for option, cancel_before_resume in ((1, False), (2, False), (1, True), (2, True)):
            gone = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(self.h.question(qs, gone))
            await asyncio.sleep(0)
            q = next(iter(self.h.questions.values()))
            picks = [[option]]
            sig = self.ph.sk.sign(approvals.question_message(self.h.channel, self.ph.did, q.qid, 'answer', qs, picks))
            self.h._q_answer(self.s, {'t': 'q_answer', 'id': q.qid, 'pick': picks, 'sig': wire.b64u(sig)})
            if cancel_before_resume:
                gone.set_result(None)
            self.assertEqual(await asyncio.wait_for(task, 2), ('gone', None) if cancel_before_resume else ('answer', picks))
            await asyncio.sleep(0)
            self.assertEqual(self.messages('question_done')[-1]['result'], 'gone' if cancel_before_resume else 'answered')

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
        self.assertNotIn('renewable', self.messages('ask_done')[-1])
        await review.on_phone(self.h, self.s, self.intent(ask, 'renew'))
        self.assertFalse(self.messages('approval_review_result')[-1]['ok'])
        self.agent.submit.assert_not_called()
