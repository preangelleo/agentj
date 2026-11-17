"""R-0052 (R-0047-r1 F1, Linux): the Linux owned / shared branch of approval_material._direct_scope takes the turn start
from Send.created — the time the owner's message was QUEUED — not the time the owner's turn began on the anchor.

Real processes, real /proc, real CLOCK_BOOTTIME; nothing in process evidence is mocked. The anchor is a live process that
stands in for the long-lived harness process (the shared Claude session pid / a kept Codex app-server); during a
non-owner turn it starts a background job through a shell hop (Claude `run_in_background`, a Codex exec session) *after*
the owner's message was queued behind that turn. When the owner's turn starts on the same anchor, that guest-born job
must NOT resolve as the owner's caller (verified_caller_origin) and must NOT count as bound to the owner's current turn
(core/order_host.turn_bound — the card-free owner_dispatch flag).

The *_guest_born_* tests are expected RED on 0.30.29 (they are the repro). The controls are expected GREEN.
Host / agent are attribute stubs only (same shape as agent.py:649 / shared.py:2683 set them); no serve, no friend store,
no network, no order is created.
"""
import _hermetic  # noqa: F401  (throw-away state dir / config home)
import os
import secrets
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentj import application_ingress, approval_material, busy_launch, compose, provenance
from agentj.core import order_host

GAP = 0.25          # > several /proc ticks (HZ=100) so every "before/after" below is unambiguous

ANCHOR = r'''
import subprocess, sys
for line in sys.stdin:
    if line.strip() == "spawn":
        # a shell hop that stays alive (background-tool shell) with the real job under it
        p = subprocess.Popen(["sh", "-c", "sleep 120 & echo $!; wait"], stdout=subprocess.PIPE, text=True)
        print(p.pid, p.stdout.readline().strip(), flush=True)
'''


def _ticks(pid):
    return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]


@unittest.skipUnless(sys.platform.startswith('linux') and hasattr(time, 'CLOCK_BOOTTIME'), 'Linux /proc branch')
class R0052(unittest.TestCase):
    def setUp(self):
        self.anchor = subprocess.Popen([sys.executable, '-I', '-c', ANCHOR], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, text=True)
        self.addCleanup(self._reap)
        self.kids = []
        time.sleep(GAP)
        self.epoch = 1
        self.host = SimpleNamespace(hist=SimpleNamespace(epoch=self.epoch), desktop_turn=None,
                                    busy=SimpleNamespace(context={}))
        # serve started before this anchor: spans floor (application_ingress keeps this host-side already)
        self.host._nonowner_floor = time.clock_gettime(time.CLOCK_BOOTTIME) - 60

    def _reap(self):
        for pid in self.kids:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        self.anchor.kill()
        self.anchor.wait()

    # -- real process actions ------------------------------------------------------------------------------------------
    def spawn(self):
        self.anchor.stdin.write('spawn\n')
        self.anchor.stdin.flush()
        shell, job = (int(x) for x in self.anchor.stdout.readline().split())
        self.kids += [job, shell]
        table = busy_launch.procs()
        self.assertEqual(table[job][0], shell)
        self.assertEqual(table[shell][0], self.anchor.pid)
        return job

    def origin(self, mid, owner=True):
        o = {'session': str(self.epoch), 'message_id': mid, 'root_message_id': mid, 'chain': [], 'channel': 'phone',
             'verified': owner, 'suspicious': not owner, 'text': 'r0052 synthetic'}
        return o

    def send(self, kind, mid, source):
        s = compose.Send(kind, secrets.token_urlsafe(8), mid, text='r0052 synthetic', by='r0052-test', source=source)
        s.approval_origin = self.origin(mid, owner=provenance.is_owner(source))
        return s

    def agent(self, shared):
        if shared:      # SharedClaudeAgent on Linux: session dict, phone turn flags
            return SimpleNamespace(kind='claude', session={'pid': self.anchor.pid, 'procStart': _ticks(self.anchor.pid)},
                                   phone_turn=False, phone_seen=False, cur_send=None, proc=None)
        # own / shared Codex: no session dict, the app-server child is the anchor, pinned on the busy board
        self.host.busy.context['codex'] = {'pid': self.anchor.pid, 'proc_start': _ticks(self.anchor.pid)}
        return SimpleNamespace(kind='codex', session=None, phone_turn=False, cur_send=None, proc=self.anchor)

    def begin(self, agent, send):
        """agent.py:649 cur_send = send; shared.py:2683 phone_turn/phone_seen; serve.agent_turn_start → note_turn spans."""
        agent.cur_send = send
        approval_material.turn_start(self.host, send)                               # serve.agent_turn_start stamp
        agent.phone_turn = True
        agent.phone_seen = True
        if not application_ingress._owner_source(send.source, send):
            application_ingress.open_span(self.host)

    def end(self, agent):
        agent.cur_send = None
        agent.phone_turn = agent.phone_seen = False
        application_ingress.close_span(self.host)

    # -- the scenario --------------------------------------------------------------------------------------------------
    def queued_behind(self, shared, guest_source):
        """Guest turn running → owner queues → guest turn starts a background job → guest ends → owner turn on same anchor."""
        self.host.agent = agent = self.agent(shared)
        guest = self.send('friend-main', 5, guest_source)
        self.begin(agent, guest)
        time.sleep(GAP)
        owner = self.send('phone-dev', 7, provenance.phone())          # owner's say: created = queue time
        time.sleep(GAP)
        job = self.spawn()                                             # born inside the guest turn, after the queue time
        time.sleep(GAP)
        self.end(agent)
        time.sleep(GAP)
        self.begin(agent, owner)                                       # same anchor (kept app-server / shared session)
        return agent, owner, job

    def assert_refused(self, agent, job):
        scope = approval_material._direct_scope(self.host, job)
        born = busy_launch.procs()[job][3]
        guest_born = application_ingress._guest_born(self.host, int(_ticks(job)) / busy_launch.HZ)
        detail = f'scope.kind={scope and scope["kind"]} host spans say guest_born={guest_born} wall_born={born:.2f}'
        self.assertTrue(guest_born, 'host-held non-owner span must already cover the job (evidence exists)')
        caller = approval_material.verified_caller_origin(self.host, job)
        bound = order_host.turn_bound(self.host, job)
        self.assertEqual((caller and caller.get('root_message_id'), bound), (None, False),
                         '(owner origin root resolved for the guest-born job, order_host.turn_bound) — ' + detail)

    def test_shared_claude_friend_turn_guest_born_job_is_not_owner(self):
        agent, _, job = self.queued_behind(True, {'kind': 'friend_task', 'role': 'task'})
        self.assert_refused(agent, job)

    def test_shared_claude_guest_email_turn_guest_born_job_is_not_owner(self):
        # a third-party (non-owner) mail handled by the main Agent; synthetic @example.com sender
        agent, _, job = self.queued_behind(True, provenance.email_guest('guest@example.com'))
        self.assert_refused(agent, job)

    def test_codex_kept_app_server_automation_turn_guest_born_job_is_not_owner(self):
        agent, _, job = self.queued_behind(False, provenance.automation('r0052-webhook'))
        self.assert_refused(agent, job)

    def test_codex_kept_app_server_friend_turn_guest_born_job_is_not_owner(self):
        agent, _, job = self.queued_behind(False, {'kind': 'friend_task', 'role': 'task'})
        self.assert_refused(agent, job)

    def test_shared_claude_merged_into_friend_turn_guest_born_job_is_not_owner(self):
        """shared.py:2872 _merge_queued: owner's queued say merged into the running friend turn; job born before the merge."""
        self.host.agent = agent = self.agent(True)
        self.begin(agent, self.send('friend-main', 5, {'kind': 'friend_task', 'role': 'task'}))
        time.sleep(GAP)
        owner = self.send('phone-dev', 7, provenance.phone())
        time.sleep(GAP)
        job = self.spawn()
        time.sleep(GAP)
        agent.cur_send = owner                                         # merge: same native turn, cur_send swapped
        approval_material.turn_start(self.host, owner)                              # shared.py:2876 agent_turn_start
        self.assert_refused(agent, job)

    # -- controls (expected green on 0.30.29 and after the fix) ---------------------------------------------------------
    def test_control_job_born_before_owner_queued_is_refused(self):
        self.host.agent = agent = self.agent(True)
        self.begin(agent, self.send('friend-main', 5, {'kind': 'friend_task', 'role': 'task'}))
        time.sleep(GAP)
        job = self.spawn()
        time.sleep(GAP)
        owner = self.send('phone-dev', 7, provenance.phone())
        self.end(agent)
        time.sleep(GAP)
        self.begin(agent, owner)
        self.assertIsNone(approval_material.verified_caller_origin(self.host, job))
        self.assertFalse(order_host.turn_bound(self.host, job))

    def test_control_job_born_in_owner_turn_is_owner(self):
        for shared in (True, False):
            with self.subTest(shared=shared):
                self.host.agent = agent = self.agent(shared)
                owner = self.send('phone-dev', 7, provenance.phone())
                time.sleep(GAP)
                self.begin(agent, owner)
                time.sleep(GAP)
                job = self.spawn()
                self.assertEqual(approval_material.verified_caller_origin(self.host, job)['root_message_id'], 7)
                self.assertTrue(order_host.turn_bound(self.host, job))
                self.end(agent)


if __name__ == '__main__':
    unittest.main()
