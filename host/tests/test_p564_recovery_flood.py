"""#564: bounded capacity audit, deferred backlog scans and transient upgrade health."""
import _hermetic  # noqa: F401
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import urllib.error

from agentj import background_done, completion_recovery as cr, doctor, update


class Capacity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.host = NS(st=NS(root=Path(self.tmp.name), log=mock.Mock()), agent_cfg={}, stopped=lambda: True)
        self.book = cr.Book(self.host)
        self.row = {'task_id': 'run:finished', 'source': 'unknown', 'recovery_point': 'reconcile_only'}
        with mock.patch.object(self.book, 'save'):
            for n in range(cr.MAX):
                self.book.add(f'{n:064x}', self.row)
        self.host.st.log.reset_mock()

    def test_21000_rejections_produce_bounded_aggregate(self):
        with mock.patch('agentj.completion_recovery.time.monotonic', return_value=1):
            for n in range(21000):
                self.book.add(f'{n + cr.MAX:064x}', self.row)
        self.assertEqual(self.host.st.log.call_count, 1)
        self.assertEqual(len(self.book.rows), cr.MAX)
        with mock.patch('agentj.completion_recovery.time.monotonic', return_value=62):
            self.book.add('f' * 64, self.row)
        self.assertEqual(self.host.st.log.call_count, 2)
        self.assertEqual(self.host.st.log.call_args.kwargs['count'], 21000)

    def test_capacity_transition_rearms_and_never_evicts_unresolved(self):
        self.book.add('e' * 64, self.row)
        self.book.rows['0' * 64]['state'] = 'reconciled'
        self.book.add('d' * 64, self.row)
        self.assertIn('d' * 64, self.book.rows)
        self.book.add('c' * 64, self.row)
        self.assertEqual([c.kwargs['result'] for c in self.host.st.log.call_args_list],
                         ['awaiting_owner_verification', 'available', 'awaiting_owner_verification'])

    def test_full_backlog_does_not_retry_each_busy_poll(self):
        n = background_done.Notifier(self.host)
        n.recovery = self.book
        n.pending = {f'{x + cr.MAX:064x}': {'foreign': True} for x in range(36)}
        n.collect = mock.Mock()
        n.ready = mock.Mock(return_value=False)
        with mock.patch.object(self.book, 'capture', wraps=self.book.capture) as capture, \
                mock.patch('agentj.completion_recovery.time.monotonic', return_value=1):
            for _ in range(600):
                asyncio.run(n.prepare())
                n.tick()
            self.assertEqual(capture.call_count, 1)
            self.book.rows['0' * 64]['state'] = 'reconciled'
            asyncio.run(n.prepare())
            self.assertIn(f'{cr.MAX:064x}', self.book.rows)

    def test_full_capture_does_not_read_reports(self):
        with mock.patch.object(self.book, 'read') as read:
            self.book.capture('e' * 64, {'report_path': 'report.md'})
        read.assert_not_called()

    def test_full_backlog_retains_new_arrivals_and_retries_next_window(self):
        n = background_done.Notifier(self.host)
        n.recovery = self.book
        n.pending = {'e' * 64: {'foreign': True}}
        n.collect = mock.Mock()
        with mock.patch.object(self.book, 'capture', wraps=self.book.capture) as capture:
            with mock.patch('agentj.completion_recovery.time.monotonic', return_value=0):
                asyncio.run(n.prepare())
                n.pending['d' * 64] = {'foreign': True}
                asyncio.run(n.prepare())
            self.assertEqual(capture.call_count, 1)
            self.assertEqual(len(n.pending), 2)
            with mock.patch('agentj.completion_recovery.time.monotonic', return_value=61):
                asyncio.run(n.prepare())
            self.assertEqual(capture.call_count, 2)
            self.assertEqual(len(n.pending), 2)

    def test_restart_rearms_one_capacity_event_without_changing_rows(self):
        self.book.save()
        reloaded = cr.Book(self.host)
        before = reloaded.rows.copy()
        reloaded.add('e' * 64, self.row)
        reloaded.add('d' * 64, self.row)
        self.assertEqual(self.host.st.log.call_count, 1)
        self.assertEqual(before, reloaded.rows)


class Upgrade(unittest.TestCase):
    def dashboard(self, responses):
        with mock.patch('agentj.doctor.cloud.api_url', return_value='https://api.example.test'), \
                mock.patch('agentj.doctor.cloud.app_url', return_value='https://account.example.test'), \
                mock.patch('agentj.doctor._http', side_effect=responses) as http:
            row = doctor.check_dashboard(NS())
        return row, http

    def test_api_5xx_retried_once_then_transient(self):
        row, http = self.dashboard([503, 503])
        self.assertEqual(http.call_count, 2)
        self.assertEqual(row['status'], 'warn')
        self.assertTrue(row['server_unavailable'])
        self.assertEqual(row['hint'], '')
        self.assertEqual(update.upgrade_blockers([row]), ([], []))
        for lang in ('zh', 'en'):
            text = update.health_text(update.doctor_counts([row]), lang, [row], verified_upgrade=True)
            self.assertIn('暂时不可用' if lang == 'zh' else 'temporarily unavailable', text)
            self.assertNotIn('修复' if lang == 'zh' else 'Repair', text)

    def test_timeout_then_success(self):
        row, http = self.dashboard([TimeoutError(), 400, 200])
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(http.call_count, 3)

    def test_timeout_twice_is_not_account_failure(self):
        row, http = self.dashboard([urllib.error.URLError(TimeoutError()), TimeoutError()])
        self.assertEqual(http.call_count, 2)
        self.assertTrue(row['server_unavailable'])
        self.assertEqual(row['status'], 'warn')

    def test_real_unexpected_response_is_not_hidden(self):
        row, http = self.dashboard([403])
        self.assertEqual(http.call_count, 1)
        self.assertEqual(row['status'], 'fail')
        self.assertFalse(row.get('server_unavailable', False))

    def test_account_frontend_5xx_retried(self):
        row, http = self.dashboard([400, 502, 200])
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(http.call_count, 3)

    def test_other_local_failure_still_requires_repair(self):
        row, _ = self.dashboard([503, 503])
        rows = [row, {'id': 'state', 'status': 'fail'}]
        text = update.health_text(update.doctor_counts(rows), 'zh', rows, verified_upgrade=True)
        self.assertIn('一键修复', text)

    def test_transient_plain_receipt_does_not_suggest_repair(self):
        row, _ = self.dashboard([503, 503])
        for lang in ('zh', 'en'):
            text = update.health_text(update.doctor_counts([row]), lang, [row])
            self.assertNotIn('修复' if lang == 'zh' else 'Repair', text)

    def test_disconnected_session_keeps_repair_guidance(self):
        row, _ = self.dashboard([503, 503])
        rows = [row, {'id': 'main-inject', 'status': 'warn', 'session_disconnected': True}]
        text = update.health_text(update.doctor_counts(rows), 'zh', rows, verified_upgrade=True)
        self.assertIn('一键修复', text)


class RelayPhase(unittest.TestCase):
    def test_revocation_failure_after_auth_stays_fail_closed_and_is_distinct(self):
        from agentj import serve
        async def go():
            stop = asyncio.Event()
            logged = []
            def log(ev, **meta):
                logged.append((ev, meta))
                stop.set()
            host = NS(cfg={'relay':'wss://relay.example.test'}, channel='synthetic', identity_ok=True,
                      stopping=stop, seat=NS(cert=lambda:None), _auth=mock.AsyncMock(),
                      credential_revocations=NS(sync=mock.AsyncMock(side_effect=ValueError('revocation_sync'))),
                      relay_up=False, relay_last=None, st=NS(log=log), _drop_all=mock.AsyncMock())
            ctx = mock.MagicMock()
            ctx.__aenter__ = mock.AsyncMock(return_value=NS())
            ctx.__aexit__ = mock.AsyncMock(return_value=False)
            with mock.patch.object(serve, 'connect', return_value=ctx):
                await serve.Host.relay_loop(host)
            self.assertFalse(host.relay_up)
            self.assertEqual(logged[0][1]['phase'], 'revocations')
            self.assertEqual(logged[0][1]['why'], 'revocation_sync')
            host._drop_all.assert_awaited_once_with('relay_down')
        asyncio.run(go())

    def test_actual_revocation_5xx_raises_the_historical_valueerror(self):
        import contextlib
        from agentj import credential_revoke
        async def go():
            with tempfile.TemporaryDirectory() as root:
                st = NS(root=Path(root), devices_lock=contextlib.nullcontext,
                        write_private=lambda path, data: path.write_bytes(data))
                revocations = credential_revoke.Revocations(NS(st=st))
                with mock.patch('agentj.credential_revoke.cloud.read_cloud', return_value={'bound': True}), \
                        mock.patch('agentj.reporter.in_daemon_thread', new=mock.AsyncMock(return_value=(503, {}))):
                    with self.assertRaisesRegex(ValueError, '^revocation_sync$'):
                        await revocations.sync()
        asyncio.run(go())
