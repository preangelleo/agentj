"""0304-frozen security implementation review r1 (reports/qa/0304-frozen/security-impl-r1.md): M1, M2, L1–L4, L7 and the
QA scene 3 card front. Real serve.Host + PeerService over FakeNet for Same owner, real Ed25519 controls, real WebAuthn
registration / assertions (test_p57 Authenticator), the real list_approval path for the card envelope, the real elevate
special card for L7. Synthetic @example.com / example.org addresses only.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import os
import pathlib
import secrets
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentj import automation_grant as AG, fence, peer_service, tasks, wire
from agentj.state import State
from test_l1 import Phone, _host, _ready
from test_p71_peer_service import FakeNet, FakeSessions
from test_0304_any_signer import TwoHosts
from test_0304_automation_grant import ALLY, Base
from _p0304_cards import LiveCards

ORG = "example.org"


# ------------------------------------------------------------------ M1: legacy (roster-less) Same owner relations
class Legacy(TwoHosts):
    """A and B where no remote has a passkey: Same owner forms through the legacy v2 ceremony (Ed signatures only)."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-0304-m1-')
        self.addCleanup(self.tmp.cleanup)
        scope = patch.dict(os.environ, {peer_service.TEST_CERT_ENV: wire.b64u(os.urandom(32))})
        scope.start()
        self.addCleanup(scope.stop)
        self.A = await self.plain('A', ['p1', 'p2'])
        self.B = await self.plain('B', ['q1'])
        for x, y in ((self.A, self.B), (self.B, self.A)):
            x.svc.store.add_friend(y.id, wire.b64u(y.svc.keys.x_pub), wire.b64u(y.svc.keys.ed_pub), y.svc.keys.mbox)
        self.pos = {'A': 0, 'B': 0}
        self.notices = {'A': [], 'B': []}
        for side in (self.A, self.B):
            original = side.svc.xfer._owner_notice
            def record(text, fid=None, tid=None, side=side, original=original):
                self.notices[side.name].append(text)
                return original(text, fid, tid)
            side.svc.xfer._owner_notice = record

    async def plain(self, name, phones):
        st = State(pathlib.Path(self.tmp.name) / name)
        st.init(relay='ws://127.0.0.1:9')
        sent = []
        h = _host(st, sent)
        svc = peer_service.PeerService(h, sessions=FakeSessions(), net_factory=FakeNet)
        h.peers = svc
        await svc.start()
        self.addAsyncCleanup(svc.stop)
        from test_0304_any_signer import Side
        side = Side(name, st, h, svc)
        side.sent = sent
        original = h.peer_broadcast
        def record(obj, side=side, original=original):
            side.broadcasts.append(obj)
            return original(obj)
        h.peer_broadcast = record
        for i, n in enumerate(phones):
            ph = Phone(st, n)                      # an approval key, no passkey
            s = _ready(h, ph, cid=10 + i)
            s.p33 = True
            side.phones[n] = (ph, s)
        return side

    async def legacy(self):
        res = await self.write(self.A, 'p1', self.signed(self.A, 'p1', {'friend': self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False))
        self.assertTrue(res['ok'], res)
        await self.pump()
        target = {'friend': self.A.id, 'op': 'owner-confirm', 'value': self.B.svc.same_owner.challenge(self.A.id)}
        res = await self.write(self.B, 'q1', self.signed(self.B, 'q1', target, uv=False))
        self.assertTrue(res['ok'], res)
        await self.pump()
        for side in (self.A, self.B):
            fid = self.other(side).id
            self.assertTrue(side.svc.same_owner.active(fid), side.name)
            self.assertNotEqual(side.svc.same_owner.row(fid).get('roster_v'), 3, 'a legacy (roster-less) relation')

    async def test_legacy_relation_ends_when_its_consenting_remote_is_revoked(self):
        await self.legacy()
        so = self.A.svc.same_owner
        self.assertEqual(so.row(self.B.id)['proof']['device'], self.A.phones['p1'][0].did, 'p1 consented on A')
        await self.A.h.revoke(self.A.phones['p2'][0].did)          # another remote: nothing changes
        self.assertTrue(so.active(self.B.id))
        self.assertEqual(self.notices['A'], [])
        await self.A.h.revoke(self.A.phones['p1'][0].did)          # the remote that started + consented on A
        self.assertFalse(so.active(self.B.id), 'only this host\'s state matters: the relation ends here')
        self.assertEqual(so.row(self.B.id)['end'][0], 'ended')
        self.assertEqual(len(self.notices['A']), 1)
        text = self.notices['A'][0]
        name = self.A.svc.store.name_of(self.B.id)
        self.assertIn(name, text)                                   # which friend
        self.assertIn('旧式', text)                                 # why
        self.assertIn('重新选择「同一个主人」', text)                # how to re-add
        await self.pump()
        self.assertFalse(self.B.svc.same_owner.active(self.A.id), 'the peer is told and ends it too')
        self.assertFalse(self.A.svc.xfer.pinned(self.B.id))

    async def test_first_passkey_lists_legacy_relations(self):
        await self.legacy()
        h, (ph, s) = self.A.h, self.A.phones['p2']
        from test_p57_passkey import Authenticator
        h.sessions.pop(self.A.phones['p1'][1].cid)          # the other remote is offline: a direct (first) key offer
        await h._pk_offer(s)
        offer = [o for cid, o in self.A.sent if cid == s.cid and o.get('t') == 'pk_offer'][-1]
        auth = Authenticator()
        await h._app(s, auth.register(wire.unb64u(offer['n'])))
        self.assertEqual([o for cid, o in self.A.sent if cid == s.cid and o.get('t') == 'pk_reg_res'][-1], {'t': 'pk_reg_res', 'ok': True})
        self.assertTrue(self.A.svc.same_owner.uv_host())
        self.assertEqual(len(self.notices['A']), 1, self.notices['A'])
        text = self.notices['A'][0]
        self.assertIn(self.A.svc.store.name_of(self.B.id), text)
        self.assertIn('旧式', text)
        self.assertIn('重新选择「同一个主人」', text)
        self.assertTrue(self.A.svc.same_owner.active(self.B.id), 'listed, not ended: the owner decides')
        # once per host: the first roster binding afterwards does not repeat it
        ph.auth = auth
        await self.bind(self.A, 'p2')
        self.assertEqual(len(self.notices['A']), 1)
        self.assertEqual(self.notices['B'], [], 'B has no passkey yet')



class RosterControl(TwoHosts):
    async def test_roster_relation_survives_its_confirming_remote_revoked(self):
        """Control: an A1 relation (roster on both sides) stands on the pinned roster, not on one remote."""
        await self.activate()                        # air starts, oppo confirms on A
        await self.A.h.revoke(self.A.phones['oppo'][0].did)
        self.assertTrue(self.A.svc.same_owner.active(self.B.id))


# ------------------------------------------------------------------ M2, L1–L4, L6, QA scene 3 (automation grants)
class Grants(Base):
    async def test_idna_deviation_and_nfkc_folding_refused(self):
        cases = {'straße.example': 'idna_deviation', 'ς.example': 'idna_deviation',
                 'ex‍ample.com': 'idna_deviation', 'ex‌ample.com': 'idna_deviation',
                 'ｅｘａｍｐｌｅ.com': 'nfkc_folding', 'ﬁle.example': 'nfkc_folding', 'ex­ample.com': 'nfkc_folding'}
        for raw, why in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(AG.idna_refusal(raw), why)
                self.assertEqual(AG.norm_domain(raw), '', 'never folded into another ASCII domain')
                self.assertEqual(AG.norm_addr('shop@' + raw), '')
                with patch.object(AG, 'owner_turn', return_value=True):
                    for req in ({'domains': [raw]}, {'to': ['shop@' + raw]}):
                        res = await AG.issue(self.host, {'kind': 'hook', 'ref': 'orders', 'to': [], 'domains': [], **req}, None)
                        self.assertEqual((res['ok'], res['why'], res['reason']), (False, 'automation_grant_idn_ambiguous', why), res)
                        self.assertIn('xn--', res['detail'])
        self.assertEqual(self.cards, [])
        # the exact punycode and an IDN both rule sets agree on are fine
        self.assertIsNone(AG.idna_refusal('xn--strae-oqa.example'))
        self.assertIsNone(AG.idna_refusal('bücher.example'))
        self.assertEqual(AG.norm_domain('bücher.example'), 'xn--bcher-kva.example')
        self.assertTrue((await self.grant(to=[], domains=['xn--strae-oqa.example']))['ok'])
        # a recipient typed with a deviation character never matches the grant either
        self.hook_turn()
        res = await self.send(['shop@straße.example'])
        self.assertFalse(res['ok'])

    async def test_hook_turn_cannot_revoke_or_list_addresses(self):
        g = await self.grant(domains=[ORG])
        self.hook_turn()                                     # a webhook turn calls the CLI over elevate.sock
        res = await AG.control(self.host, {'op': 'list'}, 4242)
        self.assertTrue(res['ok'] and res['counts_only'], res)
        self.assertEqual((res['count'], res['states'], res['items']), (1, {'live': 1}, []))
        self.assertNotIn(ALLY, json.dumps(res))
        self.assertNotIn(ORG, json.dumps(res))
        res = await AG.control(self.host, {'op': 'revoke', 'id': g['grant']}, 4242)
        self.assertEqual((res['ok'], res['why']), (False, 'automation_grant_owner_turn'))
        self.assertNotIn('items', res)
        self.assertFalse(self.ledger()['grants'][g['grant']].get('revoked'), 'the grant still stands')
        # the owner's own turn lists addresses and revokes
        self.owner_turn()
        with patch.object(AG, 'owner_turn', return_value=True):
            res = await AG.control(self.host, {'op': 'list'}, None)
            self.assertEqual(res['items'][0]['to'], [ALLY])
            res = await AG.control(self.host, {'op': 'revoke', 'id': g['grant']}, None)
        self.assertTrue(res['ok'], res)
        self.assertTrue(self.ledger()['grants'][g['grant']]['revoked'])

    def test_hook_caller_born_just_before_span_refused(self):
        self.caller.stop()
        self.addCleanup(self.caller.start)
        from agentj.core import process_evidence as pe
        rows = {100: (1, 10.0, 't'), 200: (100, 39.97, 't'), 300: (100, 40.2, 't')}
        self.host.agent = SimpleNamespace(session={'pid': 100}, cur_send=None)
        self.host._nonowner_spans = [[40.0, None]]
        with patch.object(pe, 'row', side_effect=lambda pid: rows.get(pid)):
            self.assertFalse(AG.hook_caller(self.host, 200), 'born 30 ms before the span: refused')
            self.assertTrue(AG.hook_caller(self.host, 300))

    async def test_task_delete_and_identical_recreate_does_not_revive_grant(self):
        auto = {'kind': 'task', 'id': 'digest', 'digest': 'a' * 64, 'name': 'digest'}
        with patch.object(AG, 'owner_turn', return_value=True):
            g = await AG.issue(self.host, {'kind': 'task', 'ref': 'digest', 'to': [ALLY]}, None)
        self.assertTrue(g['ok'], g)
        self.assertTrue(AG.check(self.host, auto, [ALLY])['ok'])
        # present but invalid (mid-edit) is not "deleted": only stale, nothing revoked
        self.task['problems'] = ['task.json is not valid UTF-8 JSON']
        self.assertEqual(AG.items(self.host)[0]['state'], 'stale')
        self.assertFalse(self.ledger()['grants'][g['grant']].get('revoked'))
        self.task['problems'] = []
        gone = {'on': True}
        real_find = tasks.find
        def find(wd, tid):
            if gone['on'] or tid != 'digest':
                raise tasks.TaskError('unknown')
            return self.task
        with patch('agentj.tasks.find', side_effect=find):
            res = AG.check(self.host, auto, [ALLY])               # deleted → revoked for good
            self.assertFalse(res['ok'])
            row = self.ledger()['grants'][g['grant']]
            self.assertEqual((row['revoked'], row['revoked_why']), (True, 'deleted'))
            gone['on'] = False                                   # re-created byte for byte: same tsha
            res = AG.check(self.host, auto, [ALLY])
            self.assertEqual(res['why'], 'automation_grant_required', res)
            self.assertEqual(AG.consume(self.host, auto, g['grant'], [ALLY]), 'automation_grant_required')
        self.assertIs(tasks.find, real_find)
        # the scheduler's sweep (ids it just discovered) revokes a newer grant whose task vanished between checks
        with patch.object(AG, 'owner_turn', return_value=True):
            g2 = await AG.issue(self.host, {'kind': 'task', 'ref': 'digest', 'to': [ALLY]}, None)
        self.assertTrue(g2['ok'], g2)
        with patch('agentj.tasks.find', side_effect=tasks.TaskError('unknown')):
            self.assertEqual(AG.sweep_tasks(self.host, set()), 1)
        self.assertTrue(self.ledger()['grants'][g2['grant']]['revoked'])
        self.assertFalse(AG.check(self.host, auto, [ALLY])['ok'])

    def test_grant_ledger_is_inside_the_fence_guard(self):
        """L4 (accepted residual): revocation / epoch / uses are unsigned ledger state, like workflow_mail's — they rely on
        the fence keeping the state dir out of the Agent's reach (hidden by a tmpfs, refused for host writes)."""
        path = str(self.st.root / AG.FILE)
        self.assertEqual(fence.host_write_refusal(self.st, path, home=str(self.work.parent)), 'guarded')
        root = fence._real(str(self.st.root))
        self.assertEqual(fence.guard_parts(self.st, str(self.work.parent), service=False)['root'], root)

    def test_card_front_and_zh_labels(self):
        core = {'id': 'f' * 32, 'host': 'ch', 'target': {'kind': 'hook', 'id': '_hookid', 'digest': '0123456789abcdef' * 4},
                'name': 'orders', 'actions': ['mail'], 'to': [ALLY], 'domains': [ORG],
                'account': {'address': 'owner@example.com', 'smtp': 'smtp.example.com:465', 'user': 'u' * 16},
                'stop': 0, 'expires': None, 'max_uses': None, 'created': 1, 'lang': 'zh'}
        f = AG.front(core)
        self.assertEqual(f['reason'], f'自动发邮件给 {ALLY}、@{ORG}（长期有效）· 来源 hook「orders」 · 能触发这个 hook 的人都能让 Agent 发信'
                                      ' · 指纹 0123456789ab')
        self.assertEqual(f['reason_en'], f'Mail {ALLY}, @{ORG} automatically (no expiry) · from webhook “orders” · anyone who '
                                         'can trigger this hook can make the Agent send · fingerprint 0123456789ab')
        self.assertEqual((f['title'], f['title_en']), ('授权自动化对外发信', 'Allow automated mail'))
        self.assertLessEqual(len(f['title_en']), 20)
        text = AG.shown(core)
        self.assertNotIn('Grant:', text)
        self.assertNotIn('\nSHA256:', text)
        self.assertTrue(text.endswith('校验摘要 SHA256：' + AG.digest(core)))
        self.assertIn('自动化指纹：' + core['target']['digest'], text, 'the full fingerprint stays in the details')
        en = AG.shown({**core, 'lang': 'en'})
        self.assertIn('\nGrant: ' + core['id'] + '\nSHA256: ', en)
        # a dated grant: local time with the zone frozen at issuance (not ISO UTC); a task; attachments; a use limit
        dated = {**core, 'target': {'kind': 'task', 'id': 'weekly', 'digest': 'b' * 64}, 'actions': ['mail', 'mail+files'],
                 'to': [ALLY, 'b@example.com', 'c@example.com', 'd@example.com'], 'domains': [],
                 'expires': 1796950800, 'tz': ['JST', 32400], 'max_uses': 5}
        self.assertTrue(AG.core_ok(dated))
        self.assertIn('到期时间：2026-12-11 10:00 JST (UTC+09:00)', AG.shown(dated))
        f = AG.front(dated)
        self.assertEqual(f['reason'], f'自动发邮件给 {ALLY}、b@example.com、c@example.com 等 4 个（可带附件）'
                                      '（到 2026-12-11 10:00 JST (UTC+09:00)，最多 5 次）· 来源 定时任务 weekly · 每次运行都能发信，'
                                      '你不会逐封确认 · 指纹 bbbbbbbbbbbb')
        self.assertFalse(AG.core_ok({**dated, 'tz': ['JST', 99 * 3600]}))
        self.assertEqual(AG.when(1796950800, None), '2026-12-11T01:00:00+00:00')



class Envelope(LiveCards):
    async def test_card_front_reaches_the_signed_envelope(self):
        """The real host.ask → list_approval → approval_review.bind path puts the front into the signed envelope; a
        non-host-summary tool cannot carry one."""
        from agentj import approval_review
        core = {'id': 'e' * 32, 'host': 'ch', 'target': {'kind': 'hook', 'id': '_hookid', 'digest': '9' * 64},
                'name': 'orders', 'actions': ['mail'], 'to': [ALLY], 'domains': [], 'stop': 0, 'expires': None,
                'account': {'address': 'owner@example.com', 'smtp': 'smtp.example.com:465', 'user': 'u' * 16},
                'max_uses': None, 'created': 1, 'lang': 'zh'}
        h = self.h
        for tool, inp, front, expect in ((AG.TOOL, {'summary': AG.shown(core)}, AG.front(core), True),
                                         ('Bash', {'command': 'echo synthetic'}, {'reason': 'forged'}, False)):
            task = asyncio.create_task(h.ask(tool, inp, batch=False, grant_info=True, always_ask=True,
                                             require_passkey=True, _front=front))
            for _ in range(200):
                if h.asks or task.done():
                    break
                await asyncio.sleep(.005)
            self.assertFalse(task.done(), task.result() if task.done() else '')
            a = next(iter(h.asks.values()))
            env = a.envelope
            if expect:
                self.assertEqual(env['reason'], front['reason'])
                self.assertEqual(env['reason_en'], front['reason_en'])
                self.assertEqual((env['title'], env['title_en']), ('授权自动化对外发信', 'Allow automated mail'))
                self.assertEqual(env['action'], AG.TOOL, 'proof_ok still binds the action to the tool')
                self.assertEqual(env['details'], AG.shown(core))
            else:
                self.assertNotIn('reason', env)
                self.assertNotIn('title_en', env)
            self.assertEqual(a.envelope_digest, approval_review.digest(env))
            a.fut.set_result(('deny', None, None, None))
            await asyncio.gather(task, return_exceptions=True)
            h.asks.clear()


# ------------------------------------------------------------------ L7: revoke ends a real elevate special card
class SpecialRevoke(unittest.TestCase):
    def setUp(self):
        import test_p541_special as fixture
        fixture.Special.setUp(self)
        self.wait_card = fixture.Special.wait_card.__get__(self)
        self.messages = fixture.Special.messages.__get__(self)

    def test_revoke_denies_real_elevate_card_with_review_event(self):
        async def run():
            calls = []
            spec = {'kind': 'login', 'term': 'test-terminal', 'only': self.a.did,
                    'sink': lambda *args: calls.append(True), 'cwd': '/'}
            task = asyncio.create_task(self.host.elevate.request(spec))
            card = await self.wait_card('elev')
            c = self.host.elevate.cards[card['id']]
            ad = c['review_adapter']
            self.assertEqual(ad.only, self.a.did)
            await self.host.revoke(self.a.did)
            self.assertEqual((await asyncio.wait_for(task, 1))['result'], 'denied')
            self.assertTrue(getattr(ad, 'deny_event', None), 'the review deny event went first (L7)')
            self.assertEqual(ad.review_state, 'denied')
            await asyncio.sleep(.02)
            self.assertEqual(len(self.events), 1)
            self.assertIn('主人拒绝', self.events[0].text)
            self.assertEqual(calls, [])
            await self.host.elevate.stop()
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
