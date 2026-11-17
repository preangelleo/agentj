"""R-0047: a genuine owner turn of a Host-spawned harness (Codex shared / own) on macOS can send feedback.

Mac M1, 0.30.28, Codex shared: `agentj support/feedback` from the main agent got `privacy-check/peer_unverified`
(host.log `privacy_check_failed reason=peer_unverified`). `approval_material._darwin_scope` only knew the Claude
shared-session anchor (`agent.session = {'pid', 'procStart'}`); a Host-spawned harness (Codex app-server, own Claude,
OpenCode) has no `session` dict, so every caller resolved to None. The Linux branch already anchors such turns on the
Host's own child process + `cur_send.approval_origin`; Darwin now does the same.

Real processes: the anchor is a real child of this test process (as the app-server is of serve) and the caller a real
grandchild; only `darwin_row` is backed by /proc because this runs on Linux. No Host.ask stub is involved: the decision
is the real `verified_caller_origin` -> `privacy_host.handle`; only the Jev model call is replaced (the fake endpoint).
"""
import _hermetic  # noqa: F401
import asyncio
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from agentj import approval_material, privacy, privacy_host
from agentj.core import process_evidence
from agentj.shared_codex import SharedCodexAgent

_TICK = os.sysconf('SC_CLK_TCK')
# Linux-backed twin (/proc rows rendered as darwin rows); the real-Mac run is test_r0331_owner_lineage.RealDarwin.
LINUX = sys.platform.startswith('linux') and hasattr(time, 'CLOCK_BOOTTIME')
_BOOT = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME) if LINUX else 0.0   # fixed once: rows stable across calls


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


# The anchor (stands in for the Codex app-server): waits for a line, then starts one command and prints its PID.
ANCHOR = ("import subprocess,sys\n"
          "for line in sys.stdin:\n"
          "    p=subprocess.Popen(['sleep','30']);print(p.pid,flush=True)\n")


@unittest.skipUnless(LINUX, 'macOS branch exercised with /proc-backed rows')
class DarwinOwnedHarness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.procs = []
        self.anchor = self.spawn([sys.executable, '-c', ANCHOR], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        p = mock.patch.object(process_evidence, 'darwin_row', side_effect=proc_row)
        p.start(); self.addCleanup(p.stop)
        self.epoch = 7
        self.origin = {'session': str(self.epoch), 'message_id': 41, 'root_message_id': 41, 'verified': True,
                       'suspicious': False, 'channel': 'phone', 'text': 'send feedback', 'chain': [],
                       'host_received_monotonic': str(time.monotonic())}

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            for f in (p.stdin, p.stdout):
                if f:
                    f.close()

    def spawn(self, argv, **kw):
        p = subprocess.Popen(argv, **kw)
        self.procs.append(p)
        return p

    def command(self):
        """One command the harness starts now (a descendant of the anchor)."""
        self.anchor.stdin.write('go\n'); self.anchor.stdin.flush()
        pid = int(self.anchor.stdout.readline())
        self.procs.append(NS(poll=lambda: None, kill=lambda: os.kill(pid, 9), wait=lambda: None, stdin=None, stdout=None))
        deadline = time.time() + 5
        while proc_row(pid) is None and time.time() < deadline:
            time.sleep(.01)
        return pid

    def owner_turn(self, agent, *, origin=None):
        agent.cur_send = NS(created=time.monotonic(), approval_origin=dict(origin or self.origin))
        with mock.patch.object(approval_material.sys, 'platform', 'darwin'):    # macOS clocks: process_evidence.now() is wall
            approval_material.turn_start(self.host(agent), agent.cur_send)     # serve.agent_turn_start (R-0052 stamp)
        time.sleep(2.0 / _TICK + .02)      # kernel start ticks are coarse: the command starts strictly after the turn

    def codex_shared(self):
        agent = object.__new__(SharedCodexAgent)       # the real class: no `session` dict, a Host-owned app-server
        agent.cfg = {'session_mode': 'shared'}
        agent.proc, agent.phone_turn, agent.cur_send = self.anchor, True, None
        self.assertFalse(isinstance(getattr(agent, 'session', None), dict))
        return agent

    def host(self, agent):
        hosts = self.__dict__.setdefault('_hosts', {})
        if id(agent) not in hosts:
            hosts[id(agent)] = (agent, self._new_host(agent))
        return hosts[id(agent)][1]

    def _new_host(self, agent):
        return NS(agent=agent, hist=NS(epoch=self.epoch), desktop_turn=None, st=NS(log=mock.Mock()),
                  stopped=lambda: False, agent_notice=mock.Mock())

    async def check(self, host, peer):
        req = {'t': 'privacy_check', 'gate': 'feedback', 'draft': {'problem': 'the owner asked to send this'}}
        endpoint = mock.Mock(return_value=privacy.Verdict('ok', probability=.01))      # the fake Jev endpoint
        with mock.patch.object(approval_material.sys, 'platform', 'darwin'), \
             mock.patch.object(privacy, 'layer2', endpoint):
            result = await privacy_host.handle(host, req, peer_pid=peer)
        return result, endpoint

    # -- positive -----------------------------------------------------------------------------------------------
    async def test_codex_shared_owner_turn_command_sends(self):
        agent = self.codex_shared()
        h = self.host(agent)
        self.owner_turn(agent)
        peer = self.command()
        result, endpoint = await self.check(h, peer)
        self.assertEqual(result['status'], 'ok', result)
        endpoint.assert_called_once()
        h.agent_notice.assert_not_called()

    async def test_claude_shared_owner_turn_still_sends(self):
        token = proc_row(self.anchor.pid)[2]
        agent = NS(session={'pid': self.anchor.pid, 'procStart': token}, phone_turn=True, phone_seen=True,
                   cur_send=None, cfg={'session_mode': 'shared'})
        h = self.host(agent)
        self.owner_turn(agent)
        result, endpoint = await self.check(h, self.command())
        self.assertEqual(result['status'], 'ok', result)
        endpoint.assert_called_once()

    # -- negatives: unverified sources stay refused -----------------------------------------------------------
    async def assert_refused(self, h, peer, why='peer_unverified'):
        result, endpoint = await self.check(h, peer)
        self.assertEqual(result['reason'], 'privacy-check/' + why, result)
        endpoint.assert_not_called()

    async def test_no_owner_turn_refused(self):
        agent = self.codex_shared()
        peer = self.command()
        await self.assert_refused(self.host(agent), peer)

    async def test_background_command_from_an_earlier_turn_cannot_borrow_the_new_turn(self):
        agent = self.codex_shared()
        old = self.command()                 # started before this owner turn (a kept app-server's background child)
        time.sleep(2.0 / _TICK + .02)
        self.owner_turn(agent)
        await self.assert_refused(self.host(agent), old)

    async def test_unrelated_same_uid_process_refused(self):
        agent = self.codex_shared()
        self.owner_turn(agent)
        stranger = self.spawn(['sleep', '30']).pid      # same uid, started after the turn, not under the harness
        await self.assert_refused(self.host(agent), stranger)

    async def test_anchor_must_be_hosts_own_live_child(self):
        agent = self.codex_shared()
        self.owner_turn(agent)
        peer = self.command()
        anchor = self.anchor.pid
        reparented = lambda pid: (lambda r: r and ((1,) + r[1:] if pid == anchor else r))(proc_row(pid))
        with mock.patch.object(process_evidence, 'darwin_row', side_effect=reparented):
            await self.assert_refused(self.host(agent), peer)                # anchor is no longer the Host's child
        agent.proc = NS(pid=self.anchor.pid, returncode=0)                   # Host already reaped / saw it exit
        await self.assert_refused(self.host(agent), peer)

    async def test_forged_or_stale_origin_refused(self):
        agent = self.codex_shared()
        self.owner_turn(agent, origin={**self.origin, 'session': '6'})       # another history epoch
        await self.assert_refused(self.host(agent), self.command())
        self.owner_turn(agent, origin={k: v for k, v in self.origin.items() if k not in ('message_id', 'root_message_id')})
        await self.assert_refused(self.host(agent), self.command())

    async def test_friend_turn_is_source_denied(self):
        agent = self.codex_shared()
        self.owner_turn(agent, origin={**self.origin, 'verified': False, 'suspicious': True, 'channel': 'friend_task'})
        await self.assert_refused(self.host(agent), self.command(), why='source_denied')

    async def test_turn_ended_scope_is_not_live(self):
        agent = self.codex_shared()
        h = self.host(agent)
        self.owner_turn(agent)
        peer = self.command()
        with mock.patch.object(approval_material.sys, 'platform', 'darwin'):
            scope = approval_material._direct_scope(h, peer)
            self.assertIsNotNone(scope)
            agent.cur_send = None                                            # turn end clears cur_send
            self.assertFalse(approval_material._scope_live(h, scope, peer))
            self.assertIsNone(approval_material.verified_caller_origin(h, peer))


if __name__ == '__main__':
    unittest.main()
