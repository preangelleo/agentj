"""F14: caller-requested upgrade and native authority; all I/O hermetic."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch, AsyncMock
from agentj import update, fence, main_identity, shared, preferences
from agentj.agent import ClaudeAgent
from agentj.agent_codex import CodexAgent
from agentj.state import State

class Autonomy(unittest.TestCase):
    def test_noninteractive_upgrade_restart_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            st=State(Path(tmp)/'state');st.init()
            calls=[]
            def run(argv, **kw):
                calls.append((argv,kw)); self.assertEqual(kw['stdin'],subprocess.DEVNULL)
                return subprocess.CompletedProcess(argv,0,'agentj 9.9.9' if argv[-1]=='--version' else '', '')
            with patch.object(update,'install_kind',return_value={'kind':'uv','where':tmp,'legacy':False}), patch.object(update,'new_argv',return_value=['agentj']), patch.object(update,'writable',return_value=True):
                res=update.apply(st,'9.9.9',check_fn=lambda:{'status':'newer','latest':'9.9.9'},run=run,svc_on=True)
            self.assertEqual((res['result'],res['service']),('ok','restarted'))
            self.assertEqual([a[-1] for a,_ in calls][-3:],['install','restart','doctor'])
            self.assertIn('@v9.9.9',calls[0][0][-1])
            self.assertEqual(update.take_marker(st)['to'],'9.9.9')
    def test_version_mismatch_does_not_restart(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0,'agentj 1.0.0',''))
        with patch.object(update,'writable',return_value=True),patch.object(update,'install_kind',return_value={'kind':'uv','where':'/fake','legacy':False}),patch.object(update,'new_argv',return_value=['agentj']):
            res=update.apply(Mock(),'9.9.9',check_fn=lambda:{'status':'newer','latest':'9.9.9'},run=run,svc_on=True)
        self.assertEqual(res['reason'],'not_upgraded');self.assertEqual(run.call_count,2)
    def test_native_policies_no_extra_risk_approval(self):
        a=ClaudeAgent(None,{'kind':'claude','dir':'/tmp','_workflow_ceo':True})
        argv=a.argv(None);self.assertEqual(json.loads(argv[argv.index('--settings')+1]),{})
        self.assertEqual(CodexAgent(None,{'kind':'codex','dir':'/tmp'}).policy(),{})
    def test_self_code_writable_state_still_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            st=State(Path(tmp)/'state');st.init()
            argv=fence.bwrap_argv(st,tmp)
            ro={argv[i+1] for i,x in enumerate(argv) if x=='--ro-bind'}
            self.assertFalse(set(fence.code_paths()) & ro)
            self.assertIn(str(st.root),fence.protected_paths(st))
            self.assertFalse(set(fence.code_paths()) & set(fence.protected_paths(st)))
    def test_defaults_and_identity(self):
        self.assertFalse(preferences.get(preferences.defaults(),'agent.high_risk_warnings'))
        self.assertEqual(preferences.get(preferences.defaults(),'agent.session_mode'),'shared')
        self.assertEqual(main_identity.verify_core()['version'],5)
        self.assertIn('without extra approvals',main_identity.prompt({'language':'en'}))
    def test_high_risk_shared_call_adds_no_decision(self):
        host=Mock();host.stopped.return_value=False
        a=shared.SharedClaudeAgent(host,{'kind':'claude','dir':'/tmp','_workflow_ceo':True})
        a.session={'sessionId':'owner'}
        out=asyncio.run(a.hook_event({'session_id':'owner','hook_event_name':'PreToolUse','tool_name':'Bash','tool_input':{'command':'git push origin main'}}))
        self.assertEqual(out,{});host.ask.assert_not_called()
