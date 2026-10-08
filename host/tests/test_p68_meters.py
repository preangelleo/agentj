import _hermetic
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from agentj.agent_codex import CodexAgent
from agentj.shared_codex import SharedCodexAgent
from agentj.agent_opencode import OpenCodeAgent

class Models(unittest.IsolatedAsyncioTestCase):
    async def test_default_model_in_independent_and_desktop_shared(self):
        for cls in (CodexAgent, SharedCodexAgent):
            a=object.__new__(cls)
            a.cfg={}; a.thread={}; a.human={}; a.models_cache=[]; a.unavailable_models=set()
            a.proc=Mock(); a.host=SimpleNamespace(); a.meter=Mock()
            a.call=AsyncMock(side_effect=[{'data':[{'id':'gpt-6.1-sol','isDefault':True}]},{}])
            await a.refresh_models()
            self.assertEqual(a.cur_model(),'gpt-6.1-sol')
            a.meter.assert_any_call(model='gpt-6.1-sol',model_name='gpt-6.1-sol')
            a.thread['model']='gpt-6-sol'
            self.assertEqual(a.cur_model(),'gpt-6-sol')
            a.cfg['model']='gpt-6-astra'
            self.assertEqual(a.cur_model(),'gpt-6-astra')

    async def test_first_turn_model_backfill(self):
        a=object.__new__(CodexAgent);a.tid='thread';a.thread={};a.cfg={};a.human={};a.models_cache=[]
        a.meter=Mock();a.turn_done=None;a.halting=False
        a._on_note('turn/completed',{'threadId':'thread','turn':{'status':'completed','model':'gpt-6.1-sol'}})
        self.assertEqual(a.cur_model(),'gpt-6.1-sol')
        a.meter.assert_called_once_with(model='gpt-6.1-sol',model_name='gpt-6.1-sol')

class Quota(unittest.IsolatedAsyncioTestCase):
    def agent(self):
        a=object.__new__(OpenCodeAgent)
        a.cfg={};a.sid='old';a.tasks=set();a.host=SimpleNamespace(st=Mock());a.meter=Mock()
        a._last_assistant=AsyncMock(return_value={'providerID':'p66dqa','modelID':'gpt-6','tokens':{}})
        a.context_meter=AsyncMock();a.client=SimpleNamespace(request=AsyncMock(return_value=(200,{'model':'p66dqa/gpt-6.1-sol'})))
        return a
    async def drain(self,a):
        await asyncio.gather(*list(a.tasks))
    async def test_default_clear_immediate_and_first_turn(self):
        a=self.agent()
        with patch('agentj.provider_runtime.probe',return_value=[{'window':'weekly','pct':1}]) as probe:
            res=await a.cmd_clear('')
            self.assertTrue(res.undo)
            a._last_assistant.return_value={}
            await self.drain(a)
            probe.assert_called_with('p66dqa')
            a._last_assistant.return_value={'providerID':'p66dqa','modelID':'gpt-6.1-sol'}
            a.refresh_usage();await self.drain(a)
            self.assertEqual(await a.quota_model(),'p66dqa/gpt-6.1-sol')
            self.assertEqual(probe.call_count,2)
            a.meter.assert_any_call(quota_windows=[{'window':'weekly','pct':1}])
    async def test_config_default_without_previous_message(self):
        a=self.agent();a._last_assistant.return_value={}
        self.assertEqual(await a.quota_model(),'p66dqa/gpt-6.1-sol')
    async def test_model_change_discards_inflight_quota(self):
        a=self.agent()
        async def last():
            a.cfg['model']='other/m';return {'providerID':'p66dqa','modelID':'gpt-6'}
        a._last_assistant.side_effect=last
        with patch('agentj.provider_runtime.probe',return_value=[{'window':'weekly'}]):
            a.refresh_usage();await self.drain(a)
        a.meter.assert_not_called()
