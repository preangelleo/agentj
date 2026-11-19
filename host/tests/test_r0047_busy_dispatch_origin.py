"""R-0047 item 3 (chairman 11-18 「busy-run 派单按已验证来源归类，仅派单存活期间有效；不给 owner_dispatch、不免卡花钱」).

Observed path (real Host, before this change): a `agentj busy run -- codex exec` dispatch's approvals take two routes,
both through busy_native._runner_host's Proxy (never owner_avatar.ask_for):
  A. the runner's Codex app-server approval requests (CodexAgent._decide) → Host.ask(background="task") →
     approval_offlist.source → `unknown` (the customer's 「来源未知」 refusal of an owner-ordered write);
  B. the runner's PreToolUse control channel (shared_risk.NativePathGuard, full-access threads: fingerprint / protected
     calls) → Host.ask with NO background → source borrowed the MAIN thread's current turn (owner card, or a heartbeat
     turn's card-free `fingerprint_headless` charge).
Now both carry the dispatch's own host-held binding (busy_native.Dispatch, made in start()): its label is fixed at
dispatch time and used only while that exact dispatch is live.

Real Host + real busy_native.start / _runner_host / CodexAgent / NativePathGuard / list_approval; `policy` (the kernel
caller proof, covered by its own tests) and the Codex process are stand-ins. Synthetic state, HOME and device only.
"""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import secrets
import shlex
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import approval_offlist, busy_native, compose, provenance, uv_list
from agentj.agent_codex import CodexAgent
from agentj.same_owner import ForwardSend
from agentj.shared_risk import NativePathGuard
import test_heartbeat_readonly_cards_0306 as hb

CHARGE = 'stripe charges create --amount 100 --currency usd'
FORCE = 'git push --force origin main'


def _cfg(cwd):
    return {'kind': 'codex', 'dir': str(cwd), '_workflow_ceo': True, 'session_mode': 'independent',
            'follow_harness': False, '_native_permissions': {}}


class _Fixture(hb._Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.holds = []
        self.addAsyncCleanup(self._release)

    async def _release(self):
        for hold in self.holds:
            hold.set()
        for row in getattr(self.h, '_busy_native', {}).values():
            task = row.get('task')
            if task is not None and not task.done():
                task.cancel()
            await asyncio.gather(*([task] if task else []), return_exceptions=True)

    def owner(self):
        s = compose.Send(self.ph.did, 'sid', 1, text='owner order', by=None); s.source = provenance.phone()
        return s

    def verified(self):
        return {'verified': True, 'suspicious': False, 'channel': 'phone', 'session': str(self.h.hist.epoch),
                'text': '', 'chain': []}

    async def dispatch(self, main_send, origin, key=None, binding_pid=None):
        """busy_native.start for real; `policy` returns the given origin; the job waits until released."""
        self.h.agent.cur_send = main_send
        key = key or 'run:' + secrets.token_hex(6)
        started, hold = asyncio.Event(), asyncio.Event()
        self.holds.append(hold)
        async def job(host, cfg, text, mode, it):
            started.set()
            await hold.wait()
        with mock.patch.object(busy_native, 'policy', return_value=('task', _cfg(self.cwd), origin, 'danger-full-access', '42')), \
             mock.patch.object(busy_native, 'run', job):
            await busy_native.start(self.h, {'key': key, 'argv': ['codex', 'exec', 'task'], 'cwd': str(self.cwd)},
                                    binding_pid or os.getpid(), os.getuid())
        await asyncio.wait_for(started.wait(), 2)
        return self.h._busy_native[key]['dispatch']

    def runner(self, dispatch):
        proxy = busy_native._runner_host(self.h, dispatch)
        r = CodexAgent(proxy, _cfg(self.cwd), persist=False)
        r.thread = {'sandbox': {'type': 'dangerFullAccess'}}; r.tid = 'busy-' + secrets.token_hex(3); r.pending = {}; r.out = []
        r._send = lambda obj, r=r: r.out.append(obj)
        proxy.runner = r
        return r

    async def codex_approval(self, r, command):
        """Route A: the runner's Codex app-server approval request → ('accept'|'decline', ask_done row)."""
        rid = secrets.randbelow(10 ** 6)
        r._dispatch_request({'method': 'item/commandExecution/requestApproval', 'id': rid,
                             'params': {'command': '/bin/bash -lc ' + shlex.quote(command), 'cwd': str(self.cwd),
                                        'threadId': r.tid, 'turnId': 't', 'itemId': 'i'}})
        for _ in range(600):
            if self.h.asks or any(o.get('id') == rid for o in r.out):
                break
            await asyncio.sleep(.005)
        if self.h.asks:
            return 'card', None
        answer = next(o for o in r.out if o.get('id') == rid)['result']['decision']
        return answer, self.last_done()

    def guard(self, r, command):
        """Route B: the runner's PreToolUse control channel (full-access thread)."""
        return asyncio.create_task(NativePathGuard(r).hook_event({
            'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': command},
            'cwd': str(self.cwd), 'session_id': r.tid, '_agentj_path_authenticated': True}))

    def last_done(self):
        rows = [json.loads(line) for line in self.st.log_path.read_text().splitlines() if '"ask_done"' in line]
        return rows[-1] if rows else {}

    def reasons(self):
        return [json.loads(line).get('reason') for line in self.st.log_path.read_text().splitlines() if '"ask_done"' in line]

    def write(self):
        return 'echo synthetic > ' + str(self.cwd / ('out-' + secrets.token_hex(3) + '.txt'))


class Repro(_Fixture):
    async def test_unbound_runner_is_unknown_on_both_routes(self):
        # Before: route A said `unknown`; route B borrowed the main turn (heartbeat → card-free charge). Now both are
        # the dispatch's own label, and a runner with no live binding is `unknown` whatever the main thread runs.
        for main in (self.owner(), self.heartbeat()):
            self.h.agent.cur_send = main
            r = self.runner(None)
            answer, row = await self.codex_approval(r, self.write())
            self.assertEqual(('decline', 'off_list_write_unknown', 'unknown'), (answer, row.get('reason'), row.get('source_type')))
            out = await self.outcome(self.guard(r, CHARGE))
            self.assertEqual('none', out[0])
            self.assertEqual('deny', out[1]['hookSpecificOutput']['permissionDecision'])
        self.assertNotIn('fingerprint_headless', self.reasons())

    async def test_run_binds_the_registered_dispatch(self):
        from agentj import agent_codex, busy
        dispatch = await self.dispatch(self.owner(), self.verified())
        seen = []
        class Adapter:
            kind, tid = 'codex', 'synthetic-thread'
            def __init__(self, host, cfg, **kw):
                self.host, self.cfg, self.proc = host, cfg, None
            async def run_once(self, text):
                with mock.patch.object(self.host._h, 'ask', side_effect=lambda *a, **kw: seen.append(kw) or {'behavior': 'deny'}):
                    await self.host.ask('Bash', {'command': 'true'}, None, _harness_permission=True, background='task')
                return '', 0
        with mock.patch.object(agent_codex, 'CodexAgent', Adapter), mock.patch.object(busy, 'wait_capacity', return_value=True):
            await busy_native.run(self.h, _cfg(self.cwd), 'task', 'read-only', dispatch.item)
        self.assertEqual(('busy_dispatch', dispatch), seen[0]['background'])
        self.assertTrue(dispatch.ended)                       # the job is over: no authority afterwards
        self.assertEqual('unknown', approval_offlist.source(self.h, background=('busy_dispatch', dispatch))['type'])
        # An item that is not the registered one gets no binding.
        stray = dict(dispatch.item)
        with mock.patch.object(agent_codex, 'CodexAgent', Adapter), mock.patch.object(busy, 'wait_capacity', return_value=True):
            seen.clear()
            await busy_native.run(self.h, _cfg(self.cwd), 'task', 'read-only', stray)
        self.assertEqual(('busy_dispatch', None), seen[0]['background'])


class HuaweiFileJourney(_Fixture):
    async def test_file_change_writes_only_while_its_dispatch_is_live(self):
        # A local app-server stand-in applies exactly the file change accepted by the real adapter.
        dispatch = await self.dispatch(self.owner(), self.verified())
        runner = self.runner(dispatch)
        runner.call = mock.AsyncMock()  # acknowledge the model's policy notice on decline
        payload = "# synthetic monitor\n"
        for rid, live in enumerate((True, False), start=1):
            with self.subTest(live=live):
                path = self.cwd / f"monitor-{rid}.py"
                item_id = f"create-{rid}"
                params = {"threadId": runner.tid, "turnId": "t", "itemId": item_id}
                item = {"id": item_id, "type": "fileChange", "changes": [
                    {"path": str(path), "kind": {"type": "add"}, "diff": payload}]}
                runner._on_note("item/started", {**params, "item": item})
                reply = asyncio.get_running_loop().create_future()
                def receive(response):
                    if response.get("id") == rid and "result" in response:
                        result = response["result"]["decision"]
                        if result == "accept":
                            path.write_text(payload)
                        reply.set_result(result)
                runner._send = receive
                runner._on_request({"id": rid, "method": "item/fileChange/requestApproval", "params": params})
                decision = await asyncio.wait_for(reply, 3)
                self.assertEqual("accept" if live else "decline", decision)
                self.assertEqual(live, path.exists())
                if live:
                    self.assertEqual(payload, path.read_text())
                    row = self.last_done()
                    self.assertEqual(("busy_dispatch", "host"), (row["source_type"], row["trust"]))
                    self.holds[-1].set()
                    await asyncio.wait_for(dispatch.task, 2)
                    self.assertFalse(dispatch.live(self.h))

    async def test_completion_directory_query_gets_an_accurate_refusal(self):
        self.h.agent.cur_send = self.completion()
        receipt = {}
        result = await self.h.ask("Bash", {"command": "find . -type f"}, _receipt=receipt)
        self.assertEqual("deny", result["behavior"])
        self.assertEqual("off_list_write_host_read", result["reason"])
        self.assertFalse(receipt["approval_card_shown"])
        self.assertNotIn("清单外的写操作", result["message"])
        self.assertNotIn("an off-list write", result["message"])
        self.assertIn("不是主人拒绝", result["message"])


class Origins(_Fixture):
    async def test_owner_dispatch_keeps_its_label_when_the_main_turn_changes(self):
        dispatch = await self.dispatch(self.owner(), self.verified())
        label = dispatch.label
        self.assertEqual(('busy_dispatch', 'host'), (label['type'], label['trust']))
        self.assertNotIn('owner_dispatch', label)
        friend = ForwardSend(mock.Mock(), 'f' * 16, 'tok', 1, 'synthetic', approved=True)
        for main in (self.heartbeat(), friend, self.unknown(), None):
            with self.subTest(main=type(main).__name__):
                self.h.agent.cur_send = main
                answer, row = await self.codex_approval(self.runner(dispatch), self.write())
                self.assertEqual(('accept', 'off_list', 'busy_dispatch', 'host'),
                                 (answer, row.get('reason'), row.get('source_type'), row.get('trust')))
        src = approval_offlist.source(self.h, background=('busy_dispatch', dispatch))
        self.assertNotIn('owner_dispatch', src)

    async def test_non_owner_origins_keep_their_own_trust(self):
        same_owner = ForwardSend(mock.Mock(), 'e' * 16, 'tok', 1, 'synthetic')
        guest = compose.Send('dev', 'sid', 1, text='x', by=None); guest.source = provenance.email_guest('guest@example.com')
        cases = (('same_owner', same_owner, {}, 'external'),
                 ('guest_mail', guest, {}, 'external'),
                 ('heartbeat', self.heartbeat(), {}, 'host_read'),
                 ('background_done', self.completion(), {}, 'host_read'),
                 ('unknown', self.unknown(), {}, 'unknown'),
                 # _main_peer admits an owner-turn descendant with an unverified / suspicious origin: never trusted.
                 ('owner_unverified', self.owner(), {}, 'unknown'),
                 ('owner_suspicious', self.owner(), dict(self.verified(), suspicious=True), 'unknown'),
                 ('owner_verified_not_true', self.owner(), dict(self.verified(), verified='yes'), 'unknown'))
        for name, main, origin, trust in cases:
            with self.subTest(origin=name):
                dispatch = await self.dispatch(main, origin)
                self.assertEqual(('busy_dispatch', trust), (dispatch.label['type'], dispatch.label['trust']))
                self.h.agent.cur_send = self.owner()               # an owner turn starts afterwards
                answer, row = await self.codex_approval(self.runner(dispatch), self.write())
                self.assertEqual(('decline', 'off_list_write_' + trust), (answer, row.get('reason')))
                out = await self.outcome(self.guard(self.runner(dispatch), CHARGE))
                self.assertEqual('none', out[0])                   # a non-owner dispatch's charge: refused, no card
                self.assertEqual('deny', out[1]['hookSpecificOutput']['permissionDecision'])
        self.assertNotIn('fingerprint_headless', self.reasons())

    async def test_independent_terminal_origin(self):
        dispatch = await self.dispatch(self.unknown(), busy_native._TerminalOrigin(self.verified(), channel='terminal'))
        self.assertEqual(('busy_dispatch', 'host'), (dispatch.label['type'], dispatch.label['trust']))
        answer, row = await self.codex_approval(self.runner(dispatch), self.write())
        self.assertEqual(('accept', 'off_list'), (answer, row.get('reason')))
        # A plain dict that only looks like the terminal origin is not one (and is unverified here → unknown).
        plain = await self.dispatch(self.unknown(), dict(self.verified(), channel='terminal'))
        self.assertEqual('unknown', plain.label['trust'])

    def test_independent_terminal_policy_keeps_its_sandbox_ceiling(self):
        from agentj import approval_material, busy_launch, ctl_auth
        agent = SimpleNamespace(kind='codex', cfg={'dir': '/w'}, thread={'sandbox': {'type': 'workspaceWrite'}}, human={})
        host = SimpleNamespace(agent=agent, hist=SimpleNamespace(epoch='e1'), _agent_pids=lambda: set())
        pid = os.getpid()
        with mock.patch.object(busy_launch, 'procs', return_value={pid: (1, 0, '7')}), \
             mock.patch.object(approval_material, 'verified_caller_origin', return_value=None), \
             mock.patch.object(busy_native, '_main_peer', return_value=False), \
             mock.patch.object(ctl_auth, 'refusal', return_value=None), mock.patch.object(ctl_auth, 'has_tty', return_value=True):
            req = {'argv': ['codex', 'exec', '--sandbox', 'danger-full-access', 'task'], 'cwd': '/w'}
            _, _, origin, mode, _ = busy_native.policy(host, req, pid, os.getuid())
            self.assertIs(type(origin), busy_native._TerminalOrigin)
            self.assertEqual('workspace-write', mode)             # requested full access, ceiling = the session's
            with self.assertRaises(ValueError):                   # no explicit --sandbox: refused as before
                busy_native.policy(host, {'argv': ['codex', 'exec', 'task'], 'cwd': '/w'}, pid, os.getuid())

    def test_friend_turn_dispatch_refused_at_policy(self):
        friend = ForwardSend(mock.Mock(), 'f' * 16, 'tok', 1, 'synthetic', approved=True)
        agent = SimpleNamespace(cur_send=friend, session={'pid': 1, 'procStart': '1'})
        self.assertFalse(busy_native._main_peer(SimpleNamespace(agent=agent), os.getpid(), {1: (0, 0, '1')}))

    async def test_parallel_dispatches_do_not_exchange_context(self):
        owner = await self.dispatch(self.owner(), self.verified())
        system = await self.dispatch(self.heartbeat(), {})
        r_owner, r_system = self.runner(owner), self.runner(system)
        self.h.agent.cur_send = self.owner()
        a, row_a = await self.codex_approval(r_system, self.write())
        b, row_b = await self.codex_approval(r_owner, self.write())
        self.assertEqual(('decline', 'off_list_write_host_read'), (a, row_a.get('reason')))
        self.assertEqual(('accept', 'off_list'), (b, row_b.get('reason')))


class Liveness(_Fixture):
    async def assert_dead(self, dispatch):
        self.assertEqual('unknown', approval_offlist.source(self.h, background=('busy_dispatch', dispatch))['type'])
        answer, row = await self.codex_approval(self.runner(dispatch), self.write())
        self.assertEqual('decline', answer)
        self.assertIn(row.get('reason'), ('native_context_changed', 'off_list_write_unknown', 'state_authority_changed', None))

    async def test_estop_then_resume_kills_the_binding(self):
        dispatch = await self.dispatch(self.owner(), self.verified())
        self.assertTrue(dispatch.live(self.h))
        await self.h.do_estop('terminal', '终端')
        await self.h.do_resume('terminal', '终端')
        self.assertFalse(self.h.stopped())
        self.assertFalse(dispatch.live(self.h))
        await self.assert_dead(dispatch)

    async def test_epoch_agent_task_epoch_and_end(self):
        for change in ('epoch', 'agent', 'task_epoch', 'folder', 'ended', 'task_done', 'unregistered', 'stopping'):
            with self.subTest(change=change):
                dispatch = await self.dispatch(self.owner(), self.verified())
                self.assertTrue(dispatch.live(self.h))
                undo = None
                if change == 'epoch':
                    old = self.h.hist.epoch; self.h.hist.epoch = 'other-epoch'; undo = lambda: setattr(self.h.hist, 'epoch', old)
                elif change == 'agent':
                    old = self.h.agent; self.h.agent = SimpleNamespace(**{**old.__dict__}); undo = lambda: setattr(self.h, 'agent', old)
                elif change == 'task_epoch':
                    self.h.__dict__['task_epoch'] = secrets.token_hex(8)
                elif change == 'folder':
                    old = self.h.agent_cfg; self.h.agent_cfg = {'dir': str(self.root)}; undo = lambda: setattr(self.h, 'agent_cfg', old)
                elif change == 'ended':
                    dispatch.ended = True
                elif change == 'task_done':
                    self.holds[-1].set(); await asyncio.sleep(0); await asyncio.gather(dispatch.task, return_exceptions=True)
                elif change == 'unregistered':
                    self.h._busy_native[dispatch.key]['dispatch'] = None
                elif change == 'stopping':
                    self.h.stopping.set(); undo = self.h.stopping.clear
                self.assertFalse(dispatch.live(self.h))
                self.assertEqual('unknown', approval_offlist.source(self.h, background=('busy_dispatch', dispatch))['type'])
                if change not in ('agent', 'stopping'):
                    await self.assert_dead(dispatch)
                if undo:
                    undo()

    async def test_key_reuse_never_inherits(self):
        key = 'run:' + secrets.token_hex(6)
        dispatch = await self.dispatch(self.owner(), self.verified(), key=key)
        self.holds[-1].set(); await asyncio.gather(dispatch.task, return_exceptions=True)
        dispatch.ended = True
        with mock.patch.object(busy_native, 'policy', return_value=('task', _cfg(self.cwd), self.verified(), 'danger-full-access', '42')):
            # The same binding gets the old receipt (no new job, the dead binding stays dead) …
            await busy_native.start(self.h, {'key': key, 'argv': ['codex', 'exec', 'task'], 'cwd': str(self.cwd)},
                                    os.getpid(), os.getuid())
            self.assertIs(self.h._busy_native[key]['dispatch'], dispatch)
            self.assertFalse(dispatch.live(self.h))
            # … another caller / payload is refused outright.
            with self.assertRaisesRegex(ValueError, 'already bound'):
                await busy_native.start(self.h, {'key': key, 'argv': ['codex', 'exec', 'other'], 'cwd': str(self.cwd)},
                                        os.getpid(), os.getuid())

    async def test_forged_bindings_are_unknown(self):
        dispatch = await self.dispatch(self.owner(), self.verified())
        look_alike = type('Dispatch', (), {'live': lambda self, host: True, 'label': dispatch.label})()
        for background in (('busy_dispatch', look_alike), ('busy_dispatch', dict(dispatch.label)), ('busy_dispatch', None),
                           ('busy_dispatch', dispatch, 'x'), 'busy_dispatch'):
            with self.subTest(background=background):
                self.assertEqual('unknown', approval_offlist.source(self.h, background=background)['type'])

    async def test_stop_during_off_list_decision(self):
        dispatch = await self.dispatch(self.owner(), self.verified())
        real = approval_offlist.decide
        def decide(*a, **kw):
            dispatch.ended = True                     # the job is stopped while the off-loop checks run
            return real(*a, **kw)
        with mock.patch.object(approval_offlist, 'decide', decide):
            answer, row = await self.codex_approval(self.runner(dispatch), self.write())
        self.assertEqual('decline', answer)
        self.assertNotIn('off_list', [r for r in self.reasons()][-1:])


class BusyStop(_Fixture):
    """security r1 (Codex F3 / Claude F4): `agentj busy stop run:<key>` ends a native dispatch at the Host — the real
    control-socket entrypoint (kernel peer credentials from a socketpair), not a test setting `ended`."""
    async def ctl_stop(self, key):
        return await self.ctl_stop_req({'key': key})

    async def ctl_stop_req(self, extra):
        import socket
        a, b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        class Writer:
            def __init__(self):
                self.data = bytearray()
            def write(self, data):
                self.data.extend(data)
            async def drain(self):
                pass
            def close(self):
                pass
            def get_extra_info(self, name):
                return a if name == 'socket' else None
        reader, writer = asyncio.StreamReader(), Writer()
        reader.feed_data(json.dumps({'cmd': 'busy_native_stop', **extra}).encode() + b'\n')
        reader.feed_eof()
        await self.h.on_ctl(reader, writer)
        return json.loads(bytes(writer.data).splitlines()[0])

    async def test_stop_while_queued(self):
        dispatch = await self.dispatch(self.owner(), self.verified())
        answer = await self.ctl_stop(dispatch.key)
        self.assertEqual((True, True, True), (answer['ok'], answer['native'], answer['stopped']))
        await asyncio.gather(dispatch.task, return_exceptions=True)
        self.assertTrue(dispatch.task.cancelled())
        self.assertFalse(dispatch.live(self.h))
        answer, row = await self.codex_approval(self.runner(dispatch), self.write())
        self.assertEqual('decline', answer)
        self.assertNotIn(row.get('reason'), ('off_list', 'off_list_diag'))
        self.assertEqual({'ok': True, 'native': False}, await self.ctl_stop('run:' + 'f' * 12))
        self.assertFalse((await self.ctl_stop('cli:x'))['ok'])

    async def test_stop_during_the_off_list_decision(self):
        import threading
        dispatch = await self.dispatch(self.owner(), self.verified())
        entered, release = threading.Event(), threading.Event()
        real = approval_offlist.decide
        def decide(*a, **kw):
            entered.set(); release.wait(5)
            return real(*a, **kw)
        with mock.patch.object(approval_offlist, 'decide', decide):
            pending = asyncio.create_task(self.codex_approval(self.runner(dispatch), self.write()))
            await asyncio.to_thread(entered.wait, 5)
            await self.ctl_stop(dispatch.key)
            release.set()
            answer, row = await pending
        self.assertEqual('decline', answer)
        self.assertNotIn('off_list', self.reasons()[-1:])

    async def test_stop_during_a_card_wait(self):
        self.h.ask_ttl = 30
        dispatch = await self.dispatch(self.owner(), self.verified())
        task = self.guard(self.runner(dispatch), CHARGE)
        for _ in range(600):
            if self.h.asks or task.done():
                break
            await asyncio.sleep(.005)
        ask = next(iter(self.h.asks.values()))
        await self.ctl_stop(dispatch.key)
        out = await asyncio.wait_for(task, 3)          # answered at once (agent_gone), no phone answer needed
        self.assertEqual('deny', out['hookSpecificOutput']['permissionDecision'])
        self.assertTrue(ask.fut.done())
        self.assertIn('agent_gone', self.reasons())

    async def test_stop_caller_matrix(self):
        # Same uid may stop (as with `agentj stop`, the existing same-uid emergency-stop policy); another uid may not;
        # a stop only stops — extra fields (argv / cwd / owner flags / another key) are ignored.
        dispatch = await self.dispatch(self.owner(), self.verified())
        other = await self.dispatch(self.owner(), self.verified())
        refused = busy_native.stop(self.h, {'key': dispatch.key}, os.getpid(), os.getuid() + 1)
        self.assertEqual({'ok': False, 'error': 'invalid busy key'}, refused)
        self.assertTrue(dispatch.live(self.h))
        for bad in ({'key': None}, {'key': 'run:XYZ'}, {'key': dispatch.key + 'a'}, {}, {'key': ['run:' + 'a' * 12]}):
            with self.subTest(req=bad):
                self.assertFalse(busy_native.stop(self.h, bad, os.getpid(), os.getuid())['ok'])
        rows = set(self.h._busy_native)
        answer = await self.ctl_stop_req({'key': dispatch.key, 'argv': ['codex', 'exec', 'x'], 'cwd': '/', 'owner': True,
                                          'keys': [other.key], 'resume': True})
        self.assertEqual((True, True), (answer['ok'], answer['native']))
        self.assertFalse(dispatch.live(self.h))
        self.assertTrue(other.live(self.h))                  # only the named dispatch
        self.assertEqual(rows, set(self.h._busy_native))     # nothing started or re-bound
        self.assertFalse(self.h.stopped())                   # not an estop

    def test_cli_stops_at_the_host_before_removing_the_board_file(self):
        from agentj import busy, names
        order = []
        with mock.patch.object(names, 'ctl_call', side_effect=lambda st, req, timeout=None: order.append(req) or {'ok': True, 'native': True}), \
             mock.patch.object(busy, 'remove_item', side_effect=lambda root, key: order.append(('remove', key)) or False):
            rc = busy.cmd_busy(SimpleNamespace(busy_cmd='stop', key='run:0123456789ab', state_dir=str(self.st.root)))
        self.assertEqual(0, rc)
        self.assertEqual([{'cmd': 'busy_native_stop', 'key': 'run:0123456789ab'}, ('remove', 'run:0123456789ab')], order)
        with mock.patch.object(names, 'ctl_call') as call, mock.patch.object(busy, 'remove_item', return_value=True):
            self.assertEqual(0, busy.cmd_busy(SimpleNamespace(busy_cmd='stop', key='synthetic', state_dir=str(self.st.root))))
        call.assert_not_called()                          # cli: board items never reach the Host stop


class Spending(_Fixture):
    async def card(self, task):
        for _ in range(600):
            if self.h.asks or task.done():
                break
            await asyncio.sleep(.005)
        return next(iter(self.h.asks.values()), None)

    async def test_owner_dispatch_charge_and_irreversible_take_the_face_id_card(self):
        self.assertIn('busy_dispatch', uv_list.FINGERPRINT_SOURCES)
        dispatch = await self.dispatch(self.owner(), self.verified())
        self.h.agent.cur_send = self.heartbeat()      # a heartbeat main turn no longer launders it card-free
        for command in (CHARGE, FORCE):
            self.assertTrue(uv_list.fingerprint_command('Bash', {'command': command}, cwd=str(self.cwd),
                                                        env=uv_list.host_environment(self.h), host=self.h))
            for route in ('guard', 'codex'):
                with self.subTest(command=command, route=route):
                    r = self.runner(dispatch)
                    if route == 'guard':
                        kind, uv = await self.outcome(self.guard(r, command))
                    else:
                        kind, uv = await self.outcome(asyncio.create_task(self.codex_approval(r, command)))
                    self.assertEqual(('card', True), (kind, uv))
        self.assertNotIn('fingerprint_headless', self.reasons())

    async def test_card_answer_after_the_dispatch_died_is_refused(self):
        self.h.ask_ttl = 30                           # the fixture's 2 s expires a passkey card under load
        dispatch = await self.dispatch(self.owner(), self.verified())
        for kill in (False, True):
            with self.subTest(killed=kill):
                task = self.guard(self.runner(dispatch), CHARGE)
                ask = await self.card(task)
                self.assertIsNotNone(ask)
                self.assertTrue(ask.require_passkey)
                if kill:
                    dispatch.ended = True             # stop while the card waits
                await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask, self.s), True))
                out = await asyncio.wait_for(task, 3)
                self.h.asks.clear()
                self.assertEqual({} if not kill else 'deny',
                                 out if not kill else out['hookSpecificOutput']['permissionDecision'])


if __name__ == '__main__':
    unittest.main()
