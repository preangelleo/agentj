"""Real Host/SharedClaude hook path across two host processes; no model or relay calls.

The child is the production serve.Host plus busy_loop. JSON stdin replaces only
Claude hook transport and stdout records adapter submissions (no TUI injection).
A real native shell child survives host termination; its native completion record
is read from the same isolated transcript after the second host starts.
"""
import _hermetic  # noqa: F401
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

WORKER = r'''
import asyncio, contextlib, json, os, sys
from pathlib import Path
from agentj import provenance
from agentj.state import State
from agentj.serve import Host
from agentj.shared import SharedClaudeAgent
async def main():
    root = Path(sys.argv[1]); st = State(root/'state')
    if not st.config_path.exists(): st.init(relay='ws://127.0.0.1:1')
    h = Host(st, events='jsonl', read_stdin=False)
    h.agent_cfg = {'kind':'claude','dir':str(root),'session_mode':'shared'}
    a = SharedClaudeAgent(h, h.agent_cfg); h.agent = a
    a.session = {'sessionId':'fixture-session','pid':int(sys.argv[2])}
    a.is_down = lambda: False
    sends = []
    a.submit = sends.append
    # Outside systemd jobs are unrelated to this isolated native test.
    refresh = h.busy.refresh_outside
    h.busy.refresh_outside = lambda *_: refresh(units=False)
    h.post_q = asyncio.Queue()
    task = asyncio.create_task(h.busy_loop())
    print(json.dumps({'ready':True,'pid':os.getpid(),'trusted':h.busy.trusted()}), flush=True)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            cmd = json.loads(line); op=cmd['op']
            if op == 'source':
                a.native_source = provenance.phone() if cmd['owner'] else provenance.normalize(None)
            elif op == 'hook':
                await a.hook_event(cmd['event'])
            elif op == 'finish':
                await asyncio.to_thread(h.busy.reconcile_claude, None)
                a.status='idle'; a.native_turn=False; a.bg_live.clear()
                await h.background_done.prepare()
                h.background_done.tick()
            key='shared-claude:fixture-session:native-job'
            print(json.dumps({'trusted':h.busy.trusted(),'item':h.busy.mem.get(key),
                  'sends':[{'source':s.source,'text':provenance.body(s.text,s.source),
                            'chain':hasattr(s,'owner_chain')} for s in sends]}), flush=True)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError): await task
asyncio.run(main())
'''


class NativeHostProcessRestart(unittest.TestCase):
    def test_owner_launch_survives_restart_in_unknown_turn(self):
        self.run_restart(True)

    def test_external_launch_remains_unknown_after_restart(self):
        self.run_restart(False)

    def run_restart(self, owner):
        with tempfile.TemporaryDirectory(prefix='busy-r2-') as td:
            root=Path(td); transcript=root/'native.jsonl'; transcript.write_text('')
            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                     AGENTJ_STATE_DIR=str(root/'state'), XDG_CONFIG_HOME=str(root/'config'),
                     CLAUDE_CONFIG_DIR=str(root/'claude'), HOME=str(root))
            release=root/'release'
            shell=subprocess.Popen([sys.executable,'-c',
                'import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); end=time.monotonic()+30\n'
                'while not p.exists() and time.monotonic()<end: time.sleep(.02)\n'
                'sys.exit(0 if p.exists() else 9)',str(release)])
            self.addCleanup(lambda: shell.poll() is None and (shell.kill(),shell.wait()))
            def start():
                p=subprocess.Popen([sys.executable,'-u','-c',WORKER,str(root),str(shell.pid)],env=env,
                                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                def cleanup():
                    if p.poll() is None: p.kill();p.wait()
                    for stream in (p.stdin,p.stdout,p.stderr): stream.close()
                self.addCleanup(cleanup)
                line=p.stdout.readline()
                if not line: self.fail(p.stderr.read())
                self.assertTrue(json.loads(line)['ready'])
                return p
            def send(p,**cmd):
                p.stdin.write(json.dumps(cmd)+'\n');p.stdin.flush(); line=p.stdout.readline()
                if not line: self.fail(p.stderr.read())
                return json.loads(line)
            p=start()
            send(p,op='source',owner=owner)
            initial=send(p,op='hook',event={'hook_event_name':'PostToolUse','session_id':'fixture-session',
                  'transcript_path':str(transcript),'tool_name':'Bash',
                  'tool_input':{'description':'Native background owner turn'},'tool_response':{'backgroundTaskId':'native-job'}})
            key='shared-claude:fixture-session:native-job'
            launched=json.loads((root/'state/busy/native-launches.json').read_text())
            self.assertIs(launched[key],not owner)
            since=initial['item']['since']; generation=initial['item']['generation']
            # Ensure a timestamp reset is visible even on a fast machine.
            time.sleep(1.05)
            first_pid=p.pid
            p.terminate();p.wait(timeout=10)
            self.assertIsNone(shell.poll(), "native shell must survive host shutdown")
            p=start()  # no owner input has occurred in this host process
            self.assertNotEqual(first_pid,p.pid)
            self.assertIsNone(shell.poll(), "native shell must survive second host startup")
            restored=send(p,op='hook',event={'hook_event_name':'Stop','session_id':'fixture-session',
                 'transcript_path':str(transcript),'background_tasks':[{'id':'native-job','type':'shell',
                    'description':'Native background owner turn','status':'running'}]})
            self.assertIsNone(shell.poll(), "native shell must still run at recovery")
            release.touch()
            self.assertEqual(shell.wait(timeout=10),0)
            transcript.write_text(json.dumps({'type':'user','origin':{'kind':'task-notification'},
                 'message':{'content':'<task-notification><task-id>native-job</task-id><status>completed</status></task-notification>'}})+'\n')
            result=send(p,op='finish')
            self.assertTrue(result['sends'],result)
            actual=result['sends'][-1]
            self.assertEqual(actual['source']['kind'],'system' if owner else 'unknown')
            self.assertFalse(actual['chain'])
            print(json.dumps({'control':'owner' if owner else 'external','host_pids':[first_pid,p.pid],
                              'native_pid':shell.pid,'source':actual['source']['kind'],
                              'since_before':since,'since_after':restored['item']['since'],
                              'generation_preserved':generation==restored['item']['generation'],
                              'owner_label_preserved':'owner turn' in actual['text']}),flush=True)
            self.assertEqual(restored['item']['since'],since)
            self.assertEqual(restored['item']['generation'],generation)
            if owner:
                self.assertIn('owner turn',actual['text'])
                self.assertNotIn('冒充字样',actual['text'])
            else:
                self.assertIn('冒充字样',actual['text'])
            p.terminate();p.wait(timeout=10)


if __name__ == '__main__': unittest.main()
