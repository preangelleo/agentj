"""Product shared hooks: native authority, committed Stop and local channel."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock
from agentj import danger, shared, shared_hook
from agentj.shared_state import AgentState

class Clock:
    def __init__(self): self.jobs=[]
    def now(self): return 0
    def call_later(self, delay, fn):
        timer=Mock(); self.jobs.append((fn,timer));return timer

class CommittedStop(unittest.TestCase):
    def test_ported_foreground_stop_is_held_but_background_stop_commits(self):
        turns=[];clock=Clock();s=AgentState(scheduler=clock,on_turn=lambda *x:turns.append(x))
        s.handle({'hook_event_name':'SubagentStart','agent_id':'child'})
        s.handle({'hook_event_name':'Stop','last_assistant_message':'premature','background_tasks':[]})
        self.assertEqual(turns,[])
        s.handle({'hook_event_name':'SubagentStop','agent_id':'child'})
        s.handle({'hook_event_name':'Stop','last_assistant_message':'complete','background_tasks':[]})
        self.assertEqual(turns[-1][1],'complete')
        s.handle({'hook_event_name':'SubagentStart','agent_id':'background'})
        s.handle({'hook_event_name':'Stop','last_assistant_message':'main complete','background_tasks':[
            {'type':'subagent','id':'background'}]})
        self.assertEqual(turns[-1][1],'main complete')
    def test_subagent_stop_is_not_root_completion(self):
        turns=[];s=AgentState(scheduler=Clock(),on_turn=lambda *x:turns.append(x))
        s.handle({'hook_event_name':'Stop','agent_id':'child','last_assistant_message':'child'})
        self.assertEqual(turns,[])

class SharedRisk(unittest.TestCase):
    def test_credential_approval_never_sends_values(self):
        from agentj.serve import approval_summary
        inp={'file_path':'/tmp/demo.pem','content':'SYNTHETIC_PRIVATE_VALUE'}
        v=danger.classify_shared('Write',inp)
        summary=approval_summary('Write',inp,v)
        self.assertNotIn(inp['content'],summary)
        self.assertIn('credentials',summary.lower())

    def test_routine_zero_and_four_true_risks(self):
        for command in ['git commit -m test','npm install','python verify.py','rm local.txt','git branch -D scratch']:
            self.assertFalse(danger.classify_shared('Bash',{'command':command}).danger,command)
        for command,cat in [('stripe payments create','spend'),('gh api -X DELETE repos/owner/repo','delete'),
                            ('git push origin main','send'),('cat ~/.ssh/id_ed25519','credentials')]:
            self.assertIn(cat,danger.classify_shared('Bash',{'command':command}).cats,command)
        self.assertIn('credentials',danger.classify_shared('Read',{'file_path':'/tmp/credentials/demo.txt'}).cats)
        self.assertTrue(danger.classify('Bash',{'command':'rm local.txt'}).danger,'independent policy preserved')

class ClaudeHooks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cc-hooks-',dir='/var/tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.host=Mock();self.host.st.root=self.root
        self.host.stopped.return_value=False
        self.host.st.sign_key.return_value='TEST_SIGNING_IDENTITY'
        self.host.ask=AsyncMock(return_value={'behavior':'allow','risk_grant':{'rid':'test-request','device':'test-device','sign_pub':'TEST_SIGNING_IDENTITY'}})
        self.a=shared.SharedClaudeAgent(self.host,{'kind':'claude','dir':str(self.root),'_workflow_ceo':True,'high_risk_warnings':True})
        self.a.session={'sessionId':'owner'}
    async def event(self,name,tool='Bash',inp=None,sid='owner'):
        return await self.a.hook_event({'session_id':sid,'hook_event_name':name,'tool_name':tool,'tool_input':inp or {'command':'git push origin main'}})
    async def test_pretool_can_only_ask_never_allow_and_same_category_one_card(self):
        await self.event('UserPromptSubmit')
        ask=await self.event('PreToolUse')
        self.assertEqual(ask['hookSpecificOutput']['permissionDecision'],'ask')
        allow=await self.event('PermissionRequest')
        self.assertEqual(allow['hookSpecificOutput']['decision']['behavior'],'allow')
        self.assertEqual(await self.event('PreToolUse',inp={'command':'git push other branch'}),{})
        self.host.ask.assert_awaited_once()
        await self.event('UserPromptSubmit')
        self.assertEqual((await self.event('PreToolUse'))['hookSpecificOutput']['permissionDecision'],'ask')
    async def test_native_ask_not_auto_allowed_by_risk_grant(self):
        await self.event('PreToolUse');await self.event('PermissionRequest')
        await self.event('PermissionRequest',inp={'command':'git push second branch'})
        self.assertEqual(self.host.ask.await_count,2)
    async def test_foreign_session_cannot_approve_or_commit(self):
        self.assertEqual(await self.event('PermissionRequest',sid='foreign'),{})
        self.host.ask.assert_not_awaited()
    async def test_revocation_invalidates_same_category_grant(self):
        await self.event('PreToolUse');await self.event('PermissionRequest')
        self.host.st.sign_key.return_value=None
        self.assertEqual((await self.event('PreToolUse'))['hookSpecificOutput']['permissionDecision'],'ask')
    async def test_deny_does_not_grant_risk_category(self):
        self.host.ask.return_value={'behavior':'deny'}
        await self.event('PreToolUse');ans=await self.event('PermissionRequest')
        self.assertEqual(ans['hookSpecificOutput']['decision']['behavior'],'deny')
        self.assertEqual(self.a.permissions,set())
    async def test_late_signed_answer_does_not_grant_the_next_turn(self):
        entered=asyncio.Event();release=asyncio.Event();answer=self.host.ask.return_value
        async def delayed(*args,**kwargs):
            entered.set();await release.wait();return answer
        self.host.ask.side_effect=delayed
        await self.event('PreToolUse')
        old=asyncio.create_task(self.event('PermissionRequest'))
        await entered.wait();await self.event('UserPromptSubmit');release.set()
        self.assertEqual((await old)['hookSpecificOutput']['decision']['behavior'],'allow')
        self.assertEqual(self.a.permissions,set())
        self.assertEqual((await self.event('PreToolUse'))['hookSpecificOutput']['permissionDecision'],'ask')

    async def test_same_category_denial_does_not_generate_another_card(self):
        self.host.ask.return_value={'behavior':'deny'}
        for path in ('first.pem','second.pem'):
            self.assertEqual((await self.event('PreToolUse',tool='Read',inp={'file_path':path}))['hookSpecificOutput']['permissionDecision'],'ask')
            self.assertEqual((await self.event('PermissionRequest',tool='Read',inp={'file_path':path}))['hookSpecificOutput']['decision']['behavior'],'deny')
        self.host.ask.assert_awaited_once()
        await self.event('UserPromptSubmit');await self.event('PreToolUse');await self.event('PermissionRequest')
        self.assertEqual(self.host.ask.await_count,2)

    async def test_native_routine_permission_preserved(self):
        self.assertEqual(await self.event('PreToolUse',inp={'command':'git commit -m test'}),{})
        await self.event('PermissionRequest',inp={'command':'git commit -m test'})
        self.host.ask.assert_awaited_once()
    async def test_channel_and_merge_preserve_owner_hooks_and_permissions(self):
        project=self.root/'work';(project/'.claude').mkdir(parents=True)
        target=project/'.claude/settings.local.json'
        owner={'permissions':{'deny':['Bash(curl:*)']},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'owner-hook'}]}]}}
        target.write_text(json.dumps(owner))
        chan=shared_hook.Channel(self.a,self.root/'cc.sock');await chan.start()
        try:
            shared_hook.install(project,chan.path);shared_hook.install(project,chan.path)
            result=json.loads(target.read_text())
            self.assertEqual(result['permissions'],owner['permissions'])
            self.assertEqual(result['hooks']['Stop'][0],owner['hooks']['Stop'][0])
            self.assertEqual(len(result['hooks']['Stop']),2)
            reader,writer=await asyncio.open_unix_connection(str(chan.path))
            writer.write(json.dumps({'hook_event_name':'PermissionRequest','session_id':'owner','tool_name':'Read','tool_input':{'file_path':'demo'}}).encode()+b'\n');await writer.drain()
            reply=json.loads(await reader.readline())
            self.assertEqual(reply['hookSpecificOutput']['decision']['behavior'],'allow')
            writer.close();await writer.wait_closed()
            self.assertEqual(chan.path.stat().st_mode & 0o777,0o600)
        finally:await chan.stop()
    async def test_waiting_interrupt_cannot_answer_permission_dialog(self):
        self.a.state.handle({'hook_event_name':'PermissionRequest','tool_name':'Bash'})
        self.a.status='working'
        self.assertFalse(await self.a.halt())


class NativeRiskHooks(unittest.IsolatedAsyncioTestCase):
    async def test_late_answer_does_not_cross_turn_and_revocation_blocks_execution(self):
        from agentj.shared_risk import RiskGuard
        a=Mock();a.cfg={'high_risk_warnings':True};a.host.stopped.return_value=False;a.host.st.sign_key.return_value='SIGN'
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(*args,**kwargs):
            entered.set();await release.wait()
            return {'behavior':'allow','risk_grant':{'rid':'old','device':'device','sign_pub':'SIGN'}}
        a.host.ask=AsyncMock(side_effect=delayed);guard=RiskGuard(a)
        old=asyncio.create_task(guard.check('Read',{'file_path':'demo.pem'}))
        await entered.wait();guard.reset('next');release.set()
        self.assertTrue(await old);self.assertEqual(guard.grants,{})
        a.host.st.sign_key.return_value=None
        self.assertFalse(await guard.check('Read',{'file_path':'second.pem'}))

    async def test_category_denial_is_one_card_until_next_turn(self):
        from agentj.shared_risk import RiskGuard
        a=Mock();a.cfg={'high_risk_warnings':True};a.host.ask=AsyncMock(return_value={'behavior':'deny'})
        guard=RiskGuard(a)
        self.assertFalse(await guard.check('Read',{'file_path':'first.pem'}))
        self.assertFalse(await guard.check('Read',{'file_path':'second.pem'}));a.host.ask.assert_awaited_once()
        guard.reset();self.assertFalse(await guard.check('Read',{'file_path':'third.pem'}));self.assertEqual(a.host.ask.await_count,2)

    async def test_codex_and_opencode_same_category_reset_and_native_routine(self):
        from agentj.shared_risk import RiskGuard
        for family in ('codex','opencode'):
            with self.subTest(family=family):
                a=Mock();a.kind=family;a.cfg={'dir':'/var/tmp','shared_session_id':'owner','high_risk_warnings':True}
                a.host.stopped.return_value=False;a.host.st.sign_key.return_value='SIGN'
                a.host.ask=AsyncMock(return_value={'behavior':'allow','risk_grant':{'rid':'request','device':'device','sign_pub':'SIGN'}})
                guard=RiskGuard(a)
                def ev(command):return {'hook_event_name':'PreToolUse','session_id':'owner','tool_name':'exec_command' if family=='codex' else 'Bash','tool_input':{'cmd':command} if family=='codex' else {'command':command}}
                self.assertEqual(await guard.hook_event(ev('git commit -m local')),{});a.host.ask.assert_not_awaited()
                self.assertEqual(await guard.hook_event(ev('cat demo.pem')),{})
                self.assertEqual(await guard.hook_event(ev('cat second.pem')),{});a.host.ask.assert_awaited_once()
                guard.reset('next');await guard.hook_event(ev('cat third.pem'));self.assertEqual(a.host.ask.await_count,2)
                a.host.ask.return_value={'behavior':'deny'};guard.reset('deny')
                result=await guard.hook_event(ev('cat denied.pem'))
                self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')

class DisconnectedHooks(unittest.TestCase):
    def test_malformed_or_oversized_execution_payload_fails_closed(self):
        import subprocess,sys
        for payload in ('{broken', 'null', 'x' * (shared_hook.MAX_FRAME+1), '{"hook_event_name":"Stop"}', '{"hook_event_name":"PreToolUse","tool_name":"Read","tool_input":"malformed"}'):
            r=subprocess.run([sys.executable,shared_hook.__file__,'/var/tmp/absent-agentj-channel','PreToolUse'],input=payload,text=True,capture_output=True,check=True)
            self.assertEqual(json.loads(r.stdout)['hookSpecificOutput']['permissionDecision'],'deny')
        r=subprocess.run([sys.executable,shared_hook.__file__,'/var/tmp/absent-agentj-channel','Stop'],input='{broken',text=True,capture_output=True,check=True)
        self.assertEqual(r.stdout,'','bad telemetry cannot grant execution or block desktop exit')

    def test_routine_keeps_native_authority_and_high_risk_is_denied(self):
        import subprocess, sys
        for name,tool,inp,risk in [
            ('PreToolUse','Read',{'file_path':'/tmp/package.json'},False),
            ('PermissionRequest','Bash',{'command':'git commit -m local'},False),
            ('PreToolUse','exec_command',{'cmd':'cat ~/.ssh/id_ed25519'},True),
            ('PreToolUse','apply_patch',{'command':'*** Update File: /tmp/demo.pem'},True),
            ('PermissionRequest','Read',{'file_path':'/tmp/demo.pem'},True)]:
            with self.subTest(name=name,tool=tool):
                r=subprocess.run([sys.executable, shared_hook.__file__, '/var/tmp/absent-agentj-channel'],
                    input=json.dumps({'hook_event_name':name,'tool_name':tool,'tool_input':inp}),
                    text=True,capture_output=True,check=True)
                if risk:
                    d=json.loads(r.stdout)['hookSpecificOutput']
                    self.assertEqual(d.get('permissionDecision',d.get('decision',{}).get('behavior')),'deny')
                else:
                    self.assertEqual(r.stdout,'','absence of a decision preserves native authority')

if __name__=='__main__':unittest.main()
