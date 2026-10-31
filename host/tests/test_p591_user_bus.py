"""P591: user-manager authority never crosses the Agent fence."""
import _hermetic  # noqa: F401
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agentj import fence, danger, approval_level, policy_registry
from agentj.state import State
from _testtmp import TEST_TMP


class Policy(unittest.TestCase):
    def test_manager_writes_always_require_pairing(self):
        for cmd in ('systemctl --user start x.service', 'systemctl --user enable x.timer',
                    'systemctl --user link /tmp/x.service', 'systemd-run --user true',
                    'env systemctl --user restart x.service'):
            with self.subTest(cmd=cmd):
                v = danger.classify('Bash', {'command': cmd})
                self.assertIn('cred.service', v.rules)
                self.assertEqual(approval_level.harness_floor('Bash', {'command': cmd}, v), 'hooks')
                self.assertEqual(policy_registry.main_requirement('Bash', {'command': cmd}), 'uv')
                self.assertTrue(set(danger.shared_keys(v, {'approvals': 'native'})) & danger.ALWAYS_CARD)

    def test_unit_roots_cannot_replace_the_private_proc_or_filesystem_root(self):
        for root in ('/proc', '/', '/agentj-p591-absent-root/not-created'):
            with self.subTest(root=root), patch.object(fence,'service_paths',return_value=[root]):
                with self.assertRaises(OSError): fence.service_mounts('/unused')

    def test_unit_writes_and_read_control(self):
        for p in ('~/.config/systemd/user/x.timer', '~/.local/share/systemd/user/x.service'):
            v = danger.classify('Write', {'file_path': p, 'content': 'x'})
            self.assertIn('cred.service', v.rules)
        for cmd in ('systemctl --user status x.service', 'systemctl --user list-timers', 'echo hello'):
            self.assertNotIn('cred.service', danger.classify('Bash', {'command': cmd}).rules)

    def test_actual_manager_paths_are_queried_with_fixed_read_and_fail_closed(self):
        import json
        with tempfile.TemporaryDirectory(prefix='aj-p591-manager-',dir=TEST_TMP) as tmp:
            runtime=pathlib.Path(tmp); (runtime/'bus').touch()
            env={'XDG_RUNTIME_DIR':str(runtime)}
            good=subprocess.CompletedProcess([],0,json.dumps({'type':'as','data':['/extra/user units']}),'')
            with patch.dict(os.environ, {'AGENTJ_TEST':'0'}), patch.object(fence.subprocess,'run') as run:
                run.side_effect=lambda args, **kw: (subprocess.CompletedProcess([],0,json.dumps({'type':'b','data':[True]}),'')
                                                     if 'NameHasOwner' in args else run.return_value)
                run.return_value=good
                self.assertEqual(fence._manager_unit_paths(env),['/extra/user units'])
                args=run.call_args.args[0]
                self.assertEqual(args[-4:],['org.freedesktop.systemd1','/org/freedesktop/systemd1',
                                           'org.freedesktop.systemd1.Manager','UnitPath'])
                run.return_value=subprocess.CompletedProcess([],0,json.dumps({'type':'as','data':['relative']}),'')
                with self.assertRaises(OSError): fence._manager_unit_paths(env)


    def test_absent_manager_and_relative_override_contract(self):
        with tempfile.TemporaryDirectory(prefix='aj-p591-nomanager-',dir=TEST_TMP) as tmp:
            env={'XDG_RUNTIME_DIR':tmp,'SYSTEMD_UNIT_PATH':'relative:'}
            with patch.dict(os.environ,{'AGENTJ_TEST':'0'}), patch.object(fence.os,'getuid',return_value=99999999), \
                 patch.object(fence.subprocess,'run') as run:
                self.assertEqual(fence._manager_unit_paths(env),[])
                run.assert_not_called()
            with patch.object(fence.os,'getcwd',return_value=tmp):
                self.assertIn(str(pathlib.Path(tmp)/'relative'),fence.service_paths(tmp,env))

    @unittest.skipUnless(shutil.which('dbus-daemon') and shutil.which('busctl'), 'temporary session bus tools')
    def test_real_session_bus_without_systemd_manager_is_supported(self):
        import time
        with tempfile.TemporaryDirectory(prefix='aj-p591-dbus-',dir=TEST_TMP) as tmp:
            path=pathlib.Path(tmp)/'bus'
            proc=subprocess.Popen(['dbus-daemon','--session','--nofork','--address=unix:path='+str(path)],
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if path.exists(): break
                    time.sleep(0.01)
                self.assertTrue(path.exists())
                with patch.dict(os.environ,{'AGENTJ_TEST':'0'}), patch.object(fence.os,'getuid',return_value=99999999):
                    self.assertEqual(fence._manager_unit_paths({'XDG_RUNTIME_DIR':tmp}),[])
            finally:
                proc.terminate(); proc.wait(timeout=5)



@unittest.skipUnless(sys.platform == 'linux' and shutil.which('bwrap'), 'Linux bubblewrap')
class RealFence(unittest.TestCase):
    def test_unit_writes_blocked_but_work_files_and_permission_socket_survive(self):
        with tempfile.TemporaryDirectory(prefix='aj-p591-unit-', dir=TEST_TMP) as tmp:
            d = pathlib.Path(tmp); home = d/'home'; home.mkdir()
            for rel in ('.config/systemd/user', '.local/share/systemd/user'):
                (home/rel).mkdir(parents=True)
            unit = home/'.config/systemd/user/test.service'; unit.write_text('original')
            external = home/'linked.service'; external.write_text('original')
            (unit.parent/'alias.service').symlink_to(external)
            st = State(d/'state'); st.init(); st.perm_dir.mkdir(exist_ok=True)
            sock = st.perm_dir/'test.sock'
            server = socket.socket(socket.AF_UNIX); server.bind(str(sock)); server.listen()
            try:
                env = {**os.environ, 'HOME': str(home), 'XDG_CONFIG_HOME': str(home/'.config'),
                       'XDG_DATA_HOME': str(home/'.local/share')}
                argv = fence.bwrap_argv(st, str(home), home=str(home), environ=env)
                code = """import pathlib,socket,sys
home,unit,sock=map(pathlib.Path,sys.argv[1:])
for p in (unit,unit.parent/'new.service',unit.parent/'alias.service',home/'linked.service'):
 try: p.write_text('bad')
 except OSError: pass
 else: raise SystemExit('ESCAPE: unit write')
try: unit.parent.rename(unit.parent.with_name('replaced'))
except OSError: pass
else: raise SystemExit('ESCAPE: unit rename')
(home/'ordinary.txt').write_text('ok')
s=socket.socket(socket.AF_UNIX); s.connect(str(sock)); s.close()
assert unit.read_text()=='original'
print('BLOCKED units; work and permission socket usable')"""
                r = subprocess.run(argv+[sys.executable, '-c', code, str(home),str(unit),str(sock)],
                                   capture_output=True,text=True,env=env,timeout=10)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertIn('permission socket usable',r.stdout)
                self.assertEqual((home/'ordinary.txt').read_text(),'ok')
                self.assertEqual(unit.read_text(),'original')
            finally: server.close()

    def test_missing_and_symlinked_unit_roots_cannot_modify_host(self):
        for alias in (False, True):
            with self.subTest(alias=alias), tempfile.TemporaryDirectory(prefix='aj-p591-alias-',dir=TEST_TMP) as tmp:
                d=pathlib.Path(tmp); home=d/'home'; home.mkdir()
                (home/'.config').mkdir(); (home/'.local/share').mkdir(parents=True)
                (home/'.config/sibling').write_text('old')
                target=d/'unit-target'; target.mkdir()
                if alias: (home/'.config/systemd').symlink_to(target,target_is_directory=True)
                st=State(d/'state');st.init();st.perm_dir.mkdir(exist_ok=True)
                env={**os.environ,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),
                     'XDG_DATA_HOME':str(home/'.local/share')}
                code="""import pathlib,sys
h,target=map(pathlib.Path,sys.argv[1:])
for p in (h/'.config/systemd', h/'.local/share/systemd'):
 try: (p/'bad.service').write_text('bad')
 except OSError: pass
 else: raise SystemExit('ESCAPE unit creation')
try: (h/'.config/systemd').unlink()
except OSError: pass
else: raise SystemExit('ESCAPE alias removal')
if (h/'.config/systemd').is_symlink():
 raise SystemExit('unit alias must be pinned')
(h/'.config/sibling').write_text('updated')
print('BLOCKED missing and alias roots')"""
                r=subprocess.run(fence.bwrap_argv(st,str(home),home=str(home),environ=env)+
                                 [sys.executable,'-c',code,str(home),str(target)],env=env,
                                 capture_output=True,text=True,timeout=10)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual((home/'.config/sibling').read_text(),'updated')
                self.assertFalse((target/'bad.service').exists())
                self.assertFalse((home/'.local/share/systemd').exists(), 'sandbox must not create host mountpoints')
                if not alias: self.assertFalse((home/'.config/systemd').exists())

    def test_external_directory_dropins_and_nested_aliases_are_read_only(self):
        with tempfile.TemporaryDirectory(prefix='aj-p591-dropin-', dir=TEST_TMP) as tmp:
            d=pathlib.Path(tmp); home=d/'home'; home.mkdir()
            units=home/'.config/systemd/user'; units.mkdir(parents=True)
            (home/'.local/share/systemd').mkdir(parents=True)
            external=home/'external-dropins'; external.mkdir()
            nested=home/'nested-dropins'; nested.mkdir()
            (external/'x.conf').write_text('original')
            (nested/'y.conf').write_text('original')
            (units/'x.service.d').symlink_to(external,target_is_directory=True)
            (external/'nested').symlink_to(nested,target_is_directory=True)
            st=State(d/'state'); st.init(); st.perm_dir.mkdir(exist_ok=True)
            env={**os.environ,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),
                 'XDG_DATA_HOME':str(home/'.local/share')}
            code="""import pathlib,sys
home=pathlib.Path(sys.argv[1])
for p in (home/'external-dropins/x.conf',home/'nested-dropins/y.conf',
          home/'.config/systemd/user/x.service.d/x.conf',
          home/'.config/systemd/user/x.service.d/nested/y.conf'):
 try: p.write_text('CHANGED')
 except OSError: pass
 else: raise SystemExit('ESCAPE: external directory target writable')
for p in (home/'external-dropins/new.conf',home/'nested-dropins/new.conf'):
 try: p.write_text('CHANGED')
 except OSError: pass
 else: raise SystemExit('ESCAPE: external dropin creation')
try: (home/'external-dropins').rename(home/'replaced')
except OSError: pass
else: raise SystemExit('ESCAPE: target parent replaced')
(home/'ordinary.txt').write_text('ok')
print('BLOCKED: external and nested directory dropins')"""
            r=subprocess.run(fence.bwrap_argv(st,str(home),home=str(home),environ=env)+
                             [sys.executable,'-c',code,str(home)],env=env,
                             capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual((external/'x.conf').read_text(),'original')
            self.assertEqual((nested/'y.conf').read_text(),'original')
            self.assertEqual((home/'ordinary.txt').read_text(),'ok')

    def test_systemd_search_environment_roots_are_read_only(self):
        with tempfile.TemporaryDirectory(prefix='aj-p591-search-', dir=TEST_TMP) as tmp:
            d=pathlib.Path(tmp); home=d/'home'; home.mkdir()
            (home/'.config/systemd/user').mkdir(parents=True)
            (home/'.local/share/systemd/user').mkdir(parents=True)
            extra=[home/'extra-config/systemd/user',home/'extra-data/systemd/user',home/'override-units',
                   home/'extra-config-2/systemd/user',home/'extra-data-2/systemd/user']
            for root in extra:
                root.mkdir(parents=True); (root/'probe.service').write_text('original')
            env={**os.environ,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),
                 'XDG_DATA_HOME':str(home/'.local/share'),
                 'XDG_CONFIG_DIRS':str(home/'extra-config')+':relative:'+str(home/'extra-config-2'),
                 'XDG_DATA_DIRS':str(home/'extra-data')+'::'+str(home/'extra-data-2')}
            if shutil.which('systemd-analyze'):
                paths=subprocess.check_output(['systemd-analyze','--user','unit-paths'],env=env,text=True).splitlines()
                self.assertTrue(all(str(root) in paths for root in (extra[0],extra[1],extra[3],extra[4])))
            env['SYSTEMD_UNIT_PATH']=str(extra[2])+':'
            if shutil.which('systemd-analyze'):
                paths=subprocess.check_output(['systemd-analyze','--user','unit-paths'],env=env,text=True).splitlines()
                self.assertTrue(all(str(root) in paths for root in extra))
            st=State(d/'state');st.init();st.perm_dir.mkdir(exist_ok=True)
            code="""import pathlib,sys
for name in sys.argv[1:]:
 p=pathlib.Path(name)/'probe.service'
 try: p.write_text('CHANGED')
 except OSError: pass
 else: raise SystemExit('ESCAPE: configured user unit search path writable')
print('BLOCKED: all configured unit search roots')"""
            r=subprocess.run(fence.bwrap_argv(st,str(home),home=str(home),environ=env)+
                             [sys.executable,'-c',code,*map(str,extra)],env=env,
                             capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertTrue(all((root/'probe.service').read_text()=='original' for root in extra))

    def test_live_manager_paths_not_present_in_launch_environment_are_read_only(self):
        with tempfile.TemporaryDirectory(prefix='aj-p591-livepaths-',dir=TEST_TMP) as tmp:
            d=pathlib.Path(tmp); home=d/'home'; home.mkdir()
            (home/'.config/systemd').mkdir(parents=True)
            (home/'.local/share/systemd').mkdir(parents=True)
            root=home/'manager-only'; root.mkdir(); (root/'probe.service').write_text('original')
            st=State(d/'state');st.init();st.perm_dir.mkdir(exist_ok=True)
            env={**os.environ,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),
                 'XDG_DATA_HOME':str(home/'.local/share')}
            with patch.object(fence,'_manager_unit_paths',return_value=[str(root)]):
                argv=fence.bwrap_argv(st,str(home),home=str(home),environ=env)
            code="""import pathlib,sys
try: pathlib.Path(sys.argv[1]).write_text('CHANGED')
except OSError: print('BLOCKED manager-only path')
else: raise SystemExit('ESCAPE manager-only path')"""
            r=subprocess.run(argv+[sys.executable,'-c',code,str(root/'probe.service')],env=env,
                             capture_output=True,text=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual((root/'probe.service').read_text(),'original')

    def test_nonwritable_unit_root_cannot_be_written_replaced_or_chmodded(self):
        for source, action in ((source,action) for source in ('default','search-dir') for action in ('write','replace','chmod')):
            with self.subTest(source=source,action=action), tempfile.TemporaryDirectory(prefix='aj-p591-0555-',dir=TEST_TMP) as tmp:
                d=pathlib.Path(tmp); home=d/'home'; home.mkdir()
                root=home/'.config/systemd' if source=='default' else home/'extra/systemd/user'
                units=root/'user' if source=='default' else root
                units.mkdir(parents=True)
                (home/'.local/share/systemd').mkdir(parents=True)
                unit=units/'probe.service'; unit.write_text('original'); unit.chmod(0o644)
                root.chmod(0o555)
                st=State(d/'state');st.init();st.perm_dir.mkdir(exist_ok=True)
                env={**os.environ,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),
                     'XDG_DATA_HOME':str(home/'.local/share'),'XDG_CONFIG_DIRS':str(home/'extra')}
                code="""import pathlib,sys
root=pathlib.Path(sys.argv[1]); action=sys.argv[2]; unit=pathlib.Path(sys.argv[3])
try:
 if action=='write': unit.write_text('CHANGED')
 elif action=='chmod': root.chmod(0o755)
 else:
  root.rename(root.with_name('moved'))
  unit.parent.mkdir(parents=True)
  unit.write_text('REPLACED')
except OSError: print('BLOCKED '+action)
else: raise SystemExit('ESCAPE 0555 root '+action)"""
                r=subprocess.run(fence.bwrap_argv(st,str(home),home=str(home),environ=env)+
                                 [sys.executable,'-c',code,str(root),action,str(unit)],env=env,
                                 capture_output=True,text=True,timeout=10)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(unit.read_text(),'original')

    def test_raw_bus_socket_cannot_connect(self):
        with tempfile.TemporaryDirectory(prefix='aj-p591-', dir=TEST_TMP) as tmp:
            d = pathlib.Path(tmp)
            home, rt = d/'home', d/'runtime'
            home.mkdir(); rt.mkdir(); (rt/'systemd').mkdir()
            st = State(d/'state'); st.init(); st.perm_dir.mkdir(exist_ok=True)
            endpoints = [rt/'bus', rt/'systemd/private']
            servers = []
            try:
                for endpoint in endpoints:
                    server = socket.socket(socket.AF_UNIX); server.bind(str(endpoint)); server.listen()
                    servers.append(server)
                    # Positive control: this exact live socket accepts before fencing.
                    client = socket.socket(socket.AF_UNIX); client.connect(str(endpoint)); client.close()
                env = {**os.environ, 'HOME': str(home), 'XDG_RUNTIME_DIR': str(rt)}
                argv = fence.bwrap_argv(st, str(home), home=str(home), runtime=str(rt), environ=env)
                code = '''import socket,sys
for path in sys.argv[1:]:
 s=socket.socket(socket.AF_UNIX)
 try: s.connect(path)
 except OSError: continue
 raise SystemExit('ESCAPE: raw user bus reachable')
print('BLOCKED: raw Python sockets')'''
                r = subprocess.run(argv+[sys.executable, '-c', code, *map(str,endpoints)], env=env,
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('BLOCKED', r.stdout)
            finally:
                for server in servers: server.close()
