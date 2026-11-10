"""Hotfix 2026-11-10 (STARAY Jackson 0.30.x + chairman TB 12:49): owner-turn secret / hook-rotate cards.

1. A secret-input card asked in an owner turn must still save after that turn has ended (the CLI waits 100 s and
   tells the Agent the card stays valid; the page showed 「主机没有收下这次的回答」).
2. The card's source is the owner's turn (verified), never 「找不到是谁发起的」, when asked from an owner turn.
3. A card whose authority is really gone (Agent switched) is refused with the real reason and retired.
4. A signed deny always closes a card, even after its turn moved on.
5. Hook rotate: the card is awaited inside the caller's request (bound to the live turn, so Touch ID succeeds), and a
   card whose turn is over is superseded by a new rotate instead of 「有一张卡在等主人批准」 (deadlock).
"""
import _hermetic  # noqa: F401,I001
import asyncio
import pathlib
import secrets
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import approval_review, elevate, hook_service, wire
from test_f17 import answer, enroll
from test_l1 import _ready, _state
from _p541_device import Phone, _owner_host
from test_p366_hooks import svc


def review_intent(phone, card, op):
    eid = secrets.token_hex(16)
    obj = {'t': 'approval_review', 'id': card['id'], 'op': op, 'revision': card['revision'],
           'envelope_sha256': card['envelope_sha256'], 'event_id': eid}
    obj['sig'] = wire.b64u(phone.sk.sign(approval_review.review_message(
        phone.channel, phone.did, card['id'], op, obj['revision'], eid, obj['envelope_sha256'])))
    return obj


class SecretCardAfterTurn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = pathlib.Path(self.tmp.name)
        self.st, self.sent = _state(self.tmp.name), []
        self.host = _owner_host(self.st, self.sent)
        self.ph = enroll(self.st, Phone(self.st))
        self.s = _ready(self.host, self.ph)
        self.dest = self.d / 'proj' / '.env'
        self.dest.parent.mkdir()
        self.dest.write_text('OTHER=1\n')

    def of(self, t):
        return [o for _, o in self.sent if o and o.get('t') == t]

    async def wait_for(self, t, n=1):
        for _ in range(500):
            if len(self.of(t)) >= n:
                return self.of(t)[n - 1]
            await asyncio.sleep(0.01)
        self.fail('no ' + t)

    async def ask_card(self, origin=None):
        """The owner turn's own `agentj secret request`, origin resolved from that turn (as the elevate socket does)."""
        card = elevate.norm_secret({'name': 'ARK_API_KEY', 'purpose': '调用火山方舟', 'cwd': str(self.dest.parent),
                                    'dest': f'env:{self.dest}#ARK_API_KEY'}, self.st.root)
        origin = approval_review.snapshot(self.host) if origin is None else origin
        with approval_review.origin_scope(origin):
            task = asyncio.ensure_future(self.host.elevate.request(card))
        shown = await self.wait_for('elev')
        return task, shown, origin

    def next_owner_turn(self):
        with mock.patch.object(self.host, 'hist_emit'):
            self.host.desktop_input('A later owner message: the turn that asked for the key is over.')

    def test_owner_turn_card_source_is_verified_owner(self):
        async def run():
            task, shown, origin = await self.ask_card()
            self.assertTrue(origin.get('verified'), origin)
            src = shown['envelope']['source']
            self.assertTrue(src.get('verified'), src)
            self.assertNotEqual(src.get('text'), '找不到是谁发起的')
            await self.host._app(self.s, answer(self.ph, shown, False))
            await asyncio.wait_for(task, 5)
        asyncio.run(run())

    def test_save_after_the_turn_ended_is_accepted(self):
        value = 'ark-' + 'x' * 28                       # a fixture, not a credential
        async def run():
            task, shown, _ = await self.ask_card()
            self.next_owner_turn()                      # STARAY: the CLI returned after 100 s, the turn ended
            await self.host._app(self.s, answer(self.ph, shown, True, value))
            res = await asyncio.wait_for(task, 10)
            return res
        res = asyncio.run(run())
        self.assertEqual(res['result'], 'saved', res)
        self.assertEqual(self.of('elev_refused'), [])
        self.assertIn('ARK_API_KEY=' + value, self.dest.read_text())

    def test_agent_switched_is_refused_with_the_real_reason_and_retired(self):
        async def run():
            task, shown, _ = await self.ask_card()
            self.host.agent = SimpleNamespace(kind='claude', cur_send=None, phone_turn=False, cfg={})
            await self.host._app(self.s, answer(self.ph, shown, True, 'ark-' + 'y' * 28))
            return await asyncio.wait_for(task, 10)
        res = asyncio.run(run())
        self.assertEqual(res['result'], 'refused')
        self.assertEqual([r['why'] for r in self.of('elev_refused')], ['context'])   # not 'shape' (「没有收下」)
        self.assertEqual(self.of('elev_done')[-1]['result'], 'refused')                # the card is gone from the phone
        self.assertNotIn('ARK_API_KEY=', self.dest.read_text())

    def test_signed_deny_closes_a_card_whose_turn_moved_on(self):
        async def run():
            task, shown, _ = await self.ask_card()
            self.next_owner_turn()
            self.host.agent = SimpleNamespace(kind='claude', cur_send=None, phone_turn=False, cfg={})
            await self.host._app(self.s, review_intent(self.ph, shown, 'deny'))
            return await asyncio.wait_for(task, 10)
        res = asyncio.run(run())
        self.assertEqual(res['result'], 'denied', res)
        self.assertTrue(self.of('approval_review_result')[-1]['ok'])

    def test_unverified_card_does_not_outlive_its_turn(self):
        """Security review R1: an unknown / external-source card is not savable in the owner's next turn."""
        async def run():
            task, shown, _ = await self.ask_card(origin={})          # 「找不到是谁发起的」
            self.assertFalse(shown['envelope']['source'].get('verified'))
            self.next_owner_turn()
            await self.host._app(self.s, answer(self.ph, shown, True, 'ark-' + 'z' * 28))
            return await asyncio.wait_for(task, 10)
        res = asyncio.run(run())
        self.assertEqual(res['result'], 'refused')
        self.assertEqual([r['why'] for r in self.of('elev_refused')], ['context'])
        self.assertNotIn('ARK_API_KEY=', self.dest.read_text())

    def test_unsigned_answer_cannot_retire_a_card(self):
        """Security review S3: retirement needs this device's real signature."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        async def run():
            task, shown, _ = await self.ask_card(origin={})
            self.next_owner_turn()
            await self.host._app(self.s, answer(self.ph, shown, True, 'ark-' + 'q' * 28, sk=Ed25519PrivateKey.generate()))
            await asyncio.sleep(0.2)
            self.assertFalse(task.done())
            self.assertEqual(self.of('elev_done'), [])
            await self.host._app(self.s, answer(self.ph, shown, False))
            return await asyncio.wait_for(task, 10)
        self.assertEqual(asyncio.run(run())['result'], 'denied')

    def test_refused_word_maps_authority_to_context(self):
        self.assertEqual(elevate.refused_word('unverified_trigger'), 'context')
        self.assertEqual(elevate.refused_word('something_else'), 'shape')


class HookRotateCard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def resident(self, s):
        async def go():
            from agentj import hooks
            h = hooks.build({'preset': 'bearer', 'name': 'xrtoken-fixture', 'life': 'resident'}, 'main', s.clock())
            h['state'] = 'active'
            s.store.add(h, b'fixture-old-key-not-real')
            return h['id']
        return go

    def test_rotate_waits_for_the_card_and_applies_on_allow(self):
        host, s = svc(self.tmp.name)
        async def flow():
            hid = await self.resident(s)()
            old = s.store.keys(hid)
            host.ask.reset_mock()
            host.ask.return_value = {'behavior': 'allow'}
            rr = await s.cli({'t': 'rotate', 'id': hid, 'grace': 0, 'secret': 'fixture-new-' + 'n' * 20})
            return hid, old, rr
        hid, old, rr = asyncio.run(flow())
        self.assertTrue(rr['ok'], rr)
        self.assertNotIn('pending', rr)
        self.assertEqual(host.ask.call_args.args[0], 'agentj secret request')
        self.assertNotEqual(s.store.keys(hid), old)
        self.assertEqual(s.store.keys(hid)[0], ('fixture-new-' + 'n' * 20).encode())
        self.assertNotIn(hid, s.widening)

    def test_rotate_denied_says_so_and_frees_the_hook(self):
        host, s = svc(self.tmp.name)
        async def flow():
            hid = await self.resident(s)()
            host.ask.return_value = {'behavior': 'deny', 'reason': 'authority_changed'}
            return hid, await s.cli({'t': 'rotate', 'id': hid, 'grace': 0})
        hid, rr = asyncio.run(flow())
        self.assertEqual(rr['why'], 'not_approved')
        self.assertNotIn(hid, s.widening)

    def test_stale_card_is_superseded_not_deadlocked(self):
        host, s = svc(self.tmp.name)
        async def flow():
            hid = await self.resident(s)()
            loop = asyncio.get_running_loop()
            host.asks = {}
            host.hist = SimpleNamespace(epoch=1)
            host._approval_live_authority = lambda: 'turn-2'
            first = loop.create_future()
            started = asyncio.Event()

            async def held_ask(tool, inp, **kw):
                a = SimpleNamespace(tool=tool, tool_input=inp, fut=first, review_agent=host.agent,
                                    envelope={'session': '1'}, review_live_authority='turn-1')
                host.asks['old'] = a
                started.set()
                decision = (await first)[0]
                host.asks.pop('old', None)
                return {'behavior': decision, 'reason': getattr(a, 'withdrawn', None)}
            host.ask.side_effect = held_ask
            t1 = asyncio.ensure_future(s.cli({'t': 'rotate', 'id': hid, 'grace': 0}))
            await started.wait()
            host.ask.side_effect = None
            host.ask.return_value = {'behavior': 'allow'}
            r2 = await s.cli({'t': 'rotate', 'id': hid, 'grace': 0})
            return hid, await t1, r2, first
        hid, r1, r2, first = asyncio.run(flow())
        self.assertEqual(first.result()[0], 'deny')                 # withdrawn = a deny, never a grant
        self.assertEqual(r1['why'], 'not_approved')
        self.assertTrue(r2['ok'], r2)

    def test_parked_question_is_not_superseded(self):
        """Security review S1: the owner's 「有疑问」 thread is never cut by a new rotate."""
        host, s = svc(self.tmp.name)
        async def flow():
            hid = await self.resident(s)()
            fut = asyncio.get_running_loop().create_future()
            host.asks = {'old': SimpleNamespace(tool='agentj secret request', tool_input={'hook': hid, 'rotate': True}, fut=fut,
                                                parked=True, review_agent=None, envelope={'session': '0'},
                                                review_live_authority='turn-1')}
            host.hist = SimpleNamespace(epoch=1)
            host._approval_live_authority = lambda: 'turn-2'
            s.widening.add(hid)
            return await s.cli({'t': 'rotate', 'id': hid, 'grace': 0}), fut
        rr, fut = asyncio.run(flow())
        self.assertEqual(rr['why'], 'pending')
        self.assertFalse(fut.done())

    def test_card_in_its_live_turn_is_not_superseded(self):
        host, s = svc(self.tmp.name)
        async def flow():
            hid = await self.resident(s)()
            fut = asyncio.get_running_loop().create_future()
            host.asks = {'old': SimpleNamespace(tool='agentj secret request', tool_input={'hook': hid, 'rotate': True}, fut=fut,
                                                review_agent=host.agent, envelope={'session': '1'},
                                                review_live_authority='turn-1')}
            host.hist = SimpleNamespace(epoch=1)
            host._approval_live_authority = lambda: 'turn-1'
            s.widening.add(hid)
            return await s.cli({'t': 'rotate', 'id': hid, 'grace': 0}), fut
        rr, fut = asyncio.run(flow())
        self.assertEqual(rr['why'], 'pending')
        self.assertFalse(fut.done())


if __name__ == '__main__':
    unittest.main()
