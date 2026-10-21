import _hermetic
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from agentj import ark, ark_plan_fix as fix, provider_runtime, switch_harness
from agentj.state import State
import test_p332_reset as rb

MID='doubao-seed-2.1-pro'
VALUE='synthetic-private-ark-value'

class Plans(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'work').mkdir()
        env=mock.patch.dict(os.environ,{'HOME':str(self.root),'XDG_CONFIG_HOME':str(self.root/'cfg'),'AGENTJ_STATE_DIR':str(self.root/'st'),'ARK_AGENT_PLAN_API_KEY':'','ARK_CODING_PLAN_API_KEY':''})
        env.start();self.addCleanup(env.stop)
        self.st=State();self.st.init()
        self.keyfile=self.root/'managed.env'
        patch=mock.patch.object(provider_runtime,'env_path',return_value=str(self.keyfile));patch.start();self.addCleanup(patch.stop)
        self.setup_plan('coding')
    def setup_plan(self,plan):
        self.source=ark.plan_data(plan)['provider'];self.source_name=ark.plan_data(plan)['key_env']
        self.target='ark-agent-plan' if plan=='coding' else 'ark-coding-plan'
        self.target_name=ark.plan_data('agent' if plan=='coding' else 'coding')['key_env']
        ark.install(MID,plan=plan)
        self.keyfile.write_text(self.source_name+'='+VALUE+'\nUNRELATED_KEY=do-not-use\n');self.keyfile.chmod(0o600)
        self.st.set_agent_config('opencode',str(self.root/'work'),model=self.source+'/'+MID,fence=False)
    def offer(self,**kw):
        return fix.diagnose(self.st,self.source+'/'+MID,check=lambda url,key:200,**kw)
    def test_both_directions_use_one_exact_key_and_canonical_address(self):
        for plan in ('coding','agent'):
            self.setup_plan(plan)
            (self.st.root/fix.FILE).unlink(missing_ok=True)
            seen=[]
            def check(url,key):seen.append((url,key));return 200
            v=fix.diagnose(self.st,self.source+'/'+MID,check=check)
            self.assertEqual(v['model'],self.target+'/'+MID)
            self.assertEqual(seen,[(ark.plan_data('agent' if plan=='coding' else 'coding')['block']['options']['baseURL']+'/models',VALUE)])
            self.assertNotIn(VALUE,json.dumps(v));self.assertNotIn('key_hash',v)
            self.assertNotIn(VALUE,(self.st.root/fix.FILE).read_text())
            self.assertFalse(fix.read_key(self.target_name),'no key was copied before a tap')
    def test_only_confirmed_401_offers_and_models_unsupported_uses_native_probe(self):
        check=mock.Mock(return_value=200)
        self.assertIsNone(fix.diagnose(self.st,self.source+'/'+MID,http_status=429,check=check));check.assert_not_called()
        self.assertIsNone(fix.diagnose(self.st,'other/'+MID,check=check));check.assert_not_called()
        self.assertIsNone(fix.diagnose(self.st,self.source+'/'+MID,check=lambda *a:401))
        native=mock.Mock(return_value=True)
        self.assertIsNotNone(fix.diagnose(self.st,self.source+'/'+MID,check=lambda *a:404,native=native))
        native.assert_called_once_with(self.target,MID,self.source_name,VALUE)
        again=mock.Mock()
        self.assertIsNotNone(fix.diagnose(self.st,self.source+'/'+MID,check=again));again.assert_not_called()
    def test_apply_copies_locally_probes_same_model_and_uses_no_new_key_card(self):
        v=self.offer()
        calls=[]
        def probe(model):
            self.assertEqual(fix.read_key(self.target_name),VALUE)
            self.assertEqual(self.st.agent_config()['model'],self.source+'/'+MID)
            calls.append(model);return {'ok':True}
        r=fix.apply(self.st,v['action'],probe=probe)
        self.assertTrue(r['ok']);self.assertEqual(calls,[self.target+'/'+MID])
        self.assertEqual(fix.read_key(self.source_name),VALUE)
        self.assertEqual(self.st.agent_config()['model'],self.target+'/'+MID)
        self.assertNotIn(VALUE,json.dumps(r));self.assertIsNone(fix.describe(self.st))
        self.assertEqual(fix.apply(self.st,v['action'],probe=probe)['why'],'stale')
    def test_failed_probe_preserves_main_route_and_masks_error_line(self):
        v=self.offer()
        r=fix.apply(self.st,v['action'],probe=lambda *a:{'ok':False,'line':'HTTP 401 '+VALUE+'\nsecond line'})
        self.assertFalse(r['ok']);self.assertNotIn(VALUE,json.dumps(r));self.assertNotIn('second line',json.dumps(r))
        self.assertEqual(self.st.agent_config()['model'],self.source+'/'+MID)
    def test_changed_key_expired_offer_and_existing_target_are_not_overwritten(self):
        v=self.offer()
        self.keyfile.write_text(self.source_name+'=changed\n')
        self.assertEqual(fix.apply(self.st,v['action'])['why'],'changed')
        self.setup_plan('coding');v=self.offer()
        with self.keyfile.open('a') as f:f.write(self.target_name+'=existing-different-value\n')
        self.assertEqual(fix.apply(self.st,v['action'])['why'],'target_key_conflict')
        self.assertEqual(fix.read_key(self.target_name),'existing-different-value')
        record=json.loads((self.st.root/fix.FILE).read_text());record['at']-=fix.TTL+1
        self.st.write_private(self.st.root/fix.FILE,json.dumps(record).encode())
        self.assertEqual(fix.apply(self.st,v['action'])['why'],'stale')
    def test_other_harness_reuses_normal_switch_gate_and_force_intent(self):
        self.st.set_agent_config('claude',str(self.root/'work'),fence=False)
        v=self.offer(force=True)
        def requested(*a,**kw):
            switch_harness.write_receipt(self.st,{'target':'opencode','model':self.target+'/'+MID,'status':'waiting','job':'fixture-job'})
            return {'ok':True,'scheduled':True}
        request=mock.Mock(side_effect=requested)
        probe=mock.Mock()
        r=fix.apply(self.st,v['action'],probe=probe,request=request)
        self.assertTrue(r['pending']);probe.assert_not_called()
        request.assert_called_once_with(self.st,'opencode',self.target+'/'+MID,force=True)
        self.assertEqual(self.st.agent_config()['kind'],'claude')
        switch_harness.write_receipt(self.st,{'target':'opencode','model':self.target+'/'+MID,'status':'failed','job':'fixture-job'})
        self.assertEqual(fix.view(self.st)['outcome'],'failed')
    def test_native_probe_uses_temporary_provider_config_without_persisting_key(self):
        saved=[]
        def probe(exe,model,env,timeout,**kw):
            p=Path(env['OPENCODE_CONFIG']);saved.append(p)
            block=json.loads(p.read_text())['provider'][self.target]
            self.assertEqual(block['options']['apiKey'],'{env:'+self.source_name+'}')
            self.assertEqual(env[self.source_name],VALUE)
            self.assertNotIn('OPENCODE_CONFIG_CONTENT',env)
            self.assertNotIn('OPENCODE_CONFIG_DIR',env)
            self.assertEqual(kw['waits'],());return 'hello'
        with mock.patch.dict(os.environ,{'OPENCODE_CONFIG_CONTENT':'{"provider":{"unsafe":true}}','OPENCODE_CONFIG_DIR':'/unsafe'}),mock.patch('agentj.harness.agent_bin',return_value='/fixture/opencode'),mock.patch('agentj.harness.version_of',return_value='2.0.30'),mock.patch('agentj.agent_opencode2.probe',side_effect=probe):
            self.assertTrue(fix._native_probe(self.target,MID,self.source_name,VALUE))
        self.assertFalse(saved[0].exists());self.assertFalse(fix.read_key(self.target_name))
    def test_chat_401_offer_comes_from_the_native_error_event(self):
        from agentj import agent_opencode as oc
        async def run():
            a=object.__new__(oc.OpenCodeAgent)
            a.cfg={"model":self.source+'/'+MID,"session_mode":"independent"}
            a.sid,a.connected_at_start,a.provider_fail_noted,a.restart_after_turn='ses_1',[],False,False
            a.host=SimpleNamespace(st=self.st,agent_notice=mock.Mock())
            tasks=[]
            a._bg=lambda coro:tasks.append(asyncio.create_task(coro))
            with mock.patch.object(fix,'notify',new_callable=mock.AsyncMock) as notify:
                a.on_event({"type":"session.error","properties":{"sessionID":"ses_1","error":{"name":"APIError","data":{"statusCode":401}}}})
                await asyncio.gather(*tasks)
                notify.assert_awaited_once_with(a.host,self.source+'/'+MID)
        asyncio.run(run())
    def test_default_apply_probe_passes_new_key_to_native_opencode(self):
        v=self.offer()
        with mock.patch.object(fix,'_native_probe',return_value={"ok":True}) as probe:
            self.assertTrue(fix.apply(self.st,v['action'])['ok'])
            probe.assert_called_once_with(self.target,MID,self.target_name,VALUE,detail=True)
    def test_notify_offers_without_key_card_and_unknown_key_uses_existing_card(self):
        async def run():
            host=SimpleNamespace(st=self.st,_send_p33=mock.AsyncMock())
            with mock.patch.object(fix,'diagnose',return_value={'action':'x'}),mock.patch.object(fix,'view',return_value={'offer':None}),mock.patch.object(provider_runtime,'request_key',new_callable=mock.AsyncMock) as card:
                await fix.notify(host,self.source+'/'+MID);card.assert_not_awaited();host._send_p33.assert_awaited_once()
            with mock.patch.object(fix,'diagnose',return_value=None),mock.patch.object(provider_runtime,'request_key',new_callable=mock.AsyncMock) as card:
                await fix.notify(host,self.source+'/'+MID);card.assert_awaited_once_with(host,self.source)
        asyncio.run(run())
        note=fix.checking_note(self.source+'/'+MID,'zh',{'message':'HTTP401 '+VALUE+'\nsecond line'})
        self.assertNotIn(VALUE,note);self.assertNotIn('second line',note);self.assertIn('HTTP401',note)
    def test_request_carries_confirmed_correction_to_cli_failure(self):
        self.st.set_agent_config('claude',str(self.root/'work'),fence=False)
        offer={'action':'ark_fix_'+'a'*24,'plan':'agent','model':'ark-agent-plan/'+MID}
        rec={'job':'job1','target':'opencode','status':'failed','reason':'call_failed','line':'HTTP401','ark_plan_fix':offer}
        with mock.patch.object(switch_harness,'_launch',return_value='job1'),mock.patch.object(switch_harness,'read_receipt',return_value=rec):
            r=switch_harness.request(self.st,'opencode',self.source+'/'+MID)
        self.assertEqual(r['ark_plan_fix'],offer);self.assertIn('Agent Plan',switch_harness._say(r,'zh'))
    def test_cli_401_failure_records_action_and_preserves_upstream_line(self):
        self.st.set_agent_config('claude',str(self.root/'work'),fence=False)
        offer={'action':'ark_fix_'+'a'*24,'plan':'agent','model':'ark-agent-plan/'+MID}
        probe=lambda **kw:{'harnesses':[{'name':'opencode','installed':True,'callable':False,'version':'2.0.30','probe_reason':'call_failed','probe_error':'HTTP 401: plan mismatch'}]}
        with mock.patch.object(fix,'diagnose',return_value=offer) as diagnosed:
            rc=switch_harness.worker(self.st,'opencode',self.source+'/'+MID,probe=probe,ctl=lambda *a,**k:{'ok':True},force=True,status=lambda:{'installed':True,'active':'active'})
        self.assertEqual(rc,1);diagnosed.assert_called_once_with(self.st,self.source+'/'+MID,force=True)
        r=switch_harness.read_receipt(self.st)
        self.assertEqual(r['line'],'HTTP 401: plan mismatch');self.assertEqual(r['ark_plan_fix'],offer)
        self.assertIn('Agent Plan',switch_harness._say_receipt(r,'zh'))

class Signed(unittest.TestCase):
    setUp=rb.Serve.setUp
    host=rb.Serve.host
    got=rb.Serve.got
    def test_paired_read_reaches_actual_p33_router(self):
        async def run():
            ph,other,host,s,s2=self.host()
            public={'offer':{'action':'ark_fix_'+'b'*24,'plan':'agent','model':'ark-agent-plan/'+MID},'current':None,'outcome':None}
            with mock.patch.object(fix,'view',return_value=public) as view:
                await host._app(s,{'t':'ark_plan_get','r':'readark'})
                view.assert_called_once_with(host.st)
                self.assertEqual(self.got('ark_plan',cid=s.cid),[{'t':'ark_plan','r':'readark',**public}])
        asyncio.run(run())
    def test_scheduled_cross_harness_correction_does_not_restart_early(self):
        async def run():
            ph,other,host,s,s2=self.host()
            host.agent=SimpleNamespace(kind='claude')
            action='ark_fix_'+'c'*24
            with mock.patch.object(fix,'apply',return_value={'ok':True,'pending':True,'model':'ark-agent-plan/'+MID,'plan':'agent'}),mock.patch('agentj.reset.HostOps.restart',new_callable=mock.AsyncMock) as restart:
                await host._app(s,rb.sign(ph,'rescue_do',{'action':action},{'t':'rescue_do','r':'pendingark','action':action}))
                await host.reset_desk.task
                restart.assert_not_awaited()
                self.assertTrue(all(o.get('pending') for o in self.got('reset_done')))
        asyncio.run(run())
    def test_owner_signature_required_and_action_is_not_replayed(self):
        async def run():
            ph,other,host,s,s2=self.host()
            host.agent=SimpleNamespace(kind='opencode',_own_protocol=lambda:True,apply_model=mock.AsyncMock(return_value=None))
            action='ark_fix_'+'a'*24
            with mock.patch.object(fix,'apply',return_value={'ok':True,'model':'ark-agent-plan/'+MID,'plan':'agent'}) as apply:
                await host._app(s,{'t':'rescue_do','r':'badark','action':action});apply.assert_not_called()
                obj=rb.sign(ph,'rescue_do',{'action':action},{'t':'rescue_do','r':'arkok','action':action})
                await host._app(s,obj);await host.reset_desk.task
                apply.assert_called_once_with(host.st,action)
                host.agent.apply_model.assert_awaited_once_with('ark-agent-plan/'+MID,None)
                self.assertTrue(any(o.get('ok') for o in self.got('reset_done')))
                await host._app(s,obj);self.assertEqual(apply.call_count,1)
        asyncio.run(run())
