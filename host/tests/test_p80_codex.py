"""P80: phone overrides, native provider resume and process-specific bilingual banner."""
import _hermetic
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock
from test_p51_codex_shared import Base, SID

class PhoneRouting(Base):
    async def test_resume_uses_current_native_provider_and_preserves_permissions(self):
        self.rollout(SID)
        a = self.agent(SID)
        a.human = {'model_provider': 'agentsrelay'}
        await a._thread()
        method, params = self.calls[-1]
        self.assertEqual(method, 'thread/resume')
        self.assertEqual(params['modelProvider'], 'agentsrelay')
        self.assertEqual(params['permissions'], ':read-only')
        self.assertEqual(params['approvalPolicy'], 'on-request')

    async def run_turn(self, **cfg):
        a = self.agent(SID, **cfg)
        a.tid = SID
        a.thread = {'model': 'gpt-5.6-sol', 'reasoningEffort': 'low'}
        a.original = {'approvalPolicy': 'on-request', 'permissions': ':read-only'}
        a._ready = AsyncMock(return_value=True)
        a.read_desktop = Mock()
        async def call(method, params):
            self.calls.append((method, params))
            a.turn_done.set()
            return {'turn': {'id': 'phone'}}
        a.call = AsyncMock(side_effect=call)
        await a.turn('owner message')
        return a, self.calls[-1][1]

    async def test_phone_config_model_and_effort_reach_turn_and_meter(self):
        a, params = await self.run_turn(model='gpt-6.1-sol', effort='high')
        self.assertEqual(params['model'], 'gpt-6.1-sol')
        self.assertEqual(params['effort'], 'high')
        self.assertEqual(params['permissions'], ':read-only')
        self.host.meter_update.assert_called_with(model='gpt-6.1-sol', model_name='gpt-6.1-sol', effort='high')
        self.assertEqual(a.thread['model'], params['model'])

    async def test_no_explicit_phone_choice_keeps_native_model(self):
        _, params = await self.run_turn()
        self.assertNotIn('model', params)
        self.assertNotIn('effort', params)

    async def test_default_choice_is_sent_once_then_native_thread_is_retained(self):
        a = self.agent(SID)
        a.revert = {'model': 'gpt-6.1-sol', 'effort': 'medium'}
        self.assertEqual(a.phone_choice(), a.revert)
        # The desktop actor has its own native choice; phone config remains its next phone-turn choice.
        a.cfg.update(model='gpt-6.1-sol', effort='high')
        a.follow_status('following')
        a.desktop_model({'model': 'gpt-6-astra', 'effort': 'low'})
        self.assertEqual(a.cur_model(), 'gpt-6-astra')
        self.assertEqual(a.cur_effort(), 'low')
        self.host.meter_update.assert_called_with(model='gpt-6-astra', model_name='gpt-6-astra', effort='low')
        self.assertEqual(a.phone_choice(), {'model': 'gpt-6.1-sol', 'effort': 'high'})

    async def test_writer_banner_names_terminal_and_unknown_in_both_languages(self):
        a = self.agent(SID)
        for kind in ('terminal', 'codex_app', 'chatgpt_app', 'app_server', 'unknown'):
            a.writer = {'kind': kind, 'pid': 123}
            a.follow_status('desktop_writer')
            notice = self.host.meter_update.call_args.kwargs['shared_writer']
            self.assertEqual(set(notice), {'zh', 'en'})
            if kind == 'terminal':
                self.assertIn('终端', notice['zh'])
                self.assertIn('terminal', notice['en'])
                self.assertIn('123', notice['en'])
            elif kind == 'unknown':
                self.assertIn('could not find', notice['en'])
        a.follow_status(None)
        self.assertIsNone(self.host.meter_update.call_args.kwargs['shared_writer'])

    async def test_default_round_sends_native_choice_once(self):
        a = self.agent(SID)
        a.human = {'model': 'gpt-6.1-sol', 'model_reasoning_effort': 'medium'}
        a.thread = {'model': 'gpt-5.6-sol'}
        a.cfg.update(model='gpt-6-astra', effort='high')
        self.host.set_model.side_effect = lambda v: a.cfg.update(model=v)
        self.host.set_effort.side_effect = lambda v: a.cfg.update(effort=v)
        await a.apply_model(None, None, default=True)
        a.tid = SID; a._ready = AsyncMock(return_value=True); a.read_desktop = Mock()
        async def call(method, params):
            self.calls.append((method, params)); a.turn_done.set()
            return {'turn': {'id': 'default'}}
        a.call = AsyncMock(side_effect=call)
        await a.turn('default')
        self.assertEqual(self.calls[-1][1]['model'], 'gpt-6.1-sol')
        self.assertEqual(self.calls[-1][1]['effort'], 'medium')
        self.assertIsNone(a.revert)
        await a.turn('next')
        self.assertNotIn('model', self.calls[-1][1])
        self.assertNotIn('effort', self.calls[-1][1])

    async def test_provider_resume_mismatch_is_not_silent(self):
        path = self.rollout(SID)
        a = self.agent(SID); a.human = {'model_provider': 'agentsrelay'}
        original_call = a.call.side_effect
        async def wrong_provider(method, params, timeout=60):
            response = await original_call(method, params, timeout)
            if method == 'thread/resume': response['modelProvider'] = 'openai'
            return response
        a.call.side_effect = wrong_provider
        from agentj.agent_codex import RPCError
        with self.assertRaisesRegex(RPCError, 'current model provider'):
            await a._resume(SID, path)

    async def test_writer_refusal_keeps_read_only_banner_after_turn_cleanup(self):
        from agentj.shared_codex import Refusal
        a = self.agent(SID)
        a.rollout = self.rollout(SID)
        a.writer = {'kind': 'terminal', 'pid': 123}
        a.follow_status('desktop_writer')
        a._ready = AsyncMock(side_effect=Refusal('desktop writer active'))
        await a.turn('phone message')
        self.assertEqual(a.shared_status, 'desktop_writer')
        self.assertFalse(a.phone_turn)
        self.assertIn('terminal', self.host.local_notice.call_args.args[0])
