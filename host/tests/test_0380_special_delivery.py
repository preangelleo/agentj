"""Specialized receipts observe the original paired card and do not change decisions."""
import _hermetic  # noqa: F401
import asyncio
import time
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentj import approval_delivery as delivery, browser_login, card_reminder, provenance, serve
from test_l1 import Phone, _host, _state


class SpecialCardDelivery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = _state(temp.name)
        device = Phone(self.state)
        self.sent = []
        self.host = _host(self.state, self.sent)
        self.phone = serve.Session(cid=1, state='ready', device=device.did, pub=device.pub,
                                   sign_pub=self.state.sign_key(device.did), p33=True, special_receipt=True,
                                   provenance=provenance.phone())
        self.host.sessions = {1: self.phone}
        self.jobs = []
        self.host._bg = lambda job, _: self.jobs.append(job)
        self.addCleanup(lambda: [job.close() for job in self.jobs])

    def observe(self, kind, live=lambda: True):
        frame = {'t': kind, 'id': '1' * 32, 'ttl': 60}
        if kind == 'elev':
            frame |= {'kind': 'sudo', 'n': '3' * 32}
        return delivery.observe_special(self.host, self.phone, frame, live)

    async def follow(self):
        with patch.object(delivery.asyncio, 'sleep', AsyncMock()):
            await self.jobs.pop(0)

    async def test_sudo_receipt_does_not_authorize_and_only_foreground_counts(self):
        card = self.observe('elev')
        rid, nonce = card['id'], card['delivery']['n']
        base = {'t': 'card_receipt', 'kind': 'special', 'id': rid, 'n': nonce}
        probe = delivery._special(self.host)[(rid, self.phone.cid)]
        self.assertTrue(delivery.special_write_live(self.host, self.phone, card))
        await self.host.on_p33(self.phone, 'card_receipt', {**base, 'phase': 'received', 'n': '9' * 32})
        self.assertEqual(probe.phase, '')
        await self.host.on_p33(self.phone, 'card_receipt', {**base, 'phase': 'received'})
        self.phone.fg = False
        await self.host.on_p33(self.phone, 'card_receipt', {**base, 'phase': 'visible'})
        self.assertEqual(probe.phase, 'received')
        self.phone.fg = True
        await self.host.on_p33(self.phone, 'card_receipt', {**base, 'phase': 'visible'})
        self.assertEqual(probe.phase, 'visible')
        with patch.object(card_reminder, 'deliver', AsyncMock()) as reminder:
            await self.follow()
        reminder.assert_not_called()
        self.assertEqual(self.sent, [])
        self.assertEqual(delivery._special(self.host), {})

    async def test_upgrade_reoffers_once_and_revocation_suppresses_reminder(self):
        frame = self.observe('upg_card')
        with patch.object(card_reminder, 'deliver', AsyncMock()) as reminder:
            await self.follow()
            reminder.assert_awaited_once()
        [(cid, replay)] = self.sent
        self.assertEqual((cid, replay['delivery']['n']), (self.phone.cid, frame['delivery']['n']))
        self.assertLess(replay['ttl'], frame['ttl'])
        self.sent.clear()
        frame = self.observe('upg_card')
        self.state.add_device(self.phone.pub, 'paired again', self.phone.sign_pub)
        self.assertFalse(delivery.special_write_live(self.host, self.phone, frame))
        with patch.object(card_reminder, 'deliver', AsyncMock()) as reminder:
            await self.follow()
        reminder.assert_not_called()
        self.assertEqual(self.sent, [])

    async def test_expired_replaced_or_legacy_card_cannot_create_notification(self):
        self.phone.special_receipt = False
        self.assertNotIn('delivery', self.observe('upg_card'))
        self.assertEqual(self.jobs, [])
        self.phone.special_receipt = True
        current = True
        frame = self.observe('upg_card', lambda: current)
        current = False
        self.assertFalse(delivery.special_write_live(self.host, self.phone, frame))
        with patch.object(card_reminder, 'deliver', AsyncMock()) as reminder:
            await self.follow()
        reminder.assert_not_called()
        self.assertEqual(self.sent, [])

    async def test_existing_restricted_login_emits_generic_reminder_without_replaying_code(self):
        login = self.host.elevate.login
        owner = SimpleNamespace(still_paired=lambda _: True)
        fresh = {'id': '8' * 32, 'deadline': time.monotonic() + 90, 'delivered': True,
                 'display': 'unconfirmed', 'chat_epoch': str(self.host.hist.epoch), 'owner': owner,
                 'img': b'fixture image only'}
        login.cards[fresh['id']] = fresh
        with patch.object(browser_login.asyncio, 'sleep', AsyncMock()), patch.object(
                card_reminder, 'deliver', AsyncMock()) as reminder:
            await login._remind_unshown(fresh)
            reminder.assert_awaited_once()
            host, event, pending = reminder.call_args.args
            self.assertIs(host, self.host)
            self.assertEqual(event, fresh['id'])
            self.assertEqual(reminder.call_args.kwargs, {'login': True})
            self.assertTrue(pending())
            fresh['display'] = 'shown'
            self.assertFalse(pending())
            await login._remind_unshown(fresh)
            reminder.assert_awaited_once()
            fresh['display'] = 'unconfirmed'
            owner.still_paired = lambda _: False
            self.assertFalse(pending())
            await login._remind_unshown(fresh)
            reminder.assert_awaited_once()
        self.assertEqual(self.sent, [])
