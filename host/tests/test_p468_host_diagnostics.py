"""Real Agent socket, separate denied-state CLI process; offline support transport."""
import _hermetic  # noqa: F401
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agentj import elevate, host_diagnostics as hd, names, support, feedback, reconnect
from agentj.state import State


class HostDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p468-')
        self.st = State(Path(self.tmp.name))
        self.st.init()
        self.drafts = tempfile.TemporaryDirectory(prefix='p468-draft-')
        self.checked = Path(self.drafts.name) / 'draft.checked.json'
        self.checked.write_text(json.dumps(dict(stage='other', host_form='mac', os='macOS', agent_kind='codex',
            agent_version='1', install_md_version='1', problem='Connection check', resolved=False, owner_informed=True)))
        self.el = elevate.Elevator(SimpleNamespace(st=self.st))
        self.env = patch.dict(os.environ, {})
        self.env.start()
        await self.el.start()
        async def control(r, w):
            req = json.loads(await r.readline())
            w.write((json.dumps({'ok': True, 'agent': 'codex', 'agent_status': 'working',
                                'relay_state': 'up', 'relay_up': True, 'identity_ok': True}) + '\n').encode())
            await w.drain()
            w.close()
            await w.wait_closed()
        self.ctl = await asyncio.start_unix_server(control, path=str(self.st.sock_path))

    async def asyncTearDown(self):
        self.ctl.close()
        await self.ctl.wait_closed()
        await self.el.stop()
        self.env.stop()
        self.tmp.cleanup()
        self.drafts.cleanup()

    async def child(self, command, mode='codex-macos'):
        code = '''
import os, pathlib, sys
from types import SimpleNamespace as A
from agentj import cli, feedback, support
from agentj.state import State
root = os.environ['DENIED_ROOT']
old_open = pathlib.Path.open
def denied(self, *a, **kw):
    if str(self).startswith(root):
        raise PermissionError('sandbox denied state')
    return old_open(self, *a, **kw)
pathlib.Path.open = denied
State.exists = lambda self: (_ for _ in ()).throw(PermissionError('sandbox denied state'))
command = sys.argv[1]
if command == 'status': cli.main(['status', '--json'])
elif command == 'reconnect': cli.main(['doctor', '--reconnect', '--json'])
elif command == 'support': cli.main(['support', 'ask', 'Connection check failed', '--attach-doctor', '--wait', '0'])
elif command == 'feedback': cli.main(['feedback', 'send', sys.argv[2]])
'''
        env = dict(os.environ, AGENTJ_STATE_DIR=str(self.st.root), DENIED_ROOT=str(self.st.root),
                   AGENTJ_FENCED='1' if mode in ('opencode', 'claude') else '0',
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        p = await asyncio.create_subprocess_exec(sys.executable, '-c', code, command, str(self.checked),
                                                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 20)
        return p.returncode, out.decode(), err.decode()

    async def test_denied_state_status_and_reconnect_all_harnesses(self):
        for mode in ('codex-macos', 'codex-linux', 'opencode', 'claude'):
            for cmd in ('status', 'reconnect'):
                with self.subTest(mode=mode, cmd=cmd):
                    rc, out, err = await self.child(cmd, mode)
                    self.assertEqual(rc, 0, (out, err))
                    self.assertTrue(json.loads(out)['ok'])
                    self.assertNotIn('PermissionError', err)

    async def test_denied_state_support_signed_by_host_and_redacted(self):
        st = self.st
        sent = []
        class Channel:
            signed = True
            def __init__(self): self.st = st
            def send(self, fields):
                st.signing_key()  # real host key read, impossible inside the child
                sent.append(fields)
                return 201, {'thread': 'st_' + 'a'*22, 'seq': 1}
        with patch.object(support, 'channel_for', return_value=Channel()), \
             patch.object(support.privacy, 'layer2', return_value=SimpleNamespace(status='ok')):
            for mode in ('codex-macos', 'codex-linux', 'opencode', 'claude'):
                rc, out, err = await self.child('support', mode)
                self.assertEqual(rc, 5, (out, err))
                self.assertIn('st_' + 'a'*22, out)
        self.assertEqual(len(sent), 4)
        self.assertTrue(all('attach' in item for item in sent))
        self.assertTrue((st.root / 'support' / 'threads.json').exists())

    async def test_denied_state_legacy_feedback_host_owns_bearer_and_receipt(self):
        session = Path(self.tmp.name) / 'feedback-id'
        token = 'aji_' + 'a'*43
        session.write_text(token)
        receipts = Path(self.tmp.name) / 'feedback' / 'receipts.json'
        calls = []
        def http(method, url, headers, body):
            self.assertEqual(headers['Authorization'], 'Bearer ' + token)
            calls.append(json.loads(body))
            return 201, json.dumps({'id': 'fb_' + 'a'*22, 'receipt': 'ajr_' + 'b'*43}).encode()
        with patch.object(feedback, 'SESSION_FILE', str(session)), \
             patch.object(feedback, 'http', side_effect=http), \
             patch.object(feedback, 'receipts_path', return_value=receipts), \
             patch.object(feedback.privacy, 'layer2', return_value=SimpleNamespace(status='ok', reason='ok', kind=None)):
            # The function default remains the published default, so redirect that exact host-only path.
            old = feedback.run_send
            def send(*a, **kw): return old(*a, **kw, session_file=str(session))
            with patch.object(feedback, 'run_send', side_effect=send):
                for mode in ('codex-macos', 'codex-linux', 'opencode', 'claude'):
                    rc, out, err = await self.child('feedback', mode)
                    self.assertEqual(rc, 0, (out, err))
                    self.assertIn('fb_' + 'a'*22, out)
                    self.assertNotIn(token, out + err)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(json.loads(receipts.read_text())['receipts']), 4)

    async def test_unknown_fields_and_paths_refused_before_execution(self):
        for req in ({'op': 'reconnect', 'path': '/etc/passwd'}, {'op': 'support', 'wait': 999},
                    {'op': 'feedback_send', 'session_file': '/etc/passwd'}, {'op': 'status', 'as_json': True}):
            result = await asyncio.to_thread(elevate.client_request, self.st, {'t': 'host_diagnostics', **req})
            self.assertEqual(result['exit'], 1)
            self.assertIn('refused', result['output'])


class Contracts(unittest.TestCase):
    def test_calling_claude_session_stays_automatic_not_explicit(self):
        with patch.object(reconnect, 'run', return_value=0) as run:
            self.assertEqual(hd.execute(SimpleNamespace(agent_name=lambda: 'Agent J'),
                {'op': 'reconnect', 'session': None, 'caller_session': 'calling-session'})['exit'], 0)
            self.assertIsNone(run.call_args.kwargs['session'])
            self.assertEqual(run.call_args.kwargs['env'], {'CLAUDE_CODE_SESSION_ID': 'calling-session'})

    def test_unavailable_agent_never_reads_state(self):
        for marker in ('1', '0'):
            with patch.dict(os.environ, {elevate.ENV: '/nonexistent/p468.sock', 'AGENTJ_FENCED': marker}), \
                 patch.object(State, 'exists', side_effect=PermissionError), patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(hd.request('status'), 1)
                self.assertIn('没有确认提交', out.getvalue())

    def test_legacy_feedback_raw_keeps_privacy_gate(self):
        draft = dict(stage='other', host_form='mac', os='macOS', agent_kind='codex', agent_version='1',
                     install_md_version='1', problem='Connection check', resolved=False, owner_informed=True)
        with patch.object(feedback.privacy, 'layer2', return_value=SimpleNamespace(status='blocked', reason='private', kind='key')), \
             patch.object(feedback, 'http') as http:
            rc = feedback.run_send(None, raw_text=json.dumps(draft), identity=('', ''), out=io.StringIO())
            self.assertEqual(rc, feedback.EXIT_BLOCKED)
            http.assert_not_called()
