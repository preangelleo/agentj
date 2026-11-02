"""Mock-only switch target start-config regression: no filesystem or native subprocess."""
import _hermetic
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import switch_harness as sw, olares_probe as op, shared_pin

class TargetStart(unittest.TestCase):
    def run_case(self,target,selected,saved=None,port=0):
        prev={'kind':'claude','model':'claude-sonnet-4-6','dir':'/fake/work','fence':True,
              'docker':False,'session_mode':'shared','shared_opencode_port':port,'shared_session_id':'old-claude-pin','shared_pin_owner':'owner-selection'}
        st=SimpleNamespace(agent_config=lambda:prev,log=mock.Mock())
        requests=[];seen=[];target_pin='sesTarget' if target=='opencode' else '019a3af0-1234-7000-8000-123456789abc'
        def semantic(operation,kind,model,limit,**kwargs):
            req={'op':operation,'kind':kind,'model':model,'timeout':limit}
            try:op.spec(req)
            except ValueError:return {'secs':0,'reason':'start_failed'}
            requests.append(req);return {'secs':0}
        def start(state,kind,cfg):
            seen.append(dict(cfg));return sw.start_probe(state,kind,cfg)
        with mock.patch.object(shared_pin,'resolve',return_value=target_pin) as resolve, \
             mock.patch.object(sw,'_raw_agent',return_value=dict(prev)), \
             mock.patch.object(sw,'_saved',return_value=saved or {}), \
             mock.patch.object(sw,'gate',return_value={'ok':True,'version':'2.2.5'}), \
             mock.patch.object(sw,'_progress'),mock.patch.object(sw,'_phase'), \
             mock.patch.object(sw,'write_receipt',side_effect=lambda state,r:r), \
             mock.patch.object(op,'required',return_value=True),mock.patch.object(op,'run',side_effect=semantic), \
             mock.patch.object(subprocess,'run',side_effect=AssertionError('native forbidden')), \
             mock.patch.object(subprocess,'Popen',side_effect=AssertionError('native forbidden')):
            result=sw.worker(st,target,selected,dry_run=True,force=True,ctl=mock.Mock(),
                status=lambda:{'installed':True,'active':'active'},start=start)
        self.assertEqual(result,0)
        self.assertEqual(len(requests),1)
        self.assertEqual(seen[0]['kind'],target)
        self.assertEqual(prev['model'],'claude-sonnet-4-6')
        self.assertEqual(prev['shared_session_id'],'old-claude-pin')
        self.assertEqual(seen[0]['shared_session_id'],target_pin)
        resolve.assert_called_once_with(st,target,'owner-selection','/fake/work')
        for key in ('dir','fence','docker','session_mode','shared_opencode_port','shared_pin_owner'):
            self.assertEqual(seen[0][key],prev[key])
        return requests[0]['model']
    def test_explicit_target_model_is_used_for_start(self):
        self.assertEqual(self.run_case('opencode','opencode/glm-5.3-flash-free'),'opencode/glm-5.3-flash-free')
    def test_saved_target_model_is_used_for_start(self):
        self.assertEqual(self.run_case('opencode',None,{'opencode':{'model':'opencode/glm-5.3-flash-free'}}),'opencode/glm-5.3-flash-free')
    def test_no_target_model_does_not_inherit_previous_model(self):
        self.assertIsNone(self.run_case('opencode',None))
    def test_owner_port_and_target_pin_are_preserved(self):
        self.assertEqual(self.run_case('opencode','opencode/glm-5.3-flash-free',port=12345),'opencode/glm-5.3-flash-free')
    def test_other_target_uses_its_own_model(self):
        self.assertEqual(self.run_case('codex','gpt-6'),'gpt-6')
    def test_strict_opencode_model_schema_still_rejects_bare_foreign_model(self):
        with self.assertRaises(ValueError):
            op.spec({'op':'start','kind':'opencode','model':'claude-sonnet-4-6','timeout':25})
        op.spec({'op':'start','kind':'opencode','model':'opencode/glm-5.3-flash-free','timeout':25})
