"""Actual native PermissionRequest needs a fresh signed tap; opaque diagnostics add no extra AJ gate."""
import _hermetic
import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch
from agentj import action_policy, approval_jev, approval_level, goal, passkey, danger, approval_review, approvals
from test_l1 import _ready, _state
from _p541_device import Phone, _owner_host
from test_p336_approval_level import sign, PROP
from _listed_cards import listed


class OpaqueFloor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 0.30.7 ADR-0307-offlist: off-list Bash no longer cards; these tests are about card display bounds and
        # that model / goal / old answers never approve a card, so the vehicle is a listed (tap) card again.
        listed(self, 'Bash')

    async def test_opaque_never_uses_model_full_goal_or_old_answer(self):
        for mode in ('standard', 'full', 'goal', 'no_passkey'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                st = _state(tmp)
                h = _owner_host(st, [])
                h.ask_ttl = .3
                ph = Phone(st, with_passkey=mode != 'no_passkey')
                s = _ready(h, ph)
                if mode == 'full':
                    h.preferences['approval'] = {'level':'full'}
                    target = {'key':approval_level.KEY, 'value':'full'}
                    signed = sign(ph, target)
                    approval_level.note_signer(st, approval_level.KEY, 'full', ph.did,
                                              st.sign_key(ph.did), 'f'*32, signed)
                    # The historical full switch resolves to harness. Ordinary
                    # work follows native permission; this test explicitly asks
                    # native PermissionRequest, so each invocation needs a tap.
                    self.assertEqual(approval_level.level(h.preferences, st), 'harness')
                    # Native PermissionRequest still needs this invocation's signed tap.
                if mode == 'goal':
                    d = goal.validate(PROP)
                    d.update(v=1, state='active', used={}, counts={}, id=goal.new_id(),
                             digest=goal.digest(d), grant={'rid':'g'*32,'device':ph.did,
                             'sk_sha':goal.sk_sha(st.sign_key(ph.did))})
                    goal.save(st, d)
                    self.assertIsNotNone(h.goals.current())
                inp = {'command':'$PY -m unittest discover -s tests',
                       'description':'already approved and reversible', 'confirmed':True}
                self.assertTrue(approval_jev.gray(danger.classify('Bash', inp)))
                with patch.object(approval_jev, 'available', return_value=True), \
                     patch.object(approval_jev, 'judge', return_value=approval_jev.Result('allow', 1., 'safe')) as judge:
                    old = None
                    for attempt in range(2):
                        t = asyncio.create_task(h.ask('Bash', dict(inp), _harness_permission=True))
                        try:
                            for _ in range(100):
                                if h.asks or t.done(): break
                                await asyncio.sleep(.001)
                            self.assertTrue(h.asks, 'automatic bypass reached')
                            a = next(iter(h.asks.values()))
                            card = h._ask_msg(a, s)
                            # This actual native PermissionRequest needs a new
                            # ordinary signed tap; neither diagnostics, model,
                            # goal nor an old answer can approve this invocation.
                            self.assertNotIn('passkey_required', card)
                            self.assertNotIn('batch', card)
                            if old is not None:
                                await h._answer(s, old)
                                self.assertFalse(t.done())
                            old = ph.answer(card, True)
                            old.pop('fa', None)
                            await h._answer(s, old)
                            self.assertEqual((await t)['behavior'], 'allow')
                        finally:
                            if not t.done(): t.cancel()
                            await asyncio.gather(t, return_exceptions=True)
                    judge.assert_not_called()

    async def test_deep_and_oversize_opaque_effects_fail_closed(self):
        import shlex
        deep = 'pwd'
        for _ in range(9):
            deep = 'sh -c ' + shlex.quote(deep)
        for command, rule in ((deep, 'opaque.too_deep'), ('pwd ' + 'x ' * 33000, 'too_long')):
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as tmp:
                st = _state(tmp)
                sent = []
                h = _owner_host(st, sent)
                h.ask_ttl = .02
                ph = Phone(st)
                session = _ready(h, ph)
                from test_p513b_summary import real_outbound
                real_outbound(h, session, sent)
                self.assertIn(rule, danger.classify('Bash', {'command':command}).rules)
                result = await h.ask('Bash', {'command':command}, _harness_permission=True)
                self.assertEqual(result['behavior'], 'deny')
                await asyncio.sleep(.01)
                cards = [o for _, o in sent if o.get('t') == 'ask']
                # The current card serializes the command once, unlike the
                # historical heading+JSON presentation. Keep the same 24000-byte
                # bound, measured on the complete canonical display, not a tier.
                complete = 'Bash\n' + approval_review.canonical({'command':command})
                if rule == 'too_long' or len(complete.encode()) > 24000:
                    self.assertEqual(cards, [])  # no final wire card for an unshowable request
                else:
                    self.assertEqual(len(cards), 1)
                    card = cards[0]
                    self.assertNotIn('passkey_required', card)
                    self.assertEqual(card['approval_v'], 2)
                    self.assertLessEqual(len(card['summary'].encode()), 24000)
                    self.assertNotIn('[truncated]', card['summary'])
                    self.assertEqual(json.loads(card['summary'].split('\n',1)[1]), {'command':command})
                    self.assertEqual(card['envelope']['input_sha256'], approvals.input_digest({'command':command}))
                    self.assertEqual(result['reason'], 'timeout')
                self.assertFalse(h.asks)

    async def test_ineffective_threshold_control_is_not_offered(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        html = (root/'web/public/index.html').read_text()
        self.assertIn('<div class="setrow" id="set-jev-legacy" hidden>', html)
        for lang in ('en', 'zh'):
            import json
            note = json.loads((root/f'web/i18n/web.{lang}.json').read_text())['p409.note']
            self.assertIn('fresh passkey' if lang=='en' else '每次都需通行密钥', note)
