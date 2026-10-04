"""Same-thread ownership, original profile and desktop mirror regressions."""
import _hermetic
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentj.shared_codex import owner_context,SharedCodexAgent
SID='11111111-1111-1111-1111-111111111111'
PROFILE={'type':'managed','file_system':{'type':'restricted','entries':[{'path':{'type':'special','value':{'kind':'root'}},'access':'read'}]},'network':'restricted'}
class OriginalThread(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='/var/tmp');self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.path=self.root/'owner.jsonl'
  self.ctx={'cwd':str(self.root),'approval_policy':'on-request','approvals_reviewer':'auto_review','sandbox_policy':{'type':'read-only'},'permission_profile':PROFILE,'active_permission_profile':{'id':':read-only'}}
  self.write([{'type':'session_meta','payload':{'id':SID,'cwd':str(self.root)}},{'type':'turn_context','payload':self.ctx}])
 def write(self,items):self.path.write_text(''.join(json.dumps(i)+'\n' for i in items))
 def test_preserves_exact_verified_profile_not_config_defaults(self):
  params,_=owner_context(self.path,str(self.root),SID)
  self.assertEqual(params,{'approvalPolicy':'on-request','approvalsReviewer':'auto_review','permissions':':read-only'})
 def test_custom_or_changed_profile_fails_closed(self):
  for profile,ident in [(PROFILE,'custom'),({**PROFILE,'network':'enabled'},':read-only')]:
   ctx={**self.ctx,'permission_profile':profile,'active_permission_profile':{'id':ident}}
   self.write([{'type':'session_meta','payload':{'id':SID,'cwd':str(self.root)}},{'type':'turn_context','payload':ctx}])
   with self.assertRaises(ValueError):owner_context(self.path,str(self.root),SID)
 def test_active_owner_turn_and_wrong_project_refused(self):
  with self.path.open('a') as f:f.write(json.dumps({'type':'event_msg','payload':{'type':'task_started'}})+'\n')
  with self.assertRaises(ValueError):owner_context(self.path,str(self.root),SID)
  with self.assertRaises(ValueError):owner_context(self.path,'/other',SID)
 def test_desktop_mirror_skips_tool_outputs_and_phone_duplicates(self):
  host=Mock();a=SharedCodexAgent(host,{'kind':'codex','dir':str(self.root),'_workflow_ceo':True,'shared_session_id':SID})
  with patch('agentj.shared_codex.selected_rollout',return_value=self.path):a.read_desktop()
  records=[{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'keyboard'}]}}, {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'reply'}]}}, {'type':'response_item','payload':{'type':'function_call_output','output':'never mirror'}}]
  with self.path.open('a') as f:f.write(''.join(json.dumps(r)+'\n' for r in records))
  a.read_desktop();a.read_desktop()
  host.desktop_input.assert_called_once_with('keyboard');host.desktop_text.assert_called_once_with('reply')
  a.phone_turn=True
  with self.path.open('a') as f:f.write(json.dumps(records[1])+'\n')
  a.read_desktop();self.assertEqual(host.desktop_text.call_count,1)

from unittest.mock import AsyncMock
class AlternatingActors(unittest.IsolatedAsyncioTestCase):
 async def test_releases_only_owned_actor_after_phone_turn(self):
  host=Mock();a=SharedCodexAgent(host,{'kind':'codex','dir':'/var/tmp','_workflow_ceo':True,'shared_session_id':SID})
  owned=Mock();a.proc=owned;a.original={};a.tid=SID
  a._ready=AsyncMock(return_value=True);a.read_desktop=Mock();a._kill=AsyncMock()
  async def deliver(fn):
   a.turn_done.set();return {'turn':{'id':'test'}}
  a.deliver=AsyncMock(side_effect=deliver)
  await a.turn('phone');a._kill.assert_awaited_once_with(owned)
  self.assertFalse(a.phone_turn);a.read_desktop.assert_called_once()

class HookTrust(unittest.IsolatedAsyncioTestCase):
 async def test_only_host_command_hash_trusted_before_first_resume(self):
  from agentj import shared_hook
  host=Mock();cfg={'kind':'codex','dir':'/var/tmp','_workflow_ceo':True,'shared_session_id':SID}
  a=SharedCodexAgent(host,cfg);a.risk_channel=Mock(path=Path('/var/tmp/host.sock'))
  cmd=shared_hook.settings(a.risk_channel.path,('PreToolUse',))['hooks']['PreToolUse'][0]['hooks'][0]['command']
  own={'command':cmd,'handlerType':'command','enabled':True,'currentHash':'HASH','key':'OWN','eventName':'preToolUse'}
  foreign={**own,'command':'owner-unreviewed-hook','key':'FOREIGN'}
  seen=[]
  async def call(method,params):
   seen.append((method,params))
   if method=='hooks/list':return {'data':[{'cwd':'/var/tmp','hooks':[own,foreign]}]}
   return {'thread':{'id':SID},'approvalPolicy':'never','approvalsReviewer':'user','sandbox':{'type':'dangerFullAccess'}}
  a.call=AsyncMock(side_effect=call)
  original={'approvalPolicy':'never','approvalsReviewer':'user','sandboxPolicy':{'type':'dangerFullAccess'}}
  with patch('agentj.shared_codex.selected_rollout'),patch('agentj.shared_codex.owner_context',return_value=(original,{})):
   self.assertTrue(await a._thread())
  self.assertEqual([m for m,_ in seen],['hooks/list','thread/resume'])
  params=seen[1][1];self.assertEqual(params['config'],{'hooks.state':{'OWN':{'trusted_hash':'HASH'}}})
  self.assertEqual(params['sandbox'],'danger-full-access');self.assertEqual(params['approvalPolicy'],'never')
