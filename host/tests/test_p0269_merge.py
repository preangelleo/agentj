"""0.26.9 selfheal x hotfix-0268a2 merge rulings (ADR-0269-merge-rulings).

Ruling 1: the owner's Codex daemon reporting a desktop turn `active` wins over the 0.26.9 file-age guess for plain /clear,
/stop and the automatic watchdog heal (it is another app's turn); the owner's signed 「接管卡住的会话」 (takeover) and
「强制结束这一轮」 (end_turn) still act, and the queued notice keeps counting the wait.
Ruling 2: a Claude config dir other than serve's is accepted only when it is provably the main session's own (its
registered / observed process); otherwise reason `context`.

Stand-ins only: a fake daemon process (the measured 0.166.0 shape, from test_p461f_codex_daemon) holding the rollout,
synthetic ids, an isolated CODEX_HOME / HOME under TEST_TMP."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import claude_live, codex_daemon, humane, turn_heal  # noqa: E402
from agentj.shared_codex import SharedCodexAgent  # noqa: E402
import test_p461f_codex_daemon as d461  # noqa: E402  (the fake daemon script only; its test classes are not collected here)

LINUX = sys.platform.startswith('linux')
SID = '02690269-3333-4269-8269-0269026903e3'
OLD = time.time() - 3 * 3600
RECOVERED = humane.say('selfheal.recovered', 'zh')


@unittest.skipUnless(LINUX, 'holders are read from /proc; the daemon peer pid from SO_PEERCRED')
class DaemonActiveDesktopTurn(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='m', dir=TEST_TMP)      # AF_UNIX path ≤ 108 bytes
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home, self.proj = root / 'c', root / 'p'
        self.proj.mkdir()
        env = patch.dict(os.environ, {'CODEX_HOME': str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.host = Mock()
        self.host.st.agent_session.return_value = SID
        self.host.lang, self.host.preferences, self.host.asks = 'zh', None, {}
        self.host.stopped.return_value = False
        self.host.turn_t0 = time.monotonic() - 3600
        self.host.agent_notice.return_value = None
        self.heal = turn_heal.TurnHeal(self.host)
        self.host.turn_heal = self.heal
        self.calls = []

    def rollout(self):
        d = self.home / 'sessions' / '2026' / '11' / '04'
        d.mkdir(parents=True, exist_ok=True)
        path = d / f'rollout-2026-11-04T11-38-00-{SID}.jsonl'
        items = [{'type': 'session_meta', 'payload': {'id': SID, 'cwd': str(self.proj)}},
                 {'type': 'turn_context', 'payload': {'cwd': str(self.proj), 'approval_policy': 'never',
                  'approvals_reviewer': 'user', 'sandbox_policy': {'type': 'danger-full-access'}}},
                 {'type': 'event_msg', 'payload': {'type': 'task_started'}}]
        path.write_text(''.join(json.dumps(i) + '\n' for i in items))
        os.utime(path, (OLD, OLD))           # silent for 3 h: the 0.26.9 rule alone would call it stuck
        return path

    def daemon(self, path, status):
        p = subprocess.Popen([sys.executable, '-c', d461.FAKE_DAEMON, str(path), str(codex_daemon.socket_path()), status,
                              'codex-tui'], stdout=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        self.assertEqual(p.stdout.readline().strip(), b'ok')
        return p

    def agent(self):
        a = SharedCodexAgent(self.host, {'kind': 'codex', 'dir': str(self.proj), '_workflow_ceo': True,
                                         'shared_session_id': SID})
        a.cfg['_workflow_ceo'] = False
        self.host.agent = a

        async def call(method, params, timeout=60):
            self.calls.append((method, params))
            if method == 'thread/resume':
                return {'thread': {'id': params['threadId']}, 'approvalPolicy': params.get('approvalPolicy', 'never'),
                        'approvalsReviewer': params.get('approvalsReviewer', 'user')}
            if method == 'hooks/list':
                return {'data': []}
            if method == 'turn/start':
                a.turn_done.set()
                return {'turn': {'id': 'turn-after-takeover'}}
            raise AssertionError(method)
        a.call = AsyncMock(side_effect=call)
        a._ready = lambda: a._thread()
        a.remind = AsyncMock()
        a.phone_choice = lambda: {}
        a.read_desktop = Mock()
        a.status = 'working'
        return a

    def notices(self):
        return ' '.join(c.args[0] for c in self.host.agent_notice.call_args_list)

    def selfheal_logs(self):
        return [c.kwargs for c in self.host.st.log.call_args_list if c.args and c.args[0] == 'turn_selfheal']

    async def wait_for(self, cond, secs=10):
        end = time.monotonic() + secs
        while not cond():
            self.assertLess(time.monotonic(), end, 'timed out')
            await asyncio.sleep(0.1)

    async def waiting(self, text='hello'):
        path = self.rollout()
        desk = self.daemon(path, 'active')
        a = self.agent()
        t = asyncio.create_task(a.turn(text))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        return path, desk, a, t

    def test_rule(self):
        path = self.rollout()
        self.daemon(path, 'active')
        self.assertFalse(turn_heal.desktop_stuck(path, (), SID), 'daemon active wins over 3 h of silence')
        self.assertFalse(turn_heal.desktop_stuck(path), 'thread id read from the rollout name')
        f = turn_heal.desktop_facts(path, (), SID)
        self.assertTrue(f['live'])
        self.assertEqual(f['children'], [])

    def test_unreachable_daemon_keeps_the_stuck_rule(self):
        path = self.rollout()
        p = subprocess.Popen([sys.executable, '-c', 'import sys,time\nf=open(sys.argv[1],"rb")\nprint("ok",flush=True)\n'
                              'time.sleep(60)', str(path)], stdout=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        self.assertEqual(p.stdout.readline().strip(), b'ok')
        self.assertIsNone(codex_daemon.thread_status(SID))
        self.assertTrue(turn_heal.desktop_stuck(path, (), SID))

    async def test_watchdog_does_not_auto_recover_a_daemon_active_turn_and_keeps_counting(self):
        self.host.agent_notice.return_value = 9
        page = {'id': 9, 'card': {'queued': True, 'waited_s': 0}}
        self.host.hist.get.side_effect = lambda tid: page if tid == 9 else None
        self.host.hist_update.side_effect = lambda tid, card: page.update(card=card)
        path, desk, a, _ = await self.waiting()
        await self.heal.tick()
        self.assertTrue(self.heal.state['busy'])
        self.assertFalse(self.heal.state['stuck'], self.heal.state)
        self.assertGreaterEqual(self.heal.state['waited_s'], 3500)
        self.assertGreaterEqual(page['card']['waited_s'], 3500, 'the queued notice still says how long it waited')
        self.assertNotIn('stuck', page['card'])
        await asyncio.sleep(1.5)
        self.assertTrue(a.desktop_wait, 'still waits behind the daemon turn')
        self.assertFalse([m for m, _ in self.calls if m == 'turn/start'])
        self.assertFalse(self.selfheal_logs(), 'no automatic heal')
        self.assertIsNone(desk.poll(), 'the daemon is never signalled')
        self.assertIsNone(a.dead_turn)

    async def test_signed_takeover_still_acts_on_a_daemon_active_silent_turn(self):
        path, desk, a, t = await self.waiting('after takeover')
        res = await self.heal.heal('dev1', None, action='takeover')
        await asyncio.wait_for(t, 15)
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['step'], 'release')
        starts = [p for m, p in self.calls if m == 'turn/start']
        self.assertEqual([(p['threadId'], p['input'][0]['text']) for p in starts], [(SID, 'after takeover')])
        self.assertEqual(self.selfheal_logs()[0]['by'], 'owner')
        self.assertIn(RECOVERED, self.notices())
        self.assertIsNone(desk.poll(), 'released for the phone; the daemon process itself is not signalled')

    async def test_signed_end_turn_still_acts(self):
        path, desk, a, t = await self.waiting('after end_turn')
        res = await self.heal.heal('dev1', None, action='end_turn')
        await asyncio.wait_for(t, 15)
        self.assertTrue(res['ok'], res)
        self.assertEqual(len([m for m, _ in self.calls if m == 'turn/start']), 1)

    async def test_plain_stop_still_says_esc_on_a_daemon_active_turn(self):
        path, desk, a, t = await self.waiting()
        a.rollout = path
        await a.halt(clear_queue=True)
        self.assertIsNone(a.dead_turn, 'not treated as ended')
        self.assertIn('Esc', a.halt_note or self.notices())

    async def test_plain_clear_is_refused_on_a_daemon_active_silent_turn(self):
        path = self.rollout()
        self.daemon(path, 'active')
        a = self.agent()
        a.rollout, a.rollout_sid = path, SID
        a.drop_conversation = AsyncMock()
        res = await a.cmd_clear('')
        self.assertIn('没清空', res.text)
        a.drop_conversation.assert_not_called()


class ConfigDirProof(unittest.TestCase):
    """Ruling 2 at the claude_live level (the end-to-end login cases live in test_p0269_login.ClaudeLogin)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='m2-', dir=TEST_TMP))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, True)
        for d in ('cc', 'work', 'owner'):
            (self.tmp / d).mkdir(mode=0o700)
        p = mock.patch.dict(os.environ, {'HOME': str(self.tmp), 'CLAUDE_CONFIG_DIR': str(self.tmp / 'cc')})
        p.start()
        self.addCleanup(p.stop)
        self.env = {'CLAUDE_CONFIG_DIR': str(self.tmp / 'owner')}

    def test_unproven_other_dir_is_context(self):
        exp = claude_live.expected({})
        with patch.object(claude_live, 'auth_status') as status:
            self.assertEqual(claude_live.check(self.env, exp, str(self.tmp / 'work')), 'context')
            self.assertEqual(claude_live.check(self.env, exp, str(self.tmp / 'work'), False), 'context')
        status.assert_not_called()

    def test_proven_safe_dir_is_read_from_its_real_path(self):
        exp = claude_live.expected({})
        with patch.object(claude_live, 'auth_status', return_value={'loggedIn': True, 'authMethod': 'claude.ai'}) as status:
            self.assertIsNone(claude_live.check(self.env, exp, str(self.tmp / 'work'), True))
        self.assertEqual(status.call_args.args[1]['CLAUDE_CONFIG_DIR'], os.path.realpath(self.tmp / 'owner'))
        self.assertEqual(claude_live.context({'CLAUDE_CONFIG_DIR': str(self.tmp / 'cc')}, str(self.tmp / 'work'), True), {},
                         "serve's own config: unchanged")

    def test_proven_but_unsafe_dirs_are_refused(self):
        ws = str(self.tmp / 'work')
        (self.tmp / 'work' / '.cc').mkdir(mode=0o700)
        self.assertIsNone(claude_live.context({'CLAUDE_CONFIG_DIR': str(self.tmp / 'work' / '.cc')}, ws, True))
        (self.tmp / 'open').mkdir()
        (self.tmp / 'open').chmod(0o777)
        self.assertIsNone(claude_live.context({'CLAUDE_CONFIG_DIR': str(self.tmp / 'open')}, ws, True))
        self.assertIsNone(claude_live.context({'CLAUDE_CONFIG_DIR': str(self.tmp / 'missing')}, ws, True))
        os.symlink(self.tmp / 'work' / '.cc', self.tmp / 'link')
        self.assertIsNone(claude_live.context({'CLAUDE_CONFIG_DIR': str(self.tmp / 'link')}, ws, True),
                          'a link into the workspace is judged by its real path')

    def test_proven_needs_the_registered_process_identity(self):
        a = Mock(session={'pid': os.getpid()}, native=None)
        self.assertFalse(claude_live.proven(None, 'owner', a), 'a bare pid (from anywhere) is not proof')
        self.assertFalse(claude_live.proven(None, 'owned', a))
        if LINUX:
            stat = Path(f'/proc/{os.getpid()}/stat').read_text()
            a.session['procStart'] = stat[stat.rfind(')') + 2:].split()[19]
            self.assertTrue(claude_live.proven(None, 'owner', a))
            a.session['procStart'] = '1'
            self.assertFalse(claude_live.proven(None, 'owner', a), 'a start time of another process')


if __name__ == '__main__':
    unittest.main()
