"""P508b: kernel evidence, per-child tasks, independent process count and no credential projection."""
import _hermetic
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agentj import busy, busy_probe as probe


class Provenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.home = self.root / 'home'
        (self.home / '.codex').mkdir(parents=True)
        (self.home / '.codex' / 'config.toml').write_text('model="config-model"\nmodel_reasoning_effort="high"\n')
        self.add(10, 1, 'bash', ['bash', 'launcher.sh'])
        self.add(11, 10, 'codex', ['codex', 'exec', '-m', 'gpt-test', '-c', 'model_reasoning_effort="xhigh"'])

    def add(self, pid, parent, exe, argv, start='123', env=None):
        d = self.proc / str(pid)
        d.mkdir(exist_ok=True)
        # proc stat fields 3..22 (state, ppid, ... starttime)
        (d / 'stat').write_text(f'{pid} (fixture ) process) ' + ' '.join(['S', str(parent)] + ['0'] * 17 + [start]))
        (d / 'cmdline').write_bytes(('\0'.join(argv)+'\0').encode())
        environment = {'HOME': str(self.home), **(env or {})}
        (d / 'environ').write_bytes(('\0'.join(k+'='+v for k,v in environment.items())+'\0').encode())
        with contextlib.suppress(FileNotFoundError):
            (d / 'exe').unlink()
        (d / 'exe').symlink_to('/fixture/bin/'+exe)

    def sample(self, it=None):
        return probe.fields(it or {'pid':10}, probe.Processes(self.proc))

    def test_wrapper_child_flags_config_and_effort(self):
        f=self.sample()
        self.assertEqual(f['detected']['harness'], 'codex')
        self.assertEqual(f['model'], 'gpt-test')
        self.assertEqual(f['detected']['effort'], 'xhigh')
        self.assertEqual(f['source'], {'kind':'pid','pid':10,'start':'123'})
        self.assertEqual(f['live_pids'], [11])

    def test_config_and_model_refresh(self):
        self.add(11,10,'codex',['codex','exec'])
        self.assertEqual(self.sample()['model'],'config-model')
        (self.home/'.codex/config.toml').write_text('model="changed-model"')
        self.assertEqual(self.sample()['model'],'changed-model')

    def test_self_report_conflict_cannot_override(self):
        f=self.sample({'pid':10,'harness':'claude','model':'claimed','self_reported':True})
        self.assertTrue(f['conflict'])
        self.assertEqual(f['reported'],{'harness':'claude','model':'claimed'})
        self.assertEqual(f['model'],'gpt-test')

    def test_no_source_and_dead_and_reused_pid(self):
        self.assertIsNone(self.sample({'pid':999}))
        self.assertIsNone(self.sample({'pid':10,'proc_start':'old'}))
        (self.proc/'10/stat').write_text('10 (gone) Z 1 '+'0 '*17+'123')
        self.assertIsNone(self.sample({'pid':10}))

    def test_prompt_is_not_an_executable(self):
        self.add(11,10,'bash',['bash','-c','codex --model imaginary'])
        self.assertIsNone(self.sample()['harness'])
        self.assertIsNone(self.sample()['model'])

    def test_two_children_one_process_no_parent_model(self):
        b=busy.Board(None,verify_sources=True)
        b.set_context('codex',pid=11)
        b.codex_event('item/completed',{'item':{'type':'collabAgentToolCall','tool':'spawnAgent','status':'completed',
                       'receiverThreadIds':['one','two'],'prompt':'Inspect'}},'codex')
        b.refresh_sources(probe.Processes(self.proc))
        m=b.msg()
        self.assertEqual((m['task_count'],m['live_process_count']),(2,1))
        self.assertEqual({t['source']['thread_id'] for t in m['tasks']},{'one','two'})
        self.assertTrue(all(t['model'] is None for t in m['tasks']))
        b.codex_event('thread/started',{'thread':{'id':'one','source':{'thread_spawn':{'agent_role':'worker'}},'model':'child-model'}},'codex')
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual(b.msg()['tasks'][0]['model'],'child-model')
        b.codex_event('turn/completed',{'threadId':'one'},'codex')
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual(b.msg()['task_count'],1)
        (self.proc/'11/stat').unlink()
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual(b.msg()['tasks'],[])

    def test_verified_long_running_child_does_not_expire(self):
        b=busy.Board(None,verify_sources=True)
        b.set_context('codex',pid=11)
        b.start('codex:th:long',None,session='codex')
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual(len(b.items(now=__import__('time').time()+busy.TEMP_TTL+10)),1)
        b.stop('codex:th:long')
        self.assertEqual((b.msg()['task_count'],b.msg()['live_process_count']),(0,0))

    def test_unbound_rows_never_reach_runtime(self):
        b=busy.Board(None,verify_sources=True)
        b.start('wf:x','CEO')
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual(b.items(),[])
        self.assertIsNone(b.find('wf:x'))

    def test_short_account_and_plan_no_auth_or_email(self):
        auth={'auth_mode':'chatgpt','tokens':{'account_id':'fixture@example.invalid','access_token':'synthetic-private-value'}}
        (self.home/'.codex/auth.json').write_text(json.dumps(auth))
        f=self.sample()
        self.assertTrue(f['detected']['account'].startswith('acct:'))
        self.assertEqual(f['detected']['subscription'],'chatgpt')
        wire=json.dumps(f)
        for sensitive in ('example.invalid','synthetic-private-value','access_token',str(self.home)):
            self.assertNotIn(sensitive,wire)

    def test_unsafe_model_never_leaves_sampler(self):
        self.add(11,10,'codex',['codex','exec','--model','person@example.invalid'])
        self.assertIsNone(self.sample()['model'])
        self.assertIsNone(probe.identifier('https://fixture.invalid/private'))

    def test_claude_flags_and_plan(self):
        self.add(11,10,'claude',['claude','-p','x','--model','sonnet','--effort','high'])
        (self.home/'.claude').mkdir()
        (self.home/'.claude/.credentials.json').write_text(json.dumps({'claudeAiOauth':{'subscriptionType':'max','accessToken':'synthetic-private-value'}}))
        d=self.sample()['detected']
        self.assertEqual((d['harness'],d['model'],d['effort'],d['subscription']),('claude','sonnet','high','max'))

    def test_opencode_flags(self):
        self.add(11,10,'opencode',['opencode','run','--model=openai/test'])
        self.assertEqual((self.sample()['harness'],self.sample()['model']),('opencode','openai/test'))

    def test_multiple_distinct_harnesses_are_unknown(self):
        self.add(12,10,'claude',['claude','--model','sonnet'])
        self.assertIsNone(self.sample()['harness'])
        self.assertEqual(self.sample()['live_pids'],[11,12])

    def test_unit_cgroup_source(self):
        b=busy.Board(None,verify_sources=True)
        b.outside=[busy.item('unit:agentj-p508.service','P508','ceo','Test')|{'cg':'/fixture'}]
        with patch.object(busy,'cg_pids',return_value=[10,11]):
            b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual(b.items()[0]['harness'],'codex')

    def test_shared_pid_is_deduplicated(self):
        b=busy.Board(None,verify_sources=True)
        b.outside=[{'key':k,'who':None,'kind':'temp','label':'X','since':1,'pid':11} for k in ('a','b')]
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual((b.msg()['task_count'],b.msg()['live_process_count']),(2,1))

    def test_cli_uses_runtime_snapshot(self):
        from types import SimpleNamespace
        expected={'complete':True,'t':'busy','tasks':[],'task_count':0,'unknown_count':0,'live_process_count':0,'by':'Fixture'}
        a=SimpleNamespace(busy_cmd='list',no_units=True,json=True)
        with patch.object(busy,'_root',return_value=self.root), patch('agentj.cli._ctl_quiet',return_value={'ok':True,'busy':expected}), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(busy.cmd_busy(a),0)
        self.assertEqual(json.loads(out.getvalue()),expected)

    def test_output_count_matrix(self):
        """A frozen runtime snapshot has one count contract across CLI flags and serve."""
        from types import SimpleNamespace
        from copy import deepcopy
        from agentj.serve import Host
        fixtures = json.loads((Path(__file__).parent / 'fixtures/p508-counts.json').read_text())
        fixtures += [dict(f, unit=True) for f in fixtures]
        for fixture in fixtures:
            statuses = fixture['statuses']
            b = busy.Board(None)
            b.outside = [busy.item('child:' + str(i), 'Fixture', 'temp', 'Task') | {'status': status}
                         for i, status in enumerate(statuses)]
            b.evidence = {v['key']: {'live_pids': fixture['pids'][i]} for i, v in enumerate(b.outside)}
            if fixture.get('unit'):
                unit = busy.item('unit:fixture', 'Unit fixture', 'ceo', 'Unit task')
                b.outside.append(unit)
                b.evidence[unit['key']] = {'live_pids': [90]}
            host = SimpleNamespace(busy=b, st=SimpleNamespace(agent_name=lambda: 'Fixture'),
                                   wf=SimpleNamespace(last=None))
            snapshot = Host.busy_msg(host)
            expected = (fixture['task_count'] + bool(fixture.get('unit')), fixture['unknown_count'],
                        fixture['live_process_count'] + bool(fixture.get('unit')))
            def counts(m):
                return tuple(m[k] for k in ('task_count', 'unknown_count', 'live_process_count'))
            self.assertEqual(counts(snapshot), expected)
            self.assertEqual(counts(b.msg()), expected)
            for online in (True, False):
                for no_units in (True, False):
                    for as_json in (True, False):
                        with self.subTest(statuses=statuses, unit=fixture.get('unit', False), online=online, no_units=no_units, json=as_json):
                            a = SimpleNamespace(busy_cmd='list', no_units=no_units, json=as_json)
                            response = {'ok': True, 'busy': deepcopy(snapshot)} if online else None
                            with patch.object(busy, '_root', return_value=self.root), \
                                 patch('agentj.cli._ctl_quiet', return_value=response), \
                                 patch.object(busy, 'Board', return_value=b), \
                                 patch.object(b, 'refresh_outside') as refresh, patch.object(b, 'refresh_sources'), \
                                 contextlib.redirect_stdout(io.StringIO()) as out:
                                self.assertEqual(busy.cmd_busy(a), 0)
                            if not online:
                                refresh.assert_called_once_with(units=True)
                            shown = len(statuses) + bool(fixture.get('unit') and not no_units)
                            if as_json:
                                result = json.loads(out.getvalue())
                                self.assertEqual(counts(result), expected)
                                self.assertEqual(len(result['tasks']), shown)
                                self.assertEqual(any(t['id'].startswith('unit:') for t in result['tasks']),
                                                 bool(fixture.get('unit') and not no_units))
                            else:
                                self.assertIn(f'task_count={expected[0]} unknown_count={expected[1]} live_process_count={expected[2]}', out.getvalue())
                                self.assertEqual('Unit fixture · Unit task' in out.getvalue(),
                                                 bool(fixture.get('unit') and not no_units))

    def test_capacity_uses_active_count(self):
        from types import SimpleNamespace
        b = busy.Board(None)
        b.outside = [busy.item('child:u', None, 'temp', 'Unknown') | {'status': 'unknown'}]
        with patch.object(busy, 'capacity', return_value={'can_start': 2}) as cap:
            self.assertEqual(b.sample_cap(), 2)
            cap.assert_called_once_with(0)
        for as_json in (True, False):
            a = SimpleNamespace(busy_cmd='capacity', json=as_json)
            with patch.object(busy, '_root', return_value=self.root), \
                 patch.object(busy, 'Board', return_value=b), \
                 patch.object(b, 'refresh_outside'), patch.object(b, 'refresh_sources'), \
                 patch.object(busy, 'capacity', wraps=busy.capacity) as cap, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                busy.cmd_busy(a)
            cap.assert_called_once_with(0)
            if as_json:
                self.assertEqual(json.loads(out.getvalue())['running'], 0)
            else:
                self.assertIn('running 0', out.getvalue())

    def test_counts_only_published_rows_and_unit_filter(self):
        from types import SimpleNamespace
        b = busy.Board(None)
        b.outside = [busy.item('child:unknown', None, 'temp', 'Unknown') | {'status': 'unknown'},
                     busy.item('unit:fixture', None, 'ceo', 'Unit')]
        b.evidence = {'child:unknown': {'live_pids': [11, 11]},
                      'unit:fixture': {'live_pids': [12]}, 'not-published': {'live_pids': [99]}}
        snapshot = b.msg()
        self.assertEqual((snapshot['task_count'], snapshot['unknown_count'], snapshot['live_process_count']), (1, 1, 2))
        a = SimpleNamespace(busy_cmd='list', no_units=True, json=True)
        with patch.object(busy, '_root', return_value=self.root), \
             patch('agentj.cli._ctl_quiet', return_value={'ok': True, 'busy': snapshot}), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            busy.cmd_busy(a)
        m = json.loads(out.getvalue())
        self.assertEqual((m['task_count'], m['unknown_count'], m['live_process_count']), (1, 1, 2))
        self.assertEqual([t['id'] for t in m['tasks']], ['child:unknown'])

    def test_api_environment_does_not_claim_a_stale_oauth_account(self):
        (self.home/'.codex/auth.json').write_text(json.dumps({'auth_mode':'chatgpt','tokens':{'account_id':'old-login'}}))
        self.add(11,10,'codex',['codex'],env={'OPENAI_API_KEY':'synthetic-private-value'})
        f=self.sample()
        self.assertIsNone(f['detected']['account'])
        self.assertIsNone(f['detected']['subscription'])
        self.assertNotIn('synthetic-private-value',json.dumps(f))

    def test_node_claude_and_spoofed_argv_zero(self):
        self.add(11,10,'node',['node','/fixture/node_modules/@anthropic-ai/claude-code/cli.js','--model','sonnet'])
        self.assertEqual(self.sample()['harness'],'claude')
        self.add(11,10,'sleep',['codex','10'])
        self.assertIsNone(self.sample()['harness'])

    def test_opencode_inline_config(self):
        self.add(11,10,'opencode',['opencode','serve'],env={'OPENCODE_CONFIG_CONTENT':'{"model":"provider/inline"}'})
        self.assertEqual(self.sample()['model'],'provider/inline')

    def test_live_namespaced_start_does_not_expire_without_a_heartbeat(self):
        d=self.proc/'11/ns';d.mkdir();(d/'pid').symlink_to('pid:[fixture]')
        (self.proc/'11/status').write_text('NSpid: 11 1\n')
        it=busy.item('cli:namespace','Worker','ceo','Task',pid=1,ns='pid:[fixture]')
        it['proc_start']='123'
        path=busy.write_item(self.root,it)
        os.utime(path,(1,1))
        with patch.object(busy,'pidns',return_value='pid:[host]'),patch.object(probe,'Processes',return_value=probe.Processes(self.proc)):
            self.assertEqual(len(busy.read_items(self.root)),1)

    def test_namespace_pid_not_confused_with_host_pid(self):
        d=self.proc/'11/ns';d.mkdir();(d/'pid').symlink_to('pid:[fixture]')
        (self.proc/'11/status').write_text('NSpid:\t11\t1\n')
        f=self.sample({'pid':1,'ns_other':True,'source_ns':'pid:[fixture]'})
        self.assertEqual(f['source']['pid'],11)
        self.assertIsNone(self.sample({'pid':11,'ns_other':True,'source_ns':'pid:[wrong]'}))


class RealProcess(unittest.TestCase):
    def test_real_app_server_liveness_and_cleanup(self):
        import shutil
        import time
        executable=shutil.which('codex')
        # Resolve the installed tool once; the user-level wrapper intentionally refuses an isolated HOME.
        located=subprocess.run(['mise','where','codex'],capture_output=True,text=True,timeout=5) if shutil.which('mise') else None
        if located and located.returncode==0:
            executable=str(Path(located.stdout.strip())/'bin/codex')
        if not executable:
            self.skipTest('Codex binary unavailable')
        with tempfile.TemporaryDirectory() as home:
            p=subprocess.Popen([executable,'-c','model="gpt-fixture"','app-server'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               env={'HOME':home,'CODEX_HOME':home,'PATH':os.environ['PATH']},cwd=home)
            try:
                result=None
                for _ in range(60):
                    result=probe.fields({'pid':p.pid},probe.Processes())
                    if result and result['harness']=='codex':
                        break
                    time.sleep(.05)
                self.assertIsNotNone(result)
                self.assertEqual((result['harness'],result['model']),('codex','gpt-fixture'))
            finally:
                p.terminate()
                p.wait(timeout=5)
                p.stdin.close()
            self.assertIsNone(probe.fields({'pid':p.pid},probe.Processes()))


class NativeReconcile(unittest.IsolatedAsyncioTestCase):
    async def test_lost_close_poll_removes_thread_without_reading_turns(self):
        from agentj.agent_codex import CodexAgent
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        b=busy.Board(None)
        b.start('codex:th:one',None,session='codex')
        a=object.__new__(CodexAgent)
        a.host=SimpleNamespace(busy=b);a.cfg={};a.proc=SimpleNamespace(pid=11)
        a.call=AsyncMock(return_value={'thread':{'status':{'type':'idle'}}})
        await a.refresh_busy()
        self.assertEqual(b.items(),[])
        a.call.assert_awaited_once_with('thread/read',{'threadId':'one','includeTurns':False},timeout=2)


class ContinuationC(unittest.TestCase):
    setUp = Provenance.setUp
    add = Provenance.add
    sample = Provenance.sample
    def test_native_workflow_empty_metadata_preserves_argv(self):
        self.add(11,10,'claude',['claude','--model','sonnet','--effort','high'])
        f=self.sample({'pid':10,'native':True,'model':None,'effort':None})
        self.assertEqual((f['model'],f['detected']['effort']),('sonnet','high'))

    def test_native_child_never_inherits_parent_model(self):
        self.assertIsNone(self.sample({'pid':11,'native':True,'native_id':'child'})['model'])

    def test_lost_end_expires_even_when_host_alive(self):
        for scope in ('own','opencode'):
            with self.subTest(scope=scope):
                b=busy.Board(None,verify_sources=True)
                b.set_context(scope,pid=11)
                b.start(scope+':task:child',None,session=scope)
                b.refresh_sources(probe.Processes(self.proc))
                b.mem[scope+':task:child']['since']=1
                b.mem[scope+':task:child']['activity_at']=1
                b.mem[scope+':task:child']['checked_at']=1
                self.assertEqual(b.items(now=busy.TEMP_TTL+2),[])

    def test_unknown_not_counted_and_activity_extends_ttl(self):
        b=busy.Board(None,verify_sources=True);b.set_context('own',pid=11)
        b.start('own:task:child',None,session='own')
        b.mem['own:task:child']['since']=1
        b.note('own:task:child','Inspecting')
        b.refresh_sources(probe.Processes(self.proc))
        m=b.msg()
        b.native_status('own:task:child','unknown')
        m=b.msg()
        self.assertEqual(m['task_count'],0)
        self.assertEqual(m['tasks'][0]['status'],'unknown')
        self.assertEqual(len(b.items()),1)

    def test_claude_transcript_terminal_result_closes_live_host_child(self):
        b=busy.Board(None,verify_sources=True);b.set_context('own',pid=11)
        b.start('own:tu:tool-one',None,session='own')
        transcript=self.root/'session.jsonl'
        transcript.write_text(json.dumps({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'tool-one','content':'done'}]}})+'\n')
        b.reconcile_claude('own',transcript)
        self.assertEqual(b.mem,{})

    def test_claude_async_launch_is_not_terminal(self):
        b=busy.Board(None,verify_sources=True);b.set_context('own',pid=11)
        b.start('own:tu:tool-one',None,session='own')
        transcript=self.root/'session.jsonl'
        transcript.write_text(json.dumps({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'tool-one','content':'Async agent launched agentId: worker-one'}]}})+'\n')
        b.reconcile_claude('own',transcript)
        self.assertTrue(b.mem)

    def test_shared_claude_lost_stop_uses_parent_task_result(self):
        b=busy.Board(None);b.set_context('shared:session',pid=11)
        b.hook_event({'hook_event_name':'PreToolUse','session_id':'session','tool_use_id':'call','tool_name':'Agent','tool_input':{'subagent_type':'worker'}})
        b.hook_event({'hook_event_name':'SubagentStart','session_id':'session','agent_id':'child','agent_type':'worker'})
        transcript=self.root/'parent.jsonl'
        transcript.write_text(json.dumps({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'call','content':'done'}]}})+'\n')
        b.reconcile_claude('shared:session',transcript)
        self.assertEqual(b.mem,{})

    def test_native_background_notification_closes_lost_end(self):
        b=busy.Board(None);b.set_context('own',pid=11)
        b.start('own:task:child',None,session='own')
        transcript=self.root/'parent.jsonl'
        transcript.write_text(json.dumps({'type':'user','origin':{'kind':'task-notification'},'message':{'content':'<task-notification><task-id>child</task-id><status>completed</status></task-notification>'}})+'\n')
        b.reconcile_claude('own',transcript)
        self.assertEqual(b.mem,{})

    def test_user_quoting_notification_is_not_native_state(self):
        b=busy.Board(None);b.set_context('own',pid=11)
        b.start('own:task:child',None,session='own')
        transcript=self.root/'parent.jsonl'
        transcript.write_text(json.dumps({'type':'user','message':{'content':'<task-notification><task-id>child</task-id><status>completed</status></task-notification>'}})+'\n')
        b.reconcile_claude('own',transcript)
        self.assertTrue(b.mem)

    def test_confirmed_running_child_retained_past_original_ttl(self):
        b=busy.Board(None,verify_sources=True);b.set_context('opencode',pid=11)
        b.start('opencode:s:child',None,session='opencode')
        b.mem['opencode:s:child']['since']=1
        b.native_status('opencode:s:child','running',now=busy.TEMP_TTL+20)
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual(b.items(now=busy.TEMP_TTL+21)[0]['status'],'running')
        self.assertEqual(b.items(now=2*busy.TEMP_TTL+21),[])


class OpenCodeReconcile(unittest.IsolatedAsyncioTestCase):
    async def test_live_host_completed_child_is_removed(self):
        from agentj.agent_opencode import OpenCodeAgent
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        b=busy.Board(None);b.start('opencode:task:call',None,session='opencode')
        b._alias('opencode:s:child','opencode:task:call')
        a=object.__new__(OpenCodeAgent);a.host=SimpleNamespace(busy=b);a.cfg={};a.proc=SimpleNamespace(pid=11)
        a.client=SimpleNamespace(request=AsyncMock(return_value=(200,{})))
        await a.refresh_busy()
        self.assertEqual(b.mem,{})
        a.client.request.assert_awaited_once_with('GET','/session/status',timeout=2)


    async def test_unknown_api_state_does_not_reuse_running_observation(self):
        from agentj.agent_opencode import OpenCodeAgent, HTTPError
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        b=busy.Board(None);b.start('opencode:s:child',None,session='opencode')
        b.native_status('opencode:s:child','running')
        a=object.__new__(OpenCodeAgent);a.host=SimpleNamespace(busy=b);a.cfg={};a.proc=None
        a.client=SimpleNamespace(request=AsyncMock(side_effect=HTTPError('unavailable')))
        await a.refresh_busy()
        self.assertEqual(b.msg()['task_count'],0)
        self.assertEqual(b.items()[0]['status'],'unknown')

    async def test_v2_idle_child_is_removed(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        b=busy.Board(None);b.start('opencode:s:child',None,session='opencode')
        a=object.__new__(OpenCodeV2Agent);a.host=SimpleNamespace(busy=b);a.cfg={};a.proc=None
        a.client=SimpleNamespace(request=AsyncMock(return_value=(200,{'data':{'time':{'idle':__import__('time').time()*1000+1}}})))
        await a.refresh_busy()
        self.assertEqual(b.mem,{})
        self.assertEqual(a.client.request.await_count, 2)
        a.client.request.assert_awaited_with('GET','/api/session/child',timeout=2)

    async def test_workflow_without_busy_selected_through_serve_loop(self):
        from agentj.serve import Host
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        b=busy.Board(None)
        b.start('wf:fixture','CEO',kind='ceo',model='already-detected')
        host=SimpleNamespace(busy=b,scheduler=SimpleNamespace(proc=SimpleNamespace(pid=11),runner=SimpleNamespace()),agent=SimpleNamespace(),
                             background_done=SimpleNamespace(tick=Mock()),busy_changed=Mock(),theme_refresh=Mock(return_value=False))
        with patch.object(b,'refresh_outside'),patch.object(b,'refresh_sources'),patch.object(b,'sample_load'),patch('agentj.serve.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await Host.busy_loop(host)
        self.assertEqual(b.mem['wf:fixture']['model'],'already-detected')


class ContinuationD(unittest.TestCase):
    setUp = Provenance.setUp
    add = Provenance.add
    def test_claude_start_counts_before_first_tool_and_stop_clears(self):
        b = busy.Board(None, verify_sources=True)
        b.set_context('shared:parent', pid=11)
        b.hook_event({'hook_event_name':'SubagentStart', 'session_id':'parent',
                      'agent_id':'child', 'agent_type':'worker'})
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual((b.msg()['task_count'], b.msg()['live_process_count']), (1, 1))
        self.assertEqual(b.items()[0]['status'], 'running')
        b.hook_event({'hook_event_name':'SubagentStop', 'session_id':'parent', 'agent_id':'child'})
        self.assertEqual(b.msg()['task_count'], 0)

    def test_opencode_v1_start_counts_and_end_clears(self):
        b = busy.Board(None, verify_sources=True)
        b.set_context('opencode', pid=11)
        part = {'sessionID':'parent', 'type':'tool', 'tool':'task', 'callID':'call',
                'state':{'status':'running', 'input':{'description':'Inspect'}}}
        b.opencode_event({'type':'message.part.updated', 'properties':{'part':part}}, 'parent')
        b.refresh_sources(probe.Processes(self.proc))
        self.assertEqual(b.msg()['task_count'], 1)
        part['state']['status'] = 'completed'
        b.opencode_event({'type':'message.part.updated', 'properties':{'part':part}}, 'parent')
        self.assertEqual(b.msg()['task_count'], 0)

    def test_opencode_start_counts_and_lost_end_expires(self):
        b = busy.Board(None, verify_sources=True)
        b.set_context('opencode', pid=11)
        with patch('agentj.busy.time.time', return_value=100):
            b.opencode2_event({'type':'session.created', 'data':{'id':'child', 'parentID':'parent'}}, 'parent')
            b.opencode2_event({'type':'session.execution.started', 'data':{'sessionID':'child'}}, 'parent')
            b.refresh_sources(probe.Processes(self.proc))
            self.assertEqual((b.msg()['task_count'], b.msg()['live_process_count']), (1, 1))
        self.assertEqual(b.items(now=100 + busy.TEMP_TTL + 1), [])


class OpenCodeContinuationD(unittest.IsolatedAsyncioTestCase):
    def agent(self, request):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        b = busy.Board(None)
        b.opencode2_event({'type':'session.created', 'data':{'id':'child', 'parentID':'parent'}}, 'parent')
        b.opencode2_event({'type':'session.execution.started', 'data':{'sessionID':'child'}}, 'parent')
        a = object.__new__(OpenCodeV2Agent)
        a.host = SimpleNamespace(busy=b)
        a.cfg = {}
        a.proc = None
        a.client = SimpleNamespace(request=AsyncMock(side_effect=request))
        return a, b

    async def test_old_idle_keeps_start_observation_without_renewing_ttl(self):
        async def request(method, path, **kw):
            return (404, None) if path.endswith('/active') else (200, {'data':{'time':{'idle':99000}}})
        with patch('agentj.busy.time.time', return_value=100):
            a, b = self.agent(request)
            self.assertEqual(b.msg()['task_count'], 1)
        await a.refresh_busy()
        self.assertEqual(b.items(now=101)[0]['status'], 'running')
        self.assertEqual(b.mem['opencode:s:child']['checked_at'], 100)
        self.assertEqual(b.items(now=100 + busy.TEMP_TTL + 1), [])

    async def test_poll_confirms_active_and_extends_ttl(self):
        async def request(method, path, **kw):
            self.assertEqual(path, '/api/session/active')
            return 200, {'data':{'child':{'type':'busy'}}}
        with patch('agentj.busy.time.time', return_value=100):
            a, b = self.agent(request)
        b.native_status('opencode:s:child', 'unknown')
        with patch('agentj.busy.time.time', return_value=100 + busy.TEMP_TTL):
            await a.refresh_busy()
            self.assertEqual(b.msg()['task_count'], 1)
        self.assertEqual(b.items(now=101 + busy.TEMP_TTL)[0]['status'], 'running')

    async def test_new_idle_closes_after_active_list_misses_child(self):
        async def request(method, path, **kw):
            return (200, {'data':{}}) if path.endswith('/active') else (200, {'data':{'time':{'idle':101000}}})
        with patch('agentj.busy.time.time', return_value=100):
            a, b = self.agent(request)
        await a.refresh_busy()
        self.assertEqual(b.msg()['task_count'], 0)
        self.assertEqual(b.mem, {})

    async def test_restarted_execution_ignores_idle_from_previous_execution(self):
        async def request(method, path, **kw):
            return (404, None) if path.endswith('/active') else (200, {'data':{'time':{'idle':150000}}})
        with patch('agentj.busy.time.time', return_value=100):
            a, b = self.agent(request)
        with patch('agentj.busy.time.time', return_value=200):
            b.opencode2_event({'type':'session.execution.started', 'data':{'sessionID':'child'}}, 'parent')
            await a.refresh_busy()
            self.assertEqual(b.msg()['task_count'], 1)

    async def test_poll_cannot_override_new_start_during_await(self):
        async def request(method, path, **kw):
            if path.endswith('/active'):
                return 200, {'data':{}}
            b.opencode2_event({'type':'session.execution.started', 'data':{'sessionID':'child'}}, 'parent')
            return 200, {'data':{'time':{'idle':999999}}}
        with patch('agentj.busy.time.time', return_value=100):
            a, b = self.agent(request)
            await a.refresh_busy()
            self.assertEqual(b.msg()['task_count'], 1)
