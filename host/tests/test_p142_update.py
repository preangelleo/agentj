"""P142 item 4 (A12 + B15): an explicit `update apply` never restarts a busy serve, writes the phones' marker last and rolls
back to the cached old wheel when the upgrade fails.

(a) The recovery worker (manager job) waits on `upgrade_ready` — no sudo/secret card, no approval/question, no turn, no
    task — up to READY_LIMIT, then restarts; an older serve without that command answers from `status`.
(b) The marker is written by the worker right before the restart, `from` taken from apply's pending record.
(c) The new service never comes up → the cached old wheel goes back (hash re-checked), the marker is removed, the old
    service is installed again; the record says `rolled_back`.
(d) apply: a failed version check / service hand-off rolls back in place; without a cached wheel it says `unavailable`."""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402  P202: fixtures on tmpfs, not the system disk
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import __version__, service, service_recovery, update  # noqa: E402
from agentj.state import State  # noqa: E402

INFO = {'kind': 'uv', 'where': '/fake', 'legacy': False}


def wheel(st, version):
    p = st.root / 'upgrade-cache' / f'agentj-{version}-py3-none-any.whl'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'old-wheel-bytes')
    p.with_suffix('.verified.json').write_text(json.dumps({'sha256': hashlib.sha256(b'old-wheel-bytes').hexdigest()}))
    return p


class Runner:
    """Fake installer: `--version` answers whatever was installed last."""
    def __init__(self, current='9.9.9', fail=()):
        self.calls, self.current, self.fail = [], current, fail

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[-1] == '--version':
            return subprocess.CompletedProcess(argv, 0, f'agentj {self.current}', '')
        if any(f in argv for f in self.fail):
            return subprocess.CompletedProcess(argv, 5, '', '')
        if any(str(a).endswith('.whl') for a in argv):
            self.current = __version__                         # the old wheel went back
        return subprocess.CompletedProcess(argv, 0, '', '')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p142up-')
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'HOME': self.tmp.name, 'AGENTJ_SERVICE_NAME': 'aj.p142.test'})
        env.start()
        self.addCleanup(env.stop)
        self.st = State(Path(self.tmp.name) / 'st')
        self.st.init()


class WaitReady(Base):
    def test_waits_while_busy_then_idle(self):
        answers = [{'ok': True, 'ready': False}] * 3 + [{'ok': True, 'ready': True}]
        seen = []
        res = service_recovery.wait_ready(self.st, limit=60, poll=0, sleep=lambda s: None,
                                          ctl=lambda st, req, t: seen.append(req['cmd']) or answers[len(seen) - 1])
        self.assertEqual((res, len(seen)), ('idle', 4))

    def test_never_idle_gives_up_at_the_limit(self):
        res = service_recovery.wait_ready(self.st, limit=0.2, poll=0.05, ctl=lambda st, req, t: {'ok': True, 'ready': False})
        self.assertEqual(res, 'timeout')

    def test_not_running_does_not_wait(self):
        self.assertEqual(service_recovery.wait_ready(self.st, ctl=lambda st, req, t: None), 'not_running')

    def test_older_serve_answers_from_status(self):
        def ctl(st, req, t):
            if req['cmd'] == 'upgrade_ready':
                return {'ok': False, 'error': 'unknown command'}
            return {'ok': True, 'agent_status': state.pop(0), 'asks': 0, 'task_running': None}
        state = ['working', 'idle']
        res = service_recovery.wait_ready(self.st, limit=60, poll=0, sleep=lambda s: None, ctl=ctl)
        self.assertEqual((res, state), ('idle', []))


class RecoveryWorker(Base):
    def test_marker_written_after_wait_with_pending_from(self):
        update.write_pending(self.st, '0.0.1', __version__, INFO, None)
        order = []

        def ready(st):
            order.append(('ready', (st.root / update.UPGRADED).exists()))
            return 'idle'

        def install(st):
            order.append(('install', json.loads((st.root / update.UPGRADED).read_text())['from']))
        rc = service_recovery.run(self.st, __version__, install=install, status=lambda: {'active': 'active'}, ready=ready)
        self.assertEqual(rc, 0)
        self.assertEqual(order, [('ready', False), ('install', '0.0.1')])
        self.assertFalse((self.st.root / update.PENDING).exists())

    def test_new_service_down_rolls_back_to_cached_wheel(self):
        old = wheel(self.st, __version__)
        update.write_pending(self.st, __version__, __version__, INFO, old)   # worker runs the "new" code = __version__
        installs, run = [], Runner(current='9.9.9')
        with patch.object(update, 'new_argv', return_value=['agentj']):
            rc = service_recovery.run(self.st, __version__, timeout=0.3, install=lambda st: installs.append(1),
                                      status=lambda: {'active': 'failed'}, ready=lambda st: 'idle', runner=run)
        self.assertEqual(rc, 1)
        self.assertEqual(service_recovery.read(self.st)['reason'], 'rolled_back')
        self.assertEqual(len(installs), 2, 'new service, then the old one again')
        self.assertTrue(any(str(old) in c for c in run.calls), 'the cached old wheel was installed')
        self.assertFalse((self.st.root / update.UPGRADED).exists(), 'nothing announces the failed version')
        self.assertIn('rolled back', service_recovery.failure_text(service_recovery.read(self.st)))

    def test_down_without_pending_keeps_old_behaviour(self):
        rc = service_recovery.run(self.st, __version__, timeout=0.2, install=lambda st: None,
                                  status=lambda: {'active': 'failed'}, ready=lambda st: 'idle')
        self.assertEqual((rc, service_recovery.read(self.st)['reason']), (1, 'recovery_inactive'))


class ApplyRollback(Base):
    def apply(self, run, old):
        with patch.object(update, 'install_kind', return_value=INFO), patch.object(update, 'new_argv', return_value=['agentj']), \
                patch.object(update, 'writable', return_value=True):
            return update.apply(self.st, '9.9.9', plan_fn=lambda st, info, target: update.commands(info, target),
                                check_fn=lambda: {'status': 'newer', 'latest': '9.9.9'}, run=run, svc_on=True,
                                rollback_fn=lambda st: old)

    def test_version_mismatch_rolls_back(self):
        old = wheel(self.st, __version__)
        run = Runner(current='1.0.0')
        res = self.apply(run, old)
        self.assertEqual((res['reason'], res['rollback']), ('not_upgraded', 'ok'))
        self.assertIn('rollback: ok', update.result_block(res))
        self.assertFalse(any('--deferred' in c for c in run.calls), 'never restarts')

    def test_service_handoff_failure_rolls_back_and_drops_pending(self):
        old = wheel(self.st, __version__)
        res = self.apply(Runner(fail=('--deferred',)), old)
        self.assertEqual((res['reason'], res['rollback'], res['service']), ('service_restart_failed', 'ok', 'failed'))
        self.assertFalse((self.st.root / update.PENDING).exists())
        self.assertFalse((self.st.root / update.UPGRADED).exists())

    def test_no_cached_wheel_says_unavailable(self):
        res = self.apply(Runner(current='1.0.0'), None)
        self.assertEqual(res['rollback'], 'unavailable')

    def test_success_leaves_pending_not_marker(self):
        old = wheel(self.st, __version__)
        res = self.apply(Runner(), old)
        self.assertEqual((res['result'], res['service']), ('ok', 'restart_scheduled'))
        self.assertNotIn('rollback', res)
        self.assertFalse((self.st.root / update.UPGRADED).exists())
        self.assertEqual(update.read_pending(self.st, '9.9.9')['old'], str(old))


class UpgradeReadyCtl(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-p142ur-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        env = patch.dict(os.environ, {'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
                                      'AGENTJ_STATE_DIR': str(home / 'state'), 'PATH': '/usr/bin:/bin'}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.st = State()
        self.st.init()
        from agentj.serve import Host
        self.host = Host(self.st, read_stdin=False)
        self.server = await asyncio.start_unix_server(self.host.on_ctl, path=str(self.st.sock_path))

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_secret_card_blocks_then_clears(self):
        from agentj.names import ctl_call
        self.assertEqual(await asyncio.to_thread(ctl_call, self.st, {'cmd': 'upgrade_ready'}, 5), {'ok': True, 'ready': True})
        self.host.elevate.cards['c1'] = {'kind': 'secret'}
        self.assertEqual(await asyncio.to_thread(ctl_call, self.st, {'cmd': 'upgrade_ready'}, 5), {'ok': True, 'ready': False})
        self.assertEqual(await asyncio.to_thread(service_recovery.wait_ready, self.st, limit=0.3, poll=0.1), 'timeout')
        self.host.elevate.cards.clear()
        self.assertEqual(await asyncio.to_thread(service_recovery.wait_ready, self.st, limit=5, poll=0.1), 'idle')


if __name__ == '__main__':
    unittest.main()
