"""Same owner routes to main, but every sensitive action still needs its own card."""
import _hermetic  # noqa: F401
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch
from test_p336_approval_level import Base
from test_p194_owner_direct import _Svc, ALLOW_ME
from agentj import provenance, approval_jev, goal, heartbeat, imessage

SOURCE = {'kind':'same_owner', 'role':'task'}
def delegated(agent):
    agent.cur_send = SimpleNamespace(source=SOURCE)

class ApprovalPaths(Base):
    def setUp(self):
        super().setUp()
        self.host.agent = SimpleNamespace(kind="claude", status="working", cur_send=SimpleNamespace(source=SOURCE))

    def test_source_is_data_never_owner(self):
        self.assertFalse(provenance.is_owner(SOURCE))
        self.assertTrue(provenance.untrusted_turn(self.host.agent))
        self.assertTrue(provenance.delegated_turn(self.host.agent))
        self.assertNotIn('owner authorized', provenance.hook_context(SOURCE))
        self.assertNotIn('no peer agent requested', provenance.hook_context(SOURCE))
        self.assertTrue(provenance.names_untrusted('from-name="' + provenance.SAME_OWNER + '"'))
        native = SimpleNamespace(cur_send=None, pending_source=SOURCE, untrusted=lambda: True)
        self.assertTrue(provenance.delegated_turn(native))
        self.assertTrue(provenance.untrusted_turn(native))

    def test_live_phone_batch_grant_not_used(self):
        self.host.grants['fake'] = SimpleNamespace(tool='Bash', scope=('x','y'))
        with patch.object(self.host, '_grant_live', side_effect=AssertionError('must not inspect owner grants')):
            self.assertIsNone(self.host._grant_for('Bash', ('x','y')))
        with patch.object(self.host, '_grant_for', side_effect=AssertionError('owner grant bypass')):
            self.assertEqual(self.ask('git push origin main', answer=False)['behavior'], 'deny')
        self.assertEqual(len(self.cards()), 1)

    def test_goal_low_auto_and_no_card_full_do_not_apply(self):
        self.full()
        with patch.object(self.host.goals, 'current', return_value={'preauth': True}) as current, \
             patch.object(goal, 'tier', return_value=('low', None)) as tier:
            self.assertEqual(self.ask('git push origin main', answer=False)['behavior'], 'deny')
        current.assert_not_called()
        tier.assert_not_called()
        self.assertEqual(len(self.cards()), 1)

    def test_jev_low_risk_never_skips_card(self):
        self.assertFalse(approval_jev.low_risk_pass(self.host, {'high_risk_warnings':True}, 'Bash', {'command':'ls'}))
        with patch.object(approval_jev, 'available', return_value=True), \
             patch.object(approval_jev, 'gray', return_value=True), \
             patch.object(approval_jev, 'judge', side_effect=AssertionError('must not auto approve')):
            self.assertEqual(self.ask('$PY -m unittest', answer=False)['behavior'], 'deny')
        self.assertEqual(len(self.cards()), 1)

    def test_phone_allow_only_covers_that_action(self):
        self.assertEqual(self.ask('git push origin main', answer=True)['behavior'], 'allow')
        self.assertEqual(self.ask('git push origin main', answer=False)['behavior'], 'deny')
        self.assertEqual(len(self.cards()), 2)

class PrivateServices(IsolatedAsyncioTestCase):
    async def test_imessage_mutation_requires_card(self):
        host = SimpleNamespace(agent=SimpleNamespace(cur_send=SimpleNamespace(source=SOURCE)),
                               ask=AsyncMock(return_value={'behavior':'deny'}), st=Mock())
        service = imessage.IMessage.__new__(imessage.IMessage)
        service.host = host
        service.set = AsyncMock()
        self.assertEqual((await service.handle({'op':'set','key':'enabled','value':True}))['why'], 'denied')
        host.ask.assert_awaited_once()
        self.assertFalse(host.ask.call_args.kwargs['batch'])
        service.set.assert_not_awaited()

    async def test_heartbeat_write_requires_card(self):
        host = SimpleNamespace(agent=SimpleNamespace(cur_send=SimpleNamespace(source=SOURCE)),
                               ask=AsyncMock(return_value={'behavior':'deny'}), st=Mock(), set_pref=AsyncMock())
        service = heartbeat.Heartbeat.__new__(heartbeat.Heartbeat)
        service.host = host
        self.assertEqual((await service.handle({'op':'off'}))['why'], 'denied')
        host.ask.assert_awaited_once()
        host.set_pref.assert_not_awaited()

class MailPath(_Svc):
    async def test_owner_address_and_allowlist_do_not_skip_card(self):
        delegated(self.host.agent)
        self.host.ask = AsyncMock(return_value={'behavior':'deny'})
        self.svc.allowlisted = lambda addresses: True
        result = await self.svc.agent_send({'to':[ALLOW_ME], 'subject':'check', 'body':'status'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['why'], 'denied')
        self.host.ask.assert_awaited_once()
        self.assertFalse(self.host.ask.call_args.kwargs['batch'])
