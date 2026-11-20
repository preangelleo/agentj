import shutil
import socket
import tempfile
from pathlib import Path
import subprocess
from unittest import TestCase, mock
from urllib.error import HTTPError, URLError

from agentj import update, upgrade_diag, upgrade_wheels as wheels
from test_0334_upgrade_retry import _Env


class PreparationTests(TestCase):
    def test_resolver_categories_are_closed(self):
        cases = (
            ('No solution found when resolving dependencies', 'dependency_conflict', False),
            ('unexpected argument --format', 'installer_incompatible', False),
            ('Failed to fetch https://private.invalid/x?token=secret: connection reset by peer', 'dependency_download_failed', True),
            ('Failed to fetch: 403 Forbidden', 'dependency_download_failed', False),
            ('Failed to download: invalid peer certificate', 'dependency_download_failed', False),
            ('No matching distribution found for package', 'dependency_conflict', False),
            ('unknown parser failure', 'uv_failed', False),
            ('Hash mismatch for wheel', 'install_artifact_changed', False),
        )
        for message, reason, transient in cases:
            with self.subTest(reason=reason):
                failure = wheels.resolver_failure(message)
                self.assertEqual((str(failure), failure.transient), (reason, transient))
                self.assertNotIn('secret', repr(failure))
        self.assertFalse(wheels.download_failure(HTTPError('private', 404, '', {}, None)).transient)
        self.assertTrue(wheels.download_failure(HTTPError('private', 503, '', {}, None)).transient)
        self.assertTrue(wheels.download_failure(URLError(socket.gaierror(socket.EAI_AGAIN, 'private'))).transient)

    def test_preparation_and_install_share_retry_count(self):
        faults = [wheels.resolver_failure('Failed to download: connection reset by peer'), 'ok']
        def prepare(cmd, pin, version, *, retry):
            def one(timeout):
                result = faults.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            retry.call(one, 90)
            return cmd
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        out = {}
        with mock.patch.object(update, '_pin_plan_wheel', return_value=(['uv'], None)), \
             mock.patch.object(wheels, 'prepare_update', side_effect=prepare), \
             mock.patch.object(update, '_install_sleep') as sleep:
            rc, _, _ = update.install_step(run, ['uv'], lambda _: None, out, '1.0.0', object(), sealed=True)
        self.assertEqual((rc, out['retry_count'], run.call_count, sleep.call_count), (0, 1, 1, 1))

    def test_no_solution_never_installs_or_retries(self):
        with mock.patch.object(update, '_pin_plan_wheel', return_value=(['uv'], None)), \
             mock.patch.object(wheels, 'prepare_update', side_effect=wheels.resolver_failure('no solution found when resolving')), \
             mock.patch.object(update, '_install_sleep') as sleep:
            run = mock.Mock()
            rc, _, fields = update.install_step(run, ['uv'], lambda _: None, {}, '1.0.0', object(), sealed=True)
        self.assertEqual((rc, fields['reason'], fields['retry_count'], fields['_untouched']), (1, 'dependency_conflict', 0, True))
        run.assert_not_called()
        sleep.assert_not_called()

    def test_missing_resolver_and_offline_no_solution_are_distinct(self):
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaisesRegex(wheels.PreparationFailure, 'installer_missing'):
                wheels.run_resolver([str(Path(scratch) / 'missing-uv')], scratch, 5)
            uv = shutil.which('uv')
            if not uv:
                self.skipTest('uv unavailable')
            cmd = [uv, 'pip', 'install', '--offline', '--no-index', '--no-cache', '--no-config',
                   '--no-python-downloads', '--target', str(Path(scratch) / 'target'), 'no-possible-wheel==1']
            with self.assertRaisesRegex(wheels.PreparationFailure, 'dependency_conflict'):
                wheels.run_resolver(cmd, scratch, 15)
            with self.assertRaisesRegex(wheels.PreparationFailure, 'install_artifact_unavailable'):
                wheels.run_resolver(cmd, scratch, 15, offline=True)

    def test_deadline_after_backoff_does_not_count_a_retry_that_never_ran(self):
        timer = [0]
        retry = wheels.PreparationRetry(lambda:timer[0], lambda _:timer.__setitem__(0, 1000))
        operation = mock.Mock(side_effect=wheels.resolver_failure('connection reset by peer'))
        with self.assertRaisesRegex(wheels.PreparationFailure, 'dependency_download_failed'):
            retry.call(operation, 90)
        self.assertEqual((retry.count, operation.call_count), (0, 1))


class NightlyPreparation(_Env):
    def test_nightly_no_install_rollback_or_raw_output_on_preparation_failure(self):
        failure = wheels.resolver_failure('unexpected argument --format https://private.invalid/token=secret')
        with mock.patch.object(wheels, 'prepare_update', side_effect=failure):
            result = self.attempt([])
        self.assertEqual(result['reason'], 'installer_incompatible')
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(self.installs, [])
        sent = self.sent[0]
        self.assertEqual((sent['step'], sent['retry_count'], sent['dependency_phase']), ('install', 0, 'resolve'))
        meta = upgrade_diag.metadata_body(sent)
        self.assertNotIn('private.invalid', meta)
        self.assertIn('installer_incompatible', meta)
