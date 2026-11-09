"""Card construction/transport posting alone cannot prove a remote displayed UI."""
import _hermetic
import asyncio
import unittest
import test_p541_approval_review as fixture


class DisplayReceipt(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.Review.asyncSetUp
    stop = fixture.Review.stop

    async def pending(self, receipt):
        task = asyncio.create_task(self.h.ask('Bash', {'command': 'printf receipt-fixture'},
                                            _receipt=receipt, _harness_permission=True))
        for _ in range(200):
            if self.h.asks or task.done():
                break
            await asyncio.sleep(.005)
        self.assertFalse(task.done())
        ask = next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.stop, task, ask)
        return task, ask

    async def test_online_unacknowledged_card_timeout_is_unknown_display(self):
        self.h.ask_ttl = .03
        receipt = {}
        task, _ = await self.pending(receipt)
        self.assertIsNone(receipt['approval_card_shown'])
        result = await asyncio.wait_for(task, 2)
        self.assertEqual(result['reason'], 'timeout')
        self.assertEqual(receipt, {'reason': 'timeout', 'approval_card_shown': None})

    async def test_offline_paired_card_timeout_does_not_claim_display(self):
        self.h.sessions.clear()
        self.h.ask_ttl = .03
        receipt = {}
        result = await self.h.ask('Bash', {'command': 'printf receipt-fixture'},
                                  _receipt=receipt, _harness_permission=True)
        self.assertEqual(result['reason'], 'timeout')
        self.assertEqual(receipt, {'reason': 'timeout', 'approval_card_shown': None})
        self.assertFalse(self.h.asks)

    async def test_signed_deny_reason_is_distinct_without_display_ack(self):
        receipt = {}
        task, ask = await self.pending(receipt)
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask, self.s), False))
        self.assertEqual((await task)['behavior'], 'deny')
        self.assertEqual(receipt, {'reason': 'device', 'approval_card_shown': None})

    async def test_signed_allow_cannot_invent_a_transport_display_receipt(self):
        receipt = {}
        task, ask = await self.pending(receipt)
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask, self.s), True))
        self.assertEqual((await task)['behavior'], 'allow')
        self.assertEqual(receipt, {'reason': 'device', 'approval_card_shown': None})

    async def test_no_paired_device_is_known_no_card_attempt(self):
        self.st.remove_device(self.ph.did)
        receipt = {}
        result = await self.h.ask('Bash', {'command': 'printf receipt-fixture'},
                                  _receipt=receipt, _harness_permission=True)
        self.assertEqual(result['reason'], 'no_device')
        self.assertEqual(receipt, {'reason': 'no_device', 'approval_card_shown': False})
        self.assertFalse(self.h.asks)

    async def test_invalid_input_is_known_no_card_attempt(self):
        receipt = {}
        result = await self.h.ask('Bash', {'command': {'invalid-json-set'}},
                                  _receipt=receipt, _harness_permission=True)
        self.assertEqual(result['reason'], 'invalid_input')
        self.assertEqual(receipt, {'reason': 'invalid_input', 'approval_card_shown': False})
        self.assertFalse(self.h.asks)
