"""P530 real admission/background cards and socket transport; synthetic configuration only."""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import io
import json
import os
import socket
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from test_p352_keys import Base
from agentj import cli, elevate, mail, names, provenance


class PhoneCard(Base):
    def setUp(self):
        super().setUp()
        guard = patch("agentj.diskq._guard_loop", lambda: None)
        guard.start(); self.addCleanup(guard.stop)
        self.st.log = lambda *a, **kw: None
        self.host.push_notify = lambda *_: None
        self.host.mail = mail.Mail(self.host)
        self.host.mail.set_config(mail.apply_preset(mail.blank_config(), 'gmail', 'demo@example.com'))

    def test_idle_background_card_targets_clicking_remote(self):
        from test_l1 import Phone, _ready
        second = _ready(self.host, Phone(self.st, name='second'), cid=12)
        async def go():
            await self.host._app(self.s, {'t': 'mail_key', 'r': 'a'*16})
            for _ in range(50):
                if self.of('elev') or self.of('mail_res'): break
                await asyncio.sleep(.01)
            cards = self.of('elev')
            only = self.host.elevate.cards[cards[0]['id']].get('only') if cards else None
            self.host.stopping.set()
            self.host.elevate.cancel_all('stopped')
            for task in list(self.host._bg_tasks): task.cancel()
            await asyncio.sleep(.05)
            self.assertTrue(cards, self.of('mail_res'))
            self.assertEqual(cards[0]['name'], 'AGENTJ_MAIL_PASSWORD')
            self.assertEqual(only, self.s.device)
            self.assertFalse(any(cid == second.cid and m['t'] == 'elev' for cid, m in self.sent))
            self.assertFalse(any(m.get('ok') for m in self.of('mail_res')))
        asyncio.run(go())

    def test_revoked_before_background_runs_cannot_create_card(self):
        async def go():
            await self.host._app(self.s, {'t':'mail_key','r':'b'*16})
            self.host.sessions.pop(self.s.cid)
            await asyncio.gather(*list(self.host._bg_tasks), return_exceptions=True)
            self.assertFalse(self.of('elev'))
        asyncio.run(go())

    def test_unknown_agent_source_still_rejected(self):
        async def go():
            with patch.object(mail.mail_recovery, 'recover', return_value=None):
                res = await self.host.mail.secret_card(recover=False)
            self.assertEqual(res['result'], 'group_source')
            self.assertFalse(self.of('elev'))
        asyncio.run(go())


class Transport(unittest.TestCase):
    def run_mail(self, action, inside=True):
        output = io.StringIO()
        with patch('agentj.fence.inside', return_value=inside), contextlib.redirect_stdout(output):
            try: rc = cli.main(['mail', action, '--json'])
            except SystemExit as e: rc = e.code
        return rc, json.loads(output.getvalue())

    def test_fenced_password_and_status_use_agent_socket(self):
        for action, typ, result in [('password','mail_key',{'result':'saved'}), ('status','mail_status',{'ok':True,'has_password':False})]:
            with self.subTest(action=action), patch.object(names,'ctl_call',side_effect=PermissionError('denied')), \
                 patch.object(elevate,'client_request',return_value=result) as request:
                _, res = self.run_mail(action)
                self.assertEqual(res, result)
                self.assertEqual(request.call_args.args[1], {'t':typ})
                self.assertIn('path', request.call_args.kwargs)

    def test_permission_and_connect_errors_are_never_busy(self):
        for exc, expected in [(PermissionError(1,'denied'),'sandbox_blocked'), (OSError(13,'denied'),'sandbox_blocked'),
                              (OSError(101,'unreachable'),'host_unreachable')]:
            with self.subTest(exc=type(exc).__name__), patch.object(socket.socket,'connect',side_effect=exc):
                try: names.ctl_call(SimpleNamespace(sock_path='/tmp/synthetic'), {'cmd':'status'})
                except names.ServeBusy as error:
                    self.assertEqual(error.response()['error'], expected)
                    self.assertIn('agentj', error.response()['detail'])
                else: self.fail('must fail closed')

    def test_fenced_agent_socket_denial_is_actionable(self):
        with patch.object(socket.socket,'connect',side_effect=PermissionError(1,'secret-path-must-not-leak')):
            _, res = self.run_mail('password')
        self.assertEqual(res['error'],'sandbox_blocked')
        self.assertNotIn('secret-path',str(res))
        self.assertIn('设置',res['detail'])

    def test_agent_socket_unavailable_is_not_a_host_refusal(self):
        for error, code in [(PermissionError(1,'denied'),'sandbox_blocked'), (ConnectionRefusedError(),'host_unreachable')]:
            with patch.object(socket.socket,'connect',side_effect=error):
                res = elevate.client_request(SimpleNamespace(perm_dir=mail.pathlib.Path('/tmp')), {'t':'mail_status'})
            self.assertEqual(res['result'],'unavailable')
            self.assertEqual(res['error'],code)
            self.assertNotIn('ok',res)

    def test_reconnect_never_restarts_for_permission_or_connection_failure(self):
        from agentj import reconnect
        from test_p147 import FakeService
        for cause in ('PermissionError','ConnectionResetError'):
            service = FakeService(active='active')
            out = io.StringIO()
            def denied(*a): raise names.ServeBusy(cause)
            code = reconnect.run(None, out=out, ctl=denied, service=service, as_json=True)
            self.assertEqual(code,1)
            self.assertFalse(service.calls)
            self.assertNotIn('busy',json.loads(out.getvalue())['error'])

    def test_absent_control_positive_control(self):
        with patch.object(socket.socket,'connect',side_effect=FileNotFoundError):
            self.assertIsNone(names.ctl_call(SimpleNamespace(sock_path='/tmp/synthetic'), {'cmd':'status'}))


class SocketCLI(unittest.IsolatedAsyncioTestCase):
    async def test_real_agent_socket_with_denied_control_and_hidden_state(self):
        import tempfile
        import sys
        from pathlib import Path
        from agentj.state import State
        with tempfile.TemporaryDirectory(prefix='p530-') as tmp:
            st = State(Path(tmp)/'s'); st.init()
            host = SimpleNamespace(st=st, lang='en', agent=None, stopped=lambda:False)
            host.mail = mail.Mail(host)
            host.mail.set_config(mail.apply_preset(mail.blank_config(),'gmail','demo@example.com'))
            el = elevate.Elevator(host)
            host.elevate = el
            await el.start()
            code = '''
import os,socket
from agentj import cli
from agentj.state import State
old=socket.socket.connect
def denied(self,path):
    if str(path).endswith('control.sock'):raise PermissionError(1,'denied')
    return old(self,path)
socket.socket.connect=denied
State.exists=lambda *_: (_ for _ in ()).throw(PermissionError(1,'hidden'))
cli.main(['mail',os.environ['P530_ACTION'],'--json'])
'''
            try:
                for action in ('status','password'):
                    env = dict(os.environ, AGENTJ_FENCED='1', AGENTJ_STATE_DIR=str(Path(tmp)/'hidden'),
                               AGENTJ_ELEVATE_SOCK=str(el.sock_path), P530_ACTION=action,
                               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
                    with patch.object(mail.mail_recovery,'recover',return_value=None):
                        p = await asyncio.create_subprocess_exec(sys.executable,'-c',code,env=env,
                            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                        out,err = await asyncio.wait_for(p.communicate(),10)
                    res = json.loads(out)
                    if action=='status':
                        self.assertEqual(p.returncode,0,err)
                        self.assertEqual(res['config']['address'],'demo@example.com')
                        self.assertFalse(res['has_password'])
                    else:
                        self.assertEqual(p.returncode,1,err)
                        self.assertEqual(res['result'],'group_source',res)
                        self.assertFalse(el.cards)
            finally:
                await el.stop()
