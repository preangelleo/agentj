"""Current-user Cronie transaction contract. Every OS command is injected."""
import base64
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from agentj import native_scheduler_control as native, systasks
from test_native_scheduler_control369 import FakeState


class UserCrontab(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.raw = b'# original header\r\nMAILTO=fixture\r\n\r\n0 1 * * * /bin/job first\r\n# keep\n@daily /bin/job second\n'
        self.data = self.raw
        self.calls = []
        self.runner_hook = None
        self.backend = native.Backend(platform='linux', home=tmp.name, uid=os.getuid(),
                                      crontab_run=self.runner, transaction_directory=tmp.name)
        self.st = FakeState(tmp.name)
        self.ctl = native.Controller(self.st, self.backend)
        self.target = dict(platform='linux', source='cron', scope='user', id='crontab:4', snapshot='0'*64, op='disable')
        self.refresh()

    def runner(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        if self.runner_hook:
            self.runner_hook(argv, stdin)
        if argv == ['crontab', '-V']:
            return b'crontab (cronie) 1.7.2\n'
        if argv == ['crontab', '-l']:
            self.assertIsNone(stdin)
            return self.data
        self.assertEqual(argv, ['crontab', '-'])
        self.assertIsInstance(stdin, bytes)
        self.assertTrue(self.ctl.journal())
        self.data = stdin
        return b''

    def refresh(self, op=None):
        if op:
            self.target['op'] = op
        self.target['snapshot'] = self.backend.snapshot(self.target)['snapshot']

    def test_exact_restore_preserves_every_non_job_byte(self):
        self.ctl.apply(self.target, lambda: None)
        before, after = self.raw.splitlines(keepends=True), self.data.splitlines(keepends=True)
        self.assertEqual(before[:3] + before[4:], after[:3] + after[4:])
        self.assertTrue(after[3].endswith(before[3]))
        self.assertTrue(self.ctl.describe(dict(src='cron', id='job', control_id='crontab:4', scope='user'))['supported'])
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.data, self.raw)
        self.assertEqual(self.ctl.journal(), {})
        self.assertEqual([a for a, stdin in self.calls if stdin is not None], [['crontab', '-'], ['crontab', '-']])

    def test_observed_external_change_before_install(self):
        write = self.st.write_private
        def race(path, data):
            write(path, data)
            self.data += b'# external\n'
        self.st.write_private = race
        with self.assertRaisesRegex(native.Refused, 'changed'):
            self.ctl.apply(self.target, lambda: None)
        self.assertFalse(any(stdin is not None for a, stdin in self.calls))
        self.assertTrue(self.ctl.journal())

    def test_changed_restore_never_overwrites_external_edit(self):
        self.ctl.apply(self.target, lambda: None)
        self.data += b'# third party\n'
        changed = self.data
        self.refresh('restore')
        with self.assertRaisesRegex(native.Refused, 'changed'):
            self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.data, changed)

    def test_postcheck_detects_changed_install_and_retains_original(self):
        run = self.backend.crontab_run
        def corrupt(argv, stdin=None):
            result = run(argv, stdin)
            if stdin is not None:
                self.data += b'# external\n'
            return result
        self.backend.crontab_run = corrupt
        with self.assertRaisesRegex(native.Refused, 'verify_failed'):
            self.ctl.apply(self.target, lambda: None)
        record = self.ctl.journal()[self.ctl.key(self.target)]
        self.assertEqual(base64.b64decode(record['bytes']), self.raw)

    def test_cancel_after_journal_before_install(self):
        live = [True]
        write = self.st.write_private
        def cancel(path, data):
            write(path, data)
            live[0] = False
        def guard():
            if not live[0]:
                raise native.Refused('no_device')
        self.st.write_private = cancel
        with self.assertRaisesRegex(native.Refused, 'no_device'):
            self.ctl.apply(self.target, guard)
        self.assertEqual(self.data, self.raw)
        self.st.write_private = write
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.data, self.raw)
        self.assertEqual(self.ctl.journal(), {})

    def test_recover_after_install_before_completion(self):
        live = [True]
        def hook(argv, stdin):
            if stdin is not None:
                live[0] = False
        self.runner_hook = hook
        def guard():
            if not live[0]:
                raise native.Refused('no_device')
        with self.assertRaisesRegex(native.Refused, 'no_device'):
            self.ctl.apply(self.target, guard)
        self.assertNotEqual(self.data, self.raw)
        self.runner_hook = None
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.data, self.raw)

    def test_two_controllers_contend_even_with_different_state_roots(self):
        import fcntl
        other = self.root / 'other'
        other.mkdir()
        ctl = native.Controller(FakeState(other), self.backend)
        fd = os.open(self.root / ('agentj-crontab-' + str(os.geteuid()) + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for controller in (self.ctl, ctl):
                with self.assertRaisesRegex(native.Refused, 'busy'):
                    controller.apply(self.target, lambda: None)
        finally:
            os.close(fd)
        self.assertEqual(self.data, self.raw)

    def test_second_line_blocked_until_restore(self):
        self.ctl.apply(self.target, lambda: None)
        target = dict(self.target, id='crontab:6')
        target['snapshot'] = self.backend.snapshot(target)['snapshot']
        with self.assertRaisesRegex(native.Refused, 'source_has_pending_control'):
            self.ctl.apply(target, lambda: None)

    def test_unknown_backend_refused(self):
        self.backend.crontab_run = lambda *a, **kw: b'other cron 1.0\n'
        with self.assertRaisesRegex(native.Refused, 'backend_semantics_unknown'):
            self.backend.snapshot(self.target)

    def test_bounds_and_parse_errors(self):
        for raw in (b'x' * 262145, b'\n' * 10001, b'not cron\n', b'0 1 * * * /job\\\n', b'0 1 * * * /job', b'\x00\n', b'\xff\n'):
            self.data = raw
            with self.subTest(raw=raw[:30]), self.assertRaises((native.Refused, UnicodeError)):
                self.backend.snapshot(self.target)

    def test_display_id_compatibility_and_physical_binding(self):
        rows = systasks.scan_cron(lambda a: '# keep\n@daily /bin/daily\n@daily /bin/daily\n')
        self.assertEqual([r['id'] for r in rows], ['daily', 'daily'])
        self.assertEqual([r['control_id'] for r in rows], ['crontab:2', 'crontab:3'])
        row = dict(rows[0], control_id='crontab:4', scope='user')
        self.assertEqual(self.ctl.describe(row)['target']['id'], 'crontab:4')

    def test_no_raw_content_in_public_metadata(self):
        self.ctl.apply(self.target, lambda: None)
        self.assertNotIn('/bin/job', json.dumps([self.backend.snapshot(self.target), self.ctl.restore_rows([])]))

    def test_failed_restore_can_be_retried_without_losing_disabled_identity(self):
        self.ctl.apply(self.target, lambda: None)
        self.refresh('restore')
        runner = self.backend.crontab_run
        def fail(argv, stdin=None):
            if stdin is not None:
                raise OSError('fixture install failure')
            return runner(argv, stdin)
        self.backend.crontab_run = fail
        with self.assertRaises(OSError):
            self.ctl.apply(self.target, lambda: None)
        self.backend.crontab_run = runner
        self.assertEqual(self.ctl.describe(dict(src='cron', id='job', control_id='crontab:4'))['target']['op'], 'restore')
        self.refresh('restore')
        self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.data, self.raw)

    def test_lock_symlink_fails_before_any_install(self):
        lock = self.root / ('agentj-crontab-' + str(os.geteuid()) + '.lock')
        lock.symlink_to(self.root / 'absent')
        with self.assertRaises(OSError):
            self.ctl.apply(self.target, lambda: None)
        self.assertEqual(self.data, self.raw)

    def test_fixed_runner_rejects_arbitrary_argv_without_spawn(self):
        with patch('subprocess.run', side_effect=AssertionError('must not spawn')):
            for argv in (['crontab', '-u', 'root', '-'], ['sh', '-c', 'job'], ['crontab', '-e']):
                with self.assertRaises(native.Refused):
                    native._crontab_run(argv, b'fixture')


class FixedRunner(unittest.TestCase):
    def exercise(self, output=b'fixture\n', code=0, stdin=None):
        process = Mock()
        process.stdout.fileno.return_value = 10
        process.stderr.fileno.return_value = 11
        process.stdin = Mock() if stdin is not None else None
        if process.stdin is not None:
            process.stdin.fileno.return_value = 12
        process.wait.return_value = code
        process.poll.return_value = code
        chunks = {10: [output, b''], 11: [b'']}
        class Selector:
            def __init__(self): self.entries = {}
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def register(self, stream, flags, data): self.entries[stream] = data
            def unregister(self, stream): del self.entries[stream]
            def get_map(self): return self.entries
            def select(self, timeout):
                return [(SimpleNamespace(fileobj=s, data=d), None) for s, d in list(self.entries.items())]
        argv = ['/usr/bin/crontab', '-'] if stdin is not None else ['/usr/bin/crontab', '-l']
        trusted = SimpleNamespace(executable=lambda: argv[0])
        with patch('subprocess.Popen', return_value=process) as spawn, patch('selectors.DefaultSelector', Selector), patch.object(native.os, 'set_blocking'), patch.object(native.os, 'read', side_effect=lambda fd, n: chunks[fd].pop(0)), patch.object(native.os, 'write', side_effect=lambda fd, data: len(data)):
            result = native._crontab_run(['crontab', argv[1]], stdin, native_crontab=trusted)
            self.assertEqual(spawn.call_args.args[0], argv)
            return result

    def test_bounded_read_and_stdin_install_without_os_io(self):
        self.assertEqual(self.exercise(), b'fixture\n')
        self.assertEqual(self.exercise(output=b'', stdin=b'fixture\n'), b'')

    def test_excess_output_fails_closed(self):
        with self.assertRaisesRegex(native.Refused, 'crontab_limits'):
            self.exercise(output=b'x' * 262145)

    def test_nonzero_exit_is_not_a_snapshot(self):
        with self.assertRaisesRegex(native.Refused, 'backend_unavailable'):
            self.exercise(code=1)
