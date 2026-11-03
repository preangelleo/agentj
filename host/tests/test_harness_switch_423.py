"""#423: real Host status and SharedClaude adapter, isolated HOME; no installed service/models."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
from agentj import names, serve, shared, switch_harness as sw
from test_p155_switch_harness import Clock, UP, fake_probe, mk_state


class SharedSwitch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='h423-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'HOME': str(self.root), 'XDG_CONFIG_HOME': str(self.root/'config'),
                                  'XDG_DATA_HOME': str(self.root/'data'), 'CLAUDE_CONFIG_DIR': str(self.root/'claude')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.st = mk_state(self.tmp.name, 'codex', session_mode='shared')
        self.host = serve.Host(self.st, events='quiet', read_stdin=False)
        self.clock = Clock()
        self.native_model = None
        self.busy = False
        self.restarts = 0
        self.hellos = []
        self.statuses = []
        self.addCleanup(self.evidence)
        self.server = await asyncio.start_unix_server(self.host.on_ctl, path=str(self.st.root/'control.sock'))
        self.addAsyncCleanup(self.server.wait_closed)
        self.addCleanup(self.server.close)
        self.mirror = mock.patch.object(sw, 'receipt_path', side_effect=lambda name=sw.RECEIPT: self.root/'unit'/name)
        self.mirror.start()
        self.addCleanup(self.mirror.stop)
        self.live = mock.patch.object(shared, 'claude_sessions', side_effect=lambda *a: [dict(self.host.agent.session, status='busy' if self.busy else 'idle')])
        self.live.start()
        self.addCleanup(self.live.stop)
        self.loop = asyncio.get_running_loop()

    def evidence(self):
        dest = os.environ.get('H423_EVIDENCE')
        if dest:
            path = Path(dest)/self._testMethodName
            path.mkdir(parents=True, exist_ok=True)
            if self.st.log_path.exists():
                (path/'host.log').write_bytes(self.st.log_path.read_bytes())
            (path/'switch-result.json').write_text(json.dumps(sw.read_receipt(self.st), indent=2))
            (path/'facts.json').write_text(json.dumps({'restarts':self.restarts, 'virtual_seconds':self.clock.t,
                'runtime_models':[x.get('agent_model') for x in self.statuses],
                'configuration_model':(self.st.config().get('agent') or {}).get('model'), 'hellos':self.hellos}, indent=2))

    async def install(self):
        self.restarts += 1
        cfg = self.st.agent_config()
        a = shared.SharedClaudeAgent(self.host, cfg)
        a.kind = cfg['kind']
        a.session = {'sessionId':'isolated-owner', 'pid':os.getpid()}
        a._read_native = lambda: {'model_name': self.native_model} if self.native_model else {}
        a._native_last = a._read_native()
        self.host.agent = a
        self.host.agent_cfg = cfg
        a.status = 'working' if self.busy else 'idle'

    def restart(self):
        asyncio.run_coroutine_threadsafe(self.install(), self.loop).result(10)

    def ctl(self, req):
        r = names.ctl_call(self.st, req, timeout=10)
        if req['cmd'] == 'status':
            self.statuses.append(r)
        if req['cmd'] == 'harness_hello':
            self.hellos.append(req)
            if not self.busy:
                # Native reply seam only; all Host control/provenance/queue/status code is real.
                self.host.agent.q.get_nowait()
                self.host.agent._hello_pending = False
                self.host.agent._hello_at = time.time()
                self.host.agent_said_at = time.time()+1
        return r

    async def run_job(self, model=None):
        return await asyncio.to_thread(sw.worker, self.st, 'claude', model, probe=fake_probe(), ctl=self.ctl,
            restart=self.restart, status=UP, ready=lambda *a, **k:'idle', sleep=self.clock.sleep, clock=self.clock)

    async def test_idle_exact_native_model_positive_control(self):
        self.native_model = 'claude-opus-5-5'
        self.assertEqual(await self.run_job(), 0, sw.read_receipt(self.st))

    async def test_kind_drift_still_rolls_back_and_old_agent_explains(self):
        original = self.ctl
        drifted = False
        def ctl(req):
            nonlocal drifted
            if req['cmd'] == 'status' and self.restarts == 1 and not drifted:
                drifted = True
                cfg = self.st.config()
                cfg['agent']['kind'] = 'opencode'
                self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
            return original(req)
        self.ctl = ctl
        self.assertEqual(await self.run_job(), 2)
        rec = sw.read_receipt(self.st)
        self.assertEqual((rec['status'], rec['reason']), ('rolled_back','config_mismatch'))
        self.assertEqual(self.restarts, 2)
        self.assertEqual(self.hellos[-1]['outcome'], 'rolled_back')

    async def test_write_exception_is_not_config_mismatch(self):
        with mock.patch.object(sw, '_apply', side_effect=OSError('fixture-write-error')):
            self.assertEqual(await self.run_job(), 1)
        self.assertEqual(sw.read_receipt(self.st)['reason'], 'config_write_failed')
        self.assertEqual(self.restarts, 0)

    async def test_idle_unknown_native_model_is_not_disk_mismatch(self):
        rc = await self.run_job()
        self.assertEqual(rc, 0, sw.read_receipt(self.st))
        self.assertEqual(self.restarts, 1)
        self.assertTrue(sw._reads_back(self.st, 'claude'))
        self.assertIsNone(self.st.agent_config()['model'])
        self.assertIsNone(self.statuses[-1]['agent_model'])
        self.assertTrue(self.hellos)

    async def test_idle_native_slug_is_not_disk_mismatch(self):
        self.native_model = 'Opus 5.5'
        self.assertEqual(await self.run_job(), 0, sw.read_receipt(self.st))
        self.assertEqual(self.restarts, 1)

    async def test_saved_claude_model_does_not_override_native_default(self):
        self.st.write_private(self.st.root/sw.SAVED, json.dumps({'claude':{'model':'old-claude-model','effort':'high'}}).encode())
        self.assertEqual(await self.run_job(), 0)
        self.assertIsNone(self.st.agent_config()['model'])

    async def test_explicit_model_and_runtime_display_slug_do_not_roll_back(self):
        self.native_model = 'Opus 5.5'
        self.assertEqual(await self.run_job('claude-opus-5-5'), 0)
        self.assertEqual(self.st.agent_config()['model'], 'claude-opus-5-5')

    async def test_model_config_drift_does_not_roll_back(self):
        original = self.ctl
        def ctl(req):
            if req['cmd'] == 'status' and self.restarts == 1:
                cfg = self.st.config()
                cfg['agent']['model'] = 'newer-default'
                self.st.write_private(self.st.config_path, json.dumps(cfg).encode())
            return original(req)
        self.ctl = ctl
        self.assertEqual(await self.run_job(), 0)
        self.assertEqual(self.restarts, 1)

    async def test_agent_down_still_rolls_back(self):
        original = self.ctl
        def ctl(req):
            result = original(req)
            if req['cmd'] == 'status' and self.restarts == 1:
                result['agent_status'] = 'down'
            return result
        self.ctl = ctl
        self.assertEqual(await self.run_job(), 2)
        self.assertEqual(sw.read_receipt(self.st)['reason'], 'agent_down')
        self.assertEqual(self.restarts, 2)

    async def test_busy_keeps_hello_queued_without_claiming_answer(self):
        self.busy = True
        self.assertEqual(await self.run_job(), 0, sw.read_receipt(self.st))
        rec = sw.read_receipt(self.st)
        self.assertEqual(rec['kept'], 'deferred')
        self.assertEqual(self.restarts, 1)
        self.assertTrue(self.host.agent.hello_deferred())
        self.assertNotIn('已经回答', sw._say_receipt(rec, 'zh'))


class DeferredSend(unittest.IsolatedAsyncioTestCase):
    async def test_hello_waits_for_native_idle_then_delivers_once(self):
        host = mock.Mock()
        host.stopped.return_value = False
        host.agent = None
        a = shared.SharedClaudeAgent(host, {'kind':'claude', 'dir':'/isolated', '_workflow_ceo':True})
        host.agent = a
        a.session = {'sessionId':'isolated-owner', 'pid':os.getpid()}
        a.cur_send = sw.HelloSend('fixed host hello')
        quiet = False
        a._registry_idle = lambda: quiet
        a.native_busy = lambda: not quiet
        a._await_turn_end = mock.AsyncMock()
        a.menu_desk.wait_clear = mock.AsyncMock(return_value=True)
        sent = []
        async def deliver(call):
            await call()
            a.input_seen.set()
        a.deliver = deliver
        with mock.patch.object(shared, 'claude_send', side_effect=lambda *args: sent.append(args)):
            task = asyncio.create_task(a.turn('fixed host hello'))
            try:
                await asyncio.sleep(.03)
                self.assertEqual(sent, [])
                quiet = True
                await asyncio.wait_for(task, 2)
                self.assertEqual(len(sent), 1)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


class EvidenceReasons(unittest.TestCase):
    def test_all_native_model_names_are_observational(self):
        clock = Clock()
        status = {'ok':True, 'agent':'codex', 'agent_status':'idle', 'agent_model':'other'}
        for kind in sw.KINDS:
            status['agent'] = kind
            self.assertIsNone(sw._wait_up(lambda r:status, kind, clock.sleep, clock, limit=2))

    def test_deferred_prior_native_reply_cannot_certify_hello(self):
        clock = Clock()
        def ctl(req):
            if req['cmd'] == 'harness_hello':
                return {'ok':True, 'at':100}
            return {'ok':True, 'agent':'claude', 'agent_status':'working', 'agent_phase':'turn',
                'agent_hello_deferred':clock.t < 2, 'agent_hello_at':102 if clock.t >= 2 else 0, 'agent_said_at':101}
        result = sw._hello(ctl, {'outcome':'ok','from':'codex','to':'claude'}, clock.sleep, clock, wait=4)
        self.assertEqual(result['reason'], 'no_reply')

    def test_pending_failure_and_wrong_agent_outrank_deferred(self):
        for bad in ({'agent':'codex'}, {'agent_status':'down'}, {'agent_err':'auth'}, {'agent_turn_failed':True}, {'agent_hello_failed':True}):
            clock = Clock()
            def ctl(req):
                return {'ok':True, 'at':100} if req['cmd']=='harness_hello' else {
                    'ok':True,'agent':'claude','agent_status':'working','agent_hello_deferred':True, **bad}
            result = sw._hello(ctl, {'outcome':'ok','from':'codex','to':'claude'}, clock.sleep, clock, wait=2)
            self.assertNotIn('ok', result)

    def test_new_result_fields_survive_fixed_metadata(self):
        for record in ({'status':'ok','kept':'deferred'}, {'status':'failed','reason':'config_write_failed'}):
            self.assertEqual(sw._result_meta(record), record)


class QueueLifetime(unittest.IsolatedAsyncioTestCase):
    async def test_hello_then_resume_once_and_replaced_adapter_sends_nothing(self):
        host = mock.Mock()
        host.stopped.return_value = False
        a = shared.SharedClaudeAgent(host, {'kind':'claude','dir':'/isolated','_workflow_ceo':True})
        host.agent = a
        a.session = {'sessionId':'isolated-owner','pid':os.getpid()}
        a.native_busy = lambda: False
        a._registry_idle = lambda: True
        a._await_turn_end = mock.AsyncMock()
        a.menu_desk.wait_clear = mock.AsyncMock(return_value=True)
        sent = []
        async def deliver(call):
            await call()
            a.input_seen.set()
        a.deliver = deliver
        for message in (sw.HelloSend('hello'), sw.HandoffSend('resume')):
            a.submit(message)
        with mock.patch.object(shared, 'claude_send', side_effect=lambda session,text,source: sent.append(text)):
            while not a.q.empty():
                a.cur_send = a.q.get_nowait()
                await a.turn(a.cur_send.text)
            self.assertEqual(sent, ['hello','resume'])
            host.agent = object()
            a.cur_send = sw.HelloSend('obsolete')
            await a.turn(a.cur_send.text)
            self.assertEqual(sent, ['hello','resume'])


class LockVisibility(unittest.IsolatedAsyncioTestCase):
    async def test_dequeued_hello_waiting_for_real_turn_lock_is_deferred(self):
        from types import SimpleNamespace
        host = SimpleNamespace(turn_lock=asyncio.Lock())
        a = shared.SharedClaudeAgent(host, {'kind':'claude','dir':'/isolated','_workflow_ceo':True})
        host.agent = a
        await host.turn_lock.acquire()
        a.submit(sw.HelloSend('hello'))
        task = asyncio.create_task(a.run())
        try:
            await asyncio.sleep(.01)
            self.assertTrue(a.q.empty())
            self.assertIsNone(a.cur_send)
            self.assertTrue(a.hello_deferred())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            host.turn_lock.release()


class EarlyFailure(unittest.IsolatedAsyncioTestCase):
    async def test_attach_and_modal_failure_clear_pending_instead_of_keep(self):
        for stage in ('attach','modal'):
            host = mock.Mock()
            host.stopped.return_value = False
            a = shared.SharedClaudeAgent(host, {'kind':'claude','dir':'/isolated','_workflow_ceo':True})
            host.agent = a
            a.session = None if stage == 'attach' else {'sessionId':'isolated'}
            a.attach = mock.AsyncMock(return_value=False)
            a.menu_desk.wait_clear = mock.AsyncMock(return_value=False)
            a.submit(sw.HelloSend('hello'))
            a.cur_send = a.q.get_nowait()
            await a.turn('hello')
            self.assertFalse(a.hello_deferred())
            self.assertTrue(a._hello_failed)


class NoSystemMerge(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_switch_messages_cannot_merge_into_running_native_turn(self):
        host = mock.Mock()
        host.stopped.return_value = False
        a = shared.SharedClaudeAgent(host, {'kind':'claude','dir':'/isolated','_workflow_ceo':True})
        host.agent = a
        a.session = {'sessionId':'isolated-owner'}
        original = sw.HelloSend('active hello')
        a.cur_send = original
        for queued in (sw.HelloSend('next hello'),sw.HandoffSend('resume')):
            a.submit(queued)
            with mock.patch.object(shared, 'claude_send') as send:
                await a._merge_queued()
                send.assert_not_called()
                self.assertIs(a.cur_send, original)
                self.assertIs(a.q.get_nowait(), queued)


class HaltPending(unittest.IsolatedAsyncioTestCase):
    async def test_halt_clears_pending_and_invalidates_dequeued_fixed_send(self):
        host = mock.Mock()
        host.stopped.return_value = False
        a = shared.SharedClaudeAgent(host, {'kind':'claude','dir':'/isolated','_workflow_ceo':True})
        host.agent = a
        for message in (sw.HelloSend('hello'), sw.HandoffSend('resume')):
            a.submit(message)
        dequeued = a.q.get_nowait()
        self.assertTrue(a.hello_deferred())
        await a.halt(clear_queue=True)
        self.assertFalse(a.hello_deferred())
        self.assertTrue(a._hello_failed)
        self.assertTrue(a.q.empty())
        a.halting = False  # Agent.run resets this after taking turn_lock.
        a.cur_send = dequeued
        self.assertFalse(await a._switch_idle())


    async def test_halt_during_registry_read_invalidates_old_epoch(self):
        host = mock.Mock()
        host.stopped.return_value = False
        a = shared.SharedClaudeAgent(host, {'kind':'claude','dir':'/isolated','_workflow_ceo':True})
        host.agent = a
        a.native_busy = lambda: False
        a.submit(sw.HelloSend('hello'))
        a.cur_send = a.q.get_nowait()
        entered, release = asyncio.Event(), asyncio.Event()
        async def registry_read(call):
            entered.set()
            await release.wait()
            return True
        with mock.patch.object(shared.asyncio, 'to_thread', side_effect=registry_read):
            pending = asyncio.create_task(a._switch_idle())
            await entered.wait()
            await a.halt(clear_queue=True)
            a.halting = False
            release.set()
            self.assertFalse(await pending)
        self.assertFalse(a.hello_deferred())
        self.assertTrue(a._hello_failed)
