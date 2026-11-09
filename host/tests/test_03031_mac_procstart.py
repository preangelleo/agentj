"""#03031 (0.30.3 hotfix): a Mac owner session was judged "not the owner".

Claude Code (2.1.317) on macOS writes ~/.claude/sessions/<pid>.json `procStart` as `ps -o lstart` under TZ=UTC, while
process_evidence.darwin_row renders the kernel start second in local time. On a Mac in Asia/Tokyo the two never matched,
so application_ingress._owner_tree was always False (plaza application_ingress_unverified, privacy peer_unverified).
Checked on a real Mac 2026-11-09. These tests run on Linux with the darwin branches mocked.
"""
import _hermetic  # noqa: F401
import asyncio
import calendar
import contextlib
import os
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from agentj import application_ingress as AI, approval_material as AM
from agentj.core import order_host as OH, orders, process_evidence as PE

EPOCH = calendar.timegm((2026, 11, 3, 13, 50, 34, 0, 0, 0))
UTC = 'Tue Nov  3 13:50:34 2026'          # what Claude Code wrote (TZ=UTC lstart)
TOKYO = 'Tue Nov  3 22:50:34 2026'        # darwin_row on an Asia/Tokyo Mac, same second
TWICE = 'Tue Nov  3 04:50:34 2026'        # the UTC string mistaken for local time once more (-9 h)
ROW = (1, EPOCH + 0.25, TOKYO)            # (parent, exact start, local lstart token)


@contextlib.contextmanager
def darwin():
    with mock.patch.object(PE.sys, 'platform', 'darwin'):
        yield


@contextlib.contextmanager
def tz(name):
    old = os.environ.get('TZ')
    os.environ['TZ'] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()


class Samples(unittest.TestCase):
    def test_samples_are_one_second_two_renderings(self):
        self.assertEqual(time.strftime(PE._LSTART, time.gmtime(EPOCH)), UTC)
        with tz('Asia/Tokyo'):
            self.assertEqual(time.strftime(PE._LSTART, time.localtime(EPOCH)), TOKYO)


class StartMatches(unittest.TestCase):
    def test_darwin_utc_and_local_true(self):
        with darwin():
            self.assertTrue(PE.start_matches(UTC, ROW))
            self.assertTrue(PE.start_matches(TOKYO, ROW))

    def test_darwin_other_times_false(self):
        with darwin():
            self.assertFalse(PE.start_matches('Tue Nov  3 13:50:35 2026', ROW))     # one second later
            self.assertFalse(PE.start_matches('Tue Nov  3 13:50:33 2026', ROW))     # one second earlier
            self.assertFalse(PE.start_matches(TWICE, ROW))
            self.assertFalse(PE.start_matches('2026-11-03T13:50:34Z', ROW))         # no other format
            self.assertFalse(PE.start_matches(UTC.lower(), ROW))
            self.assertFalse(PE.start_matches(UTC + ' ', ROW))

    def test_empty_none_non_string_false(self):
        with darwin():
            for bad in (None, '', 0, EPOCH, b'x', [UTC]):
                self.assertFalse(PE.start_matches(bad, ROW), bad)
            self.assertFalse(PE.start_matches(UTC, None))
            self.assertFalse(PE.start_matches(UTC, ()))
            self.assertFalse(PE.start_matches(UTC, (1, 'x', TOKYO)))

    def test_linux_stays_exact_token(self):
        with mock.patch.object(PE.sys, 'platform', 'linux'):
            self.assertTrue(PE.start_matches('123456', (1, 1234.56, '123456')))
            self.assertFalse(PE.start_matches('123457', (1, 1234.56, '123456')))
            self.assertFalse(PE.start_matches(UTC, ROW))           # no UTC rendering off macOS
            self.assertTrue(PE.start_matches(TOKYO, ROW))
            self.assertFalse(PE.start_matches(123456, (1, 1234.56, '123456')))


def host():
    return NS(agent=None, hist=NS(epoch=1), _nonowner_spans=[], _nonowner_floor=0.0)


class OwnerTree(unittest.TestCase):
    """_owner_tree on a mocked darwin: Claude session 500 (procStart = UTC string) -> 600 -> caller 700."""

    def run_tree(self, rows, start=UTC):
        agent = NS(session={'pid': 500, 'procStart': start}, cur_send=None, untrusted_foreign=False)
        with darwin(), mock.patch.object(PE, 'darwin_row', lambda pid: rows.get(pid)), \
                mock.patch.object(AI, '_owner_turn', lambda a: True):
            return AI._owner_tree(host(), 700, agent)

    def rows(self, session_row=ROW):
        return {500: session_row, 600: (500, EPOCH + 50.0, 'M'), 700: (600, EPOCH + 60.0, 'P'),
                os.getpid(): (1, EPOCH - 10.0, 'S')}

    def test_utc_procstart_is_now_the_owner(self):
        self.assertTrue(self.run_tree(self.rows()))
        self.assertTrue(self.run_tree(self.rows(), start=TOKYO))

    def test_old_comparison_was_false(self):
        rows = self.rows()
        self.assertNotEqual(str(rows[500][2]), UTC)              # the 0.30.3 check: row token vs procStart

    def test_pid_reuse_with_other_start_refused(self):
        later = EPOCH + 3600
        with tz('Asia/Tokyo'):
            reused = (1, later + 0.5, time.strftime(PE._LSTART, time.localtime(later)))
        self.assertFalse(self.run_tree(self.rows(reused)))
        self.assertFalse(self.run_tree(self.rows(), start=TWICE))
        self.assertFalse(self.run_tree(self.rows(), start=None))
        self.assertFalse(self.run_tree({k: v for k, v in self.rows().items() if k != 500}))


class DarwinScope(unittest.TestCase):
    """approval_material._darwin_scope uses the same predicate for the session anchor."""

    def scope(self, start):
        send = NS(approval_origin={'session': '1', 'message_id': 'm1'}, created=time.monotonic() - 5)
        agent = NS(session={'pid': 500, 'procStart': start}, cur_send=send, phone_turn=True, phone_seen=True)
        h = NS(agent=agent, hist=NS(epoch=1), desktop_turn=None)
        now = time.time()
        rows = {500: ROW, 700: (500, now - 1.0, 'P')}
        with darwin(), mock.patch.object(PE, 'darwin_row', lambda pid: rows.get(pid)):
            return AM._darwin_scope(h, 700)

    def test_utc_and_local_accepted_others_refused(self):
        self.assertEqual(self.scope(UTC)['anchor'], (500, ROW))
        self.assertIsNotNone(self.scope(TOKYO))
        self.assertIsNone(self.scope(TWICE))
        self.assertIsNone(self.scope(None))


class OrderHost(unittest.TestCase):
    """core.order_host.authority: on darwin the Claude session start is proven on the kernel row, then the live
    process's own `ps -o lstart` token (this serve's TZ) is what descendant() compares."""

    def authority(self, start, rows, token='LOCAL-PS'):
        ag = NS(kind='claude', cfg={'dir': '/x'}, session={'pid': 500, 'procStart': start, 'sessionId': 'sid'},
                stop_flag=False, halting=False)
        h = NS(agent=ag, stopped=lambda: False, st=NS(agent_session=lambda kind: 'sid'))
        seen = []
        P = OH.provenance
        with darwin(), mock.patch.object(OH, 'enabled', lambda: True), \
                mock.patch.object(P, 'turn_source', lambda a: {}), mock.patch.object(P, 'unattended_turn', lambda a: None), \
                mock.patch.object(P, 'is_owner', lambda s: True), mock.patch.object(P, 'untrusted_turn', lambda a: False), \
                mock.patch.object(P, 'delegated_turn', lambda a: False), \
                mock.patch.object(PE, 'row', lambda pid: rows.get(pid)), \
                mock.patch.object(PE.codex_procs, 'start_token', lambda pid: token), \
                mock.patch.object(PE, 'descendant', lambda p, a, t: seen.append((p, a, t)) or True):
            OH.authority(h, peer=700)
        return seen

    def test_utc_procstart_pins_live_token(self):
        self.assertEqual(self.authority(UTC, {500: ROW}), [(700, 500, 'LOCAL-PS')])

    def test_wrong_start_or_gone_refused(self):
        for start, rows, token in ((TWICE, {500: ROW}, 'LOCAL-PS'), (None, {500: ROW}, 'LOCAL-PS'),
                                   (UTC, {}, 'LOCAL-PS'), (UTC, {500: ROW}, None)):
            with self.assertRaises(orders.OrderError):
                self.authority(start, rows, token)

    def test_row_changes_between_reads_refused(self):
        """security F3: the re-read of row(anchor) is the TOCTOU guard — same second, different microseconds = refused."""
        class Seq:
            def __init__(self): self.n = 0
            def get(self, pid):
                self.n += 1
                return ROW if self.n == 1 else (1, EPOCH + 0.75, TOKYO)
        with self.assertRaises(orders.OrderError):
            self.authority(UTC, Seq())

    def test_linux_path_unchanged(self):
        ag = NS(kind='claude', cfg={'dir': '/x'}, session={'pid': 500, 'procStart': '123', 'sessionId': 'sid'},
                stop_flag=False, halting=False)
        h = NS(agent=ag, stopped=lambda: False, st=NS(agent_session=lambda kind: 'sid'))
        seen = []
        P = OH.provenance
        with mock.patch.object(PE.sys, 'platform', 'linux'), mock.patch.object(OH, 'enabled', lambda: True), \
                mock.patch.object(P, 'turn_source', lambda a: {}), mock.patch.object(P, 'unattended_turn', lambda a: None), \
                mock.patch.object(P, 'is_owner', lambda s: True), mock.patch.object(P, 'untrusted_turn', lambda a: False), \
                mock.patch.object(P, 'delegated_turn', lambda a: False), \
                mock.patch.object(PE, 'descendant', lambda p, a, t: seen.append((p, a, t)) or True):
            OH.authority(h, peer=700)
        self.assertEqual(seen, [(700, 500, '123')])


if __name__ == '__main__':
    unittest.main()
