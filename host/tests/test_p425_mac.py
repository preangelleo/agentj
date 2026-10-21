"""P425 #180: signed app download, safe probe, rescue and schedule-preserving migration; no real launchd."""
import _hermetic  # noqa: F401
import io
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
from agentj import macapp, service
from test_p327_macapp import ServicePlist

class SignedJobs(ServicePlist):
    def test_rescue_uses_signed_app_with_existing_cli_dispatch(self):
        with patch.object(macapp,'ensure',return_value=self.exe):
            service.install_rescue(self.st)
        d=plistlib.loads(Path(service.rescue_path(service.rescue_name())).read_bytes())
        self.assertEqual(d['ProgramArguments'],[str(self.exe),'/v/bin/agentj','rescue','watchdog'])
        self.assertEqual(d['AssociatedBundleIdentifiers'],[macapp.BUNDLE_ID])
        self.assertIn('Rescue',d['Comment'])

    def test_probe_does_not_execute_scheduled_command(self):
        with patch.object(macapp,'ensure',return_value=self.exe),patch.object(macapp.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
            argv=['/v/bin/agentj','notify','--text','never run this']
            wrapped,extra=macapp.wrap(argv)
        self.assertEqual(run.call_args.args[0],[str(self.exe),'/v/bin/agentj','--version'])
        self.assertEqual(wrapped,[str(self.exe),*argv]);self.assertTrue(extra)

    def test_module_probe_ends_before_any_subcommand(self):
        argv=['/v/bin/python3','-P','-m','agentj.cli','rescue','watchdog']
        with patch.object(macapp,'ensure',return_value=self.exe),patch.object(macapp.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
            wrapped,extra=macapp.wrap(argv)
        self.assertEqual(run.call_args.args[0],[str(self.exe),*argv[:4],'--version'])
        self.assertEqual(wrapped,[str(self.exe),*argv]);self.assertTrue(extra)

    def test_calendar_preserves_schedule_and_skips_unsafe_or_unrelated_items(self):
        directory=Path(service.plist_path('x')).parent;directory.mkdir(parents=True)
        name='net.agentj.channel.weekly.production'
        d={'Label':name,'ProgramArguments':['/v/bin/agentj','notify','--text','test'], 'RunAtLoad':False,'StartCalendarInterval':{'Weekday':1,'Hour':8},'EnvironmentVariables':{'PATH':'/usr/bin'}}
        p=directory/(name+'.plist');p.write_bytes(plistlib.dumps(d));p.chmod(0o600)
        other=directory/'net.agentj.channel.weekly.unsafe.plist';bad=dict(d,Label=other.stem,RunAtLoad=True);other.write_bytes(plistlib.dumps(bad))
        with patch.object(macapp,'ensure',return_value=self.exe):
            self.assertEqual(service.migrate_calendar_jobs(self.st),[name])
            self.assertEqual(service.migrate_calendar_jobs(self.st),[])
        new=plistlib.loads(p.read_bytes())
        for k in ('RunAtLoad','StartCalendarInterval','EnvironmentVariables'):self.assertEqual(new[k],d[k])
        self.assertEqual(plistlib.loads(other.read_bytes()),bad)
        self.assertEqual(new['ProgramArguments'][0],str(self.exe));self.assertEqual(p.stat().st_mode & 0o777,0o600)

    def test_symlink_and_fifo_are_never_read_or_replaced(self):
        directory=Path(service.plist_path('x')).parent;directory.mkdir(parents=True)
        target=self.home/'outside';target.write_bytes(b'unchanged')
        link=directory/'net.agentj.channel.weekly.link.plist';link.symlink_to(target)
        fifo=directory/'net.agentj.channel.weekly.fifo.plist';os.mkfifo(fifo)
        self.assertEqual(service.migrate_calendar_jobs(self.st),[])
        self.assertTrue(link.is_symlink());self.assertEqual(target.read_bytes(),b'unchanged')

    def test_failed_bootstrap_restores_original_bytes(self):
        p=Path(service.plist_path('net.agentj.channel.weekly.production'));p.parent.mkdir(parents=True)
        original=plistlib.dumps({'Label':p.stem,'ProgramArguments':['/v/bin/agentj','notify'], 'RunAtLoad':False,'StartCalendarInterval':{'Hour':8}});p.write_bytes(original)
        with patch.object(macapp,'ensure',return_value=self.exe),patch.object(service,'_bootstrap',return_value=types.SimpleNamespace(returncode=1)):
            self.assertEqual(service.migrate_calendar_jobs(self.st),[])
        self.assertEqual(p.read_bytes(),original)

class Download(unittest.TestCase):
    def test_rescue_fast_dispatch_does_not_import_optional_cli_modules(self):
        import sys
        code = "import sys,types;sys.argv=['agentj','rescue','watchdog'];sys.modules['agentj.rescue_watchdog']=types.SimpleNamespace(main=lambda:42);import agentj.cli"
        r=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=10)
        self.assertEqual(r.returncode,42,r.stderr.decode())

    def test_installer_user_agent_and_pin_still_required(self):
        import hashlib
        data=b'app';pin={'file':'test.zip','size':3,'sha256':hashlib.sha256(data).hexdigest()}
        with patch.object(macapp.urllib.request,'urlopen',return_value=io.BytesIO(data)) as req:
            self.assertEqual(macapp._download(pin),data)
        self.assertTrue(req.call_args.args[0].get_header('User-agent').startswith('Mozilla/5.0'))
        with patch.object(macapp.urllib.request,'urlopen',return_value=io.BytesIO(b'bad')):
            self.assertIsNone(macapp._download(pin))

if __name__=='__main__':unittest.main()
