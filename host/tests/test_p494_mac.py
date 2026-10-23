"""P494: Darwin kernel environment fixture; native controls never guess a pane."""
import _hermetic
import io
import os
import struct
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
from agentj import shared_interrupt as si, shared_controls as sc, term_mac, bailian, ark, elevate, opencode_provider as op


def kernel_env(pane='exact'):
    # A fake pane in argv must never be trusted. Secrets are synthetic and never printed.
    return struct.pack('i', 2) + b'/bin/claude\0\0claude\0HERDR_PANE_ID=foreign\0' + b'HERDR_PANE_ID=' + pane.encode() + b'\0OTHER_PRIVATE=fixture\0\0'


class PaneMatrix(unittest.TestCase):
    def test_platform_session_state_action_matrix(self):
        for platform in ('linux', 'darwin'):
            for sid in ('first', 'after-compact', 'after-second-compact'):
                for status in ('idle', 'working', 'blocked'):
                    for action in ('compact', 'stop'):
                        with self.subTest(platform=platform, sid=sid, status=status, action=action):
                            agents = [{'pane_id':'exact','agent_status':status,'agent_session':{'value':'first'}},
                                      {'pane_id':'foreign','agent_status':'idle','agent_session':{'value':'other'}}]
                            calls = []
                            def run(binary, *args):
                                if args == ('agent','list'): return {'result':{'agents':agents}}
                                calls.append(args); return {}
                            with patch('sys.platform', platform), patch.object(si,'herdr_bin',return_value='herdr'), \
                                 patch.object(sc,'herdr_bin',return_value='herdr'), patch.object(si,'_run',side_effect=run), \
                                 patch.object(sc,'_run',side_effect=run), patch('builtins.open',side_effect=lambda *a, **k: io.BytesIO(b'HERDR_PANE_ID=exact\0') if platform=='linux' else (_ for _ in ()).throw(FileNotFoundError())), \
                                 patch.object(term_mac.LIB,'_sysctl',side_effect=lambda mib,size: struct.pack('i',4096) if mib[-1]==8 else kernel_env()):
                                result = sc.send({'sessionId':sid,'pid':123},'/compact') if action=='compact' else si.interrupt({'sessionId':sid,'pid':123})
                            expected = ('sent' if status=='idle' else 'not_idle') if action=='compact' else {'idle':'idle','working':'sent','blocked':'blocked'}[status]
                            self.assertEqual(result, expected)
                            self.assertEqual(calls, [('agent','prompt','exact','/compact')] if action=='compact' and status=='idle' else
                                             [('agent','send-keys','exact','esc')] if action=='stop' and status=='working' else [])

    def test_missing_unknown_and_duplicate_panes_never_get_keys(self):
        for agents in ([], [{'pane_id':'exact','agent_status':None}],
                       [{'pane_id':'a','agent_session':{'value':'same'}},{'pane_id':'b','agent_session':{'value':'same'}}]):
            with self.subTest(agents=agents), patch.object(si,'herdr_bin',return_value='herdr'), \
                 patch.object(si,'_agents',return_value=agents), patch.object(si,'_pane_from_env',return_value='exact'), patch.object(si,'_run') as run:
                self.assertNotEqual(si.interrupt({'sessionId':'same','pid':123}),'sent')
                run.assert_not_called()

    def test_darwin_permission_denied_and_invalid_pid_fail_closed(self):
        with patch('sys.platform','darwin'), patch.object(term_mac.LIB,'args',return_value=None):
            self.assertIsNone(si._pane_from_env(123))
        for pid in (None, True, -1, 'bad'):
            self.assertIsNone(si._pane_from_env(pid))


class ProviderCards(unittest.TestCase):
    def test_presets_open_card_on_real_run_but_not_dry_run(self):
        for module in (bailian, ark):
            for dry in (False, True):
                args=SimpleNamespace(plan=None,model=None,dry_run=dry,json=True,lang='zh',harness=None)
                result={'provider':'fixture','next':['agentj secret request --name FIXTURE_API_KEY --purpose fixture --dest env:/tmp/p494.env#FIXTURE_API_KEY']}
                with self.subTest(module=module.__name__,dry=dry), patch.object(module,'install',return_value=result), \
                     patch('agentj.elevate.client_request',return_value={'result':'pending','id':'fixture-card'}) as request, redirect_stdout(io.StringIO()):
                    module.cmd(args)
                self.assertEqual(request.call_count, 0 if dry else 1)
                if not dry:
                    self.assertEqual(request.call_args.args[1]['name'],'FIXTURE_API_KEY')
                    self.assertEqual(request.call_args.args[1]['t'],'secret')

class Diagnostics(unittest.TestCase):
    def test_startup_guidance_and_switch_paths(self):
        from agentj import startup_help, switch_harness
        for kind in ('codex','opencode'):
            for language in ('zh','en'):
                for code in ('start_failed','start_timeout','no_reply','timeout','call_failed','native_failed'):
                    text=switch_harness.reason_text(code,kind,language)
                    for required in ('AJ_START_', 'PATH', 'HOME', 'agentj doctor --offline', 'agentj provider ark', 'agentj provider bailian', 'Kimi'):
                        self.assertIn(required,text)
        self.assertNotIn('untrusted-secret-value',startup_help.guidance('codex','untrusted-secret-value'))

    def test_opencode_startup_notice_retains_recovery_and_fixed_code(self):
        from unittest.mock import Mock
        from agentj.agent_opencode import OpenCodeAgent
        a=object.__new__(OpenCodeAgent);a.host=Mock(lang='zh');a.proc=None;a.client=None;a.fail_notice=Mock()
        a._startup_failure('listen','synthetic-error')
        self.assertIn('AJ_START_OPENCODE_LISTEN',a.fail_notice.call_args.args[0])
        self.assertTrue(a.fail_notice.call_args.kwargs['recovery'])
        self.assertNotIn('synthetic-error',a.fail_notice.call_args.args[0])

    def test_launchd_environment_reports_names_only_and_linux_skips(self):
        import plistlib
        import tempfile
        from pathlib import Path
        from agentj import doctor
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'service.plist';p.write_bytes(plistlib.dumps({'EnvironmentVariables':{'PATH':'/usr/bin','HOME':'/service-home'}}))
            row=doctor.check_launchd_environment({'kind':'launchd','path':str(p)}, {'PATH':'/opt/bin','HOME':'/terminal-home','HTTPS_PROXY':'http://private:fixture@localhost:99'})
            self.assertEqual(row['status'],'warn')
            for k in ('PATH','HOME','HTTPS_PROXY'): self.assertIn(k,row['summary'])
            for value in ('/service-home','/terminal-home','private:fixture'): self.assertNotIn(value,str(row))
            self.assertIsNone(doctor.check_launchd_environment({'kind':'systemd'}))

    def test_non_herdr_shared_doctor_gives_managed_session_path(self):
        from unittest.mock import Mock
        from agentj import doctor
        st=Mock(spec=['agent_config','exists']);st.exists.return_value=True;st.agent_config.return_value={'kind':'claude','session_mode':'shared','dir':'/tmp/fixture'}
        with patch('agentj.shared.claude_sessions',return_value=[{'pid':123,'sessionId':'fixture'}]), patch.object(si,'herdr_bin',return_value=None):
            row=doctor.check_shared_controls(st)
        self.assertEqual(row['status'],'warn');self.assertIn('Use a separate session',row['hint'])
        st.agent_config.return_value={'kind':'claude','session_mode':'independent'}
        self.assertIsNone(doctor.check_shared_controls(st))

class CardEntrypoints(unittest.TestCase):
    def test_generic_provider_add_opens_card(self):
        a=SimpleNamespace(action='add',id='fixture',base_url='https://fixture.example/v1',model=['model'],api='openai',name=None,key_env=None,dry_run=False,vision=None,max_output=None,context=None,json=True)
        with patch.object(op,'add',return_value={'provider':'fixture','next':['agentj secret request --name FIXTURE_API_KEY --purpose fixture --dest env:/tmp/p494.env#FIXTURE_API_KEY']}), patch.object(elevate,'client_request',return_value={'result':'pending','id':'card'}) as request, redirect_stdout(io.StringIO()):
            self.assertEqual(op.cmd(a),0)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs['timeout'],8)
        self.assertEqual(request.call_args.args[1]['wait'],1)

    def test_ark_switch_missing_key_opens_card_dry_run_does_not(self):
        for dry in (True,False):
            with patch.object(elevate,'client_request',return_value={'result':'pending','id':'card'}) as request:
                r=ark.switch(None,dry_run=dry,detector=lambda:{'opencode':'1.0'},key=lambda *a:False)
            self.assertEqual(r['reason'],'need_key')
            self.assertEqual(request.call_count,0 if dry else 1)

    def test_failed_card_and_saved_key_are_truthful(self):
        command='agentj secret request --name FIXTURE_API_KEY --purpose fixture --dest env:/tmp/p494.env#FIXTURE_API_KEY'
        for result in ('unavailable','pending','already_saved'):
            r={'next':[command]}
            with patch.object(elevate,'client_request',return_value={'result':result,'secret':'must-never-return'}):
                op.open_key_card(r)
            self.assertEqual(r['key_card'],{'result':result})
            self.assertNotIn('must-never-return',str(r))
            self.assertIn({'unavailable':'状态未确认','pending':'已发到手机','already_saved':'无需重复填写'}[result],r['key_card_message'])

    def test_kept_custom_key_reference_never_opens_wrong_card(self):
        r={'kept':True,'provider':'fixture','next':['agentj secret request --name DEFAULT_API_KEY --purpose fixture --dest env:/tmp/p494.env#DEFAULT_API_KEY']}
        with patch('agentj.provider_runtime.key_name',return_value='OWNER_API_KEY'),patch.object(elevate,'client_request') as request:
            op.open_key_card(r)
        request.assert_not_called();self.assertEqual(r['key_card']['result'],'custom_config')

    def test_card_destination_with_spaces_is_not_split(self):
        with patch('agentj.service.env_file',return_value='/fixture/Owner With Space/Library/LaunchAgents/agentj.env'), patch.object(elevate,'client_request',return_value={'result':'pending'}) as request:
            r={'next':bailian.steps('token-plan')}
            op.open_key_card(r)
        self.assertEqual(request.call_args.args[1]['dest'],'env:/fixture/Owner With Space/Library/LaunchAgents/agentj.env#BAILIAN_TOKEN_PLAN_API_KEY')

class CodexLaunch(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_failure_has_fixed_code_and_no_error_payload(self):
        from unittest.mock import Mock, AsyncMock
        from agentj.agent_codex import CodexAgent
        a=object.__new__(CodexAgent);a.host=Mock(lang='en',preferences={});a.cfg={'fence':False,'dir':'/tmp'}
        a.argv=Mock(return_value=['codex','app-server']);a.authorized_launch_argv=AsyncMock(return_value=['codex','app-server']);a.fail_notice=Mock()
        with patch('agentj.agent._bin',return_value='/fixture/codex'), patch('agentj.agent_codex.asyncio.create_subprocess_exec',side_effect=OSError('private failure payload')):
            self.assertFalse(await a._spawn())
        self.assertTrue(a.failed_start)
        text=a.fail_notice.call_args.args[0]
        self.assertIn('AJ_START_CODEX_LAUNCH',text);self.assertNotIn('private failure payload',text)

class Claudepreset(unittest.TestCase):
    def test_ark_claude_card_has_real_destination_and_keeps_profile_use_manual(self):
        args=SimpleNamespace(plan='agent',model=None,dry_run=False,json=True,lang='zh',harness='claude')
        with patch('agentj.provider_profiles.define',return_value={'key_destination':'file:/tmp/p494 profile/key'}) as define, patch.object(elevate,'client_request',return_value={'result':'pending','id':'card'}) as request, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(ark.cmd(args),0)
        define.assert_called_once();request.assert_called_once()
        self.assertEqual(request.call_args.args[1]['dest'],'file:/tmp/p494 profile/key')
        self.assertIn('provider profile use',output.getvalue())

class LaunchdHome(unittest.TestCase):
    def test_implicit_launchd_home_is_account_home_not_overridden_shell_home(self):
        import plistlib,tempfile
        from pathlib import Path
        from agentj import doctor
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'service.plist';p.write_bytes(plistlib.dumps({'EnvironmentVariables':{'PATH':'/usr/bin'}}))
            with patch('pwd.getpwuid',return_value=SimpleNamespace(pw_dir='/account-home')):
                row=doctor.check_launchd_environment({'kind':'launchd','path':str(p)},{'HOME':'/shell-home','PATH':'/usr/bin'})
        self.assertIn('HOME',row['summary']);self.assertEqual(row['status'],'warn')
        self.assertNotIn('/account-home',str(row));self.assertNotIn('/shell-home',str(row))

class MissingExecutable(unittest.IsolatedAsyncioTestCase):
    async def test_launchd_missing_cli_gets_code_environment_and_mainland_path(self):
        from unittest.mock import Mock
        from agentj.agent_codex import CodexAgent
        from agentj.agent_opencode import OpenCodeAgent
        for cls,kind in ((CodexAgent,'codex'),(OpenCodeAgent,'opencode')):
            a=object.__new__(cls);a.host=Mock(lang='zh');a.fail_notice=Mock()
            target='agentj.agent._bin' if kind=='codex' else 'agentj.agent_opencode._bin'
            with patch(target,return_value=None):
                self.assertFalse(await a._spawn())
            self.assertTrue(a.failed_start)
            text=a.fail_notice.call_args.args[0]
            for required in ('AJ_START_'+kind.upper()+'_LAUNCH','PATH','HOME','agentj provider bailian'):
                self.assertIn(required,text)
