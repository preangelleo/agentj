"""Recovered P0 reservation/retry regressions, adapted to ADR-0307 explicit cards.

The listed Bash is a test-only tap vehicle; no implicit UV requirement is restored.
Real Host and OperationOnce, synthetic paired phone; no tools execute.
"""
import _hermetic
import asyncio
import pathlib
import tempfile
import threading
import unittest

from agentj.bounded_plan import OperationOnce
from _listed_cards import listed
import test_p541_approval_review as fixture


class AnswerReservationRelease(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        listed(self, 'Bash')

    asyncSetUp = fixture.Review.asyncSetUp
    open = fixture.Review.open
    stop = fixture.Review.stop

    def refused(self):
        return [o.get('why') for _, o in self.sent if o.get('t') == 'answer_refused']

    async def test_refused_answer_releases_reservation_so_retry_allows(self):
        task, ask = await self.open()
        real = OperationOnce(pathlib.Path(self.tmp.name) / 'once.sqlite3')
        entered, release = threading.Event(), threading.Event()

        class Ledger:
            first = True
            def reserve(self, operation, rid):
                ok = real.reserve(operation, rid)
                if Ledger.first:
                    Ledger.first = False
                    entered.set()
                    release.wait(3)
                return ok
            def release(self, operation, rid):
                return real.release(operation, rid)
            def finish(self, *a):
                return real.finish(*a)
        self.h._approval_operation_once = Ledger()
        epoch = self.h.hist.epoch
        answer = asyncio.create_task(self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask, self.s), True)))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.h.hist.epoch = epoch + 1             # authority changes mid-reservation: this answer is refused
        finally:
            release.set()
        await asyncio.wait_for(answer, 2)
        self.assertEqual(self.refused(), ['authority_changed'])
        self.assertFalse(ask.fut.done())
        self.h.hist.epoch = epoch
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask, self.s), True))
        await asyncio.sleep(0)
        self.assertEqual(self.refused(), ['authority_changed'], 'retry must not read operation_already_consumed')
        self.assertEqual(ask.fut.result()[0], 'allow')


class LedgerRelease(unittest.TestCase):
    def test_release_only_drops_own_executing_row(self):
        with tempfile.TemporaryDirectory() as d:
            led = OperationOnce(pathlib.Path(d) / 'once.sqlite3')
            self.assertTrue(led.reserve('op', 'r1'))
            led.release('op', 'other')
            self.assertFalse(led.reserve('op', 'r2'))
            led.release('op', 'r1')
            self.assertTrue(led.reserve('op', 'r2'))
            led.finish('op', 'r2', 'completed')
            led.release('op', 'r2')
            self.assertTrue(led.consumed('op'))


if __name__ == '__main__':
    unittest.main()
