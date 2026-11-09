"""#0302 (0.30.2): macOS peer credentials + owner requests are not blocked outside the UV list.

macOS Python has no SO_PEERCRED, so every gate comparing the unix-socket peer uid with ours saw None and refused the
owner's own calls (unverified_plan_producer, application_ingress_unverified, privacy broker/source). These tests run on
Linux with the darwin branches mocked; the real API was checked read-only on a Mac (REPORT-0302).
"""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import os
import socket
import struct
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from agentj import application_ingress as AI, main_thread as M, privacy, privacy_host, shared_hook
from agentj.core import process_evidence as PE


def xucred(uid, version=0):
    return struct.pack('II', version, uid) + b'\0' * 68


class FakeDarwinSock:
    """LOCAL_PEERCRED = (0, 1), LOCAL_PEERPID = (0, 2); no SO_PEERCRED."""
    def __init__(self, uid, pid=4242, version=0, broken=False):
        self.uid, self.pid, self.version, self.broken = uid, pid, version, broken

    def getsockopt(self, level, opt, size):
        if self.broken:
            raise OSError('unsupported')
        if (level, opt) == (0, 1):
            return xucred(self.uid, self.version)
        if (level, opt) == (0, 2):
            return struct.pack('i', self.pid)
        raise OSError('unexpected option')


class PeerUid(unittest.TestCase):
    def test_darwin_local_peercred_gives_kernel_uid(self):
        with mock.patch.object(PE.sys, 'platform', 'darwin'):
            self.assertEqual(PE.peer_uid(FakeDarwinSock(os.getuid())), os.getuid())
            self.assertEqual(PE.peer_uid(FakeDarwinSock(os.getuid() + 1)), os.getuid() + 1)

    def test_darwin_unknown_stays_none_never_ours(self):
        with mock.patch.object(PE.sys, 'platform', 'darwin'):
            self.assertIsNone(PE.peer_uid(FakeDarwinSock(os.getuid(), version=7)))   # unknown xucred layout
            self.assertIsNone(PE.peer_uid(FakeDarwinSock(os.getuid(), broken=True)))
            self.assertIsNone(PE.peer_uid(None))

    def test_linux_is_unchanged_so_peercred(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with a, b:
            self.assertEqual(PE.peer_uid(a), os.getuid())
            self.assertEqual(shared_hook.peer_pid(a), (os.getpid(), os.getuid()))

    def test_shared_hook_peer_pid_darwin_returns_uid_not_none(self):
        no_peercred = NS(**{k: getattr(socket, k) for k in ('SOL_SOCKET',)})   # macOS: no SO_PEERCRED attribute
        with mock.patch.object(shared_hook, 'socket', no_peercred), mock.patch.object(PE.sys, 'platform', 'darwin'):
            self.assertEqual(shared_hook.peer_pid(FakeDarwinSock(os.getuid(), pid=777)), (777, os.getuid()))
            self.assertEqual(shared_hook.peer_pid(FakeDarwinSock(os.getuid() + 1, pid=777)), (777, os.getuid() + 1))


def host_with(agent):
    st = object()
    return NS(agent=agent, agent_cfg={}, st=st, kp=NS(pub='k'), channel='c', hist=NS(epoch=1),
              stopped=lambda: False, stopping=asyncio.Event(), _agent_pids=lambda: set())


class Writer:
    def is_closing(self):
        return False


class OwnerLocalIngress(unittest.IsolatedAsyncioTestCase):
    """owner_local: same-uid live caller under the live session process, no hop born while a non-owner turn was open,
    during an owner turn. Timelines follow security review r2: guest turn 100-180, owner turn from 200."""
    ANCHOR, MID, PEER = 500, 600, 700
    GUEST = [[100.0, 180.0]]

    def rows(self, *, peer_born=200.0, mid_born=190.0, peer_parent=None):
        return {self.ANCHOR: (1, 10.0, 'S'), self.MID: (self.ANCHOR, mid_born, 'M'),
                self.PEER: (peer_parent or self.MID, peer_born, 'P'), os.getpid(): (1, 5.0, 'serve')}

    def patches(self, rows, *, untrusted=False, kind='phone', sandboxed=False, process='tok', platform='darwin'):
        return [mock.patch.object(AI, '_process', lambda pid: process),
                mock.patch.object(AI, '_sandboxed', lambda agent: sandboxed),
                mock.patch.object(AI.sys, 'platform', platform),
                mock.patch.object(PE, 'darwin_row', lambda pid: rows.get(pid)),
                mock.patch.object(PE, 'linux_row', lambda pid: rows.get(pid)),
                mock.patch.object(AI.provenance, 'untrusted_turn', lambda agent: untrusted),
                mock.patch.object(AI.provenance, 'turn_source', lambda agent: {'kind': kind, 'role': 'owner' if kind in ('phone', 'terminal', 'system') else 'task'}),
                mock.patch('agentj.approval_material._direct_scope', lambda host, pid: None),
                mock.patch.object(AI.ctl_auth, 'refusal', lambda *a: 'agent'),
                mock.patch.object(AI.ctl_auth, 'has_tty', lambda pid: False)]

    async def frame_allowed(self, uid=None, *, rows=None, spans=None, floor=0.0, send=None, foreign=False, **kw):
        uid = os.getuid() if uid is None else None if uid == 'unknown' else uid
        host = host_with(NS(session={'pid': self.ANCHOR, 'procStart': 'S'}, cur_send=send, untrusted_foreign=foreign))
        host._nonowner_spans = [list(x) for x in (self.GUEST if spans is None else spans)]
        if floor is not None:
            host._nonowner_floor = floor
        with contextlib.ExitStack() as stack:
            for p in self.patches(rows or self.rows(), **kw):
                stack.enter_context(p)
            with AI.native(host, Writer(), b'{}', self.PEER, uid):
                return AI.allowed(host), AI.actor(host)

    async def test_owner_job_born_after_the_guest_turn_ended_is_allowed(self):
        self.assertEqual(await self.frame_allowed(), (True, 'owner_local'))
        self.assertTrue((await self.frame_allowed(untrusted=True, kind='same_owner'))[0])
        self.assertTrue((await self.frame_allowed(untrusted=True, kind='system', send=NS(hook_notice=None)))[0])

    async def test_f1_process_born_during_a_guest_turn_is_refused_in_a_later_owner_turn(self):
        self.assertFalse((await self.frame_allowed(rows=self.rows(mid_born=150.0)))[0])     # r2 B1 timeline
        self.assertFalse((await self.frame_allowed(rows=self.rows(peer_born=180.02)))[0])   # margin toward refusal
        self.assertFalse((await self.frame_allowed(spans=[[100.0, None]]))[0])               # guest turn still open
        self.assertFalse((await self.frame_allowed(floor=None, rows=self.rows(mid_born=3.0)))[0])  # before serve

    async def test_f2_not_under_the_session_process_is_refused(self):
        rows = self.rows(); rows[self.PEER] = (os.getpid(), 200.0, 'P')     # a serve child (avatar / bot)
        self.assertFalse((await self.frame_allowed(rows=rows))[0])
        rows = self.rows(); rows[self.ANCHOR] = (1, 10.0, 'reused')          # stale / reused session pid
        self.assertFalse((await self.frame_allowed(rows=rows))[0])

    async def test_f3_webhook_foreign_and_guest_turns_are_refused(self):
        self.assertFalse((await self.frame_allowed(untrusted=True, kind='system', send=NS(hook_notice='h1')))[0])
        self.assertFalse((await self.frame_allowed(foreign=True))[0])                       # r2 N2
        self.assertFalse((await self.frame_allowed(untrusted=True, kind='friend_task'))[0])
        self.assertFalse((await self.frame_allowed(untrusted=True, kind='group'))[0])
        self.assertFalse((await self.frame_allowed(sandboxed=True))[0])

    async def test_linux_same_rule_and_unknown_uid_still_refused(self):
        self.assertEqual(await self.frame_allowed(platform='linux'), (True, 'owner_local'))
        self.assertFalse((await self.frame_allowed(platform='linux', rows=self.rows(mid_born=150.0)))[0])
        self.assertFalse((await self.frame_allowed(platform='win32'))[0])
        self.assertFalse((await self.frame_allowed(uid=os.getuid() + 1))[0])      # another user
        self.assertFalse((await self.frame_allowed(uid='unknown'))[0])            # LOCAL_PEERCRED unreadable: fail closed
        self.assertFalse((await self.frame_allowed(process=None))[0])

    def test_spans_open_on_non_owner_turns_and_close_at_turn_end(self):
        from agentj import provenance
        clock = [100.0]
        with mock.patch.object(PE, 'now', lambda: clock[0]):
            host = NS()
            AI.note_turn(host, NS(source=provenance.phone()))
            AI.note_turn(host, NS(source=provenance.system(), hook_notice=None))
            self.assertFalse(host.__dict__.get('_nonowner_spans'))
            AI.note_turn(host, NS(source={'kind': 'friend_task', 'role': 'task'}))
            clock[0] = 180.0; AI.close_span(host)
            AI.note_turn(host, NS(source=provenance.system(), hook_notice='h'))
            self.assertEqual(host._nonowner_spans, [[100.0, 180.0], [180.0, None]])
            host._nonowner_floor = 0.0
            self.assertTrue(AI._guest_born(host, 150.0)); self.assertTrue(AI._guest_born(host, 999.0))
            clock[0] = 190.0; AI.close_span(host)
            self.assertFalse(AI._guest_born(host, 195.0))

    async def test_ordinary_refusal_is_not_used_for_uv_list(self):
        host = host_with(NS(session={'pid': 1}))
        with mock.patch('agentj.uv_list.required', lambda *a, **k: True), \
                mock.patch('agentj.uv_list.host_environment', lambda h: {}):
            from agentj import policy_registry
            tool = next(t for t in policy_registry.HOST_TOOLS if t not in ('AskUserQuestion', 'SandboxOnce', 'AuthRescue', 'AgentJBoundedPlan'))
            self.assertEqual(AI.ordinary(host, tool, {})['reason'], 'not_ordinary_application')


class PrivacyBroker(unittest.TestCase):
    def test_owner_local_origin_fallback(self):
        host = host_with(NS())
        with mock.patch('agentj.approval_material.verified_caller_origin', lambda h, p: None), \
                mock.patch.object(AI, '_process', lambda pid: 'tok'), \
                mock.patch.object(AI, '_owner_tree', lambda h, p, agent: True):
            origin = privacy_host._origin(host, 4242)
        self.assertTrue(origin['verified'] and origin['owner_local'])

    def test_guest_or_other_uid_gets_no_origin(self):
        host = host_with(NS())
        with mock.patch('agentj.approval_material.verified_caller_origin', lambda h, p: None):
            with mock.patch.object(AI, '_process', lambda pid: 'tok'), mock.patch.object(AI, '_owner_tree', lambda h, p, a: False):
                self.assertIsNone(privacy_host._origin(host, 4242))
            with mock.patch.object(AI, '_process', lambda pid: None), mock.patch.object(AI, '_owner_tree', lambda h, p, a: True):
                self.assertIsNone(privacy_host._origin(host, 4242))
        self.assertIsNone(privacy_host._origin(host, None))

    def test_f1_guest_born_child_gets_no_privacy_origin(self):
        rows = {500: (1, 10.0, 'S'), 600: (500, 150.0, 'M'), 700: (600, 200.0, 'P')}
        host = host_with(NS(session={'pid': 500, 'procStart': 'S'}, cur_send=None, untrusted_foreign=False))
        host._nonowner_spans, host._nonowner_floor = [[100.0, 180.0]], 0.0
        with mock.patch('agentj.approval_material.verified_caller_origin', lambda h, p: None), \
                mock.patch.object(AI, '_process', lambda pid: 'tok'), mock.patch.object(AI.sys, 'platform', 'darwin'), \
                mock.patch.object(PE, 'darwin_row', lambda pid: rows.get(pid)), \
                mock.patch.object(AI, '_sandboxed', lambda a: False), \
                mock.patch.object(AI.provenance, 'untrusted_turn', lambda a: False):
            self.assertIsNone(privacy_host._origin(host, 700))
            rows[600] = (500, 190.0, 'M')
            self.assertTrue(privacy_host._origin(host, 700)['owner_local'])

    def test_strict_origin_wins_when_available(self):
        host = host_with(NS())
        with mock.patch('agentj.approval_material.verified_caller_origin', lambda h, p: {'verified': True, 'turn': 7}):
            self.assertEqual(privacy_host._origin(host, 4242), {'verified': True, 'turn': 7})

    def test_source_refusal_is_actionable(self):
        hint = privacy.source_hint(privacy.Verdict('unavailable', reason='privacy-check/peer_unverified'))
        self.assertIn('peer_unverified', hint)
        self.assertIn('agentj doctor', hint)


class MainThreadAdvisory(unittest.TestCase):
    def setUp(self):
        self.host = NS(preferences={}, lang='en', cur_turn=7, status_changed=mock.Mock(), busy=NS(items=lambda: []), emit=mock.Mock())
        self.adapter = NS(host=self.host, cfg={'main_thread_work_calls': 2}, kind='codex', status='working')
        self.guard = M.Guard(self.adapter, clock=lambda: 0.0)

    def test_never_denies_only_reminds(self):
        outs = [self.guard.check('Bash', {'command': 'agentj plaza install video-teardown'}) for _ in range(12)]
        for out in outs:
            self.assertNotIn('permissionDecision', (out or {}).get('hookSpecificOutput', {}))
        reminders = [o for o in outs if o]
        self.assertGreaterEqual(len(reminders), 2)
        self.assertFalse(self.guard.snapshot()['blocked'])
        self.host.emit.assert_called_with('main_thread_guard', result='remind', calls=mock.ANY)

    def test_friends_reply_is_not_work(self):
        for cmd in ('agentj friends tell M1 "done"', 'agentj friends tell --reply-to abc M1 ok', 'agentj friends list',
                    'agentj friends history M1', 'agentj friends reports', 'agentj friends asks'):
            self.assertFalse(M.work_call('Bash', {'command': cmd}), cmd)
        self.assertTrue(M.work_call('Bash', {'command': 'agentj friends tell M1 ok > out.txt'}))
        self.assertTrue(M.work_call('Bash', {'command': 'agentj friends tell M1 ok; npm run build'}))


class SameOwnerApproveActions(unittest.IsolatedAsyncioTestCase):
    async def test_same_owner_gets_run_directly_not_task_plan(self):
        from agentj import peer_service, provenance, bounded_plan
        svc = peer_service.PeerService.__new__(peer_service.PeerService)
        svc.host = NS(cur_turn=3, task_label=None, agent=NS(), lang='zh')
        with mock.patch.object(provenance, 'turn_source', lambda a: {'kind': 'same_owner', 'role': 'peer'}), \
                mock.patch.object(bounded_plan, 'continuation_allowed', lambda h, x: False):
            res = await svc.approve_actions([{'tool': 'Bash', 'input': {'command': 'cp a b'}}])
        self.assertEqual(res, {'ok': False, 'why': 'same_owner_run_directly'})
        self.assertIn('指纹', peer_service.WHY_ZH['same_owner_run_directly'])
        for why in ('unverified_plan_producer', 'task_plan'):
            self.assertIn(why, peer_service.WHY_ZH)


if __name__ == '__main__':
    unittest.main()


class SelfCompactAfterTurn(unittest.TestCase):
    """TB 2026-11-09 14:39: phone-menu /compact queued, then refused at run ('无法确认这轮来自主人本人')."""
    def test_owner_terminal_native_source_is_owner(self):
        from agentj import self_compact, provenance
        self.assertTrue(self_compact.owner_channel(provenance.terminal()))
        self.assertTrue(self_compact.owner_channel(provenance.phone()))
        for guest in ({'kind': 'friend_task', 'role': 'task'}, {'kind': 'telegram', 'role': 'group', 'sender': 1, 'chat': -1},
                      {'kind': 'email', 'role': 'group', 'sender': 'guest@example.test'}, None, {}):
            self.assertFalse(self_compact.owner_channel(guest), guest)

    def test_queued_owner_compaction_runs_after_shared_turn_end(self):
        from agentj import self_compact, provenance
        host = NS(agent=None, stopped=lambda: False, st=NS(log=lambda *a, **k: None), lang='zh')
        agent = NS(kind='claude', host=host, cur_send=None, pending_source=None, native_source=provenance.terminal())
        host.agent = agent
        cmd = NS(data={'self_compact': 'm', 'self_compact_source': provenance.phone()})
        with mock.patch.object(self_compact, 'marker', lambda a: 'm'), mock.patch.object(self_compact, 'fresh', lambda a: True):
            self.assertIsNone(self_compact.guard(agent, cmd))
            agent.native_source = {'kind': 'friend_task', 'role': 'task'}
            self.assertIsNotNone(self_compact.guard(agent, cmd))


class SharedSessionSpans(unittest.TestCase):
    """security r3 R3-1: shared-session native turns whose source is not the owner's open a span; Stop closes it."""
    def test_unknown_peer_and_synthetic_native_turns_open_spans(self):
        from agentj import shared, provenance
        clock = [100.0]
        with mock.patch.object(PE, 'now', lambda: clock[0]):
            for src in (provenance.normalize(None), {'kind': 'same_host', 'role': 'peer'}, {'kind': 'friend_task', 'role': 'task'}):
                host = NS()
                shared.application_ingress_span(NS(host=host, native_source=src))
                self.assertEqual(host._nonowner_spans, [[100.0, None]], src)
                clock[0] = 150.0; AI.close_span(host); clock[0] = 100.0
                host._nonowner_floor = 0.0
                self.assertTrue(AI._guest_born(host, 120.0))      # born in that native turn: never owner_local later
                self.assertFalse(AI._guest_born(host, 160.0))     # born in a later owner turn: fine
            for owner in (provenance.terminal(), provenance.phone(), {'kind': 'same_owner', 'role': 'task'}):
                host = NS()
                shared.application_ingress_span(NS(host=host, native_source=owner))
                self.assertFalse(host.__dict__.get('_nonowner_spans'), owner)

    def test_every_turn_source_assignment_in_shared_opens_a_span(self):
        # security r4 C1: each place a native turn's source is decided must be followed by the span helper.
        import inspect, re as _re
        from agentj import shared
        lines = inspect.getsource(shared).splitlines()
        decided = [i for i, l in enumerate(lines) if _re.search(r'self\.native_source = (self\.out_src = )?provenance\.(terminal_input|normalize\(None\))', l)
                   or _re.search(r'self\.native_source = src$', l.strip())]
        resets = [i for i in decided if 'SessionStart' in lines[i - 1] or 'native_source = provenance.normalize(None)' in lines[i] and lines[i].startswith('        self.native_source')]
        for i in decided:
            if i in resets:
                continue
            self.assertIn('application_ingress_span(self)', lines[i + 1], f'shared.py line {i + 1}: {lines[i].strip()}')

    def test_peer_origin_or_envelope_prompt_is_not_owner(self):
        from agentj import provenance, shared
        host = NS()
        with mock.patch.object(PE, 'now', lambda: 50.0):
            src = provenance.terminal_input({'origin': {'kind': 'peer'}}, 'hi', 'hi')
            shared.application_ingress_span(NS(host=host, native_source=src))
        self.assertEqual(host._nonowner_spans, [[50.0, None]])

    def test_native_stop_keeps_span_open_while_a_non_owner_send_is_pending(self):
        from agentj import provenance
        with mock.patch.object(PE, 'now', lambda: 60.0):
            host = NS(_nonowner_spans=[[50.0, None]])
            AI.close_native_span(NS(host=host, cur_send=NS(source=provenance.system(), hook_notice='h')))
            self.assertIsNone(host._nonowner_spans[-1][1])                     # C2: webhook send still pending
            AI.close_native_span(NS(host=host, cur_send=NS(source=provenance.phone())))
            self.assertEqual(host._nonowner_spans[-1][1], 60.0)

    def test_native_span_starts_at_previous_close(self):
        with mock.patch.object(PE, 'now', lambda: 90.0):
            host = NS(_span_closed_at=70.0)
            AI.open_span(host, native=True)
        self.assertEqual(host._nonowner_spans, [[70.0, None]])               # r4 N5: noticed late, back-dated


class OwnerChannelMerge(unittest.TestCase):
    """security r3 #1/#7/#8 at the R1 merge: one predicate; owner channels open no span; forged / guest mail does."""
    def test_one_predicate_for_spans(self):
        from agentj import provenance, shared
        owner_tg = {'kind': 'telegram', 'role': 'owner', 'sender': 123, 'chat': 123}
        for src in (owner_tg, {'kind': 'wechat', 'role': 'owner'}, {'kind': 'email', 'role': 'owner', 'sender': 'owner@example.com'}):
            self.assertEqual(AI._owner_source(src), provenance.owner_intent(src), src)
            host = NS(); shared.application_ingress_span(NS(host=host, native_source=src))
            AI.note_turn(host, NS(source=src, hook_notice=None))
            self.assertFalse(host.__dict__.get('_nonowner_spans'), src)
        for src in ({'kind': 'telegram', 'role': 'forward', 'sender': 1, 'chat': 1}, {'kind': 'telegram', 'role': 'group', 'sender': 1, 'chat': -5},
                    {'kind': 'email', 'role': 'guest', 'sender': 'forged@example.com'}, {'kind': 'email', 'role': 'group', 'sender': 'x@example.com'}):
            host = NS(); AI.note_turn(host, NS(source=src, hook_notice=None))
            self.assertTrue(host.__dict__.get('_nonowner_spans'), src)
        host = NS(); AI.note_turn(host, NS(source=owner_tg, hook_notice='h'))       # webhook body is never the owner
        self.assertTrue(host.__dict__.get('_nonowner_spans'))
