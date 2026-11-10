"""0304 A1 security r1 controls (reports/qa/0304-frozen/security-threatmodel-r1.md; THREAT-MODEL.md
「r1 审查后补充的控制」 R-F1 R-F4 R-F5 R-F6 R-F11).

Same fixture as test_0304_any_signer: two real serve.Host + PeerService over FakeNet, real Ed25519 control
signatures, real WebAuthn assertions, the real serve fr_set and answer paths.
"""
import _hermetic  # noqa: F401
import asyncio
import hashlib
import json
import secrets
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentj import controls, owner_grant as og, passkey, same_owner as so, wire
from test_0304_any_signer import TwoHosts


class RF1Downgrade(TwoHosts):
    def last_packet(self, side):
        return json.loads(json.dumps(side.svc.net.of('same_owner')[-1][1]))

    async def test_v2_peer_refused_when_local_roster_nonempty(self):
        """F1 attack: a peer claiming roster_v:null / v:2 plus a start tap on this host must never become Same owner
        without a UV confirm here (the start tap is not consent once this host has a roster)."""
        b = self.B.svc
        b.same_owner.any_signer = False
        old_caps = patch.object(so, 'OTG_CAPS', [])                   # B looks like a host from before P574
        old_caps.start()
        self.addCleanup(old_caps.stop)
        with patch.object(b.xfer, 'my_roster', lambda: []):          # … with no roster (v2)
            res = await self.write(self.A, 'air', self.signed(self.A, 'air', {'friend': self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False))
            self.assertTrue(res['ok'], res)
            await self.pump()
            row = self.A.svc.same_owner.row(self.B.id)
            self.assertIsNone(row.get('remote_roster_v'))
            self.assertTrue(self.A.svc.same_owner.too_old(self.B.id))
            self.assertEqual(self.A.svc.same_owner.info(self.B.id).get('same_owner_why'), 'peer_too_old')
            self.assertFalse(row.get('token'), 'the signed start tap is not consent (no v2 settle)')
            # B (attacker-controlled) consents with a v2 Ed-only signature over the legacy op, no UV
            ph, _ = self.B.phones['mac']
            t = {'friend': self.A.id, 'op': 'owner-confirm', 'value': b.same_owner.challenge(self.A.id)}
            n, ts = secrets.token_hex(16), int(time.time() * 1000)
            proof = {'channel': self.B.h.channel, 'device': ph.did, 'pub': wire.b64u(ph.sk.public_key().public_bytes_raw()),
                     'n': n, 'ts': ts, 'sig': wire.b64u(ph.sk.sign(controls.signed_message(self.B.h.channel, ph.did, 'fr_set', n, ts, controls.object_digest('fr_set', t))))}
            b.same_owner.consent(self.A.id, True, proof)
            await self.pump()
        self.assertTrue(self.A.svc.same_owner.row(self.B.id).get('remote'), 'A saw the peer half')
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('token'))
        self.assertFalse(self.A.svc.same_owner.active(self.B.id))
        # any A remote's confirm, legacy op or v3 op, UV or not: peer_too_old
        for op in ('owner-confirm', 'owner-confirm-v3'):
            with self.subTest(op=op):
                res = await self.write(self.A, 'oppo', self.signed(self.A, 'oppo', self.target(self.A, op=op)))
                self.assertEqual((res['ok'], res.get('why')), (False, 'peer_too_old'))
        self.assertFalse(self.A.svc.same_owner.active(self.B.id))

    async def test_pre_upgrade_unverified_consent_never_completes(self):
        """Production shape seen on TB 11-09 23:00 (a5-forensics.md): this host recorded the start tap as consent
        (no UV) for a peer without a roster. When that peer consents, the round must not become active."""
        b = self.B.svc
        with patch.object(b.xfer, 'my_roster', lambda: []):
            await self.write(self.A, 'air', self.signed(self.A, 'air', {'friend': self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False))
            await self.pump()
            a = self.A.svc.same_owner
            with patch.object(a, 'uv_host', lambda: False):     # the 0.30.4 behaviour: start tap = consent
                self.assertTrue(a.settle(self.B.id))
            self.assertTrue(a.row(self.B.id).get('token'))
            ph, _ = self.B.phones['mac']
            t = {'friend': self.A.id, 'op': 'owner-confirm', 'value': b.same_owner.challenge(self.A.id)}
            n, ts = secrets.token_hex(16), int(time.time() * 1000)
            proof = {'channel': self.B.h.channel, 'device': ph.did, 'pub': wire.b64u(ph.sk.public_key().public_bytes_raw()),
                     'n': n, 'ts': ts, 'sig': wire.b64u(ph.sk.sign(controls.signed_message(self.B.h.channel, ph.did, 'fr_set', n, ts, controls.object_digest('fr_set', t))))}
            b.same_owner.consent(self.A.id, True, proof)
            await self.pump()
        self.assertFalse(self.A.svc.same_owner.active(self.B.id))
        self.assertFalse(self.B.svc.same_owner.active(self.A.id))
        self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('token'), 'the unverified consent is withdrawn')

    async def test_peer_without_roster_but_new_caps_asks_to_bind_there(self):
        b = self.B.svc
        with patch.object(b.xfer, 'my_roster', lambda: []):
            await self.write(self.A, 'air', self.signed(self.A, 'air', {'friend': self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False))
            await self.pump()
        self.assertIn('otg1', self.A.svc.same_owner.row(self.B.id)['remote_caps'])
        self.assertEqual(self.A.svc.same_owner.info(self.B.id).get('same_owner_why'), 'peer_binding_required')
        res = await self.write(self.A, 'oppo', self.signed(self.A, 'oppo', self.target(self.A, op='owner-confirm')))
        self.assertEqual((res['ok'], res.get('why')), (False, 'peer_binding_required'))

    async def test_passkeyless_remote_cannot_start_same_owner(self):
        for name, nxt in (('keyonly', 'passkey'), ('safari', 'bind')):
            with self.subTest(name=name):
                before = len(self.A.svc.net.of('same_owner'))
                res = await self.write(self.A, name, self.signed(self.A, name, {'friend': self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False))
                self.assertEqual((res['ok'], res.get('why'), res.get('next')), (False, 'roster_binding_required', nxt))
                self.assertEqual('cont' in res, nxt == 'bind', 'a ticket only where binding is possible')
                self.assertFalse(self.A.svc.same_owner.row(self.B.id).get('nonce'), 'no round starts')
                self.assertEqual(len(self.A.svc.net.of('same_owner')), before, 'nothing sent to the peer')

    async def test_caps_flip_mid_round_changes_code(self):
        await self.start()
        code = self.A.svc.same_owner.code(self.B.id)
        scope = self.A.svc.same_owner.scope(self.B.id)
        self.assertTrue(json.loads(scope)[0] == so.SCOPE_V4)
        packet = self.last_packet(self.B)
        self.assertFalse(packet['token'])
        flipped = {**packet, 'caps': [c for c in packet['caps'] if c != so.ANY_SIGNER], 'seq': packet['seq'] + 1}
        self.A.svc.on_app(self.B.id, flipped)
        self.assertFalse(self.A.svc.same_owner.v4(self.B.id))
        self.assertNotEqual(self.A.svc.same_owner.code(self.B.id), code, 'caps flip → different scope bytes → new code')
        self.assertNotIn(so.SCOPE_V4, self.A.svc.same_owner.scope(self.B.id))
        # roster_v flip to "no roster": new code again, and nobody here can confirm it (peer_too_old)
        code3 = self.A.svc.same_owner.code(self.B.id)
        v2 = {**packet, 'roster_v': None, 'roster': [], 'caps': [], 'seq': packet['seq'] + 2}
        self.A.svc.on_app(self.B.id, v2)
        self.assertNotEqual(self.A.svc.same_owner.code(self.B.id), code3)
        res = await self.write(self.A, 'oppo', self.signed(self.A, 'oppo', self.target(self.A, op='owner-confirm')))
        self.assertEqual((res['ok'], res.get('why')), (False, 'peer_too_old'))


class RF4Pin(TwoHosts):
    async def test_pin_equals_code_covered_scope_roster(self):
        await self.activate()
        for side in (self.A, self.B):
            with self.subTest(side=side.name):
                fid = self.other(side).id
                scope = side.svc.same_owner.scope(fid)
                row = side.svc.same_owner.row(fid)
                self.assertEqual(hashlib.sha256(scope.encode()).hexdigest()[:32], row['verified'])
                peer_end = next(e for e in json.loads(scope)[1:] if e[0] == fid)
                self.assertEqual(side.svc.xfer.pinned(fid), peer_end[3], 'pin == the roster inside the compared code')
                signer = row['remote_signer']
                self.assertTrue(any(e['sign_pub'] == signer['pub'] and e['dev'] == signer['device'] for e in peer_end[3]))

    async def test_pin_ignores_row_roster_mutation(self):
        await self.activate()
        a = self.A.svc
        good = a.xfer.ceremony_roster(self.B.id)
        self.assertTrue(good)
        cases = {
            'extra entry': lambda r: r['remote_roster'].append({**r['remote_roster'][0], 'name': 'planted'}),
            'renamed entry': lambda r: r['remote_roster'][0].update(name='renamed'),
            'dropped entry': lambda r: r['remote_roster'].pop(),
            'signer outside roster': lambda r: r.update(remote_signer={'pub': r['remote_signer']['pub'], 'device': 'X' * 16}),
        }
        original = json.loads(json.dumps(a.same_owner.row(self.B.id)))
        for name, mutate in cases.items():
            with self.subTest(case=name):
                with a.store._edit('owner-roster.json', {}) as d:
                    d.pop(self.B.id, None)
                with a.store._edit('same-owner.json', {}) as d:
                    d[self.B.id] = json.loads(json.dumps(original))
                    mutate(d[self.B.id])
                self.assertIsNone(a.xfer.ceremony_roster(self.B.id))
                a.xfer.pin_ceremony(self.B.id)
                a.xfer._on_roster(self.B.id, {'list': a.same_owner.row(self.B.id)['remote_roster'], 'caps': og.CAPS})
                self.assertIsNone(a.xfer.pinned(self.B.id), 'nothing pinned from a row that no longer matches the code')
        with a.store._edit('same-owner.json', {}) as d:
            d[self.B.id] = original
        a.xfer.pin_ceremony(self.B.id)
        self.assertEqual(a.xfer.pinned(self.B.id), good)


class RF5Otg(TwoHosts):
    async def grant_task(self):
        from agentj import provenance, approval_review
        from agentj.compose import Send
        h = self.A.h
        air, _ = self.A.phones['air']
        turn = h.hist_add({'k': 'phone', 'dev': air.did, 'name': 'air', 'text': 'Synthetic grant task'}, '', 'done')
        send = Send(air.did, 'grant-origin', turn['id'], source=provenance.phone())
        send.approval_origin = approval_review.remember(h, turn, provenance.phone())
        h.agent = SimpleNamespace(kind='codex', status='idle', cur_send=send, phone_turn=True, pending_source=provenance.phone())
        h.agent_cfg = {'dir': self.tmp.name, 'kind': 'codex'}
        h.ask_ttl = 2
        self.A.svc.store.name_of = lambda fid: 'Desktop'
        display = patch.object(og, 'typed_plan_display', lambda inp, normalizer: (lambda value: value))
        display.start()
        self.addCleanup(display.stop)
        plan = {'title': 'Release', 'why': 'Remove the old build',
                'steps': [{'host': self.B.id, 'cat': 'delete', 'what': 'Remove old build', 'paths': ['/tmp/p0304-old-build'], 'max': 1}]}
        task = asyncio.create_task(self.A.svc.xfer.cli({'op': 'grant', 'plan': plan}, self.tmp.name))
        for _ in range(200):
            if h.asks or task.done():
                break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(), task.result() if task.done() else '')
        return task, next(iter(h.asks.values()))

    async def answer(self, name, ask):
        from _p541_device import Phone as V2
        ph, s = self.A.phones[name]
        before = len(self.A.sent)
        await self.A.h._answer(s, V2.answer(ph, self.A.h._ask_msg(ask, s), True))
        return [o for cid, o in self.A.sent[before:] if cid == s.cid]

    async def test_otg_card_signed_by_other_bound_remote(self):
        await self.activate()
        task, ask = await self.grant_task()
        self.assertIsNone(ask.only)
        await self.answer('oppo', ask)                       # not the remote that started the task
        res = await asyncio.wait_for(task, 3)
        self.assertEqual(res.get('result'), 'signed', res)
        x = self.A.svc.xfer
        rec = x._grants_out()[ask.tool_input['otg']['tid']]
        self.assertEqual(rec['proof']['device'], self.A.phones['oppo'][0].did)
        self.assertTrue(x.local_live(rec))
        # the signer's roster binding withdrawn → its grant stops being live here (bound(proof.device) required)
        e = x.binding_entry(self.A.phones['oppo'][0].did)
        with self.A.svc.store._edit('roster-via.json', {}) as rows:
            rows.pop(e['id'], None)
        self.assertFalse(x.bound(self.A.phones['oppo'][0].did))
        self.assertFalse(x.local_live(rec))
        self.assertNotIn(ask.tool_input['otg']['tid'], x._grants_out())

    async def test_otg_card_unbound_signer_refused(self):
        await self.activate()
        task, ask = await self.grant_task()
        out = await self.answer('safari', ask)               # passkey, not roster-bound
        self.assertTrue(any(o.get('t') == 'answer_refused' and o.get('why') == 'roster_binding_required' for o in out), out)
        self.assertTrue(any(o.get('t') == 'roster_bind_offer' for o in out), 'offered the binding instead')
        self.assertFalse(ask.fut.done(), 'the unbound answer does not consume the card')
        self.assertFalse(task.done())
        out = await self.answer('keyonly', ask)              # no passkey at all
        self.assertTrue(any(o.get('t') == 'answer_refused' and o.get('why') == 'roster_binding_required' for o in out), out)
        self.assertFalse(ask.fut.done())
        await self.answer('air', ask)                        # a bound remote still signs the same card
        res = await asyncio.wait_for(task, 3)
        self.assertEqual(res.get('result'), 'signed', res)


class RF6Pinned(TwoHosts):
    async def test_task_proof_from_revoked_peer_remote_refused_after_roster_narrow(self):
        """Origin B signs a cross-host plan with pixel; A accepts it. B revokes pixel; once A applies B's shorter
        roster the same proof is refused — the frozen ceremony row (which still lists pixel) is never consulted."""
        from agentj import approval_jev
        from _p541_device import Phone as V2
        offline = patch.object(approval_jev, 'available', return_value=False)
        offline.start()
        self.addCleanup(offline.stop)
        await self.activate()
        from test_l1 import _owner_agent
        B = self.B.h
        B.agent = _owner_agent(B)
        B.agent.submit = lambda item: None
        with patch.object(B, 'hist_emit'):
            B.desktop_input('Synthetic authenticated owner task for a cross-host plan.')
        B.ask_ttl = 2
        B.cur_turn = 7
        actions = [{'tool': 'Bash', 'input': {'command': 'rm -rf ~/Documents/p0304-old'}}]
        job = asyncio.create_task(self.B.svc.cli({'t': 'tell', 'friend': self.A.id, 'text': 'Clean the old folder.', 'actions': actions}))
        for _ in range(200):
            if B.asks or job.done():
                break
            await asyncio.sleep(.005)
        self.assertFalse(job.done(), job.result() if job.done() else '')
        card = next(iter(B.asks.values()))
        pixel, ps = self.B.phones['pixel']
        await B._answer(ps, V2.answer(pixel, B._ask_msg(card, ps), True))
        res = await asyncio.wait_for(job, 3)
        self.assertTrue(res.get('ok'), res)
        sent = next(obj for _, obj in reversed(self.B.svc.net.sent) if obj['t'] == 'pmsg')
        msg = json.loads(json.dumps(sent))
        self.assertEqual(msg['task_proof']['device'], pixel.did)
        a = self.A.svc.same_owner
        self.assertIsNotNone(a.task_proof(self.B.id, msg), 'a pinned roster member signs plans')
        await B.revoke(pixel.did)
        await self.pump(drop=lambda src, obj: obj.get('t') == 'pmsg')
        self.assertNotIn(pixel.did, [e['dev'] for e in self.A.svc.xfer.pinned(self.B.id)])
        self.assertIn(pixel.did, [e['dev'] for e in a.row(self.B.id)['remote_roster']], 'the ceremony row still lists it')
        self.assertIsNone(a.task_proof(self.B.id, msg))


    async def test_post_ceremony_checks_never_read_the_ceremony_row(self):
        """v3 relation (old peer): the plan signer comes from pinned(fid) only — with no pin nobody signs, even the
        ceremony's own designated signer still listed in the frozen row."""
        self.B.svc.same_owner.any_signer = False
        await self.write(self.A, 'air', self.signed(self.A, 'air', {'friend': self.B.id, 'op': 'group', 'value': 'same-owner'}, uv=False))
        await self.pump()
        self.assertTrue((await self.confirm(self.A, 'air'))['ok'])
        designated = self.B.svc.same_owner.row(self.A.id)['pub']
        name = next(n for n in ('mac', 'pixel') if wire.b64u(self.B.phones[n][0].sk.public_key().public_bytes_raw()) == designated)
        self.assertTrue((await self.confirm(self.B, name))['ok'])
        sow, did = self.A.svc.same_owner, self.B.phones[name][0].did
        self.assertTrue(sow.active(self.B.id))
        self.assertTrue(sow.plan_signer(self.B.id, designated, did), 'pinned at completion')
        with self.A.svc.store._edit('owner-roster.json', {}) as d:
            d.pop(self.B.id)
        self.assertEqual(sow.plan_roster(self.B.id), [])
        self.assertFalse(sow.plan_signer(self.B.id, designated, did))


class RF11Restart(TwoHosts):
    async def test_roster_change_restarts_pending_round(self):
        await self.start(self.A, 'air')
        old_code, old_nonce = self.A.svc.same_owner.code(self.B.id), self.A.svc.same_owner.row(self.B.id)['nonce']
        cards = [a.rid for a in self.pairing_cards(self.A)]
        self.assertTrue((await self.confirm(self.B, 'pixel'))['ok'])       # B already said yes to the old code
        oppo, _ = self.A.phones['oppo']
        await self.A.h.revoke(oppo.did)                                  # in A's snapshot, neither starter nor signer
        row = self.A.svc.same_owner.row(self.B.id)
        self.assertNotEqual(row['nonce'], old_nonce, 'restarted with a new nonce')
        self.assertNotIn(oppo.did, [e['dev'] for e in row['roster']])
        self.assertTrue(set(cards) <= set(self.gone(self.A)), 'old cards close')
        await self.pump()
        new = self.A.svc.same_owner.code(self.B.id)
        self.assertNotEqual(new, old_code)
        self.assertEqual(new, self.B.svc.same_owner.code(self.A.id))
        self.assertFalse(self.B.svc.same_owner.row(self.A.id).get('token'), 'the consent to the old code is void')
        self.assertTrue((await self.confirm(self.A, 'air'))['ok'])
        self.assertFalse(self.A.svc.same_owner.active(self.B.id))
        self.assertTrue((await self.confirm(self.B, 'mac'))['ok'])
        self.assertTrue(self.A.svc.same_owner.active(self.B.id))
        self.assertTrue(self.B.svc.same_owner.active(self.A.id))
        self.assertNotIn(oppo.did, [e['dev'] for e in self.B.svc.xfer.pinned(self.A.id)])

    async def test_first_wins_inside_consent_critical_section(self):
        await self.start()
        sow = self.A.svc.same_owner
        t = self.target(self.A)
        obj = self.signed(self.A, 'air', t)
        self.assertTrue((await self.write(self.A, 'air', obj))['ok'])
        token = sow.row(self.B.id)['token']
        ph, _ = self.A.phones['oppo']
        late = {'channel': self.A.h.channel, 'device': ph.did, 'pub': wire.b64u(ph.sk.public_key().public_bytes_raw()),
                'n': 'x', 'ts': 1, 'sig': 'x'}
        self.assertFalse(sow.consent(self.B.id, True, late), 'a second confirm is refused inside the edit itself')
        self.assertEqual(sow.row(self.B.id)['token'], token)


if __name__ == '__main__':
    unittest.main()
