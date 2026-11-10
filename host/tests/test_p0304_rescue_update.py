import _hermetic  # noqa: F401
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import auto_update, reset, update
from agentj.state import State
from _testtmp import TEST_TMP


class RescueUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name))

    def test_cached_only_rejects_unsigned_and_tampered_without_network(self):
        path = self.st.root / 'upgrade-cache' / 'agentj-0.1.0-py3-none-any.whl'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'wheel')
        path.with_suffix('.verified.json').write_text(json.dumps({'sha256': '0' * 64, 'signed': False}))
        self.assertFalse(auto_update.signed_cached_wheel(self.st, '0.1.0'))
        with mock.patch.object(auto_update.minisign, 'verify', return_value='agentj-host/v1 version=0.1.0 sha256=' + '0'*64):
            path.with_suffix('.verified.json').write_text(json.dumps({'sha256': '0'*64, 'signed': True, 'signature': 'sig'}))
            self.assertFalse(auto_update.signed_cached_wheel(self.st, '0.1.0'))

    def test_signed_cached_wheel_validates_comment_and_hash_offline(self):
        import hashlib
        version = '0.1.0'
        data = b'published-wheel'
        path = self.st.root / 'upgrade-cache' / f'agentj-{version}-py3-none-any.whl'
        path.parent.mkdir(parents=True, mode=0o700)
        path.parent.chmod(0o700)
        path.write_bytes(data)
        path.chmod(0o600)
        digest = hashlib.sha256(data).hexdigest()
        path.with_suffix('.verified.json').write_text(json.dumps({'sha256': digest, 'signed': True, 'signature': 'test-signature'}))
        path.with_suffix('.verified.json').chmod(0o600)
        with mock.patch.object(auto_update.minisign, 'verify', return_value=f'agentj-host/v1 version={version} sha256={digest}'):
            self.assertEqual(auto_update.signed_cached_wheel(self.st, version), path)
        with mock.patch.object(auto_update.minisign, 'verify', return_value='wrong-channel'):
            self.assertIsNone(auto_update.signed_cached_wheel(self.st, version))

    def test_rollback_refuses_unsigned_previous_without_install(self):
        with mock.patch.object(reset, 'previous_wheel', return_value=('0.1.0', pathlib.Path(self.tmp.name)/'old.whl')):
            with mock.patch.object(update, 'roll_back') as install:
                self.assertEqual(reset.run_rollback(self.st)['why'], 'unavailable')
                install.assert_not_called()

    def test_reinstall_rebuilds_only_existing_service_and_reports_failure(self):
        with mock.patch.object(update, 'verified_plan', return_value=[['installer']]), \
                mock.patch.object(update, '_pre_snapshot'), mock.patch.object(update, 'writable', return_value=True):
            runner = mock.Mock(side_effect=[SimpleNamespace(returncode=0),
                                            SimpleNamespace(returncode=0, stdout=update.__version__, stderr=''),
                                            SimpleNamespace(returncode=78)])
            result = update.repair_install(self.st, runner=runner, service_installed=True, plan_fn=update.verified_plan,
                                           info={'kind': 'uv', 'where': self.tmp.name})
        self.assertEqual(result['reason'], 'service_restart_failed')
        self.assertEqual(result['service'], 'failed')
        self.assertIn('--deferred', runner.call_args_list[-1].args[0])

    def test_reinstall_uses_signed_plan_not_version_check(self):
        with mock.patch.object(update, 'check', side_effect=AssertionError('no latest check')):
            with mock.patch.object(update, 'verified_plan', return_value=[['safe-installer']]) as plan:
                runner = mock.Mock(side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=0, stdout=update.__version__, stderr='')])
                result = update.repair_install(self.st, runner=runner, service_installed=False, plan_fn=update.verified_plan, info={"kind": "uv", "where": self.tmp.name})
        self.assertEqual(result['reason'], 'reinstalled')
        plan.assert_called_once()
        self.assertEqual(runner.call_count, 2)

class SchedulerPeer(unittest.TestCase):
    def test_root_peer_guard_fails_closed_on_missing_and_non_root(self):
        import socket
        import struct
        from agentj import native_scheduler_control as native
        sock = mock.Mock()
        writer = mock.Mock()
        writer.get_extra_info.return_value = sock
        sock.getsockopt.return_value = struct.pack('3i', 123, 1000, 1000)
        self.assertFalse(native._root_helper_peer(writer))
        sock.getsockopt.return_value = struct.pack('3i', 123, 0, 0)
        self.assertTrue(native._root_helper_peer(writer))
        writer.get_extra_info.return_value = None
        self.assertFalse(native._root_helper_peer(writer))

class DeepRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st = State(pathlib.Path(self.tmp.name))

    def test_watchdog_handoff_binds_device_and_rolls_back_only_if_signed(self):
        from agentj import rescue_watchdog, rescue_repair
        wd = object.__new__(rescue_watchdog.Watchdog)
        wd.st = self.st
        emitted = []
        emit = lambda *args: emitted.append(args)
        with mock.patch.object(update, 'repair_install', return_value={'result': 'ok', 'service': 'restart_scheduled'}):
            res = wd.do('deep_repair', 'en', emit, '1' * 16, 'paired-device')
        self.assertEqual((res['pending'], res['cont']), (True, 'deep_repair_cont'))
        marker = json.loads((self.st.root / rescue_repair.MARKER).read_text())
        self.assertEqual(marker['device'], 'paired-device')
        self.assertEqual(marker['flow'], 'deep_repair')
        with mock.patch.object(update, 'repair_install', return_value={'result': 'failed'}), \
                mock.patch.object(reset, 'previous_wheel', return_value=None):
            fail = wd.do('deep_repair', 'en', emit, '2' * 16, 'paired-device')
        self.assertIs(fail['rollback'], False)

class DeepContinuation(unittest.IsolatedAsyncioTestCase):
    async def test_serve_continuation_restarts_owner_harness_and_requires_reply(self):
        from agentj import reset as module, rescue_repair, phone_restart
        from unittest.mock import AsyncMock
        desk = object.__new__(module.Desk)
        desk.h = SimpleNamespace(st=SimpleNamespace(log=mock.Mock()))
        desk._probe_step = AsyncMock(return_value={'ok': True})
        emit = AsyncMock()
        finish = AsyncMock()
        with mock.patch.object(phone_restart, 'run', new_callable=AsyncMock, return_value={'ok': True}), \
                mock.patch.object(rescue_repair, 'probe_line', return_value='answered'):
            await desk._deep_finish(emit, finish, 'en')
        self.assertTrue(finish.call_args.args[0]['ok'])
        desk._probe_step.return_value = {'ok': False, 'why': 'no_reply'}
        with mock.patch.object(phone_restart, 'run', new_callable=AsyncMock, return_value={'ok': True}), \
                mock.patch.object(rescue_repair, 'probe_line', return_value='no reply'), \
                mock.patch.object(module, 'previous_wheel', return_value=None):
            await desk._deep_finish(emit, finish, 'en')
        self.assertFalse(finish.call_args.args[0]['ok'])
        self.assertFalse(finish.call_args.args[0]['rollback'])

class RollbackRace(unittest.TestCase):
    def test_signed_install_uses_sealed_verified_bytes_when_cache_swapped(self):
        import hashlib
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name))
        path = st.root / 'upgrade-cache' / 'agentj-0.1.0-py3-none-any.whl'
        path.parent.mkdir(parents=True, mode=0o700)
        path.parent.chmod(0o700)
        original = b'authenticated-wheel'
        path.write_bytes(original)
        path.chmod(0o600)
        digest = hashlib.sha256(original).hexdigest()
        path.with_suffix('.verified.json').write_text(json.dumps({'signed': True, 'sha256': digest, 'signature': 'sig'}))
        path.with_suffix('.verified.json').chmod(0o600)
        seen = []
        def run(cmd, **kw):
            if len(cmd) > 1 and cmd[-1].endswith('.whl'):
                path.write_bytes(b'ATTACKER-WHEEL')
                seen.append(pathlib.Path(cmd[-1]).read_bytes())
            return SimpleNamespace(returncode=0, stdout='0.1.0', stderr='')
        with mock.patch.object(auto_update.minisign, 'verify', return_value=f'agentj-host/v1 version=0.1.0 sha256={digest}'), \
                mock.patch.object(update, 'new_argv', return_value=['agentj']), \
                mock.patch.object(update.auto_update if hasattr(update, 'auto_update') else auto_update, 'install_commands',
                                  side_effect=lambda _info, wheel: [['installer', str(wheel)]]):
            self.assertTrue(update.roll_back(st, {'kind': 'uv', 'where': tmp.name}, path, '0.1.0', run))
        self.assertEqual(seen, [original])

class CacheAdversary(unittest.TestCase):
    def test_symlink_cache_parent_and_files_are_never_trusted(self):
        import hashlib
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name))
        cache = st.root / 'upgrade-cache'
        cache.mkdir(mode=0o700)
        path = cache / 'agentj-0.1.0-py3-none-any.whl'
        data = b'wheel'
        path.write_bytes(data)
        path.chmod(0o600)
        digest = hashlib.sha256(data).hexdigest()
        rec = path.with_suffix('.verified.json')
        rec.write_text(json.dumps({'signed': True, 'sha256': digest, 'signature': 'sig'}))
        rec.chmod(0o600)
        with mock.patch.object(auto_update.minisign, 'verify', return_value=f'agentj-host/v1 version=0.1.0 sha256={digest}'):
            self.assertEqual(auto_update.signed_cached_wheel(st, '0.1.0'), path)
            path.rename(cache / 'original.whl')
            path.symlink_to(cache / 'original.whl')
            self.assertIsNone(auto_update.signed_cached_wheel(st, '0.1.0'))
            path.unlink()
            (cache / 'original.whl').rename(path)
            rec.rename(cache / 'original.json')
            rec.symlink_to(cache / 'original.json')
            self.assertIsNone(auto_update.signed_cached_wheel(st, '0.1.0'))
            rec.unlink()
            (cache / 'original.json').rename(rec)
            cache.rename(st.root / 'real-cache')
            cache.symlink_to(st.root / 'real-cache', target_is_directory=True)
            self.assertIsNone(auto_update.signed_cached_wheel(st, '0.1.0'))

class CrashedWatchdogContract(unittest.IsolatedAsyncioTestCase):
    async def test_signed_watchdog_failure_exposes_rollback_only_when_cached(self):
        from agentj import rescue_watchdog as rw
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name))
        wd = object.__new__(rw.Watchdog)
        wd.st = st
        wd.lang_fn = lambda: 'en'
        wd.busy = True
        wd.sessions = {}
        wd.log = mock.Mock()
        wd._emit = lambda *_: lambda *_args: None
        wd.send_app = mock.AsyncMock()
        peer = SimpleNamespace(device='paired-device')
        with mock.patch.object(update, 'repair_install', return_value={'result': 'failed'}), \
                mock.patch.object(reset, 'previous_wheel', return_value=('0.1.0', pathlib.Path('signed.whl'))), \
                mock.patch.object(wd, '_deep_report', return_value='st_' + 'a' * 22) as report:
            await wd._run_action(peer, 'a' * 16, 'deep_repair', 'deep_repair')
        msg = wd.send_app.call_args.args[1]
        self.assertEqual((msg['flow'], msg['rollback'], msg['ok']), ('deep_repair', True, False))
        self.assertEqual(msg['r'], 'a' * 16)
        self.assertEqual(msg['ticket'], 'st_' + 'a' * 22)
        report.assert_called_once()

class SignedDowngradeRace(unittest.TestCase):
    def test_explicit_rollback_never_falls_back_to_unsigned_receipt(self):
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name))
        p = st.root / 'upgrade-cache' / 'agentj-0.1.0-py3-none-any.whl'
        p.parent.mkdir(mode=0o700)
        p.write_bytes(b'unsigned-after-swap')
        import hashlib
        p.with_suffix('.verified.json').write_text(json.dumps({'signed': False, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}))
        runner = mock.Mock()
        with mock.patch.object(reset, 'previous_wheel', return_value=('0.1.0', p)), \
                mock.patch.object(auto_update, 'signed_cached_wheel', return_value=p), \
                mock.patch.object(update, 'install_kind', return_value={'kind': 'uv', 'where': tmp.name}):
            res = reset.run_rollback(st, runner=runner)
        self.assertFalse(res['ok'])
        runner.assert_not_called()

class ReinstallRace(unittest.TestCase):
    def test_reinstall_installer_receives_snapshot_not_swapped_public_cache(self):
        import hashlib
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name))
        original = b'signed-current-release-wheel'
        cache_root = pathlib.Path(tmp.name) / 'agentj-upgrade'
        cache = cache_root / 'upgrade-cache'
        cache.mkdir(parents=True, mode=0o700)
        cache_root.chmod(0o700)
        wheel = cache / f'agentj-{update.__version__}-py3-none-any.whl'
        wheel.write_bytes(original)
        wheel.chmod(0o600)
        digest = hashlib.sha256(original).hexdigest()
        rec = wheel.with_suffix('.verified.json')
        rec.write_text(json.dumps({'signed': True, 'sha256': digest, 'signature': 'sig'}))
        rec.chmod(0o600)
        seen = []
        def runner(cmd, **kw):
            if cmd[0] == 'installer':
                wheel.write_bytes(b'attacker-swapped-wheel')
                seen.append(pathlib.Path(cmd[-1]).read_bytes())
            return SimpleNamespace(returncode=0, stdout=update.__version__, stderr='')
        with mock.patch.dict('os.environ', {'XDG_CACHE_HOME': tmp.name}), \
                mock.patch.object(update, 'verified_plan', return_value=[['installer', str(wheel)]]), \
                mock.patch.object(auto_update.minisign, 'verify', return_value=f'agentj-host/v1 version={update.__version__} sha256={digest}'), \
                mock.patch.object(auto_update, 'install_commands', side_effect=lambda _info, p: [['installer', str(p)]]), \
                mock.patch.object(update, '_pre_snapshot'), mock.patch.object(update, 'writable', return_value=True), \
                mock.patch.object(update, 'new_argv', return_value=['agentj']):
            res = update.repair_install(st, runner=runner, service_installed=False, info={'kind': 'uv', 'where': tmp.name})
        self.assertEqual(res['reason'], 'reinstalled')
        self.assertEqual(seen, [original])


class FatherReport(unittest.TestCase):
    def test_automatic_report_sends_only_fixed_reason_to_redacted_pipeline(self):
        from agentj import rescue_watchdog as rw, rescue_kit
        wd = object.__new__(rw.Watchdog)
        with mock.patch.object(rescue_kit, 'report_only', return_value=('st_' + 'b'*22, False, '')) as report:
            self.assertEqual(wd._deep_report('en', {'why': '/private/path key=secret'}), 'st_' + 'b'*22)
        self.assertEqual(report.call_args.kwargs, {'tag': 'deep_repair_unknown'})
        self.assertEqual(report.call_args.args, ('en',))

    def test_test_mode_default_transport_refuses_public_support(self):
        from agentj import rescue_kit
        with mock.patch.dict('os.environ', {'AGENTJ_TEST': '1'}), mock.patch.object(rescue_kit.urllib.request, 'urlopen') as opened:
            with self.assertRaisesRegex(RuntimeError, 'disabled in tests'):
                rescue_kit._http('POST', 'https://agentj.app/api/v1/support/messages', body={'kind': 'bug'})
            opened.assert_not_called()

class DeepProbeMarker(unittest.TestCase):
    def test_probe_does_not_consume_deep_repair_marker(self):
        from agentj import rescue_repair
        tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(tmp.cleanup)
        st = State(pathlib.Path(tmp.name))
        rescue_repair.write_marker(st, 'deep_repair', 'a' * 16, 'paired-device')
        marker = st.root / rescue_repair.MARKER
        self.assertIsNone(rescue_repair.probe_marker(st))
        self.assertTrue(marker.exists(), 'an ordinary probe must not consume the signed continuation')
        self.assertEqual(rescue_repair.take_marker(st)['device'], 'paired-device')
        self.assertFalse(marker.exists())
        rescue_repair.write_marker(st, 'repair', 'b' * 16, 'paired-device')
        self.assertEqual(rescue_repair.probe_marker(st)['flow'], 'repair')
        self.assertFalse(marker.exists(), 'ordinary repair markers remain one-shot')
