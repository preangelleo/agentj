"""0.26.8a2: serve restart + a shared Claude session that predates it + an owner-turn `agentj file show`.

Claude Code appends its transcript in ~100 ms batches, so its PermissionRequest can reach the host before the host-read
transcript holds the tool_use line. Admission still comes only from that host-read line in a seen owner turn."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import pathlib
import shlex
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HUMAN = '这个会话是升级前开的，请在本机对它 /clear 或开新会话。'

HARNESS = r'''
import json,sys,subprocess,pathlib,time
hook,path,transcript,command,wd,prompt,flush=sys.argv[1:]
sys.stdin.readline()
event={'session_id':'0268a2a2-0000-4000-8000-000000000001','cwd':wd,'tool_name':'Bash','tool_input':{'command':command},'tool_use_id':'toolu_card'}
def run(kind,**extra):
    return subprocess.Popen([sys.executable,hook,path,kind],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def emit(kind,ev):
    p=run(kind);out,_=p.communicate(json.dumps(dict(ev,hook_event_name=kind)));return json.loads(out or '{}')
def line(rec):
    with open(transcript,'a') as f: f.write(json.dumps(rec)+'\n')
if prompt!='-':
    emit('UserPromptSubmit',{'session_id':event['session_id'],'cwd':wd,'prompt':prompt})
    line({'type':'user','isMeta':True,'uuid':'u-owner','origin':{'kind':'peer'},'message':{'role':'user','content':prompt}})
p=run('PermissionRequest')
p.stdin.write(json.dumps(dict(event,hook_event_name='PermissionRequest')));p.stdin.close()
if flush!='never':
    time.sleep(float(flush))
    line({'type':'assistant','uuid':'a-card','cwd':wd,'message':{'id':'m-card','role':'assistant','content':[{'type':'tool_use','id':'toolu_card','name':'Bash','input':{'command':command}}]}})
answer=json.loads(p.stdout.read() or '{}');p.wait()
print(json.dumps(answer),flush=True)
if answer.get('hookSpecificOutput',{}).get('decision',{}).get('behavior')=='allow':
    r=subprocess.run(command,shell=True,capture_output=True,text=True,cwd=wd)
    print(json.dumps({'exit':r.returncode,'stdout':r.stdout}),flush=True)
    sys.stdin.readline()
    print(json.dumps(emit('PostToolUse',dict(event,tool_response={'stdout':r.stdout,'exitCode':r.returncode}))),flush=True)
'''


class RestartedServeOldSharedSession(unittest.IsolatedAsyncioTestCase):
    SID = '0268a2a2-0000-4000-8000-000000000001'

    async def asyncSetUp(self):
        from test_l1 import _state, _host, Phone
        from agentj import provenance, shared, shared_hook
        from agentj.peer_service import PeerService
        from agentj.compose import Send
        from test_inline_media_stream import PNG
        self.tmp = tempfile.TemporaryDirectory(prefix='p0268a2-card-'); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / 'home'; self.home.mkdir()
        self.work = self.home / 'work'; self.work.mkdir()
        (self.work / 'a.png').write_bytes(PNG)
        self.claude = self.root / 'claude'
        self.envpatch = patch.dict(os.environ, {'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                                                'CLAUDE_CONFIG_DIR': str(self.claude)})
        self.envpatch.start(); self.addCleanup(self.envpatch.stop)
        self.st = _state(self.tmp.name)
        with patch('agentj.names.ctl_call', return_value=None):
            self.st.set_agent_config('claude', str(self.work), fence=False)
        self.host = _host(self.st, []); self.host._post = lambda *a, **kw: None
        self.host.peers = PeerService(self.host)
        self.phone = Phone(self.st)
        self.page = self.host.hist_add({'k': 'you'}, '', 'open')
        self.send = Send(self.phone.did, 'show the file', self.page['id'], source=provenance.phone())
        cfg = {**self.host.agent_cfg, 'dir': str(self.work), 'session_mode': 'shared', 'fence': False,
               'shared_session_id': self.SID, 'shared_identity': False}
        # The pre-upgrade session's transcript: long history and a turn the old serve saw start but never end.
        self.transcript = shared.claude_transcript(str(self.work), self.SID)
        self.transcript.parent.mkdir(parents=True)
        old = [{'type': 'user', 'uuid': f'old-{i}', 'message': {'role': 'user', 'content': f'old {i}'}} for i in range(300)]
        old.append({'type': 'assistant', 'uuid': 'old-open', 'message': {'id': 'old-m', 'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'toolu_old', 'name': 'Bash', 'input': {'command': 'agentj update apply'}}]}})
        self.transcript.write_text(''.join(json.dumps(r) + '\n' for r in old))
        self.native = shared.SharedClaudeAgent(self.host, cfg)       # a fresh serve process: nothing carried over
        self.host.agent, self.host.agent_cfg = self.native, cfg
        self.native.loop = asyncio.get_running_loop()
        self.host.cur_turn = self.page['id']
        self.notices = []; self.host.agent_notice = self.notices.append
        await self.host.elevate.start(); self.addAsyncCleanup(self.host.elevate.stop)
        self.addCleanup(self.host.stopping.set)
        self.hookpath = self.st.root / 'claude-hook.sock'
        self.native.channel = shared_hook.Channel(self.native, self.hookpath)
        await self.native.channel.start(); self.addAsyncCleanup(self.native.channel.stop)
        self.messaging = socket.socket(socket.AF_UNIX); self.addCleanup(self.messaging.close)
        self.messaging.bind(str(self.root / 'native.sock'))

    async def owner_call(self, flush, prompt=True):
        from agentj import shared_hook, provenance
        command = 'agentj file show ~/work/a.png --label 图片 --json'
        binary = self.root / 'bin'; binary.mkdir(exist_ok=True)
        cli = binary / 'agentj'
        cli.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -m agentj.cli "$@"\n'); cli.chmod(0o700)
        env = {k: os.environ[k] for k in ('HOME', 'XDG_CONFIG_HOME', 'CLAUDE_CONFIG_DIR')}
        env.update(PATH=str(binary) + ':/usr/bin:/bin', AGENTJ_TEST='1', AGENTJ_SKILL_LINK='off', AGENTJ_HARNESS_PREPARE='off',
                   AGENTJ_STATE_DIR=str(self.st.root), AGENTJ_ELEVATE_SOCK=str(self.host.elevate.sock_path),
                   PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1]))
        text = '<cross-session-message from-name="' + provenance.PHONE + '">show the file</cross-session-message>'
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', HARNESS, str(pathlib.Path(shared_hook.__file__)),
            str(self.hookpath), str(self.transcript), command, str(self.work), text if prompt else '-', str(flush), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            registry = self.claude / 'sessions'; registry.mkdir(parents=True, exist_ok=True)
            (registry / 'live.json').write_text(json.dumps({'sessionId': self.SID, 'pid': proc.pid, 'cwd': str(self.work),
                'messagingSocketPath': str(self.root / 'native.sock'), 'status': 'idle'}))
            self.assertTrue(await self.native.attach(allow_start=False))
            self.assertEqual(self.native.offset, self.transcript.stat().st_size, 'restart attaches at the end, history unread')
            # The owner's message: the turn the host delivers (phone_seen comes only from the native prompt hook).
            self.native.cur_send = self.send
            self.native.phone_turn = True
            self.native.pending_text = 'show the file'
            self.native.pending_source = self.native.native_source = provenance.phone()
            self.host.peers.xfer.begin_execution(self.send)
            t0 = time.monotonic()
            proc.stdin.write(b'go\n'); await proc.stdin.drain()
            answer = json.loads(await asyncio.wait_for(proc.stdout.readline(), 15))
            took = time.monotonic() - t0
            decision = answer.get('hookSpecificOutput', {}).get('decision', {})
            if decision.get('behavior') != 'allow':
                await asyncio.wait_for(proc.communicate(), 8)
                return decision, took
            ran = json.loads(await asyncio.wait_for(proc.stdout.readline(), 15))
            self.assertEqual(ran['exit'], 0, ran)
            self.assertEqual(json.loads(ran['stdout'].splitlines()[-1])['result'], 'queued')
            proc.stdin.write(b'complete\n'); await proc.stdin.drain()
            _, err = await asyncio.wait_for(proc.communicate(), 15)
            self.assertEqual(proc.returncode, 0, err.decode())
            await asyncio.wait_for(self.host.peers.xfer.broker.drain(), 4)
            return decision, took
        finally:
            if proc.returncode is None:
                proc.kill(); await proc.wait()

    async def test_first_owner_message_after_restart_attaches_despite_late_transcript_flush(self):
        decision, _ = await self.owner_call(0.15)
        self.assertEqual(decision.get('behavior'), 'allow', decision)
        self.assertEqual(len(self.page['media']), 1)
        self.assertEqual(json.loads(self.notices[-1].removeprefix('FILE_SHOW_RESULT: '))['result'], 'attached_to_page')

    async def test_call_never_in_host_transcript_gets_one_human_sentence(self):
        decision, took = await self.owner_call('never')
        self.assertEqual(decision, {'behavior': 'deny', 'message': HUMAN})
        self.assertLess(took, 8)
        self.assertFalse(self.page.get('media'))

    async def test_no_owner_prompt_seen_is_refused_at_once_and_unchanged(self):
        decision, took = await self.owner_call(0.15, prompt=False)
        self.assertEqual(decision, {'behavior': 'deny', 'message': 'No live native secret-call admission'})
        self.assertLess(took, 1.9, 'no flush wait outside an owner turn')
        self.assertFalse(self.page.get('media'))


if __name__ == '__main__':
    unittest.main()
