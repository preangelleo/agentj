"""Display receipts stay bound to one live paired question delivery."""
import _hermetic  # noqa: F401
import asyncio
import tempfile
import time
import unittest
from unittest.mock import patch

from agentj import provenance, serve
from test_l1 import Phone, _host, _state


class QuestionReceipt(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.st = _state(tmp.name)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.first, self.other, self.old = [self.pair(cid, cap) for cid, cap in enumerate((True, True, False), 1)]
        self.question = serve.Question('a' * 32, [], 'test', time.monotonic() + 100,
                                       asyncio.get_running_loop().create_future())
        self.host.questions[self.question.qid] = self.question
        output = patch.object(self.st, 'log', wraps=self.st.log)
        self.log = output.start()
        self.addCleanup(output.stop)
        await self.host._deliver_observed_question(self.question)
        self.cards = dict(self.sent)
        self.msg = {'t': 'card_receipt', 'kind': 'question', 'id': self.question.qid,
                    'n': self.cards[self.first.cid]['delivery']['n'], 'phase': 'received'}

    def pair(self, cid, cap):
        phone = Phone(self.st)
        s = serve.Session(cid=cid, state='ready', device=phone.did, pub=phone.pub,
                          sign_pub=self.st.sign_key(phone.did), p33=True, card_receipt=cap,
                          provenance=provenance.phone())
        self.host.sessions[cid] = s
        return s

    def phases(self):
        return [item.kwargs['phase'] for item in self.log.call_args_list if item.args == ('question_delivery',)]

    async def test_current_session_stage_order_dedup_and_no_approval(self):
        self.assertNotIn('delivery', self.cards[self.old.cid])
        self.assertNotEqual(self.msg['n'], self.cards[self.other.cid]['delivery']['n'])
        self.assertEqual(self.cards[self.first.cid]['delivery']['ttl'], 60)
        self.assertEqual(self.phases(), ['frame_written', 'frame_written'])
        await self.host.on_p33(self.first, 'card_receipt', {**self.msg, 'phase': 'visible'})
        await self.host.on_p33(self.other, 'card_receipt', self.msg)
        await self.host.on_p33(self.first, 'card_receipt', {**self.msg, 'sig': 'extra'})
        self.assertEqual(len(self.phases()), 2)
        self.first.fg = False
        for _ in range(2):
            await self.host.on_p33(self.first, 'card_receipt', self.msg)
        await self.host.on_p33(self.first, 'card_receipt', {**self.msg, 'phase': 'visible'})
        self.assertEqual(self.phases()[-1], 'received')
        self.first.fg = True
        for _ in range(2):
            await self.host.on_p33(self.first, 'card_receipt', {**self.msg, 'phase': 'visible'})
        self.assertEqual(self.phases(), ['frame_written', 'frame_written', 'received', 'visible'])
        self.assertFalse(self.question.fut.done())

    async def test_expiry_pairing_revision_and_exact_session(self):
        original = self.host.sessions[self.first.cid]
        self.host.sessions[self.first.cid] = self.other
        await self.host.on_p33(self.first, 'card_receipt', self.msg)
        self.host.sessions[self.first.cid] = original
        self.st.add_device(self.first.pub, 'again', self.first.sign_pub)
        await self.host.on_p33(self.first, 'card_receipt', self.msg)
        with patch('agentj.serve.time.monotonic', return_value=self.question.deadline):
            await self.host.on_p33(self.other, 'card_receipt', {**self.msg, 'n': self.cards[self.other.cid]['delivery']['n']})
        self.assertEqual(self.phases(), ['frame_written', 'frame_written'])

    async def test_retry_keeps_nonce_and_excludes_visible_or_replaced_connections(self):
        self.first.fg = True
        self.host._question_receipt(self.first, self.msg)
        self.host._question_receipt(self.first, {**self.msg, 'phase': 'visible'})
        self.sent.clear()
        other_nonce = self.cards[self.other.cid]['delivery']['n']
        targets = [(self.first, self.msg['n']), (self.other, other_nonce), (self.old, 'b' * 32)]
        with patch('agentj.serve.asyncio.sleep'):
            await self.host._retry_observed_question(self.question, targets)
        self.assertEqual(len(self.sent), 1)
        cid, message = self.sent.pop()
        self.assertEqual(cid, self.other.cid)
        self.assertEqual(message['delivery']['n'], other_nonce)
        self.assertLessEqual(message['delivery']['ttl'], self.cards[self.other.cid]['delivery']['ttl'])
        self.host.sessions[self.other.cid] = self.first
        with patch('agentj.serve.asyncio.sleep'):
            await self.host._retry_observed_question(self.question, targets)
        self.assertEqual(self.sent, [])
