"""P67b endpoint identity, native/config precedence and measured used-only."""
import unittest
from unittest import mock
from agentj import model_limits as ml, provider_runtime as pr, agent_opencode as oc

def provider(base=None, limit=None):
    p={'models':{'gpt-6':{'limit':{'context':limit}}}}
    if base is not None: p['options']={'baseURL':base}
    return p

class ModelLimits(unittest.TestCase):
    def test_checked_rows_and_exact_aliases(self):
        for mid,row in ml.MODEL_LIMITS.items():
            self.assertEqual(row['checked_on'],'2026-10-06')
            self.assertTrue(row['source_url'].startswith('https://developers.openai.com/'))
            self.assertEqual(ml.context_limit('custom',mid,provider('https://agentsrelay.net/v1')),272000)
            self.assertEqual(ml.context_limit('custom',mid,provider('https://api.openai.com/v1')),922000)
        self.assertIsNone(ml.context_limit('openai','gpt-6-unknown'))
    def test_endpoint_identity_beats_alias(self):
        self.assertEqual(ml.context_limit('p66dqa','gpt-6',provider('https://agentsrelay.net/v1')),272000)
        self.assertEqual(ml.context_limit('openai','gpt-6'),922000)
        for url in ('https://agentsrelay.net.evil.invalid/v1','https://evil.invalid/agentsrelay.net','http://agentsrelay.net/v1','https://api.openai.com@evil.invalid/v1','https://evil.invalid/v1'):
            self.assertIsNone(ml.context_limit('openai','gpt-6',provider(url)),url)
        self.assertIsNone(ml.context_limit('agentsrelay','gpt-6'))
    def test_codex_unknown_model_and_invalid_native_limits(self):
        pool=ml.codex_context_metadata({'model_provider':'pool','model_providers':{'pool':{'base_url':'https://agentsrelay.net/v1'}}})
        self.assertIsNone(ml.codex_context_limit('unknown-model',None,pool))
        self.assertEqual(ml.codex_context_limit('unknown-model',700,pool),700)
        for bad in (True,False,0,-1,'800000',float('inf')):
            self.assertEqual(ml.codex_context_limit('gpt-6',bad,pool),272000)
            self.assertIsNone(ml.codex_context_limit('unknown-model',bad,pool))
            self.assertIsNone(ml.codex_context_metadata({'model_context_window':bad})['limit'])

    def test_config_then_native_then_static(self):
        native=provider('https://agentsrelay.net/v1',500000)
        self.assertEqual(ml.context_limit('p66dqa','gpt-6',provider(limit=600000),native),600000)
        self.assertEqual(ml.context_limit('p66dqa','gpt-6',provider('https://agentsrelay.net/v1'),native),500000)
        for bad in (True,0,-1,'800000'):
            self.assertEqual(ml.context_limit('p66dqa','gpt-6',provider('https://agentsrelay.net/v1',bad)),272000)
        self.assertEqual(ml.context_limit('p66dqa','gpt-6',provider(),provider('https://agentsrelay.net/v1')),272000)
        self.assertEqual(ml.context_limit('other','unknown',{'models':{'unknown':{'limit':{'context':500}}}}),500)

class MeterLimits(unittest.IsolatedAsyncioTestCase):
    async def test_tokens_pool_direct_unknown(self):
        for base,expected,mid in [('https://agentsrelay.net/v1',272000,'gpt-6'),('https://api.openai.com/v1',922000,'gpt-6'),('https://agentsrelay.net/v1',None,'unknown')]:
            a=object.__new__(oc.OpenCodeAgent);a.cfg={'model':'alias/'+mid};a.models_cache=[{'id':'alias/'+mid,'name':mid}]
            a._last_assistant=mock.AsyncMock(return_value={'providerID':'alias','modelID':mid,'tokens':{'input':10,'output':2,'cache':{'read':3}}})
            a._providers=mock.AsyncMock(return_value={});a.meter=mock.Mock()
            with mock.patch.object(pr,'configured',return_value={'alias':provider(base)}):
                await a.context_meter(include_quota=False)
            self.assertEqual(a.meter.call_args.kwargs['ctx'],{'used':15,'max':expected})
    async def test_explicit_limit_independent_of_probe(self):
        a=object.__new__(oc.OpenCodeAgent);a.cfg={'model':'p/gpt-6'};a.models_cache=[{'id':'p/gpt-6','name':'M'}]
        a._last_assistant=mock.AsyncMock(return_value={'tokens':{'input':10}});a._providers=mock.AsyncMock(side_effect=OSError('unavailable'));a.meter=mock.Mock()
        with mock.patch.object(pr,'configured',return_value={'p':provider(limit=100)}): await a.context_meter(include_quota=False)
        self.assertEqual(a.meter.call_args.kwargs['ctx'],{'used':10,'max':100});a._providers.assert_not_awaited()


class NativeMeterLimits(unittest.IsolatedAsyncioTestCase):
    async def test_claude_native_max_or_used_only_or_clear(self):
        from agentj.agent import ClaudeAgent
        a=object.__new__(ClaudeAgent);a.proc=type('Proc',(),{'returncode':None})();a.cfg={'model':'claude-unknown'};a.meter=mock.Mock()
        for usage,expected in [({'totalTokens':15,'maxTokens':100},{'used':15,'max':100}),({'totalTokens':15},{'used':15,'max':None}),({'maxTokens':100},None),({'totalTokens':True,'maxTokens':100},None)]:
            a.control=mock.AsyncMock(return_value=usage)
            await a.context_meter()
            self.assertEqual(a.meter.call_args.kwargs['ctx'],expected)
        a.control=mock.AsyncMock(side_effect=OSError('offline'))
        await a.context_meter();self.assertEqual(a.meter.call_args.kwargs['ctx'],None)

    async def test_codex_missing_native_max_provider_explicit_or_unknown(self):
        from agentj.agent_codex import CodexAgent
        a=object.__new__(CodexAgent);a.tid='t';a.research=False;a.persist=True;a.cfg={'model':'gpt-6'};a.meter=mock.Mock()
        for config,expected in [({},None),({'model_provider':'openai'},None),({'model_provider':'pool','model_providers':{'pool':{'base_url':'https://agentsrelay.net/v1','experimental_bearer_token':'fixture-private-value'}}},272000),({'model_provider':'direct','model_providers':{'direct':{'base_url':'https://api.openai.com/v1'}}},922000),({'model_context_window':500},500)]:
            a.context_metadata=ml.codex_context_metadata(config)
            self.assertNotIn('fixture-private',str(a.context_metadata))
            a._on_note('thread/tokenUsage/updated',{'threadId':'t','tokenUsage':{'last':{'totalTokens':15}}})
            self.assertEqual(a.meter.call_args.kwargs['ctx'],{'used':15,'max':expected})
            a._on_note('thread/tokenUsage/updated',{'threadId':'t','tokenUsage':{'last':{'totalTokens':15},'modelContextWindow':800}})
            self.assertEqual(a.meter.call_args.kwargs['ctx'],{'used':15,'max':800})
        a._on_note('thread/tokenUsage/updated',{'threadId':'t','tokenUsage':{'modelContextWindow':800}})
        self.assertEqual(a.meter.call_args.kwargs['ctx'],None)
        a._on_note('thread/tokenUsage/updated',{'threadId':'t','tokenUsage':None})
        self.assertEqual(a.meter.call_args.kwargs['ctx'],None)
        a._on_note('turn/started',{'threadId':'t','turn':{'id':'next'}})
        self.assertEqual(a.meter.call_args.kwargs['ctx'],None)
        self.assertIsNone(a.usage)
        a.context_metadata=ml.codex_context_metadata({'openai_base_url':'https://api.openai.com.evil.invalid/v1'})
        a._on_note('thread/tokenUsage/updated',{'threadId':'t','tokenUsage':{'last':{'totalTokens':15}}})
        self.assertEqual(a.meter.call_args.kwargs['ctx'],{'used':15,'max':None})
