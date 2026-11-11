"""0.30.7 approval round 2 — F2 on a REAL process tree (no mocks of core.process_evidence).

automation_grant.hook_caller(host, peer) is True only for a process that descends from the live Agent session process
with every hop born inside the currently open non-owner span. Earlier tests mocked process_evidence.row; here a real
"session" process (stand-in for the Agent harness) starts a pre-span job, then — after the span opened — a child that
starts a grandchild. Asserted with the real /proc rows:
  descendant born after the span → True (child and grandchild);  unrelated same-user process born after the span → False;
  the session's job born before the span floor → False;  a closed span → False;  dead anchor → False.
Every process runs in its own session/process group and is killed + reaped in cleanup. Linux only (/proc rows).
"""
import _hermetic  # noqa: F401,I001
import os
import signal
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace

from agentj import application_ingress, automation_grant
from agentj.core import process_evidence as pe

# The stand-in Agent session: a pre-span job (P), wait for "go", then child C → grandchild G.
SESSION = ('sleep 30 & echo P $!; read go; '
           'sh -c \'sleep 30 & echo G $!; wait\' & echo C $!; wait')


@unittest.skipUnless(sys.platform.startswith('linux'), 'linux /proc process rows')
class RealTree(unittest.TestCase):
    def spawn(self, argv, **kw):
        p = subprocess.Popen(argv, start_new_session=True, **kw)

        def reap():
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for f in (p.stdin, p.stdout):
                if f:
                    f.close()
            p.wait(5)
        self.addCleanup(reap)
        return p

    def read_pids(self, proc, want):
        got = {}
        deadline = time.monotonic() + 5
        while not want <= set(got) and time.monotonic() < deadline:
            line = proc.stdout.readline().decode().split()
            if len(line) == 2:
                got[line[0]] = int(line[1])
        self.assertLessEqual(want, set(got), 'fixture did not report its pids')
        return got

    def test_hook_caller_on_real_processes(self):
        session = self.spawn(['sh', '-c', SESSION], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        host = SimpleNamespace(agent=SimpleNamespace(session={'pid': session.pid}))
        pre = self.read_pids(session, {'P'})['P']
        self.assertEqual(pe.row(pre)[0], session.pid)

        time.sleep(0.05)                                   # P is clearly older than the span (clock ticks are 10 ms)
        application_ingress.open_span(host)
        time.sleep(application_ingress.SPAN_MARGIN + 0.1)  # born after the margin-narrowed floor
        session.stdin.write(b'go\n'); session.stdin.flush()
        pids = self.read_pids(session, {'C', 'G'})
        child, grandchild = pids['C'], pids['G']
        stranger = self.spawn(['sleep', '30'])             # same user, born inside the span, not under the session

        for _ in range(100):                               # the grandchild's parent must be the child (not yet exec'd)
            if (pe.row(grandchild) or (None,))[0] == child:
                break
            time.sleep(0.01)
        self.assertEqual(pe.row(child)[0], session.pid)
        self.assertEqual(pe.row(grandchild)[0], child)

        self.assertTrue(automation_grant.hook_caller(host, grandchild), 'descendant born after the span')
        self.assertTrue(automation_grant.hook_caller(host, child), 'direct child born after the span')
        self.assertFalse(automation_grant.hook_caller(host, stranger.pid), 'unrelated same-user process')
        self.assertFalse(automation_grant.hook_caller(host, pre), 'session job born before the span floor')
        self.assertFalse(automation_grant.hook_caller(host, session.pid), 'the anchor itself is not a hook process')
        self.assertFalse(automation_grant.hook_caller(host, os.getpid()), 'the test runner (not under the session)')

        application_ingress.close_span(host)
        self.assertFalse(automation_grant.hook_caller(host, grandchild), 'closed span')
        application_ingress.open_span(host)               # a NEW span: the old descendants are older than its floor
        self.assertFalse(automation_grant.hook_caller(host, grandchild), 'born in an earlier span')
        host.__dict__['_nonowner_spans'][-1][0] = 0.0     # (re-widen only to isolate the dead-anchor check below)
        self.assertTrue(automation_grant.hook_caller(host, grandchild))

        os.kill(session.pid, signal.SIGKILL)               # dead anchor: the session process is gone
        session.wait(5)
        self.assertIsNone(pe.row(session.pid))
        self.assertFalse(automation_grant.hook_caller(host, grandchild), 'dead anchor')
        self.assertFalse(automation_grant.hook_caller(host, child), 'dead anchor')


if __name__ == '__main__':
    unittest.main()
