"""Offline regressions at the busy runner / Host boundaries; no real harness."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from agentj import busy, busy_dispatch, busy_native


class Boundary(unittest.TestCase):
    def test_native_parser_uses_known_dispatch_shape_and_rejects_config_override(self):
        text, options = busy_native.parse(['codex', 'exec', '--sandbox=read-only', '--model', 'synthetic', 'work'])
        self.assertEqual((text, options), ('work', {'sandbox': 'read-only', 'model': 'synthetic'}))
        for argv in (['codex', 'exec', '-c', 'sandbox_mode="danger-full-access"', 'work'],
                     ['codex', 'exec', '--yolo', 'work'], ['bash', '-c', 'codex exec work'], ['claude', '-p', 'work'], ['opencode', 'run', 'work'], None, ['codex', 'exec', None]):
            with self.assertRaises(ValueError):
                busy_native.parse(argv)

    def test_native_detach_uses_host_once_and_never_manager(self):
        from agentj import names
        answer = {'ok': True, 'key': 'run:123456abcdef', 'sandbox': 'read-only'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(names, 'ctl_call', return_value=answer) as ipc, \
             patch.object(busy_dispatch, 'write_probe'), patch.object(busy_dispatch.subprocess, 'run') as manager, \
             patch.object(busy_dispatch.subprocess, 'Popen') as fallback:
            self.assertEqual(busy_dispatch.detach(Path(tmp), SimpleNamespace(), ['codex', 'exec', 'work'], answer['key']), 0)
        self.assertEqual(ipc.call_count, 1)
        manager.assert_not_called()
        fallback.assert_not_called()

    def test_ambiguous_host_reply_never_replays(self):
        from agentj import names
        with tempfile.TemporaryDirectory() as tmp, patch.object(names, 'ctl_call', side_effect=names.ServeBusy('timeout')) as ipc, \
             patch.object(busy_dispatch, 'write_probe'), patch.object(busy_dispatch.subprocess, 'Popen') as fallback:
            with self.assertRaisesRegex(RuntimeError, 'outcome unknown'):
                busy_dispatch.detach(Path(tmp), SimpleNamespace(), ['codex', 'exec', 'work'], 'run:123456abcdef')
        self.assertEqual(ipc.call_count, 1)
        fallback.assert_not_called()

    def test_old_report_and_symlink_parent_are_unfinished(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / 'REPORT.md'
            report.write_text('old')
            os.utime(report, (1, 1))
            it = {'report': str(report), 'started': 10}
            self.assertEqual(busy.delivery_status(it, 0)['delivery'], 'unfinished')
            alias = root / 'alias'
            alias.symlink_to(root, target_is_directory=True)
            self.assertEqual(busy.delivery_status({'report': str(alias / 'REPORT.md')}, 0)['delivery'], 'unfinished')

    def test_finished_receipt_retains_schedule_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            it = {'key': 'run:schedule', 'lk': 'schedule', 'session': 'synthetic', 'harness': 'codex',
                  'cwd': tmp, 'report': 'missing', 'started': 10}
            busy.write_finished(tmp, it, None)
            row, = busy.finished_items(tmp)
            self.assertEqual((row['lk'], row['session'], row['harness'], row['exit_code'], row['delivery']),
                             ('schedule', 'synthetic', 'codex', None, 'unfinished'))

    def test_pinned_cpu_increase_is_progress_and_pid_reuse_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / '123'
            proc.mkdir()
            def write(start, cpu):
                fields = ['S'] + ['0'] * 19
                fields[11], fields[19] = str(cpu), start
                (proc / 'stat').write_text('123 (fake process) ' + ' '.join(fields))
            write('42', 1)
            one = busy_dispatch.cpu_snapshot(123, '42', Path(tmp))
            progress = busy_dispatch.Progress(0, one)
            write('42', 200)
            self.assertFalse(progress.observe(600, busy_dispatch.cpu_snapshot(123, '42', Path(tmp))))
            write('43', 300)
            self.assertIsNone(busy_dispatch.cpu_snapshot(123, '42', Path(tmp)))


class HostPolicy(unittest.TestCase):
    def test_kernel_verified_owner_is_capped_and_fence_is_preserved(self):
        from agentj import approval_material, busy_launch
        host = SimpleNamespace(agent=SimpleNamespace(kind='codex', cfg={'kind': 'codex', 'dir': '/synthetic', 'fence': True},
            thread={'sandbox': {'type': 'readOnly'}}))
        with patch.object(approval_material, 'verified_caller_origin', return_value={'verified': True}), \
             patch.object(busy_launch, 'procs', return_value={123: (1, 123, '42', 0)}):
            text, cfg, source, mode, start = busy_native.policy(host,
                {'argv': ['codex', 'exec', '--sandbox', 'danger-full-access', 'work'], 'cwd': '/synthetic'}, 123, os.getuid())
        self.assertEqual((text, mode, start, cfg['fence']), ('work', 'read-only', '42', True))

    def test_bare_json_owner_claim_is_not_authority(self):
        from agentj import approval_material, busy_launch, ctl_auth
        host = SimpleNamespace(agent=SimpleNamespace(kind='codex', cfg={'dir': '/synthetic'}, thread={'sandbox': {'type': 'dangerFullAccess'}}),
                               _agent_pids=lambda: [])
        with patch.object(approval_material, 'verified_caller_origin', return_value=None), \
             patch.object(busy_launch, 'procs', return_value={123: (1, 123, '42', 0)}), \
             patch.object(ctl_auth, 'refusal', return_value='agent'):
            with self.assertRaisesRegex(ValueError, 'unknown source'):
                busy_native.policy(host, {'argv': ['codex', 'exec', 'work'], 'cwd': '/synthetic',
                    'verified': True, 'sandbox': 'danger-full-access'}, 123, os.getuid())


class HostNotice(unittest.TestCase):
    def test_actual_notifier_coalesces_routes_once_and_recovers(self):
        from agentj import background_done, busy_launch
        with tempfile.TemporaryDirectory() as tmp:
            launch = busy_launch.Launch(tmp)
            launch.turn(True, now=100, words='synthetic owner task')
            chain = launch.current_chain()
            launch.jobs['run:test'] = {'who': '', 'label': 'synthetic', 'report': '', 'chain': chain}
            board = busy.Board(tmp)
            board.launch = launch
            board.outside = [{'key': 'run:test', 'since': 100, 'status': 'stalled', 'stalled_at': 700}]
            h = SimpleNamespace(st=SimpleNamespace(root=tmp, log=Mock()), busy=board, agent_notice=Mock())
            notifier = background_done.Notifier(h)
            with patch.object(background_done.time, 'time', return_value=701):
                notifier.collect_stalls()
                notifier.collect_stalls()
                restored = background_done.Notifier(h)
                restored.collect_stalls()
            self.assertEqual(h.agent_notice.call_count, 1)
            self.assertEqual(len(restored.pending), 1)
            board.outside[0]['status'] = 'running'
            restored.collect_stalls()
            self.assertFalse(restored.stalls)
            self.assertFalse(restored.pending)

    def test_unknown_or_other_owner_chain_cannot_route(self):
        from agentj import background_done, busy_launch
        with tempfile.TemporaryDirectory() as tmp:
            board = busy.Board(tmp)
            launch = board.launch = busy_launch.Launch(tmp)
            launch.turn(True, now=100, words='first owner task')
            launch.jobs['run:test'] = {'who': '', 'label': '', 'report': '', 'chain': launch.current_chain()}
            launch.turn(True, now=200, words='different active task')
            board.outside = [{'key': 'run:test', 'status': 'stalled', 'stalled_at': 700}]
            h = SimpleNamespace(st=SimpleNamespace(root=tmp, log=Mock()), busy=board, agent_notice=Mock())
            n = background_done.Notifier(h)
            with patch.object(background_done.time, 'time', return_value=701):
                n.collect_stalls()
            h.agent_notice.assert_not_called()

class HostAdmission(unittest.IsolatedAsyncioTestCase):
    async def test_stopped_host_does_not_register_or_start_native_work(self):
        host = SimpleNamespace(stopped=lambda: True)
        with patch.object(busy_native, 'policy') as policy:
            with self.assertRaisesRegex(ValueError, 'Stop everything'):
                await busy_native.start(host, {}, 123, os.getuid())
        policy.assert_not_called()
        self.assertNotIn('_busy_native', vars(host))

    async def test_host_entry_binds_payload_and_starts_exactly_one_adapter(self):
        from agentj import approval_material, busy_launch
        with tempfile.TemporaryDirectory() as tmp:
            launch = busy_launch.Launch(tmp)
            host = SimpleNamespace(st=SimpleNamespace(root=Path(tmp)), busy=SimpleNamespace(launch=launch), stopped=lambda: False,
                agent=SimpleNamespace(kind='codex', cfg={'kind': 'codex', 'dir': tmp, 'fence': True},
                                      thread={'sandbox': {'type': 'workspaceWrite'}}))
            request = {'key': 'run:123456abcdef', 'argv': ['codex', 'exec', 'synthetic task'], 'cwd': tmp}
            async def fake_adapter(*args):
                pass
            with patch.object(approval_material, 'verified_caller_origin', return_value={'verified': True}), \
                 patch.object(busy_launch, 'procs', return_value={123: (1, 123, '42', 0)}), \
                 patch.object(busy_native, 'run', side_effect=fake_adapter) as adapter:
                first = await busy_native.start(host, request, 123, os.getuid())
                second = await busy_native.start(host, request, 123, os.getuid())
                self.assertEqual(first, second)
                self.assertEqual((first['sandbox'], first['fence']), ('workspace-write', True))
                self.assertTrue(launch.ok(request['key']))
                self.assertFalse(launch.known_process(999, {999: (1, 999, '42', 0)}))
                with self.assertRaisesRegex(ValueError, 'already bound'):
                    await busy_native.start(host, {**request, 'argv': ['codex', 'exec', 'other task']}, 123, os.getuid())
                await host._busy_native[request['key']]['task']
            self.assertEqual(adapter.call_count, 1)

    async def test_estop_cancels_only_its_active_native_adapter(self):
        from agentj import agent_codex, owner_avatar
        with tempfile.TemporaryDirectory() as tmp:
            halt = [False]
            started, ended = asyncio.Event(), asyncio.Event()
            class Adapter:
                def __init__(self, *args, **kwargs):
                    self.proc = None
                async def run_once(self, text):
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        ended.set()
            host = SimpleNamespace(st=SimpleNamespace(root=Path(tmp)), stopped=lambda: halt[0])
            item = busy.item('run:123456abcdef', None, 'ceo', 'synthetic', cwd=tmp, harness='codex',
                             report=str(Path(tmp) / 'MISSING.md'))
            with patch.object(agent_codex, 'CodexAgent', Adapter), patch.object(owner_avatar, '_Host', return_value=host), \
                 patch.object(busy, 'wait_capacity', return_value=True), patch.object(busy, 'HEARTBEAT', .001):
                task = asyncio.create_task(busy_native.run(host, {}, 'synthetic', 'read-only', item))
                await asyncio.wait_for(started.wait(), 1)
                halt[0] = True
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            self.assertTrue(ended.is_set())
            row, = busy.finished_items(tmp)
            self.assertEqual(row['exit_code'], 130)
            self.assertEqual(row['delivery'], 'unfinished')


class SessionMetadata(unittest.TestCase):
    def test_fixed_dated_directory_matches_only_this_thread_and_never_reads_body(self):
        import datetime
        now = datetime.datetime(2026, 11, 7, tzinfo=datetime.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'sessions/2026/11/07'
            directory.mkdir(parents=True)
            target = directory / 'rollout-test-thread.jsonl'
            target.write_text('synthetic transcript must never be read')
            (directory / 'rollout-other-thread.jsonl').write_text('other')
            with patch.object(Path, 'read_text', side_effect=AssertionError('body read')):
                snap = busy_dispatch.codex_session_snapshot(tmp, 'test-thread', now)
            self.assertEqual(len(snap), 1)
            self.assertEqual(snap[0][0], target.name)
            self.assertFalse(busy_dispatch.codex_session_snapshot(tmp, '../escape', now))
