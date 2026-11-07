"""Offline native-OS contract; no native binary or manager is accessed."""
import unittest
from agentj import native_scheduler_control as native


class NativePOSIX(unittest.TestCase):
    def test_native_no_version_probe(self):
        for platform in ('linux', 'darwin'):
            calls = []
            class Contract:
                def run(self, argv, stdin=None):
                    calls.append(argv)
                    if argv == ['crontab', '-V']:
                        raise native.Refused('backend_unavailable')
                    return b'@daily /bin/fixture\n'
            backend = native.Backend(platform=platform, native_crontab=Contract())
            self.assertEqual(backend.crontab_bytes(), b'@daily /bin/fixture\n')
            self.assertEqual(calls, [['crontab', '-l']])

import os
import stat
from types import SimpleNamespace
from unittest.mock import patch, Mock
from agentj import native_crontab_os as contract
import test_native_user_crontab369 as legacy


class FakeOS:
    def __init__(self, platform='linux'):
        self.magic = b'\x7fELF' if platform == 'linux' else b'\xcf\xfa\xed\xfe'
        self.paths = {}
        for i, path in enumerate(('/', '/usr', '/usr/bin', '/usr/sbin', '/usr/bin/crontab')):
            mode = stat.S_IFREG | 0o2755 if path.endswith('crontab') else stat.S_IFDIR | 0o755
            self.paths[path] = SimpleNamespace(st_dev=1, st_ino=i+1, st_mode=mode, st_uid=0,
                st_gid=0, st_size=200, st_mtime_ns=1, st_ctime_ns=1)
        self.redirects = {}
        self.opened = []

    def lstat(self, path):
        if path not in self.paths:
            raise FileNotFoundError(path)
        return self.paths[path]

    def realpath(self, path):
        return self.redirects.get(path, path)

    def open(self, path, flags):
        self.opened.append(path)
        self.fdstat = SimpleNamespace(**vars(self.lstat(path)))
        return 42

    def patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        for name, value in (('lstat', self.lstat), ('open', self.open),
                            ('fstat', lambda fd: self.fdstat), ('read', lambda fd, n: self.magic),
                            ('close', lambda fd: None)):
            stack.enter_context(patch.object(contract.os, name, value))
        stack.enter_context(patch.object(contract.os.path, 'realpath', self.realpath))
        return stack


class BinaryIdentity(unittest.TestCase):
    def test_platform_binary_magic_and_privilege_modes(self):
        for platform in ('linux', 'darwin'):
            fs = FakeOS(platform)
            for mode in (0o755, 0o4755, 0o2755):
                fs.paths['/usr/bin/crontab'].st_mode = stat.S_IFREG | mode
                with fs.patches():
                    self.assertEqual(contract.NativeCrontab(platform).executable(), '/usr/bin/crontab')

    def test_untrusted_metadata_and_wrappers(self):
        for kind in ('owner', 'writable', 'directory', 'notexec', 'script', 'parent', 'redirect', 'missing'):
            fs = FakeOS()
            st = fs.paths['/usr/bin/crontab']
            if kind == 'owner': st.st_uid = 1000
            if kind == 'writable': st.st_mode |= 0o020
            if kind == 'directory': st.st_mode = stat.S_IFDIR | 0o755
            if kind == 'notexec': st.st_mode = stat.S_IFREG | 0o644
            if kind == 'script': fs.magic = b'#!/b'
            if kind == 'parent': fs.paths['/usr'].st_mode |= 0o002
            if kind == 'redirect': fs.redirects['/usr/bin/crontab'] = '/tmp/crontab'
            if kind == 'missing': del fs.paths['/usr/bin/crontab']
            with self.subTest(kind=kind), fs.patches(), self.assertRaises((contract.UnsafeBinary, OSError)):
                contract.NativeCrontab('linux').executable()

    def test_pinned_identity_rechecked_before_spawn(self):
        for field in ('st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_mtime_ns', 'st_ctime_ns', 'st_size', 'st_dev'):
            fs = FakeOS()
            with self.subTest(field=field), fs.patches():
                binary = contract.NativeCrontab('linux')
                binary.executable()
                st = fs.paths['/usr/bin/crontab']
                setattr(st, field, getattr(st, field) + 1)
                with patch('subprocess.Popen', side_effect=AssertionError('spawn forbidden')):
                    with self.assertRaisesRegex(native.Refused, 'backend_binary_untrusted'):
                        native._crontab_run(['crontab', '-'], b'fixture\n', native_crontab=binary)

    def test_linux_sbin_and_closed_darwin_path(self):
        for platform in ('linux', 'darwin'):
            fs = FakeOS(platform)
            fs.paths['/usr/sbin/crontab'] = fs.paths.pop('/usr/bin/crontab')
            with fs.patches():
                if platform == 'linux':
                    self.assertEqual(contract.NativeCrontab(platform).executable(), '/usr/sbin/crontab')
                else:
                    with self.assertRaises(contract.UnsafeBinary):
                        contract.NativeCrontab(platform).executable()

    def test_trusted_symlink_and_retarget(self):
        fs = FakeOS()
        fs.paths['/usr/sbin/crontab'] = SimpleNamespace(**vars(fs.paths['/usr/bin/crontab']))
        fs.paths['/usr/bin/crontab'].st_mode = stat.S_IFLNK | 0o777
        fs.redirects['/usr/bin/crontab'] = '/usr/sbin/crontab'
        with fs.patches():
            binary = contract.NativeCrontab('linux')
            self.assertEqual(binary.executable(), '/usr/sbin/crontab')
            fs.redirects['/usr/bin/crontab'] = '/tmp/crontab'
            with self.assertRaises(contract.UnsafeBinary): binary.executable()

    def test_open_identity_swap_and_banner_cannot_authorize(self):
        fs = FakeOS()
        with fs.patches():
            def swapped(fd):
                st = SimpleNamespace(**vars(fs.fdstat))
                st.st_ino += 1
                return st
            with patch.object(contract.os, 'fstat', swapped), patch('subprocess.Popen', side_effect=AssertionError('must not spawn')):
                backend = native.Backend(platform='linux')
                with self.assertRaisesRegex(native.Refused, 'backend_binary_untrusted'):
                    backend.crontab_bytes()

    def test_unsupported_and_path_not_consulted(self):
        with self.assertRaises(contract.UnsafeBinary): contract.NativeCrontab('freebsd')
        fs = FakeOS()
        with fs.patches(), patch.dict(os.environ, {'PATH': '/tmp/evil'}):
            self.assertEqual(contract.NativeCrontab('linux').executable(), '/usr/bin/crontab')
            self.assertEqual(set(fs.opened), {'/usr/bin/crontab'})


class NativeController(unittest.TestCase):
    def exercise(self, platform, method='test_exact_restore_preserves_every_non_job_byte'):
        fixture = legacy.UserCrontab(method)
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fs = FakeOS(platform)
        binary = contract.NativeCrontab(platform)
        calls = []
        # Process simulation still traverses the real OS identity verifier on each
        # list/install. Only file metadata and process IO are fake.
        def process(argv, stdin=None, **kwargs):
            with fs.patches():
                self.assertEqual(binary.executable(), '/usr/bin/crontab')
            calls.append(argv)
            if argv == ['crontab', '-V']:
                raise native.Refused('backend_unavailable')
            return fixture.runner(argv, stdin)
        with patch.object(native, '_crontab_run', process):
            fixture.backend = native.Backend(platform=platform, home=str(fixture.root),
                native_crontab=binary, transaction_directory=str(fixture.root))
            fixture.ctl = native.Controller(fixture.st, fixture.backend)
            fixture.target['platform'] = platform
            fixture.refresh()
            getattr(fixture, method)()
        self.assertNotIn(['crontab', '-V'], calls)

    def test_vixie_roundtrip_no_version(self): self.exercise('linux')
    def test_darwin_roundtrip_no_version(self): self.exercise('darwin')

    def test_native_transaction_guards(self):
        for platform in ('linux', 'darwin'):
            for method in ('test_observed_external_change_before_install',
                           'test_changed_restore_never_overwrites_external_edit',
                           'test_two_controllers_contend_even_with_different_state_roots',
                           'test_cancel_after_journal_before_install',
                           'test_bounds_and_parse_errors'):
                with self.subTest(platform=platform, method=method):
                    self.exercise(platform, method)


class BoundedNativeRunner(legacy.FixedRunner):
    def exercise(self, *args, **kwargs):
        # Explicit fake verifier keeps inherited IO tests entirely offline and
        # preserves their logical argv assertions without changing product checks.
        with patch.object(contract.NativeCrontab, 'executable', return_value='crontab'):
            return super().exercise(*args, **kwargs)

    def test_absolute_spawn(self):
        with patch.object(contract.NativeCrontab, 'executable', return_value='/usr/bin/crontab'), patch('subprocess.Popen', side_effect=OSError('fixture')) as spawn:
            with self.assertRaises(native.Refused): native._crontab_run(['crontab', '-l'])
        self.assertEqual(spawn.call_args.args[0], ['/usr/bin/crontab', '-l'])

    def test_timeout_kills_process(self):
        process = Mock()
        process.stdin = None
        process.poll.return_value = None
        selector = Mock()
        selector.__enter__ = Mock(return_value=selector)
        selector.__exit__ = Mock(return_value=False)
        selector.get_map.return_value = {1: True}
        with patch.object(contract.NativeCrontab, 'executable', return_value='/usr/bin/crontab'), patch('subprocess.Popen', return_value=process), patch('selectors.DefaultSelector', return_value=selector), patch.object(native.os, 'set_blocking'), patch('time.monotonic', side_effect=[0, 6]):
            with self.assertRaisesRegex(native.Refused, 'backend_timeout'):
                native._crontab_run(['crontab', '-l'])
        process.kill.assert_called_once()

class AppleSpoolContract(unittest.TestCase):
    def test_generated_outer_headers_preserve_user_comments_and_control_id(self):
        import io
        fixture = legacy.UserCrontab('test_exact_restore_preserves_every_non_job_byte')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        body = (b'# DO NOT EDIT THIS FILE - edit the master and reinstall.\n'
                b'# (- installed on USER COMMENT)\n# (Cron version -- USER COMMENT)\n'
                b'MAILTO=fixture\n0 1 * * * /bin/job first\n0 2 * * * /bin/job second\n')
        def outer(number):
            return (b'# DO NOT EDIT THIS FILE - edit the master and reinstall.\n'
                    + ('# (- installed on OS generation %d)\n' % number).encode()
                    + b'# (Cron version -- OS)\n')
        spool = outer(0) + body
        installed = []
        fs = FakeOS('darwin')
        binary = contract.NativeCrontab('darwin')
        def process(argv, stdin=None, **kwargs):
            nonlocal spool
            with fs.patches():self.assertEqual(binary.executable(), '/usr/bin/crontab')
            self.assertNotEqual(argv, ['crontab', '-V'])
            if argv == ['crontab', '-l']:
                # Apple's copy_file skips its three outer comment lines, not
                # any matching comments inside the user's installed body.
                stream = io.BytesIO(spool)
                for _ in range(3):
                    first = stream.read(1)
                    if first != b'#':return first + stream.read()
                    stream.readline()
                return stream.read()
            self.assertEqual(argv, ['crontab', '-'])
            self.assertTrue(fixture.ctl.journal())
            installed.append(stdin)
            spool = outer(len(installed)) + stdin
            return b''
        with patch.object(native, '_crontab_run', process):
            fixture.backend = native.Backend(platform='darwin', native_crontab=binary,
                                            transaction_directory=str(fixture.root))
            fixture.ctl = native.Controller(fixture.st, fixture.backend)
            fixture.target.update(platform='darwin', id='crontab:5')
            fixture.refresh()
            rows = native.systasks.scan_cron(run=lambda argv: process(argv).decode())
            self.assertEqual(rows[0]['control_id'], 'crontab:5')
            fixture.ctl.apply(fixture.target, lambda: None)
            changed = fixture.backend.crontab_bytes()
            before_lines, after_lines = body.splitlines(keepends=True), changed.splitlines(keepends=True)
            self.assertEqual(after_lines[:4] + after_lines[5:], before_lines[:4] + before_lines[5:])
            self.assertTrue(after_lines[4].startswith(b'# agentj-disabled:'))
            fixture.refresh('restore')
            fixture.ctl.apply(fixture.target, lambda: None)
            self.assertEqual(fixture.backend.crontab_bytes(), body)
            self.assertEqual(spool, outer(2) + body)
            self.assertEqual(installed[-1], body)
            self.assertEqual(fixture.ctl.journal(), {})
