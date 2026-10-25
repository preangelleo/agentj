"""P521: isolated processes/profiles only; never operate the owner's browser or user services."""
import _hermetic  # noqa: F401
import asyncio
import concurrent.futures
import json
import os
import pathlib
import plistlib
import signal
import sys
import unittest
from unittest.mock import Mock, patch

from agentj import browser, browser_login, browser_sites, service
import test_p114
from test_p114 import Base


class SingleBrowser(Base):
    _fake = test_p114.Runtime._fake

    def test_dead_runtime_is_replaced_but_wrong_profile_is_not_reused(self):
        browser._write_json(self.st, browser.runtime_path(self.st), {
            'pid': 99999999, 'starttime': 'old', 'port': 1, 'profile': str(browser.profile_dir(self.st))})
        with patch.object(browser, 'installed', return_value=self._fake()):
            proc, rec = browser.start_browser(self.st, headless=True)
        try:
            self.assertIsNotNone(proc)
            self.assertTrue(browser.runtime(self.st))
            changed = dict(rec, profile='/not-this-profile')
            browser._write_json(self.st, browser.runtime_path(self.st), changed)
            self.assertIsNone(browser.runtime(self.st))
        finally:
            browser.stop_proc(proc)

    def test_parallel_starts_reuse_one_process_and_port(self):
        browser.ensure_dir(self.st)
        children = []
        try:
            with patch.object(browser, 'installed', return_value=self._fake()):
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                    results = list(pool.map(lambda _: browser.start_browser(self.st, headless=True), range(5)))
                children = [proc for proc, _ in results if proc is not None]
                self.assertEqual(len(children), 1)
                self.assertEqual(len({rec['pid'] for _, rec in results}), 1)
                self.assertEqual(len({rec['port'] for _, rec in results}), 1)
                port_file = browser.profile_dir(self.st) / 'DevToolsActivePort'
                before = port_file.read_bytes()
                proc, rec = browser.start_browser(self.st)
                if proc is not None:
                    children.append(proc)
                self.assertIsNone(proc)
                self.assertEqual(port_file.read_bytes(), before)
        finally:
            for proc in children:
                browser.stop_proc(proc)

    def test_live_pid_with_unavailable_cdp_never_launches_again(self):
        browser.ensure_dir(self.st)
        browser._write_json(self.st, browser.runtime_path(self.st), {
            'pid': os.getpid(), 'starttime': browser.proc_start_time(os.getpid()), 'port': 1})
        with patch.object(browser, 'installed', return_value=self._fake()), patch.object(browser.subprocess, 'Popen') as popen:
            with self.assertRaises(browser.BrowserError):
                browser.start_browser(self.st)
            popen.assert_not_called()


class MultiSeat(Base):
    def test_four_seats_default_to_at_most_one_visible_browser(self):
        for platform in ('linux', 'macos', 'wsl2'):
            modes = []
            for n in range(1, 5):
                st = __import__('agentj.state', fromlist=['State']).State(self.st.root / str(n))
                browser.ensure_dir(st)
                proc = Mock(pid=100 + n)
                with patch.dict(os.environ, {'AGENTJ_INSTANCE': str(n)}), \
                        patch.object(browser, 'system', return_value=platform), \
                        patch.object(browser, 'display_env', return_value=True), \
                        patch.object(browser, 'installed', return_value={'path': '/fake/chrome', 'version': 'test'}), \
                        patch.object(browser, 'choose_executable', return_value=('/fake/chrome', 'bundled')), \
                        patch.object(browser.subprocess, 'Popen', return_value=proc) as launch, \
                        patch.object(browser, '_wait_port', return_value=(12345, '/test')), \
                        patch.object(browser, 'listen_addresses', return_value={'127.0.0.1'}):
                    _, rec = browser._start_browser(st)
                    modes.append(rec['headless'])
                    self.assertEqual('--headless=new' in launch.call_args.args[0], n != 1)
            self.assertEqual(modes, [False, True, True, True])

    def test_secondary_seat_never_auto_shows_when_display_arrives(self):
        proc = Mock(pid=1234, returncode=None)
        proc.poll.side_effect = [None, 0]
        with patch.dict(os.environ, {'AGENTJ_INSTANCE': '2'}), \
                patch.object(browser, 'settings', return_value={'enabled': True}), \
                patch.object(browser, 'adopted', return_value=None), \
                patch.object(browser, 'start_browser', return_value=(proc, {'pid':1234, 'headless':True})), \
                patch.object(browser.signal, 'signal'), patch.object(browser.time, 'sleep'), \
                patch.object(self.st, 'log'), \
                patch.object(browser.time, 'monotonic', side_effect=[0, 31]), \
                patch.object(browser, '_manager_has_display', return_value=True) as display:
            self.assertEqual(browser.run(self.st), 0)
            display.assert_not_called()


    def test_explicit_show_closes_verified_headless_then_reuses_profile(self):
        rec = {'pid':1234, 'starttime':'old', 'ws':'ws://127.0.0.1:1/test', 'headless':True}
        shown = {**rec, 'pid':5678, 'headless':False}
        with patch.object(browser, 'runtime', side_effect=[rec, rec, shown]), \
                patch.object(browser, 'adopted', return_value=None), \
                patch.object(browser, 'display_env', return_value=True), \
                patch.object(browser, 'login_in_progress', return_value=False), \
                patch.object(browser, 'proc_start_time', return_value=None), \
                patch('agentj.browser_cdp.CDP') as cdp, \
                patch.object(browser, '_start_browser', return_value=(Mock(), shown)) as launch:
            self.assertEqual(browser.visible_runtime(self.st), shown)
            cdp.return_value.__enter__.return_value.call.assert_called_once_with('Browser.close')
            launch.assert_called_once_with(self.st, headless=False)

    def test_show_never_restarts_adopted_no_screen_or_active_qr_browser(self):
        rec = {'pid':1234, 'starttime':'old', 'ws':'ws://127.0.0.1:1/test', 'headless':True}
        for adopted, display, active in (({'port':1}, True, False), (None, False, False), (None, True, True)):
            with patch.object(browser, 'runtime', return_value=rec), \
                    patch.object(browser, 'adopted', return_value=adopted), \
                    patch.object(browser, 'display_env', return_value=display), \
                    patch.object(browser, '_manager_has_display', return_value=False), \
                    patch.object(browser, 'login_in_progress', return_value=active), \
                    patch('agentj.browser_cdp.CDP') as cdp, patch.object(browser, '_start_browser') as launch:
                self.assertEqual(browser.visible_runtime(self.st), rec)
                cdp.assert_not_called()
                launch.assert_not_called()

    def test_failed_graceful_close_never_starts_a_second_browser(self):
        rec = {'pid':1234, 'starttime':'old', 'ws':'ws://127.0.0.1:1/test', 'headless':True}
        with patch.object(browser, 'runtime', return_value=rec), \
                patch.object(browser, 'adopted', return_value=None), \
                patch.object(browser, 'display_env', return_value=True), \
                patch.object(browser, 'login_in_progress', return_value=False), \
                patch.object(browser, 'proc_start_time', return_value='old'), \
                patch.object(browser.time, 'monotonic', side_effect=[0, 11]), \
                patch('agentj.browser_cdp.CDP'), patch.object(browser, '_start_browser') as launch:
            with self.assertRaises(browser.BrowserError):
                browser.visible_runtime(self.st)
            launch.assert_not_called()

    def test_only_open_routes_request_visible_runtime(self):
        from agentj import browser_sites as bs
        with patch.object(browser, 'visible_runtime', return_value={'ws':'fake', 'headless':False}) as show, \
                patch('agentj.browser_sites.CDP'):
            self.assertTrue(bs.open_url(self.st, 'http://127.0.0.1:1')['ok'])
            show.assert_called_once_with(self.st)
        with patch.object(browser, 'visible_runtime') as show, \
                patch.object(browser, 'wake'), patch.object(bs, 'check', return_value={'ok':True}), \
                patch.object(browser, 'endpoint', return_value={'ok':True}):
            for req in ({'action':'check'}, {'action':'check', 'if_stale':True}, {'action':'endpoint'}):
                browser.execute(req, self.st)
            show.assert_not_called()


    def test_checklist_and_non_qr_sign_in_show_background_seat(self):
        shown = {'ws':'fake', 'pid':1234, 'headless':False}
        with patch.object(browser, 'visible_runtime', return_value=shown) as show, \
                patch.object(browser_sites, 'ensure_item_sites', return_value=['gmail']), \
                patch.object(browser_sites, 'load', return_value={'sites':{'gmail':{'status':'auth_required'}}}), \
                patch.object(browser_sites, 'templates', return_value={'gmail':{'login_url':'https://example.invalid'}}), \
                patch('agentj.browser_sites.CDP'), \
                patch.object(browser, 'raise_window', return_value={'ok':True, 'method':'test'}):
            self.assertTrue(browser_sites.open_login(self.st, 'gmail')['ok'])
            show.assert_called_once_with(self.st)
        cards = browser_login.LoginCards.__new__(browser_login.LoginCards)
        cards.st = self.st
        with patch.object(browser, 'visible_runtime', return_value=shown) as show, \
                patch.object(browser_sites, 'open_url', return_value={'ok':True}):
            res = asyncio.run(cards._host_login('gmail', {'login_url':'https://example.invalid'}, {}, {'headless':True}))
            self.assertEqual(res['result'], 'host_login')
            self.assertTrue(res['opened'])
            show.assert_called_once_with(self.st)


class Supervisor(Base):
    def test_normal_window_exit_is_success(self):
        proc = Mock(pid=1234, returncode=0)
        proc.poll.return_value = 0
        with patch.object(browser, 'settings', return_value={'enabled': True}), patch.object(browser, 'adopted', return_value=None), \
                patch.object(browser, 'start_browser', return_value=(proc, {'pid': 1234, 'headless': False})), \
                patch.object(browser.signal, 'signal'):
            self.assertEqual(browser.run(self.st), 0)

    def test_existing_browser_run_exits_zero_without_stopping_it(self):
        with patch.object(browser, 'settings', return_value={'enabled': True}), patch.object(browser, 'adopted', return_value=None), \
                patch.object(browser, 'start_browser', return_value=(None, {'pid': 1234})), \
                patch.object(browser.signal, 'signal'), patch.object(browser, 'stop_proc') as stop:
            self.assertEqual(browser.run(self.st), 0)
            stop.assert_not_called()

    def test_service_restart_preserves_browser_and_runtime(self):
        proc = Mock(pid=1234, returncode=None)
        browser._write_json(self.st, browser.runtime_path(self.st), {'pid': 1234})
        handlers = {}
        def register(sig, fn):
            handlers[sig] = fn
        def sleep(_):
            handlers[signal.SIGTERM]()
        with patch.object(browser, 'settings', return_value={'enabled': True}), patch.object(browser, 'adopted', return_value=None), \
                patch.object(browser, 'start_browser', return_value=(proc, {'pid': 1234, 'headless': False})), \
                patch.object(browser.signal, 'signal', side_effect=register), patch.object(browser.time, 'sleep', side_effect=sleep), \
                patch.object(browser, 'stop_proc') as stop:
            proc.poll.return_value = None
            self.assertEqual(browser.run(self.st), 0)
            stop.assert_not_called()
            self.assertTrue(browser.runtime_path(self.st).exists())

    def test_services_do_not_respawn_windows(self):
        unit = browser.unit_text(['/agentj'], {})
        self.assertIn('Restart=no', unit)
        self.assertIn('KillMode=process', unit)
        plist = plistlib.loads(browser.plist_bytes('test.browser', ['/agentj'], '/tmp/log'))
        self.assertFalse(plist['KeepAlive'])
        self.assertTrue(plist['AbandonProcessGroup'])

    def test_background_check_does_not_wake_closed_browser(self):
        with patch.object(browser, 'wake') as wake, patch('agentj.browser_sites.check', return_value={'ok': False}):
            browser.execute({'action': 'check', 'if_stale': True}, self.st)
            wake.assert_not_called()


    def test_upgrade_hook_refreshes_browser_and_keeps_host_failure_independent(self):
        notes = []
        with patch.object(browser, 'refresh_service') as refresh:
            service._browser_note(self.st, notes)
            refresh.assert_called_once_with(self.st)
        with patch.object(browser, 'refresh_service', side_effect=OSError):
            service._browser_note(self.st, notes)
        self.assertEqual(len(notes), 1)
        self.assertIn('agentj browser setup', notes[0])

    def test_refresh_rewrites_only_existing_service_without_start(self):
        with patch.object(browser, 'cleanup_legacy_services', return_value=[]), \
                patch.object(service, 'platform', return_value='linux'), \
                patch.object(service, 'unit_dir', return_value=str(self.st.root)), \
                patch.object(browser, 'service_install') as install:
            browser.refresh_service(self.st)
            install.assert_not_called()
            (self.st.root / (browser.svc_name() + '.service')).write_text('old service')
            browser.refresh_service(self.st)
            install.assert_called_once_with(self.st, start=False)

    def test_legacy_cleanup_scoped_to_exact_state_and_known_service(self):
        with patch.object(service, 'platform', return_value='linux'), \
                patch.object(service, 'legacy_name', return_value='test-old'), \
                patch.object(service, 'unit_dir', return_value=str(self.st.root)), \
                patch.object(service, '_systemctl') as ctl:
            path = self.st.root / 'test-old-browser.service'
            path.write_text(browser.unit_text(['/agentj'], {'AGENTJ_STATE_DIR': '/different-state'}))
            self.assertEqual(browser.cleanup_legacy_services(self.st), [])
            ctl.assert_not_called()
            path.write_text(browser.unit_text(['/agentj'], {'AGENTJ_STATE_DIR': str(self.st.root)}))
            self.assertEqual(browser.cleanup_legacy_services(self.st), ['test-old-browser'])
            self.assertFalse(path.exists())
            self.assertEqual(ctl.call_args_list[0].args, ('disable', '--now', 'test-old-browser.service'))

    def test_macos_upgrade_does_not_open_now_and_keeps_next_login_start(self):
        home = self.st.root / 'home'
        seen = []
        def bootstrap(name, path):
            seen.append(plistlib.loads(pathlib.Path(path).read_bytes())['RunAtLoad'])
            return __import__('subprocess').CompletedProcess([], 0, '', '')
        with patch.dict(os.environ, {'HOME': str(home)}), \
                patch.object(service, 'platform', return_value='macos'), \
                patch.object(browser, 'svc_name', return_value='test.p521.browser'), \
                patch.object(browser, 'cleanup_legacy_services', return_value=[]), \
                patch.object(service.macapp, 'wrap', side_effect=lambda argv: (argv, {})), \
                patch.object(service, '_bootstrap', side_effect=bootstrap):
            out = browser.service_install(self.st, start=False)
            self.assertEqual(seen, [False], 'upgrade bootstrap must not open a window')
            self.assertTrue(plistlib.loads(pathlib.Path(out['path']).read_bytes())['RunAtLoad'],
                            'normal start at next login must be kept')

    def test_macos_legacy_cleanup_preserves_other_state(self):
        home = self.st.root / 'home'
        agents = home / 'Library' / 'LaunchAgents'
        agents.mkdir(parents=True)
        with patch.object(service, 'platform', return_value='macos'), \
                patch.object(service, 'legacy_name', return_value='test.old'), \
                patch.object(pathlib.Path, 'home', return_value=home), \
                patch.object(service, '_bootout_wait', return_value=True) as bootout:
            path = agents / 'test.old.browser.plist'
            path.write_bytes(browser.plist_bytes('test.old.browser', ['/agentj'], '/tmp/log', {'AGENTJ_STATE_DIR':'/other'}))
            self.assertEqual(browser.cleanup_legacy_services(self.st), [])
            bootout.assert_not_called()
            path.write_bytes(browser.plist_bytes('test.old.browser', ['/agentj'], '/tmp/log', {'AGENTJ_STATE_DIR':str(self.st.root)}))
            self.assertEqual(browser.cleanup_legacy_services(self.st), ['test.old.browser'])
            self.assertFalse(path.exists())

    def test_wsl2_uses_linux_lock_and_service_policy(self):
        with patch.object(browser, 'system', return_value='wsl2'):
            self.assertTrue(browser.platform_key().startswith('linux-'))
            self.assertIn('Restart=no', browser.unit_text(['/agentj'], {}))
            with browser.start_lock(self.st):
                self.assertTrue((browser.bdir(self.st) / 'start.lock').is_file())


if __name__ == '__main__':
    unittest.main()
