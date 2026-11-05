"""No post-restart running Stop: production busy loop detects native completion.

Hook transport, attachment's exact transcript binding, and native adapter writes
are fixtures. No fake completion/ready calls or background-state clearing.
"""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agentj import provenance
from agentj.serve import Host
from agentj.shared import SharedClaudeAgent
from agentj.state import State

SID = 'fixture-session'
OWNER = 'shared-claude:' + SID
KEY = OWNER + ':native-job'


class RestartWithoutStop(unittest.IsolatedAsyncioTestCase):
    async def test_owner_completion_without_recovery_stop_once_across_restarts(self):
        await self.run_case(True)

    async def test_external_completion_stays_unknown(self):
        await self.run_case(False)

    async def run_case(self, owner):
        with tempfile.TemporaryDirectory(prefix='busy-r3-') as td:
            root = Path(td); st = State(root/'state'); st.init(relay='ws://127.0.0.1:1')
            transcript = root/'native.jsonl'; transcript.write_text('')
            def host():
                h=Host(st, events='jsonl', read_stdin=False)
                h.agent_cfg={'kind':'claude','dir':str(root),'session_mode':'shared'}
                a=SharedClaudeAgent(h,h.agent_cfg); h.agent=a
                a.session={'sessionId':SID,'pid':0}; a.is_down=lambda:False
                h.busy.refresh_outside=lambda *a:None
                h.busy.refresh_sources=lambda:None
                h.busy.sample_load=lambda:None
                h.theme_refresh=lambda:False
                h.post_q=asyncio.Queue()
                # Stand-in only for attach's exact served-session transcript binding.
                h.busy.claude_transcripts[OWNER]=str(transcript)
                return h,a
            h,a=host();a.native_source=provenance.phone() if owner else provenance.normalize(None)
            await a.hook_event({'hook_event_name':'PostToolUse','session_id':SID,
                'transcript_path':str(transcript),'tool_name':'Bash',
                'tool_input':{'description':'Native background owner turn'},
                'tool_response':{'backgroundTaskId':'native-job'}})
            original=dict(h.busy.mem[KEY])
            self.assertIs(h.busy.launched[KEY],not owner)
            h,a=host()  # no Stop, no running hook, no owner input
            self.assertFalse(h.busy.mem)
            sends=[]
            def deliver(send):
                sends.append(send);send.state='delivered';h.background_done.complete(send)
            a.submit=deliver
            event={'type':'user','origin':{'kind':'task-notification','producer':'session-task'},
                   'turnOrigin':'task_notification','timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'message':{'content':'<task-notification><task-id>native-job</task-id><status>completed</status>'
                              '<summary>Native background owner turn</summary></task-notification>'}}
            transcript.write_text(json.dumps(event)+'\n')  # completes before first restarted scan
            loop=asyncio.create_task(h.busy_loop())
            try:
                async with asyncio.timeout(3):
                    while not sends: await asyncio.sleep(.02)
            finally:
                loop.cancel()
                with contextlib.suppress(asyncio.CancelledError):await loop
            self.assertEqual(len(sends),1)
            self.assertEqual(sends[0].source['kind'],'system' if owner else 'unknown')
            self.assertFalse(hasattr(sends[0],'owner_chain'))
            if owner:self.assertIn('owner turn',sends[0].text)
            seen=json.loads((st.root/'busy/completion.json').read_text())['seen']
            self.assertEqual(len(seen),1)
            for _ in range(2):
                await asyncio.to_thread(h.busy.reconcile_claude,None)
                await h.background_done.prepare();h.background_done.tick(now=10**12)
            self.assertEqual(len(sends),1)
            h,a=host();a.submit=sends.append
            await asyncio.to_thread(h.busy.reconcile_claude,None)
            await h.background_done.prepare();h.background_done.tick(now=10**12)
            self.assertEqual(len(sends),1)
            self.assertEqual(h.busy.native_starts[KEY]['generation'],original['generation'])
            self.assertEqual(h.busy.native_starts[KEY]['since'],original['since'])


if __name__=='__main__':unittest.main()


class NativeProcessWithoutStop(unittest.TestCase):
    def test_real_native_child_survives_host_pid_change_and_notifies_once(self):
        import os
        import subprocess
        import sys
        import time
        from test_busy_source_restart_r2 import WORKER
        worker = WORKER.replace("sends = []", "h.busy.claude_transcripts['shared-claude:fixture-session'] = str(root/'native.jsonl')\n    sends = []\n    ticks=[0]\n    original_tick=h.background_done.tick\n    def observed_tick(*args, **kwargs):\n        result=original_tick(*args, **kwargs); ticks[0]+=1; return result\n    h.background_done.tick=observed_tick")
        worker = worker.replace("{'trusted':h.busy.trusted(),'item'", "{'ticks':ticks[0],'trusted':h.busy.trusted(),'item'")
        start = worker.index("            elif op == 'finish':")
        end = worker.index("            key=", start)
        worker = worker[:start] + "            elif op == 'ack':\n                for send in sends:\n                    send.state='delivered'; h.background_done.complete(send)\n" + worker[end:]
        with tempfile.TemporaryDirectory(prefix='busy-r3-process-') as td:
            root=Path(td); transcript=root/'native.jsonl';transcript.write_text('')
            release=root/'release'
            child=subprocess.Popen([sys.executable,'-c',
                'import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); end=time.monotonic()+40\n'
                'while not p.exists() and time.monotonic()<end:time.sleep(.02)\n'
                'sys.exit(0 if p.exists() else 9)',str(release)])
            self.addCleanup(lambda:child.poll() is None and (child.kill(),child.wait()))
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]),HOME=td,
                     AGENTJ_STATE_DIR=str(root/'state'),XDG_CONFIG_HOME=str(root/'config'),CLAUDE_CONFIG_DIR=str(root/'claude'))
            def launch():
                p=subprocess.Popen([sys.executable,'-u','-c',worker,td,str(child.pid)],env=env,
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                def cleanup():
                    if p.poll() is None:p.kill();p.wait()
                    for s in (p.stdin,p.stdout,p.stderr):s.close()
                self.addCleanup(cleanup)
                self.assertTrue(json.loads(p.stdout.readline())['ready'])
                return p
            def send(p,**cmd):
                p.stdin.write(json.dumps(cmd)+'\n');p.stdin.flush()
                return json.loads(p.stdout.readline())
            p=launch();first=p.pid
            send(p,op='source',owner=True)
            initial=send(p,op='hook',event={'hook_event_name':'PostToolUse','session_id':SID,
                'transcript_path':str(transcript),'tool_name':'Bash',
                'tool_input':{'description':'Native background owner turn'},
                'tool_response':{'backgroundTaskId':'native-job'}})
            p.terminate();p.wait(timeout=10)
            self.assertIsNone(child.poll())
            p=launch();second=p.pid;self.assertNotEqual(first,second)
            self.assertIsNone(child.poll())
            self.assertIsNone(send(p,op='poll')['item'])  # no running Stop on the restarted host
            release.touch();self.assertEqual(child.wait(timeout=10),0)
            transcript.write_text(json.dumps({'type':'user','origin':{'kind':'task-notification'},
                'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'message':{'content':'<task-notification><task-id>native-job</task-id><status>completed</status>'
                           '<summary>Native background owner turn</summary></task-notification>'}})+'\n')
            deadline=time.monotonic()+15
            result=send(p,op='poll')
            while not result['sends'] and time.monotonic()<deadline:
                time.sleep(.1);result=send(p,op='poll')
            self.assertEqual(len(result['sends']),1,result)
            self.assertEqual(result['sends'][0]['source']['kind'],'system')
            self.assertFalse(result['sends'][0]['chain'])
            self.assertIn('owner turn',result['sends'][0]['text'])
            send(p,op='ack');p.terminate();p.wait(timeout=10)
            p=launch();third=p.pid;result=send(p,op='poll')
            deadline=time.monotonic()+5
            while result['ticks'] < 1 and time.monotonic()<deadline:
                time.sleep(.02);result=send(p,op='poll')
            self.assertGreaterEqual(result['ticks'],1)
            self.assertEqual(result['sends'],[])
            print(json.dumps({'host_pids':[first,second,third],'native_pid':child.pid,
                'source':'system','sends':1,'dedup_after_restart':True}),flush=True)
            p.terminate();p.wait(timeout=10)
