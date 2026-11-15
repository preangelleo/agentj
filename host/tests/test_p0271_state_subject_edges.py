"""Security follow-up: channel binding and off-list awaited audit boundaries."""
import _hermetic
import asyncio
import json
import threading
import unittest
from unittest import mock
from agentj import application_ingress, approval_offlist, approvals, handoff, list_approval, provenance
import test_p0271_state_subject_current as fixture


class StateSubjectEdges(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StateSubject.asyncSetUp
    open = fixture.StateSubject.open
    close = fixture.StateSubject.close
    review_intent = fixture.StateSubject.review_intent
    question_approve = fixture.StateSubject.question_approve
    other_state = fixture.StateSubject.other_state
    replace_root = fixture.StateSubject.replace_root

    def change_channel(self):
        config = self.st.config()
        config['channel'] = 'synthetic-replaced-channel'
        self.st.config_path.write_text(json.dumps(config))

    def audit(self, state):
        return state.approvals_path.read_bytes() if state.approvals_path.exists() else b''

    async def test_config_channel_change_invalidates_genuine_uv_card(self):
        task, card = await self.open()
        self.change_channel()
        before = self.audit(self.st)
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(card, self.s), True))
        if not card.fut.done():
            card.fut.set_result(('deny', None, None, None))
        self.assertEqual(((await task)['behavior'], self.audit(self.st)), ('deny', before))

    async def test_host_channel_change_invalidates_subject(self):
        subject = list_approval.StateSubject.capture(self.h)
        self.h.channel = 'synthetic-replaced-channel'
        self.assertFalse(subject.live(self.h))

    async def test_config_channel_change_invalidates_current_question_continuation(self):
        card, resume = await self.question_approve('agentj fn run', {'name':'synthetic'})
        self.change_channel()
        self.assertIsNone(list_approval.take_resume(self.h, card.tool, card.tool_input, True,
                                                   card.list_resume_context, None))
        self.assertFalse(self.h.__dict__.get('_uv_list_resumes'))

    async def offlist_race(self, target, replace):
        entered, release = threading.Event(), threading.Event()
        real = getattr(approval_offlist, target)
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise OSError('synthetic off-list barrier')
            return real(*args, **kwargs)
        task = None
        original = self.h.st
        try:
            with mock.patch.object(handoff, 'owner_side', return_value=False), \
                    mock.patch.object(approval_offlist, target, side_effect=blocked):
                task = asyncio.create_task(self.h.ask('Bash', {'command':'pwd'}))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                replace()
                replacement = self.h.st
                before = self.audit(replacement)
                release.set()
                result = await task
                self.assertEqual((result['behavior'], self.audit(replacement)), ('deny', before))
                self.assertEqual(result.get('reason'), 'state_authority_changed')
                self.assertFalse(self.h.asks)
        finally:
            release.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.h.st = original

    async def test_offlist_decide_await_cannot_retarget_to_another_state(self):
        other = self.other_state()
        await self.offlist_race('decide', lambda: setattr(self.h, 'st', other))

    async def test_offlist_record_thread_cannot_retarget_to_another_state(self):
        other = self.other_state()
        await self.offlist_race('_record', lambda: setattr(self.h, 'st', other))

    async def test_own_handoff_read_await_cannot_inherit_replaced_root(self):
        cfg = {'dir': str(self.st.root.parent / 'synthetic-work'), 'session_mode':'own'}
        path = handoff.switch_path(cfg)
        path.parent.mkdir(parents=True)
        path.write_text('synthetic handoff')
        entered, release = threading.Event(), threading.Event()
        real = handoff.own_read
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise OSError('synthetic own-read barrier')
            return real(*args, **kwargs)
        task = None
        try:
            with mock.patch.object(handoff, 'owner_side', return_value=True), \
                    mock.patch.object(self.h, '_switch_cfg', return_value=cfg), \
                    mock.patch.object(handoff, 'own_read', side_effect=blocked):
                task = asyncio.create_task(self.h.ask('Read', {'file_path':str(path)}))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.replace_root()
                before = self.audit(self.st)
                release.set()
                result = await task
                self.assertEqual((result['behavior'], self.audit(self.st)), ('deny', before))
        finally:
            release.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_ordinary_host_allow_rechecks_after_admission(self):
        real = application_ingress.ordinary
        def admitted(*args):
            result = real(*args)
            self.assertEqual(result['behavior'], 'allow')
            self.replace_root()
            return result
        with provenance.paired_request(lambda: self.h._paired_live(self.s)):
            with application_ingress.paired(self.h, self.s, {'t':'synthetic-ordinary'}):
                with mock.patch.object(application_ingress, 'ordinary', side_effect=admitted):
                    result = await self.h.ask('agentj config', {'changes':{'language':'en'}})
        self.assertEqual(result['behavior'], 'deny')
        self.assertEqual(result.get('reason'), 'state_authority_changed')

    async def test_unchanged_offlist_owner_allows_and_audits_without_card(self):
        answer = await self.h.ask('Bash', {'command':'pwd'})
        self.assertEqual(answer['behavior'], 'allow')
        self.assertFalse(self.h.asks)
        rows = approvals.read_log(self.st)
        self.assertTrue(rows)
        self.assertEqual((rows[-1]['channel'], rows[-1]['decision']), (self.h.channel, 'allow'))

    async def test_unrelated_config_change_does_not_cancel_current_uv(self):
        task, card = await self.open()
        config = self.st.config()
        config['synthetic_non_authority_metadata'] = 'changed'
        self.st.config_path.write_text(json.dumps(config))
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(card, self.s), True))
        self.assertEqual((await task)['behavior'], 'allow')
        self.assertEqual(approvals.read_log(self.st)[-1]['channel'], self.h.channel)

    async def test_malformed_current_channel_record_fails_closed(self):
        task, card = await self.open()
        self.st.config_path.write_text('[]')
        before = self.audit(self.st)
        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(card, self.s), True))
        if not card.fut.done():
            card.fut.set_result(('deny', None, None, None))
        self.assertEqual(((await task)['behavior'], self.audit(self.st)), ('deny', before))
