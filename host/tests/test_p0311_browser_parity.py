"""0.30.11 browser parity: isolated state and fake CDP, no customer profile or network.

The two expected failures record behavior that needs product-code changes; they do not
weaken existing assertions or claim that a real site/browser has been qualified.
"""
import _hermetic  # noqa: F401 -- isolate state/config and disable real CDP detection
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import browser, browser_login as bl, browser_sites as bs
from test_p114 import Base, FakeCDP


class SupervisorExit(Base):
    def _exit(self, code):
        proc = Mock(pid=1234, returncode=code)
        proc.poll.return_value = code
        with patch.object(browser, 'settings', return_value={'enabled': True}), \
                patch.object(browser, 'adopted', return_value=None), \
                patch.object(browser, 'start_browser', return_value=(proc, {'pid': 1234, 'headless': False})), \
                patch.object(browser.signal, 'signal'), \
                patch.object(browser, 'service_start') as restart, \
                patch.object(browser, '_write_json') as write:
            result = browser.run(self.st)
        restart.assert_not_called()
        # No explicit exit-intent record is written by the observer today.
        return result, [c.args for c in write.call_args_list]

    def test_owner_closing_window_does_not_respawn_it(self):
        result, writes = self._exit(0)
        self.assertEqual(result, 0)
        self.assertEqual(writes, [])

    def test_unexpected_browser_crash_is_not_treated_as_owner_close(self):
        # Desired: crash is distinguishable and recoverable without changing the
        # owner's intentional close policy. Today run() returns 0 for both.
        result, _ = self._exit(137)
        self.assertNotEqual(result, 0)


class SiteAuthentication(Base):
    def test_live_cdp_is_not_proof_of_wechat_sign_in(self):
        for site, out_url in (('mp-weixin', 'https://mp.weixin.qq.com/'),
                              ('channels-weixin', 'https://channels.weixin.qq.com/login.html')):
            with self.subTest(site=site):
                bs.add(self.st, site)
                cd = FakeCDP([(0, out_url, 'complete')])
                result = bs.check(self.st, [site], _cdp=cd)
                self.assertEqual(result['sites'][0]['status'], 'auth_required')
                self.assertEqual(cd.closed, ['T1'])
                with patch.object(browser, 'runtime', return_value={'ws': 'fake'}):
                    rows = {e['id']: e for e in bs.registry(self.st)}
                self.assertEqual(rows['browser-runtime']['status'], 'ready')
                self.assertEqual(rows['browser-' + site]['check']['status'], 'auth_required')
                self.assertEqual(rows['browser-' + site]['status'], 'unavailable')

    def test_unknown_page_is_not_labeled_signed_out_even_with_live_cdp(self):
        bs.add(self.st, 'channels-weixin')
        cd = FakeCDP([(0, 'https://channels.weixin.qq.com/unrecognized', 'complete')])
        result = bs.check(self.st, ['channels-weixin'], _cdp=cd)
        self.assertEqual(result['sites'][0]['status'], 'unknown')
        self.assertEqual(cd.closed, ['T1', 'T2', 'T3'])  # unknown is retried twice


class QrRefresh(Base, unittest.IsolatedAsyncioTestCase):
    async def test_refresh_of_active_card_mints_new_qr_instead_of_reusing_expiring_one(self):
        # In-memory fake only. Existing request() takes the same-site fast path
        # before capture, so a phone refresh while a card is open reuses its ID.
        bs.add(self.st, 'channels-weixin')
        host = Mock()
        host.st = self.st
        host.channel = 'test-channel'
        host.stopped.return_value = False
        host._approvers.return_value = ['approved-device']
        host._send_ready = AsyncMock()
        cards = bl.LoginCards(Mock(host=host, st=self.st))
        old = {'id': 'a' * 32, 'site': 'channels-weixin',
               'deadline': bl.time.monotonic() + 2, 'img': bytearray(b'old'),
               'cdp': Mock(), 'session': 'old', 'timer': Mock(), 'watch': Mock()}
        cards.cards[old['id']] = old
        with patch.object(browser, 'settings', return_value={'enabled': True}), \
                patch.object(browser, 'runtime', return_value={'ws': 'fake', 'headless': True}), \
                patch.object(cards, '_open_and_capture', return_value={
                    'state': 'qr', 'png': b'new', 'cdp': Mock(), 'session': 'new'}) as capture, \
                patch.object(cards, '_telegram', new_callable=AsyncMock, return_value='none'), \
                patch.object(cards, '_watch', new_callable=AsyncMock):
            response = await cards.request({'site': 'channels-weixin'}, by='approved-device')
            self.assertEqual(response['result'], 'sent')
            self.assertNotEqual(response['id'], old['id'])
            capture.assert_called_once()
            self.assertNotIn(old['id'], cards.cards)
            self.assertEqual(old['img'], bytearray())
            cards.end_all('cancelled')


if __name__ == '__main__':
    unittest.main()
