"""P501c plan discovery and isolated avatar selection regressions."""
import _hermetic
import unittest
from unittest.mock import patch
from agentj import channel_avatar as avatar

class Plans(unittest.TestCase):
    def test_same_harness_profiles_are_distinct_and_codex_recommended(self):
        from agentj.channel_plans import discover
        detected={'harnesses':[{'name':k,'installed':True,'supported':True,'logged_in':True} for k in ('claude','codex','kimi','qwen','dsh')]}
        profiles=[{'id':'relay','harness':'codex','model':'fixture'}, {'id':'other','harness':'codex','model':'fixture2'}]
        with patch('agentj.channel_plans.profile_available',return_value=True):plans=discover(detected,profiles)
        ids=[p['id'] for p in plans]
        for pid in ('codex','profile:relay','profile:other','kimi','qwen','dsh'):self.assertIn(pid,ids)
        self.assertTrue(avatar.choice({'kind':'claude'},{},plans)['ask'])
        self.assertEqual(avatar.choice({}, {},plans)['recommend'],'codex')
        self.assertEqual(avatar.choose('profile:other',{},plans),{'kind':'codex','model':'fixture2','channel_profile':'other'})

    def test_one_harness_with_two_profiles_still_asks_once(self):
        plans=[{'id':'profile:a','kind':'claude','config':{'kind':'claude','channel_profile':'a'}},
               {'id':'profile:b','kind':'claude','config':{'kind':'claude','channel_profile':'b'}}]
        self.assertTrue(avatar.choice({}, {},plans)['ask'])
        self.assertFalse(avatar.choice({}, {'asked':True},plans)['ask'])

    def test_missing_profile_never_silently_uses_main(self):
        with self.assertRaises(ValueError):avatar.choose('profile:missing',{'kind':'codex'},[])

    def test_domestic_standalone_is_not_selectable(self):
        for kind in ('kimi','qwen','dsh'):
            with self.assertRaises(ValueError):avatar.choose(kind,{'kind':'codex'},[kind])

class ProfileExecution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace as NS
        from agentj import provider_profiles as profiles
        from agentj.wechat import Sessions
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        patcher=patch.object(profiles,'root',return_value=self.root/'profiles')
        patcher.start();self.addCleanup(patcher.stop)
        self.cfg={'kind':'codex','model':'fixture','channel_profile':'a'}
        self.host=NS(st=NS(),agent_cfg={'kind':'claude'},external_channels=NS(store=NS(data={'avatar':{'config':self.cfg}})))
        self.session=Sessions(self.host,NS())
        for kind,pid in (('codex','a'),('claude','b'),('opencode','c')):
            profiles.define(pid,kind,'https://provider.example.test/v1','fixture')
            profiles.atomic(profiles.profile(pid)/'key',b'fixture-profile-material')

    async def test_selected_codex_profile_is_in_process_env_never_argv(self):
        from agentj import peer_session
        env=self.session.execution_env('codex',self.cfg)
        self.assertEqual(env['AGENTJ_CHANNEL_PROFILE_KEY'],'fixture-profile-material')
        with patch.object(peer_session,'codex_features',return_value=set(peer_session.CODEX_REQUIRED)):
            cmd=self.session._codex_argv('fixture','prompt',None,self.cfg,env)
        self.assertIn('model_provider="a"',cmd)
        self.assertIn('model_providers.a.env_key="AGENTJ_CHANNEL_PROFILE_KEY"',cmd)
        self.assertNotIn('fixture-profile-material',' '.join(cmd))
        self.assertEqual(self.host.agent_cfg,{'kind':'claude'})

    async def test_selected_claude_and_opencode_profiles_do_not_change_native_config(self):
        for kind,pid in (('claude','b'),('opencode','c')):
            self.cfg.update(kind=kind,channel_profile=pid,model='c/fixture' if kind=='opencode' else 'fixture')
            env=self.session.execution_env(kind,self.cfg)
            if kind=='claude':
                self.assertEqual(env['ANTHROPIC_AUTH_TOKEN'],'fixture-profile-material')
                self.assertEqual(env['ANTHROPIC_BASE_URL'],'https://provider.example.test')
            else:
                import json
                conf=json.loads(self.session._opencode_config('policy',env))
                self.assertEqual(conf['provider']['c']['options']['apiKey'],'fixture-profile-material')
                self.assertEqual(conf['permission'],{'*':'deny'})
                self.assertEqual(conf['tools'],{'*':False})
                self.assertEqual(conf['plugin'],[])
        self.assertEqual(self.host.agent_cfg,{'kind':'claude'})

    async def test_deleted_or_changed_profile_fails_closed(self):
        from agentj import provider_profiles as profiles
        (profiles.profile('a')/'key').unlink()
        with self.assertRaises(ValueError):self.session.execution_env('codex',self.cfg)
        profiles.atomic(profiles.profile('a')/'key',b'fixture-profile-material')
        self.cfg['model']='other'
        with self.assertRaises(ValueError):self.session.execution_env('codex',self.cfg)

class Onboarding(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_bind_no_longer_asks_for_an_avatar_plan(self):
        from types import SimpleNamespace as NS
        from unittest.mock import AsyncMock
        import test_p118_telegram as fixture
        base=fixture._Base();base.setUp()
        base.host.external_channels=NS(offer_choice=AsyncMock())
        base.t.auto_avatar=AsyncMock()
        try:
            await base.t.bind(base.cfg)
            await base.t.bind(base.cfg)
            base.host.external_channels.offer_choice.assert_not_awaited()   # P562: the main conversation answers, no plan to pick
        finally:
            if getattr(base.t,'avatar_task',None):await base.t.avatar_task
            base.doCleanups()

    async def test_mail_enable_no_longer_asks_for_an_avatar_plan(self):
        from types import SimpleNamespace as NS
        from unittest.mock import AsyncMock,MagicMock
        from agentj import mail
        import asyncio
        old=mail.blank_config();old.update(address='owner@example.test')
        new={**old,'enabled':True}
        tasks=[]
        avatar=NS(offer_choice=AsyncMock())
        manager=object.__new__(mail.Mail)
        import threading
        manager._config_lock=threading.RLock()
        manager.st=NS();manager.host=NS(external_channels=avatar,_bg=lambda c,*_:tasks.append(asyncio.create_task(c)))
        manager.wake=NS(set=MagicMock());manager._watch_allow=MagicMock()
        with patch.object(mail,'read_config',return_value=old),patch.object(mail,'save_config',side_effect=lambda st,c:c):
            self.assertTrue(manager.set_config(new)['enabled'])
        await asyncio.gather(*tasks)
        avatar.offer_choice.assert_not_awaited()     # P562

    async def test_three_channels_share_one_choice_and_recommend_codex(self):
        from types import SimpleNamespace as NS
        from unittest.mock import MagicMock
        from agentj.external_channels import ExternalChannels
        channel=object.__new__(ExternalChannels)
        channel.h=NS(agent_cfg={'kind':'claude'},agent_notice=MagicMock())
        channel.store=NS(data={},save=MagicMock())
        plans=[{'id':'claude','kind':'claude','config':{'kind':'claude'}},
               {'id':'profile:plan','kind':'codex','config':{'kind':'codex'}}]
        with patch('agentj.channel_plans.inventory',return_value=plans):
            for _ in range(3):await channel.offer_choice()
        channel.h.agent_notice.assert_called_once()
        self.assertIn('推荐 Codex',channel.h.agent_notice.call_args.args[0])

    async def test_signed_selection_changes_only_avatar_and_denial_preserves_it(self):
        from types import SimpleNamespace as NS
        from unittest.mock import AsyncMock,MagicMock
        from agentj.external_channels import ExternalChannels
        channel=object.__new__(ExternalChannels)
        main={'kind':'claude','model':'sonnet'}
        channel.h=NS(agent_cfg=main,agent=NS(cur_send=NS(source={'kind':'phone','role':'owner'})))
        channel.store=NS(data={'avatar':{},'sessions':{'old':'session'}},save=MagicMock())
        channel.sessions=NS(stop_all=AsyncMock())
        channel.approval=AsyncMock(return_value=False)
        plans=[{'id':'profile:route','kind':'codex','config':{'kind':'codex','model':'fixture','channel_profile':'route'}}]
        with patch('agentj.channel_plans.inventory',return_value=plans),patch('agentj.external_channels.openrouter_bound',return_value=False):
            self.assertFalse((await channel.select({'id':'profile:route'}))['ok'])
            self.assertEqual(channel.store.data['avatar'],{})
            channel.sessions.stop_all.assert_not_awaited()
            channel.approval.return_value=True
            self.assertTrue((await channel.select({'id':'profile:route'}))['ok'])
        self.assertEqual(channel.store.data['avatar']['config']['channel_profile'],'route')
        self.assertEqual(main,{'kind':'claude','model':'sonnet'})
        self.assertEqual(channel.store.data['sessions'],{})
        channel.sessions.stop_all.assert_awaited_once()
        channel.h.agent.cur_send.source={'kind':'telegram','role':'owner'}
        self.assertEqual((await channel.select({'id':'profile:route'}))['why'],'owner_phone_required')

class FreeExecution(unittest.IsolatedAsyncioTestCase):
    def test_empty_root_command_carries_no_owner_material_or_password_in_argv(self):
        from pathlib import Path
        from agentj.channel_free import command
        with patch('agentj.channel_free.shutil.which',return_value='/usr/bin/bwrap'),patch.object(Path,'is_file',return_value=True):
            argv,env=command('/fixture/opencode',Path('/fixture/scratch'),12345,'synthetic-process-password',
                             {'OPENAI_API_KEY':'fixture-not-forwarded','HOME':'/fixture/owner'})
        self.assertNotIn('synthetic-process-password',' '.join(argv))
        self.assertNotIn('/fixture/owner',' '.join(argv))
        self.assertNotIn('OPENAI_API_KEY',env)
        self.assertEqual(env['HOME'],'/avatar-home')
        self.assertEqual(env['OPENCODE_SERVER_PASSWORD'],'synthetic-process-password')
        self.assertIn('--unshare-pid',argv)
        self.assertNotIn('--ro-bind-try',argv)

    def test_permissions_and_native_results_fail_closed(self):
        from agentj.channel_free import rules,message_result
        from agentj.agent_opencode import evaluate
        for action in ('bash','edit','write','webfetch','websearch','skill','task','unknown','question'):
            self.assertEqual(evaluate(action,'anything',rules()),'deny')
        self.assertEqual(evaluate('read','/fixture/owner/private',rules()),'deny')
        self.assertEqual(evaluate('read','/avatar/scratch',rules()),'allow')
        self.assertEqual(message_result([{'info':{'role':'assistant','error':{'data':{'message':'Free usage limit reached'}}}}])[-1],'free_quota')
        self.assertEqual(message_result([{'info':{'role':'assistant'},'parts':[{'type':'tool','state':{'status':'completed'}}]}])[-1],'channel_forbidden')

    async def test_native_http_success_quota_tool_and_stop_never_use_paid_route(self):
        import asyncio,json,sys,tempfile
        from pathlib import Path
        from types import SimpleNamespace as NS
        from agentj import peer_session
        from agentj.channel_free import NativeFree
        from agentj.wechat import Sessions
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script=root/'server.py'
            script.write_text('''import os,json,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  if self.path=='/event':
   self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
   self.wfile.write(b'data: {"type":"server.connected"}\\n\\n')
   if os.environ.get('FIXTURE_SSE_STATUS'):
    event={'type':'session.status','properties':{'sessionID':'ses_fixture','status':{'type':'retry','message':'Free usage limit reached'}}}
    self.wfile.write(('data: '+json.dumps(event)+'\\n\\n').encode())
   self.wfile.flush();return
  if self.path=='/global/health':value={'healthy':True}
  elif self.path=='/permission':value=[]
  else:value=json.loads(os.environ['FIXTURE_MESSAGES'])
  raw=json.dumps(value).encode();self.send_response(200);self.end_headers();self.wfile.write(raw)
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  if self.path.endswith('/prompt_async'):
   assert data['model']['providerID']=='opencode'
   assert data['model']['modelID']=='fixture-free'
   value=None
  else:value={'id':'ses_fixture'}
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps(value).encode())
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''')
            cfg={'kind':'opencode','model':'opencode/fixture-free','channel_free':True}
            host=NS(st=NS(),agent_cfg=cfg,preferences={})
            sessions=Sessions(host,NS(data={}));sessions._exe=lambda _: 'fixture'
            free=NativeFree(sessions)
            final={'info':{'role':'assistant','time':{'completed':1}},'parts':[{'type':'text','text':'{"decision":"reply","text":"hello","topic":""}'}]}
            cases=[([final],None),([{'info':{'role':'assistant','error':{'data':{'message':'Free usage limit reached'}}}}],'free_quota'),
                   ([{'info':{'role':'assistant'},'parts':[{'type':'tool'}]}],'channel_forbidden'),([], 'free_quota')]
            for messages,error in cases:
                def cmd(exe,scratch,port,password,proxy):
                    return [sys.executable,str(script),str(port)],{'FIXTURE_MESSAGES':json.dumps(messages),'FIXTURE_SSE_STATUS':'yes' if not messages else ''}
                with patch('agentj.channel_free.command',side_effect=cmd),patch.object(peer_session,'sandbox_root',return_value=root), \
                     patch('agentj.channel_free.catalogue',return_value=([cfg['model']],b'{}')):
                    result=await free._native_turn({'id':'owner'},{},'hello')
                self.assertEqual(result.error,error)
                if error is None:self.assertEqual(result.text,'hello')
                self.assertEqual(len(sessions._procs),0)
                self.assertFalse(list(root.glob('free-*')))
            def pending(exe,scratch,port,password,proxy):
                return [sys.executable,str(script),str(port)],{'FIXTURE_MESSAGES':'[]'}
            with patch('agentj.channel_free.command',side_effect=pending),patch.object(peer_session,'sandbox_root',return_value=root), \
                 patch('agentj.channel_free.catalogue',return_value=([cfg['model']],b'{}')):
                task=asyncio.create_task(free._native_turn({'id':'owner'},{},'hello'))
                for _ in range(100):
                    if sessions._procs:break
                    await asyncio.sleep(.01)
                await sessions.stop_all()
                self.assertEqual((await task).error,'stopped')
            self.assertFalse(sessions._procs)

class FreshCatalogue(unittest.TestCase):
    def test_refresh_is_zero_credential_and_same_metadata_is_carried_to_server(self):
        import json
        from pathlib import Path
        from types import SimpleNamespace as NS
        from agentj import channel_free
        channel_free._CATALOGUE.clear()
        self.addCleanup(channel_free._CATALOGUE.clear)
        raw={'opencode':{'models':{'fixture-free':{'cost':{'input':0,'output':0}},
                                   'paid':{'cost':{'input':1,'output':1}}}}}
        def run(argv,**kw):
            self.assertIn('--refresh',argv)
            self.assertNotIn('OPENAI_API_KEY',kw['env'])
            self.assertNotIn('OPENCODE_API_KEY',kw['env'])
            destination=Path(kw['env']['XDG_CACHE_HOME'])/'opencode/models.json'
            destination.parent.mkdir(parents=True);destination.write_text(json.dumps(raw))
            return NS(returncode=0,stdout='opencode/fixture-free\n{"cost":{"input":0,"output":0}}\nopencode/paid\n{"cost":{"input":1,"output":1}}')
        with patch.dict('os.environ',{'OPENAI_API_KEY':'fixture-not-forwarded'}),patch.object(channel_free.subprocess,'run',side_effect=run) as called:
            models,data=channel_free.catalogue('fixture')
            self.assertEqual(models,['opencode/fixture-free'])
            self.assertEqual(json.loads(data),raw)
            self.assertEqual(channel_free.catalogue('fixture'),(models,data))
            called.assert_called_once()

    def test_failed_refresh_never_qualifies_old_bundled_free_ids(self):
        import subprocess
        from agentj import channel_free
        channel_free._CATALOGUE.clear()
        with patch.object(channel_free.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixture',25)):
            self.assertEqual(channel_free.catalogue('fixture'),([],None))

class DomesticExecutablePlans(unittest.TestCase):
    def test_independent_clis_disabled_saved_provider_profiles_execute(self):
        from agentj.channel_plans import discover
        detected = {'harnesses':[{'name':k,'installed':True,'supported':True,'logged_in':True}
                                  for k in ('claude','opencode','kimi','qwen','dsh')]}
        saved = [{'id':k,'harness':h,'model':'fixture'} for k,h in
                 (('kimi','claude'),('qwen','opencode'),('deepseek','opencode'))]
        with patch('agentj.channel_plans.profile_available', return_value=True):
            rows = discover(detected, saved)
        for kind in ('kimi','qwen','dsh'):
            row = next(r for r in rows if r['id'] == kind)
            self.assertFalse(row['selectable'])
            self.assertIn('暂不支持', row['label']['zh'])
            with self.assertRaises(ValueError):avatar.choose(kind, {}, rows)
        for row in saved:
            cfg = avatar.choose('profile:' + row['id'], {}, rows)
            self.assertEqual(cfg['kind'], row['harness'])
            self.assertEqual(cfg['channel_profile'], row['id'])
        with self.assertRaisesRegex(ValueError, 'harness_unavailable'):
            avatar.choose('shared', {'kind':'qwen'}, rows)

class DomesticTurns(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ProfileExecution.asyncSetUp

    async def test_profiles_reach_supported_executor_with_own_provider(self):
        import json
        from unittest.mock import AsyncMock, MagicMock
        self.session.store.session_id=MagicMock(return_value=None)
        self.session.store.set_session_id=MagicMock()
        self.session.st.log=MagicMock()
        from agentj import peer_session, provider_profiles as profiles
        for pid, kind in (('kimi', 'claude'), ('qwen', 'opencode'), ('deepseek-plan', 'opencode')):
            profiles.define(pid, kind, 'https://provider.example.test/v1', 'fixture')
            profiles.atomic(profiles.profile(pid)/'key', b'fixture-domestic-material')
            self.cfg.update(kind=kind, channel_profile=pid, model=pid+'/fixture' if kind=='opencode' else 'fixture')
            event = ({'type':'result', 'result':'{"decision":"reply","text":"OK","topic":""}'} if kind=='claude'
                     else {'type':'text', 'part':{'type':'text','text':'{"decision":"reply","text":"OK","topic":""}'}})
            async def run(argv, cwd, env, text, gen):
                self.assertNotIn('fixture-domestic-material', ' '.join(argv))
                if kind=='claude':
                    self.assertEqual(env['ANTHROPIC_AUTH_TOKEN'], 'fixture-domestic-material')
                    self.assertEqual(env['ANTHROPIC_BASE_URL'], 'https://provider.example.test')
                else:
                    conf=json.loads(env['OPENCODE_CONFIG_CONTENT'])
                    self.assertEqual(conf['provider'][pid]['options']['apiKey'], 'fixture-domestic-material')
                    self.assertEqual(conf['tools'], {'*':False})
                return json.dumps(event), '', 0
            self.session._run=AsyncMock(side_effect=run)
            with patch.object(peer_session, 'sandbox_root', return_value=self.root/'scratch'), \
                 patch.object(peer_session, 'sandbox_dir', return_value=self.root/'scratch'/'owner'), \
                 patch.object(peer_session.fence, 'protected_paths', return_value=[]), \
                 patch.object(peer_session.fence, 'problem', return_value=None), \
                 patch.object(peer_session, 'fenced', side_effect=lambda st,argv,*args:argv), \
                 patch.object(peer_session, 'claude_flags', return_value=set(peer_session.CLAUDE_REQUIRED)), \
                 patch.object(peer_session, 'opencode_flags', return_value={'--agent','--format','--dir'}), \
                 patch.object(self.session, '_exe', return_value='/fixture/'+kind):
                result=await self.session.turn({'id':'owner'}, {'auto':{'mode':'on'}}, 'Reply OK')
            self.assertIsNone(result.error)
            self.assertEqual(result.text, 'OK')
            self.session._run.assert_awaited_once()
            self.assertEqual(self.host.agent_cfg, {'kind':'claude'})
