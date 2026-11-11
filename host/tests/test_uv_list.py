"""UV-list: real Host cards and signature/UV verification; isolated synthetic State."""
import _hermetic
import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import approval_review, approvals, policy_registry, provenance, uv_list
from agentj.serve import Host
from _p541_device import Phone, _owner_host
from test_l1 import _state, _ready
from _listed_cards import listed

class ListApproval(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.st=_state(tmp.name);self.sent=[];self.h=_owner_host(self.st,self.sent)
        self.h.ask_ttl=2;self.ph=Phone(self.st,with_passkey=True);self.s=_ready(self.h,self.ph)
        self.h.agent.submit=mock.Mock()
        page=self.h.hist_add({'k':'phone','name':'你','text':'Synthetic owner command.'},'','done')
        self.origin=approval_review.remember(self.h,page,provenance.phone())
        self.h.agent.cur_send=SimpleNamespace(approval_origin=self.origin,source=provenance.phone())
    async def open(self,tool='agentj fn run',inp=None):
        task=asyncio.create_task(self.h.ask(tool,inp or {'name':'synthetic'},grant_info=True))
        for _ in range(100):
            if self.h.asks or task.done():break
            await asyncio.sleep(.002)
        if task.done():self.fail(str(task.result()))
        a=next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.close,task,a)
        return task,a
    async def close(self,task,a):
        if not a.fut.done():a.fut.set_result(('deny',None,None,None))
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)
    async def test_list_signed_uv_positive(self):
        task,a=await self.open()
        self.assertTrue(a.require_passkey)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
        result=await task
        self.assertEqual(result['behavior'],'allow');self.assertTrue(result['grant_info']['uv'])
        records=[r for r in approvals.read_log(self.st) if r.get('reason')=='device']
        self.assertEqual(approvals.check_record(self.st,records[-1]),'ok')
    async def test_signed_without_uv_cannot_allow(self):
        task,a=await self.open();obj=self.ph.answer(self.h._ask_msg(a,self.s),True);obj.pop('fa')
        await self.h._answer(self.s,obj)
        self.assertFalse(a.fut.done());self.assertFalse(a.uv_ok)
    async def test_forged_signature_source_cannot_allow(self):
        task,a=await self.open();obj=self.ph.answer(self.h._ask_msg(a,self.s),True)
        obj['sig']='A'*86
        await self.h._answer(self.s,obj);self.assertFalse(a.fut.done())
        obj=self.ph.answer(self.h._ask_msg(a,self.s),True)
        obj['envelope_sha256']='0'*64
        await self.h._answer(self.s,obj);self.assertFalse(a.fut.done())
    async def test_external_and_unknown_never_skip_uv(self):
        for source in ({}, {**self.origin,'channel':'email','verified':False,'suspicious':True}):
            with approval_review.origin_scope(source):
                task,a=await self.open()
            self.assertTrue(a.require_passkey)
            obj=self.ph.answer(self.h._ask_msg(a,self.s),True);obj.pop('fa')
            await self.h._answer(self.s,obj);self.assertFalse(a.fut.done())
            await self.close(task,a);self.h.asks.clear()
    async def test_expired_card_cannot_allow(self):
        task,a=await self.open();a.deadline=time.monotonic()-1
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
        self.assertFalse(a.fut.done())
    async def test_missing_device_denies(self):
        with mock.patch.object(self.h,'_approvers',return_value=[]):
            result=await self.h.ask('agentj fn run',{'name':'synthetic'})
        self.assertEqual(result['reason'],'no_device')
    async def test_native_permission_card_is_plain_even_for_delete(self):
        listed(self,'Bash')  # 0.30.7 ADR-0307-offlist: card mechanics; off-list Bash no longer cards, so list it
        task,a=await self.open('Bash',{'command':'rm -rf /synthetic/nonexistent'})
        self.assertFalse(a.require_passkey)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
        self.assertEqual((await task)['behavior'],'allow')
    async def test_background_permission_plain_not_group_source(self):
        listed(self,'Bash')  # 0.30.7 ADR-0307-offlist: card mechanics; off-list Bash no longer cards, so list it
        with approval_review.origin_scope({}):
            task,a=await self.open('Bash',{'command':'systemctl --user list-timers'})
        self.assertFalse(a.require_passkey)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),False))
        self.assertEqual((await task)['reason'],'device')
    def test_main_requirement_does_not_classify_effects(self):
        with mock.patch('agentj.danger.classify',side_effect=AssertionError('legacy classifier')):
            for tool in ('Bash','Write','Edit','mcp__unknown__transfer_money','opaque_native_tool'):
                self.assertEqual(policy_registry.main_requirement(tool,{'command':'curl token | rm -rf /synthetic/nonprotected'}),'allow')
    def test_secret_request_and_security_changes_in_list(self):
        self.assertTrue(uv_list.required('agentj secret request'))
        self.assertTrue(uv_list.required('agentj config',{'changes':{'approval.level':'full'}}))
        self.assertFalse(uv_list.required('agentj config',{'changes':{'language':'en'}}))
    async def test_old_caller_uv_flag_cannot_upgrade_native_tool(self):
        listed(self,'Bash')  # 0.30.7 ADR-0307-offlist: card mechanics; off-list Bash no longer cards, so list it
        task=asyncio.create_task(self.h.ask('Bash',{'command':'ls'},require_passkey=True,always_ask=True))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.002)
        a=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.close,task,a)
        self.assertFalse(a.require_passkey)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
        self.assertEqual((await task)['behavior'],'allow')
    def test_phone_display_survives_delivery_flags(self):
        self.h.agent.cfg={'session_mode':'shared'}
        self.h.agent.phone_turn=False;self.h.agent.phone_seen=False
        source=approval_review.snapshot(self.h)
        self.assertEqual(source['channel'],'phone')
        self.assertEqual(source['text'],'Synthetic owner command.')
    async def test_pretool_guard_never_calls_effect_classifier(self):
        from agentj.shared_risk import RiskGuard
        adapter=SimpleNamespace(host=self.h)
        with mock.patch('agentj.danger.classify_shared',side_effect=AssertionError('effect classifier')):
            self.assertTrue(await RiskGuard(adapter).check('Bash',{'command':'rm -rf /synthetic'}))
    def test_terminal_does_not_classify_line_for_uv(self):
        from agentj.terminal import line_needs_face
        with mock.patch('agentj.confirm_tier.harness_keeps',side_effect=AssertionError('old classifier')):
            self.assertFalse(line_needs_face('rm -rf /synthetic'))
            self.assertFalse(line_needs_face('agentj fn run synthetic'))
    def test_codex_policy_preserves_native_config(self):
        from agentj.agent_codex import CodexAgent
        agent=SimpleNamespace(human={'approval_policy':'never','sandbox_mode':'read-only','approvals_reviewer':'user'},
                              cfg={'follow_harness':True},research=False)
        policy=CodexAgent.policy(agent)
        self.assertEqual(policy['approvalPolicy'],'never')
        self.assertEqual(policy['sandbox'],'read-only')
    def test_opencode_rules_preserve_native_bypass(self):
        from agentj.agent_opencode import session_rules
        rules=[{'permission':'*','pattern':'*','action':'allow'}]
        self.assertEqual(session_rules(rules),rules)
    def test_native_asks_never_use_jev_auto_answer(self):
        from agentj.approval_jev import low_risk_pass
        self.assertFalse(low_risk_pass(self.h,{'high_risk_warnings':True},'Read',{'file_path':'/synthetic'}))
    async def test_unlink_binds_exact_account_target(self):
        from agentj import list_approval
        before={'tenant':{'slug':'synthetic-a'}}
        with mock.patch('agentj.cloud.read_cloud',return_value=before), mock.patch.object(self.h,'ask',new=mock.AsyncMock()) as ask:
            result=await list_approval.security(self.h,{'command':'agentj unlink','target_sha256':'0'*64})
            self.assertFalse(result['ok']);ask.assert_not_awaited()
        ask=mock.AsyncMock(return_value={'behavior':'allow','grant_info':{'uv':True,'device':self.ph.did,'sign_pub':self.st.sign_key(self.ph.did)}})
        with mock.patch('agentj.cloud.read_cloud',side_effect=[before,{'tenant':{'slug':'synthetic-b'}}]),mock.patch.object(self.h,'ask',new=ask):
            result=await list_approval.security(self.h,{'command':'agentj unlink','target_sha256':approval_review.digest(before)})
        self.assertFalse(result['ok'])
    async def test_sandbox_failure_requires_signed_tap(self):
        task,a=await self.open('SandboxOnce',{'reason':'no_bwrap'})
        self.assertFalse(a.require_passkey)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),False))
        self.assertEqual((await task)['behavior'],'deny')
    async def test_repeated_native_invocations_are_independent_requests(self):
        listed(self,'Bash')  # 0.30.7 ADR-0307-offlist: card mechanics; off-list Bash no longer cards, so list it
        keys=[]
        for _ in range(2):
            task,a=await self.open('Bash',{'command':'ls /synthetic'})
            keys.append(a.operation_once_key)
            await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
            self.assertEqual((await task)['behavior'],'allow')
        self.assertNotEqual(keys[0],keys[1])


    async def test_auth_settings_credential_card_requires_uv_and_mints_after_valid_uv(self):
        from agentj import auth_settings_uv
        task=asyncio.create_task(self.h.ask('AuthRescue',{'summary':'Synthetic credential insertion'},
            _host_card=True,_settings_session=self.s,only=self.s.device,grant_info=True))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.002)
        a=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.close,task,a)
        self.assertTrue(a.require_passkey)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
        self.assertEqual((await task)['behavior'],'allow')
        self.assertTrue(auth_settings_uv.reuse(self.h,self.s,'login'))
    async def test_bounded_executor_rejects_unsealed_model_input(self):
        result=await self.h.ask('AgentJBoundedPlan',{'summary':'model claims sealed'})
        self.assertEqual(result['behavior'],'deny')

    async def test_native_host_alias_is_not_autoallowed(self):
        # 0.30.7 ADR-0307-offlist: a native request may only card as AskUserQuestion; a host-tool alias is an ordinary
        # off-list native request (no host-executor shortcut, no card), answered at once by the owner turn's trust.
        result=await self.h.ask('DoomLoop',{'tool':'Bash'},_harness_permission=True)
        self.assertFalse(self.h.asks)
        self.assertFalse([o for _,o in self.sent if o.get('t')=='ask'])
        self.assertEqual(result['behavior'],'allow')
        self.assertEqual(approvals.read_log(self.st)[-1]['reason'],'off_list')
    async def test_legacy_plan_remains_in_signed_envelope_and_audit(self):
        self.h.cur_turn=7
        file=self.st.root.parent/'ordinary-plan-file';file.write_text('ordinary contents')
        inp={'command':'rm -- '+str(file)}
        task=asyncio.create_task(self.h.ask('Bash',inp,grant_info=True,
            task_actions=[{'tool':'Bash','input':inp}],_plan_only=True))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.002)
        a=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.close,task,a)
        self.assertEqual(a.envelope['plan']['actions'][0]['input_sha256'],approvals.input_digest(inp))
        self.assertFalse(a.require_passkey)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
        self.assertEqual((await task)['behavior'],'allow')
        signed=[r for r in approvals.read_log(self.st) if r.get('reason')=='device'][-1]
        self.assertTrue(signed['task_scope'])
        self.assertEqual(approvals.check_record(self.st,signed),'ok')
    def test_explicit_config_collection_is_visible_and_narrow(self):
        entries={entry['command'] for entry in uv_list.entries()}
        for key in uv_list.CONFIG_KEYS:
            self.assertIn('agentj config set '+key,entries)
            self.assertTrue(uv_list.required('agentj config',{'changes':{key:False}}))
        for key in ('notify.enabled','updates.report_failures','agent.high_risk_warnings'):
            self.assertFalse(uv_list.required('agentj config',{'changes':{key:False}}))

    async def test_large_ascii_input_is_rejected_before_display_truncation(self):
        listed(self,'Bash')  # 0.30.7 ADR-0307-offlist: card mechanics; off-list Bash no longer cards, so list it
        result=await self.h.ask('Bash',{'command':'A'*24001},_harness_permission=True)
        self.assertEqual(result['reason'],'too_large')
        self.assertFalse(self.h.asks)
    async def test_native_summary_cannot_hide_actual_parameters(self):
        task=asyncio.create_task(self.h.ask('AuthRescue',{'summary':'harmless','command':'synthetic-real-operation'},_harness_permission=True))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.002)
        a=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.close,task,a)
        self.assertIn('synthetic-real-operation',a.summary)
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),False))
        self.assertEqual((await task)['behavior'],'deny')

    def review_intent(self,a,op):
        from agentj import wire
        import secrets
        event=secrets.token_hex(16)
        obj={'t':'approval_review','id':a.rid,'op':op,'revision':a.review_revision,
             'event_id':event,'envelope_sha256':a.envelope_digest}
        obj['sig']=wire.b64u(self.ph.sk.sign(approval_review.review_message(self.h.channel,
            self.ph.did,a.rid,op,a.review_revision,event,a.envelope_digest)))
        return obj
    async def question_approve(self,tool,inp):
        task,a=await self.open(tool,inp)
        await approval_review.on_phone(self.h,self.s,self.review_intent(a,'question'))
        result=await task
        self.assertTrue(result['review_pending']);self.assertFalse(a.fut.done())
        explain=self.h.agent.submit.call_args.args[0]
        self.h.agent.cur_send=explain;self.h.cur_turn=explain.turn
        await approval_review.on_phone(self.h,self.s,self.review_intent(a,'reopen'))
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),True))
        await asyncio.sleep(.01)
        self.assertEqual(a.fut.result()[0],'allow')
        resume=self.h.agent.submit.call_args.args[0]
        self.assertIsNot(resume,explain)
        self.h.agent.cur_send=resume
        return a,resume
    async def test_question_original_uv_resumes_function_once_with_original_proof(self):
        inp={'name':'synthetic','argv':['true'],'cwd':'/synthetic','digest':'0'*64}
        a,resume=await self.question_approve('agentj fn run',inp)
        count=len([v for _,v in self.sent if v and v.get('t')=='ask'])
        answer=await self.h.ask('agentj fn run',inp,grant_info=True)
        self.assertEqual(answer['behavior'],'allow');self.assertTrue(answer['grant_info']['uv'])
        self.assertEqual(answer['grant_info']['rid'],a.rid)
        self.assertEqual(answer['grant_info']['fa'],a.fa)
        self.assertEqual(len([v for _,v in self.sent if v and v.get('t')=='ask']),count)
        second,new=await self.open('agentj fn run',inp)
        self.assertNotEqual(new.rid,a.rid);self.assertTrue(new.require_passkey)
    async def test_question_plain_native_resumes_once_and_cannot_borrow_other_input(self):
        listed(self,'Bash')  # 0.30.7 ADR-0307-offlist: card mechanics; off-list Bash no longer cards, so list it
        inp={'command':'rm /synthetic'}
        a,resume=await self.question_approve('Bash',inp)
        other,new=await self.open('Bash',{'command':'rm /other-synthetic'})
        self.assertFalse(other.done())
        await self.close(other,new);self.h.asks.pop(new.rid,None)
        answer=await self.h.ask('Bash',inp,grant_info=True)
        self.assertEqual(answer['behavior'],'allow');self.assertEqual(answer['grant_info']['rid'],a.rid)
        self.assertFalse(answer['grant_info']['uv'])
    async def test_question_resume_requires_host_send_identity_current_key_and_original_deadline(self):
        from agentj import list_approval
        inp={'name':'synthetic'}
        a,resume=await self.question_approve('agentj fn run',inp)
        context=a.list_resume_context
        self.h.agent.cur_send=SimpleNamespace(**resume.__dict__)
        self.assertIsNone(list_approval.take_resume(self.h,a.tool,inp,True,context,None))
        self.h.agent.cur_send=resume
        with mock.patch.object(self.st,'sign_key',return_value=b'0'*32):
            self.assertIsNone(list_approval.take_resume(self.h,a.tool,inp,True,context,None))
        self.assertFalse(self.h.__dict__.get('_uv_list_resumes'))
        a,resume=await self.question_approve('agentj fn run',inp)
        self.h.__dict__['_uv_list_resumes'][a.rid]['deadline']=time.monotonic()-1
        self.assertIsNone(list_approval.take_resume(self.h,a.tool,inp,True,a.list_resume_context,None))

    async def test_real_shared_permission_callback_preserves_exact_phone_source(self):
        listed(self,'Bash')  # 0.30.7 ADR-0307-offlist: card mechanics; off-list Bash no longer cards, so list it
        from agentj.shared import SharedClaudeAgent
        actor=SharedClaudeAgent(self.h,{**(self.h.agent_cfg or {}),'session_mode':'shared','dir':str(self.st.root.parent)})
        actor.session={'sessionId':'synthetic-native'}
        actor.cur_send=self.h.agent.cur_send
        actor.phone_turn=False;actor.phone_seen=False
        self.h.agent=actor
        event={'hook_event_name':'PermissionRequest','session_id':'synthetic-native','tool_name':'Bash',
               'tool_input':{'command':'ls /synthetic'}}
        task=asyncio.create_task(actor._scoped_hook_event(event))
        for _ in range(100):
            if self.h.asks or task.done():break
            await asyncio.sleep(.002)
        self.assertFalse(task.done(),str(task.result()) if task.done() else '')
        a=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.close,task,a)
        self.assertEqual(a.envelope['source']['channel'],'phone')
        self.assertEqual(a.envelope['source']['message_id'],self.origin['message_id'])
        self.assertEqual(a.envelope['source']['text'],'Synthetic owner command.')
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(a,self.s),False))
        self.assertEqual((await task)['hookSpecificOutput']['decision']['behavior'],'deny')
    def test_independent_unknown_request_does_not_borrow_active_phone_display(self):
        with provenance.request_source(None):
            self.assertEqual(approval_review.snapshot(self.h),{})

    async def test_question_workflow_grant_original_v2_proof_remains_verifiable(self):
        from agentj import workflow_mail
        import copy
        core={'contract':{'workflow_id':'synthetic','root':str(self.st.root.parent)},
              'binding':{'primary':'owner@example.invalid'},'artifact_root':str(self.st.root.parent),
              'expires':int(time.time())+3600,'max':10,'per_day':3,'max_files':10,'max_bytes':26214400,
              'id':'synthetic-grant','host':self.h.channel,'lang':'en'}
        inp={'summary':workflow_mail.shown(core)}
        a,resume=await self.question_approve(workflow_mail.TOOL,inp)
        answer=await self.h.ask(workflow_mail.TOOL,inp,grant_info=True)
        proof=answer['grant_info']
        self.assertTrue(workflow_mail.proof_ok(self.st,core,proof))
        self.assertEqual(proof['rid'],a.rid);self.assertEqual(proof['approval_v'],2)
        bad=copy.deepcopy(proof);bad['envelope']['input_sha256']='0'*64
        self.assertFalse(workflow_mail.proof_ok(self.st,core,bad))
        bad=copy.deepcopy(proof);bad.pop('fa')
        self.assertFalse(workflow_mail.proof_ok(self.st,core,bad))
        bad=copy.deepcopy(proof);bad.pop('approval_v');bad.pop('envelope')
        self.assertFalse(workflow_mail.proof_ok(self.st,core,bad))

    async def test_question_function_controller_executes_original_once_then_requires_new_card(self):
        from agentj import functions
        import os,subprocess,sys
        root=self.st.root.parent
        row=functions.record('synthetic',[sys.executable,'-c',"open('executed-once','a').write('1')"],str(root))
        functions._save(self.st,{'synthetic':row})
        a,resume=await self.question_approve('agentj fn run',row)
        result=await functions.handle(self.h,{'t':'fn','op':'run','name':'synthetic'},peer=os.getpid())
        self.assertTrue(result['ok'],result)
        subprocess.run(result['function']['argv'],cwd=result['function']['cwd'],check=True)
        self.assertEqual((root/'executed-once').read_text(),'1')
        again=asyncio.create_task(functions.handle(self.h,{'t':'fn','op':'run','name':'synthetic'},peer=os.getpid()))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.002)
        self.assertFalse(again.done())
        next_card=next(iter(self.h.asks.values()));self.addAsyncCleanup(self.close,again,next_card)
        self.assertNotEqual(next_card.rid,a.rid);self.assertTrue(next_card.require_passkey)
        self.assertEqual((root/'executed-once').read_text(),'1')
    async def test_emergency_stop_destroys_question_continuation(self):
        from agentj import list_approval
        a,resume=await self.question_approve('agentj fn run',{'name':'synthetic'})
        self.assertTrue(self.h.__dict__.get('_uv_list_resumes'))
        await self.h.do_estop('synthetic-test','Synthetic test')
        self.assertFalse(self.h.__dict__.get('_uv_list_resumes'))
        self.assertIsNone(list_approval.take_resume(self.h,a.tool,a.tool_input,True,a.list_resume_context,None))

class SecretList(unittest.TestCase):
    import test_p541_special as _fixture
    setUp=_fixture.Special.setUp
    messages=_fixture.Special.messages
    pickup=_fixture.Special.pickup
    def test_external_secret_pickup_never_skips_owner_uv(self):
        from agentj import approval_special
        from test_p73_secret import fa_out
        async def run():
            source={'message_id':None,'root_message_id':None,'session':str(self.host.hist.epoch),
                    'channel':'webhook','verified':False,'suspicious':True,'text':'Synthetic external data','chain':[]}
            with approval_review.origin_scope(source):
                card=await self.pickup()
            c=self.host.elevate.out.cards[card['id']]
            self.assertTrue(approval_special.source_allows(c,self.host))
            await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n']})
            self.assertEqual(self.messages('secret_out_val'),[])
            await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n'],
                'fa':fa_out(self.a.auth,self.a,card)})
            values=[(cid,obj) for cid,obj in self.sent if obj and obj.get('t')=='secret_out_val']
            self.assertEqual([cid for cid,_ in values],[self.sa.cid])
            self.assertEqual(self.host.elevate.out.cards,{})
        asyncio.run(run())


class OfflineConfig(unittest.TestCase):
    from test_preferences import PreferencesTest as _fixture
    setUp=_fixture.setUp
    def test_initial_real_agent_root_bootstrap_and_offline_approval_denial(self):
        from agentj import preferences
        preferences.ensure()
        work=self.home/'work';work.mkdir()
        with mock.patch('agentj.names.ctl_call',return_value=None), mock.patch('agentj.voice.validate_runtime'), mock.patch('agentj.claude_statusline.ensure_default'), mock.patch('agentj.claude_inbound.ensure_shared_default'):
            self.st.set_agent_config('claude',str(work))
            self.assertEqual(preferences.get(preferences.effective(self.st),'agent.working_root'),str(work))
            old=preferences.path().read_bytes()
            for exists in (True,False):
                with mock.patch.object(self.st,'exists',return_value=exists):
                    with self.assertRaises(preferences.ConfigError):
                        preferences.transact(self.st,preferences.edit(old.decode(),'approval.level','harness'))
                self.assertEqual(preferences.path().read_bytes(),old)
            result=preferences.transact(self.st,preferences.edit(old.decode(),'appearance.theme','dark'))
            self.assertTrue(result['ok'])
    def test_nonlist_config_has_no_uv_and_root_cannot_rebootstrap(self):
        from agentj import preferences
        preferences.ensure()
        one=self.home/'one';one.mkdir();two=self.home/'two';two.mkdir()
        with mock.patch('agentj.names.ctl_call',return_value=None), mock.patch('agentj.voice.validate_runtime'), mock.patch('agentj.claude_statusline.ensure_default'), mock.patch('agentj.claude_inbound.ensure_shared_default'):
            self.st.set_agent_config('claude',str(one))
            with self.assertRaises(preferences.ConfigError):
                self.st.set_agent_config('claude',str(two))
            old=preferences.read()[0]
            result=preferences.transact(self.st,preferences.edit(old,'notify.enabled',False))
            self.assertTrue(result['ok'])
