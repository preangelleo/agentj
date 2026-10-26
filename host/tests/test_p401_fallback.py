"""P401: failure injection, least authority, local reliability and one-time notices."""
import _hermetic  # noqa: F401
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agentj import fallback, local_model, opencode_free

DS = 'opencode/deepseek-v4.1-flash-free'
LC = 'opencode/glm-5.3-flash-free'

class Chain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = SimpleNamespace(root=Path(self.tmp.name))
        self.notice = Mock()

    def run_chain(self, models, answers, enabled=False):
        attempted = []
        def cloud(exe, model, prompt):
            attempted.append(model)
            result = answers.get(model)
            if isinstance(result, Exception): raise result
            return result or ''
        with patch.object(fallback, 'free_models', return_value=models), \
             patch.object(fallback, 'zero_retention_today', return_value={LC}), \
             patch.object(fallback, 'cloud_reply', side_effect=cloud), \
             patch.object(local_model, 'enabled', return_value=enabled), \
             patch.object(local_model, 'diagnose', return_value='local evidence') as local:
            result = fallback.diagnose(self.st, '401 Unauthorized', 'zh', self.notice, 'oc')
        return attempted, result, local.call_count

    def test_own_failure_uses_deepseek_first(self):
        a, r, n = self.run_chain([LC, DS], {DS: 'cloud'})
        self.assertEqual(a, [DS]); self.assertEqual(n, 0)
        self.assertIn('这是兜底模型的回答', r)

    def test_deepseek_failure_uses_zero_retention(self):
        a, r, n = self.run_chain([DS, LC], {DS: OSError(), LC: 'answer'})
        self.assertEqual(a, [DS, LC]); self.assertEqual(n, 0)
        self.assertIn('OpenCode 官方承诺不留存、不训练', r)

    def test_missing_free_notifies_once_across_runs(self):
        for _ in range(2):
            a, r, n = self.run_chain([LC], {LC: 'answer'})
            self.assertEqual(a, [LC]); self.assertEqual(n, 0)
        self.notice.assert_called_once()

    def test_offline_reaches_opted_in_local(self):
        a, r, n = self.run_chain(None, {}, enabled=True)
        self.assertEqual(a, []); self.assertEqual(n, 1); self.assertIn('qwen3.5:9b', r)
        self.notice.assert_not_called()  # unknown is not evidence the model disappeared

    def test_cloud_failures_reach_local(self):
        a, r, n = self.run_chain([DS, LC], {DS: OSError(), LC: OSError()}, True)
        self.assertEqual(a, [DS, LC]); self.assertEqual(n, 1)

    def test_unapproved_models_never_run(self):
        a, r, n = self.run_chain(['opencode/space-bunny-free', 'opencode/minimax-m3.1-flash-free', 'opencode/mimo-free'], {})
        self.assertEqual(a, []); self.assertIsNone(r); self.assertEqual(n, 0)

    def test_local_is_opt_in(self):
        self.assertFalse(local_model.enabled(self.st))

    def test_terms_unknown_fails_closed(self):
        with patch.object(fallback, 'fetch_terms', side_effect=OSError()):
            self.assertEqual(fallback.zero_retention_today(), set())

class Local(unittest.TestCase):
    def test_retry_500_and_final_text_and_unload(self):
        steps = [(500, {}), (500, {}), (200, {'message': {'content': '401 Unauthorized'}})]
        seen = []
        def call(path, body, timeout):
            seen.append((path, body))
            return steps.pop(0) if path == '/api/chat' else (200, {})
        result = local_model.diagnose('401 Unauthorized', transport=call)
        self.assertEqual(result, '401 Unauthorized')
        self.assertEqual(sum(p == '/api/chat' for p, _ in seen), 3)
        self.assertEqual(seen[-1][1]['keep_alive'], 0)
        self.assertIn('每轮只调一个工具', seen[0][1]['messages'][0]['content'])
        self.assertFalse(seen[0][1]['think'])

    def test_tools_cannot_write_or_read_arbitrary_paths(self):
        for name in ['shell', 'read_log', 'write_file', 'support_ask']:
            self.assertEqual(local_model.tool(name, {'path': '/etc/passwd'}, '401'), {'error': 'unknown_tool'})
        self.assertEqual(local_model.tool('get_status', {}, '401')['original_error'], '401')

    def test_many_tools_are_rejected_then_report_accepted(self):
        calls = iter([
            (200, {'message': {'tool_calls': [{'function': {'name': 'get_status', 'arguments': {}}}] * 2}}),
            (200, {'message': {'tool_calls': [{'function': {'name': 'send_report', 'arguments': {'body': 'evidence'}}}]}}),
        ])
        with patch.object(local_model, 'tool', wraps=local_model.tool) as tool:
            out = local_model.diagnose('401', transport=lambda p,b,t: next(calls) if p == '/api/chat' else (200, {}))
        self.assertEqual(out, 'evidence'); tool.assert_not_called()

    def test_exhausted_500_is_bounded(self):
        seen = []
        def call(p,b,t): seen.append(p); return 500, {}
        with self.assertRaises(local_model.LocalError): local_model.diagnose('401', transport=call)
        self.assertEqual(seen.count('/api/chat'), 4); self.assertEqual(seen[-1], '/api/generate')

    def test_hardware_gate(self):
        self.assertTrue(local_model.eligible('Darwin', 'arm64', 16 * 1024**3, 21 * 1024**3))
        for system, chip, ram in [('Darwin','x86_64',32), ('Darwin','arm64',8), ('Linux','x86_64',64)]:
            self.assertFalse(local_model.eligible(system,chip,ram * 1024**3,30 * 1024**3))

class Hook(unittest.IsolatedAsyncioTestCase):
    async def test_no_failure_no_fallback(self):
        h = SimpleNamespace(cur_failed=False)
        with patch.object(fallback, 'diagnose') as d:
            await fallback.after_failure(h, SimpleNamespace())
        d.assert_not_called()

    async def test_duplicate_failure_runs_once_and_stop_discards(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        h = SimpleNamespace(cur_failed=True, _fallback_error='401', _fallback_used=False,
                            stopped=lambda:False, st=SimpleNamespace(root=Path(tmp.name)), lang='zh',
                            agent_notice=Mock(), agent_text=Mock())
        a = SimpleNamespace(cfg={}, halting=False, cur_send=None, native_source={"kind": "terminal", "role": "owner"})
        with patch.object(fallback, 'diagnose', return_value='reply') as d:
            await fallback.after_failure(h,a); await fallback.after_failure(h,a)
        d.assert_called_once(); h.agent_text.assert_called_once_with('reply')
        h._fallback_used=False; a.halting=True
        with patch.object(fallback, 'diagnose') as d: await fallback.after_failure(h,a)
        d.assert_not_called()

class Safety(unittest.TestCase):
    def test_cloud_only_gets_fixed_signals(self):
        raw = '401 Unauthorized api_key=private-canary-value /private/customer/file'
        safe = fallback.safe_error(raw)
        self.assertIn('401',safe); self.assertNotIn('canary',safe); self.assertNotIn('customer',safe)

    def test_terms_require_explicit_current_promise(self):
        previous = fallback._terms_cache
        self.addCleanup(setattr, fallback, '_terms_cache', previous)
        for text, expected in [
            ('GLM-5.3-Flash Free is free on OpenCode for a limited time. Its provider follows a zero-retention policy and does not use your data for model training.', {LC}),
            ('GLM-5.3-Flash Free may use your data for training.', set()),
            ('Every provider has no retention.', set())]:
            fallback._terms_cache = None
            with patch.object(fallback,'fetch_terms',return_value=text):
                self.assertEqual(fallback.zero_retention_today(),expected)

    def test_list_is_fresh_isolated_and_requires_zero_cost(self):
        import os
        seen = []
        def run(args, **kw):
            seen.append((args,kw))
            return SimpleNamespace(returncode=0,stdout=DS+'\n'+json.dumps({'cost':{'input':0,'output':0}}))
        with patch.dict(os.environ, {'AGENTJ_OC_FREE_LIST':'on','OPENCODE_API_KEY':'secret-canary'}), patch.object(fallback.subprocess,'run',side_effect=run):
            for _ in range(2): self.assertEqual(fallback.free_models('oc'),[DS])
        self.assertEqual(len(seen),2)
        for args, kw in seen:
            self.assertEqual(args,['oc','models','opencode','--verbose'])
            self.assertNotIn('OPENCODE_API_KEY',kw['env'])
            self.assertFalse(Path(kw['env']['HOME']).exists())

class RealLoop(unittest.IsolatedAsyncioTestCase):
    async def test_failure_hook_keeps_owner_source_until_fallback(self):
        from agentj.agent import Agent
        from agentj import provenance
        class Runner(Agent):
            kind='fixture'
            async def _identity_refresh(self): pass
            async def turn(self,text): self.fail_notice('401 Unauthorized')
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        h=SimpleNamespace(cur_failed=False,_fallback_used=False,lang='zh',st=SimpleNamespace(root=Path(tmp.name)),
                          agent_notice=Mock(),agent_status=Mock(),agent_text=Mock(),stopped=lambda:False)
        h.turn_failed=lambda: setattr(h,'cur_failed',True)
        h.fallback_failure=lambda text: setattr(h,'_fallback_error',text)
        h.agent_turn_start=lambda *a: None
        h.agent_turn_end=Mock(side_effect=lambda: task.cancel())
        a=Runner(h,{})
        # Exercise the real phone-send branch; a non-owner must not send a free-cloud request.
        for source, expected in [(provenance.phone(),1), (provenance.system(),0), ({'kind':'unknown','role':'guest'},0)]:
            h.cur_failed=False;h._fallback_used=False
            send=SimpleNamespace(sid='s',text='hello',state='queued',source=source,wait_ready=None)
            async def ready(): return True
            send.wait_ready=ready
            a.q.put_nowait(send)
            with patch('agentj.agent.compactprep.decorate',return_value='hello'),patch.object(fallback,'diagnose',return_value='diagnosis') as d:
                task=asyncio.create_task(a.run())
                try: await task
                except asyncio.CancelledError: pass
            self.assertEqual(d.call_count,expected)

class Catalog(unittest.TestCase):
    def test_catalog_unavailable_is_not_a_disappearance(self):
        import os
        with patch.dict(os.environ,{'AGENTJ_OC_FREE_LIST':'on'}):
            for output in ['', DS+'\nnot-json', DS+'\n{}']:
                with patch.object(fallback.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=output)):
                    self.assertIsNone(fallback.free_models('oc'))

    def test_space_bunny_and_alias_never_suggested(self):
        from agentj import opencode_free
        self.assertEqual(opencode_free.pick(['opencode/space-bunny-free', LC]), LC)
        self.assertIsNone(opencode_free.pick(['opencode/space-bunny-free','opencode/minimax-m3.1-flash-free']))

class Cloud(unittest.TestCase):
    def test_isolated_home_no_key_and_version_dispatch(self):
        import os
        from agentj import agent_opencode2
        for version, target in [('1.18.34',opencode_free), ('2.0.31',agent_opencode2)]:
            with patch.dict(os.environ,{'OPENCODE_API_KEY':'canary','OPENCODE_CONFIG_CONTENT':'private plugins'}), \
                 patch.object(fallback.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=version)), \
                 patch.object(target,'probe',return_value='answer') as probe:
                self.assertEqual(fallback.cloud_reply('oc', LC, 'fixed evidence'), 'answer')
                args, kwargs=probe.call_args
                self.assertNotIn('OPENCODE_API_KEY',args[2]);self.assertNotIn('OPENCODE_CONFIG_CONTENT',args[2])
                self.assertFalse(Path(args[2]['HOME']).exists())
                self.assertEqual(kwargs['prompt'],'fixed evidence');self.assertTrue(kwargs['diagnostic'])

class Wire(unittest.TestCase):
    def test_v1_diagnostic_uses_prompt_and_only_scratch_reads(self):
        from test_p298_opencode_free import Fake
        from agentj.agent_opencode import evaluate
        f=Fake();f.setUp();self.addCleanup(f.doCleanups)
        reply=opencode_free.probe(f.exe,LC,dict(__import__('os').environ),5,waits=(),prompt='synthetic 401',diagnostic=True)
        self.assertTrue(reply)
        trace=f.log();rules=next(x['session']['permission'] for x in trace if isinstance(x,dict) and 'session' in x)
        msg=next(x['message'] for x in trace if isinstance(x,dict) and 'message' in x)
        self.assertEqual(msg['parts'][0]['text'],'synthetic 401')
        for permission,path in [('bash','cat /etc/passwd'),('read','/etc/passwd'),('webfetch','https://example.com'),('skill','*'),('lsp','*'),('edit','*')]:
            self.assertEqual(evaluate(permission,path,rules),'deny')
        self.assertTrue(any(isinstance(x,dict) and x.get('delete') for x in trace))

class Control(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_blocks_worker_continuation(self):
        import threading
        entered=threading.Event();release=threading.Event();seen=[]
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        h=SimpleNamespace(cur_failed=True,_fallback_used=False,_fallback_error='401',lang='zh',
                          st=SimpleNamespace(root=Path(tmp.name)),stopped=lambda:False,agent_text=Mock(),agent_notice=Mock())
        a=SimpleNamespace(cfg={},halting=False,cur_send=None,native_source={"kind": "terminal", "role": "owner"})
        def diagnose(*args):
            entered.set();release.wait(2);seen.append(args[-1]());return 'late report'
        with patch.object(fallback,'diagnose',side_effect=diagnose):
            task=asyncio.create_task(fallback.after_failure(h,a))
            await asyncio.to_thread(entered.wait,2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            release.set()
            for _ in range(30):
                if seen: break
                await asyncio.sleep(.01)
        self.assertEqual(seen,[True]);h.agent_text.assert_not_called();h.agent_notice.assert_not_called()

class LocalCLI(unittest.TestCase):
    def test_cli_actions_and_disk_gate(self):
        import argparse
        parser=argparse.ArgumentParser();local_model.add_parser(parser.add_subparsers())
        for action in ['install','status','off']:
            a=parser.parse_args(['local-model',action]);self.assertIs(a.fn,local_model.command)
        self.assertFalse(local_model.eligible('Darwin','arm64',16*1024**3,19*1024**3))
