"""P94: concurrent private installations, signed-helper ownership, residency guidance.
These are isolated HOME processes, not a substitute for two real OS users.
"""
import _hermetic  # noqa: F401
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentj import doctor, elevate_helper as eh, service


class Coexistence(unittest.TestCase):
    def test_three_concurrent_private_hosts(self):
        script = r"""
import json, socket, sys
from agentj.state import State
from agentj.admin import AdminServer
from agentj import elevate_helper as eh
st=State();st.init();st.perm_dir.mkdir(mode=0o700,exist_ok=True)
s=socket.socket(socket.AF_UNIX);s.bind(str(st.perm_sock_path))
admin=AdminServer(st).start()
print(json.dumps({'port':admin.port,'root':str(st.root),'channel':st.config()['channel'],'helper':eh.HELPER}),flush=True)
sys.stdin.readline()
admin.stop();s.close()
"""
        with tempfile.TemporaryDirectory() as td:
            processes=[]
            try:
                for i in range(3):
                    home=Path(td)/str(i);home.mkdir()
                    env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'.config'),PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(Path(__file__).resolve().parents[1]))
                    env.pop('AGENTJ_STATE_DIR',None);env.pop('AGENTJARVIS_STATE_DIR',None)
                    processes.append(subprocess.Popen([sys.executable,'-c',script],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
                rows=[]
                for proc in processes:
                    line=proc.stdout.readline()
                    if not line:
                        self.fail(proc.stderr.read())
                    rows.append(json.loads(line))
                for key in ('port','root','channel'):
                    self.assertEqual(len({row[key] for row in rows}),3,key)
                for row in rows:
                    self.assertEqual(Path(row['root']).stat().st_mode & 0o777,0o700)
            finally:
                for proc in processes:
                    if proc.poll() is None:
                        try:proc.communicate('stop\n',timeout=10)
                        except subprocess.TimeoutExpired:proc.kill();proc.communicate()

    def test_helper_files_and_uninstall_are_uid_scoped(self):
        self.assertTrue(eh.HELPER.endswith('-'+str(os.getuid())))
        with tempfile.TemporaryDirectory() as td:
            from agentj.state import State
            st=State(Path(td)/'state');st.init()
            files=eh.files(st,'someone')
            source=files['agentj-elevate'].decode()
            ns={'__name__':'p94_test'};exec(compile(source,'helper','exec'),ns)
            self.assertEqual(ns['KEYS'],eh.KEYS);self.assertEqual(ns['STATE'],eh.STATE)
            for mac in (False,True):
                a=eh.install_script(td,{k:eh.sha(v) for k,v in files.items()},mac=mac)
                b=eh.install_script(td,{k:eh.sha(v) for k,v in files.items()},mac=mac)
                self.assertNotEqual(a,b,'staging names must not collide during concurrent sync')
                self.assertIn(eh.KEYS,a);self.assertIn(eh.HELPER,a)
            self.assertNotIn('rm -f /etc/sudoers.d/agentj-elevate ',eh.uninstall_script())

    def test_linger_warns_on_desktop_without_mutation(self):
        with mock.patch.object(service,'_linger',return_value='no'):
            row=doctor.check_linger({'kind':'systemd'},{'WAYLAND_DISPLAY':'wayland-1'})
            self.assertEqual(row['status'],'warn');self.assertIn('enable-linger',row['hint'])
        with mock.patch.object(service,'platform',return_value='linux'), mock.patch.object(service,'_linger',return_value='yes'):
            self.assertIsNone(service.residency_hint('en'))

    def test_mac_gui_detection_and_phone_reminder(self):
        for code,want in ((0,'ok'),(1,'warn')):
            with mock.patch.object(service,'_launchctl',return_value=subprocess.CompletedProcess([],code)):
                self.assertEqual(doctor.check_login_session({'kind':'launchd'})['status'],want)
        self.assertIsNone(doctor.check_login_session({'kind':'systemd'}))
        with mock.patch.object(service,'platform',return_value='macos'):
            self.assertIn('log in once',service.residency_hint('en'))
            self.assertIn('重启后',service.residency_hint('zh'))

    def test_identity_guide_does_not_change_the_immutable_core(self):
        from agentj import main_identity
        main_identity.verify_core()
        for lang in ('zh','en'):
            text=main_identity.prompt({'language':lang})
            self.assertIn('/docs/multi-seat/',text)

    def test_phone_reminder_preserves_linux_onboarding_and_is_once_on_mac(self):
        import asyncio
        import test_p72_onboarding as onboarding_fixture

        async def check(platform):
            fixture = onboarding_fixture.Serve()
            fixture.setUp()
            try:
                with mock.patch.object(service, "platform", return_value=platform), mock.patch.object(service, "_linger", return_value="no"):
                    await fixture._pair(7, onboarding_fixture.PC)
                    session = await fixture._pair(8, onboarding_fixture.PHONE)
                    def system_lines():
                        return [t["reply"]["text"] for t in fixture.host.hist.page(limit=50)[0] if t["src"].get("k") == "sys"]
                    lines = system_lines()
                    self.assertEqual(sum("也连上了" in line for line in lines), 1)
                    self.assertEqual(sum("这台电脑重启后，请登录一次" in line for line in lines), 1 if platform == "macos" else 0)
                    self.assertEqual(len(lines), 2 if platform == "macos" else 1,
                                     "Linux linger belongs in docs/doctor, not an extra onboarding chat")
                    await fixture.host.on_ready(session, None)
                    await asyncio.sleep(.05)
                    self.assertEqual(system_lines(), lines, "reconnect must not repeat the reboot reminder")
            finally:
                fixture.tearDown()

        for platform in ("linux", "macos"):
            with self.subTest(platform=platform):
                asyncio.run(check(platform))

    def test_three_uid_helper_installations_do_not_overlap(self):
        import importlib.util
        modules=[]
        for uid in (21001,21002,21003):
            spec=importlib.util.spec_from_file_location('agentj._p94_helper_'+str(uid),eh.__file__)
            m=importlib.util.module_from_spec(spec)
            with mock.patch('os.getuid',return_value=uid):spec.loader.exec_module(m)
            modules.append(m)
        for name in ('HELPER','KEYS','STATE','SUDOERS'):
            self.assertEqual(len({getattr(m,name) for m in modules}),3)
        for mine in modules:
            for other in modules:
                if mine is not other:
                    for name in ('HELPER','KEYS','STATE','SUDOERS'):
                        self.assertNotIn(getattr(other,name),mine.uninstall_script())
