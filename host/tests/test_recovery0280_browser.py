"""#470 fake CDP only; real browser modules must import, dependencies are never faked."""
import _hermetic  # noqa: F401
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from agentj import browser_login as login, browser_sites as sites


class ScanLanding(unittest.IsolatedAsyncioTestCase):
    def fixture(self, href):
        class FakeCDP:
            def location(self, session):
                return href, 'complete'
        card = {'id': 'synthetic', 'site': 'bilibili', 'title': 'Bilibili', 'lock': threading.Lock(),
                'cdp': FakeCDP(), 'session': 'fake'}
        desk = login.LoginCards.__new__(login.LoginCards)
        desk.st = NS(log=Mock())
        desk.host = NS(lang='en', hist_add=Mock(), send_app=AsyncMock())
        desk.cards = {card['id']: card}
        desk._end = Mock()
        desk._audit = Mock()
        return desk, card

    async def test_landing_requires_trusted_fresh_check(self):
        desk, card = self.fixture('https://www.bilibili.com/')
        with patch.object(sites, 'load', return_value={'sites': {'bilibili': {'template': 'bilibili'}}}), \
             patch.object(sites, 'check', return_value={'sites': [{'site': 'bilibili', 'status': 'logged_in'}]}) as check:
            self.assertEqual(desk._tab(card)[0], 'landing')
            await desk._check_tap(NS(device='fixture'), card)
        check.assert_called_once_with(desk.st, ['bilibili'], False, 'login')
        desk._end.assert_called_once_with(card, 'done')
        desk.host.send_app.assert_not_awaited()

    async def test_scan_tap_reports_failure_without_claiming_success(self):
        desk, card = self.fixture('https://passport.bilibili.com/login?synthetic=private')
        with patch.object(sites, 'load', return_value={'sites': {'bilibili': {'template': 'bilibili'}}}), \
             patch.object(sites, 'check', return_value={'sites': [{'site': 'bilibili', 'status': 'auth_required'}]}):
            await desk._check_tap(NS(device='fixture'), card)
        desk._end.assert_not_called()
        message = desk.host.send_app.await_args.args[1]
        self.assertEqual(message['why'], 'other_browser')
        self.assertEqual(message['state'], 'waiting')
        self.assertNotIn('private', str(desk.host.hist_add.call_args))

    async def test_actual_phone_tap_retains_pairing_nonce_and_trusted_probe(self):
        desk,card=self.fixture('https://passport.bilibili.com/login?fixture=private')
        desk.cards.clear();card.update(id='a'*32,nonce='fixture-nonce');desk.cards[card['id']]=card
        desk.st.sign_key=Mock(return_value=b'fixture-device-key')
        msg={'t':'login_qr_check','id':card['id'],'n':card['nonce']}
        with patch.object(sites,'load',return_value={'sites':{'bilibili':{'template':'bilibili'}}}), \
             patch.object(sites,'check',return_value={'sites':[{'site':'bilibili','status':'auth_required'}]}) as check:
            await desk.on_phone(NS(device='fixture'),msg)
            check.assert_called_once_with(desk.st,['bilibili'],False,'login')
            self.assertEqual(desk.host.send_app.await_args.args[1]['why'],'other_browser')
            check.reset_mock()
            desk.st.sign_key.return_value=None
            await desk.on_phone(NS(device='revoked'),msg)
            check.assert_not_called()
            desk.st.sign_key.return_value=b'fixture-device-key'
            await desk.on_phone(NS(device='fixture'),dict(msg,n='wrong-nonce'))
            check.assert_not_called()
            self.assertEqual(desk.host.send_app.await_args.args[1]['why'],'gone')
        desk._end.assert_not_called()
