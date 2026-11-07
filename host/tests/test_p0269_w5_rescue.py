"""0.26.10 lane w5-rescue (#484, ADR-0269-w5-rescue): 「紧急修复」's 「重置连接」 and 「重开 AI 会话」 must really recover.

Chairman: 「这个重置连接和这个重开AI对话，到底是在什么样一个执行逻辑？怎么遇到问题从来没管用过？」 Customer (Codex): a turn
stuck on Stop for ~1 h; /stop ended it at once, the buttons never did; the upgrade restart then waited its full 600 s.

Real scenarios with the stand-in harnesses (real subprocesses through serve, fenced where bubblewrap works):
- Codex turn that never ends and ignores turn/interrupt (fakecodex HANG) → 「重置连接」 does what /stop does first: the hung
  app-server Agent J started is ended, the queued owner message reaches the SAME thread, the result says so (< 60 s);
- the same with 「重开 AI 会话」 (it used to call `_thread()` on a stopped app-server: always failed and dropped the pin);
- Claude turn that never ends (fakeclaude HANG) → reset stops it, the queued message is resumed with --resume;
- Codex app-server that never answers turn/start (fakecodex NOACK) → the deaf app-server is ended, the next message
  works, the reset's result names it;
- usage limit used up (fakecodex QUOTA) → both buttons say plainly that no button fixes it and what to do (ok:false);
- shared Codex desktop turn the owner's daemon still calls active but silent ≥ DEAD_IDLE (Avon) → dead for the watchdog,
  /stop, both buttons and the upgrade restart; a live desktop turn / a pending approval (L-6) is never released;
- upgrade restart: never waits on the turn that launched it nor on a stuck turn; the phone is told 「已安装，等当前任务
  结束后重启（最多 10 分钟）」 once.
"""
import _hermetic  # noqa: F401,I001
from _testtmp import TEST_TMP  # noqa: E402
import asyncio
import json
import os
import pathlib
import sys
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock, Mock, patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import auto_update, phone_restart, reset, service_recovery, turn_heal  # noqa: E402
from agentj.agent_codex import CodexAgent  # noqa: E402
from test_controls import sign  # noqa: E402
from test_slash import _Chain  # noqa: E402
import test_p0269_selfheal as base  # noqa: E402

RECOVER_S = 60          # #484 acceptance: recovered within one minute of the tap
ZH = lambda pair: pair[0]  # noqa: E731


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f'/proc/{pid}/stat') as f:
            return f.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return True


# ------------------------------------------------------------------ end to end through serve with the stand-ins
class _Rescue(_Chain):
    def flow(self, check, first='HANG'):
        async def script(c):
            h, s = c['host'], c['s']
            s.p33 = True
            h.relay_up = True                       # the relay link is up (these units have no relay: step 1 must not wait)
            n = [0]

            async def say(text):
                n[0] += 1
                sid = f'{n[0]:022d}'
                await h._app(s, {'t': 'say', 'sid': sid, 'text': text, 'ts': 0})
                await c['wait'](lambda: [o for _, o in c['sent'] if o.get('t') == 'say_res' and o.get('sid') == sid], 5000)
                return sid

            async def press(action, r):
                if action == 'reset':
                    obj = sign(c['ph'], 'reset', {'what': 'connection'}, {'t': 'reset', 'r': r})
                else:
                    obj = sign(c['ph'], 'rescue_do', {'action': action}, {'t': 'rescue_do', 'r': r, 'action': action})
                t0 = time.monotonic()
                await h._app(s, obj)
                await c['wait'](lambda: [o for _, o in c['sent'] if o.get('t') == 'reset_done' and o.get('r') == r],
                                RECOVER_S * 1000)
                done = [o for _, o in c['sent'] if o.get('t') == 'reset_done' and o.get('r') == r][-1]
                steps = [o for _, o in c['sent'] if o.get('t') == 'reset_step' and o.get('r') == r]
                return done, steps, time.monotonic() - t0
            c.update(say=say, press=press,
                     said=lambda text: text in json.dumps([o for _, o in c['sent']], ensure_ascii=False))
            await say(first)
            if first in ('HANG', 'NOACK'):
                await c['wait'](lambda: h.agent.status == 'working' and h.agent.proc is not None)
                await asyncio.sleep(0.5)
            await check(c, h)
        self.run_chain(script)

    def starts(self):
        return [x for x in self.logged() if x.get('method') == 'turn/start']


class CodexHang(_Rescue):
    KIND = 'codex'

    def _hung(self, c, h):
        # the thread lives on in Codex's own store after its app-server ends (the stand-in keeps threads in memory only)
        os.environ['FAKE_CX_THREADS'] = json.dumps([h.agent.tid])
        return h.agent.proc, h.agent.tid

    def test_reset_connection_stops_the_hung_turn_and_delivers_the_queued_message_in_the_same_thread(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            hung, tid = self._hung(c, h)
            await c['say']('after the hang')
            done, steps, secs = await c['press']('reset', 'rs1')
            self.assertLess(secs, RECOVER_S)
            self.assertTrue(done['ok'], done)
            self.assertIn(ZH(reset.UNSTICK['ok']), done['text'])
            self.assertIn(ZH(reset.UNSTICK['run']), [x['text'] for x in steps], 'the /stop step is shown first')
            self.assertFalse(alive(hung.pid), 'the app-server Agent J started was ended (same as /stop)')
            await c['wait'](lambda: any(x['params']['input'][0]['text'].startswith('after the hang') for x in self.starts()), RECOVER_S * 1000)
            after = [x for x in self.starts() if x['params']['input'][0]['text'].startswith('after the hang')][0]
            self.assertEqual(after['params']['threadId'], tid, 'the same conversation')
            await c['wait'](lambda: c['said']('ECHO: after the hang'))
            log = self.st.log_path.read_text()
            self.assertIn('"ev": "rescue_unstick"', log)
            self.assertNotIn('after the hang', log)
        self.flow(check)

    def test_reopen_ai_session_recovers_resumes_the_thread_and_keeps_the_queue(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            hung, tid = self._hung(c, h)
            await c['say']('queued behind')
            done, steps, secs = await c['press']('restart_session', 'rr1')
            self.assertLess(secs, RECOVER_S)
            self.assertTrue(done['ok'], done)
            self.assertFalse(done.get('new'), 'the same conversation was resumed, not a new one')
            self.assertIn(ZH(reset.UNSTICK['ok']), done['text'])
            self.assertFalse(alive(hung.pid))
            self.assertEqual(self.st.agent_session('codex'), tid, 'the pin is kept')
            await c['wait'](lambda: any(x['params']['input'][0]['text'].startswith('queued behind') for x in self.starts()), RECOVER_S * 1000)
            after = [x for x in self.starts() if x['params']['input'][0]['text'].startswith('queued behind')][0]
            self.assertEqual(after['params']['threadId'], tid)
            await c['wait'](lambda: c['said']('ECHO: queued behind'))
        self.flow(check)


class CodexNoAck(_Rescue):
    KIND = 'codex'

    def test_turn_start_never_acked_ends_the_deaf_app_server_and_the_buttons_say_so(self):
        p = patch.object(CodexAgent.call, '__defaults__', (2.0,))
        p.start()
        self.addCleanup(p.stop)

        async def check(c, h):
            deaf = h.agent.proc
            await c['wait'](lambda: '"ev": "codex_turn_noack"' in self.st.log_path.read_text(), 15000)
            await c['wait'](lambda: not alive(deaf.pid), 10000)
            self.assertIsNotNone(h.agent.noack_at)
            done, _, secs = await c['press']('reset', 'na1')
            self.assertLess(secs, RECOVER_S)
            self.assertIn(ZH(reset.NOACK), done['text'])
            await c['say']('works again')
            await c['wait'](lambda: c['said']('ECHO: works again'), 20000)
        self.flow(check, first='NOACK')

    def test_reset_during_an_unanswered_turn_start_recovers(self):
        async def check(c, h):
            deaf = h.agent.proc
            done, _, secs = await c['press']('reset', 'na2')
            self.assertLess(secs, RECOVER_S)
            self.assertTrue(done['ok'], done)
            self.assertFalse(alive(deaf.pid))
            await c['say']('next one')
            await c['wait'](lambda: c['said']('ECHO: next one'), 20000)
        self.flow(check, first='NOACK')


class CodexQuota(_Rescue):
    KIND = 'codex'

    def test_both_buttons_say_no_button_fixes_a_used_up_limit(self):
        async def check(c, h):
            await c['wait'](lambda: h.agent.status == 'idle' and h.auth_rescue.state.get('codex'))
            done, _, _ = await c['press']('reset', 'q1')
            self.assertFalse(done['ok'])
            self.assertEqual(done['why'], 'quota')
            self.assertIn('额度用完了', done['text'])
            self.assertIn('帮不了', done['text'])
            done2, _, _ = await c['press']('restart_session', 'q2')
            self.assertEqual((done2['ok'], done2['why']), (False, 'quota'))
            self.assertIn('额度用完了', done2['text'])
        self.flow(check, first='QUOTA')


class ClaudeHang(_Rescue):
    KIND = 'claude'

    def test_reset_stops_a_claude_turn_that_never_ends_and_resumes_the_session(self):
        async def check(c, h):
            hung = h.agent.proc
            sid = self.st.agent_session('claude')
            await c['say']('after claude hang')
            done, _, secs = await c['press']('reset', 'cl1')
            self.assertLess(secs, RECOVER_S)
            self.assertTrue(done['ok'], done)
            self.assertIn(ZH(reset.UNSTICK['ok']), done['text'])
            self.assertFalse(alive(hung.pid))
            await c['wait'](lambda: c['said']('ECHO: after claude hang'), RECOVER_S * 1000)
            if sid:
                self.assertEqual(self.st.agent_session('claude'), sid, 'the same session (--resume)')
        self.flow(check)


# ------------------------------------------------------------------ RESEARCH-infinity ①: interrupt → that turn's children → /stop
class ToolHarness(base.Agent):
    """A harness Agent J started whose turn waits on one tool child; it ignores the soft interrupt and finishes the turn by
    itself once that child ends (as Codex / Claude report a killed command and wrap up)."""
    kind = 'codex'
    CODE = ('import subprocess,sys,time\nc=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
            'print("started",flush=True)\nc.wait()\nprint("done",flush=True)\ntime.sleep(60)')

    def __init__(self, host, cfg):
        super().__init__(host, cfg)
        self.cfg['_workflow_ceo'] = False
        self.proc = None

    async def turn(self, text):
        self.host.turn_t0 = time.monotonic() - 1
        self.proc = await asyncio.create_subprocess_exec(sys.executable, '-c', self.CODE, stdout=asyncio.subprocess.PIPE,
                                                         start_new_session=True)
        while (await self.proc.stdout.readline()).strip() != b'done':
            pass


@unittest.skipUnless(sys.platform.startswith('linux'), '/proc process table')
class ThreeSteps(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_turns_tool_child_is_ended_the_harness_lives(self):
        import tempfile
        root = pathlib.Path(tempfile.mkdtemp(dir=TEST_TMP))
        st = NS(agent_session=lambda kind: 'conv-1', log=Mock(), root=root)
        h = NS(st=st, lang='zh', preferences=None, asks={}, stopped=lambda: False, agent_notice=Mock(), turn_t0=None,
               stop_turn=AsyncMock())
        h.turn_heal = heal = turn_heal.TurnHeal(h)
        a = h.agent = ToolHarness(h, {'kind': 'codex', 'dir': str(root), '_workflow_ceo': True})

        async def drive():
            text = await a.q.get()
            a.status = 'working'
            await a.turn(text)
            heal.turn_ended()
            a.status = 'idle'
        task = asyncio.create_task(drive())
        a.q.put_nowait('build')
        while a.proc is None:
            await asyncio.sleep(0.05)
        self.addAsyncCleanup(self._reap, a.proc)
        await asyncio.sleep(1.0)                                   # the tool child runs
        with patch.object(turn_heal, 'BOOT_GRACE', 0), patch.object(reset, 'SOFT_WAIT', 3.0):
            res = await reset.unstick(h, 'dev1')
        await asyncio.wait_for(task, 5)
        self.assertEqual(res, {'ran': True, 'ok': True, 'how': 'children'})
        self.assertIsNone(a.proc.returncode, 'the harness Agent J started is not ended when its tool child was the problem')
        h.stop_turn.assert_not_awaited()
        self.assertIn(ZH(reset.UNSTICK['children']), (await reset.after_text(
            NS(agent=a, turn_at=(0.0,), auth_rescue=None, st=st), res, '', 'zh'))[0])

    async def test_unknown_turn_start_skips_step_two(self):
        """security #484 note 1: without turn_t0 「this turn's children」 would widen to older background processes the owner
        kept — step ② is skipped and nothing is signalled."""
        import tempfile
        root = pathlib.Path(tempfile.mkdtemp(dir=TEST_TMP))
        st = NS(agent_session=lambda kind: 'conv-1', log=Mock(), root=root)
        h = NS(st=st, lang='zh', turn_t0=None)
        a = ToolHarness(h, {'kind': 'codex', 'dir': str(root), '_workflow_ceo': True})
        task = asyncio.create_task(a.turn('build'))
        while a.proc is None:
            await asyncio.sleep(0.05)
        self.addAsyncCleanup(self._reap, a.proc)
        await asyncio.sleep(1.0)
        h.turn_t0 = None                                           # the turn's start is not known
        rows = turn_heal.proc_rows()
        kids = turn_heal.turn_children([a.proc.pid], None, rows)
        with patch.object(turn_heal, 'BOOT_GRACE', 0):
            self.assertEqual(await reset._stop_turn_children(h, a), 0)
        await asyncio.sleep(0.5)
        self.assertFalse(task.done(), 'the tool child was not ended')
        task.cancel()
        self.assertTrue(all(alive(k) for k in kids if k in turn_heal.proc_rows()))

    async def _reap(self, p):
        if p.returncode is None:
            with __import__('contextlib').suppress(ProcessLookupError):
                os.killpg(p.pid, __import__('signal').SIGKILL)
            await p.wait()


# ------------------------------------------------------------------ shared Codex: the owner's daemon says "active" (Avon)
class SharedDesktopDead(base.SharedCodexHeal):
    def setUp(self):
        super().setUp()
        p = patch.object(turn_heal, 'daemon_live', return_value=True)      # the owner's daemon still reports it active
        p.start()
        self.addCleanup(p.stop)

    def test_rule(self):
        f = {'idle': turn_heal.DEAD_IDLE + 1, 'children': [], 'asking': False, 'live': True}
        self.assertTrue(turn_heal.dead_facts(f), 'ten silent minutes: dead whatever the daemon says')
        self.assertFalse(turn_heal.dead_facts({**f, 'idle': turn_heal.STUCK_IDLE + 1}), 'daemon active wins before that')
        self.assertTrue(turn_heal.dead_facts({**f, 'idle': turn_heal.STUCK_IDLE + 1, 'live': False}))
        self.assertFalse(turn_heal.dead_facts({**f, 'asking': True}), 'L-6: a pending approval is never dead')
        self.assertFalse(turn_heal.dead_facts({**f, 'children': [123]}), 'a running tool is never dead')

    async def test_watchdog_releases_a_daemon_active_but_silent_desktop_turn(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn('hi after the dead desktop turn'))
        await self.wait_for(lambda: a.desktop_wait)
        await self.heal.tick()
        self.assertTrue(self.heal.state.get('stuck'), self.heal.state)
        await asyncio.wait_for(t, 15)
        starts = [p for m, p in self.calls if m == 'turn/start']
        self.assertEqual([s['threadId'] for s in starts], [base.SID])

    async def test_reset_unstick_lets_the_waiting_message_through(self):
        path = self.rollout()
        desk = self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn('hi'))
        await self.wait_for(lambda: a.desktop_wait)
        res = await reset.unstick(self.host, 'dev1')
        self.assertEqual(res, {'ran': True, 'ok': True, 'how': 'released'})
        await asyncio.wait_for(t, 15)
        self.assertEqual(len([m for m, _ in self.calls if m == 'turn/start']), 1)
        self.assertIsNone(desk.poll(), 'the owner\'s desktop / daemon is never signalled')

    async def test_reset_flow_end_to_end_clears_the_desktop_wait(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn('hi'))
        await self.wait_for(lambda: a.desktop_wait)
        h = self.host
        sent = []
        h._send_device = AsyncMock(side_effect=lambda d, m: sent.append(m))
        h._send_p33 = AsyncMock(side_effect=lambda fn: sent.append(fn(None)))
        h.ws, h.relay_up, h.sessions, h.turn_at, h.auth_rescue = None, True, {}, (0.0, 0.0, False), None
        h.eff_status = lambda: a.status
        h.agent_reconnect = AsyncMock(return_value={'ok': True})
        with patch.object(reset, 'DEVICE_WAIT', 0), patch.object(reset, 'codex_fix', return_value=None):
            await reset.Desk(h)._run('dev1', 'r1', 'reset')
        await asyncio.wait_for(t, 15)
        done = [m for m in sent if m.get('t') == 'reset_done'][-1]
        self.assertTrue(done['ok'], done)
        self.assertIn(ZH(reset.UNSTICK['released']), done['text'])
        self.assertEqual(len([m for m, _ in self.calls if m == 'turn/start']), 1)

    async def test_live_desktop_turn_is_never_released_and_the_owner_is_told(self):
        path = self.rollout()
        os.utime(path, None)                               # written just now: a desktop turn really running
        self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn('hi'))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        res = await reset.unstick(self.host, 'dev1')
        self.assertEqual(res['why'], 'desktop_live')
        self.assertTrue(a.desktop_wait)
        self.host.turn_at, self.host.auth_rescue = (0.0, 0.0, False), None
        with patch.object(reset, 'codex_fix', return_value='CODEX_FIX'):
            text, why = await reset.after_text(self.host, res, '', 'zh')
        self.assertEqual(why, 'desktop_live')
        self.assertIn('关掉那个 codex 终端', text)
        self.assertIn('CODEX_FIX', text, 'the CLI / daemon version fix when they differ')

    async def test_pending_approval_is_never_released(self):
        path = self.rollout()
        with path.open('a') as f:              # L-6: the desktop turn asks its owner (an unanswered approval)
            f.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'exec_approval_request', 'call_id': 'c1'}}) + '\n')
        os.utime(path, (base.OLD, base.OLD))
        self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn('hi'))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        with patch('agentj.shared_codex.pending_approval', return_value=True):
            self.assertFalse(turn_heal.desktop_stuck(path, (), base.SID))
            res = await reset.unstick(self.host, 'dev1')
        self.assertEqual(res['why'], 'desktop_live')

    async def test_reopen_carries_the_dead_turn_and_keeps_the_waiting_message(self):
        path = self.rollout()
        self.holder(path)
        a = self.agent()
        a.wait_path = path
        a.dead_turn = None
        made = []

        def make(_h, cfg):
            b = NS(kind='codex', cfg=dict(cfg), stop=AsyncMock(), start=Mock(), set_status=Mock(), dead_turn=None)
            made.append(b)
            return b
        self.host.sends = NS(items={})
        self.host.queue_dropped = Mock()
        self.host.turn_lock = asyncio.Lock()
        self.host.grants = {}
        self.host.st.agent_session.return_value = base.SID
        a.q.put_nowait('waiting owner message')
        a.stop = AsyncMock()
        with patch.object(phone_restart.agent, 'make', side_effect=make), \
                patch.object(phone_restart, 'prepare', new_callable=AsyncMock):
            res = await phone_restart.run(self.host, release=True)
        self.assertTrue(res['ok'], res)
        self.assertEqual(made[-1].dead_turn, (str(path), path.stat().st_size))
        self.host.queue_dropped.assert_not_called()
        self.assertEqual(made[-1].q.get_nowait(), 'waiting owner message')


# ------------------------------------------------------------------ the upgrade restart's idle wait (Avon: 600 s)
class UpgradeWait(unittest.TestCase):
    def host(self, status='working', stuck=False, upgrade_turn=None, queued=False):
        q = asyncio.Queue()
        if queued:
            q.put_nowait('x')
        h = NS(agent=NS(status=status, q=q), peers=None, asks={}, scheduler=NS(current_id=None), elevate=NS(cards={}),
               bots=None, _open_question=lambda: None, turn_t0=123.0, upgrade_turn=upgrade_turn,
               turn_heal=NS(view=lambda: {'busy': True, 'stuck': stuck}))
        return h

    def test_never_waits_on_the_launching_or_a_stuck_turn(self):
        self.assertEqual(auto_update.restart_ready(self.host(status='idle')), (True, None))
        self.assertEqual(auto_update.restart_ready(self.host()), (False, 'turn'), 'a turn merely running: wait')
        self.assertEqual(auto_update.restart_ready(self.host(upgrade_turn=123.0)), (True, 'launching_turn'))
        self.assertEqual(auto_update.restart_ready(self.host(upgrade_turn=99.0)), (False, 'turn'), 'a later turn: wait')
        self.assertEqual(auto_update.restart_ready(self.host(upgrade_turn=123.0, queued=True)), (False, 'turn'))
        self.assertEqual(auto_update.restart_ready(self.host(stuck=True)), (True, 'stuck_turn'))
        # security #484 note 3: a queued owner message is delivered before the restart, never lost with it
        self.assertEqual(auto_update.restart_ready(self.host(stuck=True, queued=True)), (False, 'stuck_queued'))
        h = self.host(stuck=True)
        h.agent.cur_send = NS(state='pending')              # waiting behind the stuck (desktop) turn, not yet delivered
        self.assertEqual(auto_update.restart_ready(h), (False, 'stuck_queued'))
        h = self.host(stuck=True)
        h.asks = {'a': NS(fut=NS(done=lambda: False))}
        self.assertEqual(auto_update.restart_ready(h), (False, 'busy'), 'an open card still waits')

    def test_recovery_worker_asks_with_purpose_upgrade(self):
        seen = []

        def ctl(st, req, timeout):
            seen.append(req)
            return {'ok': True, 'ready': True}
        self.assertEqual(service_recovery.wait_ready(None, ctl=ctl, purpose='upgrade'), 'idle')
        self.assertEqual(seen, [{'cmd': 'upgrade_ready', 'purpose': 'upgrade'}])
        src = pathlib.Path(service_recovery.__file__).read_text()
        self.assertIn("wait_ready(st, purpose='upgrade')", src)


class QueuedSurvivesUpgrade(base.OwnedHarness):
    """security #484 note 3, end to end with a harness Agent J started that hangs: the upgrade restart is held while an owner
    message waits behind the stuck turn; the heal delivers it into the same conversation; only then is the restart ready."""

    async def test_queued_owner_message_is_delivered_before_the_restart(self):
        a = self.make()
        h = self.host
        h.peers, h.asks, h.scheduler, h.elevate, h.bots = None, {}, NS(current_id=None), NS(cards={}), None
        h._open_question = lambda: None
        self.drive(a)
        a.submit('hang')
        while a.proc is None:
            await asyncio.sleep(0.05)
        a.submit('hello')
        a.event_at = time.monotonic() - 400
        self.heal.state = await asyncio.to_thread(self.heal.measure)
        self.assertTrue(self.heal.state.get('stuck'))
        self.assertEqual(auto_update.restart_ready(h), (False, 'stuck_queued'), 'held: a message would be lost')
        with patch.object(turn_heal, 'ESCALATE', 1.0):
            self.assertTrue(await auto_update.deliver_before_restart(h))
        self.assertEqual(a.got, [('conv-1', 'hello')], 'delivered into the same conversation before the restart')
        for _ in range(50):
            if a.status == 'idle':
                break
            await asyncio.sleep(0.1)
        self.heal.state = await asyncio.to_thread(self.heal.measure)
        self.assertEqual(auto_update.restart_ready(h), (True, None))

    async def test_estop_never_triggers_the_delivery_heal(self):
        a = self.make()
        self.host.stopped = lambda: True
        heal = AsyncMock()
        self.heal.heal = heal
        self.assertFalse(await auto_update.deliver_before_restart(self.host))
        heal.assert_not_awaited()
        del a


class UpgradeWaitServe(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from test_l1 import Phone, _host, _ready, _state
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(self.tmp.name)
        self.sent = []
        self.ph = Phone(self.st, 'iPhone')
        self.h = _host(self.st, self.sent)
        self.s = _ready(self.h, self.ph)
        self.s.p33 = True

    async def test_wait_note_once_and_launching_turn_released(self):
        a = base.FakeHarness(self.h, {'kind': 'codex', 'dir': self.tmp.name, '_workflow_ceo': True})
        a.status = 'working'
        self.h.agent = a
        self.h.turn_t0 = time.monotonic()
        ready, why = auto_update.restart_ready(self.h)
        self.assertEqual((ready, why), (False, 'turn'))
        with patch.object(self.h, 'agent_notice') as note:
            self.h._upgrade_wait_note(ready, why)
            self.h._upgrade_wait_note(ready, why)
        note.assert_called_once_with('已安装，等当前任务结束后重启（最多 10 分钟）。')
        self.h.upgrade_turn = self.h.turn_t0          # `agentj update apply` ran in this very turn
        self.assertEqual(auto_update.restart_ready(self.h), (True, 'launching_turn'))

    def test_serve_wiring(self):
        from agentj import serve
        src = pathlib.Path(serve.__file__).read_text()
        self.assertIn('auto_update.restart_ready(self)', src)
        self.assertIn('auto_update.deliver_before_restart(self)', src)
        self.assertIn('self.upgrade_turn = self.turn_t0 if self.agent and self.agent.status != "idle" else None', src)
        self.assertIn('self.upgrade_turn = self.turn_t0               # the restart does not wait', src)


# ------------------------------------------------------------------ texts: never promise what a button cannot do
class Texts(unittest.TestCase):
    def test_quota_text_names_the_way_out(self):
        q = {'cls': 'rate_limit', 'name': 'Codex', 'at': time.time() + 3600}
        zh = reset.quota_text(q, 'zh')
        self.assertIn('帮不了', zh)
        self.assertIn('自动接着做', zh)
        self.assertIn('cannot', reset.quota_text({**q, 'cls': 'balance'}, 'en'))
        for v in list(reset.UNSTICK.values()) + [reset.NOACK, reset.NOACK_NEXT]:
            for t in v:
                self.assertNotRegex(t, r'试用|trial')

    def test_quota_state_only_for_the_last_turn(self):
        h = NS(agent=NS(kind='codex', limit_hit=None), turn_at=(1000.0, 0.0, True),
               auth_rescue=NS(state={'codex': {'cls': 'rate_limit', 'at': 1001}}))
        self.assertEqual(reset.quota_state(h)['cls'], 'rate_limit')
        h.turn_at = (5000.0, 0.0, False)                # a later turn started after that limit: not this one
        self.assertIsNone(reset.quota_state(h))


if __name__ == '__main__':
    unittest.main()
