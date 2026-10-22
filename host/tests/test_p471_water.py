"""P471: OpenCode metadata and conservative display fallback."""
import unittest
from unittest import mock
from agentj import model_limits as ml, provider_runtime as pr
from agentj.agent_opencode import OpenCodeAgent
from agentj.agent_opencode2 import OpenCodeV2Agent

class Limits(unittest.TestCase):
    def test_checked_exact_models(self):
        for mid, size in [('deepseek-v4-flash',1048576),('deepseek-v4-flash-free',200000),('deepseek-v4.1-flash-free',200000),('deepseek-v4-pro',1048576),('kimi-k2.5',262144),('glm-4.7-flash',200000),('minimax-m2.5',204800)]:
            self.assertEqual(ml.opencode_context_limit('custom',mid),size,mid)
        for mid in ('deepseek-mystery-free','custom-model','gpt-6-unknown'):
            self.assertEqual(ml.opencode_context_limit('custom',mid),200000)
        self.assertIsNone(ml.codex_context_limit('deepseek-v4-flash',None,{}))

class Meter(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_fallback_unknown_and_probe_failure(self):
        for cls in (OpenCodeAgent, OpenCodeV2Agent):
            for mid, limit, fail, expected in [('deepseek-v4-flash-free',80000,False,80000),('deepseek-v4-flash-free',None,False,200000),('deepseek-v4.1-flash-free',None,False,200000),('deepseek-v4-pro',None,True,1048576),('custom-model',None,False,200000),('custom-model',None,True,200000)]:
                with self.subTest(cls=cls.__name__,mid=mid,limit=limit,fail=fail):
                    a=object.__new__(cls);a.cfg={'model':'opencode/'+mid};a.models_cache=[{'id':'opencode/'+mid,'name':mid}]
                    a._last_assistant=mock.AsyncMock(return_value={'tokens':{'input':24000,'output':1000,'cache':{'read':7000}}})
                    a._providers=mock.AsyncMock(return_value={'providers':[{'id':'opencode','models':{mid:{'limit':{'context':limit}}}}]},side_effect=OSError('offline') if fail else None)
                    a.meter=mock.Mock()
                    with mock.patch.object(pr,'configured',return_value={}):
                        await a.context_meter(include_quota=False)
                    self.assertEqual(a.meter.call_args.kwargs['ctx'],{'used':32000,'max':expected})

    async def test_invalid_limits_and_missing_usage(self):
        for cls in (OpenCodeAgent, OpenCodeV2Agent):
            for limit in (True, 0, -1, '200000', None):
                a = object.__new__(cls)
                a.cfg = {'model': 'custom/unknown'}
                a.models_cache = [{'id': 'custom/unknown', 'name': 'unknown'}]
                a._providers = mock.AsyncMock(return_value={'providers': [{'id': 'custom', 'models': {'unknown': {'limit': {'context': limit}}}}]})
                a._last_assistant = mock.AsyncMock(return_value={'tokens': {'input': 0}})
                a.meter = mock.Mock()
                with mock.patch.object(pr, 'configured', return_value={}):
                    await a.context_meter(include_quota=False)
                    self.assertEqual(a.meter.call_args.kwargs['ctx'], {'used': 0, 'max': 200000})
                    a._last_assistant.return_value = {}
                    await a.context_meter(include_quota=False)
                    self.assertIsNone(a.meter.call_args.kwargs['ctx'])

    async def test_slow_metadata_does_not_drop_usage(self):
        a = object.__new__(OpenCodeAgent)
        a.cfg = {'model': 'custom/unknown'}
        a.models_cache = [{'id': 'custom/unknown', 'name': 'unknown'}]
        a._last_assistant = mock.AsyncMock(return_value={'tokens': {'total': 32000}})
        a._providers = mock.AsyncMock(side_effect=TimeoutError)
        a.meter = mock.Mock()
        with mock.patch.object(pr, 'configured', return_value={}):
            await a.context_meter(include_quota=False)
        self.assertEqual(a.meter.call_args.kwargs['ctx'], {'used': 32000, 'max': 200000})

    async def test_default_model_from_assistant_and_stale_selection(self):
        for cls in (OpenCodeAgent, OpenCodeV2Agent):
            a = object.__new__(cls)
            a.cfg = {}
            a.models_cache = [{'id': 'opencode/deepseek-v4.1-flash-free', 'name': 'Flash Free'}]
            a._last_assistant = mock.AsyncMock(return_value={
                'providerID': 'opencode', 'modelID': 'deepseek-v4.1-flash-free',
                'tokens': {'input': 24000, 'output': 1000, 'cache': {'read': 7000}}})
            a._providers = mock.AsyncMock(return_value={})
            a.meter = mock.Mock()
            with mock.patch.object(pr, 'configured', return_value={}):
                await a.context_meter(include_quota=False)
                self.assertEqual(a.meter.call_args.kwargs['ctx'], {'used': 32000, 'max': 200000})
                a.meter.reset_mock()
                async def changed():
                    a.cfg['model'] = 'custom/new'
                    return {}
                a._providers.side_effect = changed
                await a.context_meter(include_quota=False)
                a.meter.assert_not_called()
