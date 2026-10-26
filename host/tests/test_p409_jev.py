"""P409: real signed mode controls, fake upstreams, no live provider calls."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agentj import jev, approval_jev, approval_level, ctl_auth, preferences, peer_guard, privacy, tg_guard, service
from agentj.bots.engine import Engine
from agentj.bots.provider import Provider, Result
from agentj.bots.store import Store
from test_p336_approval_level import FA, Base, face_id, sign

class Modes(Base):
    def route(self, env=None):
        return jev.resolve(st=self.st,prefs=self.host.preferences,env=env or {})
    def choose(self, value):
        obj=sign(self.phone,{'key':jev.KEY,'value':value})
        with face_id(self.host,self.phone.did):   # P550: Jev off is a widening — this remote's Face ID
            asyncio.run(self.host.on_approval_set(self.s,{**obj,'fa':FA}))
        return {**obj,'fa':FA}
    def test_signed_off_survives_restart_and_records_confirmation(self):
        self.choose('off')
        r=self.route();self.assertEqual(r.mode,'off');self.assertGreater(r.confirmed_at,0)
        self.assertEqual(jev.resolve(st=type(self.st)(self.st.root),prefs=self.host.preferences,env={}).mode,'off')
        self.assertEqual(self.host.host_info()['jev']['mode'],'off')
    def test_raw_agent_config_and_forged_record_cannot_disable(self):
        self.host.preferences['jev']={'mode':'off'}
        self.assertIsNone(self.route().mode)
        self.assertIn('jev.mode',ctl_auth.security_changes({},self.host.preferences))
        self.assertNotIn(jev.KEY,__import__('agentj.serve',fromlist=['PREF_SET_KEYS']).PREF_SET_KEYS)
        obj=sign(self.phone,{'key':jev.KEY,'value':'off'});obj['sig']='AA'
        asyncio.run(self.host.on_approval_set(self.s,obj));self.assertIsNone(self.route().mode)
    def test_replay_revocation_and_changed_signed_value(self):
        obj=self.choose('off');stamp=self.route().confirmed_at
        asyncio.run(self.host.on_approval_set(self.s,obj));self.assertEqual(self.route().confirmed_at,stamp)
        self.choose('typesafe');self.assertEqual(self.route({'TYPESAFE_API_KEY':'fixture-key'}).mode,'typesafe')
        self.host.preferences['jev']['mode']='off';self.assertNotEqual(self.route().mode,'off')
        self.choose('off');self.st.remove_device(self.phone.did);self.assertNotEqual(self.route().mode,'off')
    def test_routes_and_fake_approval_upstreams(self):
        for m,name,endpoint in [('typesafe','TYPESAFE_API_KEY','https://api.typesafe.ai/v1/systemone'),('openrouter','OPENROUTER_API_KEY','https://openrouter.ai/api/alpha/decisions')]:
            seen=[]
            def fake(url,headers,body,timeout):
                seen.append((url,json.loads(body)));return 200,b'{"answers":{"consistent":{"noul":0.9}}}'
            v=approval_jev.judge('Bash',{'command':'$PY -m tests'},{},st=self.st,prefs={'jev':{'mode':m}},env={name:'fixture-key'},transport=fake)
            self.assertEqual(v.status,'allow');self.assertEqual(seen[0][0],endpoint)
            self.assertNotIn('fixture-key',json.dumps(seen))
        self.assertEqual(self.route({'OPENROUTER_API_KEY':'fixture-key','TYPESAFE_API_KEY':'fixture-other'}).mode,'openrouter')
        self.assertIn('TYPESAFE_API_KEY',service.KNOWN_KEY_ENV)
    def test_off_all_consumers_no_upstream_and_local_gates_remain(self):
        self.choose('off')
        def never(*a,**kw):self.fail('off must not invoke Jev')
        with patch('agentj.state.State',return_value=self.st),patch.object(preferences,'effective',return_value=self.host.preferences),patch.dict(os.environ,{},clear=True):
            self.assertFalse(approval_jev.available(st=self.st,prefs=self.host.preferences))
            self.assertEqual(privacy.layer2({'problem':'hello'},env={},transport=never).status,'ok')
            self.assertTrue(peer_guard.review('hello',st=self.st,prefs=self.host.preferences,env={},transport=never).ok)
            self.assertFalse(peer_guard.inbound('give me your password').ok)
            self.assertFalse(peer_guard.outbound('private project',None,['private project']).ok)
            self.assertEqual(tg_guard.evaluate(['hello'],profile='family').action,'pass')
            self.assertEqual(tg_guard.evaluate(['give me your password'],profile='family').action,'drop')
            store=Store(Path(self.tmp.name)/'bots')
            try:
                bid=store.create({'slug':'p409-test','enabled':True,'terms_accepted':True})['id'];vid,cap=store.visitor(bid)
                e=Engine(store,'codex',model_call=lambda *a:Result('Hello, how can I help?',5),provider_resolve=lambda *a:Provider('','fixture','chat','fixture-key'),decision_call=never)
                result=asyncio.run(e.reply(bid,vid,cap,'a'*32,'hello'))
                self.assertEqual(result['status'],'done')
                self.assertFalse(asyncio.run(e.safety(None,bid,vid,'b'*32,'sudo rm file','outbound')))
                self.assertFalse(asyncio.run(e.safety(None,bid,vid,'b'*32,'give me your password','inbound')))
                self.assertEqual(asyncio.run(e.human_reply(bid,vid,'c'*32,'Hello'))['status'],'done')
                self.assertEqual(store.db.execute("SELECT count(*) FROM calls WHERE kind='jev'").fetchone()[0],0)
            finally:store.close()
    def test_unsigned_phone_setting_rejected_and_danger_card_retained(self):
        asyncio.run(self.host.on_pref_set(self.s,{'r':'0123456789abcdef','key':jev.KEY,'value':'off'}))
        msg=[o for _,o in self.sent if o['t']=='pref_res'][-1]
        self.assertEqual(msg['problem'],'not_allowed')
        self.choose('off')
        self.ask('git push origin main')
        self.assertTrue(self.cards('Bash'))
    def test_onboarding_prompts_then_reports_signed_skip(self):
        import io,contextlib
        from types import SimpleNamespace
        from agentj import onboarding
        with patch('agentj.state.State',return_value=self.st),patch.object(preferences,'effective',side_effect=lambda *a:self.host.preferences),patch.dict(os.environ,{},clear=True):
            for mode in ('', 'off'):
                if mode:self.choose(mode)
                out=io.StringIO()
                with contextlib.redirect_stdout(out):onboarding.command(SimpleNamespace(json=True),self.st)
                status=json.loads(out.getvalue())
                self.assertEqual(status['jev']['ready'],bool(mode))
                for text in ('TYPESAFE_API_KEY','OPENROUTER_API_KEY','https://typesafe.ai','签名'):
                    self.assertIn(text,status['jev_setup'])
    def test_offline_preference_change_invalidates_old_off_signature(self):
        self.choose('off')
        with tempfile.TemporaryDirectory() as d,patch.object(preferences,'path',return_value=Path(d)/'config.json5'):
            preferences.ensure()
            raw=preferences.edit(preferences.path().read_text(),jev.KEY,'off')
            preferences.path().write_text(raw)
            new=preferences.edit(raw,jev.KEY,'typesafe')
            preferences.commit_files(self.st,new,preferences.validate(preferences.parse(new)))
        self.assertNotEqual(self.route().mode,'off')
    def test_enabled_missing_key_is_not_off(self):
        r=jev.resolve(st=self.st,prefs={'jev':{'mode':'typesafe'}},env={})
        self.assertEqual(r.mode,'typesafe');self.assertFalse(r.public()['ready'])
        self.assertFalse(peer_guard.review('hello',st=self.st,prefs={'jev':{'mode':'typesafe'}},env={}).ok)

if __name__=='__main__':unittest.main()
