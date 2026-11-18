"""R-0047 + R-0052 (0.30.31): caller origin of an owner turn and its delegation chain, for Codex shared and Claude shared.

Owner positives (feedback through the real privacy_host broker; a delegated sub-agent's nested process inherits the turn)
and the negatives that must stay refused: a process born in a non-owner turn the owner was queued / merged behind, a
kept anchor whose guest turn left in-process work running (Codex native sub-agent thread, Claude background task —
security r0 F2), forged / unverified sources, a stamp for another Send, a macOS clock step (r0 F4).

Real processes, real /proc and CLOCK_BOOTTIME (Linux). The macOS twins back darwin_row with /proc (wall-clock start) and
patch sys.platform, as test_r0047_darwin_owned_feedback does. The Integration class drives the real serve.Host
agent_turn_start through the real Agent queue loop (agent.py) and the real SharedClaudeAgent._merge_queued (r0 F3).
No network: the Jev call is the fake endpoint. Throw-away state; HOME is never written.
"""
import _hermetic  # noqa: F401
import asyncio
import contextlib
import os
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from agentj import application_ingress, approval_material, busy, busy_launch, compose, privacy, privacy_host, provenance
from agentj.core import order_host, process_evidence
from agentj.shared import SharedClaudeAgent
from agentj.shared_state import AgentState


class _NoTimers:
    """AgentState scheduler without real threads: a test fires the stale watchdog explicitly (state._on_stale())."""
    def now(self):
        return time.time()

    def call_later(self, delay, fn):
        return NS(cancel=lambda: None)
from agentj.shared_codex import SharedCodexAgent

GAP = 0.25
LINUX = sys.platform.startswith('linux') and hasattr(time, 'CLOCK_BOOTTIME')
TEST_TMP = os.environ.get('TEST_TMP') or tempfile.gettempdir()

# Stands in for the long-lived harness process (shared Claude session / kept Codex app-server). "spawn" starts a shell hop
# with a job under it (a tool call / background shell); "nest" two shell hops (a delegated sub-agent's own command).
ANCHOR = r'''
import subprocess, sys
for line in sys.stdin:
    cmd = line.strip()
    if cmd == "spawn":
        p = subprocess.Popen(["sh", "-c", "sleep 120 & echo $!; wait"], stdout=subprocess.PIPE, text=True)
    elif cmd == "nest":
        p = subprocess.Popen(["sh", "-c", "sh -c 'sleep 120 & echo $!; wait' & wait"], stdout=subprocess.PIPE, text=True)
    else:
        continue
    print(p.pid, p.stdout.readline().strip(), flush=True)
'''


def _ticks(pid):
    return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]


_TICK = os.sysconf('SC_CLK_TCK')
_BOOT = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME) if LINUX else 0.0


def proc_row(pid):
    """/proc rendered the way darwin_row returns it: (parent, wall-clock start seconds, start token)."""
    try:
        with open(f'/proc/{pid}/stat', 'rb') as fh:
            s = fh.read().decode('utf-8', 'replace').rsplit(')', 1)[1].split()
        if os.stat(f'/proc/{pid}').st_uid != os.getuid() or s[0] in ('Z', 'X'):
            return None
        return int(s[1]), _BOOT + int(s[19]) / _TICK, 'start-' + s[19]
    except (OSError, ValueError, IndexError):
        return None


class _Anchor:
    """Real anchor process + the jobs it starts; reaped on cleanup."""

    def __init__(self, case):
        self.p = subprocess.Popen([sys.executable, '-I', '-c', ANCHOR], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True)
        self.kids = []
        case.addCleanup(self.reap)

    @property
    def pid(self):
        return self.p.pid

    def run(self, what='spawn'):
        self.p.stdin.write(what + '\n')
        self.p.stdin.flush()
        shell, job = (int(x) for x in self.p.stdout.readline().split())
        self.kids += [job, shell]
        deadline = time.time() + 5
        while not process_evidence.row(job) and time.time() < deadline:     # /proc on Linux, libproc on macOS
            time.sleep(.01)
        return job

    def reap(self):
        for pid in self.kids:
            with contextlib.suppress(OSError):
                os.kill(pid, 9)
        self.p.kill()
        self.p.wait()
        for f in (self.p.stdin, self.p.stdout):
            f.close()


class _Matrix:
    """Shared helpers: a host stub that carries exactly what serve.Host holds for these proofs."""
    epoch = 3

    def make_host(self):
        return NS(hist=NS(epoch=self.epoch), desktop_turn=None, busy=busy.Board(None), st=NS(log=mock.Mock()),
                  stopped=lambda: False, agent_notice=mock.Mock(), agent=None, _agent_pids=lambda: set(),
                  kp=NS(pub=b'r0331-host-key'), channel='r0331-channel', stopping=asyncio.Event())

    def origin(self, mid, owner=True, epoch=None):
        return {'session': str(self.epoch if epoch is None else epoch), 'message_id': mid, 'root_message_id': mid,
                'chain': [], 'channel': 'phone' if owner else 'friend_task', 'verified': owner, 'suspicious': not owner,
                'text': 'r0331 synthetic', 'task_record': f'message:{self.epoch}:{mid}'}

    def send(self, mid, source, **origin):
        s = compose.Send('r0331-device', secrets.token_urlsafe(8), mid, text='r0331 synthetic', by=None, source=source)
        s.approval_origin = self.origin(mid, owner=provenance.is_owner(source), **origin)
        return s

    def codex(self, host, anchor):
        agent = object.__new__(SharedCodexAgent)          # the real class: no session dict, Host-owned app-server
        agent.kind, agent.cfg, agent.session, agent.research = 'codex', {'session_mode': 'shared'}, None, False
        agent.proc, agent.phone_turn, agent.cur_send, agent.host = anchor.p, False, None, host
        agent._busy('thread/status', {})                  # the real busy board pins the app-server (pid + start)
        self.assertEqual(host.busy.context['shared-codex']['pid'], anchor.pid)
        host.agent = agent
        return agent

    def claude(self, host, anchor, token=None, reconciled=True):
        agent = object.__new__(SharedClaudeAgent)          # the real class: the shared session pid is the anchor
        agent.kind, agent.cfg = 'claude', {'session_mode': 'shared'}
        agent.session = {'sessionId': 'r0331-session', 'pid': anchor.pid, 'procStart': token or _ticks(anchor.pid)}
        agent.phone_turn = agent.phone_seen = False
        agent.cur_send, agent.proc, agent.host, agent.sub_pending = None, None, host, []
        agent.bg_live, agent.bg_parent, agent.bg_known, agent.bg_nested, agent.bg_clean = {}, {}, None, {}, None
        agent.main_seen_at = time.monotonic()
        agent.untrusted_state, agent.untrusted_foreign, agent.untrusted_at, agent.peer_low = None, False, 0.0, False
        agent.state = AgentState(scheduler=_NoTimers())       # the real foreground / deferred-Stop state machine
        if hasattr(agent, '_bg_unresolved'):                     # wired as SharedClaudeAgent.__init__ wires it
            agent.state._on_drop = agent._bg_unresolved
        agent.sub_agents, agent.tool_agent = {}, {}
        agent.pending_source = agent.native_source = provenance.normalize(None)
        host.agent = agent
        if reconciled:
            self.main_stop(agent)
        return agent

    # -- the real in-process counters (busy.codex_event via CodexAgent._busy, SharedClaudeAgent._note_background) -------
    def hook(self, agent, event):
        """One served hook event in SharedClaudeAgent's own order: _note_agent → _note_background → state.handle."""
        event = {'session_id': 'r0331-session', **event}
        name = event['hook_event_name']
        agent._note_agent(name, event)
        agent._note_background(name, event)
        agent.state.handle(event)

    def main_stop(self, agent, tasks=()):
        self.hook(agent, {'hook_event_name': 'Stop',
                          'background_tasks': [{'id': t, 'type': 'subagent', 'status': 'running'} for t in tasks]})

    def launch(self, agent, task='guest-task', by=None):
        """A background sub-agent the current turn starts inside the harness (by=<agent id>: launched by a sub-agent)."""
        if isinstance(agent, SharedCodexAgent):
            agent._busy('thread/started', {'thread': {'id': task, 'source': {'subAgent': {'thread_spawn': {
                'parent_thread_id': 'main', 'depth': 1, 'agent_role': 'worker'}}}}})
            agent._busy('turn/started', {'threadId': task, 'turn': {'id': task + '-turn'}})
        else:
            event = {'hook_event_name': 'PostToolUse', 'tool_name': 'Agent',
                     'tool_input': {}, 'tool_response': {'agentId': task, 'isAsync': True}}
            if by:
                event['agent_id'] = by
            self.hook(agent, event)

    def pending_agent_call(self, agent):
        """A foreground Agent call whose SubagentStart has not arrived yet (real SharedClaudeAgent._note_agent)."""
        self.hook(agent, {'hook_event_name': 'PreToolUse', 'tool_name': 'Agent',
                          'tool_input': {'subagent_type': 'worker', 'description': 'x'}})
        self.assertTrue(agent.sub_pending)

    def finish(self, agent, task='guest-task'):
        if isinstance(agent, SharedCodexAgent):
            agent._busy('turn/completed', {'threadId': task, 'turn': {'id': task + '-turn', 'status': 'completed'}})
        else:
            self.hook(agent, {'hook_event_name': 'SubagentStop', 'agent_id': task})

    def ingress_kind(self, host, peer):
        """The real native-frame admission (application_ingress.native) for this caller: which actor it would be."""
        writer = NS(is_closing=lambda: False)
        with application_ingress.native(host, writer, {'t': 'r0331'}, peer, os.getuid()):
            return application_ingress.actor(host)

    def begin(self, host, agent, send):
        """agent.py queue loop: cur_send = send, then serve.Host.agent_turn_start (note_turn spans + the stamp)."""
        agent.cur_send = send
        agent.phone_turn = agent.phone_seen = True
        application_ingress.note_turn(host, send)
        approval_material.turn_start(host, send)

    def end(self, host, agent):
        agent.cur_send = None
        agent.phone_turn = agent.phone_seen = False
        application_ingress.close_span(host)

    async def feedback(self, host, peer):
        req = {'t': 'privacy_check', 'gate': 'feedback', 'draft': {'problem': 'the owner asked to send this'}}
        endpoint = mock.Mock(return_value=privacy.Verdict('ok', probability=.01))
        with mock.patch.object(privacy, 'layer2', endpoint):
            result = await privacy_host.handle(host, req, peer_pid=peer)
        return result, endpoint

    def owner_root(self, host, peer):
        caller = approval_material.verified_caller_origin(host, peer)
        return caller and caller.get('root_message_id')


@unittest.skipUnless(LINUX, 'Linux /proc branch')
class Linux(_Matrix, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.anchor = _Anchor(self)
        time.sleep(GAP)
        self.host = self.make_host()
        self.host._nonowner_floor = time.clock_gettime(time.CLOCK_BOOTTIME) - 60   # serve started before the anchor

    def agents(self):
        return (('codex', self.codex), ('claude', self.claude))

    async def assert_owner(self, job, root=7):
        self.assertEqual(self.owner_root(self.host, job), root)
        self.assertTrue(order_host.turn_bound(self.host, job))
        result, endpoint = await self.feedback(self.host, job)
        self.assertEqual(result['status'], 'ok', result)
        endpoint.assert_called_once()

    async def assert_refused(self, job, why='peer_unverified', bound=False):
        self.assertIsNone(self.owner_root(self.host, job))
        if bound is not None:
            self.assertFalse(order_host.turn_bound(self.host, job))
        result, endpoint = await self.feedback(self.host, job)
        self.assertEqual(result.get('reason'), 'privacy-check/' + why, result)
        endpoint.assert_not_called()

    # -- positives ------------------------------------------------------------------------------------------------------
    async def test_owner_turn_can_send_feedback(self):
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                await self.assert_owner(self.anchor.run())
                self.end(self.host, agent)

    async def test_secret_input_before_first_busy_event_holds_exact_phone_root(self):
        agent = self.codex(self.host, self.anchor)
        send = self.send(9, provenance.phone())
        self.host._approval_origins = {9: send.approval_origin.copy()}
        self.begin(self.host, agent, send)
        self.host.busy.context.clear()
        time.sleep(GAP)
        caller = self.anchor.run()
        self.assertIsNone(approval_material.verified_caller_origin(self.host, caller))
        owner, origin = approval_material.verified_phone_chat(self.host, caller)
        self.assertIs(owner, agent)
        self.assertEqual(origin, send.approval_origin)
        self.host._approval_origins.clear()
        self.assertIsNone(approval_material.verified_phone_chat(self.host, caller))

    async def test_delegated_subagent_command_in_owner_turn_inherits(self):
        """The owner turn hands work to a sub-agent whose own command is two more hops down: same root, same turn."""
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                job = self.anchor.run('nest')
                hops, current, table = 0, job, busy_launch.procs()
                while current != self.anchor.pid:
                    current, hops = table[current][0], hops + 1
                self.assertGreaterEqual(hops, 3)
                await self.assert_owner(job)
                self.end(self.host, agent)

    async def test_leftover_ends_then_owner_passes(self):
        """F2 green side: once the guest turn's in-process work ended (or /stop ended it), the owner's new command passes."""
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
                self.leave_inprocess_work(agent)
                time.sleep(GAP)
                self.end(self.host, agent)
                time.sleep(GAP)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                job = self.anchor.run()
                await self.assert_refused(job)
                self.clear_inprocess_work(agent)
                await self.assert_owner(job)
                self.end(self.host, agent)

    # -- negatives ------------------------------------------------------------------------------------------------------
    def leave_inprocess_work(self, agent):
        self.launch(agent)          # Codex: a native sub-agent thread in the kept app-server; Claude: a background Task

    def clear_inprocess_work(self, agent):
        self.finish(agent)

    async def test_kept_anchor_guest_subagent_spawns_during_owner_turn_is_refused(self):
        """r0 F2 red: the friend turn's sub-agent outlives it on the kept anchor and starts a FRESH command during the
        next owner turn — every hop born after the owner stamp, outside any guest span — must still not be the owner."""
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
                self.leave_inprocess_work(agent)
                time.sleep(GAP)
                self.end(self.host, agent)
                time.sleep(GAP)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                job = self.anchor.run()
                born = int(_ticks(job)) / busy_launch.HZ
                self.assertFalse(application_ingress._guest_born(self.host, born), 'the job itself is owner-era')
                await self.assert_refused(job)
                self.clear_inprocess_work(agent)
                self.end(self.host, agent)

    async def test_new_anchor_generation_has_no_guest_history(self):
        """/stop or release kills the kept anchor; the next generation is clean even with an old guest span recorded."""
        agent = self.codex(self.host, self.anchor)
        self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
        self.leave_inprocess_work(agent)
        self.end(self.host, agent)
        time.sleep(GAP)
        self.anchor = _Anchor(self)                  # a fresh app-server (the kept one ended with its sub-agents)
        agent = self.codex(self.host, self.anchor)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        await self.assert_owner(self.anchor.run())

    async def test_queued_behind_guest_turn_is_not_owner(self):
        for name, make in self.agents():
            for guest in ({'kind': 'friend_task', 'role': 'task'}, provenance.email_guest('guest@example.com'),
                          provenance.automation('r0331-webhook')):
                with self.subTest(harness=name, guest=guest.get('kind')):
                    agent = make(self.host, self.anchor)
                    self.begin(self.host, agent, self.send(5, guest))
                    time.sleep(GAP)
                    owner = self.send(7, provenance.phone())       # queued now: Send.created
                    time.sleep(GAP)
                    job = self.anchor.run()                        # born in the guest turn, after the queue time
                    time.sleep(GAP)
                    self.end(self.host, agent)
                    time.sleep(GAP)
                    self.begin(self.host, agent, owner)
                    await self.assert_refused(job)
                    self.end(self.host, agent)

    async def test_forged_or_unverified_sources_refused(self):
        for name, make in self.agents():
            with self.subTest(harness=name, case='friend origin'):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
                time.sleep(GAP)
                result, endpoint = await self.feedback(self.host, self.anchor.run())
                self.assertIn(result.get('reason'), ('privacy-check/source_denied', 'privacy-check/peer_unverified'), result)
                endpoint.assert_not_called()
                self.end(self.host, agent)
            with self.subTest(harness=name, case='stamp of another send'):
                agent = make(self.host, self.anchor)
                owner = self.send(7, provenance.phone())
                self.begin(self.host, agent, owner)
                agent.cur_send = self.send(7, provenance.phone())    # cur_send replaced without agent_turn_start
                time.sleep(GAP)
                await self.assert_refused(self.anchor.run())
                self.end(self.host, agent)
            with self.subTest(harness=name, case='other epoch'):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(7, provenance.phone(), epoch=self.epoch + 1))
                time.sleep(GAP)
                await self.assert_refused(self.anchor.run())        # r1 F5: turn_bound re-checks the epoch too
                self.end(self.host, agent)
            with self.subTest(harness=name, case='not under the anchor'):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                stranger = subprocess.Popen(['sleep', '30'])
                self.addCleanup(stranger.wait)
                self.addCleanup(stranger.kill)
                await self.assert_refused(stranger.pid)
                self.end(self.host, agent)

    # -- r1 -------------------------------------------------------------------------------------------------------------
    async def test_restart_unknown_inventory_refused_until_main_stop(self):
        """r1 F1: serve restarted under a still-running shared Claude session (anchor older than serve's floor, no spans
        remembered). A guest task that survived inside the session spawns fresh nested commands in the first owner turn:
        refused — caller origin, turn_bound, feedback and the ordinary-ingress fallback — until Claude Code's own main
        Stop reports the background list; then the owner's command passes."""
        self.host._nonowner_floor = time.clock_gettime(time.CLOCK_BOOTTIME)    # this serve started after the session
        agent = self.claude(self.host, self.anchor, reconciled=False)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run('nest')
        await self.assert_refused(job)
        self.assertIsNone(self.ingress_kind(self.host, job))
        self.main_stop(agent)                          # the authoritative inventory: nothing in-process running
        await self.assert_owner(job)
        self.assertEqual(self.ingress_kind(self.host, job), 'native')
        self.end(self.host, agent)

    async def test_restart_still_running_task_keeps_refusing(self):
        self.host._nonowner_floor = time.clock_gettime(time.CLOCK_BOOTTIME)
        agent = self.claude(self.host, self.anchor, reconciled=False)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        self.main_stop(agent, tasks=('survivor',))     # Claude Code says a background task is still running
        time.sleep(GAP)
        await self.assert_refused(self.anchor.run('nest'))
        self.end(self.host, agent)

    async def test_nested_async_subagent_is_counted(self):
        """r1 F3: a sub-agent of the guest turn launches its own async sub-agent; the main Stop list no longer shows the
        first, but the nested one keeps the anchor tainted until its own SubagentStop."""
        agent = self.claude(self.host, self.anchor)
        self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
        self.launch(agent, 'sub-1')
        self.launch(agent, 'sub-2', by='sub-1')
        self.finish(agent, 'sub-1')
        self.main_stop(agent)
        self.assertEqual(agent.bg_live, {})
        self.assertIn('sub-2', agent.bg_nested)
        self.end(self.host, agent)
        time.sleep(GAP)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run()
        await self.assert_refused(job)
        self.finish(agent, 'sub-2')
        await self.assert_owner(job)
        self.end(self.host, agent)

    async def test_ordinary_ingress_fallback_keeps_the_guest_refusal(self):
        """r1 F3 (codex) / F2 (claude): an owner background job from an earlier owner turn is owner_local; the same job
        is not once a guest turn left in-process work running on the anchor (unknown_agent must not fall through)."""
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(6, provenance.phone()))
                time.sleep(GAP)
                old = self.anchor.run()                       # the owner's own background job
                self.end(self.host, agent)
                time.sleep(GAP)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                self.assertIsNone(self.owner_root(self.host, old), 'born before this turn: not a direct caller')
                self.assertEqual(self.ingress_kind(self.host, old), 'owner_local')
                self.end(self.host, agent)
                self.begin(self.host, agent, self.send(8, {'kind': 'friend_task', 'role': 'task'}))
                self.launch(agent)
                self.end(self.host, agent)
                time.sleep(GAP)
                self.begin(self.host, agent, self.send(9, provenance.phone()))
                self.assertNotEqual(self.ingress_kind(self.host, old), 'owner_local')
                self.finish(agent)
                self.assertEqual(self.ingress_kind(self.host, old), 'owner_local')
                self.end(self.host, agent)

    # -- r2 -------------------------------------------------------------------------------------------------------------
    async def test_pending_agent_call_keeps_taint_across_empty_main_stop(self):
        """r2 F2: an empty Stop.background_tasks is not absence while a foreground Agent call is still pending."""
        agent = self.claude(self.host, self.anchor)
        self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
        self.pending_agent_call(agent)
        self.end(self.host, agent)
        time.sleep(GAP)                     # the empty Stop comes well after the guest span closed (beyond SPAN_MARGIN)
        self.main_stop(agent)
        time.sleep(GAP)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run()
        await self.assert_refused(job)
        self.hook(agent, {'hook_event_name': 'SubagentStart', 'agent_id': 'fg-1', 'agent_type': 'worker'})
        self.finish(agent, 'fg-1')
        self.main_stop(agent)
        await self.assert_owner(job)
        self.end(self.host, agent)

    async def test_restart_unseen_agent_id_keeps_refusing_until_its_stop(self):
        """r2 F1 (claude): after a restart a sub-agent this serve never saw start sends a PreToolUse; a later empty main
        Stop does not launder it — only its own SubagentStop does."""
        self.host._nonowner_floor = time.clock_gettime(time.CLOCK_BOOTTIME)
        agent = self.claude(self.host, self.anchor, reconciled=False)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        self.hook(agent, {'hook_event_name': 'PreToolUse', 'agent_id': 'ghost', 'tool_name': 'Bash',
                          'tool_input': {'command': 'true'}})
        self.main_stop(agent)
        time.sleep(GAP)
        job = self.anchor.run('nest')
        await self.assert_refused(job)
        self.finish(agent, 'ghost')
        self.main_stop(agent)
        await self.assert_owner(job)
        self.end(self.host, agent)

    async def test_nested_overflow_is_sticky_for_the_session_generation(self):
        """r2 F2 (claude): more nested async launches than the inventory keeps → unknown until a new session generation."""
        self.host._nonowner_floor = time.clock_gettime(time.CLOCK_BOOTTIME)
        agent = self.claude(self.host, self.anchor)
        for i in range(66):
            self.launch(agent, f'n-{i}', by='parent')
        for i in range(66):
            self.finish(agent, f'n-{i}')
        self.main_stop(agent)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        await self.assert_refused(self.anchor.run())
        self.end(self.host, agent)

    # -- r3 -------------------------------------------------------------------------------------------------------------
    async def r3_case(self, poison):
        """Restart → empty main Stop (clean point) → delay > SPAN_MARGIN → `poison` (unseen guest sub-agent / overflow)
        → fresh nested command and an owner job, during the owner turn: nothing is the owner's."""
        self.host._nonowner_floor = time.clock_gettime(time.CLOCK_BOOTTIME)
        agent = self.claude(self.host, self.anchor, reconciled=False)
        self.begin(self.host, agent, self.send(6, provenance.phone()))
        time.sleep(GAP)
        old = self.anchor.run()                                  # the owner's own background job (owner_local candidate)
        self.main_stop(agent)
        self.assertIsNotNone(agent.bg_clean)
        self.end(self.host, agent)
        time.sleep(GAP)                                          # well beyond SPAN_MARGIN after the clean point
        self.assertEqual(self.ingress_kind(self.host, old), None)        # no current Send: ingress not admitted yet
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        self.assertEqual(self.ingress_kind(self.host, old), 'owner_local')   # control before the poison
        poison(agent)
        time.sleep(GAP)
        job = self.anchor.run('nest')
        await self.assert_refused(job)
        self.assertIsNone(self.ingress_kind(self.host, job))
        self.assertNotEqual(self.ingress_kind(self.host, old), 'owner_local')
        return agent, job

    async def test_r3_unseen_subagent_after_clean_point_taints_unconditionally(self):
        def unseen(agent):
            self.hook(agent, {'hook_event_name': 'PreToolUse', 'agent_id': 'ghost', 'tool_name': 'Bash',
                              'tool_input': {'command': 'true'}})
        agent, job = await self.r3_case(unseen)
        self.main_stop(agent)                                    # another empty Stop does not clear it
        await self.assert_refused(job)
        self.finish(agent, 'ghost')                              # only its own SubagentStop does
        await self.assert_owner(job)
        self.end(self.host, agent)

    async def test_r3_overflow_after_clean_point_taints_until_new_generation(self):
        def overflow(agent):
            for i in range(66):
                self.launch(agent, f'n-{i}', by='parent')
            for i in range(66):
                self.finish(agent, f'n-{i}')
        agent, job = await self.r3_case(overflow)
        self.main_stop(agent)
        await self.assert_refused(job)
        self.end(self.host, agent)

    async def watchdog_probe(self, agent, tick=time.sleep):
        """Codex r4 F1 probe: an owner job from an earlier owner turn (owner_local candidate); a guest foreground
        sub-agent announces a tool (SubagentStart + PreToolUse), the main Stop is held (fake), the stale watchdog drops
        the sub-agent and commits; the owner turn starts and, with NO further hook, a delayed fresh process appears."""
        self.begin(self.host, agent, self.send(4, provenance.phone()))
        tick(GAP)
        old = self.anchor.run()
        self.end(self.host, agent)
        tick(GAP)
        self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
        self.hook(agent, {'hook_event_name': 'SubagentStart', 'agent_id': 'g1', 'agent_type': 'worker'})
        self.hook(agent, {'hook_event_name': 'PreToolUse', 'agent_id': 'g1', 'tool_name': 'Bash',
                          'tool_input': {'command': 'sleep 1; long-running'}})
        self.main_stop(agent)
        self.assertIsNotNone(agent.state._deferred_stop, 'shared_state held the fake Stop')
        agent.state._on_stale()                                   # the watchdog drops g1 and commits the held Stop
        self.assertEqual(agent.state._foreground_open(), 0)
        self.end(self.host, agent)
        tick(GAP)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        tick(GAP)
        return old, self.anchor.run('nest')                       # no hook since the watchdog

    async def test_r3_fake_stop_then_stale_watchdog_keeps_guest_subagent_tainting(self):
        agent = self.claude(self.host, self.anchor)
        old, job = await self.watchdog_probe(agent)
        await self.assert_refused(job)
        self.assertIsNone(self.ingress_kind(self.host, job))
        self.assertNotEqual(self.ingress_kind(self.host, old), 'owner_local')
        self.main_stop(agent)                                     # an empty main Stop does not resolve it
        await self.assert_refused(job)
        self.finish(agent, 'g1')                                  # only its own SubagentStop does
        self.main_stop(agent)
        await self.assert_owner(job)
        self.assertEqual(self.ingress_kind(self.host, old), 'owner_local')
        self.end(self.host, agent)

    async def test_r4_failed_agent_call_pops_only_its_own_pending_entry(self):
        """Claude r4 F4: a failure matches (type, description); another pending call of the same type stays pending."""
        agent = self.claude(self.host, self.anchor)
        first = {'tool_name': 'Agent', 'tool_input': {'subagent_type': 'worker', 'description': 'guest call'}}
        second = {'tool_name': 'Agent', 'tool_input': {'subagent_type': 'worker', 'description': 'other call'}}
        self.hook(agent, {'hook_event_name': 'PreToolUse', **first})
        self.hook(agent, {'hook_event_name': 'PreToolUse', **second})
        self.hook(agent, {'hook_event_name': 'PostToolUseFailure', **second, 'error': 'synthetic failure'})
        self.assertEqual(agent.sub_pending, [('worker', 'guest call')])

    async def test_r3_open_foreground_subagent_counts_as_leftover(self):
        agent = self.claude(self.host, self.anchor)
        self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
        self.hook(agent, {'hook_event_name': 'SubagentStart', 'agent_id': 'g2', 'agent_type': 'worker'})
        self.end(self.host, agent)
        time.sleep(GAP)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run()
        await self.assert_refused(job)
        self.finish(agent, 'g2')
        await self.assert_owner(job)
        self.end(self.host, agent)

    async def test_r3_many_reconciled_owner_subagents_do_not_overflow(self):
        """Claude r3 F3 (availability): 600 owner sub-agents started and stopped one after another are reconciled work —
        the owner still passes (only LIVE unknown work counts toward the overflow)."""
        agent = self.claude(self.host, self.anchor)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        for i in range(600):
            self.hook(agent, {'hook_event_name': 'SubagentStart', 'agent_id': f'own-{i}', 'agent_type': 'worker'})
            self.hook(agent, {'hook_event_name': 'PreToolUse', 'agent_id': f'own-{i}', 'tool_name': 'Read',
                              'tool_input': {'file_path': '/dev/null'}})
            self.hook(agent, {'hook_event_name': 'SubagentStop', 'agent_id': f'own-{i}'})
        self.main_stop(agent)
        self.assertIsNone(agent.__dict__.get('bg_overflow'))
        time.sleep(GAP)
        await self.assert_owner(self.anchor.run())
        self.end(self.host, agent)

    async def test_r3_failed_agent_call_leaves_no_leftover(self):
        self.host._nonowner_floor = time.clock_gettime(time.CLOCK_BOOTTIME)    # restart: history unknown, overlap applies
        agent = self.claude(self.host, self.anchor, reconciled=False)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        call = {'tool_name': 'Agent', 'tool_input': {'subagent_type': 'worker', 'description': 'x'}}
        self.hook(agent, {'hook_event_name': 'PreToolUse', **call})
        self.assertTrue(agent.sub_pending)
        self.hook(agent, {'hook_event_name': 'PostToolUseFailure', **call, 'error': 'synthetic failure'})
        self.assertEqual(agent.sub_pending, [])
        self.main_stop(agent)
        time.sleep(GAP)
        await self.assert_owner(self.anchor.run())
        self.end(self.host, agent)


@unittest.skipUnless(LINUX, 'macOS twin is backed by Linux /proc rows')
class DarwinTwin(_Matrix, unittest.IsolatedAsyncioTestCase):
    """r0 F1: both macOS branches (_darwin_scope phone branch, _darwin_owned_scope) use the turn stamp, the guest spans,
    the anchor taint and the clock-step guard. Runs the darwin code with /proc-backed wall-clock rows."""

    def setUp(self):
        self.anchor = _Anchor(self)
        for p in (mock.patch.object(process_evidence, 'darwin_row', side_effect=proc_row),
                  mock.patch.object(approval_material.sys, 'platform', 'darwin')):
            p.start()
            self.addCleanup(p.stop)
        time.sleep(GAP)
        self.host = self.make_host()

    def agents(self):
        def claude(host, anchor):
            return self.claude(host, anchor, token=proc_row(anchor.pid)[2])
        return (('codex', self.codex), ('claude', claude))

    async def outcome(self, job):
        result, endpoint = await self.feedback(self.host, job)
        return ('ok' if result.get('status') == 'ok' else 'refused'), result.get('reason') or None, endpoint.called

    async def test_owner_turn_passes(self):
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                self.assertEqual(await self.outcome(self.anchor.run()), ('ok', None, True))
                self.end(self.host, agent)

    async def test_queued_behind_friend_turn_refused(self):
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
                time.sleep(GAP)
                owner = self.send(7, provenance.phone())
                time.sleep(GAP)
                job = self.anchor.run()
                time.sleep(GAP)
                self.end(self.host, agent)
                time.sleep(GAP)
                self.begin(self.host, agent, owner)
                self.assertEqual(await self.outcome(job), ('refused', 'privacy-check/peer_unverified', False))
                self.end(self.host, agent)

    async def test_kept_anchor_guest_leftover_refused(self):
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
                self.launch(agent)
                self.end(self.host, agent)
                time.sleep(GAP)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                job = self.anchor.run()
                self.assertEqual(await self.outcome(job), ('refused', 'privacy-check/peer_unverified', False))
                self.finish(agent)
                self.assertEqual(await self.outcome(job), ('ok', None, True))
                self.end(self.host, agent)

    async def test_clock_step_refuses(self):
        """r0 F4 / r1 F2: a wall-clock step between turns, or during this turn, voids the stamp — and keeps refusing for
        the rest of this serve's life, even after later steady stamps (the recorded wall-clock history stays corrupt)."""
        agent = self.codex(self.host, self.anchor)
        self.begin(self.host, agent, self.send(6, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run()
        self.assertEqual(await self.outcome(job), ('ok', None, True))     # steady clock: passes
        real, cont = time.time, approval_material._continuous
        with mock.patch.object(approval_material.time, 'time', lambda: real() + 30):     # step during the turn
            self.assertEqual((await self.outcome(job))[:2], ('refused', 'privacy-check/peer_unverified'))
        self.end(self.host, agent)
        for mid in (7, 8, 9):                                  # later steady turns: still refused
            self.begin(self.host, agent, self.send(mid, provenance.phone()))
            time.sleep(GAP)
            self.assertEqual((await self.outcome(self.anchor.run()))[:2], ('refused', 'privacy-check/peer_unverified'))
            self.end(self.host, agent)
        fresh = self.make_host()                               # a serve restart starts a clean clock history
        agent = self.codex(fresh, self.anchor)
        self.host = fresh
        self.begin(fresh, agent, self.send(10, provenance.phone()))
        time.sleep(GAP)
        self.assertEqual(await self.outcome(self.anchor.run()), ('ok', None, True))
        self.end(fresh, agent)

    async def test_restart_unknown_inventory_refused_until_main_stop(self):
        agent = self.claude(self.host, self.anchor, token=proc_row(self.anchor.pid)[2], reconciled=False)
        self.host._nonowner_floor = time.time()                    # serve restarted after the session started
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run('nest')
        self.assertEqual(await self.outcome(job), ('refused', 'privacy-check/peer_unverified', False))
        self.main_stop(agent)
        self.assertEqual(await self.outcome(job), ('ok', None, True))
        self.end(self.host, agent)

    async def test_clock_step_contaminates_persistently(self):
        """r1 F2 deterministic probe: guest span [99, 70] (wall clock stepped back during the guest turn), a guest child
        born at wall 100 under the owned anchor; owner stamps at 71, 72, 73 (continuous clock steady) — all refused,
        and the ordinary-ingress fallback too. Control without a step: a child born after the owner stamp passes."""
        me = os.getpid()
        rows = {501: (me, 50.0, 'a'), 502: (501, 100.0, 'c'), me: (1, 10.0, 'serve')}
        clock = {'wall': 99.0, 'cont': 1000.0}
        agent = NS(kind='codex', cfg={'session_mode': 'shared'}, session=None, phone_turn=True, cur_send=None,
                   proc=NS(pid=501, returncode=None), busy_scope=lambda: 'shared-codex')
        self.host.agent = agent
        self.host._nonowner_spans = [[99.0, 70.0]]
        with mock.patch.object(process_evidence, 'darwin_row', side_effect=lambda p: rows.get(p)), \
                mock.patch.object(approval_material.time, 'time', lambda: clock['wall']), \
                mock.patch.object(approval_material, '_continuous', lambda: clock['cont']):
            approval_material.turn_start(self.host, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
            clock.update(wall=71.0, cont=1002.0)                   # 2 s later on the continuous clock, wall −28 s
            for wall in (71.0, 72.0, 73.0):
                clock.update(wall=wall, cont=1002.0 + wall - 71.0)
                owner = self.send(7, provenance.phone())
                owner.created = time.monotonic()
                agent.cur_send = owner
                approval_material.turn_start(self.host, owner)
                self.assertIsNone(approval_material.verified_caller_origin(self.host, 502), wall)
                self.assertFalse(order_host.turn_bound(self.host, 502), wall)
                self.assertFalse(application_ingress._owner_tree(self.host, 502, agent), wall)
            self.assertTrue(self.host._clock_step)
        control = self.make_host()
        control._nonowner_spans, control.agent = [[60.0, 70.0]], agent
        rows[502] = (501, 102.0, 'c2')
        clock.update(wall=101.0, cont=2000.0)
        with mock.patch.object(process_evidence, 'darwin_row', side_effect=lambda p: rows.get(p)), \
                mock.patch.object(approval_material.time, 'time', lambda: clock['wall']), \
                mock.patch.object(approval_material, '_continuous', lambda: clock['cont']):
            owner = self.send(7, provenance.phone())
            owner.created = time.monotonic() - 5            # queued 5 s before its turn started
            agent.cur_send = owner
            approval_material.turn_start(control, owner)
            clock.update(wall=103.0, cont=2002.0)
            self.assertEqual(approval_material.verified_caller_origin(control, 502)['root_message_id'], 7)

    async def test_pending_agent_call_keeps_taint_across_empty_main_stop(self):
        agent = self.claude(self.host, self.anchor, token=proc_row(self.anchor.pid)[2])
        self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
        self.pending_agent_call(agent)
        self.end(self.host, agent)
        time.sleep(GAP)                     # the empty Stop comes well after the guest span closed (beyond SPAN_MARGIN)
        self.main_stop(agent)
        time.sleep(GAP)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        self.assertEqual(await self.outcome(self.anchor.run()), ('refused', 'privacy-check/peer_unverified', False))
        self.end(self.host, agent)

    async def test_desktop_clock_step_detected_without_any_send(self):
        """r2 F1 (codex) probe: no Send at all. A guest span recorded on a steady clock [60, 70]; then the wall clock is
        stepped back ~29 s; a desktop owner turn's caller (born at wall 100, after the span) must be refused at the
        proof itself — and stay refused on later steady proofs. Control: the same chronology without a step passes."""
        me = os.getpid()
        rows = {500: (1, 50.0, 'S'), 501: (500, 100.0, 'm'), 502: (501, 100.0, 'c'), me: (1, 10.0, 'serve')}
        for stepped in (False, True):
            with self.subTest(stepped=stepped):
                host = self.make_host()
                host.desktop_turn = 1
                origin = {**self.origin(7), 'host_received_monotonic': str(time.monotonic() - 40)}
                host._approval_origins = {1: origin}
                agent = NS(session={'pid': 500, 'procStart': 'S'}, phone_turn=False, phone_seen=False, cur_send=None,
                           bg_live={}, bg_known=(500, 'S'), sub_pending=[], bg_nested={}, cfg={'session_mode': 'shared'})
                host.agent = agent
                clock = {'wall': 60.0, 'cont': 1000.0}
                with mock.patch.object(process_evidence, 'darwin_row', side_effect=lambda p: rows.get(p)), \
                        mock.patch.object(approval_material.time, 'time', lambda: clock['wall']), \
                        mock.patch.object(approval_material, '_continuous', lambda: clock['cont']):
                    application_ingress.open_span(host)                      # guest turn, steady clock
                    clock.update(wall=70.0, cont=1010.0)
                    application_ingress.close_span(host)
                    clock.update(wall=101.0 if not stepped else 72.0, cont=1041.0)   # stepped: wall −29 s vs continuous
                    caller = approval_material.verified_caller_origin(host, 502)
                    bound = order_host.turn_bound(host, 502)
                    if not stepped:
                        self.assertEqual((caller['root_message_id'], bound), (7, True))
                        continue
                    self.assertEqual((caller, bound), (None, False))
                    self.assertTrue(host._clock_step)
                    self.assertTrue(approval_material.lineage_blocked(host, agent, 50.0))
                    for later in (1.0, 2.0, 3.0):                            # steady from now on: still refused
                        clock.update(wall=72.0 + later, cont=1041.0 + later)
                        self.assertIsNone(approval_material.verified_caller_origin(host, 502))

    async def test_r4_watchdog_dropped_guest_subagent_refused_without_further_hooks(self):
        agent = self.claude(self.host, self.anchor, token=proc_row(self.anchor.pid)[2])
        _, job = await Linux.watchdog_probe(self, agent)
        self.assertEqual(await self.outcome(job), ('refused', 'privacy-check/peer_unverified', False))
        self.assertFalse(order_host.turn_bound(self.host, job))
        self.finish(agent, 'g1')
        self.main_stop(agent)
        self.assertEqual(await self.outcome(job), ('ok', None, True))
        self.end(self.host, agent)

    async def test_r3_unseen_subagent_after_clean_point_taints_unconditionally(self):
        agent = self.claude(self.host, self.anchor, token=proc_row(self.anchor.pid)[2], reconciled=False)
        self.host._nonowner_floor = time.time()
        time.sleep(GAP)                                          # the clean point lands well after the floor (r4 F3)
        self.main_stop(agent)
        time.sleep(GAP)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        self.hook(agent, {'hook_event_name': 'PreToolUse', 'agent_id': 'ghost', 'tool_name': 'Bash',
                          'tool_input': {'command': 'true'}})
        time.sleep(GAP)
        job = self.anchor.run('nest')
        self.assertEqual(await self.outcome(job), ('refused', 'privacy-check/peer_unverified', False))
        self.assertFalse(order_host.turn_bound(self.host, job))
        self.finish(agent, 'ghost')
        self.assertEqual(await self.outcome(job), ('ok', None, True))
        self.end(self.host, agent)


@unittest.skipUnless(sys.platform == 'darwin', 'real macOS process rows (libproc)')
class RealDarwin(_Matrix, unittest.IsolatedAsyncioTestCase):
    """Real-Mac acceptance (both r0/r1/r2 reviews): the macOS branches against REAL process_evidence.darwin_row (libproc
    PROC_PIDTBSDINFO), real spawned processes and the real serve-parent check — this test process plays serve. No row,
    clock or platform is mocked except where a test deliberately steps the wall clock."""

    def setUp(self):
        self.anchor = _Anchor(self)                    # a real child of this process, as the app-server is of serve
        time.sleep(GAP)
        self.host = self.make_host()
        self.assertEqual(process_evidence.darwin_row(self.anchor.pid)[0], os.getpid())

    def agents(self):
        def claude(host, anchor, **kw):
            return self.claude(host, anchor, token=process_evidence.darwin_row(anchor.pid)[2], **kw)
        return (('codex', self.codex), ('claude', claude))

    async def outcome(self, job):
        result, endpoint = await self.feedback(self.host, job)
        return ('ok' if result.get('status') == 'ok' else 'refused'), result.get('reason') or None, endpoint.called

    REFUSED = ('refused', 'privacy-check/peer_unverified', False)

    async def test_owner_turn_and_nested_command_pass(self):
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                for job in (self.anchor.run(), self.anchor.run('nest')):
                    self.assertEqual(self.owner_root(self.host, job), 7)
                    self.assertTrue(order_host.turn_bound(self.host, job))
                    self.assertEqual(await self.outcome(job), ('ok', None, True))
                self.end(self.host, agent)

    async def test_queued_behind_guest_turn_refused(self):
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
                time.sleep(GAP)
                owner = self.send(7, provenance.phone())
                time.sleep(GAP)
                job = self.anchor.run()
                time.sleep(GAP)
                self.end(self.host, agent)
                time.sleep(GAP)
                self.begin(self.host, agent, owner)
                self.assertIsNone(self.owner_root(self.host, job))
                self.assertFalse(order_host.turn_bound(self.host, job))
                self.assertEqual(await self.outcome(job), self.REFUSED)
                self.end(self.host, agent)

    async def test_guest_leftover_refused_until_it_ends(self):
        for name, make in self.agents():
            with self.subTest(harness=name):
                agent = make(self.host, self.anchor)
                self.begin(self.host, agent, self.send(5, {'kind': 'friend_task', 'role': 'task'}))
                self.launch(agent)
                time.sleep(GAP)
                self.end(self.host, agent)
                time.sleep(GAP)
                self.begin(self.host, agent, self.send(7, provenance.phone()))
                time.sleep(GAP)
                job = self.anchor.run()
                self.assertEqual(await self.outcome(job), self.REFUSED)
                self.finish(agent)
                self.assertEqual(await self.outcome(job), ('ok', None, True))
                self.end(self.host, agent)

    async def test_restart_unknown_inventory_refused_until_main_stop(self):
        agent = self.claude(self.host, self.anchor, token=process_evidence.darwin_row(self.anchor.pid)[2], reconciled=False)
        self.host._nonowner_floor = time.time()            # serve "restarted" after the session started
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run('nest')
        self.assertEqual(await self.outcome(job), self.REFUSED)
        self.main_stop(agent)
        self.assertEqual(await self.outcome(job), ('ok', None, True))
        self.end(self.host, agent)

    async def test_clock_step_is_sticky(self):
        agent = self.codex(self.host, self.anchor)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run()
        self.assertEqual(await self.outcome(job), ('ok', None, True))
        real = time.time
        with mock.patch.object(approval_material.time, 'time', lambda: real() - 30):
            self.assertEqual(await self.outcome(job), self.REFUSED)
        self.assertTrue(self.host._clock_step)
        self.end(self.host, agent)
        self.begin(self.host, agent, self.send(8, provenance.phone()))
        time.sleep(GAP)
        self.assertEqual(await self.outcome(self.anchor.run()), self.REFUSED)
        self.end(self.host, agent)

    async def test_anchor_must_be_this_serves_child(self):
        """Owned branch serve-parent check on real rows: an anchor whose parent is not this process is refused."""
        agent = self.codex(self.host, self.anchor)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        job = self.anchor.run('nest')
        shell = process_evidence.darwin_row(job)[0]                     # the shell hop: child of the anchor, not of us
        self.assertNotEqual(process_evidence.darwin_row(shell)[0], os.getpid())
        agent.proc = NS(pid=shell, returncode=None)
        self.assertIsNone(self.owner_root(self.host, job))
        self.assertEqual(await self.outcome(job), self.REFUSED)
        agent.proc = self.anchor.p
        self.assertEqual(await self.outcome(job), ('ok', None, True))
        self.end(self.host, agent)

    async def test_r3_unseen_and_dropped_foreground_subagents_refused(self):
        """Round 4 on real rows: (a) restart → empty Stop → later unseen sub-agent; (b) guest foreground sub-agent behind
        a fake Stop that the stale watchdog committed — both refuse a fresh nested command until their SubagentStop."""
        token = process_evidence.darwin_row(self.anchor.pid)[2]
        agent = self.claude(self.host, self.anchor, token=token, reconciled=False)
        self.host._nonowner_floor = time.time()
        time.sleep(GAP)                                          # the clean point lands well after the floor (r4 F3)
        self.main_stop(agent)
        time.sleep(GAP)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        self.hook(agent, {'hook_event_name': 'PreToolUse', 'agent_id': 'ghost', 'tool_name': 'Bash',
                          'tool_input': {'command': 'true'}})
        time.sleep(GAP)
        job = self.anchor.run('nest')
        self.assertEqual(await self.outcome(job), self.REFUSED)
        self.finish(agent, 'ghost')
        self.assertEqual(await self.outcome(job), ('ok', None, True))
        self.end(self.host, agent)
        self.begin(self.host, agent, self.send(8, {'kind': 'friend_task', 'role': 'task'}))
        self.hook(agent, {'hook_event_name': 'SubagentStart', 'agent_id': 'g1', 'agent_type': 'worker'})
        self.hook(agent, {'hook_event_name': 'PreToolUse', 'agent_id': 'g1', 'tool_name': 'Bash',
                          'tool_input': {'command': 'true'}})
        self.main_stop(agent)
        self.assertIsNotNone(agent.state._deferred_stop)
        agent.state._on_stale()
        self.end(self.host, agent)
        time.sleep(GAP)
        self.begin(self.host, agent, self.send(9, provenance.phone()))
        time.sleep(GAP)                                          # no hook between the watchdog and admission (r4 F1)
        job = self.anchor.run('nest')
        self.assertEqual(await self.outcome(job), self.REFUSED)
        self.finish(agent, 'g1')
        self.main_stop(agent)
        self.assertEqual(await self.outcome(job), ('ok', None, True))
        self.end(self.host, agent)

    async def test_unrelated_process_refused(self):
        agent = self.codex(self.host, self.anchor)
        self.begin(self.host, agent, self.send(7, provenance.phone()))
        time.sleep(GAP)
        stranger = subprocess.Popen(['sleep', '30'])
        self.addCleanup(stranger.wait)
        self.addCleanup(stranger.kill)
        self.assertEqual(await self.outcome(stranger.pid), self.REFUSED)
        self.end(self.host, agent)


@unittest.skipUnless(LINUX, 'Linux /proc branch')
class Integration(_Matrix, unittest.IsolatedAsyncioTestCase):
    """r0 F3: the stamp comes from the real serve.Host.agent_turn_start, called by the real Agent queue loop and by the
    real SharedClaudeAgent._merge_queued — no hand-set _turn_started."""

    async def asyncSetUp(self):
        from agentj.state import State
        from test_l1 import _host
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        self.st = State(Path(tmp.name) / 's')
        self.st.init(relay='ws://127.0.0.1:1')
        self.h = _host(self.st, [])
        self.epoch = self.h.hist.epoch
        self.anchor = _Anchor(self)
        time.sleep(GAP)

    def real_send(self, mid, source):
        s = self.send(mid, source)
        s.approval_origin['session'] = str(self.h.hist.epoch)
        return s

    async def test_queue_loop_owner_queued_behind_friend(self):
        """Agent.run: friend Send runs, the owner's Send waits in the queue, the friend turn starts a job, then the owner
        turn starts on the same app-server. The friend's job is refused; the owner turn's own job passes."""
        seen = {}

        class Probe(SharedCodexAgent):
            async def _identity_refresh(self):
                return None

            async def turn(inner, text):
                send = inner.cur_send
                if send.turn == 5:
                    time.sleep(GAP)
                    inner.q.put_nowait(owner)                     # the owner queues behind the running friend turn
                    time.sleep(GAP)
                    seen['guest'] = self.anchor.run()
                    time.sleep(GAP)
                else:
                    time.sleep(GAP)
                    seen['owner'] = self.anchor.run()
                    seen['owner_root'] = self.owner_root(self.h, seen['owner'])
                    seen['guest_root'] = self.owner_root(self.h, seen['guest'])
                    seen['guest_bound'] = order_host.turn_bound(self.h, seen['guest'])
                    seen['owner_bound'] = order_host.turn_bound(self.h, seen['owner'])
                    seen['done'].set_result(True)

        cfg = {**(self.h.agent_cfg or {}), 'kind': 'codex', 'session_mode': 'shared', 'fence': False}
        agent = Probe(self.h, cfg)
        agent.proc = self.anchor.p
        self.h.agent = agent
        self.h.busy.context['shared-codex'] = {'pid': self.anchor.pid, 'proc_start': _ticks(self.anchor.pid)}
        friend = self.real_send(5, {'kind': 'friend_task', 'role': 'task'})
        owner = self.real_send(7, provenance.phone())
        seen['done'] = asyncio.get_running_loop().create_future()
        agent.q.put_nowait(friend)
        loop = asyncio.create_task(agent.run())
        try:
            await asyncio.wait_for(seen['done'], 30)
        finally:
            loop.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await loop
        self.assertEqual((seen['guest_root'], seen['guest_bound']), (None, False))
        self.assertEqual((seen['owner_root'], seen['owner_bound']), (7, True))

    async def test_merge_queued_into_friend_turn(self):
        """shared._merge_queued: the owner's say merged into the running friend turn (real agent_turn_start). A job born
        before the merge, and one born after it inside the still-open friend span, are both refused."""
        cfg = {**(self.h.agent_cfg or {}), 'kind': 'claude', 'dir': str(self.st.root), 'session_mode': 'shared', 'fence': False}
        native = SharedClaudeAgent(self.h, cfg)
        self.assertEqual(native.state._on_drop, native._bg_unresolved, 'r4: watchdog drops reach the inventory')
        native.session = {'sessionId': 'r0331', 'pid': self.anchor.pid, 'procStart': _ticks(self.anchor.pid)}
        self.h.agent = native
        friend = self.real_send(5, {'kind': 'friend_task', 'role': 'task'})
        native.cur_send = friend
        native.phone_turn = native.phone_seen = True
        self.h.agent_turn_start('friend', friend)
        time.sleep(GAP)
        owner = self.real_send(7, provenance.phone())
        native.q.put_nowait(owner)
        time.sleep(GAP)
        before = self.anchor.run()
        time.sleep(GAP)
        with mock.patch.object(native, 'deliver', mock.AsyncMock()):
            await native._merge_queued()
        self.assertIs(native.cur_send, owner)
        self.assertIs(self.h._turn_started[0], owner, 'the merge stamped through the real agent_turn_start')
        time.sleep(GAP)
        after = self.anchor.run()
        for job in (before, after):
            self.assertIsNone(self.owner_root(self.h, job))
            self.assertFalse(order_host.turn_bound(self.h, job))
        self.h.agent_turn_end()


if __name__ == '__main__':
    unittest.main()
