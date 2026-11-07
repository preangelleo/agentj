"""Offline signed installation error propagation."""
import unittest
from unittest.mock import AsyncMock, patch
from agentj import setup_ops
# Reuse the existing real Ed25519 signed-request fixture, without replacing controls.
from test_install_assist_host import Boundary

class RunnerFailures(Boundary):
    async def test_signed_install_network_failure_uses_shared_guidance(self):
        for reason, offers in (('network', True), ('checksum', False), ('timeout', False), ('denied', False)):
            with self.subTest(reason=reason):
                self.runner._operation = AsyncMock(side_effect=setup_ops.OpError(reason))
                with patch.object(self.runner, '_current_revision', return_value=1):
                    result = await self.runner.run(self.s, self.request('ai.install', {'cli':'claude', 'source':'official'}))
                self.assertFalse(result['ok'])
                self.assertEqual(result['why'], reason)
                self.assertEqual('help' in result, offers)
                if offers:
                    self.assertEqual(result['failure_category'], 'network')
                    self.assertIn('Kimi Code', result['help'])

import test_install_native as native_fixture

class NativeRunnerNetwork(unittest.IsolatedAsyncioTestCase):
    setUp = native_fixture.Native.setUp
    tearDown = native_fixture.Native.tearDown
    request = native_fixture.Native.request
    native = native_fixture.Native.native

    async def test_native_worker_network_exit_reaches_signed_runner(self):
        self.native()
        with patch('agentj.setup_native.owned_process', AsyncMock(return_value=23)) as process:
            result = await self.runner.run(self.s, self.request('ai.install', {'cli':'claude', 'source':'official'}))
        process.assert_awaited_once()
        self.assertFalse(result['ok'])
        self.assertEqual(result['why'], 'network')
        self.assertEqual(result['failure_category'], 'network')
        self.assertIn('Kimi Code', result['help'])

class DownloadFailures(unittest.TestCase):
    def test_worker_preserves_download_network_and_http_distinction(self):
        import urllib.error
        from agentj import setup_native
        for error, code in ((urllib.error.URLError('offline'), 23),
                            (TimeoutError(), 23),
                            (urllib.error.HTTPError('https://example.invalid', 403, 'Forbidden', {}, None), 1),
                            (ValueError('checksum'), 21)):
            with self.subTest(code=code), patch('agentj.binaries.resolve', return_value={'path':None}), \
                 patch('agentj.setup_packages.install', side_effect=error), patch('agentj.setup_env.env_edit') as edit:
                self.assertEqual(setup_native.worker(['install', '/unused', '/unused', 'claude', 'official']), code)
                edit.assert_not_called()

    def test_real_archive_download_failure_retains_network_type(self):
        import io
        import urllib.error
        from types import SimpleNamespace
        from agentj import setup_packages
        with patch('agentj.setup_packages.platform.system', return_value='Darwin'), \
             patch('agentj.setup_packages.platform.machine', return_value='arm64'), \
             patch('agentj.setup_packages.shutil.which', return_value=None), \
             patch('agentj.setup_packages.urllib.request.urlopen', side_effect=urllib.error.URLError('offline')) as download, \
             patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaisesRegex(ValueError, '^network$'):
                setup_packages.install('opencode', 'official', '/work/qa/provider-followup/unused', '/work/qa/provider-followup/tmp')
        download.assert_called_once()
