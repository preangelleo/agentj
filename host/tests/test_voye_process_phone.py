"""Independent OS process + local paired-phone ingress regression; no customer or service."""
import _hermetic  # noqa: F401
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import unittest

from agentj import provenance, shared_hook


# This child is the Claude-like process, not a mocked host process: it owns the
# peer UDS, receives the Host's authenticated frame, writes its own transcript,
# invokes the real hook and CLI, then reports native completion.
CHILD = r'''
import json,os,pathlib,socket,subprocess,sys
sock,transcript,hook,hook_sock,command,work,mode=sys.argv[1:]
s=socket.socket(socket.AF_UNIX);s.bind(sock);s.listen(1)
print('ready',flush=True)
if mode=='phone':
 c,_=s.accept(); f=c.makefile(); auth=json.loads(f.readline()); frame=json.loads(f.readline())
 assert auth=={'type':'auth','token':'synthetic-peer-token'} and frame['type']=='user'
 text=frame['message']['content']
 row={'type':'user','isMeta':True,'origin':{'kind':'peer','name':'Agent J Phone'},'message':{'content':text}}
else:
 row={'type':'user','isMeta':True,'origin':{'name':'Agent J Phone'},'message':{'content':'show the file'}}
pathlib.Path(transcript).write_text(json.dumps(row)+'\n');print('delivered',flush=True)
sys.stdin.readline()
block={'type':'tool_use','id':'voye-call','name':'Bash','input':{'command':command}}
with open(transcript,'a') as out: out.write(json.dumps({'type':'assistant','cwd':work,'message':{'id':'voye-msg','role':'assistant','content':[block]}})+'\n')
event={'session_id':os.environ['TEST_SESSION'],'cwd':work,'tool_name':'Bash','tool_input':{'command':command},'tool_use_id':'voye-call'}
def emit(kind,**extra):
 p=subprocess.run([sys.executable,hook,hook_sock,kind],input=json.dumps(dict(event,hook_event_name=kind,**extra)),capture_output=True,text=True,timeout=8)
 assert p.returncode==0,(p.stderr,p.stdout)
 # A native-allowed PreToolUse emits no output: this is not a grant.
 return json.loads(p.stdout) if p.stdout.strip() else {}
a=emit('PreToolUse');decision=a.get('hookSpecificOutput',{}).get('permissionDecision')
print(json.dumps({'decision':decision}),flush=True)
if decision!='deny':
 p=subprocess.run(command,shell=True,capture_output=True,text=True,cwd=work,timeout=8)
 print(json.dumps({'exit':p.returncode,'stdout':p.stdout}),flush=True)
 sys.stdin.readline()
 print(json.dumps(emit('PostToolUse',tool_response={'stdout':p.stdout,'exitCode':p.returncode})),flush=True)
s.close()
'''


class RealProcessPhone(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_shared_file_show import NativeFlows
        await NativeFlows.asyncSetUp(self)

    async def exercise(self, mode, session='voye-session', stale=False):
        # All writable child state, registry, peer keys and the generated command
        # are confined to the per-test fake HOME/state created in asyncSetUp.
        self.native.phone_seen = False
        peer = self.root / 'peer.sock'
        transcript = self.root / 'real-transcript.jsonl'
        self.native.transcript = transcript
        hook_sock = self.st.root / 'claude-hook.sock'
        channel = shared_hook.Channel(self.native, hook_sock)
        await channel.start()
        self.addAsyncCleanup(channel.stop)
        # The CLI is invoked in the child, not from the test runner.
        binary = self.root / 'bin'; binary.mkdir(exist_ok=True)
        cli = binary / 'agentj'
        cli.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -m agentj.cli "$@"\n')
        cli.chmod(0o700)
        command = 'agentj file show ' + shlex.quote(str(self.image)) + ' --json'
        env = dict(os.environ)
        env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.root / 'config'),
                   XDG_CACHE_HOME=str(self.root / 'cache'), XDG_DATA_HOME=str(self.root / 'data'),
                   CLAUDE_CONFIG_DIR=str(self.root / 'claude'), AGENTJ_STATE_DIR=str(self.st.root),
                   AGENTJ_ELEVATE_SOCK=str(self.host.elevate.sock_path), AGENTJ_TEST='1',
                   AGENTJ_SKILL_LINK='off', AGENTJ_HARNESS_PREPARE='off',
                   PYTHONDONTWRITEBYTECODE='1', TEST_SESSION=session,
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                   PATH=str(binary) + ':/usr/bin:/bin')
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', CHILD, str(peer), str(transcript),
            str(Path(shared_hook.__file__)), str(hook_sock), command, str(self.work), mode,
            env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            self.assertEqual(await asyncio.wait_for(proc.stdout.readline(), 8), b'ready\n')
            self.native.session = {'sessionId': 'voye-session', 'pid': proc.pid, 'cwd': str(self.work),
                                   'messagingSocketPath': str(peer), 'status': 'working'}
            registry = self.root / 'claude' / 'sessions'; registry.mkdir(parents=True, exist_ok=True)
            (registry / 'owner.json').write_text(json.dumps(self.native.session))
            key = registry / f'{proc.pid}.{hashlib.sha256(str(peer).encode()).hexdigest()}.key'
            key.write_text(json.dumps({'peerToken': 'synthetic-peer-token'})); key.chmod(0o600)
            self.native.native_request = ('voye-session', self.send, 'show the file')
            self.native.pending_text = 'show the file'
            self.native.pending_source = self.native.native_source = provenance.phone()
            if mode == 'phone':
                await asyncio.wait_for(asyncio.to_thread(__import__('agentj.shared', fromlist=['claude_send']).claude_send,
                    self.native.session, 'show the file', provenance.phone()), 8)
            self.assertEqual(await asyncio.wait_for(proc.stdout.readline(), 8), b'delivered\n')
            self.native.read_transcript()
            if mode == 'phone':
                self.assertTrue(self.native.phone_seen, 'real Host peer delivery must be seen')
            else:
                self.assertFalse(self.native.phone_seen, 'display label cannot supply phone provenance')
            self.native.issue_hook_token()
            if stale:
                self.host.peers.xfer.end_execution()
            proc.stdin.write(b'run\n'); await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), 8)
            if not line:
                err = await proc.stderr.read()
                self.fail('child exited before hook decision: ' + err.decode()[:1200])
            decision = json.loads(line)['decision']
            if decision != 'deny':
                result = json.loads(await asyncio.wait_for(proc.stdout.readline(), 8))
                receipt = json.loads(result['stdout'].splitlines()[-1])
                if session != 'voye-session' or stale:
                    self.assertEqual(result['exit'], 1)
                    self.assertEqual(receipt['why'], 'native_admission_missing')
                else:
                    self.assertEqual(result['exit'], 0, result)
                    self.assertEqual(receipt['result'], 'queued')
                self.assertFalse(self.page.get('media'))
                proc.stdin.write(b'complete\n'); await proc.stdin.drain()
                await asyncio.wait_for(proc.stdout.readline(), 8)
                await asyncio.wait_for(self.host.peers.xfer.broker.drain(), 4)
            _, err = await asyncio.wait_for(proc.communicate(), 8)
            self.assertEqual(proc.returncode, 0, err.decode()[:300])
            return decision
        finally:
            if proc.returncode is None:
                proc.kill(); await proc.wait()

    async def test_real_peer_process_attaches_only_same_session(self):
        self.assertNotEqual(await self.exercise('phone'), 'deny')
        self.assertEqual(len(self.page.get('media', [])), 1)

    async def test_fake_phone_label_denied(self):
        self.assertEqual(await self.exercise('label'), 'deny')
        self.assertFalse(self.page.get('media'))

    async def test_stale_owner_execution_denied(self):
        self.assertEqual(await self.exercise('phone', stale=True), 'deny')
        self.assertFalse(self.page.get('media'))

    async def test_other_process_session_denied(self):
        self.assertNotEqual(await self.exercise('phone', session='forked-session'), 'deny')
        # Native Bash may run without a hook decision; the broker must still refuse
        # its CLI receipt in the foreign session (checked inside exercise).
        self.assertFalse(self.page.get('media'))
