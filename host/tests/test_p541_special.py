"""#541 real sealed-controller review, nonce, terminal and device isolation regressions."""
import _hermetic  # noqa: F401,I001
import asyncio
import copy
import json
import secrets
import tempfile
import time
import unittest
from types import SimpleNamespace

from agentj import approval_review as review, elevate, secret_out, wire
from test_f17 import answer, enroll
from test_l1 import _ready, _state
from _p541_device import Phone, _owner_host
from test_p73_secret import fa_out


def intent(phone, card, op, event_id=None):
    obj = {'t': 'approval_review', 'id': card['id'], 'op': op, 'revision': card['revision'],
           'envelope_sha256': card['envelope_sha256'], 'event_id': event_id or secrets.token_hex(16)}
    obj['sig'] = wire.b64u(phone.sk.sign(review.review_message(phone.channel, phone.did, card['id'], op,
        obj['revision'], obj['event_id'], obj['envelope_sha256'])))
    return obj


class Special(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st, self.sent = _state(self.tmp.name), []
        self.host = _owner_host(self.st, self.sent)
        self.a, self.b = enroll(self.st, Phone(self.st)), enroll(self.st, Phone(self.st, name='B'))
        self.sa, self.sb = _ready(self.host, self.a), _ready(self.host, self.b, cid=12)
        self.events = []
        self.host.agent.submit = self.events.append

    def messages(self, t):
        return [obj for _, obj in self.sent if obj and obj.get('t') == t]

    async def wait_card(self, kind):
        for _ in range(300):
            items = self.messages(kind)
            if items:
                return items[-1]
            await asyncio.sleep(.01)
        self.fail('missing card ' + kind)

    async def pickup(self):
        result = await self.host.elevate.out.send({'name': 'TEST_ITEM', 'purpose': '取回自己的链接',
            'kind': 'text', 'value': 'fixture-value-not-a-real-credential'})
        self.assertEqual(result['result'], 'sent')
        return self.messages('secret_out')[-1]

    def test_secret_question_reopen_only_signing_session_gets_value(self):
        async def run():
            card = await self.pickup()
            before_deadline = self.host.elevate.out.cards[card['id']]['deadline']
            q = intent(self.a, card, 'question')
            await self.host._app(self.sa, q)
            await self.host._app(self.sa, q)
            self.assertEqual(len(self.events), 1)
            self.assertNotIn('fixture-value-not-a-real-credential', self.events[0].text)
            questioned = self.messages('approval_review_result')[-1]['ask']
            self.assertEqual(questioned['review_state'], 'questioned')
            await self.host._app(self.sa, {'t': 'secret_out_open', 'id': card['id'], 'n': card['n'],
                'fa': fa_out(self.a.auth, self.a, card)})
            self.assertEqual(self.messages('secret_out_val'), [])
            await self.host._app(self.sb, intent(self.b, questioned, 'reopen'))
            reopened = self.messages('approval_review_result')[-1]['ask']
            self.assertEqual(reopened['id'], card['id'])
            self.assertNotEqual(reopened['n'], card['n'])
            self.assertEqual(self.host.elevate.out.cards[card['id']]['deadline'], before_deadline)
            await self.host._app(self.sb, {'t': 'secret_out_open', 'id': card['id'], 'n': reopened['n'],
                'fa': fa_out(self.b.auth, self.b, reopened)})
            vals = [(cid, obj) for cid, obj in self.sent if obj and obj.get('t') == 'secret_out_val']
            self.assertEqual([cid for cid, _ in vals], [self.sb.cid])
            self.assertEqual(self.host.elevate.out.cards, {})
            await self.host._app(self.sa, intent(self.a, reopened, 'reopen'))
            self.assertFalse(self.messages('approval_review_result')[-1]['ok'])
            await asyncio.sleep(.02)
        asyncio.run(run())

    def test_secret_signature_covers_source_and_refuses_v1(self):
        async def run():
            card = await self.pickup()
            forged = copy.deepcopy(card)
            forged['envelope']['source']['text'] = 'forged owner task'
            forged['envelope_sha256'] = review.digest(forged['envelope'])
            await self.host._app(self.sb, {'t': 'secret_out_open', 'id': card['id'], 'n': card['n'],
                'fa': fa_out(self.b.auth, self.b, forged)})
            legacy = {key: value for key, value in card.items() if key != 'envelope_sha256'}
            await self.host._app(self.sa, {'t': 'secret_out_open', 'id': card['id'], 'n': card['n'],
                'fa': fa_out(self.a.auth, self.a, legacy)})
            self.assertEqual(self.messages('secret_out_val'), [])
            self.host.elevate.out.end_all('stopped')
            await asyncio.sleep(.02)
        asyncio.run(run())

    def test_unknown_delivery_wipes_and_never_transfers(self):
        async def run(raises):
            card = await self.pickup()
            c = self.host.elevate.out.cards[card['id']]
            original = self.host.send_app
            async def failed(session, obj):
                if obj['t'] == 'secret_out_val':
                    self.assertEqual(session.device, self.b.did)
                    if raises:
                        raise ConnectionError('synthetic transport failure')
                    return False
                return await original(session, obj)
            self.host.send_app = failed
            await self.host._app(self.sb, {'t': 'secret_out_open', 'id': card['id'], 'n': card['n'],
                'fa': fa_out(self.b.auth, self.b, card)})
            self.host.send_app = original
            self.assertEqual(bytes(c['value']), b'')
            self.assertEqual(c['review_state'], 'unknown')
            await self.host._app(self.sa, {'t': 'secret_out_open', 'id': card['id'], 'n': card['n'],
                'fa': fa_out(self.a.auth, self.a, card)})
            self.assertEqual(self.messages('secret_out_val'), [])
            await asyncio.sleep(.02)
        for raises in (False, True):
            asyncio.run(run(raises))

    def test_secret_deny_terminal_emits_one_safe_main_thread_event(self):
        async def run():
            card = await self.pickup()
            await self.host._app(self.sa, intent(self.a, card, 'deny'))
            await self.host._app(self.sa, intent(self.a, card, 'deny'))
            self.assertEqual(len(self.events), 1)
            self.assertIn('主人拒绝', self.events[0].text)
            self.assertNotIn('fixture-value-not-a-real-credential', self.events[0].text)
            await self.host._app(self.sb, {'t': 'secret_out_open', 'id': card['id'], 'n': card['n'],
                'fa': fa_out(self.b.auth, self.b, card)})
            self.assertEqual(self.messages('secret_out_val'), [])
            await asyncio.sleep(.02)
        asyncio.run(run())

    def test_old_decline_cannot_cancel_new_revision(self):
        async def run():
            card=await self.pickup()
            old=intent(self.a,card,'deny')
            await self.host._app(self.sa,intent(self.a,card,'question'))
            adapter=self.host._approval_special[card['id']]
            new=adapter.render(self.sa)
            await self.host._app(self.sa,old)
            self.assertIn(card['id'],self.host.elevate.out.cards)
            await self.host._app(self.sa,{'t':'secret_out_decline','id':card['id']})
            self.assertIn(card['id'],self.host.elevate.out.cards)
            await self.host._app(self.sa,intent(self.a,new,'deny'))
            self.assertNotIn(card['id'],self.host.elevate.out.cards)
            await asyncio.sleep(.02)
        asyncio.run(run())

    def test_admin_question_releases_caller_then_executes_once_after_fresh_approval(self):
        async def run():
            calls = []
            async def execute(c, session, obj, signed, nonce, priv):
                calls.append(c['id'])
                c['busy'] = False
                self.host.elevate._finish(c, {'result': 'done', 'code': 0, 'stdout': 'ok', 'stderr': ''})
            self.host.elevate._execute = execute
            spec = elevate.norm_sudo({'argv': ['/usr/bin/true'], 'why': '核对管理员操作'})
            task = asyncio.create_task(self.host.elevate.request(spec))
            card = await self.wait_card('elev')
            q = intent(self.a, card, 'question')
            await self.host._app(self.sa, q)
            result = await asyncio.wait_for(task, 1)
            self.assertEqual(result['result'], 'questioned')
            self.assertEqual(calls, [])
            self.assertIn(card['id'], self.host.elevate.cards)
            await self.host._app(self.sa, answer(self.a, card, True, 'synthetic-password'))
            self.assertEqual(calls, [])
            questioned = self.messages('approval_review_result')[-1]['ask']
            await self.host._app(self.sb, intent(self.b, questioned, 'reopen'))
            reopened = self.messages('approval_review_result')[-1]['ask']
            approved = answer(self.b, reopened, True, 'synthetic-password')
            await self.host._app(self.sb, approved)
            await self.host._app(self.sb, approved)
            await asyncio.sleep(.05)
            self.assertEqual(calls, [card['id']])
            repeated = await self.host.elevate.request(spec)
            self.assertEqual(repeated['result'], 'done')
            self.assertTrue(repeated['already_requested'])
            self.assertEqual(calls, [card['id']])
            self.assertEqual(len(self.events), 1)
            await self.host.elevate.stop()
        asyncio.run(run())

    def test_secret_input_question_then_deny_wins_and_writes_nothing(self):
        async def run():
            from pathlib import Path
            dest = Path(self.tmp.name) / 'private-output.txt'
            spec = elevate.norm_secret({'name': 'TEST_ONLY', 'purpose': '测试输入', 'dest': str(dest)}, self.st.root)
            task = asyncio.create_task(self.host.elevate.request(spec))
            card = await self.wait_card('elev')
            await self.host._app(self.sa, intent(self.a, card, 'question'))
            self.assertEqual((await asyncio.wait_for(task, 1))['result'], 'questioned')
            fresh = self.messages('approval_review_result')[-1]['ask']
            await asyncio.gather(self.host._app(self.sa, answer(self.a, fresh, False)),
                self.host._app(self.sb, answer(self.b, fresh, True, 'synthetic-input-value')))
            await asyncio.sleep(.03)
            self.assertFalse(dest.exists())
            self.assertEqual(self.host.elevate.cards, {})
            self.assertEqual(len(self.events), 2)
            self.assertIn('主人拒绝', self.events[-1].text)
            self.assertNotIn('synthetic-input-value', self.events[-1].text)
            await self.host._app(self.sb, intent(self.b, fresh, 'reopen'))
            self.assertFalse(self.messages('approval_review_result')[-1]['ok'])
            await self.host.elevate.stop()
        asyncio.run(run())

    def test_pickup_during_delivery_cannot_be_denied_or_transferred(self):
        async def run():
            card = await self.pickup()
            original = self.host.send_app
            entered, release = asyncio.Event(), asyncio.Event()
            async def hold(session, obj):
                if obj['t'] == 'secret_out_val':
                    entered.set()
                    await release.wait()
                return await original(session, obj)
            self.host.send_app = hold
            task = asyncio.create_task(self.host._app(self.sb, {'t': 'secret_out_open', 'id': card['id'],
                'n': card['n'], 'fa': fa_out(self.b.auth, self.b, card)}))
            await asyncio.wait_for(entered.wait(), 1)
            await self.host._app(self.sa, intent(self.a, card, 'deny'))
            await self.host._app(self.sa, intent(self.a, card, 'question'))
            self.assertEqual(self.events, [])
            self.assertEqual(self.host.elevate.out.cards[card['id']]['review_state'], 'executing')
            release.set()
            await task
            vals = [(cid, obj) for cid, obj in self.sent if obj and obj.get('t') == 'secret_out_val']
            self.assertEqual([cid for cid, _ in vals], [self.sb.cid])
            self.assertEqual(self.host.elevate.out.cards, {})
            await asyncio.sleep(.02)
        asyncio.run(run())

    def test_expired_secret_pickup_renew_only_requests_fresh_owner_task(self):
        async def run():
            card = await self.pickup()
            c = self.host.elevate.out.cards[card['id']]
            self.host.elevate.out._end(c, 'expired')
            request = intent(self.a, card, 'renew')
            await self.host._app(self.sa, request)
            await self.host._app(self.sa, request)
            self.assertEqual(len(self.events), 1)
            self.assertIn('确认从未执行', self.events[0].text)
            self.assertEqual(self.host.elevate.out.cards, {})
            self.assertEqual(bytes(c['value']), b'')
            self.assertEqual(c['review_state'], 'expired')
            self.assertEqual(self.messages('secret_out_val'), [])
            await asyncio.sleep(.02)
        asyncio.run(run())

    def test_login_and_vshare_review_preserve_live_phone_waiter(self):
        async def run(kind):
            calls = []
            spec = ({'kind': 'login', 'term': 'test-terminal', 'only': self.a.did,
                     'sink': lambda *args: calls.append(True), 'cwd': '/'} if kind == 'login' else
                    {'kind': 'vshare', 'dev': self.a.did, 'name': 'test requester',
                     'cred': 'public-test-credential-id', 'paired': 1, 'flags': [],
                     'only': [self.a.did], 'sink': lambda *args: calls.append(True)})
            self.sent.clear()
            task = asyncio.create_task(self.host.elevate.request(spec))
            card = await self.wait_card('elev')
            await self.host._app(self.sb, intent(self.b, card, 'question'))
            self.assertFalse(self.messages('approval_review_result')[-1]['ok'])
            await self.host._app(self.sa, intent(self.a, card, 'question'))
            await asyncio.sleep(.02)
            self.assertFalse(task.done(), 'phone request retains its PRF/sink lifetime; main Agent is not this waiter')
            questioned = self.messages('approval_review_result')[-1]['ask']
            await self.host._app(self.sa, intent(self.a, questioned, 'reopen'))
            reopened = self.messages('approval_review_result')[-1]['ask']
            self.assertEqual(reopened['id'], card['id'])
            self.assertNotEqual(reopened['n'], card['n'])
            self.assertEqual(calls, [])
            await self.host._app(self.sa, answer(self.a, reopened, False))
            self.assertEqual((await asyncio.wait_for(task, 1))['result'], 'denied')
            self.assertEqual(calls, [])
        async def both():
            await run('login')
            await run('vshare')
            await self.host.elevate.stop()
        asyncio.run(both())

    def test_admin_signed_source_tamper_and_v1_downgrade_never_execute(self):
        async def run():
            calls = []
            async def execute(*args):
                calls.append(True)
            self.host.elevate._execute = execute
            spec = elevate.norm_sudo({'argv': ['/usr/bin/true'], 'why': '测试签名绑定'})
            task = asyncio.create_task(self.host.elevate.request(spec))
            card = await self.wait_card('elev')
            forged = copy.deepcopy(card)
            forged['envelope']['action'] = 'altered action'
            forged['envelope']['source']['text'] = 'altered origin'
            forged['envelope_sha256'] = review.digest(forged['envelope'])
            await self.host._app(self.sa, answer(self.a, forged, True, 'synthetic-password'))
            legacy = {key: value for key, value in card.items() if key != 'envelope_sha256'}
            await self.host._app(self.sb, answer(self.b, legacy, True, 'synthetic-password'))
            self.assertEqual(calls, [])
            self.assertFalse(task.done())
            await self.host._app(self.sa, answer(self.a, card, False))
            self.assertEqual((await task)['result'], 'denied')
            self.assertEqual(calls, [])
            await self.host.elevate.stop()
        asyncio.run(run())
