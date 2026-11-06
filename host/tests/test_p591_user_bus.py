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
                for tool in ('Bash', 'exec_command'):
                    inp={'command': cmd}
                    requirement=policy_registry.main_requirement(tool, inp)
                    self.assertEqual(requirement, 'uv', tool)
                    self.assertEqual(approval_level.full_access_keep(tool, inp, v, requirement=requirement), 'uv', tool)
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



class RealFence(unittest.TestCase):
    """V2 trusted configuration proofs; historical method IDs, no attack execution.

    Only synthetic directories/aliases are discovered. Read-only mount intent and
    ordering are verified with mutation controls. Actual kernel enforcement remains
    a separate positive runtime qualification obligation.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p591-config-', dir=TEST_TMP)
        self.d = pathlib.Path(self.tmp.name)
        self.home = self.d/'home'; self.home.mkdir()
        self.units = self.home/'.config/systemd/user'; self.units.mkdir(parents=True)
        (self.home/'.local/share/systemd').mkdir(parents=True)
        self.st = State(self.d/'state'); self.st.init(); self.st.perm_dir.mkdir(exist_ok=True)
        self.env = {'HOME':str(self.home), 'XDG_CONFIG_HOME':str(self.home/'.config'),
                    'XDG_DATA_HOME':str(self.home/'.local/share'),
                    'XDG_CONFIG_DIRS':str(self.home/'etc'), 'XDG_DATA_DIRS':str(self.home/'share')}

    def tearDown(self):
        self.tmp.cleanup()

    def argv(self, env=None, runtime='', manager=()):
        with patch.object(fence,'control_sockets',return_value=[]), \
             patch.object(fence,'_manager_unit_paths',return_value=list(manager)), \
             patch.object(fence,'other_states',return_value=[]):
            return fence.bwrap_argv(self.st,str(self.home),home=str(self.home),runtime=runtime,environ=env or self.env)

    def protected(self, argv, path):
        path = str(path)
        triples = [argv[i:i+3] for i in range(len(argv))]
        pairs = [argv[i:i+2] for i in range(len(argv))]
        self.assertTrue(['--ro-bind',os.path.realpath(path),path] in triples or
                        (['--tmpfs',path] in pairs and ['--remount-ro',path] in pairs), path)
        # A later writable bind must never reverse the protected mount.
        index = max(i for i in range(len(argv)) if argv[i:i+2] in (['--remount-ro',path],['--tmpfs',path]) or
                    argv[i:i+3] == ['--ro-bind',os.path.realpath(path),path])
        self.assertFalse(any(argv[i] == '--bind' and argv[i+2] == path for i in range(index+1,len(argv)-2)))

    def negative(self, argv, path):
        # Mutate a vector only, never execute the weakened configuration.
        broken = argv.copy(); path = str(path)
        for i in range(len(broken)-2):
            if broken[i:i+3] == ['--ro-bind',os.path.realpath(path),path]:
                broken[i] = '--bind'; break
        else:
            i = next(i for i in range(len(broken)-1) if broken[i:i+2] == ['--remount-ro',path])
            broken[i] = '--synthetic-invalid'
        with self.assertRaises(AssertionError): self.protected(broken,path)

    def test_unit_writes_blocked_but_work_files_and_permission_socket_survive(self):
        unit = self.units/'test.service'; unit.write_text('synthetic')
        external = self.home/'linked.service'; external.write_text('synthetic')
        (self.units/'alias.service').symlink_to(external)
        ordinary = self.home/'ordinary.txt'; ordinary.write_text('synthetic')
        a = self.argv()
        for path in (self.units.parent,external): self.protected(a,path); self.negative(a,path)
        self.assertIn(['--bind',str(self.st.perm_dir),str(self.st.perm_dir)], [a[i:i+3] for i in range(len(a))])
        self.assertIn(['--bind',str(ordinary),str(ordinary)], [a[i:i+3] for i in range(len(a))])
        self.assertEqual(unit.read_text(),'synthetic')

    def test_missing_and_symlinked_unit_roots_cannot_modify_host(self):
        import shutil
        shutil.rmtree(self.units.parent)
        missing = self.units.parent
        a = self.argv(); self.protected(a,missing); self.negative(a,missing)
        self.assertFalse(missing.exists(), 'generator must not create host mountpoints')
        target = self.home/'target'; target.mkdir()
        missing.symlink_to(target,target_is_directory=True)
        a = self.argv(); self.protected(a,missing); self.negative(a,missing)
        self.assertTrue(missing.is_symlink(), 'configuration generation leaves fixture aliases unchanged')

    def test_external_directory_dropins_and_nested_aliases_are_read_only(self):
        external, nested = self.home/'external', self.home/'nested'
        external.mkdir(); nested.mkdir()
        (self.units/'test.service.d').symlink_to(external,target_is_directory=True)
        (external/'nested').symlink_to(nested,target_is_directory=True)
        a = self.argv()
        for path in (external,nested): self.protected(a,path); self.negative(a,path)

    def test_systemd_search_environment_roots_are_read_only(self):
        extra = [self.home/'etc/systemd/user',self.home/'share/systemd/user',self.home/'override']
        for path in extra: path.mkdir(parents=True)
        env = {**self.env,'SYSTEMD_UNIT_PATH':str(extra[-1])+':'}
        a = self.argv(env)
        for path in extra: self.protected(a,path); self.negative(a,path)
        self.assertNotIn('relative',fence.service_paths(str(self.home),env))

    def test_live_manager_paths_not_present_in_launch_environment_are_read_only(self):
        extra = self.home/'manager-only'; extra.mkdir()
        self.assertNotIn(str(extra),self.env.values())
        a = self.argv(manager=[str(extra)])
        self.protected(a,extra); self.negative(a,extra)
        self.assertIn(['--bind',str(extra.parent),str(extra.parent)], [a[i:i+3] for i in range(len(a))])

    def test_nonwritable_unit_root_cannot_be_written_replaced_or_chmodded(self):
        self.units.parent.chmod(0o555)
        try:
            a = self.argv(); self.protected(a,self.units.parent); self.negative(a,self.units.parent)
            # Read-only mount intent cannot rely solely on source Unix mode bits.
            self.assertEqual(self.units.parent.stat().st_mode & 0o777,0o555)
        finally: self.units.parent.chmod(0o755)

    def test_raw_bus_socket_cannot_connect(self):
        runtime = self.d/'runtime'; runtime.mkdir(); (runtime/'systemd').mkdir()
        env = {**self.env,'XDG_RUNTIME_DIR':str(runtime),'DBUS_SESSION_BUS_ADDRESS':'unix:path='+str(runtime/'bus'),
               'SYSTEMD_BUS_ADDRESS':'unix:path='+str(runtime/'systemd/private')}
        a = self.argv(env,runtime=str(runtime))
        def verify(a):
            pairs = [a[i:i+2] for i in range(len(a))]
            self.assertIn(['--tmpfs',str(runtime)],pairs)
            for name in ('DBUS_SESSION_BUS_ADDRESS','SYSTEMD_BUS_ADDRESS'):
                self.assertIn(['--unsetenv',name],pairs)
            self.assertNotIn(['--bind',str(runtime),str(runtime)], [a[i:i+3] for i in range(len(a))])
            self.assertEqual(fence._RUNTIME_BACK,())
        verify(a)
        broken = a.copy(); i = next(i for i in range(len(a)-1) if a[i:i+2] == ['--tmpfs',str(runtime)])
        broken[i] = '--synthetic-invalid'
        with self.assertRaises(AssertionError): verify(broken)
        with self.assertRaises(AssertionError): verify(a + ['--bind',str(runtime),str(runtime)])
        for name in ('DBUS_SESSION_BUS_ADDRESS','SYSTEMD_BUS_ADDRESS'):
            broken = a.copy(); i = broken.index(name); broken[i-1] = '--synthetic-invalid'
            with self.assertRaises(AssertionError): verify(broken)
