"""0.26.9 selfheal h-watchdog (#461, ADR-0269-watchdog): a stuck turn heals itself without a person.

Customer (Mac, Codex): 「Codex 正在回答，等它说完」 for an hour, /stop and /clear queued behind it, `agentj service restart`
did not help; host.log looped 「desktop turn active / did not finish」 and logged 7 cmd_detect TypeErrors.
Proven here: the queued owner message is delivered into the SAME conversation after the heal (not a side process); a long
tool child is not stuck; a live desktop writer / a process Agent J did not start is never signalled; /clear preempts;
rescue_do end_turn / takeover are owner-signed, logged, never automatic; e-stop wins; cmd_detect no longer raises."""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import humane, reset, rescue_kit, serve, shared_codex, turn_heal  # noqa: E402
from agentj.agent import Agent  # noqa: E402
from agentj.shared_codex import SharedCodexAgent  # noqa: E402

LINUX = sys.platform.startswith('linux')
SID = '02690269-0269-4269-8269-026902690269'
PROFILE = {'type': 'managed', 'file_system': {'type': 'restricted', 'entries': [
    {'path': {'type': 'special', 'value': {'kind': 'root'}}, 'access': 'read'}]}, 'network': 'restricted'}
OLD = time.time() - 3 * 3600
RECOVERED = humane.say('selfheal.recovered', 'zh')


def foreign_process():
    """A process Agent J did not start: double-forked, so this test process is not in its parent chain."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:                                     # child: fork again, report the grandchild, exit
        os.setsid()
        g = os.fork()
        if g == 0:
            os.close(r)
            os.execv(sys.executable, [sys.executable, '-c', 'import time; time.sleep(60)'])
        os.write(w, str(g).encode())
        os._exit(0)
    os.close(w)
    os.waitpid(pid, 0)
    g = int(os.read(r, 32))
    os.close(r)
    return g


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f'/proc/{pid}/stat') as f:
        return f.read().rsplit(')', 1)[1].split()[0] != 'Z'


class Rule(unittest.TestCase):
    def test_judge(self):
        j = turn_heal.judge
        self.assertEqual(j(idle=301, waited=None, children=[]), 'no_event')
        self.assertIsNone(j(idle=3600, waited=9999, children=[4242]), 'a long build (live tool child) is not stuck')
        self.assertIsNone(j(idle=3600, waited=9999, children=[], asking=True), 'a card waits for the owner')
        self.assertIsNone(j(idle=None, waited=9999, children=[]), 'no event clock: never judged by silence')
        self.assertEqual(j(idle=130, waited=None, children=[], loops=3), 'loop')
        self.assertEqual(j(idle=130, waited=700, children=[]), 'queued')
        self.assertIsNone(j(idle=130, waited=100, children=[]))
        self.assertIsNone(j(idle=60, waited=9999, children=[], loops=9))

    def test_turn_children_skip_boot_helpers_and_older_processes(self):
        rows = {100: (1, 1000.0), 101: (100, 1005.0), 102: (100, 2000.0), 103: (101, 2100.0), 104: (100, 1200.0),
                105: (100, None), 200: (1, 2500.0)}
        self.assertEqual(turn_heal.turn_children([100], 1500.0, rows), [102, 103, 105])

    @unittest.skipUnless(LINUX, '/proc parent chain')
    def test_may_stop_only_what_agentj_started(self):
        st = NS(root=Path(tempfile.mkdtemp(dir=TEST_TMP)))
        g = foreign_process()
        self.addCleanup(lambda: os.kill(g, signal.SIGKILL) if alive(g) else None)
        self.assertFalse(turn_heal.may_stop(st, g))
        self.assertFalse(turn_heal.may_stop(st, 1))
        self.assertFalse(turn_heal.may_stop(st, os.getpid()))
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        self.assertTrue(turn_heal.may_stop(st, p.pid), 'serve is in its parent chain')
        self.assertTrue(alive(g))

    def test_owner_signed_only(self):
        for a in turn_heal.ACTIONS:
            self.assertIn(a, reset.RESCUE_ACTIONS)
            self.assertNotIn(a, rescue_kit.AUTO_ACTIONS, 'support never triggers it')
            self.assertNotIn(a, reset.OFFERABLE, 'never offered by support')

    def test_humane_lines(self):
        self.assertEqual(humane.say('selfheal.recovered', 'zh'), '刚才卡住了，已自动恢复。')
        self.assertEqual(humane.say('selfheal.recovered', 'en'), 'It got stuck just now — recovered automatically.')


# ------------------------------------------------------------------ shared Codex: the customer's case
@unittest.skipUnless(LINUX, 'holders are read from /proc')
class SharedCodexHeal(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p0269-', dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home, self.proj = root / 'codex', root / 'proj'
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
                 {'type': 'turn_context', 'payload': {'cwd': str(self.proj), 'approval_policy': 'on-request',
                  'approvals_reviewer': 'user', 'sandbox_policy': {'type': 'read-only'}, 'permission_profile': PROFILE,
                  'active_permission_profile': {'id': ':read-only'}}},
                 {'type': 'event_msg', 'payload': {'type': 'task_started'}}]
        path.write_text(''.join(json.dumps(i) + '\n' for i in items))
        os.utime(path, (OLD, OLD))
        return path

    def holder(self, path, child=False):
        """The desktop / daemon that has the rollout open (live writer). `child`: it is running a tool right now."""
        code = ('import sys,time,subprocess\nf=open(sys.argv[1],"rb")\n'
                + ('c=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n' if child else '')
                + 'print("ok",flush=True)\ntime.sleep(60)')
        p = subprocess.Popen([sys.executable, '-c', code, str(path)], stdout=subprocess.PIPE, start_new_session=True)
        self.addCleanup(lambda: (os.killpg(p.pid, signal.SIGKILL), p.wait()))
        self.assertEqual(p.stdout.readline().strip(), b'ok')
        return p

    def agent(self):
        a = SharedCodexAgent(self.host, {'kind': 'codex', 'dir': str(self.proj), '_workflow_ceo': True,
                                         'shared_session_id': SID})
        a.cfg['_workflow_ceo'] = False             # the main conversation (proof counts its deliveries)
        self.host.agent = a

        async def call(method, params, timeout=60):
            self.calls.append((method, params))
            if method == 'thread/resume':
                return {'thread': {'id': params['threadId']}, 'approvalPolicy': params.get('approvalPolicy', 'on-request'),
                        'approvalsReviewer': params.get('approvalsReviewer', 'user'),
                        'activePermissionProfile': {'id': params.get('permissions')}}
            if method == 'hooks/list':
                return {'data': []}
            if method == 'turn/start':
                a.turn_done.set()
                return {'turn': {'id': 'turn-after-heal'}}
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

    async def test_stuck_desktop_turn_heals_and_the_queued_message_reaches_the_same_thread(self):
        path = self.rollout()
        desk = self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn('hello after the hang'))
        await self.wait_for(lambda: a.desktop_wait)
        self.assertIn('等它说完', self.notices())
        await self.heal.tick()                      # one watchdog tick: stuck (3 h quiet, no tool child) → heal
        await asyncio.wait_for(t, 15)
        starts = [p for m, p in self.calls if m == 'turn/start']
        self.assertEqual(len(starts), 1, 'the queued owner message was delivered')
        self.assertEqual(starts[0]['threadId'], SID, 'into the same conversation')
        self.assertEqual(starts[0]['input'][0]['text'], 'hello after the hang')
        self.assertEqual([p['threadId'] for m, p in self.calls if m == 'thread/resume'], [SID])
        self.assertIn(RECOVERED, self.notices())
        self.assertEqual(self.selfheal_logs()[-1]['result'], 'recovered')
        self.assertIsNone(desk.poll(), 'the live desktop writer was not killed')
        for kw in self.selfheal_logs():
            self.assertFalse(set(kw) - {'agent', 'phase', 'by', 'action', 'why', 'queued', 'step', 'result'}, 'metadata only')

    async def test_desktop_turn_running_a_tool_is_not_stuck(self):
        path = self.rollout()
        desk = self.holder(path, child=True)
        a = self.agent()
        t = asyncio.create_task(a.turn('wait for the build'))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        with patch.object(turn_heal, 'BOOT_GRACE', 0):
            await self.heal.tick()
        self.assertFalse(self.heal.state['stuck'])
        self.assertGreaterEqual(self.heal.state['children'], 1)
        await asyncio.sleep(1.5)
        self.assertTrue(a.desktop_wait, 'still waits behind the long build')
        self.assertFalse([m for m, _ in self.calls if m == 'turn/start'])
        self.assertIsNone(desk.poll())
        self.assertNotIn(RECOVERED, self.notices())

    async def test_estop_blocks_the_automatic_heal(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        self.host.stopped.return_value = True
        t = asyncio.create_task(a.turn('x'))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        await self.heal.tick()
        self.assertTrue(self.heal.state['stuck'])
        await asyncio.sleep(1.5)
        self.assertTrue(a.desktop_wait)
        self.assertFalse(self.selfheal_logs())
        self.assertEqual(await self.heal.heal('dev1', None, action='takeover'), {'ok': False, 'why': 'estop'})

    async def test_queued_notice_carries_waited_and_stuck(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        self.host.agent_notice.return_value = 7
        page = {'id': 7, 'card': {'queued': True, 'waited_s': 0}}
        self.host.hist.get.side_effect = lambda tid: page if tid == 7 else None
        self.host.hist_update.side_effect = lambda tid, card: page.update(card=card)
        self.host.stopped.return_value = True            # measure only
        t = asyncio.create_task(a.turn('x'))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        kw = [c.kwargs for c in self.host.agent_notice.call_args_list if '等它说完' in c.args[0]][0]
        self.assertEqual(kw['card'], {'queued': True, 'waited_s': 0})
        await self.heal.tick()
        self.assertTrue(page['card']['stuck'])
        self.assertGreaterEqual(page['card']['waited_s'], 3500)

    async def test_stop_on_a_stuck_desktop_turn_does_not_say_esc(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        a.rollout = path
        t = asyncio.create_task(a.turn('x'))
        await self.wait_for(lambda: a.desktop_wait)
        a.halt_quiet = True
        await a.halt(clear_queue=False)
        await asyncio.wait_for(t, 5)
        self.assertIsNone(a.halt_note, 'no 「按 Esc」 for a turn nobody is running')
        self.assertEqual(a.dead_turn, (str(path), path.stat().st_size), 'the next message goes past it')

    async def test_clear_on_a_stuck_desktop_turn_clears(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        a.drop_conversation = AsyncMock()
        res = await a.cmd_clear('')
        self.assertTrue(res.undo, res.text)


# ------------------------------------------------------------------ an Agent-J-owned harness that hangs
class FakeHarness(Agent):
    """A harness Agent J launched (its own process group). 'hang' ignores the interrupt and only ends with its process."""
    kind = 'codex'

    def __init__(self, host, cfg, child=False):
        super().__init__(host, cfg)
        self.cfg['_workflow_ceo'] = False
        self.proc, self.got, self.child, self.procs = None, [], child, []

    async def spawn(self):
        code = 'import time; time.sleep(60)'
        if self.child:      # a tool child started well after the harness: a long build
            code = 'import subprocess,sys,time\ntime.sleep(0.3)\nsubprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\ntime.sleep(60)'
        self.proc = await asyncio.create_subprocess_exec(sys.executable, '-c', code, start_new_session=True)
        self.procs.append(self.proc)

    async def turn(self, text):
        if self.proc is None or self.proc.returncode is not None:
            await self.spawn()                       # relaunched with Agent J's own settings
        self.turn_done = asyncio.Event()
        conv = self.host.st.agent_session(self.kind)
        self.touch()
        if text == 'hang':
            await self.proc.wait()
            return
        await self.deliver(lambda: self._took(conv, text))

    async def _took(self, conv, text):
        self.got.append((conv, text))
        return True


class OwnedHarness(unittest.IsolatedAsyncioTestCase):
    def make(self, child=False, cfg=None):
        self.st = NS(agent_session=lambda kind: 'conv-1', log=Mock(), root=Path(tempfile.mkdtemp(dir=TEST_TMP)))
        self.host = NS(st=self.st, lang='zh', preferences=None, asks={}, stopped=lambda: False, agent_notice=Mock(),
                       turn_t0=time.monotonic())
        self.heal = self.host.turn_heal = turn_heal.TurnHeal(self.host)
        a = FakeHarness(self.host, {'kind': 'codex', 'dir': str(self.st.root), '_workflow_ceo': True, **(cfg or {})}, child)
        self.host.agent = a
        self.addAsyncCleanup(self.cleanup, a)
        return a

    async def cleanup(self, a):
        for p in a.procs:
            if p.returncode is None:
                with __import__('contextlib').suppress(ProcessLookupError):
                    os.killpg(p.pid, signal.SIGKILL)
                await p.wait()
        if getattr(self, 'loop_task', None):
            self.loop_task.cancel()

    def drive(self, a):
        """serve's Agent.run in miniature: one turn at a time from the queue, turn_end reported."""
        async def loop():
            while True:
                text = await a.q.get()
                a.status = 'working'
                self.host.turn_t0 = time.monotonic()
                await a.watched(a.turn(text))
                self.heal.turn_ended()
                if a.q.empty():
                    a.status = 'idle'
        self.loop_task = asyncio.create_task(loop())

    def notices(self):
        return ' '.join(c.args[0] for c in self.host.agent_notice.call_args_list)

    async def test_hung_turn_is_stopped_relaunched_and_the_queued_message_delivered_in_the_same_conversation(self):
        a = self.make()
        self.drive(a)
        a.submit('hang')
        while a.proc is None:
            await asyncio.sleep(0.05)
        hung = a.proc
        a.submit('hello')
        a.event_at = time.monotonic() - 400              # five minutes and more without an event
        with patch.object(turn_heal, 'ESCALATE', 1.0):
            await self.heal.tick()
        self.assertTrue(self.heal.state.get('stuck'))
        self.assertIsNotNone(hung.returncode, 'the app-server Agent J started was stopped after the interrupt')
        self.assertEqual(a.got, [('conv-1', 'hello')], 'delivered into the same conversation')
        self.assertIsNot(a.proc, hung, 'relaunched')
        self.assertIn(RECOVERED, self.notices())

    @unittest.skipUnless(LINUX, '/proc process table')
    async def test_long_tool_child_is_not_interrupted(self):
        a = self.make(child=True)
        self.drive(a)
        a.submit('hang')
        while a.proc is None:
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)                         # the build child is running
        a.event_at = time.monotonic() - 3600
        with patch.object(turn_heal, 'BOOT_GRACE', 0):
            await self.heal.tick()
        self.assertFalse(self.heal.state['stuck'])
        self.assertGreaterEqual(self.heal.state['children'], 1)
        self.assertIsNone(a.proc.returncode, 'not interrupted')
        self.assertFalse(self.st.log.call_args_list)

    async def test_owner_window_is_never_signalled(self):
        """Shared Claude in the owner's own window: Agent J did not start it → takeover says so, nothing is killed."""
        a = self.make(cfg={'session_mode': 'shared'})
        a.kind = 'claude'
        self.drive(a)
        a.submit('hang')
        while a.proc is None:
            await asyncio.sleep(0.05)
        with patch.object(turn_heal, 'ESCALATE', 0.5):
            res = await self.heal.heal('dev1', None, action='takeover')
        self.assertEqual(res['why'], 'foreign')
        self.assertIsNone(a.proc.returncode)

    async def test_preempt_interrupts_and_keeps_the_queue(self):
        a = self.make()
        self.drive(a)
        a.submit('hang')
        while a.proc is None:
            await asyncio.sleep(0.05)
        hung = a.proc
        a.submit('after')
        self.assertTrue(await self.heal.preempt('dev1', 'clear'))
        for _ in range(50):
            if a.got:
                break
            await asyncio.sleep(0.1)
        self.assertIsNotNone(hung.returncode)
        self.assertEqual(a.got, [('conv-1', 'after')])


# ------------------------------------------------------------------ serve
class Serve(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_l1 import Phone, _host, _ready, _state
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []
        self.ph, self.other = Phone(self.st, 'iPhone'), Phone(self.st, 'Pixel')
        self.h = _host(self.st, self.sent)
        self.s = _ready(self.h, self.ph)
        self.s.p33 = True

    async def test_cmd_detect_sends_cmds_state_without_type_error(self):
        facts = [{'name': 'kimi', 'label': 'Kimi Code', 'installed': False, 'logged_in': False}]
        with patch('agentj.cmdrun.available_commands', return_value=['status']) as names, \
                patch('agentj.cmdrun.command_harnesses', return_value=facts) as harnesses:
            await self.h._detect_terminal_commands()
        names.assert_called_once_with()
        harnesses.assert_called_once_with(detect=True)
        self.assertIn({'t': 'cmds_state', 'names': ['status'], 'harnesses': facts}, [o for _, o in self.sent])
        self.assertFalse(self.h._cmdrun_detecting)

    def controls(self):
        p = self.st.controls_path
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    async def test_rescue_do_end_turn_and_takeover_are_signed_and_logged(self):
        from test_controls import sign
        heal = AsyncMock(return_value={'ok': True, 'step': 'interrupt'})
        self.h.turn_heal.heal = heal
        await self.h._app(self.s, {'t': 'rescue_do', 'r': 'u1', 'action': 'end_turn'})                       # unsigned
        await self.h._app(self.s, sign(self.ph, 'rescue_do', {'action': 'takeover'},
                                       {'t': 'rescue_do', 'r': 'k1', 'action': 'takeover'}, key=self.other.sk))  # not this phone
        await asyncio.sleep(0.2)
        heal.assert_not_awaited()
        await self.h._app(self.s, sign(self.ph, 'rescue_do', {'action': 'end_turn'}, {'t': 'rescue_do', 'r': 'e1', 'action': 'end_turn'}))
        for _ in range(30):
            if heal.await_count:
                break
            await asyncio.sleep(0.1)
        heal.assert_awaited_once()
        self.assertEqual(heal.await_args.kwargs['action'], 'end_turn')
        log = self.controls()
        self.assertEqual([c['result'] for c in log], ['refused:shape', 'refused:bad_signature', 'ok'])
        for _ in range(30):
            if [o for _, o in self.sent if o.get('t') == 'reset_done' and o.get('r') == 'e1']:
                break
            await asyncio.sleep(0.1)
        done = [o for _, o in self.sent if o.get('t') == 'reset_done' and o.get('r') == 'e1'][0]
        self.assertEqual((done['flow'], done['ok']), ('end_turn', True))

    async def test_clear_preempts_the_running_turn_never_after_turn(self):
        a = FakeHarness(self.h, {'kind': 'codex', 'dir': self.tmp.name, '_workflow_ceo': True})
        a.status = 'working'
        a.proc = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(60)', start_new_session=True)
        self.addAsyncCleanup(self._reap, a.proc)
        self.h.agent = a
        running = a.proc
        await self.h.on_slash(self.s, 'clear', '', True, True)
        self.assertIsNotNone(running.returncode, 'the running turn was interrupted, not waited for')
        texts = json.dumps([o for _, o in self.sent], ensure_ascii=False) + json.dumps(self.h.hist.since(0) if hasattr(self.h.hist, 'since') else [], ensure_ascii=False)
        self.assertNotIn(humane.say('cmd.after_turn', 'zh'), texts)
        self.assertEqual([x.name for x in list(a.q._queue)], ['clear'])

    async def _reap(self, p):
        if p.returncode is None:
            with __import__('contextlib').suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            await p.wait()

    async def test_status_carries_the_turn_state(self):
        self.h.turn_heal.state = {'busy': True, 'waited_s': 61, 'idle_s': 400, 'children': 0, 'stuck': True, 'why': 'no_event'}
        self.assertEqual(self.h.turn_heal.view()['why'], 'no_event')
        self.assertIn('"turn": self.turn_heal.view()', Path(serve.__file__).read_text())


class Doctor(unittest.TestCase):
    def test_version_mismatch_and_stuck_turn_in_json(self):
        from agentj import doctor, codex_procs
        st = Mock()
        st.exists.return_value = True
        st.agent_config.return_value = {'kind': 'codex'}
        table = [(501, '/opt/codex-new/codex app-server daemon'), (502, 'bash')]
        vers = {'/usr/bin/codex': 'codex-cli 0.160.0', '/opt/codex-new/codex': 'codex-cli 0.166.0'}
        with patch.object(codex_procs, 'proc_table', return_value=table), \
                patch.object(doctor, '_agent_bin', return_value='/usr/bin/codex'), \
                patch.object(doctor, '_version_of', side_effect=lambda e: vers.get(e)):
            row = doctor.check_turn(st, {'turn': {'busy': True, 'waited_s': 3700, 'idle_s': 3600, 'children': 0,
                                                  'stuck': True, 'why': 'no_event'}})
        self.assertEqual(row['status'], doctor.WARN)
        # w2-sec C-1: the daemon's binary (named only by its command line) is never run — "?" + another install = mismatch
        self.assertEqual(row['data']['codex'], {'cli': '0.160.0', 'servers': [{'pid': 501, 'version': None, 'daemon': True,
                                                                               'ours': False, 'other': True}],
                                                'mismatch': True})
        self.assertTrue(row['data']['turn']['stuck'])
        self.assertIn('? daemon other', row['summary'])
        json.dumps(row)


if __name__ == '__main__':
    unittest.main()
