"""Upgrade handoff must precede any operation that kills the caller's service tree."""
import _hermetic
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import update
from agentj.state import State

class Handoff(unittest.TestCase):
    def test_apply_never_bootouts_in_caller_tree(self):
        with tempfile.TemporaryDirectory() as td:
            st = State(Path(td)); st.init()
            calls = []
            def run(argv, **kw):
                calls.append(argv)
                if argv[-2:] == ['service', 'install']:
                    raise SystemExit('SIGTERM after bootout: bootstrap unreachable')
                return Mock(returncode=0, stdout='agentj 9.9.9', stderr='')
            with patch.object(update, 'commands', return_value=[['install-package']]), patch.object(update, 'writable', return_value=True):
                res = update.apply(st, check_fn=lambda:{'status':'newer','latest':'9.9.9'}, run=run, svc_on=True)
            self.assertEqual(res['exit'], 0)
            self.assertTrue(any('--deferred' in c for c in calls))
            self.assertFalse(any(c[-2:] == ['service','restart'] for c in calls))

from agentj import __version__, service, service_recovery, doctor, cli
import plistlib
import signal
import time

class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name)/'st'); self.st.init()
        env=patch.dict(os.environ, {'HOME':self.tmp.name,'AGENTJ_STATE_DIR':str(self.st.root),'AGENTJ_SERVICE_NAME':'aj.p76.test'})
        env.start();self.addCleanup(env.stop)
    def test_manager_jobs_have_no_secrets_and_no_parent_process(self):
        with patch.dict(os.environ, {'CLAUDE_CODE_OAUTH_TOKEN':'fixture-secret'}):
            with patch.object(service,'platform',return_value='linux'),patch.object(service_recovery.subprocess,'run',return_value=Mock(returncode=0)) as run:
                service_recovery.schedule(self.st,['agentj'],__version__)
                argv=run.call_args.args[0]
                self.assertEqual(argv[0],'systemd-run');self.assertIn('--collect',argv)
                self.assertNotIn('fixture-secret',str(argv))
                self.assertIn('--recovery-worker',argv)
            with patch.object(service,'platform',return_value='macos'),patch.object(service,'_launchctl',return_value=Mock(returncode=0)) as ctl:
                service_recovery.schedule(self.st,['agentj'],__version__)
                self.assertEqual(ctl.call_args.args[0],'bootstrap')
                p=Path(ctl.call_args.args[-1]);job=plistlib.loads(p.read_bytes())
                self.assertFalse(job['KeepAlive']);self.assertTrue(job['RunAtLoad'])
                self.assertNotIn('fixture-secret',p.read_text());self.assertEqual(p.stat().st_mode & 0o777,0o600)
    def test_failures_durable_and_doctor_then_success_clear(self):
        self.assertEqual(service_recovery.run(self.st,__version__,install=Mock(side_effect=OSError()),status=Mock()),1)
        self.assertEqual(service_recovery.failure(self.st)['reason'],'OSError')
        self.assertTrue((self.st.root/service_recovery.NOTICE).exists())
        self.assertIn('agentj service start',service_recovery.failure_text(service_recovery.failure(self.st)))
        self.assertEqual(service_recovery.run(self.st,__version__,install=Mock(),status=lambda:{'active':'active'}),0)
        self.assertIsNone(service_recovery.failure(self.st));self.assertFalse((self.st.root/service_recovery.NOTICE).exists())
        self.assertEqual(service_recovery.run(self.st,'wrong',install=Mock()),1)
        self.assertEqual(service_recovery.failure(self.st)['reason'],'recovery_version_mismatch')
    def test_inactive_and_missing_worker_are_visible(self):
        self.assertEqual(service_recovery.run(self.st,__version__,timeout=0,install=Mock(),status=lambda:{'active':'inactive'}),1)
        self.assertEqual(service_recovery.failure(self.st)['reason'],'recovery_inactive')
        self.st.write_private(self.st.root/service_recovery.RECORD,json.dumps({'status':'pending','at':time.time()-91}).encode())
        self.assertIsNotNone(service_recovery.failure(self.st))
    def test_launch_failure_persists_before_returning(self):
        with patch.object(service_recovery,'launch_job',side_effect=OSError()),self.assertRaises(OSError):
            service_recovery.schedule(self.st,['agentj'],__version__)
        self.assertEqual(service_recovery.read(self.st)['status'],'failed')
    def test_start_stop_installed_and_missing_on_both_platforms(self):
        for plat in ('linux','macos'):
            with patch.object(service,'platform',return_value=plat),patch.object(service,'status',return_value={'installed':False}),patch.object(service,'install',return_value={'name':'aj.p76.test'}) as install:
                service.start(self.st);install.assert_called_once_with(self.st)
            with patch.object(service,'platform',return_value=plat),patch.object(service,'status',return_value={'installed':True}),patch.object(service,'_loaded',return_value=True),patch.object(service,'_systemctl',return_value=Mock(returncode=0)) as systemctl,patch.object(service,'_launchctl',return_value=Mock(returncode=0)) as ctl,patch.object(service,'_bootout_wait',return_value=True) as bootout:
                service.start(self.st);service.stop()
                if plat=='linux':
                    self.assertEqual([c.args[0] for c in systemctl.call_args_list],['start','stop'])
                else:
                    self.assertEqual(ctl.call_args.args[0],'kickstart');bootout.assert_called_once()
    def test_worker_survives_terminated_caller_tree_and_verifies_new_version(self):
        # Local manager fixture: manager spawns outside the service process group.
        # Linux cgroup membership is modeled by killing every PID in caller group;
        # physical launchd/systemd package upgrade remains a separate acceptance.
        host=str(Path(__file__).resolve().parents[1])
        for mode in ('bootout-sigterm','cgroup-kill'):
            with self.subTest(mode=mode):
                script=Path(self.tmp.name)/('caller-'+mode+'.py')
                worker=Path(self.tmp.name)/('worker-'+mode+'.py')
                active=Path(self.tmp.name)/('active-'+mode)
                worker.write_text("import os,sys,signal\nfrom pathlib import Path\nsys.path.insert(0,"+repr(host)+")\nfrom agentj import service_recovery,__version__\nfrom agentj.state import State\nparent=int(sys.argv[1])\nst=State(Path("+repr(str(self.st.root))+"))\ndef install(st):\n    os.killpg(parent,signal.SIGTERM)\n    Path("+repr(str(active))+").write_text(__version__)\nraise SystemExit(service_recovery.run(st,__version__,install=install,status=lambda:{'active':'active'}))\n")
                script.write_text("import subprocess,sys,os,time\np=subprocess.Popen([sys.executable,"+repr(str(worker))+",str(os.getpid())],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\ntime.sleep(30)\n")
                caller=subprocess.Popen([sys.executable,str(script)],start_new_session=True)
                try:
                    self.assertEqual(caller.wait(timeout=15),-signal.SIGTERM)
                    end=time.monotonic()+10
                    while time.monotonic()<end and (not active.exists() or service_recovery.read(self.st).get('status')!='ok'):
                        time.sleep(.05)
                    self.assertEqual(active.read_text(),__version__)
                    self.assertEqual(service_recovery.read(self.st)['status'],'ok')
                finally:
                    if caller.poll() is None:
                        os.killpg(caller.pid,signal.SIGTERM);caller.wait(timeout=5)

    def test_manual_start_of_active_target_clears_old_failure(self):
        service_recovery.record(self.st,'failed',__version__,'launch_failed')
        with patch.object(service,'status',return_value={'installed':True,'active':'active'}),patch.object(service,'_systemctl') as ctl,patch.object(service,'_launchctl') as launch:
            service.start(self.st);ctl.assert_not_called();launch.assert_not_called()
        self.assertIsNone(service_recovery.failure(self.st))
        service_recovery.record(self.st,'failed','9.9.9','launch_failed')
        with patch.object(service,'status',return_value={'installed':True,'active':'active'}):
            service.start(self.st)
        self.assertIsNotNone(service_recovery.failure(self.st))

    def test_plain_install_from_old_apply_hands_off_before_bootout(self):
        import argparse
        args=argparse.Namespace(mode='install',deferred=None,recovery_worker=None,json=False)
        with patch.object(service,'needs_deferred_install',return_value=True),patch.object(service_recovery,'schedule') as schedule,patch.object(service,'remove_legacy') as legacy,patch.object(service,'install') as install,patch.object(cli,'State',return_value=self.st):
            cli.cmd_service(args);schedule.assert_called_once();legacy.assert_not_called();install.assert_not_called()
    def test_legacy_process_detection_pid_namespace_and_mac_active(self):
        with patch.object(service,'platform',return_value='linux'):
            self.assertTrue(service.needs_deferred_install('0::/user.slice/aj.p76.test.service/child'))
            self.assertFalse(service.needs_deferred_install('0::/user.slice/other.service'))
        with patch.object(service,'platform',return_value='macos'),patch.object(service,'status',return_value={'installed':True,'active':'active'}),patch.object(service,'legacy_status',return_value={}):
            self.assertTrue(service.needs_deferred_install())
        with patch.object(service,'platform',return_value='macos'),patch.object(service,'status',return_value={'installed':False}),patch.object(service,'legacy_status',return_value={}):
            self.assertFalse(service.needs_deferred_install())
    def test_private_socket_failure_uses_user_bus_and_preserves_xdg_paths(self):
        with patch.object(service,'platform',return_value='linux'),patch.object(service_recovery.subprocess,'run',side_effect=[Mock(returncode=1),Mock(returncode=0)]) as run,patch.dict(os.environ,{'XDG_CONFIG_HOME':self.tmp.name+'/cfg'}):
            service_recovery.launch_job(self.st,['agentj','service','install'])
            bus=run.call_args.args[0]
            self.assertEqual(bus[0],'busctl');self.assertIn('StartTransientUnit',bus)
            self.assertIn('XDG_CONFIG_HOME='+self.tmp.name+'/cfg',bus)

    def test_fenced_caller_can_handoff_without_host_state_access(self):
        with patch.object(self.st,'write_private',side_effect=PermissionError()),patch.object(service_recovery,'launch_job') as job:
            service_recovery.schedule(self.st,['agentj'],__version__)
        job.assert_called_once();p=service_recovery.receipt_path()
        self.assertEqual(p.stat().st_mode & 0o777,0o600)
        self.assertEqual(service_recovery.read(self.st)['status'],'pending')
        with patch.object(self.st,'write_private',side_effect=PermissionError()),patch.object(service_recovery,'launch_job',side_effect=OSError()),self.assertRaises(OSError):
            service_recovery.schedule(self.st,['agentj'],__version__)
        self.assertEqual(service_recovery.failure(self.st)['reason'],'launch_failed')
        self.assertEqual(service_recovery.run(self.st,__version__,install=Mock(),status=lambda:{'active':'active'}),0)
        self.assertFalse(p.exists());self.assertEqual(update.take_marker(self.st)['to'],__version__)
    def test_mac_job_plist_outside_keys_and_never_unloads_main_label(self):
        with patch.object(service,'platform',return_value='macos'),patch.object(service,'_launchctl',return_value=Mock(returncode=0)) as ctl:
            service_recovery.launch_job(self.st,['agentj'],'recovery')
            path=Path(ctl.call_args.args[-1]);self.assertFalse(path.is_relative_to(self.st.root))
            job=plistlib.loads(path.read_bytes());env=job['EnvironmentVariables']
            with patch.dict(os.environ,env):
                service_recovery.cleanup_job(self.st)
            self.assertFalse(path.exists());self.assertEqual(ctl.call_args.args[0],'bootout')
            ctl.reset_mock()
            with patch.dict(os.environ,{'AGENTJ_JOB_LABEL':'net.agentj.host','AGENTJ_JOB_PLIST':service.plist_path('net.agentj.host')}):
                service_recovery.cleanup_job(self.st)
            ctl.assert_not_called()
