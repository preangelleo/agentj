"""P491: failed native startup must not poison the next phone turn."""
import _hermetic
import asyncio
import json
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import agent_opencode as oc, rescue_kit as kit
import test_p59_opencode as p59


class Retry(p59.Chain):
    test_key_change_restarts_owned_serve_same_conversation_and_cli_and_proxy = None

    failure = "exit"

    def test_exit_before_listen_then_next_phone_message(self):
        normal_wait = oc.START_WAIT
        async def script(c):
            a = c.host.agent
            original = a.argv
            delivered = mock.Mock(wraps=c.host.say_delivered)
            c.host.say_delivered = delivered
            calls = []
            def argv(port):
                calls.append(port)
                return [sys.executable, '-c', 'import time; time.sleep(5)' if self.failure == 'timeout' else 'pass'] if len(calls) == 1 and self.failure in ('exit', 'timeout') else original(port)
            a.argv = argv
            prepare, events = a._prepare, a._events
            attempts = [0]
            async def prepare_once():
                attempts[0] += 1
                if self.failure == 'prepare' and attempts[0] == 1:
                    raise AttributeError('synthetic prepare failure')
                return await prepare()
            async def events_once(proc):
                if self.failure == 'events' and len(calls) == 1:
                    return
                return await events(proc)
            a._prepare, a._events = prepare_once, events_once
            await c.say('first startup fails')
            await c.wait(lambda: a.status == 'down' and a.failed_start)
            delivered.assert_not_called()
            self.assertIsNone(a.proc)
            self.assertIsNone(a.client)
            self.assertIsNone(a.sid)
            self.assertNotIn('AttributeError', '\n'.join(c.msgs()))
            # A failed startup never writes a native prompt, nor claims delivery.
            if self.log.exists():
                self.assertNotIn('prompt_async', self.log.read_text())
            oc.START_WAIT = normal_wait
            await c.say('second message succeeds')
            await c.wait(lambda: 'ECHO: second message succeeds' in c.msgs())
            await c.wait(lambda: a.status == 'idle')
            self.assertEqual(len(calls), 2)
            delivered.assert_called_once()
            self.assertTrue(a.readiness()['ready'])
            records = [json.loads(x) for x in self.log.read_text().splitlines()]
            prompts = [x for x in records if x.get('path', '').endswith('/prompt_async')]
            self.assertEqual(len(prompts), 1, records)
        with mock.patch.object(oc, "CONNECT_WAIT", 1), mock.patch.object(oc, "START_WAIT", 0.1 if self.failure == "timeout" else oc.START_WAIT):
            self.run_chain(script)


class SharedRetry(Retry):
    shared = True


class TimeoutRetry(Retry):
    failure = "timeout"


class SharedTimeoutRetry(SharedRetry):
    failure = "timeout"


class PrepareRetry(Retry):
    failure = "prepare"


class SharedPrepareRetry(SharedRetry):
    failure = "prepare"


class EventsRetry(Retry):
    failure = "events"


class SharedEventsRetry(SharedRetry):
    failure = "events"


class Cancel(p59.Chain):
    test_key_change_restarts_owned_serve_same_conversation_and_cli_and_proxy = None

    def test_cancel_during_startup_reaps_owned_child_and_preserves_pin(self):
        async def script(c):
            a = c.host.agent
            a.argv = lambda port: [sys.executable, '-c', 'import time; time.sleep(20)']
            self.st.set_agent_session('opencode', 'ses_keep_native_history')
            task = getattr(a, "attach_task", None) or asyncio.create_task(a._spawn())
            await c.wait(lambda: a.proc is not None)
            old = a.proc
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((a.proc, a.client, a.sid), (None, None, None))
            self.assertIsNotNone(old.returncode)
            self.assertEqual(self.st.agent_session('opencode'), 'ses_keep_native_history')
        self.run_chain(script)


class SharedCancel(Cancel):
    shared = True


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    def agent(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.proc = SimpleNamespace(returncode=1)
        a.client, a.sid = object(), 'old'
        a.connected = asyncio.Event(); a.connected.set()
        a.failed_start = False
        a.host = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()), lang='zh')
        return a

    async def test_old_cleanup_never_clears_new_client(self):
        a = self.agent(); old = a.proc
        a.proc = SimpleNamespace(returncode=None)
        client = a.client
        self.assertFalse(a._retire(old))
        self.assertIs(a.client, client)
        self.assertEqual(a.sid, 'old')
        self.assertTrue(a.connected.is_set())

    async def test_old_watch_cannot_finish_new_turn(self):
        a = self.agent()
        old = mock.Mock(wait=mock.AsyncMock(return_value=1))
        a.turn_proc = None; a.turn_done = asyncio.Event()
        pending = asyncio.get_running_loop().create_future()
        a.pending = {"new": pending}; a.ended = set(); a._busy = mock.Mock()
        await a._watch(old)
        self.assertFalse(a.turn_done.is_set()); self.assertFalse(pending.done())
        self.assertIsNotNone(a.client)

    async def test_kill_wait_does_not_clear_a_new_generation(self):
        a = self.agent(); a.ended = set()
        old = mock.Mock(returncode=None)
        a.proc = old
        newer, client = mock.Mock(returncode=None), object()
        async def wait_old():
            a.proc, a.client, a.sid = newer, client, 'new-session'
            old.returncode = 0
        old.wait = mock.AsyncMock(side_effect=wait_old)
        with mock.patch.object(oc, '_signal_tree'):
            await a._kill(old)
        self.assertIs(a.proc, newer); self.assertIs(a.client, client)
        self.assertEqual(a.sid, 'new-session')

    async def test_dead_or_missing_client_is_not_ready_and_stale_sid_clears(self):
        a = self.agent()
        self.assertFalse(a._transport_ready())
        old = a.proc
        self.assertTrue(a._retire(old))
        self.assertEqual((a.proc, a.client, a.sid), (None, None, None))
        a.proc = SimpleNamespace(returncode=None)
        self.assertFalse(a._transport_ready())

    async def test_session_guard_fails_closed_without_native_request(self):
        from agentj.shared import SharedOpenCodeAgent
        for cls in (oc.OpenCodeAgent, SharedOpenCodeAgent):
            a = self.agent(); a.owned_server = True
            a.fail_notice = mock.Mock()
            a._prepare = mock.AsyncMock(); a._attach = mock.AsyncMock()
            self.assertFalse(await cls._session_ok(a))
            a._prepare.assert_not_awaited(); a._attach.assert_not_awaited()
            self.assertNotIn('AttributeError', a.fail_notice.call_args.args[0])


class DeliveryUncertain(unittest.IsolatedAsyncioTestCase):
    async def test_transport_error_after_write_never_replays_prompt(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from agentj.shared_opencode2 import SharedOpenCodeV2Agent
        for cls in (oc.OpenCodeAgent, OpenCodeV2Agent, SharedOpenCodeV2Agent):
            with self.subTest(adapter=cls.__name__):
                h = SimpleNamespace(st=mock.Mock(), lang='zh')
                a = cls(h, {'kind':'opencode', 'dir':'/tmp', 'session_mode':'independent'}, persist=False)
                a.proc = mock.Mock(returncode=None); a.sid = 'ses_accept_then_disconnect'
                native = mock.AsyncMock(side_effect=asyncio.TimeoutError)
                a.client = SimpleNamespace(request=native)
                a._protocol = mock.AsyncMock(return_value=False)
                a._creds_check = mock.AsyncMock(); a._ensure_started = mock.AsyncMock(return_value=True)
                a._session_ok = mock.AsyncMock(return_value=True)
                a._identity = a._model = a._reminder = mock.AsyncMock()
                a._kill = mock.AsyncMock(); a.fail_notice = mock.Mock()
                await a.turn('do this exactly once')
                self.assertEqual(native.await_count, 1)
                self.assertEqual(a._ensure_started.await_count, 1)
                self.assertNotIn('TimeoutError', a.fail_notice.call_args.args[0])


class RecoveryReadiness(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_cannot_claim_ai_ready_from_relay_or_idle(self):
        from agentj.reset import HostOps
        h = SimpleNamespace(ws=None, relay_up=True, eff_status=lambda:'idle',
                            agent=SimpleNamespace(kind='opencode', readiness=lambda:{'ready':False}))
        ops = HostOps(h)
        self.assertFalse((await ops.reconnect())['ok'])
        h.agent.readiness=lambda:{'ready':True}
        self.assertTrue((await ops.reconnect())['ok'])


class Rescue(unittest.TestCase):
    def test_relay_online_does_not_prove_ai_ready(self):
        for native, want in [({'ready': True, 'code': 'adapter_ready'}, kit.OK),
                             ({'ready': False, 'code': 'process_exited'}, kit.FAIL),
                             (None, kit.FAIL)]:
            r = kit.Report()
            reply = {'ok': True, 'relay_up': True, 'harness_readiness': native}
            with mock.patch.object(kit, 'agentj_bin', return_value='/fixture/agentj'), \
                 mock.patch.object(kit, 'run', return_value=(0, json.dumps(reply))), mock.patch.object(kit, 'say'):
                kit.check_harness(r)
            self.assertEqual(r.get('harness'), want)
            self.assertEqual(kit.verdict(r), want == kit.OK)

    def test_missing_readiness_never_verdict_healthy(self):
        self.assertFalse(kit.verdict(kit.Report()))


class ResumeRaces(unittest.IsolatedAsyncioTestCase):
    """RESUME1: pause real adapter awaits, replace their owner, then release stale replies."""
    def agent(self, cls=oc.OpenCodeAgent):
        a = object.__new__(cls)
        a.proc = SimpleNamespace(returncode=None)
        a.client = SimpleNamespace(request=mock.AsyncMock())
        a.sid = 'ses_old'
        a.turn_done = asyncio.Event()
        a.connected = asyncio.Event(); a.connected.set()
        a.failed_start = False
        a.host = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()))
        a.pending = {'new-approval': asyncio.get_running_loop().create_future()}
        a._kids = {'ses_child': {}}
        a.turn_t0 = 10000
        a._on_perm = mock.Mock(); a._turn_end = mock.Mock()
        a._text_part = mock.Mock(); a._text = mock.Mock()
        a._busy = mock.Mock(); a.on_event = mock.Mock()
        return a

    def replace(self, a, change):
        if change == 'process':
            a.proc = SimpleNamespace(returncode=None)
        elif change == 'client':
            a.client = SimpleNamespace(request=mock.AsyncMock())
        elif change == 'session':
            a.sid = 'ses_new'
        else:
            a.turn_done = asyncio.Event()

    async def test_owned_shared_restart_cleanup_preserves_replacement(self):
        from agentj.shared import SharedOpenCodeAgent
        a = self.agent(SharedOpenCodeAgent)
        a.cfg = {'dir': '/tmp', 'language': 'en'}
        a.owned_server = True; a.persist = False; a.v2 = False
        a.risk_channel = None; a.risk = mock.Mock()
        a.phone_messages = set(); a.collect = None
        a._creds_check = mock.AsyncMock(); a._ensure_started = mock.AsyncMock(return_value=True)
        a._session_ok = mock.AsyncMock(return_value=True)
        a.deliver = mock.AsyncMock()
        a._wait_turn = mock.AsyncMock()
        a.restart_after_turn = False
        async def finish():
            a.restart_after_turn = True
        a._wait_turn.side_effect = finish
        entered, release = asyncio.Event(), asyncio.Event()
        old = a.proc
        async def kill(proc):
            self.assertIs(proc, old)
            entered.set(); await release.wait()
        a._kill = mock.AsyncMock(side_effect=kill)
        task = asyncio.create_task(a.turn('one turn'))
        await asyncio.wait_for(entered.wait(), 1)
        self.replace(a, 'process'); self.replace(a, 'client'); self.replace(a, 'session')
        newer, client, sid = a.proc, a.client, a.sid
        release.set(); await task
        self.assertIs(a.proc, newer); self.assertIs(a.client, client)
        self.assertEqual(a.sid, sid); self.assertTrue(a.readiness()['ready'])

    async def test_stream_loss_revokes_readiness_until_live_event(self):
        for failure in ('eof', 'http', 'timeout'):
            with self.subTest(failure=failure):
                a = self.agent()
                sleeping, release, live = asyncio.Event(), asyncio.Event(), asyncio.Event()
                calls = 0
                async def events(**kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        if failure == 'http':
                            raise oc.HTTPError('fixture refused')
                        if failure == 'timeout':
                            raise asyncio.TimeoutError
                        return
                    live.set()
                    await release.wait()
                    yield {'type': 'server.connected'}
                    await asyncio.Event().wait()
                async def sleep(delay):
                    sleeping.set(); await release.wait()
                a.client.events = events
                a._resync = mock.AsyncMock()
                with mock.patch.object(oc.asyncio, 'sleep', side_effect=sleep):
                    task = asyncio.create_task(a._events(a.proc))
                    try:
                        await asyncio.wait_for(sleeping.wait(), 1)
                        self.assertFalse(a.readiness()['ready'])
                        release.set()
                        await asyncio.wait_for(live.wait(), 1)
                        await asyncio.wait_for(a.connected.wait(), 1)
                        self.assertTrue(a.readiness()['ready'])
                        a._resync.assert_awaited_once()
                    finally:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

    async def test_resync_awaits_cannot_mutate_replacement(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        for cls in (oc.OpenCodeAgent, OpenCodeV2Agent):
            stages = ('permission', 'catch_up', 'status') + (('child',) if cls is OpenCodeV2Agent else ())
            for stage in stages:
                for change in ('process', 'client', 'session', 'turn'):
                    with self.subTest(adapter=cls.__name__, stage=stage, change=change):
                        a = self.agent(cls)
                        entered, release = asyncio.Event(), asyncio.Event()
                        async def pause():
                            entered.set(); await release.wait()
                        async def request(method, path):
                            at = 'child' if 'ses_child' in path else 'permission' if path.endswith('/permission') else 'status'
                            if at == stage:
                                await pause()
                            if at in ('permission', 'child'):
                                # Earlier requests retain the pending approval; stale response removes it.
                                rows = [] if at == stage else [{'id': 'new-approval'}]
                                return 200, {'data': rows} if cls is OpenCodeV2Agent else rows
                            return 200, {}  # absent session is idle in v1
                        async def catch_up():
                            if stage == 'catch_up':
                                await pause()
                        async def idle():
                            if stage == 'status':
                                await pause()
                            return True
                        a.client.request.side_effect = request
                        a._catch_up = mock.AsyncMock(side_effect=catch_up)
                        a._idle_since_turn = mock.AsyncMock(side_effect=idle)
                        task = asyncio.create_task(a._resync())
                        await asyncio.wait_for(entered.wait(), 1)
                        self.replace(a, change)
                        a._on_perm.reset_mock()
                        release.set(); await task
                        self.assertFalse(a.pending['new-approval'].done())
                        a._on_perm.assert_not_called(); a._turn_end.assert_not_called()

    async def test_resync_return_cannot_dispatch_stale_sse_event(self):
        for change in ('process', 'client'):
            with self.subTest(change=change):
                a = self.agent(); old = a.proc
                sleeping, release = asyncio.Event(), asyncio.Event()
                async def events(**kwargs):
                    if not sleeping.is_set():
                        return
                    yield {'type': 'old-event'}
                    old.returncode = 0
                async def sleep(delay):
                    sleeping.set()
                async def resync():
                    self.replace(a, change)
                    release.set()
                a.client.events = events
                a._resync = mock.AsyncMock(side_effect=resync)
                with mock.patch.object(oc.asyncio, 'sleep', side_effect=sleep):
                    await asyncio.wait_for(a._events(old), 1)
                self.assertTrue(release.is_set())
                a.on_event.assert_not_called(); a._busy.assert_not_called()
                self.assertTrue(a.connected.is_set())  # stale stream must not revoke a replacement

    async def test_resync_return_discards_old_turn_but_keeps_live_stream(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from agentj.shared import SharedOpenCodeAgent
        from agentj.shared_opencode2 import SharedOpenCodeV2Agent
        for cls in (oc.OpenCodeAgent, SharedOpenCodeAgent, OpenCodeV2Agent, SharedOpenCodeV2Agent):
            for change in ('session', 'turn', 'unchanged'):
                with self.subTest(adapter=cls.__name__, change=change):
                    a = self.agent(cls); proc, client = a.proc, a.client
                    entered, release = asyncio.Event(), asyncio.Event()
                    reconnected = False
                    stale = {'type': 'session.execution.succeeded' if a.v2 else 'session.idle',
                             'data' if a.v2 else 'properties': {'sessionID': a.sid}}
                    live = {'type': 'live-event'}
                    a.saw_busy = True; a.desktop_turn = False; a.touch = mock.Mock()
                    a.host.desktop_end = mock.Mock()
                    a._turn_end.side_effect = lambda: a.turn_done.set()
                    a.on_event = mock.Mock(wraps=lambda ev: cls.on_event(a, ev))
                    async def events(**kwargs):
                        if not reconnected:
                            return
                        yield stale
                        # Assert before the following live event can hide the stale mutation.
                        self.assertIs(a.proc, proc); self.assertIs(a.client, client)
                        self.assertEqual(a.turn_done.is_set(), change == 'unchanged')
                        self.assertEqual(a.on_event.call_args_list,
                                         [mock.call(stale)] if change == 'unchanged' else [])
                        yield live
                        proc.returncode = 0
                    async def sleep(delay):
                        nonlocal reconnected
                        reconnected = True
                    async def resync():
                        entered.set(); await release.wait()
                    a.client.events = events
                    a._resync = mock.AsyncMock(side_effect=resync)
                    with mock.patch.object(oc.asyncio, 'sleep', side_effect=sleep):
                        task = asyncio.create_task(a._events(proc))
                        try:
                            await asyncio.wait_for(entered.wait(), 1)
                            if change != 'unchanged':
                                self.replace(a, change)
                            release.set()
                            await asyncio.wait_for(task, 1)
                        finally:
                            if not task.done():
                                task.cancel()
                                await asyncio.gather(task, return_exceptions=True)
                    expected = [stale, live] if change == 'unchanged' else [live]
                    self.assertEqual(a.on_event.call_args_list, [mock.call(ev) for ev in expected])
                    self.assertEqual(a._busy.call_args_list, [mock.call(ev) for ev in expected])
                    self.assertEqual(a._turn_end.call_count, int(change == 'unchanged'))
                    self.assertFalse(a.pending['new-approval'].done())

    async def test_catch_up_cannot_deliver_replaced_session_text(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from agentj.shared import SharedOpenCodeAgent
        for cls in (oc.OpenCodeAgent, SharedOpenCodeAgent, OpenCodeV2Agent):
            for change in ('process', 'client', 'session', 'turn'):
                with self.subTest(adapter=cls.__name__, change=change):
                    a = self.agent(cls)
                    a.message_roles = {}
                    entered, release = asyncio.Event(), asyncio.Event()
                    async def messages(*args):
                        entered.set(); await release.wait()
                        return [{'type': 'assistant', 'id': 'old-text', 'time': {'created': 10000},
                                 'content': [{'type': 'text', 'text': 'stale'}],
                                 'info': {'id': 'old-text', 'role': 'assistant', 'time': {'created': 10000}},
                                 'parts': [{'type': 'text', 'text': 'stale'}]}]
                    async def request(*args):
                        return 200, await messages()
                    a.client.request.side_effect = request
                    a._messages = mock.AsyncMock(side_effect=messages)
                    task = asyncio.create_task(a._catch_up())
                    await asyncio.wait_for(entered.wait(), 1)
                    self.replace(a, change)
                    release.set(); await task
                    a._text_part.assert_not_called(); a._text.assert_not_called()
                    a.on_event.assert_not_called(); self.assertEqual(a.message_roles, {})


    async def test_live_resync_still_reconciles_approvals_text_and_turn(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        from agentj.shared import SharedOpenCodeAgent
        from agentj.shared_opencode2 import SharedOpenCodeV2Agent
        for cls in (oc.OpenCodeAgent, SharedOpenCodeAgent, OpenCodeV2Agent, SharedOpenCodeV2Agent):
            with self.subTest(adapter=cls.__name__):
                a = self.agent(cls); a.message_roles = {}
                async def request(method, path):
                    if path.endswith('/permission'):
                        data = []
                    elif path.endswith('/message'):
                        data = [{'type': 'assistant', 'id': 'live-text', 'time': {'created': 10000},
                                 'content': [{'type': 'text', 'text': 'live'}],
                                 'info': {'id': 'live-text', 'role': 'assistant', 'time': {'created': 10000}},
                                 'parts': [{'type': 'text', 'text': 'live'}]}]
                    elif path.startswith('/api/'):
                        data = {'time': {'idle': 10001}}
                    else:
                        data = {'ses_old': {'type': 'idle'}}
                    return 200, {'data': data} if a.v2 else data
                a.client.request.side_effect = request
                await a._resync()
                self.assertTrue(a.pending['new-approval'].done())
                a._turn_end.assert_called_once()
                (a._text if a.v2 else a._text_part).assert_called_once()
