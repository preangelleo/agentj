import _hermetic
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock
from agentj import opencode_provider as op, provider_runtime, keys
from agentj.state import State


class Providers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root/'cfg'), 'AGENTJ_STATE_DIR': str(self.root/'state')})
        env.start(); self.addCleanup(env.stop)
        self.st = State(); self.st.init()
        self.envfile = self.root/'keys.env'; self.envfile.write_text(''); self.envfile.chmod(0o600)
        p = mock.patch.object(provider_runtime, 'env_path', return_value=str(self.envfile)); p.start(); self.addCleanup(p.stop)
        self.st.set_agent_config('opencode', str(self.root), model='ark-agent-plan/doubao-seed-2.1-pro', fence=False)
        self.agent = SimpleNamespace(kind='opencode', cfg=self.st.agent_config(), owns_harness=lambda: True, turn_done=None, proc=None)
        self.host = SimpleNamespace(st=self.st, agent=self.agent, agent_cfg=self.agent.cfg)

    def test_204_auxiliary_ownership_and_unify_preserve_other_settings(self):
        from agentj import provider_setup as ps
        cfg = {'small_model': 'agentsrelay/claude-small', 'agent': {'review': {'model': 'agentsrelay/claude', 'permission': {'bash': 'deny'}}, 'build': {'temperature': 0.2}}, 'provider': {'custom': {'options': {'apiKey': '{env:CUSTOM_KEY}'}}}}
        op._write(op.config_file(), cfg)
        view = ps.routing(self.host)
        self.assertEqual(view['main'], self.agent.cfg['model'])
        self.assertEqual([r['provider'] for r in view['rows']], ['agentsrelay', 'agentsrelay'])
        self.assertTrue(view['different'])
        self.assertTrue(ps.unify(self.host, view['stamp'])['ok'])
        out = op._load(op.config_file())
        self.assertEqual(out['small_model'], view['main'])
        self.assertEqual(out['agent']['review']['model'], view['main'])
        self.assertEqual(out['agent']['review']['permission'], {'bash': 'deny'})
        self.assertEqual(out['provider'], cfg['provider'])
        self.assertFalse(ps.routing(self.host)['different'])

    def test_204_stale_busy_external_refuse(self):
        from agentj import provider_setup as ps
        op._write(op.config_file(), {'small_model': 'other/small'})
        v = ps.routing(self.host)
        op._write(op.config_file(), {'small_model': 'changed/small'})
        self.assertFalse(ps.unify(self.host, v['stamp'])['ok'])
        v = ps.routing(self.host)
        self.agent.owns_harness = lambda: False
        self.assertFalse(ps.unify(self.host, v['stamp'])['ok'])
        self.agent.owns_harness = lambda: True
        self.agent.turn_done = asyncio.Event()
        self.assertFalse(ps.unify(self.host, v['stamp'])['ok'])

    def test_207_legacy_payg_key_offer_and_move_only_after_verified(self):
        from agentj import provider_setup as ps
        value = 'synthetic-bailian-only'
        self.envfile.write_text('BAILIAN_CODING_PLAN_API_KEY='+value+'\nUNRELATED_KEY=keep\n')
        seen = []
        def verify(name, key):
            seen.append(name); self.assertEqual(key, value)
            return name == 'DASHSCOPE_API_KEY'
        v = ps.check_key(self.host, 'BAILIAN_CODING_PLAN_API_KEY', verify=verify)
        self.assertEqual(v['target'], 'DASHSCOPE_API_KEY')
        self.assertNotIn(value, json.dumps(v))
        self.assertNotIn('DASHSCOPE_API_KEY', keys.read_env())
        self.assertTrue(ps.move_key(self.host, v['offer'], verify=verify)['ok'])
        self.assertEqual(keys.read_env()['DASHSCOPE_API_KEY'], value)
        self.assertNotIn('BAILIAN_CODING_PLAN_API_KEY', keys.read_env())
        self.assertEqual(keys.read_env()['UNRELATED_KEY'], 'keep')
        self.assertFalse(ps.move_key(self.host, v['offer'], verify=verify)['ok'])

    def test_164_ark_plan_and_payg_mismatch_no_guess_from_shape(self):
        from agentj import provider_setup as ps
        for source, target in [('ARK_CODING_PLAN_API_KEY','ARK_AGENT_PLAN_API_KEY'), ('ARK_AGENT_PLAN_API_KEY','ARK_API_KEY'), ('BAILIAN_TOKEN_PLAN_API_KEY','DASHSCOPE_API_KEY')]:
            self.envfile.write_text(source+'=synthetic-same-shape\n')
            v = ps.check_key(self.host, source, verify=lambda name, value: name == target)
            self.assertEqual(v['target'], target)
        self.assertFalse(ps.check_key(self.host, source, verify=lambda *a: False).get('offer'))
        self.assertTrue(ps.check_key(self.host, source, verify=lambda *a: True)['valid'])

    def test_move_changed_conflict_and_reverify_fail_preserve_source(self):
        from agentj import provider_setup as ps
        source = 'ARK_CODING_PLAN_API_KEY'; target = 'ARK_AGENT_PLAN_API_KEY'
        self.envfile.write_text(source+'=synthetic-first\n')
        verify = lambda name, value: name == target
        v = ps.check_key(self.host, source, verify=verify)
        self.envfile.write_text(source+'=synthetic-changed\n')
        self.assertFalse(ps.move_key(self.host, v['offer'], verify=verify)['ok'])
        v = ps.check_key(self.host, source, verify=verify)
        self.assertFalse(ps.move_key(self.host, v['offer'], verify=lambda *a: False)['ok'])
        self.assertNotIn(target, keys.read_env())
        v = ps.check_key(self.host, source, verify=verify)
        with self.envfile.open('a') as f: f.write(target+'=synthetic-existing\n')
        self.assertFalse(ps.move_key(self.host, v['offer'], verify=verify)['ok'])
        self.assertEqual(keys.read_env()[target], 'synthetic-existing')

    def test_key_saved_during_network_verification_is_never_overwritten(self):
        from agentj import provider_setup as ps
        for changed in ('ARK_CODING_PLAN_API_KEY','ARK_AGENT_PLAN_API_KEY'):
            self.envfile.write_text('ARK_CODING_PLAN_API_KEY=synthetic-first\n')
            v=ps.check_key(self.host,'ARK_CODING_PLAN_API_KEY',verify=lambda n,v:n=='ARK_AGENT_PLAN_API_KEY')
            def verify(name,value):
                if changed=='ARK_CODING_PLAN_API_KEY': self.envfile.write_text(changed+'=synthetic-newer\n')
                else:
                    with self.envfile.open('a') as f:f.write(changed+'=synthetic-newer\n')
                return True
            self.assertFalse(ps.move_key(self.host,v['offer'],verify=verify)['ok'])
            self.assertEqual(keys.read_env()[changed],'synthetic-newer')

    def test_204_limit_note_tracks_error_model_window_and_reset(self):
        from agentj import provider_setup as ps
        data = {'message': 'weekly quota exhausted', 'resetAt': '2026-10-22T10:00:00Z'}
        for lang, window in [('en','weekly'), ('zh','本周')]:
            note = ps.limit_note('agentsrelay/claude-small', 'ark-agent-plan/main', data, lang)
            self.assertIn('agentsrelay/claude-small', note)
            self.assertIn(window, note)
            self.assertIn('2026-10-22T10:00:00Z', note)
            self.assertNotIn('ark-agent-plan/main', note)

    def test_effective_routes_overlay_only_models_and_detect_source_change(self):
        from agentj import provider_setup as ps
        op._write(op.config_file(), {'small_model': 'local/small'})
        ps.remember(self.agent, {'small_model': 'project/small', 'agent': {'review': {'model': 'project/reviewer'}}})
        v=ps.routing(self.host)
        self.assertTrue(v['effective']); self.assertEqual(v['rows'][0]['provider'],'project')
        (self.root/'opencode.json').write_text('{"small_model":"owner/project-edit"}')
        self.assertFalse(ps.unify(self.host,v['stamp'])['ok'], 'stale project metadata is refused')
        v=ps.routing(self.host)
        self.assertTrue(ps.unify(self.host,v['stamp'])['ok'])
        self.host.agent=self.agent
        cfg={'permission':{'bash':'deny'},'agent':{'review':{'permission':{'edit':'deny'},'model':'project/reviewer'}}}
        out=json.loads(ps.overlay(self.host,json.dumps(cfg)))
        self.assertEqual(out['small_model'],v['main'])
        self.assertEqual(out['permission'],cfg['permission'])
        self.assertEqual(out['agent']['review']['permission'],cfg['agent']['review']['permission'])
        self.assertEqual(out['agent']['review']['model'],v['main'])
        op._write(op.config_file(),{'small_model':'owner/later-choice'})
        self.assertEqual(json.loads(ps.overlay(self.host,json.dumps(cfg))),cfg, 'later owner edits win')

    def test_expired_ambiguous_offer_and_untrusted_names_never_write(self):
        from agentj import provider_setup as ps
        self.envfile.write_text('ARK_CODING_PLAN_API_KEY=synthetic-only\n')
        v=ps.check_key(self.host,'ARK_CODING_PLAN_API_KEY',verify=lambda n,v:n=='ARK_AGENT_PLAN_API_KEY')
        with mock.patch.object(ps.time,'time',return_value=ps.time.time()+ps.TTL+1):
            self.assertFalse(ps.move_key(self.host,v['offer'],verify=lambda *a:True)['ok'])
        self.assertFalse(ps.check_key(self.host,[],verify=lambda *a:True)['ok'])
        v=ps.check_key(self.host,'ARK_CODING_PLAN_API_KEY',verify=lambda n,v:n!='ARK_CODING_PLAN_API_KEY')
        self.assertNotIn('offer',v)
        self.assertNotIn('ARK_AGENT_PLAN_API_KEY',keys.read_env())


class Events(unittest.TestCase):
    def agent(self, v2=False):
        from agentj.agent_opencode import OpenCodeAgent
        from agentj.agent_opencode2 import OpenCodeV2Agent
        a = object.__new__(OpenCodeV2Agent if v2 else OpenCodeAgent)
        a.cfg = {'model':'ark-agent-plan/main','language':'en','session_mode':'independent'}
        a.sid='main'; a._kids={'child': {}}; a.turn_done=None; a.saw_busy=False; a._turn_end=mock.Mock()
        a.connected_at_start=[]; a.provider_fail_noted=False; a.restart_after_turn=False
        a.host=SimpleNamespace(st=SimpleNamespace(log=mock.Mock()),agent_notice=mock.Mock())
        a.fail_notice=mock.Mock(); a.touch=mock.Mock(); a._child_done=mock.Mock()
        a.last_provider_fail=None
        return a

    def test_malformed_child_error_is_ignored(self):
        a=self.agent()
        a.on_event({'type':'session.error','properties':{'sessionID':'child','error':'bad shape'}})
        a.fail_notice.assert_not_called()

    def test_v1_explicit_auxiliary_limit_does_not_fail_main(self):
        a=self.agent()
        a.on_event({'type':'session.error','properties':{'sessionID':'main','error':{'name':'APIError','data':{'statusCode':429,'message':'weekly quota exhausted','providerID':'agentsrelay','modelID':'claude-small'}}}})
        a.fail_notice.assert_not_called(); self.assertFalse(a.provider_fail_noted); self.assertFalse(a.restart_after_turn)
        self.assertIn('agentsrelay/claude-small',a.host.agent_notice.call_args.args[0])

    def test_v1_child_limit_does_not_fail_main(self):
        a=self.agent()
        a.on_event({'type':'session.error','properties':{'sessionID':'child','error':{'name':'APIError','data':{'statusCode':429,'message':'weekly quota exhausted','providerID':'agentsrelay','modelID':'claude-small'}}}})
        a.fail_notice.assert_not_called(); self.assertFalse(a.provider_fail_noted)
        self.assertIn('agentsrelay/claude-small',a.host.agent_notice.call_args.args[0])

    def test_v2_auxiliary_limit_does_not_fail_main(self):
        a=self.agent(True)
        error={'type':'provider.rate-limit','status':429,'message':'weekly quota exhausted','providerID':'agentsrelay','modelID':'claude-small'}
        a.on_event({'type':'session.step.failed','data':{'sessionID':'main','error':error}})
        a.fail_notice.assert_not_called(); self.assertFalse(a.provider_fail_noted)
        self.assertIn('agentsrelay/claude-small',a.host.agent_notice.call_args.args[0])
        a.on_event({'type':'session.execution.failed','data':{'sessionID':'child','error':error}})
        self.assertEqual(a.host.agent_notice.call_count,2)

    def test_v2_ark401_uses_confirmed_plan_correction(self):
        from agentj import ark_plan_fix
        async def run():
            a=self.agent(True); tasks=[]
            a._bg=lambda coro:tasks.append(asyncio.create_task(coro))
            with mock.patch.object(ark_plan_fix,'notify',new_callable=mock.AsyncMock) as notify:
                a._failed({'type':'provider.auth','status':401,'message':'wrong plan'})
                await asyncio.gather(*tasks)
                notify.assert_awaited_once_with(a.host,'ark-agent-plan/main')
            self.assertIn('checking which plan',a.fail_notice.call_args.args[0])
            self.assertNotIn('login',a.fail_notice.call_args.args[0])
        asyncio.run(run())

    def test_main_limit_still_fails_unknown_source_stays_honest(self):
        for v2 in (False,True):
            a=self.agent(v2)
            if v2:
                a.on_event({'type':'session.execution.failed','data':{'sessionID':'main','error':{'type':'provider.rate-limit','status':429,'message':'rate limited'}}})
            else:
                a.on_event({'type':'session.error','properties':{'sessionID':'main','error':{'name':'APIError','data':{'statusCode':429,'message':'rate limited'}}}})
            self.assertTrue(a.provider_fail_noted); self.assertIn('source unconfirmed',a.fail_notice.call_args.args[0])


from test_p352_keys import Base as KeyBase
from test_p57_passkey import pk_record

class Signed(KeyBase):
    def test_unify_requires_fresh_page_bound_uv_and_rejects_changed_snapshot(self):
        from agentj import provider_setup as ps
        self.st.set_passkey(self.ph.pub,pk_record(self.a))
        self.host.agent=SimpleNamespace(kind='opencode',cfg={'model':'fixture/main'},owns_harness=lambda:True,turn_done=None,proc=None)
        op._write(op.config_file(),{'small_model':'other/small'})
        page=self.get();name='UNIFY_'+page['routing']['stamp']
        self.op(page,'unify',name)
        self.assertFalse(self.of('keys_res')[-1]['ok'])
        page=self.get();name='UNIFY_'+page['routing']['stamp']
        self.op(page,'unify',name,fa=self.fa(page,'unify',name))
        self.assertTrue(self.of('keys_res')[-1]['ok'])
        self.assertEqual(op._load(op.config_file())['small_model'],'fixture/main')
        page=self.get();name='UNIFY_'+page['routing']['stamp']
        op._write(op.config_file(),{'small_model':'later/small'})
        self.op(page,'unify',name,fa=self.fa(page,'unify',name))
        self.assertFalse(self.of('keys_res')[-1]['ok'])

    def test_move_requires_real_uv_and_nonce_not_just_offer_id(self):
        from agentj import provider_setup as ps
        self.st.set_passkey(self.ph.pub,pk_record(self.a))
        p=mock.patch.object(provider_runtime,'env_path',return_value=str(self.envf));p.start();self.addCleanup(p.stop)
        self.envf.write_text('BAILIAN_CODING_PLAN_API_KEY=synthetic-bailian\n'); self.envf.chmod(0o600)
        with mock.patch.object(ps,'_verify',side_effect=lambda name,value:name=='DASHSCOPE_API_KEY'):
            self.app({'t':'keys_op','op':'check','name':'BAILIAN_CODING_PLAN_API_KEY','r':'check1'})
            offer=self.of('keys_res')[-1]['offer']
            page=self.get()
            self.op(page,'move',offer)
            self.assertFalse(self.of('keys_res')[-1]['ok'])
            self.assertNotIn('DASHSCOPE_API_KEY',keys.read_env())
            page=self.get()
            self.op(page,'move',offer,fa=self.fa(page,'move',offer))
            self.assertTrue(self.of('keys_res')[-1]['ok'])
            self.assertEqual(keys.read_env()['DASHSCOPE_API_KEY'],'synthetic-bailian')
            self.op(page,'move',offer,fa=self.fa(page,'move',offer))
            self.assertFalse(self.of('keys_res')[-1]['ok'])
