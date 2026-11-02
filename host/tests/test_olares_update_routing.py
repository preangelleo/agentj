"""Market owns the existing Olares image; no local installer or manager job."""
import _hermetic  # noqa: F401
import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from agentj import update, auto_update, phone_update

FILES = {'/opt/agentj-olares/' + n for n in ('entry.sh', 'setup_page.py', 'serve_env.py')}

class Routing(unittest.TestCase):
    def market(self, prefix='/opt/agentj', files=FILES, platform='linux'):
        stack = ExitStack()
        stack.enter_context(patch.object(update.sys, 'prefix', prefix))
        stack.enter_context(patch.object(update.sys, 'platform', platform))
        stack.enter_context(patch.object(Path, 'is_file', lambda p: str(p) in files))
        self.addCleanup(stack.close)
        return stack

    def test_existing_chart_without_new_environment_marker(self):
        self.market()
        self.assertEqual(update.install_kind()['kind'], 'olares')

    def test_generic_linux_wsl_and_containers_remain_native(self):
        for prefix, files, platform in [('/opt/agentj', set(), 'linux'), ('/tmp/native/uv/tools/agentj', FILES, 'linux'),
                                         ('/opt/agentj', FILES - {'/opt/agentj-olares/entry.sh'}, 'linux'),
                                         ('/opt/agentj', FILES, 'darwin')]:
            with self.subTest(prefix=prefix, files=files, platform=platform), self.market(prefix, files, platform):
                self.assertNotEqual(update.install_kind()['kind'], 'olares')

    def test_check_uses_market_without_public_version_request(self):
        self.market()
        with patch.object(update, 'fetch_latest', side_effect=AssertionError('public version queried')):
            rec = update.check()
        self.assertEqual(rec['status'], 'market_managed')
        self.assertIsNone(rec['latest'])
        self.assertEqual(rec['command'], '')

    def test_direct_and_authorized_apply_refuse_before_check_or_state(self):
        self.market()
        for fn, args in [(update.apply, ()), (update.authorized_apply, ('bad-code', '99.0.0'))]:
            with self.subTest(fn=fn.__name__):
                st = Mock()
                rec = fn(st, *args, check_fn=Mock(side_effect=AssertionError('check')))
                self.assertEqual(rec['reason'], 'market_managed')
                self.assertEqual(rec['exit'], 2)
                self.assertFalse(st.mock_calls)

    def test_planners_refuse_before_cache_or_command(self):
        self.market()
        with patch.object(auto_update, 'cached_wheel', side_effect=AssertionError('cache')):
            with self.assertRaisesRegex(ValueError, 'market_managed'):
                update.verified_plan(Mock(), {'kind': 'pip', 'where': '/opt/agentj'}, '99.0.0')
        with self.assertRaisesRegex(ValueError, 'market_managed'):
            auto_update.install_commands({'kind': 'olares', 'where': '/opt/agentj'}, 'unused.whl')

    def test_daily_and_automatic_paths_ignore_enabled_preferences(self):
        self.market()
        st = Mock()
        self.assertFalse(update.auto_enabled(st))
        self.assertFalse(auto_update.enabled(st))
        self.assertFalse(auto_update.launch(st))
        self.assertEqual(auto_update.run(st)['reason'], 'market_managed')
        self.assertFalse(st.mock_calls)

    def test_phone_launch_never_calls_manager_or_apply(self):
        self.market()
        st = Mock()
        with patch('agentj.service_recovery.launch_job', side_effect=AssertionError('systemd')):
            self.assertFalse(phone_update.launch(st))
        rec = phone_update.run(st, apply_fn=Mock(side_effect=AssertionError('install')))
        self.assertEqual(rec['reason'], 'market_managed')
        for lang in ('zh', 'en'):
            text = phone_update.text(rec, lang)
            self.assertIn('Olares', text)
            self.assertIn('Market' if lang == 'en' else '应用市场', text)
            self.assertNotIn('agentj update apply', text)
            self.assertNotIn('systemd', text)
        self.assertFalse(st.mock_calls)

    def test_fenced_compatibility_operations_refuse_without_state_or_runner(self):
        self.market()
        st, runner = Mock(), Mock(side_effect=AssertionError('runner'))
        for op in ('prepare', 'pending', 'marker', 'record', 'clear', 'rollback', 'health'):
            self.assertEqual(update.host_op(st, {'op': op}, {}, run=runner),
                             {'ok': False, 'why': 'market_managed'})
        self.assertTrue(update.host_op(st, {'op': 'ping'}, {})['ok'])
        self.assertFalse(st.mock_calls)
        runner.assert_not_called()

    def test_phone_upgrade_fallback_no_check_or_lock(self):
        from agentj.serve import Host
        self.market()
        host = SimpleNamespace(lang='en', st=Mock(), cmd_card=Mock())
        with patch.object(update, 'check', side_effect=AssertionError('check')), patch.object(phone_update, 'release') as release:
            asyncio.run(Host.phone_upgrade(host, 'turn', 'phone'))
        self.assertIn('Market', host.cmd_card.call_args.args[1].text)
        release.assert_not_called()
