"""P51: a Codex shared continuation that cannot be established degrades (new thread / wait / owner defaults);
only Codex's own refusal reaches the phone, with the concrete reason."""
import _hermetic
import asyncio,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentj import shared_codex
from agentj.agent_codex import RPCError
from agentj.shared_codex import SharedCodexAgent,REASONS,brief,Refusal
SID='11111111-1111-1111-1111-111111111111'
NEW='22222222-2222-2222-2222-222222222222'
PROFILE={'type':'managed','file_system':{'type':'restricted','entries':[{'path':{'type':'special','value':{'kind':'root'}},'access':'read'}]},'network':'restricted'}
WW_PROFILE={'type':'managed','file_system':{'type':'restricted','entries':[]},'network':'restricted'}


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/var/tmp');self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'codex';self.proj=Path(self.tmp.name)/'proj';self.proj.mkdir()
        env=patch.dict(os.environ,{'CODEX_HOME':str(self.home)});env.start();self.addCleanup(env.stop)
        self.host=Mock();self.host.st.agent_session.return_value=None
        self.calls=[]

    def rollout(self,sid,cwd=None,ctx=None,active=False,name=None):
        d=self.home/'sessions'/'2026'/'10'/'05';d.mkdir(parents=True,exist_ok=True)
        path=d/(name or f'rollout-2026-10-05T00-00-00-{sid}.jsonl')
        items=[{'type':'session_meta','payload':{'id':sid,'cwd':str(cwd or self.proj)}}]
        if ctx is not False:
            items.append({'type':'turn_context','payload':ctx or {'cwd':str(cwd or self.proj),'approval_policy':'on-request',
                'approvals_reviewer':'user','sandbox_policy':{'type':'read-only'},'permission_profile':PROFILE,
                'active_permission_profile':{'id':':read-only'}}})
        if active:items.append({'type':'event_msg','payload':{'type':'task_started'}})
        path.write_text(''.join(json.dumps(i)+'\n' for i in items))
        return path

    def agent(self,sid='',**cfg):
        a=SharedCodexAgent(self.host,{'kind':'codex','dir':str(self.proj),'_workflow_ceo':True,'shared_session_id':sid,**cfg})
        async def call(method,params,timeout=60):
            self.calls.append((method,params))
            if method=='thread/start':return {'thread':{'id':NEW},'approvalPolicy':'on-request','approvalsReviewer':'user'}
            if method=='thread/resume':
                if self.resume_error:raise RPCError({'message':self.resume_error})
                return {'thread':{'id':params['threadId']},'approvalPolicy':params.get('approvalPolicy','on-request'),
                        'approvalsReviewer':params.get('approvalsReviewer','user'),'activePermissionProfile':{'id':params.get('permissions')}}
            if method=='hooks/list':return {'data':[]}
            raise AssertionError(method)
        self.resume_error=None
        a.call=AsyncMock(side_effect=call)
        return a

    def notices(self):
        return ' '.join(c.args[0] for c in self.host.agent_notice.call_args_list)

    def events(self,name):
        return [c.kwargs for c in self.host.emit.call_args_list if c.args and c.args[0]==name]


class NoResumableThread(Base):
    async def test_fresh_install_without_any_thread_opens_new_one(self):
        a=self.agent()
        self.assertTrue(await a._thread())
        self.assertEqual([m for m,_ in self.calls],['thread/start'])
        self.assertEqual(self.calls[0][1],{'cwd':str(self.proj)})          # owner's own Codex defaults, no overrides
        self.assertEqual(a.tid,NEW);self.assertEqual(a.cfg['shared_session_id'],NEW);self.assertEqual(a.original,{})
        self.host.st.set_agent_session.assert_called_with('codex',NEW)
        self.assertIn('已为你新开一个 Codex 会话',self.notices());self.assertIn(f'codex resume {NEW}',self.notices())
        self.assertEqual(self.events('shared_new_thread')[0]['reason'],'exact Codex thread ID required')

    async def test_unselected_discovers_newest_thread_of_this_project(self):
        other='33333333-3333-3333-3333-333333333333'
        self.rollout(other,cwd='/elsewhere')
        self.rollout(SID)
        a=self.agent()
        await a._thread()
        self.assertEqual(self.calls[-1][0],'thread/resume');self.assertEqual(self.calls[-1][1]['threadId'],SID)
        self.assertEqual(a.original,{'approvalPolicy':'on-request','approvalsReviewer':'user','permissions':':read-only'})

    async def test_missing_or_foreign_selected_thread_falls_back_to_new(self):
        for setup in (lambda:None, lambda:self.rollout(SID,cwd='/elsewhere')):
            self.calls.clear();self.host.reset_mock();self.host.st.agent_session.return_value=None
            setup()
            a=self.agent(SID)
            await a._thread()
            self.assertEqual(self.calls[-1][0],'thread/start');self.assertEqual(a.tid,NEW)
        self.assertEqual(self.events('shared_new_thread')[0]['reason'],'wrong thread/project')

    async def test_native_resume_refusal_falls_back_to_new(self):
        self.rollout(SID)
        a=self.agent(SID);self.resume_error='thread not found /home/x/.codex/y'
        await a._thread()
        self.assertEqual([m for m,_ in self.calls],['thread/resume','thread/start'])
        self.assertNotIn('/home',self.events('shared_new_thread')[0]['reason'])


class OwnerDefaults(Base):
    async def test_unverified_profile_resumes_with_owner_defaults(self):
        self.rollout(SID,ctx={'cwd':str(self.proj),'approval_policy':'on-request','sandbox_policy':{'type':'workspace-write'},
                               'permission_profile':WW_PROFILE,'active_permission_profile':{'id':None}})
        a=self.agent(SID)
        await a._thread()
        self.assertEqual(self.calls[-1],('thread/resume',{'threadId':SID,'excludeTurns':True}))
        self.assertEqual(a.original,{})
        self.assertEqual(self.events('shared_degraded')[0]['reason'],'unverified original permission profile')
        self.assertIn('默认权限',self.notices())
        a.tid=None;await a._thread()                                         # told once, not every turn
        self.assertEqual(self.notices().count('默认权限'),1)

    async def test_thread_without_completed_turn_resumes_with_defaults(self):
        self.rollout(SID,ctx=False)
        a=self.agent(SID);await a._thread()
        self.assertEqual(self.calls[-1],('thread/resume',{'threadId':SID,'excludeTurns':True}))
        self.assertEqual(self.events('shared_degraded')[0]['reason'],'no original turn permissions')

    async def test_external_sandbox_has_no_override_and_uses_defaults(self):
        self.rollout(SID,ctx={'cwd':str(self.proj),'approval_policy':'never','sandbox_policy':{'type':'external-sandbox'}})
        a=self.agent(SID);await a._thread()
        self.assertEqual(self.calls[-1],('thread/resume',{'threadId':SID,'excludeTurns':True}))

    async def test_changed_policy_on_resume_delivers_under_native_answer(self):
        self.rollout(SID)
        a=self.agent(SID)
        orig=a.call.side_effect
        async def call(method,params,timeout=60):
            r=await orig(method,params)
            if method=='thread/resume':r['approvalPolicy']='never'
            return r
        a.call.side_effect=call
        self.assertTrue(await a._thread());self.assertEqual(a.original,{})

    async def test_missing_hook_degrades_with_notice(self):
        self.rollout(SID)
        a=self.agent(SID);a.risk_channel=Mock(path=Path('/var/tmp/p51.sock'))
        await a._thread()
        self.assertEqual([m for m,_ in self.calls],['hooks/list','thread/resume'])
        self.assertNotIn('config',self.calls[-1][1])
        self.assertIn('高危提醒',self.notices())


class DesktopTurn(Base):
    async def test_waits_for_desktop_turn_then_delivers(self):
        path=self.rollout(SID,active=True)
        a=self.agent(SID);a.turn_done=asyncio.Event()
        async def finish():
            await asyncio.sleep(1.2)
            with path.open('a') as f:f.write(json.dumps({'type':'event_msg','payload':{'type':'task_complete'}})+'\n')
        t=asyncio.create_task(finish())
        await a._thread();await t
        self.assertEqual(self.calls[-1][0],'thread/resume')
        self.assertIn('等它这一轮结束后自动发出',self.notices())
        self.assertEqual(self.events('shared_waiting')[0]['reason'],'desktop turn active; alternate turns')
        self.assertFalse(self.events('shared_refused'))

    async def test_stop_ends_the_wait_without_failure(self):
        self.rollout(SID,active=True)
        a=self.agent(SID);a._spawn=AsyncMock(return_value=True);a.proc=None
        async def spawn():
            a.proc=Mock(returncode=None);return True
        a._spawn=AsyncMock(side_effect=spawn);a._kill=AsyncMock()
        task=asyncio.create_task(a.turn('hi'))
        await asyncio.sleep(.3);a.turn_done.set()
        await asyncio.wait_for(task,5)
        self.assertFalse(self.events('shared_refused'));self.host.local_notice.assert_not_called()

    async def test_desktop_turn_restarting_before_resume_waits_again(self):
        self.rollout(SID)
        a=self.agent(SID);a.turn_done=asyncio.Event()
        real=shared_codex.owner_context;seen=[]
        def flaky(*args):
            seen.append(1)
            if len(seen)==1:raise Refusal('desktop turn active; alternate turns')
            return real(*args)
        with patch.object(shared_codex,'owner_context',side_effect=flaky):await a._thread()
        self.assertEqual(len(seen),2);self.assertEqual([m for m,_ in self.calls],['thread/resume'])
        self.assertEqual(a.original['permissions'],':read-only')

    async def test_wait_has_a_named_limit(self):
        self.rollout(SID,active=True)
        a=self.agent(SID);a.turn_done=asyncio.Event()
        with patch.object(shared_codex,'DESKTOP_WAIT',0):
            with self.assertRaises(Refusal) as r:await a._thread()
        self.assertEqual(r.exception.code,'desktop turn did not finish')


class Messages(Base):
    async def run_turn(self,error):
        a=self.agent(SID);a._ready=AsyncMock(side_effect=error);a._kill=AsyncMock()
        self.host.turn_failed=Mock()
        await a.turn('hi')
        return self.events('shared_refused')[0]['reason'],' '.join(c.args[0] for c in self.host.local_notice.call_args_list)

    async def test_every_named_reason_has_bilingual_plain_text(self):
        for code,(zh,en) in REASONS.items():
            self.assertTrue(zh and en and zh!=en,code)
        reason,text=await self.run_turn(Refusal('desktop turn did not finish'))
        self.assertEqual(reason,'desktop turn did not finish')
        self.assertIn('30 分钟',text);self.assertIn('agentj activity',text);self.assertNotIn('核对原会话权限',text)

    async def test_unknown_error_carries_type_and_short_text_without_paths(self):
        reason,text=await self.run_turn(OSError('boom at /home/someone/.codex/secret.json'))
        self.assertTrue(reason.startswith('OSError: boom'),reason);self.assertNotIn('/home',reason);self.assertNotIn('secret',reason)
        self.assertIn('agentj doctor',text)

    async def test_codex_refusal_is_reported_as_codex_refusal(self):
        reason,text=await self.run_turn(RPCError({'message':'model not available'}))
        self.assertEqual(reason,'RPCError: model not available');self.assertIn('Codex 拒绝了这条消息',text)

    async def test_reason_lands_in_activity_and_host_log(self):
        from types import SimpleNamespace
        from agentj import activity
        from agentj.cli import activity_line
        root=Path(self.tmp.name)/'state';root.mkdir()
        logged=[]
        self.host.st=SimpleNamespace(root=root,config=lambda:{},agent_session=lambda k:None,set_agent_session=lambda *a:None,
                                     log=lambda ev,**kw:logged.append((ev,kw)))
        await self.run_turn(Refusal('desktop turn did not finish'))
        rows=[r for r in activity.all_since(self.host.st,None) if r.get('k')=='shared']
        self.assertEqual(rows[0]['reason'],'desktop turn did not finish');self.assertEqual(rows[0]['ev'],'shared_refused')
        self.assertIn('desktop turn did not finish',activity_line(rows[0]))
        self.assertIn(('shared_refused',{'agent':'codex','reason':'desktop turn did not finish'}),logged)

    def test_brief_keeps_named_codes(self):
        self.assertEqual(brief(ValueError('wrong thread/project')),'wrong thread/project')
        self.assertEqual(brief(ValueError('x')),'ValueError: x')


class Doctor(Base):
    def row(self,sid=''):
        from agentj import doctor
        st=Mock();st.exists.return_value=True;st.agent_session.return_value=None
        st.agent_config.return_value={'kind':'codex','dir':str(self.proj),'session_mode':'shared','shared_session_id':sid}
        return doctor.check_codex_shared(st)

    def test_states(self):
        r=self.row();self.assertEqual(r['status'],'warn');self.assertIn('新开一个',r['summary'])
        self.rollout(SID,active=True)
        r=self.row();self.assertEqual(r['status'],'ok');self.assertIn('最近',r['summary'])
        r=self.row(SID);self.assertEqual(r['status'],'ok');self.assertIn('排队',r['summary'])
        r=self.row(NEW);self.assertEqual(r['status'],'warn');self.assertIn('找不到',r['summary'])

    def test_other_modes_have_no_row(self):
        from agentj import doctor
        st=Mock();st.exists.return_value=True
        st.agent_config.return_value={'kind':'codex','dir':str(self.proj),'session_mode':'independent'}
        self.assertIsNone(doctor.check_codex_shared(st))


if __name__=='__main__':
    unittest.main()
