"""P78 owner-approved peer replies and first-phone-message doctor guidance."""
import _hermetic
import copy
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import doctor, main_identity, peer_service, wire
from agentj.state import State
from test_p71_peer_service import Base, freq, peer_keys, pmsg, sign_answer, settle, CANARY_TEXT

class PeerOwnerReplies(Base):
    async def test_new_friend_defaults_to_friends(self):
        k = peer_keys()
        self.svc.on_request(k.id, k.x_pub, k.ed_pub, freq(k), k.mbox)
        a = self.host.of('ask')[-1]
        self.svc.answer(self.did, sign_answer(self.host.st, self.did, self.dsk, a, True))
        self.assertEqual(self.store.get(k.id)['group'], 'friend')
        self.assertEqual(self.store.group_of(k.id)['auto']['max_auto_rounds'], 12)

    async def test_signed_no_draft_generates_once_and_sends(self):
        k = self.friend(group='friend')
        self.sess.push('ask_owner', '')
        self.svc.on_app(k.id, pmsg('怎么实现技术方案？'))
        await settle()
        a = self.host.of('ask')[-1]
        self.sess.next.clear(); self.sess.push('reply', '用有界队列处理')
        o = sign_answer(self.host.st, self.did, self.dsk, a, True)
        self.assertTrue(self.svc.answer(self.did, o)); await settle()
        self.assertEqual(self.net.of('pmsg')[-1][1]['text'], '用有界队列处理')
        calls = len(self.sess.calls)
        self.svc.answer(self.did, o); await settle()
        self.assertEqual(len(self.sess.calls), calls)

    async def test_one_signed_action_promotes_and_replies_tamper_refused(self):
        k = self.friend(group='friend'); self.sess.push('ask_owner', '需要再讨论')
        self.svc.on_app(k.id, pmsg('继续讨论技术细节')); await settle()
        a = self.host.of('ask')[-1]
        bad = sign_answer(self.host.st, self.did, self.dsk, a, True); bad['group'] = 'colleague'
        self.svc.answer(self.did, bad)
        self.assertEqual(self.store.get(k.id)['group'], 'friend')
        self.assertEqual(self.net.of('pmsg'), [])
        good = sign_answer(self.host.st, self.did, self.dsk, a, True, 'colleague')
        self.svc.answer(self.did, good); await settle()
        self.assertEqual(self.store.get(k.id)['group'], 'colleague')
        self.assertEqual(len(self.net.of('pmsg')), 1)
        self.assertIn('约时间', self.store.group_of(k.id)['auto']['ask'])

    async def test_generated_secret_still_refused(self):
        k = self.friend(); self.sess.push('ask_owner', '')
        self.svc.on_app(k.id, pmsg('技术问题')); await settle()
        a = self.host.of('ask')[-1]
        self.sess.next.clear(); self.sess.push('reply', CANARY_TEXT)
        self.svc.answer(self.did, sign_answer(self.host.st, self.did, self.dsk, a, True)); await settle()
        self.assertEqual(self.net.of('pmsg'), [])

    async def test_seven_technical_rounds_auto_then_quote_asks(self):
        k = self.friend(group='friend'); self.sess.push('reply', '技术回答')
        for i in range(7):
            self.clock.t += 11
            self.svc.on_app(k.id, pmsg('技术讨论 '+str(i))); await settle()
        self.assertEqual(len(self.net.of('pmsg')), 7)
        self.assertEqual(self.host.of('ask'), [])
        self.sess.next.clear(); self.sess.push('ask_owner', '报价需要主人确认')
        self.clock.t += 11; self.svc.on_app(k.id, pmsg('请报价')); await settle()
        self.assertEqual(self.host.of('ask')[-1]['tool'], 'peer_question')
        self.assertEqual(len(self.net.of('pmsg')), 7)

class DoctorFirstTurn(unittest.TestCase):
    def test_no_first_turn_is_actionable_but_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as td:
            st = State(pathlib.Path(td)/'state'); st.init(relay='ws://127.0.0.1:9')
            cfg = {'kind':'claude', 'dir':td, 'working_root':td}
            with patch.object(st, 'agent_config', return_value=cfg), patch.object(main_identity, 'latest_audit', return_value=(False, 'no verified main Agent launch')):
                row = next(r for r in doctor.check_main_identity(st) if r['id']=='main-inject')
                self.assertEqual(row['status'], doctor.WARN)
                self.assertIn('从手机发一句话', row['summary'])
                self.assertIn('send one message', row['summary'])
            with patch.object(st, 'agent_config', return_value=cfg), patch.object(main_identity, 'latest_audit', return_value=(False, 'identity mismatch')):
                row = next(r for r in doctor.check_main_identity(st) if r['id']=='main-inject')
                self.assertEqual(row['status'], doctor.FAIL)

class AJIBinding(unittest.TestCase):
    def test_signed_aji_and_replay_no_code_logged(self):
        from agentj import cloud
        with tempfile.TemporaryDirectory() as td:
            st=State(pathlib.Path(td)/'state');st.init(relay='ws://127.0.0.1:9');code='AJI-'+'a'*32;seen=[]
            def post(url,obj):
                import base64,json
                inner=json.loads(base64.urlsafe_b64decode(obj['body']+'='*(-len(obj['body'])%4)))
                seen.append(inner);self.assertEqual(inner['token'],code);self.assertTrue(url.endswith('/seat-bind'))
                return 410,{'error':'installation_already_used'}
            self.assertEqual(cloud.seat_bind(st,'https://agentj.app/api',code,'Installed',post=post)['status'],'invalid_setup')
            self.assertEqual(len(seen),1)
            self.assertFalse(any(code.encode() in p.read_bytes() for p in st.root.rglob('*') if p.is_file()))
            self.assertEqual(cloud.seat_bind(st,'https://agentj.app/api','AJI-invalid','Installed',post=lambda *a:self.fail('bad code sent'))['status'],'bad_code')


class UpgradeQualification(unittest.TestCase):
    def test_old_identity_metadata_is_refreshable(self):
        with tempfile.TemporaryDirectory() as td:
            st = State(pathlib.Path(td)/'state'); st.init(relay='ws://127.0.0.1:9')
            cfg = {'kind':'claude', 'dir':td, 'working_root':td}
            with patch.object(st, 'agent_config', return_value=cfg), patch.object(main_identity, 'latest_audit', return_value=(False, 'restart main Agent: identity metadata differs')):
                row = next(r for r in doctor.check_main_identity(st) if r['id']=='main-inject')
                self.assertEqual(row['status'], doctor.WARN)
                self.assertIn('/clear', row['summary'])
                self.assertIn('升级后从手机发一句话', row['summary'])

    def test_upgrade_integrity_and_existing_workflows_are_distinct(self):
        from agentj import update, phone_update
        rows = [{'id':'main-core','status':'ok'}, {'id':'root-structure','status':'fail'}]
        self.assertEqual(update.upgrade_health(rows), (False,['root-structure']))
        self.assertEqual(update.upgrade_health([{'id':'main-core','status':'fail'}]), (True,[]))
        self.assertEqual(update.upgrade_health([{'status':'fail'}]), (True,[]))
        self.assertEqual(update.upgrade_health([]), (True,[]))
        self.assertEqual(update.upgrade_health([{'id':'upgrade-restart','status':'fail'}]), (True,[]))
        rec = {'result':'ok','reason':'upgraded','from':'1','to':'2','service':'restart_scheduled','existing_issues':['root-structure']}
        self.assertIn('升级已完成', update.result_block(rec))
        self.assertIn('升级成功', phone_update.text(rec))

    def test_apply_old_workflow_failure_does_not_fail_install(self):
        from agentj import update
        import subprocess, json
        with tempfile.TemporaryDirectory() as td:
            st = State(pathlib.Path(td)/'state');st.init()
            def run(argv, **kw):
                if '--version' in argv: return subprocess.CompletedProcess(argv,0,'agentj 9.9.9','')
                if 'doctor' in argv:
                    self.assertIn('--json',argv)
                    return subprocess.CompletedProcess(argv,1,json.dumps({'checks':[{'id':'root-structure','status':'fail'}]}),'')
                return subprocess.CompletedProcess(argv,0,'','')
            with patch.object(update,'writable',return_value=True), patch.object(update,'install_kind',return_value={'kind':'uv','where':td}), patch.object(update,'new_argv',return_value=['agentj']):
                rec = update.apply(st,'9.9.9',check_fn=lambda:{'status':'newer','latest':'9.9.9'},run=run,svc_on=False)
            self.assertEqual(rec['result'],'ok')
            self.assertEqual(rec['existing_issues'],['root-structure'])
