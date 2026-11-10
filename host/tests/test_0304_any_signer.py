"""0304 A1 (chairman 11-09 11:59 / 12:01): any paired remote with a roster-bound UV passkey confirms Same owner.

Two real serve.Host + PeerService pairs over FakeNet (no relay), real Ed25519 control signatures, real WebAuthn
assertions (test_p57_passkey.Authenticator) and the real serve fr_set path (owner_uv_offer → controls.check →
PeerService.write). Contract: reports/qa/0304-frozen/THREAT-MODEL.md §A1 and contract-a1-scope-v4.md.
"""
import _hermetic  # noqa: F401
import asyncio
import json
import os
import pathlib
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch

from agentj import controls, owner_grant as og, passkey, peer_service, same_owner as so, wire
from agentj.state import State
from test_l1 import Phone, _host, _ready
from test_p71_peer_service import FakeNet, FakeSessions

HOST = pathlib.Path(__file__).resolve().parents[1]


class Side:
    def __init__(self, name, st, h, svc):
        self.name, self.st, self.h, self.svc = name, st, h, svc
        self.phones, self.broadcasts, self.sent = {}, [], []

    @property
    def id(self):
        return self.svc.keys.id


class TwoHosts(unittest.IsolatedAsyncioTestCase):
    """A = 'Laptop' (air, oppo bound; safari has a passkey but no roster binding; keyonly has no passkey),
    B = 'Desktop' (mac, pixel bound)."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aj-0304-a1-')
        self.addCleanup(self.tmp.cleanup)
        scope = patch.dict(os.environ, {peer_service.TEST_CERT_ENV: wire.b64u(os.urandom(32))})
        scope.start()
        self.addCleanup(scope.stop)
        self.A = await self.side('A', ['air', 'oppo', 'safari'], ['air', 'oppo'])
        self.B = await self.side('B', ['mac', 'pixel'], ['mac', 'pixel'])
        keyonly = Phone(self.A.st, 'keyonly')
        s = _ready(self.A.h, keyonly, cid=19)
        s.p33 = True
        self.A.phones['keyonly'] = (keyonly, s)
        for x, y in ((self.A, self.B), (self.B, self.A)):
            x.svc.store.add_friend(y.id, wire.b64u(y.svc.keys.x_pub), wire.b64u(y.svc.keys.ed_pub), y.svc.keys.mbox)
        self.pos = {'A': 0, 'B': 0}

    async def asyncTearDown(self):
        for side in (self.A, self.B):
            side.h.stopping.set()

    async def side(self, name, phones, bound):
        st = State(pathlib.Path(self.tmp.name) / name)
        st.init(relay='ws://127.0.0.1:9')
        sent = []
        h = _host(st, sent)
        svc = peer_service.PeerService(h, sessions=FakeSessions(), net_factory=FakeNet)
        h.peers = svc
        await svc.start()
        self.addAsyncCleanup(svc.stop)
        side = Side(name, st, h, svc)
        side.sent = sent
        original = h.peer_broadcast
        def record(obj, side=side, original=original):
            side.broadcasts.append(obj)
            return original(obj)
        h.peer_broadcast = record
        for i, n in enumerate(phones):
            ph = Phone(st, n, with_passkey=True)
            s = _ready(h, ph, cid=10 + i)
            s.p33 = True
            side.phones[n] = (ph, s)
        for n in bound:
            await self.bind(side, n)
        return side

    async def bind(self, side, name):
        ph, s = side.phones[name]
        e = side.svc.xfer.binding_entry(ph.did)
        fa = ph.auth.assert_(og.bind_challenge(e), b'', nonce=b'')
        sig = wire.b64u(ph.sk.sign(b'agentj-owner-roster-v1\n' + og.jcs({k: e[k] for k in ('id', 'alg', 'spki', 'rp', 'dev')})))
        await side.svc.xfer.bind_phone(s, {'t': 'roster_bind', 'fa': fa, 'roster_sig': sig})
        self.assertTrue(side.svc.xfer.bound(ph.did), name)

    def other(self, side):
        return self.B if side is self.A else self.A

    async def pump(self, drop=None):
        for _ in range(40):
            await asyncio.sleep(.005)
            moved = 0
            for src in (self.A, self.B):
                dst = self.other(src)
                rows = src.svc.net.sent[self.pos[src.name]:]
                self.pos[src.name] = len(src.svc.net.sent)
                for target, obj in rows:
                    self.assertEqual(target, dst.id)
                    obj = json.loads(json.dumps(obj))
                    if drop and drop(src, obj):
                        continue
                    dst.svc.on_app(src.id, obj)
                    moved += 1
            if not moved:
                return
        self.fail('peer transport did not settle')

    def signed(self, side, name, target, *, uv=True, n=None):
        ph, _ = side.phones[name]
        n = n or secrets.token_hex(16)
        ts = int(time.time() * 1000)
        digest = controls.object_digest('fr_set', target)
        obj = {'t': 'fr_set', 'r': secrets.token_hex(8), **target, 'n': n, 'ts': ts,
               'sig': wire.b64u(ph.sk.sign(controls.signed_message(side.h.channel, ph.did, 'fr_set', n, ts, digest)))}
        if uv and hasattr(ph, 'auth'):
            obj['fa'] = ph.auth.assert_(passkey.elevate_challenge(side.h.channel, ph.did, n, 'fr_set', n, digest), b'', nonce=b'')
        return obj

    async def write(self, side, name, obj):
        _, s = side.phones[name]
        await side.h.on_friends(s, 'fr_set', obj)
        res = next((o for cid, o in reversed(side.sent) if cid == s.cid and o.get('t') == 'ctl_res' and o.get('r') == obj['r']), None)
        self.assertIsNotNone(res, 'no ctl_res')
        return res

    def target(self, side, op='owner-confirm-v3', value=None):
        fid = self.other(side).id
        return {'friend': fid, 'op': op, 'value': side.svc.same_owner.challenge(fid) if value is None else value}

    async def start(self, side=None, name='air'):
        side = side or self.A
        fid = self.other(side).id
        res = await self.write(side, name, self.signed(side, name, {'friend': fid, 'op': 'group', 'value': 'same-owner'}, uv=False))
        self.assertTrue(res['ok'], res)
        await self.pump()
        self.assertTrue(self.A.svc.same_owner.v4(self.B.id))
        self.assertTrue(self.B.svc.same_owner.v4(self.A.id))
        self.assertEqual(self.A.svc.same_owner.code(self.B.id), self.B.svc.same_owner.code(self.A.id))

    async def confirm(self, side, name, **kw):
        res = await self.write(side, name, self.signed(side, name, self.target(side), **kw))
        await self.pump()
        return res

    async def activate(self, a='oppo', b='pixel'):
        await self.start()
        self.assertTrue((await self.confirm(self.A, a))['ok'])
        self.assertTrue((await self.confirm(self.B, b))['ok'])
        self.assertTrue(self.A.svc.same_owner.active(self.B.id))
        self.assertTrue(self.B.svc.same_owner.active(self.A.id))

    def pairing_cards(self, side):
        fid = self.other(side).id
        return [a for a in side.svc.asks.values() if a.data.get('friend') == fid and a.extra.get('pairing')]

    def gone(self, side):
        return [o['id'] for o in side.broadcasts if o.get('t') == 'ask_done' and o.get('result') == 'gone']


class AnySigner(TwoHosts):
    async def test_non_initiator_remotes_complete_v4_and_cards_fold(self):
        await self.start(self.A, 'air')
        cards = {side.name: [a.rid for a in self.pairing_cards(side)] for side in (self.A, self.B)}
        self.assertTrue(cards['A'] and cards['B'])
        card = self.A.svc.ask_msg(self.pairing_cards(self.A)[0])
        self.assertEqual(card['pq'].get('pairing_v'), 4)
        self.assertTrue(json.loads(card['pq']['pairing_scope'])[0] == so.SCOPE_V4)
        # oppo (not the starter) on A, pixel (not the most recently seen) on B
        self.assertTrue((await self.confirm(self.A, 'oppo'))['ok'])
        self.assertTrue((await self.confirm(self.B, 'pixel'))['ok'])
        self.assertTrue(self.A.svc.same_owner.active(self.B.id))
        self.assertTrue(self.B.svc.same_owner.active(self.A.id))
        for side in (self.A, self.B):
            self.assertFalse(self.pairing_cards(side))
            self.assertTrue(set(cards[side.name]) <= set(self.gone(side)), 'every remote\'s card folds (ask_done gone)')

    async def test_unbound_remote_cannot_sign(self):
        await self.start()
        for name in ('safari', 'keyonly'):
            with self.subTest(name=name):
                res = await self.confirm(self.A, name, uv=(name == 'safari'))
                self.assertFalse(res['ok'])
                self.assertIn(res['why'], ('roster_binding_required', 'no_passkey'))
        # host level: even a signed write that reaches PeerService.write is refused for a remote outside the roster
        ph, _ = self.A.phones['keyonly']
        t = self.target(self.A)
        n, ts = secrets.token_hex(16), int(time.time() * 1000)
        proof = {'channel': self.A.h.channel, 'device': ph.did, 'pub': wire.b64u(ph.sk.public_key().public_bytes_raw()),
                 'n': n, 'ts': ts, 'sig': wire.b64u(ph.sk.sign(controls.signed_message(self.A.h.channel, ph.did, 'fr_set', n, ts, controls.object_digest('fr_set', t))))}
        self.assertEqual((await self.A.svc.write('fr_set', t, device=ph.did, proof=proof))[:2], (False, 'roster_binding_required'))
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('token'))

    async def test_passkey_without_roster_binding_refused(self):
        await self.start()
        before = len(self.A.svc.net.of('same_owner'))
        res = await self.confirm(self.A, 'safari')
        self.assertEqual((res['ok'], res.get('why')), (False, 'roster_binding_required'))
        self.assertTrue(any(o.get('t') == 'roster_bind_offer' for o in self.A.broadcasts), 'the bind offer is shown instead')
        self.assertEqual(len(self.A.svc.net.of('same_owner')), before, 'nothing emitted')
        # binding AFTER the round started does not put it into this round's signed roster
        await self.bind(self.A, 'safari')
        res = await self.confirm(self.A, 'safari')
        self.assertEqual((res['ok'], res.get('why')), (False, 'roster_changed'))
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('token'))

    async def test_revoked_signer_refused_locally(self):
        await self.start()
        # unbound but still paired (binding withdrawn) → refused even though it is in the snapshot
        ph, _ = self.A.phones['oppo']
        e = self.A.svc.xfer.binding_entry(ph.did)
        with self.A.svc.store._edit('roster-via.json', {}) as rows:
            rows.pop(e['id'], None)
        res = await self.confirm(self.A, 'oppo')
        self.assertEqual((res['ok'], res.get('why')), (False, 'roster_binding_required'))
        # fully revoked: its old private key still signs, nothing accepts it
        ph, _ = self.A.phones['air']
        t = self.target(self.A)
        obj = self.signed(self.A, 'air', t)
        removed, _ = await self.A.h.revoke(ph.did)
        self.assertTrue(removed)
        proof = {'channel': self.A.h.channel, 'device': ph.did, 'pub': wire.b64u(ph.sk.public_key().public_bytes_raw()),
                 **{k: obj[k] for k in ('n', 'ts', 'sig', 'fa')}}
        ok, why, _ = await self.A.svc.write('fr_set', t, device=ph.did, proof=proof)
        self.assertFalse(ok)
        self.assertIn(why, ('phone_required', 'expired'))
        self.assertFalse(self.A.svc.same_owner.active(self.B.id))

    async def test_revoke_ticks_roster_immediately(self):
        await self.activate()
        pinned = {e['dev'] for e in self.B.svc.xfer.pinned(self.A.id)}
        self.assertEqual(pinned, {self.A.phones['air'][0].did, self.A.phones['oppo'][0].did},
                         'B pins A\'s whole code-covered roster at completion')
        before = len(self.A.svc.net.of('roster'))
        gone = self.A.phones['air'][0].did
        await self.A.h.revoke(gone)
        rosters = self.A.svc.net.of('roster')
        self.assertGreater(len(rosters), before, 'roster sent at revoke time, not on the 30 s tick')
        self.assertNotIn(gone, [e['dev'] for e in rosters[-1][1]['list']])
        await self.pump()
        self.assertEqual({e['dev'] for e in self.B.svc.xfer.pinned(self.A.id)}, {self.A.phones['oppo'][0].did})
        self.assertTrue(self.B.svc.same_owner.active(self.A.id), 'the relation itself stays')

    async def test_replay_same_round_and_cross_round(self):
        await self.start()
        obj = self.signed(self.A, 'oppo', self.target(self.A))
        self.assertTrue((await self.write(self.A, 'oppo', obj))['ok'])
        token = self.A.svc.same_owner.row(self.B.id)['token']
        emitted = len(self.A.svc.net.of('same_owner'))
        # same signed write again: controls' one-time nonce refuses it
        again = await self.write(self.A, 'oppo', {**obj, 'r': secrets.token_hex(8)})
        self.assertFalse(again['ok'])
        # a fresh write by another bound remote after the first: already_confirmed, no token recompute, no emit
        late = await self.write(self.A, 'air', self.signed(self.A, 'air', self.target(self.A)))
        self.assertEqual((late['ok'], late.get('why')), (False, 'already_confirmed'))
        self.assertEqual(self.A.svc.same_owner.row(self.B.id)['token'], token)
        self.assertEqual(len(self.A.svc.net.of('same_owner')), emitted)
        await self.pump()
        # B replays A's packet: sequence refuses it
        packet = json.loads(json.dumps(self.A.svc.net.of('same_owner')[-1][1]))
        seq = self.B.svc.same_owner.row(self.A.id)['remote_seq']
        self.B.svc.on_app(self.A.id, packet)
        self.assertEqual(self.B.svc.same_owner.row(self.A.id)['remote_seq'], seq)
        # cross-round: end it, start again; the old round's proof cannot confirm the new round on either host
        old_value, old_proof = obj['value'], self.A.svc.same_owner.row(self.B.id)['proof']
        self.assertTrue((await self.write(self.A, 'air', self.signed(self.A, 'air', self.target(self.A, 'owner-reject'), uv=False)))['ok'])
        await self.pump()
        await self.start()
        self.assertNotEqual(self.A.svc.same_owner.challenge(self.B.id), old_value)
        stale = await self.write(self.A, 'oppo', self.signed(self.A, 'oppo', self.target(self.A, value=old_value)))
        self.assertEqual((stale['ok'], stale.get('why')), (False, 'expired'))
        self.assertFalse(self.B.svc.same_owner.verify_proof(self.A.id, old_proof))

    async def test_expired_round_any_signer_refused(self):
        await self.start()
        for side in (self.A, self.B):
            with side.svc.store._edit('same-owner.json', {}) as rows:
                rows[self.other(side).id]['expires'] = side.svc.clock() - 1
        for side, name in ((self.A, 'air'), (self.A, 'oppo'), (self.B, 'mac'), (self.B, 'pixel')):
            with self.subTest(side=side.name, name=name):
                res = await self.confirm(side, name)
                self.assertEqual((res['ok'], res.get('why')), (False, 'expired'))
        self.assertFalse(self.A.svc.same_owner.active(self.B.id))

    async def test_two_remotes_sign_concurrently_first_wins(self):
        await self.start()
        cards = [a.rid for a in self.pairing_cards(self.A)]
        emitted = len(self.A.svc.net.of('same_owner'))
        first = self.signed(self.A, 'air', self.target(self.A))
        second = self.signed(self.A, 'oppo', self.target(self.A))
        r1, r2 = await asyncio.gather(self.write(self.A, 'air', first), self.write(self.A, 'oppo', second))
        self.assertEqual(sorted([r1['ok'], r2['ok']]), [False, True])
        loser = r2 if r1['ok'] else r1
        self.assertEqual(loser['why'], 'already_confirmed')
        row = self.A.svc.same_owner.row(self.B.id)
        self.assertEqual(row['proof']['device'], self.A.phones['air' if r1['ok'] else 'oppo'][0].did)
        self.assertEqual(len(self.A.svc.net.of('same_owner')), emitted + 1, 'exactly one consent packet')
        self.assertTrue(set(cards) <= set(self.gone(self.A)))
        self.assertFalse(self.pairing_cards(self.A))
        await self.pump()
        self.assertTrue((await self.confirm(self.B, 'mac'))['ok'])
        self.assertTrue(self.A.svc.same_owner.active(self.B.id))

    async def test_code_covers_both_rosters(self):
        await self.start()
        scope = json.loads(self.A.svc.same_owner.scope(self.B.id))
        self.assertEqual(scope[0], so.SCOPE_V4)
        ends = {e[0]: e for e in scope[1:]}
        self.assertEqual(len(ends[self.A.id]), 4)
        self.assertEqual({e['dev'] for e in ends[self.A.id][3]}, {self.A.phones[n][0].did for n in ('air', 'oppo')})
        self.assertEqual({e['dev'] for e in ends[self.B.id][3]}, {self.B.phones[n][0].did for n in ('mac', 'pixel')})
        self.assertEqual(self.A.svc.same_owner.scope(self.B.id), self.B.svc.same_owner.scope(self.A.id))
        code = self.A.svc.same_owner.code(self.B.id)
        # any change to any entry of either roster changes the code
        for side, i in ((self.A, 0), (self.A, 1), (self.B, 0)):
            with self.subTest(side=side.name, i=i):
                row = self.B.svc.same_owner.row(self.A.id) if side is self.A else self.A.svc.same_owner.row(self.B.id)
                key = 'remote_roster'
                edited = json.loads(json.dumps(row[key]))
                edited[i]['name'] = 'renamed'
                with patch.object(so.SameOwner, 'row', lambda s, f, r={**row, key: edited}: r):
                    other = self.B if side is self.A else self.A
                    self.assertNotEqual(other.svc.same_owner.code(side.id), code)
        # a remote roster that changes after its consent is refused on receipt (packet ignored)
        self.assertTrue((await self.confirm(self.B, 'mac'))['ok'])
        packet = json.loads(json.dumps(self.B.svc.net.of('same_owner')[-1][1]))
        packet['roster'][0]['name'] = 'changed after consent'
        packet['seq'] += 5
        before = self.A.svc.same_owner.row(self.B.id)
        self.A.svc.on_app(self.B.id, packet)
        self.assertEqual(self.A.svc.same_owner.row(self.B.id), before)
        # a peer claiming v4 off after its consent is refused too (no downgrade of a confirmed round)
        packet = json.loads(json.dumps(self.B.svc.net.of('same_owner')[-1][1]))
        packet['caps'] = [c for c in packet['caps'] if c != so.ANY_SIGNER]
        packet['seq'] += 6
        self.A.svc.on_app(self.B.id, packet)
        self.assertEqual(self.A.svc.same_owner.row(self.B.id), before)

    async def test_peer_verify_accepts_any_roster_member_and_only_them(self):
        await self.start()
        self.assertTrue((await self.confirm(self.A, 'oppo'))['ok'])
        proof = self.A.svc.same_owner.row(self.B.id)['proof']
        self.assertTrue(self.B.svc.same_owner.verify_proof(self.A.id, proof), 'any A roster member, not A\'s starter')
        self.assertNotEqual(proof['pub'], self.B.svc.same_owner.row(self.A.id)['remote_pub'])
        # a key outside A's roster snapshot, even with a valid Ed signature over the exact write, is refused
        ph, _ = self.A.phones['safari']
        t = {'friend': self.B.id, 'op': 'owner-confirm-v3', 'value': self.B.svc.same_owner.challenge(self.A.id)}
        n, ts = secrets.token_hex(16), int(time.time() * 1000)
        dg = controls.object_digest('fr_set', t)
        forged = {'channel': self.A.h.channel, 'device': ph.did, 'pub': wire.b64u(ph.sk.public_key().public_bytes_raw()),
                  'n': n, 'ts': ts, 'sig': wire.b64u(ph.sk.sign(controls.signed_message(self.A.h.channel, ph.did, 'fr_set', n, ts, dg))),
                  'fa': ph.auth.assert_(passkey.elevate_challenge(self.A.h.channel, ph.did, n, 'fr_set', n, dg), b'', nonce=b'')}
        self.assertFalse(self.B.svc.same_owner.verify_proof(self.A.id, forged))
        # a roster member without UV (Ed only) is refused
        self.assertFalse(self.B.svc.same_owner.verify_proof(self.A.id, {k: v for k, v in proof.items() if k != 'fa'}))
        # a roster member's assertion without the UV flag is refused
        ph, _ = self.A.phones['oppo']
        weak = {**proof, 'fa': ph.auth.assert_(passkey.elevate_challenge(self.A.h.channel, ph.did, proof['n'], 'fr_set', proof['n'], dg), b'', nonce=b'', flags=1)}
        self.assertFalse(self.B.svc.same_owner.verify_proof(self.A.id, weak))

    async def test_old_peer_falls_back_to_designated_signer(self):
        self.B.svc.same_owner.any_signer = False          # B models a host from before 0304 (no any_signer cap)
        fid = self.B.id
        res = await self.write(self.A, 'air', self.signed(self.A, 'air', {'friend': fid, 'op': 'group', 'value': 'same-owner'}, uv=False))
        self.assertTrue(res['ok'])
        await self.pump()
        self.assertFalse(self.A.svc.same_owner.v4(fid))
        self.assertFalse(self.B.svc.same_owner.v4(self.A.id))
        self.assertNotIn(so.ANY_SIGNER, self.B.svc.net.of('same_owner')[-1][1]['caps'])
        self.assertEqual(self.A.svc.same_owner.code(fid), self.B.svc.same_owner.code(self.A.id))
        scope = json.loads(self.A.svc.same_owner.scope(fid))
        self.assertTrue(all(len(e) == 5 for e in scope), 'v3 scope with one designated signer per end')
        card = self.A.svc.ask_msg(self.pairing_cards(self.A)[0])
        self.assertNotIn('pairing_v', card['pq'])
        # A: only the starter (air) may confirm; oppo gets wrong_remote
        res = await self.confirm(self.A, 'oppo')
        self.assertEqual((res['ok'], res.get('why')), (False, 'wrong_remote'))
        self.assertTrue((await self.confirm(self.A, 'air'))['ok'])
        # B: only its invite key (most recently seen) — the other one is refused
        designated = self.B.svc.same_owner.row(self.A.id)['pub']
        names = {wire.b64u(self.B.phones[n][0].sk.public_key().public_bytes_raw()): n for n in ('mac', 'pixel')}
        wrong = next(n for k, n in names.items() if k != designated)
        res = await self.confirm(self.B, wrong)
        self.assertEqual((res['ok'], res.get('why')), (False, 'wrong_remote'))
        self.assertTrue((await self.confirm(self.B, names[designated]))['ok'])
        self.assertTrue(self.A.svc.same_owner.active(fid))
        self.assertTrue(self.B.svc.same_owner.active(self.A.id))
        # v3 pin stays the unique ceremony signer, not the whole list
        self.A.svc.xfer._on_roster(fid, {'list': self.B.svc.xfer.my_roster(), 'caps': og.CAPS})
        self.assertEqual([e['sign_pub'] for e in self.A.svc.xfer.pinned(fid)], [designated])

    async def test_v4_pin_comes_from_ceremony_not_from_first_roster_packet(self):
        await self.start()
        # before completion a roster packet pins nothing (v4 pins only an active, code-verified ceremony)
        self.A.svc.xfer._on_roster(self.B.id, {'list': self.B.svc.xfer.my_roster(), 'caps': og.CAPS})
        self.assertIsNone(self.A.svc.xfer.pinned(self.B.id))
        no_roster = lambda src, obj: obj.get('t') in ('roster', 'roster_ack')   # noqa: E731
        res = await self.write(self.A, 'air', self.signed(self.A, 'air', self.target(self.A)))
        self.assertTrue(res['ok'])
        await self.pump(drop=no_roster)
        # B's roster packets are dropped: A's pin must come from the ceremony itself
        res = await self.write(self.B, 'pixel', self.signed(self.B, 'pixel', self.target(self.B)))
        self.assertTrue(res['ok'])
        await self.pump(drop=no_roster)
        self.assertTrue(self.A.svc.same_owner.active(self.B.id))
        pinned = self.A.svc.xfer.pinned(self.B.id)
        ceremony = self.A.svc.same_owner.row(self.B.id)['remote_roster']
        self.assertEqual([e['id'] for e in pinned], [e['id'] for e in ceremony])
        self.assertTrue(all(og.entry_ok(e) and og.binding_ok(e) for e in pinned))
        # an unauthenticated extra entry in a later roster packet is not added (needs via)
        extra = {**ceremony[0], 'id': wire.b64u(os.urandom(16)), 'dev': 'X' * 16, 'name': 'stranger'}
        self.A.svc.xfer._on_roster(self.B.id, {'list': [*ceremony, extra], 'caps': og.CAPS})
        self.assertNotIn(extra['id'], [e['id'] for e in self.A.svc.xfer.pinned(self.B.id)])

    async def test_task_plan_any_roster_signer(self):
        await self.activate()
        sow = self.A.svc.same_owner
        b = self.B.id
        for n in ('mac', 'pixel'):
            ph, _ = self.B.phones[n]
            sk = wire.b64u(ph.sk.public_key().public_bytes_raw())
            self.assertTrue(sow.plan_signer(b, sk, ph.did), n)
            self.assertTrue(self.B.svc.same_owner.local_plan_signer(self.A.id, ph.did, sk), n)
        stranger = Phone(self.B.st, 'stranger', with_passkey=True)
        sk = wire.b64u(stranger.sk.public_key().public_bytes_raw())
        self.assertFalse(sow.plan_signer(b, sk, stranger.did))
        self.assertFalse(self.B.svc.same_owner.local_plan_signer(self.A.id, stranger.did, sk), 'unbound → root_reconfirm')
        # a member removed from B's roster (revoked there) stops signing plans as soon as A applies the roster
        gone, _ = self.B.phones['mac']
        await self.B.h.revoke(gone.did)
        await self.pump()
        self.assertFalse(sow.plan_signer(b, wire.b64u(gone.sk.public_key().public_bytes_raw()), gone.did))
        self.assertTrue(sow.plan_signer(b, wire.b64u(self.B.phones['pixel'][0].sk.public_key().public_bytes_raw()), self.B.phones['pixel'][0].did))

    async def test_otg_grant_card_any_bound_remote_signs(self):
        """A1 row secret_xfer.py:1017: the grant card fans out; any roster-bound remote signs; unbound → refused."""
        from agentj import provenance
        from agentj.compose import Send
        from types import SimpleNamespace
        await self.activate()
        x = self.A.svc.xfer
        h = self.A.h
        air, air_s = self.A.phones['air']
        oppo, oppo_s = self.A.phones['oppo']
        safari, safari_s = self.A.phones['safari']
        turn = h.hist_add({'k': 'phone', 'dev': air.did, 'name': 'air', 'text': 'Synthetic grant task'}, '', 'done')
        send = Send(air.did, 'grant-origin', turn['id'], source=provenance.phone())
        from agentj import approval_review
        send.approval_origin = approval_review.remember(h, turn, provenance.phone())
        h.agent = SimpleNamespace(kind='codex', status='idle', cur_send=send, phone_turn=True, pending_source=provenance.phone())
        h.agent_cfg = {'dir': self.tmp.name, 'kind': 'codex'}
        h.ask_ttl = 2
        self.A.svc.store.name_of = lambda fid: 'Desktop'      # a display name, not the friend id (display safety check)
        # Real AJ-xxxx peer ids trip the display projector's agentj_public_id class (HostFlows uses ids 'A'/'B');
        # this test is about who signs, so the display projection is pass-through here (reported as an open item).
        display = patch.object(og, 'typed_plan_display', lambda inp, normalizer: (lambda value: value))
        display.start()
        self.addCleanup(display.stop)
        plan = {'title': 'Release', 'why': 'Remove the old build',
                'steps': [{'host': self.B.id, 'cat': 'delete', 'what': 'Remove old build', 'paths': ['/tmp/p0304-old-build'], 'max': 1}]}
        task = asyncio.create_task(x.cli({'op': 'grant', 'plan': plan}, self.tmp.name))
        for _ in range(200):
            if h.asks or task.done():
                break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(), task.result() if task.done() else '')
        ask = next(iter(h.asks.values()))
        self.assertIsNone(ask.only)
        for _, s in self.A.phones.values():
            self.assertTrue(any(c.get('id') == ask.rid for c in h.open_cards(s)), 'every remote sees it')
        tid = ask.tool_input['otg']['tid']
        from _p541_device import Phone as V2
        # R-F5: an unbound remote's allow is refused without consuming the card
        await h._answer(safari_s, V2.answer(safari, h._ask_msg(ask, safari_s), True))
        self.assertFalse(task.done())
        self.assertNotIn(tid, x._grants_out())
        await h._answer(oppo_s, V2.answer(oppo, h._ask_msg(ask, oppo_s), True))
        res = await asyncio.wait_for(task, 3)
        self.assertEqual(res.get('result'), 'signed', res)
        rec = x._grants_out()[tid]
        self.assertEqual(rec['proof']['device'], oppo.did)
        self.assertEqual(rec['device'], air.did, 'the task stays bound to the starting remote')
        await self.pump()
        self.assertIn(tid, self.B.svc.xfer._grants_in(), 'the peer verifies it against A\'s pinned roster')

    def test_no_designated_signer_left(self):
        """Grep contract (A1-9): the removed single-signer gates do not come back."""
        xfer = (HOST / 'agentj' / 'secret_xfer.py').read_text()
        peer = (HOST / 'agentj' / 'peer_service.py').read_text()
        owner = (HOST / 'agentj' / 'same_owner.py').read_text()
        self.assertNotIn('only=device', xfer)
        self.assertNotIn('proof["proof"].get("sk") != self.same_owner.row(fid).get("pub")', peer)
        self.assertNotIn('proof["sk"] != self.row(fid).get("remote_pub")', owner)
        self.assertIn("'caps': OTG_CAPS + ([ANY_SIGNER]", owner)
        # v4 scope ends carry no signer field
        scope_src = owner[owner.index('def scope(self, fid'):owner.index('def v4(self')]
        v4_part = scope_src[scope_src.index('if self.v4(fid, r):'):scope_src.index('ends = [(self.svc.keys.id')]
        self.assertNotIn("'pub'", v4_part)
        self.assertNotIn("remote_pub", v4_part)


if __name__ == '__main__':
    unittest.main()
