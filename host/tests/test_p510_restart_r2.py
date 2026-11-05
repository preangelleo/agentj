"""#510 r2: real Host.ask policy boundary; tests never execute a restart shell."""
import _hermetic
import asyncio
import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from test_p551_confirm_levels import Base
from agentj import restart_guard, approval_level, approval_policy, danger


class RestartProof(Base):
    def setUp(self):
        super().setUp()
        self.host.ask_ttl = .03

    def proof(self, tool, inp):
        if restart_guard.shape(tool, inp):
            return {**inp, 'command': inp['command'].replace('agentj service', '/installed/bin/agentj service')}

    def test_proven_restart_signed_full_executes_with_one_audit(self):
        self.harness()
        self.host.agent_cfg['session_mode'] = 'shared'
        for tool, key in (('Bash', 'command'), ('exec_command', 'cmd'), ('shell_command', 'command')):
            for cmd in ('agentj service restart', 'systemd-run --user --on-active=45 agentj service restart'):
                with self.subTest(tool=tool,cmd=cmd), mock.patch.object(restart_guard, 'prove', side_effect=lambda t,i: dict(i)), mock.patch.object(restart_guard, 'schedule') as schedule:
                    ans = self.ask({key:cmd}, tool=tool)
                self.assertEqual(ans['behavior'], 'deny')
                self.assertEqual(ans['host_handled'], 'service_restart')
                schedule.assert_called_once()
                self.assertFalse(self.cards())
                self.assertEqual(self.last()['reason'], 'level_harness')

    def test_other_levels_are_buttons_not_uv_and_timeout_does_not_execute(self):
        for lv in ('safe', 'standard'):
            self.host.preferences['approval'] = {'level': lv}
            with self.subTest(lv=lv), mock.patch.object(restart_guard, 'prove', side_effect=lambda t,i: dict(i)), mock.patch.object(restart_guard, 'schedule') as schedule:
                ans = self.ask('agentj service restart')
            self.assertEqual(ans['behavior'], 'deny')
            schedule.assert_not_called()
            card = self.cards()[-1]
            self.assertNotIn('passkey_required', card)
            self.assertIn('短暂断开连接', card['why_scope'])

    def test_real_paired_tap_schedules_once_and_rechecks(self):
        async def go(change=False, allow=True):
            self.host.ask_ttl = 1
            cmd = {'command':'agentj service restart'}
            calls = [dict(cmd), None] if change else [dict(cmd), dict(cmd)]
            with mock.patch.object(restart_guard, 'prove', side_effect=calls), mock.patch.object(restart_guard, 'schedule') as schedule:
                task = asyncio.create_task(self.host.ask('Bash', cmd))
                for _ in range(100):
                    if self.host.asks: break
                    await asyncio.sleep(.001)
                a = next(iter(self.host.asks.values()))
                card = self.host._ask_msg(a, self.s)
                self.assertNotIn('passkey_required', card)
                await self.host._answer(self.s, self.phone.answer(card, allow))
                ans = await task
                self.assertEqual(schedule.call_count, int(allow and not change))
                self.assertEqual('host_handled' in ans, allow and not change)
        asyncio.run(go())
        asyncio.run(go(change=True))
        asyncio.run(go(allow=False))

    def test_unproven_or_dangerous_remains_uv(self):
        self.harness()
        for cmd in ('agentj service restart', 'agentj service restart; cat /tmp/.env',
                    'systemd-run --user -p ExecStartPre=/tmp/code agentj service restart',
                    'agentj service restart; printf x > ~/.claude/settings.json'):
            with self.subTest(cmd=cmd), mock.patch.object(restart_guard, 'prove', return_value=None):
                ans = self.ask(cmd)
            self.assertEqual(ans['behavior'], 'deny')
            self.assertTrue(self.cards()[-1]['passkey_required'])

    def test_exact_shape_rejects_overrides_companions_and_unbounded_timer(self):
        for cmd in ('agentj service restart', 'systemd-run --user --on-active=45 agentj service restart',
                    'systemd-run --user --collect --unit=agentj-restart-test --on-active 45s agentj service restart'):
            self.assertIsNotNone(restart_guard.shape('Bash', {'command':cmd}), cmd)
        for cmd in ('env agentj service restart', 'PATH=/tmp agentj service restart',
                    'agentj service restart; true', 'systemd-run --user agentj service restart',
                    'systemd-run --user --on-active=301 agentj service restart',
                    'systemd-run --user --on-active=45 --unit=arbitrary agentj service restart',
                    'systemd-run --user --on-active=45 --on-active=3 agentj service restart'):
            self.assertIsNone(restart_guard.shape('Bash', {'command':cmd}), cmd)


    def test_finished_permission_or_scheduler_failure_never_retries(self):
        import subprocess
        self.harness()
        async def go():
            gone = asyncio.get_running_loop().create_future()
            gone.set_result(True)
            with mock.patch.object(restart_guard, 'prove', side_effect=lambda t,i: dict(i)), mock.patch.object(restart_guard, 'schedule') as schedule:
                ans = await self.host.ask('Bash', {'command':'agentj service restart'}, gone=gone)
                schedule.assert_not_called()
                self.assertNotIn('host_handled', ans)
        asyncio.run(go())
        for exc in (subprocess.TimeoutExpired('read-only manager', 3), RuntimeError('restart_schedule_unknown_do_not_retry')):
            with mock.patch.object(restart_guard, 'prove', side_effect=lambda t,i: dict(i)), mock.patch.object(restart_guard, 'schedule', side_effect=exc):
                ans = self.ask('agentj service restart')
                self.assertEqual(ans['behavior'], 'deny')
                self.assertIn('restart_schedule_', self.last()['reason'])
                if isinstance(exc, RuntimeError):
                    self.assertIn('请勿重复执行', ans['message'])

    def test_test_process_and_isolated_state_can_never_schedule_real_service(self):
        import os
        with mock.patch('subprocess.run') as run, mock.patch.object(restart_guard, 'prove', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'restart_disabled_in_tests'):
                restart_guard.schedule('Bash', {'command':'agentj service restart'}, self.st)
            with mock.patch.dict(os.environ, {'AGENTJ_TEST':'0'}):
                with self.assertRaisesRegex(RuntimeError, 'restart_not_live_state'):
                    restart_guard.schedule('Bash', {'command':'agentj service restart'}, self.st)
            run.assert_not_called()

class ProofFiles(unittest.TestCase):
    def setUp(self):
        import contextlib
        import tempfile
        from pathlib import Path
        from agentj import service, fence
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(tmp)
        self.exe = self.root / 'bin' / 'agentj'
        self.exe.parent.mkdir()
        self.exe.write_text('#!' + str(self.exe.parent / 'python') + '\nfrom agentj.cli import main\nmain()\n')
        self.exe.chmod(0o755)
        self.unit = self.root / 'agentj.service'
        self.envfile = self.root / 'agentj.env'
        for target, val in (( 'platform', 'linux'), ('name', 'agentj'), ('unit_dir', str(self.root)), ('env_file', str(self.envfile))):
            self.stack.enter_context(mock.patch.object(service, target, return_value=val))
        self.stack.enter_context(mock.patch('sys.executable', str(self.exe.parent / 'python')))
        self.stack.enter_context(mock.patch.object(fence, 'code_ro', return_value=[str(self.root)]))
        self.stack.enter_context(mock.patch.object(restart_guard, '_installed', return_value=True))
        self.unit.write_text(service.unit_text('agentj', [str(self.exe)], {'PATH':'/usr/bin:/bin'}))
        self.props = {'MainPID':str(os.getpid()), 'FragmentPath':str(self.unit), 'DropInPaths':'', 'NeedDaemonReload':'no'}
        self.stack.enter_context(mock.patch.object(restart_guard, '_manager', side_effect=lambda *a,**kw: SimpleNamespace(returncode=0, stdout='' if a[0] == 'show-environment' else '\n'.join(k+'='+v for k,v in self.props.items()))))
        self.seal()

    def seal(self):
        self.stack.enter_context(mock.patch.object(restart_guard, '_INITIAL', restart_guard._snapshot()))

    def prove(self):
        return restart_guard.prove('Bash', {'command':'agentj service restart'})

    def test_canonical_installed_service_proves(self):
        self.assertIsNotNone(self.prove())

    def test_modified_env_after_start_fails(self):
        self.envfile.write_text('PATH=/tmp\n')
        self.assertIsNone(self.prove())

    def test_other_pid_or_pending_reload_fails(self):
        for key, val in (('MainPID','0'), ('NeedDaemonReload','yes'), ('FragmentPath','/tmp/other')):
            old = self.props[key]
            self.props[key] = val
            self.assertIsNone(self.prove())
            self.props[key] = old

    def test_resource_dropin_preserved_and_unsafe_dropin_fails(self):
        p = self.root / 'agentj.service.d' / '50-CPUWeight.conf'
        p.parent.mkdir()
        p.write_text('[Service]\nCPUWeight=100\n')
        self.props['DropInPaths'] = str(p)
        self.seal()
        self.assertIsNotNone(self.prove())
        p.write_text('[Service]\nExecStartPre=/tmp/code\n')
        self.seal()  # even preexisting unsafe overrides must fail
        self.assertIsNone(self.prove())

    def test_unexpected_startup_environment_fails(self):
        from agentj import service
        self.unit.write_text(service.unit_text('agentj', [str(self.exe)], {'PATH':'/usr/bin', 'PYTHONPATH':'/tmp'}))
        self.seal()
        self.assertIsNone(self.prove())

    def test_systemctl_generated_control_resource_dropin_proves_and_is_sealed(self):
        # Isolate the real systemd user.control sibling layout inside this test's temp root.
        parent = self.root / 'systemd'
        unit_root = parent / 'user'
        unit_root.mkdir(parents=True)
        from agentj import service
        self.stack.enter_context(mock.patch.object(service, 'unit_dir', return_value=str(unit_root)))
        self.unit = unit_root / 'agentj.service'
        self.unit.write_text(service.unit_text('agentj', [str(self.exe)], {'PATH':'/usr/bin:/bin'}))
        self.props['FragmentPath'] = str(self.unit)
        p = parent / 'user.control' / 'agentj.service.d' / '50-IOWeight.conf'
        p.parent.mkdir(parents=True)
        p.write_text('# systemd generated header\n# second comment\n[Service]\nIOWeight=100\n')
        self.props['DropInPaths'] = str(p)
        self.seal()
        self.assertIsNotNone(self.prove())
        p.write_text('# changed resource cap\n[Service]\nIOWeight=200\n')
        self.assertIsNone(self.prove())
        self.seal()
        self.assertIsNotNone(self.prove())
        p.write_text('# systemd header\n[Service]\nIOWeight=100\nEnvironment=PYTHONPATH=/tmp\n')
        self.seal()
        self.assertIsNone(self.prove())
