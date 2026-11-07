"""#585 「紧急修复」做彻底 — every button proves what it promises (real serve Host + real stand-in harness subprocesses).

Chairman 11-07 18:14: 「这几个按钮……点了以后，好像没有几次真正的是帮用户解决了问题呢？……要不点了没用，放在那里干啥呢？」

Reproduced stuck states (fakecodex / fakeclaude are real child processes of serve, fenced where bubblewrap works):
- Codex turn that never ends and ignores turn/interrupt (HANG; Stella / Olares: the session answers nothing for hours);
- the same with 「全部停下」 already on (Stella pressed it; takeover then failed silently three times);
- an approval card the phone never got (the stuck wait of a sub-thread / a card lost during compaction);
- Claude turn that never ends.
Each button is checked for what its words promise: 停止 = the process tree is gone (checked) and the waits are withdrawn;
重启 Agent = a new process that answers one line; 换个新对话 = old one kept, the new one answers; 接管 = a real reason and
the next step; 重启服务 = the harness tree is ended first, a marker survives the restart and the page's follow-up proves it;
一键修复 = the ladder stop → restart Agent → restart service → new conversation, stops at the first rung where the Agent answers.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import sys
import time
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from agentj import reset, rescue_repair, service  # noqa: E402
from test_controls import sign  # noqa: E402
from test_p0269_w5_rescue import _Rescue, alive  # noqa: E402

LIMIT = 75     # s per press (the probe allows a cold start)


class _P585(_Rescue):
    def press_more(self, c):
        async def press(action, r, ms=LIMIT * 1000):
            h, s = c['host'], c['s']
            obj = sign(c['ph'], 'rescue_do', {'action': action}, {'t': 'rescue_do', 'r': r, 'action': action})
            await h._app(s, obj)
            await c['wait'](lambda: [o for _, o in c['sent'] if o.get('t') == 'reset_done' and o.get('r') == r
                                     and not o.get('pending')], ms)
            done = [o for _, o in c['sent'] if o.get('t') == 'reset_done' and o.get('r') == r][-1]
            steps = [o for _, o in c['sent'] if o.get('t') == 'reset_step' and o.get('r') == r]
            return done, steps
        return press

    def probes(self):
        return [x for x in self.logged() if x.get('method') == 'turn/start'
                and 'Emergency repair' not in json.dumps(x) and '紧急修复' in json.dumps(x, ensure_ascii=False)]


class CodexStuck(_P585):
    KIND = 'codex'

    def test_one_tap_repair_stops_the_hung_turn_and_proves_the_agent_answers(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            hung = h.agent.proc
            press = self.press_more(c)
            done, steps = await press('repair', 'rp1')
            self.assertTrue(done['ok'], done)
            self.assertEqual(done['step'], 1, 'the first rung (stop) was enough')
            self.assertFalse(alive(hung.pid), 'the hung app-server is gone (checked)')
            self.assertTrue(self.probes(), 'a real probe turn reached the harness')
            self.assertGreater(getattr(h, 'agent_said_at', 0), 0)
            texts = [x['text'] for x in steps]
            self.assertTrue(any('确认 Agent 能回话' in x for x in texts), texts)
            self.assertIn('停止 ✓', done['text'])
        self.flow(check)

    def test_stella_estop_on_and_stuck_takeover_says_why_and_repair_recovers(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            h.estop = {'on': True}               # 「全部停下」 was pressed and the turn is still stuck (customer report)
            press = self.press_more(c)
            done, _ = await press('takeover', 'tk1')
            self.assertFalse(done['ok'])
            self.assertEqual(done['why'], 'estop')
            self.assertTrue(done.get('resume'), 'a one-tap Resume is offered, not a silent failure')
            self.assertIn('恢复', done['text'])
            done, steps = await press('repair', 'rp2')
            self.assertFalse(done['ok'])
            self.assertEqual((done['why'], done.get('resume'), done.get('cont')), ('estop', True, 'repair'))
            self.assertTrue(h.stopped(), 'a repair never lifts Stop everything by itself (security #585-1/2)')
            self.assertIn(ZH(reset.STOPPED_WAIT), done['text'])
            await h.do_resume('phone:' + c['ph'].did, '手机')        # the owner's own Resume (the card's button)
            done, _ = await press('repair', 'rp2b')                 # the page sends repair again by itself
            self.assertTrue(done['ok'], done)
        self.flow(check)

    def test_stop_ends_the_process_tree_and_withdraws_the_lost_card(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            h.asks['lost'] = type('A', (), {'fut': fut})()         # a card the phone never got (compaction / sub-thread)
            hung = h.agent.proc
            press = self.press_more(c)
            done, _ = await press('stop_hard', 'st1')
            self.assertTrue(done['ok'], done)
            self.assertTrue(fut.done() and fut.result()[0] == 'deny', 'the wait ended like 全部停下 ends it')
            self.assertFalse(alive(hung.pid))
            self.assertIn('确认都不在了', done['text'])
            self.assertIn('撤下', done['text'])
            self.assertFalse(h.stopped(), 'stop does not latch Stop everything')
            h.asks.pop('lost', None)
        self.flow(check)

    def test_restart_agent_answers_one_line_before_it_says_ok(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            os.environ['FAKE_CX_THREADS'] = json.dumps([h.agent.tid])
            old = h.agent.proc
            press = self.press_more(c)
            done, steps = await press('restart_session', 'ra1')
            self.assertTrue(done['ok'], done)
            self.assertFalse(alive(old.pid))
            self.assertIn('Agent 已经回话', done['text'])
            self.assertTrue(self.probes())
        self.flow(check)

    def test_new_conversation_keeps_the_old_one_and_the_new_one_answers(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            old_tid = h.agent.tid
            press = self.press_more(c)
            done, _ = await press('new_chat', 'nc1')
            self.assertTrue(done['ok'], done)
            self.assertEqual(done.get('old_session'), old_tid, 'the old conversation can be brought back')
            undo = json.loads((self.st.root / reset.UNDO).read_text())
            self.assertEqual(undo['session'], old_tid)
            self.assertIn('Agent 已经回话', done['text'])
        self.flow(check)

    def test_restart_service_ends_the_harness_first_and_the_follow_up_proves_it(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            hung = h.agent.proc
            calls = []
            press = self.press_more(c)
            with patch.object(service, 'status', return_value={'installed': True}), \
                    patch.object(service, 'platform', return_value='linux'), \
                    patch.object(service, 'name', return_value='p585-test'), \
                    patch.object(service, '_systemctl', side_effect=lambda *a, **k: calls.append(a)):
                await h._app(c['s'], sign(c['ph'], 'rescue_do', {'action': 'restart'}, {'t': 'rescue_do', 'r': 'rs1', 'action': 'restart'}))
                await c['wait'](lambda: calls, 20000)
            first = [o for _, o in c['sent'] if o.get('t') == 'reset_done' and o.get('r') == 'rs1'][0]
            self.assertTrue(first.get('pending') and first.get('cont') == 'probe', first)
            self.assertFalse(alive(hung.pid), 'the backend process does not outlive the service restart')
            self.assertEqual(calls, [('--no-block', 'restart', 'p585-test.service')])
            rec = json.loads((self.st.root / rescue_repair.MARKER).read_text())
            rec['pid'] = rec['pid'] + 100000                 # as seen by the restarted serve
            (self.st.root / rescue_repair.MARKER).write_text(json.dumps(rec))
            await c['wait'](lambda: not h.reset_desk.busy, 10000)
            done, _ = await press('probe', 'pb1')
            self.assertTrue(done['ok'], done)
            self.assertIn(ZH(reset.RESTARTED), done['text'])
            self.assertIn('Agent 已经回话', done['text'])
        self.flow(check)

    def test_ladder_climbs_to_a_new_conversation_and_offers_support_when_nothing_answers(self):
        async def check(c, h):
            await c['wait'](lambda: isinstance(h.agent.turn_id, str))
            press = self.press_more(c)

            async def deaf(_h, *a, **k):
                return {'ok': False, 'why': 'no_reply'}
            with patch.object(rescue_repair, 'probe', deaf), patch.object(service, 'status', return_value={'installed': False}):
                done, steps = await press('repair', 'rp3', 120000)
            self.assertFalse(done['ok'])
            self.assertEqual(done['why'], 'all_failed')
            self.assertTrue(done.get('report'), 'support with a diagnosis is offered (sent only after the owner agrees)')
            self.assertEqual(sorted({x['step'] for x in steps}), [1, 2, 3, 4], 'every rung was tried')
            for w in ('停止 ✗', '重启 Agent ✗', '换个新对话 ✗'):
                self.assertIn(w, done['text'])
        self.flow(check)


class ClaudeStuck(_P585):
    KIND = 'claude'

    def test_one_tap_repair_recovers_a_claude_turn_that_never_ends(self):
        async def check(c, h):
            hung = h.agent.proc
            press = self.press_more(c)
            done, _ = await press('repair', 'cr1')
            self.assertTrue(done['ok'], done)
            self.assertFalse(alive(hung.pid))
            self.assertGreater(getattr(h, 'agent_said_at', 0), 0)
        self.flow(check)

    def test_restart_agent_on_claude_answers(self):
        async def check(c, h):
            press = self.press_more(c)
            done, _ = await press('restart_session', 'cr2')
            self.assertTrue(done['ok'], done)
            self.assertIn('Agent 已经回话', done['text'])
        self.flow(check)


class ProbeUnit(unittest.IsolatedAsyncioTestCase):
    async def test_probe_never_runs_on_estop_and_withdraws_itself(self):
        from types import SimpleNamespace as NS
        logs = []
        q = []
        h = NS(agent=NS(kind='codex', submit=q.append), stopped=lambda: False, lang='zh', agent_said_at=0,
               st=NS(log=lambda *a, **k: logs.append(k)), eff_status=lambda: 'idle')
        t = [0.0]

        async def sleep(s):
            t[0] += s
        res = await rescue_repair.probe(h, timeout=2, sleep=sleep, clock=lambda: t[0])
        self.assertEqual(res['why'], 'no_reply')
        self.assertEqual(q[0].state, 'cancelled', 'an unanswered probe never follows a restart')
        h.stopped = lambda: True
        self.assertEqual((await rescue_repair.probe(h))['why'], 'estop')

    def test_marker_once_and_pid_change(self):
        import tempfile
        from types import SimpleNamespace as NS
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            st = NS(root=root, write_private=lambda p, b: p.write_bytes(b))
            rescue_repair.write_marker(st, 'repair', 'r1', 'dev', steps=[[1, False]])
            rec = rescue_repair.take_marker(st)
            self.assertFalse(rec['restarted'])
            self.assertEqual(rec['steps'], [[1, False]])
            self.assertIsNone(rescue_repair.take_marker(st), 'read once')


class ServeHung(unittest.TestCase):
    """serve itself does not answer: the rescue watchdog answers 一键修复 / 停止 / 重启 Agent with its own restart, and asks
    the page to prove the Agent answers once serve is back (cont probe) — never 「格式不对」."""

    def test_watchdog_runs_restart_for_the_new_buttons_and_asks_for_the_probe(self):
        import test_p332_watchdog as w
        import threading
        from agentj import rescue_watchdog as rw
        base = w.Base('setUp'); base.setUp()
        try:
            st = base.st
            with w.Relay4001() as relay:
                st.init(relay=relay.url, web="https://m.agentj.app")
                probe, restarted = {"v": "down"}, []

                def restart():
                    restarted.append(1)
                    threading.Timer(0.2, lambda: probe.update(v="ok")).start()
                    return True
                wd = rw.Watchdog(st, poll=0.1, probe_fn=lambda: probe["v"], restart_fn=restart, restart_wait=5,
                                 lang_fn=lambda: "zh", stopped_fn=lambda: False)
                loop, t = w._run(wd)
                ph = w.Phone(st, relay.url)
                try:
                    self.assertTrue(w._wait(lambda: st.config()["channel"] in relay.hosts))
                    ph.connect(); ph.resume()
                    ph.until(lambda m: m["t"] == "rescue")
                    req = ph.signed("rescue_do", "rescue_do", {"action": "repair"}, action="repair")
                    ph.app_send(req)
                    done = ph.until(lambda m: m["t"] == "reset_done", 15)
                    self.assertTrue(done["ok"], done)
                    self.assertEqual((done.get("pending"), done.get("cont")), (True, "probe"))
                    self.assertEqual(restarted, [1])
                finally:
                    ph.close()
                    w._stop(wd, loop, t)
        finally:
            for c in base._cleanups:
                c[0]()


import test_p0269_selfheal as _sh  # noqa: E402


class SharedCodexDaemon(_sh.SharedCodexHeal):
    """Avon / Olares: the stuck turn is in the owner's own Codex daemon. 「停止」 never signals it: a dead turn is let
    through (the waiting message goes on), a live one is named with the way out — never a fake success."""

    def setUp(self):
        super().setUp()
        p = patch.object(__import__('agentj.turn_heal', fromlist=['x']), 'daemon_live', return_value=True)
        p.start()
        self.addCleanup(p.stop)

    async def test_stop_releases_a_dead_desktop_turn_without_touching_the_daemon(self):
        path = self.rollout()
        desk = self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn('hi'))
        await self.wait_for(lambda: a.desktop_wait)
        self.host.asks, self.host.questions = {}, {}
        res = await rescue_repair.stop_hard(self.host, 'dev1')
        self.assertTrue(res['ok'], res)
        await asyncio.wait_for(t, 15)
        self.assertIsNone(desk.poll(), "the owner's daemon is never signalled")

    async def test_stop_names_a_live_desktop_turn_and_the_way_out(self):
        path = self.rollout()
        os.utime(path, None)
        desk = self.holder(path)
        a = self.agent()
        t = asyncio.create_task(a.turn('hi'))
        self.addCleanup(t.cancel)
        await self.wait_for(lambda: a.desktop_wait)
        self.host.asks, self.host.questions = {}, {}
        res = await rescue_repair.stop_hard(self.host, 'dev1')
        self.assertEqual((res['ok'], res['why']), (False, 'desktop_live'))
        self.assertIn('会话模式', rescue_repair.stop_text(res, 'zh'))
        self.assertIsNone(desk.poll())


class Negative(unittest.TestCase):
    def test_support_can_never_offer_or_run_the_new_actions(self):
        for name in ('stop_hard', 'probe', 'repair', 'repair_cont', 'new_chat'):
            self.assertIn(name, reset.RESCUE_ACTIONS)
            self.assertEqual(reset.clean_offer([name]), [], name)
            from agentj import rescue_kit
            self.assertNotIn(name, rescue_kit.AUTO_ACTIONS)
            from agentj import rescue_watchdog
            self.assertNotIn(name, rescue_watchdog.HELP_ACTIONS)

    def test_bad_signature_on_a_new_action_is_refused_and_runs_nothing(self):
        import test_controls as tc
        st_dir = __import__('tempfile').TemporaryDirectory()
        self.addCleanup(st_dir.cleanup)
        from test_slash import _state
        st = _state(st_dir.name)
        sent = []
        ph, host = tc.Phone(st), tc._host(st, sent)
        s = tc._ready(host, ph); s.p33 = True
        ran = []
        host.reset_desk.start = lambda *a: ran.append(a)
        obj = sign(ph, 'rescue_do', {'action': 'probe'}, {'t': 'rescue_do', 'r': 'b' * 16, 'action': 'repair'})   # signed for another action
        asyncio.run(host._app(s, obj))
        done = [o for _, o in sent if o.get('t') == 'reset_done']
        self.assertEqual(done[-1]['why'], 'bad_signature')
        self.assertEqual(ran, [])


def ZH(pair):
    return pair[0]


if __name__ == '__main__':
    unittest.main()
