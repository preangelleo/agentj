"""Actual signed phone controls create their own approval source, independent of the main Agent task."""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import approval_review as review, provenance
from test_f17 import answer
import test_p408_terminal as terminal_fixture
import test_p414_vault as vault_fixture
from test_p541_special import intent


def old_main_task(rig):
    turn = rig.host.hist_add({'k': 'phone', 'name': '你', 'text': '这是主线程无关的旧任务'}, '', 'done')
    origin = review.remember(rig.host, turn, provenance.phone())
    if rig.host.agent is None:
        rig.host.agent = SimpleNamespace(kind='claude', cur_send=None, native_source=provenance.phone())
    rig.host.agent.cur_send = SimpleNamespace(source=provenance.phone(), approval_origin=origin)
    rig.host.agent.submit = mock.Mock()
    return origin


class PhoneOrigins(unittest.TestCase):
    def test_signed_terminal_open_has_new_human_action_root_through_question_reopen_deny(self):
        rig = terminal_fixture.Rig(); rig.setUp()
        self.addCleanup(rig.tearDown)
        rig.passkey(); rig.enable()
        async def run():
            old = old_main_task(rig)
            await rig.step2(await rig.step1())
            card = await rig.until(lambda: next((item for item in rig.msgs('elev') if item.get('kind') == 'login'), None))
            source = card['envelope']['source']
            self.assertEqual(source['channel'], 'phone')
            self.assertTrue(source['verified'])
            self.assertFalse(source['suspicious'])
            self.assertNotEqual(source['root_message_id'], old['root_message_id'])
            self.assertEqual(source['owner_action'], {'action': 'terminal_login', 'device': rig.phone.did, 'mode': 'line'})
            self.assertIn('解锁这台遥控器', source['text'])
            self.assertNotIn(terminal_fixture.GOOD, json.dumps(source, ensure_ascii=False))
            await rig.host._app(rig.s, intent(rig.phone, card, 'question'))
            questioned = rig.msgs('approval_review_result')[-1]['ask']
            self.assertEqual(questioned['review_state'], 'questioned')
            await rig.host._app(rig.s, intent(rig.phone, questioned, 'reopen'))
            reopened = rig.msgs('approval_review_result')[-1]['ask']
            self.assertEqual(reopened['id'], card['id'])
            self.assertNotEqual(reopened['n'], card['n'])
            self.assertEqual(reopened['envelope']['source']['owner_action'], source['owner_action'])
            await rig.host._app(rig.s, answer(rig.phone, reopened, False))
            await rig.until(lambda: not rig.host.elevate.cards)
            self.assertIsNone(rig.desk.term)
            self.assertEqual(rig.host.agent.cur_send.approval_origin, old)
            await rig.host.elevate.stop()
        asyncio.run(run())

    def test_signed_vault_share_root_is_requester_not_approver_or_stale_agent(self):
        rig = vault_fixture.Vshare(); rig.setUp()
        self.addCleanup(rig.tearDown)
        rig.store()
        async def run():
            old = old_main_task(rig)
            await rig.step2(await rig.ask())
            card = await rig.card(cid=rig.s.cid)
            source = card['envelope']['source']
            self.assertEqual(source['channel'], 'phone')
            self.assertTrue(source['verified'])
            self.assertNotEqual(source['root_message_id'], old['root_message_id'])
            self.assertEqual(source['owner_action'], {'action': 'vault_share', 'device': rig.b.did})
            self.assertNotEqual(source['owner_action']['device'], rig.ph.did)
            self.assertIn('已保存的 Agent J 主机登录密码', source['text'])
            self.assertNotIn(rig.prf.hex(), json.dumps(source, ensure_ascii=False))
            await rig.host._app(rig.s, intent(rig.ph, card, 'question'))
            questioned = [item for _, item in rig.sent if item.get('t') == 'approval_review_result'][-1]['ask']
            await rig.host._app(rig.s, intent(rig.ph, questioned, 'reopen'))
            reopened = [item for _, item in rig.sent if item.get('t') == 'approval_review_result'][-1]['ask']
            await rig.host._app(rig.s, rig.approve(reopened, None, ok=False))
            result = await rig.result()
            self.assertFalse(result['ok'])
            self.assertEqual(result['why'], 'denied')
            self.assertEqual(rig.host.agent.cur_send.approval_origin, old)
            await rig.host.elevate.stop()
        asyncio.run(run())

    def test_invalid_phone_control_cannot_mint_human_action_record(self):
        rig = terminal_fixture.Rig(); rig.setUp()
        self.addCleanup(rig.tearDown)
        rig.passkey(); rig.enable()
        async def run():
            before = len(rig.host.hist.turns)
            await rig.step2(await rig.step1(), sig='invalid-signature')
            self.assertEqual(len(rig.host.hist.turns), before)
            self.assertEqual(rig.msgs('elev'), [])
            await rig.host.elevate.stop()
        asyncio.run(run())
