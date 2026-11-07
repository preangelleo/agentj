"""Offline contract regressions for the independent busy runner."""
import _hermetic
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from agentj import busy


class Admission(unittest.TestCase):
    def test_waits_for_actual_capacity(self):
        stop = threading.Event()
        with patch.object(busy, 'capacity', side_effect=[{'can_start': 0}, {'can_start': 1}]) as sample:
            self.assertTrue(busy.wait_capacity(stop, interval=0))
        self.assertEqual(sample.call_count, 2)

    def test_cancelled_queue_never_admits(self):
        stop = threading.Event()
        stop.set()
        with patch.object(busy, 'capacity') as sample:
            self.assertFalse(busy.wait_capacity(stop, interval=0))
        sample.assert_not_called()

    def test_removed_registration_cancels_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(busy.wait_capacity(threading.Event(), Path(tmp) / 'gone', interval=0))


class Delivery(unittest.TestCase):
    def test_report_exit_and_type_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / 'REPORT.md'
            it = {'key': 'run:test', 'cwd': tmp, 'report': 'REPORT.md'}
            for data, code, expected in [(None, 0, 'unfinished'), ('', 0, 'unfinished'),
                                         ('done', 1, 'unfinished'), ('done', None, 'unfinished'),
                                         ('done', 0, 'delivered')]:
                if data is not None:
                    report.write_text(data)
                busy.write_finished(root, it, code)
                rows = busy.finished_items(root)
                self.assertEqual(rows[0]['delivery'], expected)
                self.assertEqual(rows[0]['exit_code'], code)
            report.unlink()
            report.symlink_to(root / 'other')
            (root / 'other').write_text('done')
            busy.write_finished(root, it, 0)
            self.assertEqual(busy.finished_items(root)[0]['delivery'], 'unfinished')

    def test_report_text_never_enters_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'REPORT.md'
            report.write_text('synthetic-private-report-content')
            busy.write_finished(Path(tmp), {'key': 'run:private', 'report': str(report)}, 0)
            receipt = next((busy.items_dir(Path(tmp)) / 'finished').glob('*.json')).read_text()
            self.assertNotIn('synthetic-private-report-content', receipt)
            self.assertEqual(json.loads(receipt)['delivery'], 'delivered')


class Progress(unittest.TestCase):
    def test_idle_is_record_only_and_never_kills(self):
        from agentj.busy_dispatch import Progress
        progress = Progress(now=0, snapshot=())
        self.assertFalse(progress.observe(599, ()))
        self.assertTrue(progress.observe(600, ()))
        self.assertFalse(progress.observe(601, ()))
        self.assertTrue(progress.stalled)
        self.assertEqual(progress.action, 'record-only')

    def test_output_and_tree_changes_reset_idle(self):
        from agentj.busy_dispatch import Progress
        progress = Progress(now=0, snapshot=())
        progress.output(500)
        self.assertFalse(progress.observe(900, ()))
        self.assertFalse(progress.observe(1000, ('new',)))
        self.assertTrue(progress.observe(1600, ('new',)))

    def test_snapshot_is_bounded_and_ignores_symlink_directories(self):
        from agentj.busy_dispatch import tree_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'file').write_text('x')
            (root / 'personal').symlink_to('/nonexistent', target_is_directory=True)
            (root / '.hidden').mkdir()
            self.assertLessEqual(len(tree_snapshot(root, limit=2)), 2)


class Detach(unittest.TestCase):
    def test_systemd_is_one_shot_and_does_not_raise_permissions(self):
        from agentj.busy_dispatch import manager_command
        cmd = manager_command('linux', 'agentj-busy-test', ['/python', '-m', 'agentj'], '/work', '/work/log')
        self.assertIn('Restart=no', cmd)
        self.assertIn('Nice=10', cmd)
        self.assertIn('CPUWeight=20', cmd)
        self.assertNotIn('--scope', cmd)
        self.assertNotIn('--no-sandbox', cmd)

    def test_mac_plist_never_restarts(self):
        from agentj.busy_dispatch import launchd_job
        job = launchd_job('test', ['/python'], '/work', '/work/log')
        self.assertFalse(job['KeepAlive'])
        self.assertTrue(job['RunAtLoad'])
        self.assertEqual(job['Nice'], 10)

    def test_fenced_session_cannot_escape_to_user_manager(self):
        from agentj.busy_dispatch import check_detach
        with patch.dict('os.environ', {'AGENTJ_FENCED': '1'}):
            with self.assertRaises(ValueError):
                check_detach(['/bin/true'])

    def test_bypass_flags_are_rejected(self):
        from agentj.busy_dispatch import check_detach
        for flag in ('--dangerously-skip-permissions', '--dangerously-bypass-approvals-and-sandbox', '--yolo'):
            with self.assertRaises(ValueError):
                check_detach(['codex', flag])


class LaunchBoundaries(unittest.TestCase):
    def test_write_probe_cleans_up(self):
        from agentj.busy_dispatch import write_probe
        with tempfile.TemporaryDirectory() as tmp:
            write_probe(tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_manager_failure_never_replays_via_fallback(self):
        from agentj import busy_dispatch as dispatch
        from types import SimpleNamespace
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'bus').touch()
            with patch.dict('os.environ', {'XDG_RUNTIME_DIR': tmp, 'AGENTJ_FENCED': '0', 'CODEX_SANDBOX_NETWORK_DISABLED': '0'}), \
                 patch.object(dispatch.Path, 'exists', return_value=True), \
                 patch.object(dispatch, 'write_probe'), \
                 patch.object(dispatch.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run, \
                 patch.object(dispatch.subprocess, 'Popen') as fallback:
                with self.assertRaises(RuntimeError):
                    dispatch.detach(Path(tmp), SimpleNamespace(), ['/bin/true'], 'run:123456abcdef')
            fallback.assert_not_called()
            argv = run.call_args.args[0]
            self.assertIn('AGENTJ_STATE_DIR=' + tmp, argv)
            self.assertEqual(argv.count('/bin/true'), 1)

    def test_supervisor_drains_both_streams_and_redacts_receipt_tail(self):
        from agentj.busy_dispatch import supervise
        import subprocess
        import sys
        import io
        # Only a local synthetic Python payload. No manager or harness is called.
        payload = 'import os; os.write(1,b"ordinary-output\\n"); os.write(2,b"ordinary-error\\n")'
        child = subprocess.Popen([sys.executable, '-c', payload], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stdout', out), patch('sys.stderr', err):
            code, tail = supervise(child, tmp, lambda _: self.fail('short command stalled'))
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), 'ordinary-output\n')
        self.assertEqual(err.getvalue(), 'ordinary-error\n')
        self.assertIn('ordinary-output', tail)


class Runner(unittest.TestCase):
    def test_independent_run_uses_priority_wrapper_and_own_child_environment(self):
        from agentj import binaries, peer_origin, names, busy_dispatch, resource_policy
        from types import SimpleNamespace
        import os
        from unittest.mock import Mock
        args = SimpleNamespace(busy_cmd='run', command=['--', '/synthetic/worker'], who='', kind='ceo', label='', report=None)
        child = Mock(pid=123456789)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(busy, '_root', return_value=Path(tmp)), \
             patch.object(busy, 'current_unit', return_value=None), \
             patch.object(busy, 'capacity', return_value={'can_start': 1}), \
             patch.object(names, 'ctl_call'), \
             patch.object(peer_origin, 'pin_name', return_value=None), \
             patch.object(busy.subprocess, 'Popen', return_value=child) as launch, \
             patch.object(busy_dispatch, 'supervise', return_value=(0, 'safe diagnostic')):
            before = os.environ.get('CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS')
            self.assertEqual(busy.cmd_busy(args), 0)
            self.assertEqual(os.environ.get('CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS'), before)
            self.assertEqual(launch.call_args.args[0], resource_policy.work_argv(['/synthetic/worker']))
            self.assertEqual(launch.call_args.kwargs['env']['CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS'], '0')
            self.assertTrue(launch.call_args.kwargs['start_new_session'])
            rows = busy.finished_items(Path(tmp))
            self.assertEqual(rows[0]['exit_code'], 0)
            self.assertEqual(rows[0]['log_tail'], 'safe diagnostic')

    def test_queue_cancellation_does_not_spawn(self):
        from agentj import names
        from types import SimpleNamespace
        args = SimpleNamespace(busy_cmd='run', command=['/synthetic/worker'], who='', kind='ceo', label='', report=None)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(busy, '_root', return_value=Path(tmp)), \
             patch.object(busy, 'current_unit', return_value=None), \
             patch.object(names, 'ctl_call'), \
             patch.object(busy, 'wait_capacity', return_value=False), \
             patch.object(busy.subprocess, 'Popen') as launch:
            self.assertEqual(busy.cmd_busy(args), 130)
        launch.assert_not_called()


class Reservations(unittest.TestCase):
    def test_recent_admission_prevents_load_average_startup_burst(self):
        from agentj.busy_dispatch import reserve_capacity
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'first.json', Path(tmp) / 'second.json'
            first.write_text('{"key":"run:first"}')
            second.write_text('{"key":"run:second"}')
            self.assertTrue(reserve_capacity(first, 1, now=100))
            self.assertFalse(reserve_capacity(second, 1, now=101))
            self.assertTrue(reserve_capacity(second, 1, now=161))


class SessionBoundary(unittest.TestCase):
    def test_unknown_native_policy_is_refused_instead_of_silently_inherited(self):
        from agentj.busy_dispatch import check_detach
        for cmd in (['codex', 'exec', 'data'], ['claude', '-p', 'data'], ['bash', '-c', 'codex exec data']):
            with self.assertRaises(ValueError):
                check_detach(cmd)

    def test_foreground_refuses_bypass_before_start(self):
        from types import SimpleNamespace
        args = SimpleNamespace(busy_cmd='run', command=['codex', '--yolo'])
        with patch.object(busy, '_root', return_value=Path('/synthetic/state')), patch.object(busy.subprocess, 'Popen') as launch:
            self.assertEqual(busy.cmd_busy(args), 2)
        launch.assert_not_called()
