"""Production _attach_once binds the selected transcript even with no new hook."""
import _hermetic
import asyncio
import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
from agentj import provenance
from agentj.serve import Host
from agentj.shared import SharedClaudeAgent
from agentj.state import State

class AttachBinding(unittest.IsolatedAsyncioTestCase):
    async def test_real_attach_binds_then_no_hook_completion_notifies_once(self):
        with tempfile.TemporaryDirectory(prefix='qa-r3-attach-') as td:
            root=Path(td); config=root/'claude'; sid='12345678-1234-1234-1234-123456789abc'
            owner='shared-claude:'+sid; key=owner+':native-job'
            transcript=config/'projects'/re.sub(r'[^A-Za-z0-9]','-',str(root))/(sid+'.jsonl')
            transcript.parent.mkdir(parents=True);transcript.write_text('')
            st=State(root/'state');st.init(relay='ws://127.0.0.1:1')
            def host():
                h=Host(st,events='jsonl',read_stdin=False)
                h.agent_cfg={'kind':'claude','dir':str(root),'session_mode':'shared','shared_session_id':sid}
                a=SharedClaudeAgent(h,h.agent_cfg);h.agent=a
                return h,a
            h,a=host();a.session={"sessionId":sid,"pid":0};a.native_source=provenance.phone()
            await a.hook_event({'hook_event_name':'PostToolUse','session_id':sid,'transcript_path':str(transcript),
                'tool_name':'Bash','tool_input':{'description':'QA owner task'},'tool_response':{'backgroundTaskId':'native-job'}})
            original=h.busy.native_starts[key].copy()
            # New Host; no hook and no direct map assignment after restart.
            h,a=host(); self.assertFalse(h.busy.claude_transcripts);self.assertFalse(h.busy.mem)
            session={'sessionId':sid,'pid':0,'status':'idle','startedAt':0}
            with mock.patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(config)}), \
                 mock.patch('agentj.shared.claude_sessions',return_value=[session]), \
                 mock.patch.object(a,'prepare_channel',new=mock.AsyncMock()), \
                 mock.patch.object(a,'issue_hook_token'), \
                 mock.patch.object(a,'refresh_models',new=mock.AsyncMock()):
                self.assertTrue(await a._attach_once(allow_start=False))
            if a._native_scan is not None:await a._native_scan
            self.assertEqual(h.busy.claude_transcripts,{owner:str(transcript)})
            self.assertFalse(h.busy.mem)
            transcript.write_text(json.dumps({'type':'user','origin':{'kind':'task-notification','producer':'session-task'},
                'turnOrigin':'task_notification','timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'message':{'content':'<task-notification><task-id>native-job</task-id><status>completed</status><summary>QA owner task</summary></task-notification>'}})+'\n')
            sends=[]
            def deliver(send):
                sends.append(send);send.state='delivered';h.background_done.complete(send)
            a.submit=deliver;a.is_down=lambda:False
            h.busy.refresh_outside=lambda *args:None;h.busy.refresh_sources=lambda:None;h.busy.sample_load=lambda:None
            h.theme_refresh=lambda:False;h.post_q=asyncio.Queue()
            loop=asyncio.create_task(h.busy_loop())
            try:
                async with asyncio.timeout(3):
                    while not sends:await asyncio.sleep(.02)
            finally:
                loop.cancel()
                with contextlib.suppress(asyncio.CancelledError):await loop
            self.assertEqual(len(sends),1);self.assertEqual(sends[0].source['kind'],'system')
            self.assertFalse(hasattr(sends[0],'owner_chain'))
            self.assertEqual(h.busy.native_starts[key],original)
            self.assertIn('owner task',sends[0].text)
if __name__=='__main__':unittest.main(verbosity=2)
