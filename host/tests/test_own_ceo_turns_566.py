"""#566: a live host-verified busy CEO may report from prompting mode; names never grant authority."""
import _hermetic  # noqa: F401
import os
import subprocess
import unittest

from agentj import busy_launch, peer_origin, provenance
from test_p455_same_host import SameHost as _SameHost
from test_p455_security import pinned


class OwnCEO566(_SameHost):
    def live_ceo(self, *, trusted=True, mode='prompting'):
        # Real process/start/namespace/UID, not model-provided process metadata.
        proc = subprocess.Popen(['/usr/bin/sleep', '30'])
        self.addCleanup(lambda: (proc.terminate(), proc.wait()) if proc.poll() is None else None)
        table = busy_launch.procs()
        row = table[proc.pid]
        it = {'key': 'own-ceo-566', 'pid': proc.pid, 'proc_start': row[2], '_ctime': row[3],
              'who': 'CEO', 'label': 'own-ceo-test', 'report': ''}
        if trusted:
            self.assertTrue(self.host.busy.launch.accept_verified(it, table))
        else:
            self.host.busy.launch.turn(False)
            self.host.busy.launch.judge(it, {}, table, set())
            self.assertFalse(self.host.busy.launch.ok(it['key']))
        receiver = busy_launch.procs()[os.getpid()]
        self.native.session.update(pid=os.getpid(), procStart=receiver[2],
                                   pidDomain='host:pid:' + peer_origin._ns())
        # Restore the actual kernel seam inherited setup replaces for old #455 tests.
        self.host.peer_launch.verify = peer_origin.Launches.verify.__get__(self.host.peer_launch)
        return proc, {'kind': 'peer', 'from': 'uds:isolated-test', 'fromMode': mode,
                      'verifiedPeerPid': proc.pid, 'verifiedPeerProcStart': row[2]}

    def test_prompting_own_busy_report_can_stop_write_and_relaunch(self):
        self.owner_launch()
        proc, origin = self.live_ceo()
        self.away()
        self.peer_message(origin)
        self.assertEqual(provenance.unattended_turn(self.native), 'same_host')
        for command, tool in ((pinned('systemctl --user stop agentj-p455-ceo'), 'Bash'),
                              ({'file_path': str(self.work / 'PROMPT-566.md'), 'content': 'Continue incident diagnosis.'}, 'Write'),
                              (pinned('systemd-run --user --collect --unit=agentj-p566-ceo claude -p "read PROMPT-566.md"'), 'Bash')):
            with self.subTest(tool=tool):
                out, _ = self.permission(command, tool)
                self.assertEqual(out['behavior'], 'allow', out)
        self.assertIn('agentj-p566-ceo.service', self.host.peer_launch.units)
        self.assertFalse(self.cards())
        self.assertIsNone(proc.poll())

    def test_untrusted_busy_prompting_and_claimed_name_stay_unknown(self):
        _, origin = self.live_ceo(trusted=False)
        self.peer_message(origin, name='my-own-dispatched-CEO')
        self.refused()
        out, _ = self.permission('cat ~/.ssh/id_rsa')
        self.assertEqual(out['behavior'], 'deny')
        self.assertFalse(self.cards())

    def test_busy_report_wrong_start_hop_and_unproved_mode_stay_unknown(self):
        _, origin = self.live_ceo()
        for changed in ({'verifiedPeerProcStart': '1'}, {'hopChain': []}, {'fromMode': 'other'}):
            self.native.peer_low = False
            self.native.native_turn = False
            self.native.native_source = provenance.normalize(None)
            self.peer_message({**origin, **changed})
            self.refused()

    def test_verified_busy_does_not_launder_active_foreign_peer(self):
        _, origin = self.live_ceo()
        self.peer_message({**origin, 'verifiedPeerProcStart': '1'})
        self.peer_message(origin)
        self.refused()

    def test_busy_report_never_becomes_owner_or_approves_uv(self):
        _, origin = self.live_ceo()
        self.peer_message(origin)
        self.assertEqual(provenance.unattended_turn(self.native), 'same_host')
        self.assertFalse(provenance.is_owner(provenance.turn_source(self.native)))
        self.away()
        for command in ('cat ~/.ssh/id_rsa', 'stripe charges create --amount 500',
                        'rm -rf ~/Documents', 'printf DATA > ~/.claude/settings.json'):
            with self.subTest(command=command):
                out, _ = self.permission(command)
                self.assertEqual(out['behavior'], 'deny', out)

    def test_dead_pinned_sender_cannot_report(self):
        proc, origin = self.live_ceo()
        proc.terminate()
        proc.wait()
        self.peer_message(origin)
        self.refused()


# Imported legacy class is only a fixture, not a duplicate standalone suite.
del _SameHost

if __name__ == '__main__':
    unittest.main()
