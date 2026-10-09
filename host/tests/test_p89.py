import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agentj import keep_awake as k, elevate, doctor, main_identity
from agentj.state import State


class Policy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / 'state')
        self.mac = {'system': 'macos', 'sleep': {'ac': 20, 'battery': 5}, 'awake': False}
        self.linux = {'system': 'linux', 'targets': {t: 'static' for t in k.TARGETS}, 'awake': False}
        self.linux['targets']['hibernate.target'] = 'masked'

    def test_dry_run_and_status_never_write_or_send_card(self):
        with patch.object(k, 'status', return_value=self.mac), patch.object(elevate, 'client_request') as send:
            self.assertTrue(k.execute('on', dry_run=True, st=self.st)['ok'])
            self.assertTrue(k.execute('status', st=self.st)['ok'])
            self.assertTrue(k.execute('off', dry_run=True, st=self.st)['ok'])
            send.assert_not_called(); self.assertFalse(self.st.root.exists())

    def test_mac_roundtrip_idempotent_preserves_battery_and_original(self):
        current = copy.deepcopy(self.mac)
        def send(st, req):
            args = req['argv']; self.assertEqual(args[0], '/usr/bin/pmset')
            current['sleep']['ac' if args[1] == '-c' else 'battery'] = int(args[-1])
            return {'result': 'done', 'code': 0}
        with patch.object(k, 'status', side_effect=lambda: copy.deepcopy(current)), patch.object(elevate, 'client_request', side_effect=send) as call:
            self.assertTrue(k.execute('on', st=self.st)['verified'])
            self.assertEqual(current['sleep'], {'ac': 0, 'battery': 5})
            self.assertTrue(k.execute('on', st=self.st)['verified']); self.assertEqual(call.call_count, 1)
            self.assertEqual(json.loads((self.st.root / 'keep-awake.json').read_text())['sleep'], {'ac': 20})
            self.assertTrue(k.execute('off', st=self.st)['verified'])
            self.assertEqual(current['sleep'], self.mac['sleep'])
            self.assertTrue(k.execute('off', st=self.st)['verified']); self.assertEqual(call.call_count, 2)

    def test_linux_does_not_unmask_preexisting_masks(self):
        current = copy.deepcopy(self.linux)
        def send(st, req):
            args = req['argv']
            for target in args[2:]: current['targets'][target] = 'masked' if args[1] == 'mask' else 'static'
            return {'result': 'done', 'code': 0}
        with patch.object(k, 'status', side_effect=lambda: copy.deepcopy(current)), patch.object(elevate, 'client_request', side_effect=send), patch.object(k.os.path, 'lexists', return_value=False):
            self.assertTrue(k.execute('on', st=self.st)['verified'])
            self.assertTrue(k.execute('off', st=self.st)['verified'])
            self.assertEqual(current['targets'], self.linux['targets'])

    def test_success_receipt_requires_readback(self):
        with patch.object(k, 'status', return_value=self.mac), patch.object(elevate, 'client_request', return_value={'result':'done','code':0}):
            res = k.execute('on', st=self.st)
            self.assertFalse(res['ok']); self.assertFalse(res['verified']); self.assertTrue(res['restore_retained'])

    def test_denied_partial_failure_retains_restore(self):
        with patch.object(k, 'status', return_value=self.mac), patch.object(elevate, 'client_request', return_value={'result':'denied'}):
            res = k.execute('on', st=self.st)
            self.assertFalse(res['ok']); self.assertTrue((self.st.root/'keep-awake.json').exists())

    def test_wsl_guidance_no_elevation(self):
        with patch.object(k, 'status', return_value={'system':'wsl','awake':None,'guidance':k.WINDOWS}), patch.object(elevate, 'client_request') as send:
            self.assertTrue(k.execute('on', st=self.st)['guidance_only']); send.assert_not_called()
            self.assertFalse(self.st.root.exists())

    def test_corrupt_record_and_override_refused(self):
        self.st.root.mkdir(); (self.st.root/'keep-awake.json').write_text('{}')
        with patch.object(k, 'status', return_value=self.mac), patch.object(elevate, 'client_request') as send:
            self.assertFalse(k.execute('on', st=self.st)['ok']); send.assert_not_called()
        with patch.object(k.os.path, 'lexists', return_value=True):
            with self.assertRaises(ValueError): k.plan('on', self.linux, None, 'ac')

    def test_linux_battery_only_request_not_silently_global(self):
        with self.assertRaises(ValueError): k.plan('on', self.linux, None, 'battery')

    def test_cli_preview_skips_migration_and_never_creates_state(self):
        from agentj import cli, migrate
        with patch.object(migrate, 'auto') as auto, patch.object(elevate, 'client_request', return_value={'result':'unavailable'}), patch.object(k, 'status', return_value=self.mac), patch('builtins.print'):
            with self.assertRaises(SystemExit) as ended: cli.main(['keep-awake','on','--dry-run','--json'])
            self.assertEqual(ended.exception.code, 0); auto.assert_not_called()

    def test_pmset_parser_separates_power_sources(self):
        with patch.object(k, 'system', return_value='macos'), patch.object(k, 'read', return_value=(0, 'Battery Power:\n sleep 5\n displaysleep 2\nAC Power:\n sleep 0', '')):
            r=k.status(); self.assertEqual(r['sleep'], {'battery':5,'ac':0}); self.assertTrue(r['awake'])

    def test_doctor_unknown_warn_and_identity_guidance(self):
        with patch.object(k, 'status', return_value={'awake':None}):
            self.assertEqual(doctor.check_keep_awake()['status'], 'warn')
        for lang in ('en','zh'): self.assertIn('keep-awake', main_identity.ELEVATE_LINE[lang])


class MacAuth(unittest.TestCase):
    def test_active_include_biometrics_warn_signed_effect_and_do_not_launch(self):
        with tempfile.TemporaryDirectory() as d, patch.object(elevate.sys, 'platform', 'darwin'):
            p=Path(d); (p/'sudo').write_text('auth include sudo_local\n')
            (p/'sudo_local').write_text('# auth sufficient pam_tid.so\nauth optional pam_reattach.so\nauth sufficient pam_tid.so\n')
            risk=elevate.local_auth_risk(p); self.assertEqual(len(risk),2)
            with patch.object(elevate, 'local_auth_risk', return_value=risk), patch.object(elevate.subprocess, 'Popen') as launch:
                card=elevate.norm_sudo({'argv':['true'],'why':'test'})
                self.assertIn('Touch ID',card['effect']); self.assertEqual(elevate.shown_fields(card)[2],card['effect'])
                res=elevate.run_sudo(['true'],bytearray(b'fake'), '/', 2)
                self.assertEqual(res['why'],'local_auth_required'); launch.assert_not_called()
            (p/'sudo_local').write_text('# auth sufficient pam_tid.so\n')
            self.assertEqual(elevate.local_auth_risk(p),[])

    def test_gui_osascript_elevation_refused(self):
        with patch.object(elevate.sys,'platform','darwin'):
            with self.assertRaises(elevate.Refused):
                elevate.norm_sudo({'argv':['osascript','-e','do shell script "true" with administrator privileges'],'why':'test'})

# Reuse the real host/socket/paired-phone fixture; no system settings are changed.
import test_f17 as f17


class SocketFlows(unittest.TestCase):
    setUp = f17.Flows.setUp
    tearDown = f17.Flows.tearDown
    cards = f17.Flows.cards
    wait_card = f17.Flows.wait_card
    run_flow = f17.Flows.run_flow
    assert_no_leak = f17.Flows.assert_no_leak

    def test_host_owned_policy_uses_signed_phone_card_and_readback(self):
        current = {'system':'macos','sleep':{'ac':20,'battery':5},'awake':False}
        def apply(argv,password,cwd,timeout,sudo):
            self.assertEqual(argv, ['/usr/bin/pmset','-c','sleep','0'])
            self.assertEqual(bytes(password).decode(), f17.PW)
            current['sleep']['ac']=0
            return {'result':'done','code':0,'stdout':'','stderr':''}
        async def phone(host, session, ph):
            card = await self.wait_card()
            self.assertEqual(card['cmd'],'/usr/bin/pmset -c sleep 0')
            await host._app(session, f17.answer(ph,card,True,f17.PW))
        with patch.object(k,'status',side_effect=lambda:copy.deepcopy(current)), patch.object(elevate,'run_sudo',side_effect=apply):
            res,host,ph=self.run_flow({'t':'keep_awake','action':'on','power':'ac','dry_run':False},phone)
        self.assertTrue(res['verified']); self.assertTrue(res['ok'])
        self.assertEqual(json.loads((self.st.root/'keep-awake.json').read_text())['sleep'],{'ac':20})
        self.assertEqual(elevate.check_record(self.st,elevate.read_log(self.st)[-1]),'ok')
        self.assert_no_leak(f17.PW,result=res)

    def test_socket_dry_run_has_no_card_and_invalid_request_refused(self):
        with patch.object(k,'status',return_value={'system':'macos','sleep':{'ac':20},'awake':False}):
            res,_,_=self.run_flow({'t':'keep_awake','action':'on','power':'ac','dry_run':True},None)
        self.assertTrue(res['dry_run']); self.assertEqual(self.cards(),[])
        self.assertFalse((self.st.root/'keep-awake.json').exists())
        res,_,_=self.run_flow({'t':'keep_awake','action':'on','power':'injected','dry_run':False},None)
        self.assertEqual(res['result'],'refused')

    def test_known_pam_risk_phone_warning_and_failure_without_password_retries(self):
        async def phone(host, session, ph):
            card = await self.wait_card()
            self.assertIn('Touch ID',card['effect']); self.assertLessEqual(len(card['effect']),300)
            await host._app(session,f17.answer(ph,card,True,f17.PW))
        with patch.object(elevate,'local_auth_risk',return_value=['sudo_local: pam_tid.so']), patch.object(elevate.subprocess,'Popen') as launch:
            res,_,_=self.run_flow({'t':'sudo','argv':['true'],'why':'test'},phone)
            launch.assert_not_called()
        self.assertEqual(res['why'],'local_auth_required'); self.assertEqual(len(self.cards()),1)
        self.assert_no_leak(f17.PW,result=res)
