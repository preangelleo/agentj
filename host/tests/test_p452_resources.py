"""Resource protection is effective or visibly degraded; work never inherits transport priority."""
import _hermetic
import asyncio
import threading
import time
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agentj import resource_policy as rp, service, doctor


class Policy(unittest.TestCase):
    def test_both_service_templates_protect_transport_without_privileged_nice(self):
        for text in (service.unit_text('p452', ['/bin/true'], {}), service.rescue_unit_text('p452-rescue', ['/bin/true'], {})):
            for setting in rp.TRANSPORT_UNIT:
                self.assertIn(setting, text)
            self.assertNotIn('Nice=-', text)
            self.assertNotIn('MemoryMax=128M', text)
        for render in (service.plist_bytes, service.rescue_plist_bytes):
            data = plistlib.loads(render('net.agentj.test', ['/bin/true'], {}, '/tmp/test.log'))
            self.assertEqual(data['ProcessType'], 'Interactive')
            self.assertFalse(data.get('LowPriorityIO', False))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux priority inheritance')
    def test_exec_preserves_stdio_exit_argv_and_descendant_priority_with_minimal_path(self):
        # -I ignores PYTHONPATH; payload execution also works with a minimal PATH.
        payload = 'import os,sys,json,subprocess; child=subprocess.check_output([sys.executable,"-c","import os;print(os.getpriority(os.PRIO_PROCESS,0))"]);print(json.dumps([sys.argv[1:],sys.stdin.read(),os.getpriority(os.PRIO_PROCESS,0),int(child)]));sys.exit(17)'
        env = {**os.environ, 'PATH': '/nonexistent'}
        r = subprocess.run(rp.work_argv([sys.executable, '-c', payload, 'a b', '$(unchanged)']),
                           env=env, input='hello', text=True, capture_output=True)
        self.assertEqual(r.returncode, 17, r.stderr)
        argv, stdin, nice, childnice = json.loads(r.stdout)
        self.assertEqual(argv, ['a b', '$(unchanged)'])
        self.assertEqual(stdin, 'hello')
        self.assertGreaterEqual(nice, 10)
        self.assertGreaterEqual(childnice, 10)
        self.assertGreaterEqual(os.getpriority(os.PRIO_PROCESS, 0), 0)

    def test_work_never_executes_system_helpers_from_work_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'unexpected-helper'
            fake = root / 'busctl'
            fake.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ran")\n')
            fake.chmod(0o755)
            r = subprocess.run(rp.work_argv([sys.executable, '-c', 'print("payload")']),
                               env={**os.environ, 'PATH': str(root) + os.pathsep + os.defpath},
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), 'payload')
            self.assertFalse(marker.exists(), 'work PATH executed outside the fence')

    def test_scope_failure_does_not_execute_payload_or_lose_work(self):
        with patch.object(rp.os, 'access', return_value=True), patch.object(rp.subprocess, 'run', side_effect=subprocess.TimeoutExpired('busctl', 3)):
            self.assertFalse(rp._work_scope())
        with patch.object(rp.os, 'access', return_value=True), patch.object(rp.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(rp._work_scope())

    def test_scope_binds_to_host_and_requests_low_weights_before_exec(self):
        pid = os.getpid()
        reads = ['0::/user.slice/user@1000.service/app.slice/agentj-test.service\n', f'0::/user.slice/user@1000.service/app.slice/agentj-work-{pid}.scope\n']
        with patch.object(rp.os, 'access', return_value=True), patch.object(rp.Path, 'read_text', side_effect=reads), patch.object(rp.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(rp._work_scope())
        cmd = run.call_args.args[0]
        for name, signature, value in [('CPUWeight', 't', '20'), ('IOWeight', 't', '20')]:
            at = cmd.index(name)
            self.assertEqual(cmd[at:at+3], [name, signature, value])
        for name in ('BindsTo', 'After'):
            at = cmd.index(name)
            self.assertEqual(cmd[at:at+4], [name, 'as', '1', 'agentj-test.service'])

    def test_mac_work_is_background_before_exec(self):
        with patch.object(rp.sys, 'platform', 'darwin'), patch.object(rp.os, 'setpriority'), patch.object(rp.os, 'access', return_value=True), patch.object(rp.os, 'execvpe') as execute:
            rp.exec_work(['/bin/echo', 'hello'])
            self.assertEqual(execute.call_args.args[:2], ('/usr/bin/taskpolicy', ['/usr/bin/taskpolicy', '-b', '/bin/echo', 'hello']))

    def test_doctor_does_not_confuse_configured_properties_with_effective_controllers(self):
        with patch.object(rp, '_properties', return_value={'ControlGroup': '/does-not-exist', 'MainPID': '0', 'CPUWeight': '1000', 'MemoryLow': str(256*1024**2)}):
            res = rp.inspect_unit('p452.service')
        self.assertFalse(res['effective'])
        self.assertIn('cpu.weight', res['missing'])
        with patch.object(rp, 'status', return_value={'effective': False, 'detail': 'missing cpu/io/memory'}):
            row = doctor.check_resources({'installed': True})
        self.assertEqual(row['status'], 'warn')
        self.assertIn('nice', row['hint'])

    def test_memory_pressure_and_unknown_never_claim_guaranteed_uptime(self):
        for state in ("pressured", "unknown", "observed_available"):
            with patch.object(rp, "memory_state", return_value=state), patch.object(rp, "status", return_value={"effective": True, "detail": "configured"}):
                row = doctor.check_resources({"installed": True})
            self.assertEqual(row["status"], "ok" if state == "observed_available" else "warn")
            self.assertIn("no uptime guarantee", row["summary"])
        with patch.object(rp.sys, "platform", "linux"), patch.object(Path, "read_text", return_value="MemTotal: 1000000 kB\nMemAvailable: 1000 kB\n"):
            self.assertEqual(rp.memory_state(), "pressured")
        with patch.object(Path, "read_text", side_effect=OSError):
            self.assertEqual(rp.memory_state(), "unknown")

    def test_memory_state_observes_local_cgroup_limit_before_global_free_memory(self):
        def read(p):
            return {"/proc/self/cgroup":"0::/p499.scope\n", "/sys/fs/cgroup/p499.scope/memory.current":"180000000",
                    "/sys/fs/cgroup/p499.scope/memory.high":"128000000", "/sys/fs/cgroup/p499.scope/memory.max":"256000000"}[str(p)]
        with patch.object(rp.sys, "platform", "linux"), patch.object(Path, "read_text", read):
            self.assertEqual(rp.memory_state(), "pressured")

    def test_mac_doctor_checks_loaded_job_not_only_plist(self):
        svc = {'installed': True, 'kind': 'launchd', 'name': 'net.agentj.host'}
        with patch.object(service, 'platform', return_value='macos'), patch.object(service, '_launchctl', return_value=subprocess.CompletedProcess([], 0, 'spawn type = interactive (4)\n')):
            self.assertTrue(rp.status(svc)['effective'])
        with patch.object(service, 'platform', return_value='macos'), patch.object(service, '_launchctl', return_value=subprocess.CompletedProcess([], 0, 'spawn type = background (5)\n')):
            self.assertFalse(rp.status(svc)['effective'])


class EventLoop(unittest.IsolatedAsyncioTestCase):
    async def test_slow_fence_probe_does_not_delay_phone_ping(self):
        from test_l1 import _state, _host, _ready, Phone
        from agentj import agent, fence
        with tempfile.TemporaryDirectory() as temp:
            st = _state(temp)
            sent = []
            host = _host(st, sent)
            session = _ready(host, Phone(st))
            runner = agent.Agent(host, {'dir':temp, 'fence':True, '_workflow_ceo':True})
            entered, release = threading.Event(), threading.Event()
            threads = []
            def slow_probe(*args):
                threads.append(threading.current_thread() is threading.main_thread())
                entered.set()
                release.wait(2)
                return None
            with patch.object(fence, 'problem', slow_probe), patch.object(fence, 'wrap', side_effect=lambda st, argv, *a, **k: argv):
                task = asyncio.create_task(runner.authorized_launch_argv(['/bin/true']))
                try:
                    for _ in range(100):
                        if entered.is_set():break
                        await asyncio.sleep(.01)
                    self.assertTrue(entered.is_set())
                    self.assertFalse(task.done(), 'sandbox probe blocked the event loop')
                    await host.on_p33(session, 'ping', {'r':'probe'})
                    self.assertEqual(sent[-1][1], {'t':'pong','r':'probe'})
                finally:
                    release.set()
                    await task
            self.assertFalse(threads[0], 'first sandbox probe must run off-loop')

    async def test_slow_ark_view_does_not_delay_phone_ping(self):
        from test_l1 import _state, _host, _ready, Phone
        from agentj import ark_plan_fix
        with tempfile.TemporaryDirectory() as temp:
            st = _state(temp)
            sent = []
            host = _host(st, sent)
            session = _ready(host, Phone(st))
            entered, release = threading.Event(), threading.Event()
            def slow_view(st):
                entered.set()
                release.wait(2)
                return {}
            with patch.object(ark_plan_fix, 'view', slow_view):
                task = asyncio.create_task(host.on_p33(session, 'ark_plan_get', {'r': 'slow'}))
                try:
                    for _ in range(100):
                        if entered.is_set():
                            break
                        await asyncio.sleep(.01)
                    self.assertTrue(entered.is_set())
                    self.assertFalse(task.done())
                    await host.on_p33(session, 'ping', {'r': 'ping'})
                    self.assertEqual(sent[-1][1], {'t': 'pong', 'r': 'ping'})
                    self.assertFalse(task.done(), 'ping waits for a blocked disk view')
                finally:
                    release.set()
                    await task


if __name__ == '__main__':
    unittest.main()
