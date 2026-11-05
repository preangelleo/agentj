"""Fresh checks for the final a4 contracts; no inherited tests are rediscovered."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agentj import busy, provenance, peer_origin, approval_policy, danger
from test_p450_launch_source import LaunchSource
from test_p455_f3b import Dispatch469
from test_p565_mail_notice import _Base, alert, ALERT

class Handshake(LaunchSource):
    def test_true_completion_uses_kernel_handshake_before_first_poll(self):
        self.away()
        async def run():
            srv = await asyncio.start_unix_server(self.host.on_ctl, path=str(self.st.sock_path))
            env = dict(os.environ, AGENTJ_STATE_DIR=str(self.st.root), PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            try:
                child = await asyncio.create_subprocess_exec(sys.executable, '-c', 'from agentj.cli import main; main()', 'busy', 'run', '--who', 'test', '--kind', 'ceo', '--label', 'instant', '--', 'true', env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await asyncio.wait_for(child.communicate(), 12)
                self.assertEqual(child.returncode, 0, err.decode())
                self.host.busy.refresh_outside(units=False)
                self.assertTrue(any(not it.get('foreign') for it in self.host.busy.finished))
            finally:
                srv.close(); await srv.wait_closed()
        asyncio.run(run())
        self.assertEqual(self.completion().source['kind'], 'system')

    def test_handshake_rejects_wrong_peer_uid_key_and_tainted_turn(self):
        self.away()
        pid = self.spawn()
        key = 'run:auth-negative'
        busy.write_item(self.st.root, busy.item(key, 'test', 'ceo', pid=pid, ns=busy.pidns()))
        self.assertFalse(self.host._busy_launch_request({'key':key}, pid, os.getuid()+1))
        self.assertFalse(self.host._busy_launch_request({'key':key}, pid+1, os.getuid()))
        self.assertFalse(self.host._busy_launch_request({'key':'run:missing'}, pid, os.getuid()))
        self.host.busy.launch.turn(False)
        self.assertFalse(self.host._busy_launch_request({'key':key}, pid, os.getuid()))
        self.assertFalse(self.host.busy.launch.ok(key))

class Policy(LaunchSource):
    def test_full_service_dispatch_free_but_own_service_protected(self):
        self.away(); self.harness()
        self.host.agent.cur_send = self.owner()
        self.host.agent.cur_send.source = provenance.system()
        for cmd in ('systemctl --user status agentj-p502.service; git status',
                    'R=$(ls reports/*.md | head -1); sed -n 1,5p "$R"'):
            self.assertEqual(self.ask(cmd)['behavior'], 'allow', cmd)
        self.assertEqual(self.cards(), [])
        self.ask('systemctl --user restart agentj.service')
        self.assertTrue(self.host.asks)

    def test_policy_reclassification_keeps_cwd_and_real_credential_floor(self):
        cmd = 'R=$(ls reports/*.md | head -1); sed -n 1,5p "$R"'
        v = danger.classify_shared('Bash', {'command':cmd}, cwd=str(self.work))
        tier, row = approval_policy.decide('Bash', {'command':cmd}, v, requirement='allow', workdir=str(self.work), chain=True)
        self.assertNotEqual(row, 'secret_read')
        self.assertEqual(approval_policy._shared_cred('Bash', {'command':'cat ~/.aws/credentials'}, workdir=str(self.work)), True)

class NativePersistence(unittest.TestCase):
    def test_bounded_restart_and_corruption_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            b = busy.Board(d); b.trusted = lambda: True
            for n in range(70):
                b.start(f'shared-claude:session:{n}', 'worker', session='shared-claude:session')
            b2=busy.Board(d)
            self.assertEqual(len(b2.launched), 64)
            self.assertNotIn('shared-claude:session:0',b2.launched)
            self.assertIsNone(b2.launched.get('shared-claude:other:69'))
            p=Path(d)/'busy/native-launches.json'
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            p.write_text('{broken')
            self.assertEqual(busy.Board(d).launched,{})
            p.write_text(json.dumps({'shared-claude:session:x':'false'}))
            self.assertEqual(busy.Board(d).launched,{})

class DirectCEO(Dispatch469):
    def test_direct_busy_codex_proven_and_no_peer_unit_record(self):
        cmd=self.f.today().split(f'-p WorkingDirectory={self.f.ws} ',1)[1].replace('claude -p --permission-mode bypassPermissions','codex exec --dangerously-bypass-approvals-and-sandbox')
        self.assertEqual(peer_origin.check(cmd,str(self.f.ws),None,self.f.ctx())[1], 'ok')
        self.assertEqual(peer_origin.units(cmd,str(self.f.ws),self.f.ctx()), [])
        res, card=self.ask_card(cmd)
        self.assertEqual((res['behavior'],card),('allow',None))

class Notice(_Base):
    async def test_strict_notice_metadata_precedes_accept_and_ordinary_mail_does_not(self):
        captured=[]
        async def accept(session, text, sid, blobs=None):
            captured.append(session.verified_mail_notice)
            return await self._accept(session,text,sid,blobs)
        self.host._accept.side_effect=accept
        self.imap.add(alert())
        await self.m.check()
        self.assertEqual(captured,[True])
        cfg = __import__('agentj.mail',fromlist=['read_config']).read_config(self.st)
        cfg['auth']='off'
        __import__('agentj.mail',fromlist=['save_config']).save_config(self.st,cfg)
        self.imap.add(alert(msgid='<unverified@alerts.example>',ar=''))
        await self.m.check()
        self.assertEqual(captured,[True,False])

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(cls(n) for cls in (Handshake,Policy,NativePersistence,DirectCEO,Notice) for n in loader.getTestCaseNames(cls) if n in cls.__dict__)
