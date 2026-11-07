"""Transcript task hints do not substitute for private native launch receipts."""
import _hermetic  # noqa: F401
import json, sys, unittest
from types import SimpleNamespace as NS
from test_shared import ClaudeTransport
from agentj import shared,provenance
from agentj.compose import Send
from unittest.mock import AsyncMock,Mock
import asyncio

class Carry(ClaudeTransport):
 async def check_task(self,foreign):
  a=shared.SharedClaudeAgent(self.host,self.cfg);await a.attach()
  self.host.agent=a;a.loop=asyncio.get_running_loop()
  self.host.turn_queued_note=Mock()
  # Real Shared.turn sets source/taint. Delivery receipt only is isolated;
  # fixture creates no model or external session.
  async def deliver(fn):
   rec={'type':'user','uuid':'qa-input-'+str(a.cur_send.turn),'isMeta':True,'origin':{'kind':'peer'},'message':{'content':provenance.envelope(str(self.root/'peer.sock'),a.cur_send.text,a.cur_send.source)}}
   with self.transcript.open('a') as f:f.write(json.dumps(rec)+'\n')
   a.read_transcript();a.input_seen.set()
  async def finish():
   await a.hook_event({'hook_event_name':'Stop','session_id':self.sid,'last_assistant_message':'〔不回群〕'})
   await asyncio.sleep(0)
  a.deliver=deliver;a._await_turn_end=finish
  a.cur_send=Send('fixture','mail-sid',0,text='visitor notice',source=provenance.email_guest('visitor@example.test'))
  await a.turn('visitor notice');a.cur_send=None
  a.cur_send=Send('fixture','phone-sid',1,text='owner continues previously authorised work',source=provenance.phone())
  await a.turn(a.cur_send.text);a.cur_send=None
  self.host.busy=NS(launched={f'shared-claude:{self.sid}:own-job':foreign})
  rec={'type':'user','uuid':'qa-known-job','turnOrigin':'task_notification','origin':{'kind':'task-notification'},'message':{'content':'<task-notification><task-id>own-job</task-id></task-notification>'}}
  with self.transcript.open('a') as f:f.write(json.dumps(rec)+'\n')
  a.read_transcript()
  return a
 async def test_board_boolean_alone_cannot_promote_task_to_owner_system(self):
  a=await self.check_task(False)
  self.assertIsNone(provenance.unattended_turn(a))
  self.assertTrue(a.untrusted_foreign)
 async def test_external_task_after_email_stays_blocked(self):
  a=await self.check_task(True)
  self.assertIsNone(provenance.unattended_turn(a))
  self.assertTrue(a.untrusted_foreign)

if __name__=='__main__':
 names=['test_board_boolean_alone_cannot_promote_task_to_owner_system','test_external_task_after_email_stays_blocked']
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(Carry(n) for n in names));sys.exit(not result.wasSuccessful())
