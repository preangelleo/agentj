"""Relay aliases use actual local IPC and the existing Host native policy, never naked exec."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from agentj import approval_material, busy, busy_launch, busy_native, busy_probe, display_privacy, peer_guard
from agentj.state import State

HOST = Path(__file__).resolve().parents[1]

class Journey(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root/'work'; self.work.mkdir()
        self.env = {**os.environ, 'HOME':str(self.root), 'CODEX_HOME':str(self.root/'codex'),
                    'XDG_CONFIG_HOME':str(self.root/'cfg'), 'AGENTJ_STATE_DIR':str(self.root/'state'),
                    'PYTHONPATH':str(HOST)}
        self.patcher=patch.dict(os.environ,self.env); self.patcher.start();self.addCleanup(self.patcher.stop)
        self.config=self.root/'codex/config.toml';self.config.parent.mkdir()
        self.configure()
        self.st=State();self.st.sock_path.parent.mkdir(parents=True,exist_ok=True)
        launch=busy_launch.Launch(self.st.root)
        self.agent=SimpleNamespace(kind='codex',cfg={'kind':'codex','dir':str(self.work),'fence':True},
           session={'pid':os.getpid(),'procStart':busy_probe.start_id(os.getpid())},
           thread={'sandbox':{'type':'readOnly'},'modelProvider':'relay','model':'fixture-model'},human={},config_stale=lambda:False)
        self.host=SimpleNamespace(st=self.st, agent=self.agent, agent_cfg=self.agent.cfg,
              busy=SimpleNamespace(launch=launch), stopped=lambda:False, _agent_pids=lambda:[os.getpid()])
        self.started=[];self.received=[]
        self.origin=patch.object(approval_material,'verified_caller_origin',return_value=None);self.origin.start();self.addCleanup(self.origin.stop)
        async def child(host,cfg,text,mode,it):
            self.started.append((mode,cfg['fence'],text))
            p=await asyncio.create_subprocess_exec(sys.executable,'-c',
                "from pathlib import Path;Path('artifact.txt').write_text('fixture child completed')",cwd=cfg['dir'])
            await p.wait()
        self.runner=patch.object(busy_native,'run',side_effect=child);self.runner.start();self.addCleanup(self.runner.stop)
        from agentj import agentsrelay_dispatch as relay
        self.relay=relay
        from agentj.shared_hook import peer_pid
        async def serve(reader,writer):
            try:
                req=json.loads(await reader.readline());self.received.append(req)
                pid,uid=peer_pid(writer.get_extra_info('socket'))
                try: result=await relay.start(self.host,req,pid,uid)
                except ValueError as exc:result={'ok':False,'error':str(exc)}
                writer.write((json.dumps(result)+'\n').encode());await writer.drain()
            finally:writer.close();await writer.wait_closed()
        self.server=await asyncio.start_unix_server(serve,path=self.st.sock_path)
        self.addAsyncCleanup(self.close_server)

    async def close_server(self):
        self.server.close();await self.server.wait_closed()
        for row in getattr(self.host,'_busy_native',{}).values():
            await row['task']

    def configure(self,url='https://agentsrelay.net/v1'):
        self.config.write_text('model_provider="relay"\n[model_providers.relay]\nbase_url='+json.dumps(url)+'\nwire_api="responses"\nenv_key="AGENTSRELAY_OPENAI_KEY"\n')

    async def cli(self,*args):
        p=await asyncio.create_subprocess_exec(sys.executable,'-m','agentj.cli','relay',*args,
            cwd=self.work,env=self.env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(p.communicate(),15)
        return p.returncode,out.decode(),err.decode()

    async def test_real_cli_socket_kernel_peer_and_child_inherit_read_only_ceiling(self):
        rc,out,err=await self.cli('codex','--sandbox','danger-full-access','write the fixture report')
        self.assertEqual(rc,0,err+out);reply=json.loads(out)
        self.assertEqual(reply['sandbox'],'read-only');self.assertTrue(reply['fence'])
        self.assertEqual(set(reply),{'ok','key','route','manager','sandbox','fence'})
        await self.host._busy_native[reply['key']]['task']
        self.assertEqual(self.started,[('read-only',True,'write the fixture report')])
        self.assertEqual((self.work/'artifact.txt').read_text(),'fixture child completed')
        self.assertNotIn('prompt',out);self.assertNotIn('write the fixture',out)

    async def test_official_or_credential_bearing_config_never_starts_relay(self):
        for url in ('https://api.openai.com/v1','http://agentsrelay.net/v1',
                    'https://agentsrelay.net/v1?key=synthetic','https://agentsrelay.net.evil.example/v1'):
            self.configure(url)
            rc,out,_=await self.cli('codex','do work')
            self.assertNotEqual(rc,0);self.assertEqual(json.loads(out)['error'],'relay_profile_required')
        self.assertEqual(self.started,[])

    async def test_known_secret_prompt_is_refused_before_child_or_provider(self):
        secret='fixture-value-for-outbound-3579';(self.work/'.env').write_text('CUSTOM_SECRET='+secret+'\n')
        rc,out,_=await self.cli('codex','use '+secret)
        self.assertNotEqual(rc,0);self.assertEqual(json.loads(out)['error'],'relay_text_refused')
        self.assertNotIn(secret,out);self.assertFalse(self.started)

    async def test_unknown_source_wrong_uid_stale_profile_stop_and_incomplete_scan_refuse(self):
        req={'key':'run:123456abcdef','cmd':'agentsrelay_native','cwd':str(self.work),'argv':['codex','exec','work']}
        with self.assertRaises(ValueError):await self.relay.start(self.host,req,os.getpid(),os.getuid()+1)
        self.agent.session={'pid':1,'procStart':'not-a-current-root'}
        with self.assertRaises(ValueError):await self.relay.start(self.host,req,os.getpid(),os.getuid())
        self.agent.session={'pid':os.getpid(),'procStart':busy_probe.start_id(os.getpid())}
        self.agent.config_stale=lambda:True
        with self.assertRaisesRegex(ValueError,'relay_profile_required'):await self.relay.start(self.host,req,os.getpid(),os.getuid())
        self.agent.config_stale=lambda:False;self.host.stopped=lambda:True
        with self.assertRaises(ValueError):await self.relay.start(self.host,req,os.getpid(),os.getuid())
        self.host.stopped=lambda:False;(self.work/'.env').mkdir()
        with self.assertRaisesRegex(ValueError,'relay_text_refused'):await self.relay.start(self.host,req,os.getpid(),os.getuid())
        self.assertFalse(self.started)

    async def test_unicode_and_json_escaped_secrets_and_fragments_do_not_reach_child(self):
        values = [('unicode', '雪山旅程甲乙丙丁戊己庚辛壬癸'),
                  ('json-escape', 'ab"cd\\ef"gh\\ij"kl\\mn')]
        for label, value in values:
            for part in ('full', 'fragment'):
                with self.subTest(kind=label, part=part):
                    self.host.__dict__.pop('_display_secret_index', None)
                    text = value if part == 'full' else value[2:10]
                    with patch.dict(os.environ, {'CUSTOM_SECRET': value}):
                        rc, out, err = await self.cli('codex', 'use ' + text)
                    self.assertNotEqual(rc, 0)
                    self.assertEqual(json.loads(out)['error'], 'relay_text_refused')
                    self.assertNotIn(text, out + err)
        self.assertFalse(self.started)

    async def test_environment_secret_changes_refresh_the_same_host(self):
        rc, out, err = await self.cli('codex', 'first safe task')
        self.assertEqual(rc, 0, out + err)
        await self.host._busy_native[json.loads(out)['key']]['task']
        count = len(self.started)
        for label, value in [('new', 'opaque-first-environment-13579'),
                             ('rotated', 'opaque-second-environment-24680')]:
            with self.subTest(change=label), patch.dict(os.environ, {'CUSTOM_SECRET': value}):
                rc, out, err = await self.cli('codex', 'use ' + value)
                self.assertNotEqual(rc, 0)
                self.assertEqual(json.loads(out)['error'], 'relay_text_refused')
                self.assertNotIn(value, out + err)
        self.assertEqual(len(self.started), count)

    async def test_route_change_during_outbound_scan_refuses_without_launch(self):
        req={'key':'run:123456abcdef','cmd':'agentsrelay_native','cwd':str(self.work),'argv':['codex','exec','work']}
        def changed(*args):self.configure('https://api.openai.com/v1')
        with patch.object(self.relay,'_text',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'relay_profile_required'):
                await self.relay.start(self.host,req,os.getpid(),os.getuid())
        self.assertFalse(self.started)

    async def test_claude_is_explicitly_unavailable_and_transport_ambiguity_never_replays(self):
        rc,out,_=await self.cli('claude','work')
        self.assertNotEqual(rc,0);self.assertEqual(json.loads(out)['error'],'claude_host_adapter_unavailable')
        self.assertFalse(self.received)
        self.server.close();await self.server.wait_closed()
        async def silent(reader,writer):
            self.received.append(json.loads(await reader.readline()));writer.close();await writer.wait_closed()
        self.st.sock_path.unlink(missing_ok=True)
        self.server=await asyncio.start_unix_server(silent,path=self.st.sock_path)
        rc,out,_=await self.cli('codex','work')
        self.assertNotEqual(rc,0);reply=json.loads(out)
        self.assertEqual(reply['error'],'outcome_unknown');self.assertEqual(len(self.received),1)
        self.assertFalse(self.started)

class RouteSnapshot(unittest.IsolatedAsyncioTestCase):
    async def test_launch_and_rpc_use_same_closed_snapshot_after_native_config_changes(self):
        from agentj import agentsrelay_dispatch as relay
        from agentj.agent_codex import CodexAgent, RPCError
        profile=relay._RelayProfile('agentj_relay_'+'a'*32,'https://agentsrelay.net/v1','fixture-model')
        a=object.__new__(CodexAgent);a.cfg={'_agentsrelay_provider':profile}
        a.n=0;a.waits={};a.thread={'modelProvider':profile.id}
        sent=[]
        async def drain():pass
        a.proc=SimpleNamespace(stdin=SimpleNamespace(drain=drain))
        def send(message):
            sent.append(message);a.waits[message['id']].set_result({'result':{'ok':True}})
        a._send=send
        with patch('agentj.agent._bin',return_value='codex'):
            argv=a.argv()
        self.assertIn('model_provider="'+profile.id+'"',argv)
        self.assertIn('model_providers.'+profile.id+'.base_url="https://agentsrelay.net/v1"',argv)
        self.assertNotIn('Authorization',' '.join(argv))
        for method in ('thread/start','thread/resume'):
            await a.call(method,{'modelProvider':'openai','model':'other','config':{
                'model_provider':'openai','model_providers.'+profile.id+'.base_url':'https://evil.example',
                'approval_policy':'never'}})
            params=sent[-1]['params']
            self.assertEqual(params['modelProvider'],profile.id);self.assertEqual(params['model'],'fixture-model')
            self.assertEqual(params['config']['model_providers'][profile.id]['base_url'],profile.base_url)
            self.assertFalse(any(k.startswith('model_providers.') for k in params['config']))
            self.assertEqual(params['config']['approval_policy'],'never')
        await a.call('turn/start', {'modelProvider':'openai','model':'other','configOverrides':{'model_provider':'evil'},'sandboxPolicy':{'type':'readOnly'}})
        self.assertNotIn('modelProvider',sent[-1]['params']);self.assertNotIn('configOverrides',sent[-1]['params'])
        self.assertEqual(sent[-1]['params']['model'],'fixture-model')
        self.assertEqual(sent[-1]['params']['sandboxPolicy'],{'type':'readOnly'})
        a.thread={'modelProvider':'openai'}
        with self.assertRaises(RPCError):await a.call('turn/start',{})
        self.assertEqual(len(sent),3)
        for invalid in ({'id':profile.id,'base_url':profile.base_url,'model':profile.model},
                        relay._RelayProfile('id.with.dot',profile.base_url,profile.model),
                        relay._RelayProfile(profile.id,'https://evil.example',profile.model),
                        relay._RelayProfile(profile.id,profile.base_url,'model\nsecret')):
            with self.assertRaises(ValueError):relay.provider_options(invalid)

    async def test_real_app_server_pipe_receives_only_pinned_route_and_keeps_native_policy(self):
        from agentj.agent_codex import CodexAgent
        from agentj import agentsrelay_dispatch as relay
        profile=relay._RelayProfile('agentj_relay_'+'b'*32,'https://agentsrelay.net/v1','fixture-model')
        script="import sys,json\nfor line in sys.stdin:\n r=json.loads(line);print(json.dumps({'id':r['id'],'result':r['params']}),flush=True)\n"
        p=await asyncio.create_subprocess_exec(sys.executable,'-u','-c',script,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
        a=object.__new__(CodexAgent);a.cfg={'_agentsrelay_provider':profile};a.n=0;a.waits={}
        a.thread={};a.proc=p
        async def wire():
            while line:=await p.stdout.readline():
                msg=json.loads(line);a.waits[msg['id']].set_result(msg)
        reader=asyncio.create_task(wire())
        try:
            result=await a.call('thread/start',{'modelProvider':'wrong','sandbox':'read-only','approvalPolicy':'untrusted'})
            self.assertEqual(result['modelProvider'],profile.id)
            self.assertEqual(result['config']['model_providers'][profile.id]['env_key'],'AGENTSRELAY_OPENAI_KEY')
            self.assertEqual((result['sandbox'],result['approvalPolicy']),('read-only','untrusted'))
            self.assertEqual(a.provider(),{'id':'agentsrelay','host':'agentsrelay.net','relay':True})
        finally:
            p.stdin.close();await p.wait();await reader

class ModelEnvironment(unittest.IsolatedAsyncioTestCase):
    async def test_only_host_profile_can_deliver_one_existing_model_key_to_real_child(self):
        from agentj import agentsrelay_dispatch as relay, fence
        profile=relay._RelayProfile('agentj_relay_'+'c'*32,'https://agentsrelay.net/v1','fixture-model')
        with tempfile.TemporaryDirectory() as tmp:
            base={'HOME':tmp,'CODEX_HOME':tmp+'/codex','PATH':os.environ.get('PATH','/usr/bin:/bin'),
                  'AGENTSRELAY_OPENAI_KEY':'synthetic-model-value-357902468',
                  'AGENTSRELAY_CLAUDE_KEY':'synthetic-other-relay-value',
                  'OPENAI_API_KEY':'synthetic-official-provider-value',
                  'AWS_SECRET_ACCESS_KEY':'synthetic-unrelated-value','HERDR_SOCKET_PATH':'/synthetic/control'}
            for kind in ('claude','codex','opencode'):
                plain=fence.auxiliary_environment(base,kind)
                self.assertNotIn('AGENTSRELAY_OPENAI_KEY',plain)
                self.assertNotIn('AGENTSRELAY_CLAUDE_KEY',plain)
            for fake in ({'id':profile.id,'base_url':profile.base_url,'model':profile.model}, True, 'AGENTSRELAY_OPENAI_KEY'):
                with self.assertRaises(ValueError):relay.native_model_key({'_agentsrelay_provider':fake})
            key=relay.native_model_key({'_agentsrelay_provider':profile})
            env=fence.auxiliary_environment(base,'codex',native_model_key=key)
            env=fence.launch_environment(env,kind='codex',native_model_key=key)
            self.assertEqual(env.get(key),base[key])
            for name in ('AGENTSRELAY_CLAUDE_KEY','OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY','HERDR_SOCKET_PATH'):
                self.assertNotIn(name,env)
            script="import os,json;print(json.dumps({'model':bool(os.getenv('AGENTSRELAY_OPENAI_KEY')),'other':any(os.getenv(k) for k in ('AGENTSRELAY_CLAUDE_KEY','OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY','HERDR_SOCKET_PATH'))}))"
            argv=[sys.executable,'-c',script]
            p=await asyncio.create_subprocess_exec(*argv,cwd=tmp,env=env,stdout=asyncio.subprocess.PIPE)
            out,_=await p.communicate();self.assertEqual(p.returncode,0)
            self.assertEqual(json.loads(out),{'model':True,'other':False})
            for value in (base[key],base['AGENTSRELAY_CLAUDE_KEY'],base['AWS_SECRET_ACCESS_KEY']):
                self.assertNotIn(value,out.decode());self.assertNotIn(value,' '.join(argv))
            self.assertEqual(list(Path(tmp).iterdir()),[],'model authentication leaves no credential file')
            for wrong in ('AWS_SECRET_ACCESS_KEY','AGENTSRELAY_CLAUDE_KEY','AGENTSRELAY_OPENAI_KEY=bad'):
                with self.assertRaises(ValueError):fence.auxiliary_environment(base,'codex',native_model_key=wrong)
            with self.assertRaises(ValueError):fence.auxiliary_environment(base,'claude',native_model_key=key)
