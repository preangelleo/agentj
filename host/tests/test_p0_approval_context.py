"""P0 actual Host cross-context phone decisions; no operation executes."""
import _hermetic
import copy
import unittest
from agentj import approval_review as review
import test_p541_approval_review as fixture

class ContextSubmission(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.Review.asyncSetUp
    open=fixture.Review.open
    stop=fixture.Review.stop

    async def scoped(self):
        child = copy.deepcopy(self.origin)
        child['chain'] = [{'kind':'subagent','id':'synthetic-child'}]
        with review.origin_scope(child):
            task, ask = await self.open()
        self.assertNotEqual(ask.review_request_source, review.digest(review.snapshot(self.h)))
        return task, ask

    async def test_hook_context_valid_signed_uv_allow(self):
        task, ask = await self.scoped()
        await self.h._app(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        self.assertTrue(ask.fut.done(),self.sent)
        self.assertEqual((await task)['behavior'],'allow')

    async def test_hook_context_valid_signed_deny(self):
        task, ask = await self.scoped()
        await self.h._app(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),False))
        self.assertTrue(ask.fut.done(),self.sent)
        self.assertEqual((await task)['behavior'],'deny')

    async def test_live_root_change_before_submission_refused(self):
        task, ask = await self.scoped()
        self.agent.cur_send.approval_origin = {**self.origin, 'root_message_id': 99999}
        await self.h._app(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        self.assertFalse(ask.fut.done())
        self.assertEqual(self.sent[-1][1]['why'],'authority_changed')

    async def test_live_cwd_change_before_submission_refused(self):
        task, ask = await self.scoped()
        self.h.agent_cfg = {**self.h.agent_cfg, 'dir':'/synthetic/replaced-work'}
        await self.h._app(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        self.assertFalse(ask.fut.done())
        self.assertEqual(self.sent[-1][1]['why'],'authority_changed')

    async def test_live_desktop_turn_change_before_submission_refused(self):
        task, ask = await self.scoped()
        self.h.desktop_turn = 99999
        await self.h._app(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        self.assertFalse(ask.fut.done())
        self.assertEqual(self.sent[-1][1]['why'],'authority_changed')

    async def test_live_same_root_source_trust_change_refused(self):
        task, ask = await self.scoped()
        self.agent.cur_send.approval_origin = {**self.origin, 'verified':False, 'suspicious':True}
        await self.h._app(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        self.assertFalse(ask.fut.done())
        self.assertEqual(self.sent[-1][1]['why'],'authority_changed')

    async def test_hook_context_forged_signature_refused(self):
        task, ask = await self.scoped()
        obj=self.ph.answer(self.h._ask_msg(ask,self.s),True)
        obj['sig']='AA'
        await self.h._app(self.s,obj)
        self.assertFalse(ask.fut.done())
        self.assertEqual(self.sent[-1][1]['why'],'bad_signature')

    async def test_hook_context_missing_uv_refused(self):
        task, ask = await self.scoped()
        obj=self.ph.answer(self.h._ask_msg(ask,self.s),True)
        obj.pop('fa',None)
        await self.h._app(self.s,obj)
        self.assertFalse(ask.fut.done())
        self.assertEqual(self.sent[-1][1]['why'],'passkey_required')

class ReservationRelease(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from agentj.bounded_plan import OperationOnce
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.ledger=OperationOnce(Path(tmp.name)/'once.sqlite3')

    def test_exact_unresolved_rid_can_retry(self):
        self.assertTrue(self.ledger.reserve('operation','first'))
        self.ledger.release('operation','first')
        self.assertTrue(self.ledger.reserve('operation','first'))

    def test_other_rid_cannot_release(self):
        self.assertTrue(self.ledger.reserve('operation','first'))
        self.ledger.release('operation','other')
        self.assertTrue(self.ledger.consumed('operation'))
        self.assertFalse(self.ledger.reserve('operation','other'))

    def test_terminal_cannot_release_even_after_reopen(self):
        from agentj.bounded_plan import OperationOnce
        self.assertTrue(self.ledger.reserve('operation','first'))
        for terminal in ('completed','denied','revoked','unknown'):
            self.ledger.finish('operation','first',terminal)
            self.ledger.release('operation','first')
            reopened=OperationOnce(self.ledger.path)
            reopened.release('operation','first')
            self.assertTrue(reopened.consumed('operation'))
            self.assertFalse(reopened.reserve('operation','first'))

class LiveAuthorityRace(unittest.IsolatedAsyncioTestCase):
    import test_p541_answer_race as _race
    asyncSetUp=fixture.Review.asyncSetUp
    open=fixture.Review.open
    stop=fixture.Review.stop
    race=_race.AnswerRace.race

    async def test_cwd_change_during_reserve_refused(self):
        await self.race(lambda ask:setattr(self.h,'agent_cfg',{**self.h.agent_cfg,'dir':'/synthetic/new-work'}))
