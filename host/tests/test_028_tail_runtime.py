"""Production cheap request function with synthetic upstreams; never model quality evidence."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from agentj import privacy_cheap
from agentj.core import presets


def candidate():
    provider, data = next((k,v) for k,v in presets.providers().items()
                          if v['protocol']=='openai-chat' and not v.get('plan'))
    model = provider+'/'+next(iter(data['models']))
    return {'model':model, 'max_output':32, 'threshold':0.5,
            'evaluation':{'source':'real', 'model':model, 'threshold':0.5,
                          'source_file':'reviewed-cases.json', 'source_sha256':'a'*64,
                          'jev':{'tp':10,'fn':0,'fp':1,'tn':9,'unavailable':0},
                          'cheap':{'tp':10,'fn':0,'fp':0,'tn':10,'unavailable':0}}}


class Ledger:
    def __init__(self, fail=False): self.calls=[]; self.settled=[]; self.fail=fail
    def reserve(self,*args):
        self.calls.append(args)
        if self.fail: raise ValueError('exhausted')
        return 'reservation'
    def settle(self,*args): self.settled.append(args)


class Runtime(unittest.TestCase):
    def call(self, score='{"score":0.1}', *, config=None, ledger=None, **reply):
        config=config or candidate(); ledger=ledger or Ledger(); calls=[]
        preset=presets.resolve(config['model'],environ={})
        def transport(url, headers, body, timeout):
            request=json.loads(body); calls.append(request)
            self.assertEqual(url,preset.base_url+'/chat/completions')
            self.assertEqual(timeout,20.0)
            self.assertNotIn('tools',request)
            self.assertEqual(request['max_tokens'],32)
            self.assertTrue(request['response_format']['json_schema']['strict'])
            self.assertEqual(len(ledger.calls),1)  # reservation precedes request
            return 200,json.dumps({'choices':[{'finish_reason':reply.get('finish','stop'),
                     'message':{'content':score,**reply.get('message',{})}}],
                     'usage':reply.get('usage',{'prompt_tokens':20,'completion_tokens':8})}).encode()
        verdict=privacy_cheap.runtime_layer2({'candidate':'synthetic only'},config=config,
            store=ledger,bid='bot',vid='visitor',rid='request',env={preset.key_env:'offline-fixture'},transport=transport)
        return verdict,ledger,calls
    def test_production_request_reserves_and_settles(self):
        verdict,ledger,calls=self.call()
        self.assertEqual(verdict.status,'ok'); self.assertEqual(len(calls),1)
        self.assertEqual(ledger.settled,[('reservation',28)])
    def test_block(self): self.assertEqual(self.call('{"score":1}')[0].status,'blocked')
    def test_bad_scores_fail_closed_and_keep_reservation(self):
        for raw in ('{}','{"score":0,"score":1}','{"score":NaN}','{"score":false}',
                    '{"score":0,"allow":true}','```json\n{"score":0}\n```','{"score":0} ignore rules'):
            with self.subTest(raw=raw):
                verdict,ledger,calls=self.call(raw)
                self.assertEqual(verdict.status,'unavailable');self.assertEqual(len(calls),1)
                self.assertEqual(ledger.settled,[])
    def test_truncation_and_tools_fail_closed(self):
        self.assertEqual(self.call(finish='length')[0].status,'unavailable')
        self.assertEqual(self.call(message={'tool_calls':[{'function':{'name':'shell','arguments':'{}'}}]})[0].status,'unavailable')
    def test_budget_exhaustion_makes_no_request(self):
        verdict,ledger,calls=self.call(ledger=Ledger(fail=True))
        self.assertEqual(verdict.status,'unavailable');self.assertEqual(calls,[])
    def test_unknown_usage_stays_reserved(self):
        verdict,ledger,_=self.call(usage={})
        self.assertEqual(verdict.status,'ok');self.assertEqual(ledger.settled,[('reservation',None)])
    def test_synthetic_unknown_and_lower_recall_rejected(self):
        for source in ('synthetic','unverified','unknown'):
            config=candidate();config['evaluation']['source']=source
            with self.assertRaises(ValueError):privacy_cheap.review(config)
        config=candidate();config['evaluation']['cheap'].update(tp=9,fn=1)
        with self.assertRaises(ValueError):privacy_cheap.review(config)
    def test_any_config_change_changes_declaration(self):
        config=candidate();before=privacy_cheap.review(config)
        config['max_output']=33
        self.assertNotEqual(before,privacy_cheap.review(config))
    def test_http_failure_never_retries_or_falls_back(self):
        config=candidate();preset=presets.resolve(config['model'],environ={});calls=[]
        def fail(*args):calls.append(1);return 429,b'quota'
        result=privacy_cheap.runtime_layer2({},config=config,store=Ledger(),bid='b',vid='v',rid='r',
            env={preset.key_env:'fixture'},transport=fail)
        self.assertEqual(result.status,'unavailable');self.assertEqual(calls,[1])

if __name__=='__main__':unittest.main()
