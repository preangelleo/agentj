import _hermetic  # noqa: F401
import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import DEFAULT, AsyncMock, Mock, patch

from agentj import approval_delivery as delivery, card_reminder, preferences, provenance, serve
from test_l1 import Phone, _host, _state


class ApprovalObservation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.st = _state(temp.name)
        self.sent = []
        self.host = _host(self.st, self.sent)
        self.phone, self.other, self.old = [self.pair(i, i < 3) for i in (1, 2, 3)]
        self.ask = SimpleNamespace(rid='b' * 32, deadline=time.monotonic() + 90, only=self.phone.device,
                                   envelope_digest='digest', fut=asyncio.get_running_loop().create_future(),
                                   review_revision=1, review_state='pending', delivery_subject_live=lambda: True)
        self.host.asks[self.ask.rid] = self.ask
        self.host._ask_msg = lambda ask, _: {'t': 'ask', 'id': ask.rid, 'ttl': int(ask.deadline - time.monotonic())}
        self.jobs = []
        self.host._bg = lambda job, _: self.jobs.append(job)
        self.addCleanup(lambda: [job.close() for job in self.jobs])
        await delivery.deliver_observed(self.host, self.ask, True)
        [(cid, self.card)] = self.sent
        self.assertEqual(cid, self.phone.cid)
        self.receipt = {'t': 'card_receipt', 'kind': 'ask', 'id': self.ask.rid, 'n': self.card['delivery']['n'],
                        'phase': 'received'}
        self.probe = delivery.probes(self.host)[(self.ask.rid, self.phone.cid)]

    def pair(self, cid, cap):
        phone = Phone(self.st)
        session = serve.Session(cid=cid, state='ready', device=phone.did, pub=phone.pub,
                                sign_pub=self.st.sign_key(phone.did), p33=True, card_receipt=cap, ask_receipt=cap,
                                provenance=provenance.phone())
        self.host.sessions[cid] = session
        return session

    async def follow(self):
        with patch('agentj.approval_delivery.asyncio.sleep', new=AsyncMock()):
            await self.jobs.pop(0)

    async def test_receipt_is_exact_foreground_and_does_not_answer(self):
        await self.host.on_p33(self.other, 'card_receipt', self.receipt)
        await self.host.on_p33(self.phone, 'card_receipt', {**self.receipt, 'phase': 'visible'})
        self.assertEqual(self.probe.phase, '')
        await self.host.on_p33(self.phone, 'card_receipt', self.receipt)
        self.phone.fg = False
        await self.host.on_p33(self.phone, 'card_receipt', {**self.receipt, 'phase': 'visible'})
        self.assertEqual(self.probe.phase, 'received')
        self.phone.fg = True
        await self.host.on_p33(self.phone, 'card_receipt', {**self.receipt, 'phase': 'visible'})
        self.assertEqual(self.probe.phase, 'visible')
        self.assertFalse(self.ask.fut.done())
        with patch.object(card_reminder, 'deliver', new=AsyncMock()) as reminder:
            await self.follow()
        reminder.assert_not_called()
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(delivery.probes(self.host))

    async def test_retries_once_with_same_nonce_and_rechecks_before_send(self):
        with patch.object(card_reminder, 'deliver', new=AsyncMock()) as reminder:
            await self.follow()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[1][1]['delivery']['n'], self.receipt['n'])
        self.assertLessEqual(self.sent[1][1]['delivery']['ttl'], self.card['delivery']['ttl'])
        reminder.assert_awaited_once()

    async def test_changed_revision_and_device_do_not_retry_or_remind(self):
        self.st.add_device(self.phone.pub, 'paired again', self.phone.sign_pub)
        self.assertFalse(delivery.ready_to_write(self.host, self.phone, self.card))
        with patch.object(card_reminder, 'deliver', new=AsyncMock()) as reminder:
            await self.follow()
        reminder.assert_not_called()
        self.assertEqual(len(self.sent), 1)

    async def test_disconnected_session_cannot_replay_into_replacement(self):
        self.host.sessions[self.phone.cid] = self.other
        self.assertFalse(delivery.ready_to_write(self.host, self.phone, self.card))
        with patch.object(card_reminder, 'deliver', new=AsyncMock()):
            await self.follow()
        self.assertEqual(len(self.sent), 1)

    async def test_question_uses_same_reminder_only_while_unconfirmed_and_current(self):
        self.enterContext(patch.object(card_reminder, 'ENABLED', True))
        q = serve.Question('e' * 32, [], 'test', time.monotonic() + 90, asyncio.get_running_loop().create_future(),
                           delivery_epoch=self.host.hist.epoch, reminder_live=lambda: True)
        self.host.questions[q.qid] = q
        self.host.preferences = {'notify': {'card_reminders': ['telegram']}}
        await self.host._deliver_observed_question(q)
        follow = self.jobs.pop()
        with patch('agentj.approval_delivery.asyncio.sleep', new=AsyncMock()), patch.object(
                card_reminder, 'deliver', new=AsyncMock()) as reminder:
            await follow
            reminder.assert_awaited_once()
            self.assertEqual(reminder.call_args.args[1], q.qid)
            q.reminder_live = lambda: False
            self.assertFalse(reminder.call_args.args[2]())


class OwnerReminder(unittest.IsolatedAsyncioTestCase):
    def host(self, channels):
        return SimpleNamespace(lang='en', preferences={'notify': {'card_reminders': channels}},
                               stopped=lambda: False, st=SimpleNamespace(log=Mock()))

    async def test_rejection_advances_once_and_all_payloads_are_fixed(self):
        self.enterContext(patch.object(card_reminder, 'ENABLED', True))
        host = self.host(['wechat', 'telegram', 'mail'])
        wc, tg, email = [AsyncMock(return_value=value) for value in
                         (card_reminder.CLOSED, card_reminder.CLOSED, card_reminder.ACCEPTED)]
        with patch.multiple(card_reminder, _wechat=wc, _telegram=tg, _mail=email):
            await card_reminder.deliver(host, 'event-secret-marker', lambda: True)
            await card_reminder.deliver(host, 'event-secret-marker', lambda: True)
        for provider in (wc, tg, email):
            provider.assert_awaited_once()
            _, subject, text, _ = provider.call_args.args
            self.assertEqual((subject, text), ('An Agent J card is waiting',
                                                'Open Agent J on your paired remote to review the card.'))
        self.assertEqual([kw['via'] for _, kw in host.st.log.call_args_list], ['wechat', 'telegram', 'mail'])

    async def test_uncertain_send_and_revocation_stop_fallback(self):
        self.enterContext(patch.object(card_reminder, 'ENABLED', True))
        host = self.host(['wechat', 'telegram'])
        wc, tg = AsyncMock(return_value=card_reminder.UNKNOWN), AsyncMock()
        with patch.multiple(card_reminder, _wechat=wc, _telegram=tg):
            await card_reminder.deliver(host, 'uncertain', lambda: True)
        wc.assert_awaited_once()
        tg.assert_not_called()

        async def withdraw(*_):
            host.preferences['notify']['card_reminders'] = []
            return card_reminder.CLOSED

        with patch.multiple(card_reminder, _wechat=withdraw, _telegram=tg):
            await card_reminder.deliver(host, 'revoked', lambda: True)
        tg.assert_not_called()

    async def test_saved_channels_cannot_send_before_release_admission(self):
        host = self.host(['wechat', 'telegram', 'mail'])
        self.assertEqual(card_reminder.channels(host.preferences), ())
        with patch.multiple(card_reminder, _wechat=DEFAULT, _telegram=DEFAULT, _mail=DEFAULT) as providers:
            await card_reminder.deliver(host, 'deferred', lambda: True)
            for provider in providers.values():
                provider.assert_not_called()
        host.st.log.assert_not_called()

    def test_invalid_opt_in_fails_closed(self):
        self.enterContext(patch.object(card_reminder, 'ENABLED', True))
        for value in (['telegram', 'telegram'], ['other'], [{}]):
            prefs = self.host(value).preferences
            with self.assertRaises(preferences.ConfigError):
                preferences.validate(prefs)
            self.assertEqual(card_reminder.channels(prefs), ())
        self.assertEqual(card_reminder.channels({}), ())
