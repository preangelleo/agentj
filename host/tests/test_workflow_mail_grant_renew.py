"""Workflow mail grants survive send statistics; an inactive renewable grant always carries exactly one host card."""
import _hermetic  # noqa: F401
import asyncio
import os
from unittest.mock import patch

from agentj import workflow_mail as wm, contacts
from test_p194_owner_direct import ME
from test_workflow_owner_mail import Delivery


class Renew(Delivery):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.cc = ['person0@example.test', 'person1@example.test']
        self.ids = []
        for a in self.cc:          # created by an ordinary send: created == last_sent_at, send_count 1
            self.ids.append(contacts.record_sent(self.st, [a])[0]['id'])

    async def status(self, peer=True):
        req = {'op': 'status', 'workflow_id': 'story'}
        if peer:
            return await self.service.control(req, os.getpid())
        with patch.object(wm, 'source', return_value=None):
            return await self.service.control(req, None)

    def rename_owner(self):
        self.rename_n = getattr(self, 'rename_n', 0) + 1
        contacts.local(self.st, {'op': 'owner_set', 'owner_ok': True,
                                 'contact': {'name': 'Renamed', 'emails': [f'changed{self.rename_n}@example.test'], 'phones': ['13800001111']}})

    def hold(self):
        """The phone card stays open until the test answers it."""
        self.answers = []
        async def held(*a, **kw):
            self.assertGreaterEqual(kw['ttl'], 1800)        # a 2-minute default is too short for the owner's phone
            fut = asyncio.get_running_loop().create_future()
            self.answers.append(fut)
            return await self.sign(*a, **kw) if await fut == 'allow' else {'behavior': 'deny'}
        self.ask.side_effect = held

    async def settle(self, n):
        for _ in range(200):
            if len(self.answers) >= n:
                break
            await asyncio.sleep(.005)
        return len(self.answers)

    async def answer(self, value):
        self.answers[-1].set_result(value)
        await self.service.pending['story']

    async def test_workflow_mail_grant_survives_contact_send_stats(self):
        for cc in ([], self.ids):
            with self.subTest(cc=bool(cc)):
                await self.grant(cc_contacts=cc) if cc else await self.grant()
                row = self.service.load()['grants']['story']
                for _ in range(3):
                    contacts.record_sent(self.st, [ME] + self.cc)
                self.assertIsNone(self.service.why(row, self.contract))
                self.assertEqual((await self.status())['grant']['status'], 'active')

    async def test_workflow_mail_grant_contact_identity_change_invalidates(self):
        await self.grant(cc_contacts=self.ids)
        row = self.service.load()['grants']['story']
        b = contacts.read(self.st)
        contacts.get(b, self.ids[1])['emails'][0]['v'] = 'other@example.test'
        contacts.write(self.st, b)
        self.assertEqual(self.service.why(row, self.contract), 'contact_changed')
        await self.grant()
        row = self.service.load()['grants']['story']
        self.rename_owner()
        self.assertEqual(self.service.why(row, self.contract), 'contact_changed')
        res = await self.service.send(self.req, os.getpid())
        self.assertEqual((res['why'], res['reason']), ('grant_required', 'contact_changed'))
        self.assertEqual(self.smtp.sent, [])

    async def test_workflow_mail_inactive_without_card_requests_one_card(self):
        await self.grant()
        self.rename_owner()
        self.hold()
        res = (await self.status(peer=False))['grant']          # read-only main-session query: no card, no owner wording
        self.assertEqual((res['status'], res['reason'], res['card']), ('inactive', 'contact_changed', 'unverified'))
        self.assertNotIn('拍板', res['note'])
        self.assertEqual(self.answers, [])
        first = (await self.status())['grant']
        self.assertEqual((first['card'], await self.settle(1)), ('sent', 1))
        self.assertIn('发信计数', self.ask.await_args.args[1]['summary'])
        self.assertEqual((await self.status())['grant']['card'], 'pending')
        self.assertEqual((await self.service.control({'op': 'grant', 'workflow_id': 'story'}, os.getpid()))['why'], 'card_pending')
        self.assertEqual(len(self.answers), 1)
        await self.answer('timeout')                             # host timeout = default deny
        later = (await self.status())['grant']
        self.assertEqual(later['card'], 'cooldown'); self.assertTrue(later['card_retry_at'])
        self.assertEqual(len(self.answers), 1)
        data = self.service.load(); data['cards']['story']['at'] -= wm.COOLDOWN; self.service.save(data)
        self.assertEqual((await self.status())['grant']['card'], 'sent')
        self.assertEqual(await self.settle(2), 2)
        self.src.stop()                                          # the requesting run ended before the owner looked
        await self.answer('allow')
        self.assertTrue(self.service.live(self.service.load()['grants']['story'], self.contract))

    async def test_workflow_mail_grant_card_outlives_cli_wait(self):
        self.hold()
        with patch.object(wm, 'WAIT', 0.05):
            res = await self.service.control({'op': 'grant', 'workflow_id': 'story'}, os.getpid())
        self.assertEqual((res['why'], await self.settle(1)), ('card_pending', 1))
        await self.answer('allow')
        self.assertEqual((await self.status())['grant']['status'], 'active')

    async def test_workflow_mail_legacy_full_record_grant_compat(self):
        with patch.object(wm, 'ident', wm.digest):                  # pre-fix grants hashed the whole record
            await self.grant(cc_contacts=self.ids)
        row = self.service.load()['grants']['story']
        self.assertIsNone(self.service.why(row, self.contract))
        contacts.record_sent(self.st, [ME] + self.cc)              # stats drift is provably the only change
        self.assertIsNone(self.service.why(row, self.contract))
        b = contacts.read(self.st); contacts.owner(b).update(last_sent_at=12345, send_count=5); contacts.write(self.st, b)
        with patch.object(wm, 'ident', wm.digest):
            await self.grant()
        row = self.service.load()['grants']['story']
        contacts.record_sent(self.st, [ME])                        # owner had sends before the grant: not provable
        self.assertIsNone(self.service.why(row, self.contract))
        self.rename_owner()
        self.hold()
        self.assertEqual((await self.status())['grant']['card'], 'sent')
        self.assertEqual(await self.settle(1), 1)
        self.rename_owner()
        self.assertEqual(self.service.why(row, self.contract), 'contact_changed')
        await self.answer('allow')                                  # identity changed while the card waited
        self.assertFalse(self.service.live(self.service.load()['grants']['story'], self.contract))

    async def test_workflow_mail_revoked_or_never_granted_sends_no_card(self):
        self.assertEqual((await self.status())['grant']['reason'], 'never_granted')
        await self.grant()
        await self.service.control({'op': 'revoke', 'workflow_id': 'story'}, None)
        self.ask.reset_mock()
        res = (await self.status())['grant']
        self.assertEqual((res['reason'], res['card']), ('revoked', None))
        self.ask.assert_not_awaited()

    async def test_workflow_mail_cli_prints_reason(self):
        import argparse, contextlib, io
        from agentj import mail
        p = argparse.ArgumentParser(); mail.add_parser(p.add_subparsers(dest='command'))
        args = p.parse_args(['mail', 'workflow-status', '--workflow', 'story']); args.json = True
        await self.grant(); self.rename_owner()
        res = await self.status()
        err = io.StringIO()
        with patch('agentj.state.State', return_value=self.st), patch('agentj.elevate.client_request', return_value=res), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mail.cmd(args), 0)
        self.assertIn('联系人记录变化', err.getvalue()); self.assertIn('已自动发授权卡', err.getvalue())
        self.assertNotIn('拍板', err.getvalue())
