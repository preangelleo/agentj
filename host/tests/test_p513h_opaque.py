"""Opaque permissions always get a fresh signed card (P573: one button, no Face ID), including Full/goal/Jev bypass attempts."""
import _hermetic
import asyncio
import tempfile
import unittest
from unittest.mock import patch
from agentj import action_policy, approval_jev, approval_level, goal, passkey
from test_l1 import Phone, _owner_host, _ready, _state
from test_p336_approval_level import sign, PROP


class OpaqueFloor(unittest.IsolatedAsyncioTestCase):
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
                    # P552 (chairman 10-26): a signed released full is in force as 减少弹窗 (`harness`), where opaque commands
                    # follow the harness (P550/P551, test_p513i); the fresh-UV floor below is the other levels' invariant
                    self.assertEqual(approval_level.level(h.preferences, st), 'harness')
                    continue
                if mode == 'goal':
                    d = goal.validate(PROP)
                    d.update(v=1, state='active', used={}, counts={}, id=goal.new_id(),
                             digest=goal.digest(d), grant={'rid':'g'*32,'device':ph.did,
                             'sk_sha':goal.sk_sha(st.sign_key(ph.did))})
                    goal.save(st, d)
                    self.assertIsNotNone(h.goals.current())
                inp = {'command':'$PY -m unittest discover -s tests',
                       'description':'already approved and reversible', 'confirmed':True}
                self.assertTrue(approval_jev.gray(h._classify('Bash', inp)))
                with patch.object(approval_jev, 'available', return_value=True), \
                     patch.object(approval_jev, 'judge', return_value=approval_jev.Result('allow', 1., 'safe')) as judge:
                    old = None
                    for attempt in range(2):
                        t = asyncio.create_task(h.ask('Bash', dict(inp)))
                        try:
                            for _ in range(100):
                                if h.asks or t.done(): break
                                await asyncio.sleep(.001)
                            self.assertTrue(h.asks, 'automatic bypass reached')
                            a = next(iter(h.asks.values()))
                            card = h._ask_msg(a, s)
                            # P573 (#386 ⑤): a command Jev cannot read is one button (was Face ID) — still always a card,
                            # never a model / goal / old-answer bypass
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
                _ready(h, ph)
                self.assertIn(rule, h._classify('Bash', {'command':command}).rules)
                result = await h.ask('Bash', {'command':command})
                self.assertEqual(result['behavior'], 'deny')
                await asyncio.sleep(.01)
                cards = [o for _, o in sent if o.get('t') == 'ask']
                if rule == 'too_long' or len(action_policy.summary('Bash', {'command':command}).encode()) > 24000:
                    self.assertEqual(cards, [])  # complete effect cannot fit; never send a truncated card
                else:
                    self.assertEqual(len(cards), 1)
                    self.assertTrue(cards[0]['passkey_required'])
                    self.assertIn("Request SHA-256:", cards[0]['summary'])

    async def test_ineffective_threshold_control_is_not_offered(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        html = (root/'web/public/index.html').read_text()
        self.assertIn('<div class="setrow" id="set-jev-legacy" hidden>', html)
        for lang in ('en', 'zh'):
            import json
            note = json.loads((root/f'web/i18n/web.{lang}.json').read_text())['p409.note']
            self.assertIn('fresh passkey' if lang=='en' else '每次都需通行密钥', note)
