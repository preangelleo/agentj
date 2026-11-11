"""Real Host answer handoff across asynchronous operation reservation; no tools execute."""
import _hermetic
import asyncio
import copy
import threading
import unittest
import test_p541_approval_review as fixture
from _listed_cards import listed



# Each test owns its first-init preferences; another case's root is not authority.
import os as _cfg_os
import tempfile as _cfg_tmp
from unittest.mock import patch as _cfg_patch

def _isolate_config(test):
    config = _cfg_tmp.TemporaryDirectory(prefix="p0300-config-")
    test.addCleanup(config.cleanup)
    env = _cfg_patch.dict(_cfg_os.environ, {"XDG_CONFIG_HOME": config.name})
    env.start(); test.addCleanup(env.stop)

class AnswerRace(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _isolate_config(self)
        # 0.30.7 ADR-0307-offlist: off-list Bash no longer cards; the subject is the answer/reservation race, so the
        # vehicle is a listed (tap) card again.
        listed(self, 'Bash')
        await fixture.Review.asyncSetUp(self)
    open = fixture.Review.open
    stop = fixture.Review.stop

    async def race(self, change):
        task, ask = await self.open()
        entered, release = threading.Event(), threading.Event()
        class Ledger:
            def reserve(self, operation, rid):
                entered.set()
                if not release.wait(3):
                    raise OSError('fixture deadline')
                return True
        self.h._approval_operation_once = Ledger()
        answer = asyncio.create_task(self.h._app(self.s, self.ph.answer(self.h._ask_msg(ask, self.s), True)))
        self.addAsyncCleanup(asyncio.gather, answer, return_exceptions=True)
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            change(ask)
        finally:
            release.set()
        await asyncio.wait_for(answer, 2)
        self.assertFalse(ask.fut.done(), 'old approval must not resolve the tool request')
        refused = [o for _, o in self.sent if o.get('t') == 'answer_refused']
        self.assertTrue(refused, self.sent)
        self.assertEqual(refused[-1]['why'], 'authority_changed')
        self.assertFalse(task.done())

    async def test_clear_during_reservation_refuses_old_approval(self):
        await self.race(lambda ask: setattr(self.h.hist, 'epoch', self.h.hist.epoch + 1))

    async def test_same_agent_new_task_during_reservation_refuses_old_approval(self):
        await self.race(lambda ask: setattr(self.h, 'task_epoch', 'new-host-task-epoch'))

    async def test_same_task_replaced_source_during_reservation_refuses_old_approval(self):
        def change(ask):
            origin = copy.deepcopy(self.agent.cur_send.approval_origin)
            origin['root_message_id'] += 1
            self.agent.cur_send.approval_origin = origin
        await self.race(change)
