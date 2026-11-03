import _hermetic  # noqa: F401
import json
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from agentj import file_show, owner_broker


class RequestBinding(unittest.TestCase):
    def test_shell_tilde_and_absolute_are_same_bounded_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'HOME': '/tmp/synthetic-home'}):
            command = 'agentj file show ~/Downloads/a.png --json'
            native = owner_broker.parse('Bash', {'command': command}, tmp)
            argv_path = subprocess.check_output(['/bin/sh', '-c', 'printf %s ~/Downloads/a.png'], text=True)
            cli = file_show.request(argv_path, cwd=tmp)
            self.assertEqual(native, cli)
            broker = owner_broker.Broker(SimpleNamespace(execution=SimpleNamespace(live=True, send=object())))
            broker.calls['call'] = {'request': native, 'admitted': True, 'check': lambda: True, 'ticket': 'test'}
            self.assertEqual(broker.receipt(cli)['result'], 'queued')

    def test_absolute_control(self):
        req = file_show.request('/tmp/a.png', cwd='/tmp')
        self.assertEqual(req, owner_broker.parse('Bash', {'command': 'agentj file show /tmp/a.png --json'}, '/tmp'))

    def test_relative_and_quoted_tilde_follow_existing_file_semantics(self):
        with patch.dict(os.environ, {'HOME': '/tmp/synthetic-home'}):
            for path in ('./a.png', '~/Downloads/a.png'):
                native = owner_broker.parse('Bash', {'command': 'agentj file show ' + shlex.quote(path)}, '/tmp')
                self.assertEqual(native, file_show.request(path, cwd='/tmp'))

    def test_socket_failure_has_fixed_transport_reason(self):
        args = SimpleNamespace(path='/tmp/a.png', label='', json=True)
        import io
        for error in (OSError('synthetic-private-path'), ValueError('synthetic-private-path')):
            out = io.StringIO()
            with patch('agentj.elevate.client_request', side_effect=error), patch('sys.stdout', out):
                with self.assertRaises(SystemExit) as raised:
                    file_show.cli(args)
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(json.loads(out.getvalue()), file_show.failure('broker_unavailable'))

    def test_compound_command_is_not_admitted_and_cli_says_run_it_alone(self):
        # 0.26.7a1: `cd ~/x && agentj file show … --json 2>&1 | head` is no native file_show call → missing + fixed hint
        self.assertIsNone(owner_broker.parse('Bash', {'command': 'cd /tmp && agentj file show /tmp/a.png --json 2>&1 | head -c 600'}, '/tmp'))
        import io
        for as_json in (True, False):
            out = io.StringIO()
            with patch('agentj.elevate.client_request', return_value=file_show.failure('native_admission_missing')), \
                    patch('sys.stdout', out), self.assertRaises(SystemExit):
                file_show.cli(SimpleNamespace(path='/tmp/a.png', label='', json=as_json))
            self.assertIn(file_show.HINT, out.getvalue())

    def test_receipt_reasons_and_replay(self):
        req = file_show.request('/tmp/a.png', cwd='/tmp')
        x = SimpleNamespace(execution=None)
        broker = owner_broker.Broker(x)
        self.assertEqual(broker.receipt(req)['why'], 'no_owner_session')
        x.execution = SimpleNamespace(live=True, send=object())
        self.assertEqual(broker.receipt(req)['why'], 'native_admission_missing')
        row = {'request': req, 'admitted': True, 'check': lambda: False, 'ticket': 'test'}
        broker.calls['stale'] = row
        self.assertEqual(broker.receipt(req)['why'], 'native_admission_expired')
        row['check'] = lambda: True
        broker.calls['ambiguous'] = dict(row)
        self.assertEqual(broker.receipt(req)['why'], 'native_admission_ambiguous')


class NativeFlows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncio
        from test_l1 import _state, _host, Phone
        from agentj import provenance, shared
        from agentj.peer_service import PeerService
        from agentj.compose import Send
        from test_inline_media_stream import PNG
        self.tmp = tempfile.TemporaryDirectory(prefix='file-show-')
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / 'home'; self.home.mkdir()
        self.work = self.home / 'work'; self.work.mkdir()
        self.image = self.work / 'a.png'; self.image.write_bytes(PNG)
        self.envpatch = patch.dict(os.environ, {'HOME': str(self.home),
            'XDG_CONFIG_HOME': str(self.root / 'config'), 'CLAUDE_CONFIG_DIR': str(self.root / 'claude')})
        self.envpatch.start(); self.addCleanup(self.envpatch.stop)
        self.st = _state(self.tmp.name)
        with patch('agentj.names.ctl_call', return_value=None):
            self.st.set_agent_config('claude', str(self.work), fence=False)
        self.host = _host(self.st, []); self.host._post = lambda *a, **kw: None
        self.host.peers = PeerService(self.host)
        self.phone = Phone(self.st)
        self.page = self.host.hist_add({'k': 'you'}, '', 'open')
        self.send = Send(self.phone.did, 'show the file', self.page['id'], source=provenance.phone())
        self.cfg = {**self.host.agent_cfg, 'dir': str(self.work), 'session_mode': 'shared', 'fence': False}
        self.native = shared.SharedClaudeAgent(self.host, self.cfg)
        self.host.agent, self.host.agent_cfg = self.native, self.cfg
        self.native.cur_send = self.send
        self.native.phone_turn = self.native.phone_seen = True
        self.native.pending_source = provenance.phone()
        self.native.loop = asyncio.get_running_loop()
        self.host.cur_turn = self.page['id']
        self.host.peers.xfer.begin_execution(self.send)
        self.notices = []; self.host.agent_notice = self.notices.append
        await self.host.elevate.start()
        self.addAsyncCleanup(self.host.elevate.stop)
        self.addCleanup(self.host.stopping.set)

    async def shared_call(self, command, *, success=True, mutate=None, wd=None):
        """Real authenticated hook, host-read transcript, shell CLI, UDS and completion."""
        import asyncio
        import socket
        import sys
        from agentj import shared_hook
        registry = self.root / 'claude' / 'sessions'; registry.mkdir(parents=True, exist_ok=True)
        messaging = socket.socket(socket.AF_UNIX)
        messaging_path = self.root / 'native.sock'
        messaging.bind(str(messaging_path)); self.addCleanup(messaging.close)
        self.transcript = self.root / 'transcript.jsonl'; self.transcript.touch()
        self.native.transcript = self.transcript
        binary = self.root / 'bin'; binary.mkdir()
        cli = binary / 'agentj'
        cli.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -m agentj.cli "$@"\n'); cli.chmod(0o700)
        hookpath = self.st.root / 'claude-hook.sock'
        channel = shared_hook.Channel(self.native, hookpath)
        await channel.start(); self.addAsyncCleanup(channel.stop)
        code = r'''
import json,sys,subprocess,pathlib
hook,path,transcript,command,wd=sys.argv[1:]
sys.stdin.readline()
block={'type':'tool_use','id':'file-call','name':'Bash','input':{'command':command}}
pathlib.Path(transcript).write_text(json.dumps({'type':'assistant','cwd':wd,'message':{'id':'msg','role':'assistant','content':[block]}})+'\n')
event={'session_id':'file-session','cwd':wd,'tool_name':'Bash','tool_input':{'command':command},'tool_use_id':'file-call'}
def emit(kind,**extra):
    p=subprocess.run([sys.executable,hook,path,kind],input=json.dumps(dict(event,hook_event_name=kind,**extra)),capture_output=True,text=True)
    return json.loads(p.stdout or '{}')
answer=emit('PermissionRequest');print(json.dumps(answer),flush=True)
if answer.get('hookSpecificOutput',{}).get('decision',{}).get('behavior')=='allow':
    p=subprocess.run(command,shell=True,capture_output=True,text=True,cwd=wd)
    print(json.dumps({'exit':p.returncode,'stdout':p.stdout}),flush=True)
    sys.stdin.readline()
    print(json.dumps(emit('PostToolUse',tool_response={'stdout':p.stdout,'exitCode':p.returncode})),flush=True)
'''
        env = {k: os.environ[k] for k in ('HOME', 'XDG_CONFIG_HOME', 'CLAUDE_CONFIG_DIR')}
        env.update(PATH=str(binary) + ':/usr/bin:/bin', AGENTJ_TEST='1', AGENTJ_SKILL_LINK='off',
                   AGENTJ_HARNESS_PREPARE='off', AGENTJ_STATE_DIR=str(self.st.root),
                   AGENTJ_ELEVATE_SOCK=str(self.host.elevate.sock_path),
                   PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(pathlib.Path(__file__).resolve().parents[1]))
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(pathlib.Path(shared_hook.__file__)),
            str(hookpath), str(self.transcript), command, str(wd or self.work), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            self.native.session = {'sessionId': 'file-session', 'pid': proc.pid, 'cwd': str(self.work),
                                  'messagingSocketPath': str(messaging_path), 'status': 'working'}
            (registry / 'owner.json').write_text(json.dumps(self.native.session))
            self.native.issue_hook_token()
            proc.stdin.write(b'go\n'); await proc.stdin.drain()
            answer = json.loads(await asyncio.wait_for(proc.stdout.readline(), 8))
            behavior = answer.get('hookSpecificOutput', {}).get('decision', {}).get('behavior')
            if behavior != 'allow':
                await asyncio.wait_for(proc.communicate(), 8)
                return behavior, None
            cli_result = json.loads(await asyncio.wait_for(proc.stdout.readline(), 8))
            self.assertEqual(cli_result['exit'], 0, cli_result)
            queued = json.loads(cli_result['stdout'].splitlines()[-1])
            self.assertEqual(queued['result'], 'queued')
            self.assertFalse(self.page.get('media'), 'CLI receipt alone cannot attach')
            if mutate: mutate()
            if not success:
                # Native failure must not be mistaken for successful CLI completion.
                self.host.peers.xfer.broker.complete('file-call', False, queued)
            proc.stdin.write(b'complete\n'); await proc.stdin.drain()
            out, err = await asyncio.wait_for(proc.communicate(), 8)
            self.assertEqual(proc.returncode, 0, err.decode())
            await asyncio.wait_for(self.host.peers.xfer.broker.drain(), 4)
            return behavior, queued
        finally:
            if proc.returncode is None:
                proc.kill(); await proc.wait()

    async def test_shared_tilde_real_cli_and_hook_attaches_only_after_completion(self):
        behavior, queued = await self.shared_call('agentj file show ~/work/a.png --json')
        self.assertEqual(behavior, 'allow')
        self.assertEqual(len(self.page['media']), 1)
        result = json.loads(self.notices[-1].removeprefix('FILE_SHOW_RESULT: '))
        self.assertEqual(result['result'], 'attached_to_page')
        self.assertFalse(result['client_downloaded'])
        broker = self.host.peers.xfer.broker
        self.assertNotEqual(broker.receipt(file_show.request(str(self.image), cwd=str(self.work)))['result'], 'queued')
        broker.complete('file-call', True, queued)
        await broker.drain()
        self.assertEqual(len(self.page['media']), 1)

    async def test_shared_owner_turn_bash_after_cd_attaches(self):
        # the shared session's shell had cd'd below the project: native cwd (transcript line) is the request cwd
        sub = self.work / 'notes'; sub.mkdir()
        behavior, queued = await self.shared_call('agentj file show ~/work/a.png --label 图片 --json', wd=sub)
        self.assertEqual(behavior, 'allow')
        self.assertEqual(len(self.page['media']), 1)
        self.assertEqual(json.loads(self.notices[-1].removeprefix('FILE_SHOW_RESULT: '))['result'], 'attached_to_page')

    async def test_shared_owner_turn_cwd_outside_project_still_refused(self):
        out = self.root / 'elsewhere'; out.mkdir()
        (out / 'a.png').write_bytes(self.image.read_bytes())
        behavior, queued = await self.shared_call('agentj file show a.png --json', wd=out)
        self.assertEqual(behavior, 'allow')
        self.assertFalse(self.page.get('media'))
        self.assertEqual(json.loads(self.notices[-1].removeprefix('FILE_SHOW_RESULT: '))['why'], 'outside')

    async def test_shared_native_failure_never_attaches(self):
        await self.shared_call('agentj file show ~/work/a.png --json', success=False)
        self.assertFalse(self.page.get('media'))
        self.assertFalse(self.notices)

    async def test_shared_expired_execution_never_attaches(self):
        await self.shared_call('agentj file show ~/work/a.png --json', mutate=self.host.peers.xfer.end_execution)
        self.assertFalse(self.page.get('media'))

    async def test_shared_changed_remote_send_never_attaches(self):
        from agentj.compose import Send
        from agentj import provenance
        def change():
            self.native.cur_send = Send('other-device', 'other turn', self.page['id'], source=provenance.phone())
        await self.shared_call('agentj file show ~/work/a.png --json', mutate=change)
        self.assertFalse(self.page.get('media'))

    async def test_shared_changed_page_never_attaches(self):
        def change():
            self.host.cur_turn = self.host.hist_add({'k':'you'}, '', 'open')['id']
        await self.shared_call('agentj file show ~/work/a.png --json', mutate=change)
        self.assertFalse(self.page.get('media'))

    async def test_shared_changed_project_never_attaches(self):
        def change(): self.host.agent_cfg = {**self.cfg, 'dir': str(self.root)}
        await self.shared_call('agentj file show ~/work/a.png --json', mutate=change)
        self.assertFalse(self.page.get('media'))
        self.assertEqual(json.loads(self.notices[-1].removeprefix('FILE_SHOW_RESULT: '))['result'], 'failure')

    async def test_background_email_peer_cannot_admit_file(self):
        from agentj import provenance
        from agentj.compose import Send
        sources = [provenance.system(), {'kind':'email','role':'owner','sender':'owner@example.test'},
                   {'kind':'same_owner','role':'task','fid':'peer','otg':[]}]
        for index, source in enumerate(sources):
            self.send = Send(self.phone.did, 'not paired owner', self.page['id'], source=source)
            self.native.cur_send = self.send; self.native.pending_source = source
            self.host.peers.xfer.begin_execution(self.send)
            inp = {'command':'agentj file show ~/work/a.png --json'}
            broker = self.host.peers.xfer.broker
            broker.register(str(index), 'Bash', inp, str(self.work))
            self.assertEqual((await broker.execute('Bash', inp, str(index)))['behavior'], 'deny')
            self.assertFalse(self.page.get('media'))

    async def test_native_tilde_socket_equivalent_and_file_guards(self):
        from agentj import provenance
        from test_inline_media_stream import PNG
        self.host.agent = SimpleNamespace(cur_send=self.send, native_source=provenance.phone())
        broker = self.host.peers.xfer.broker
        (self.work / 'link.png').symlink_to(self.image)
        (self.work / '.env').write_text('SYNTHETIC_TEST_VALUE=not-a-credential\n')
        outside = self.root / 'outside.png'; outside.write_bytes(PNG)
        secret_name = self.work / ('sk-' + 'or-v1-' + 'b'*48 + '.png'); secret_name.write_bytes(PNG)
        for index, path in enumerate(('~/work/a.png', '~/work/link.png', '~/work/.env', str(outside), str(secret_name))):
            inp = {'command': 'agentj file show ' + shlex.quote(path) + ' --json'}
            call = str(index)
            broker.register(call, 'Bash', inp, str(self.work))
            self.assertEqual((await broker.execute('Bash', inp, call))['behavior'], 'allow')
            req = file_show.request(path, cwd=str(self.work))
            # Exercise actual client/server receipt route without native harness credentials.
            from agentj.elevate import client_request
            import asyncio
            queued = await asyncio.to_thread(client_request, self.st, req, path=str(self.host.elevate.sock_path))
            self.assertEqual(queued['result'], 'queued')
            broker.complete(call, True, queued, 'Bash', inp, str(self.work))
            await broker.drain()
            result = json.loads(self.notices[-1].removeprefix('FILE_SHOW_RESULT: '))
            self.assertEqual(result['result'], 'attached_to_page' if index == 0 else 'failure')
        self.assertEqual(len(self.page['media']), 1)

    async def test_shared_phone_not_seen_cannot_register_or_attach(self):
        self.native.phone_seen = False
        behavior, queued = await self.shared_call('agentj file show ~/work/a.png --json')
        self.assertEqual(behavior, 'deny')
        self.assertIsNone(queued)
        self.assertFalse(self.page.get('media'))

    async def test_native_completion_identity_and_cwd_must_match(self):
        broker = self.host.peers.xfer.broker
        command = 'agentj file show ~/work/a.png --json'
        inp = {'command': command}
        req = file_show.request('~/work/a.png', cwd=str(self.work))
        for index, changes in enumerate(({'call':'wrong'}, {'native':('wrong','turn')},
                     {'inp':{'command': command + ' --label other'}}, {'cwd':str(self.root)}, {'success':False})):
            call = str(index)
            broker.register(call, 'Bash', inp, str(self.work), native=('thread','turn'))
            self.assertEqual((await broker.execute('Bash', inp, call))['behavior'], 'allow')
            queued = broker.receipt(req)
            args = dict(call=call, success=True, output=queued, tool='Bash', inp=inp, cwd=str(self.work), native=('thread','turn'))
            args.update(changes)
            broker.complete(**args); await broker.drain()
            self.assertFalse(self.page.get('media'))
            broker.calls.pop(call, None)

    async def test_different_cwd_cannot_unlock_admission(self):
        broker = self.host.peers.xfer.broker
        inp = {'command':'agentj file show ~/work/a.png --json'}
        broker.register('cwd-call', 'Bash', inp, str(self.work))
        self.assertEqual((await broker.execute('Bash', inp, 'cwd-call'))['behavior'], 'allow')
        self.assertEqual(broker.receipt(file_show.request('~/work/a.png', cwd=str(self.root)))['why'], 'native_admission_missing')
        self.assertFalse(self.page.get('media'))

    async def test_attachment_rejection_reasons_do_not_bypass_checks(self):
        from agentj import owner_call, provenance
        req = file_show.request('~/work/a.png', cwd=str(self.work))
        self.assertEqual((await file_show.attach(self.host, req, lambda: True, str(self.work)))['why'], 'native_context_missing')
        with owner_call.request(lambda: True):
            self.assertEqual((await file_show.attach(self.host, req, lambda: False, str(self.work)))['why'], 'expired')
            self.assertEqual((await file_show.attach(self.host, req, lambda: True, str(self.root)))['why'], 'project_changed')
            self.send.source = provenance.system()
            self.assertEqual((await file_show.attach(self.host, req, lambda: True, str(self.work)))['why'], 'owner_source_required')
            self.send.source = provenance.phone()
            self.page['end'] = 'done'
            self.assertEqual((await file_show.attach(self.host, req, lambda: True, str(self.work)))['why'], 'owner_page_inactive')
        self.assertFalse(self.page.get('media'))

    async def test_shared_plain_cli_preserves_native_completion_receipt(self):
        behavior, queued = await self.shared_call('agentj file show ~/work/a.png')
        self.assertEqual(behavior, 'allow')
        self.assertEqual(queued['result'], 'queued')
        self.assertEqual(len(self.page['media']), 1)
        self.assertEqual(json.loads(self.notices[-1].removeprefix('FILE_SHOW_RESULT: '))['result'], 'attached_to_page')
