"""P405 bilateral state, signed decisions, task delivery and private status contract."""
import _hermetic  # noqa: F401
import asyncio
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from test_p71_peer_service import Base, FakeHost, FakeNet, FakeSessions, device, pmsg, settle, sign_answer, SECRET
from agentj import peer_service, peer_guard, wire, controls
from agentj.same_owner import aggregate, clean_status


class SameOwnerTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.other_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.other_tmp.cleanup)
        self.other = FakeHost(self.other_tmp.name)
        self.remote = peer_service.PeerService(self.other, sessions=FakeSessions(), net_factory=FakeNet, clock=self.clock)
        self.remote.HOUSEKEEP = 3600
        self.remote.index = peer_guard.SecretIndex([SECRET])
        self.remote.index_at = __import__('time').monotonic()
        await self.remote.start()
        self.addAsyncCleanup(self.remote.stop)
        self.odid, self.osk = device(self.other.st)
        self.a, self.b = self.svc.keys.id, self.remote.keys.id
        for svc, keys in ((self.svc, self.remote.keys), (self.remote, self.svc.keys)):
            svc.store.add_friend(keys.id, wire.b64u(keys.x_pub), wire.b64u(keys.ed_pub), keys.mbox)
        self.positions = [0, 0]
        self.pages, self.sends = [], []
        self.host.hist_add = lambda *a: self.pages.append(a) or {'id': len(self.pages)}
        self.host.agent = SimpleNamespace(submit=self.sends.append, kind='codex', status='idle', native_source={'kind':'terminal','role':'owner'})
        self.other_pages, self.other_sends = [], []
        self.other.hist_add = lambda *a: self.other_pages.append(a) or {'id':len(self.other_pages)}
        self.other.agent = SimpleNamespace(submit=self.other_sends.append, kind='codex', status='idle', native_source={'kind':'terminal','role':'owner'})

    async def pump(self):
        for _ in range(20):
            await settle()
            count = 0
            for i, (src, dst, fid) in enumerate(((self.svc, self.remote, self.a), (self.remote, self.svc, self.b))):
                rows = src.net.sent[self.positions[i]:]
                self.positions[i] = len(src.net.sent)
                for _, obj in rows:
                    if obj['t'] in ('same_owner', 'owner_status', 'owner_status_res'):
                        dst.on_app(fid, obj)
                        count += 1
            if not count:
                return
        self.fail('handshake loop')

    async def choose(self, svc, fid, did, sk, value='same-owner'):
        target = {'friend':fid,'op':'owner-confirm' if value == 'confirm' else 'owner-reject' if value == 'reject' else 'group',
                  'value':svc.same_owner.challenge(fid) if value in ('confirm', 'reject') else value}
        n, ts = __import__('secrets').token_hex(16), int(__import__('time').time()*1000)
        sig = wire.b64u(sk.sign(controls.signed_message(svc.host.channel, did, 'fr_set', n, ts, controls.object_digest('fr_set', target))))
        proof = {'channel':svc.host.channel,'device':did,'pub':wire.b64u(sk.public_key().public_bytes_raw()),'n':n,'ts':ts,'sig':sig}
        self.assertIsNone(controls.check(svc.st, controls.Nonces(), svc.host.channel, did, proof, 'fr_set', target))
        return await svc.write('fr_set',target,device=did,proof=proof)

    async def confirm(self):
        self.assertEqual((await self.choose(self.svc, self.b, self.did, self.dsk))[:2], (True, None))
        await self.pump()
        self.assertFalse(self.svc.same_owner.active(self.b))
        # P417 #186: the start was the starting side's consent; the other side confirms once
        await self.choose(self.remote, self.a, self.odid, self.osk, 'confirm')
        await self.pump()
        self.assertTrue(self.svc.same_owner.active(self.b))
        self.assertTrue(self.remote.same_owner.active(self.a))

    async def test_unilateral_pending_and_cli_cannot_consent(self):
        res = await self.svc.write('fr_set', {'friend':self.b,'op':'group','value':'same-owner'})
        self.assertEqual(res[1], 'phone_required')
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        self.assertEqual(self.svc.list_view()['friends'][0]['same_owner'], 'pending')
        self.assertEqual((await self.svc.cli({'t':'status','friend':self.b}))['code'], 403)

    async def test_p482_long_task_reviews_payload_not_host_envelope(self):
        await self.confirm()
        original = self.svc._outbound
        async def review(text):
            if 'Reply command:' in text:
                return peer_guard.Verdict(False, 'jev_refused')
            return await original(text)
        m = pmsg('请审阅以下稿件。' + '检查论证与表达。' * 460)
        with patch.object(self.svc, '_outbound', review):
            self.svc._on_pmsg(self.b, m)
            await settle()
        self.assertEqual(len(self.sends), 1)
        ready = asyncio.create_task(self.sends[0].wait_ready())
        with patch.object(self.svc, '_outbound', review):
            await settle()
            await self.pump()
            self.assertTrue(await ready)
        self.assertIn(m['text'], self.sends[0].text)
        self.assertEqual(self.sends[0].source['kind'], 'same_owner')
        self.assertTrue(self.host.notices)
        self.assertIn('reply', self.host.pushes)
        self.assertTrue((await self.svc.tell(self.b, '审阅完成', m['mid']))['ok'])
        self.svc._on_pmsg(self.b, m)
        await settle()
        self.assertEqual(len(self.sends), 1)

    async def test_p482_reply_to_received_legacy_task_without_forward_mapping(self):
        await self.confirm()
        m = pmsg('请审稿')
        self.assertTrue(self.svc.guards.accept(self.b, m))
        self.store.hist_add(self.b, {'mid':m['mid'], 'dir':'in', 'text':m['text'], 'ts':1, 's':'got'})
        self.assertTrue((await self.svc.tell(self.b, '审阅完成', m['mid']))['ok'])
        reply = self.net.of('pmsg')[-1][1]
        self.assertEqual(reply['thread'], m['thread'])
        self.assertEqual(reply['irt'], m['mid'])
        self.assertTrue(reply['end'])
        self.assertFalse((await self.svc.tell(self.b, '审阅完成', 'f'*16))['ok'])

    async def test_p482_reply_fallback_rejects_revoked_and_terminal_requests(self):
        await self.confirm()
        m = pmsg('检查服务')
        self.svc.guards.accept(self.b, m)
        self.svc.guards.state(self.b, m['mid'], 'cancelled')
        self.assertEqual((await self.svc.tell(self.b, '完成', m['mid']))['why'], 'bad_reply')
        m = pmsg('检查服务')
        self.svc.guards.accept(self.b, m)
        self.svc.same_owner.consent(self.b, False)
        self.assertEqual((await self.svc.tell(self.b, '完成', m['mid']))['why'], 'bad_reply')

    async def test_p482_untrusted_payload_and_name_still_reviewed(self):
        await self.confirm()
        async def refuse(text):
            return peer_guard.Verdict(False, 'jev_refused')
        with patch.object(self.svc, '_outbound', refuse):
            self.assertFalse(await self.svc.same_owner.forward(self.b, pmsg('普通任务')))
        self.assertFalse(self.sends)
        self.assertFalse(self.svc.same_owner.row(self.b).get('forwarded'))
        seen = []
        async def audit(text):
            seen.append(text)
            return peer_guard.Verdict(True)
        with patch.object(self.svc, '_outbound', audit):
            self.assertTrue(await self.svc.same_owner.forward(self.b, pmsg('普通任务')))
        self.assertIn('from_friend', seen[0])
        self.assertNotIn('Reply command:', seen[0])

    async def test_bilateral_revoke_and_replayed_consent(self):
        await self.confirm()
        old = next(o for _,o in reversed(self.remote.net.sent) if o['t']=='same_owner')
        await self.remote.write('fr_set', {'friend':self.a,'op':'group','value':'friend'}, device=self.odid)
        await self.pump()
        self.assertFalse(self.svc.same_owner.active(self.b))
        self.assertFalse(self.remote.same_owner.active(self.a))
        self.svc.on_app(self.b, old)
        self.assertFalse(self.svc.same_owner.active(self.b))
        self.assertEqual((await self.svc.same_owner.status(self.b))['code'], 403)

    async def test_spoofed_account_card_and_remote_only_claim_refused(self):
        self.svc.store.set_card(self.b, {'owner':'same-account','name':'same-account'})
        self.svc.on_app(self.b, {'t':'same_owner','seq':1,'token':'a'*32,'echo':'b'*32,'account':'same-account'})
        self.assertFalse(self.svc.same_owner.active(self.b))
        self.svc.on_app(self.b, {'t':'owner_status','r':'c'*32})
        await settle()
        self.assertEqual(self.net.of('owner_status_res')[-1][1]['code'], 403)
        self.assertNotIn('status', self.net.of('owner_status_res')[-1][1])

    async def test_source_name_snapshot_cannot_change_after_filter(self):
        await self.confirm()
        self.store.set_card(self.b, {'name':'original-name'})
        outbound = self.svc._outbound
        async def change_name(text):
            verdict = await outbound(text)
            if 'from_friend' in text:
                self.store.set_card(self.b, {'name':SECRET})
            return verdict
        with patch.object(self.svc, '_outbound', change_name):
            self.assertTrue(await self.svc.same_owner.forward(self.b, pmsg('检查服务')))
        self.assertNotIn(SECRET, self.pages[0][0]['text'])
        self.assertIn('original-name', self.pages[0][0]['text'])

    async def test_revocation_during_status_filter_returns_403(self):
        await self.confirm()
        self.host.meter_state = {'model':'gpt-6.1-sol'}
        outbound = self.svc._outbound
        async def revoke(text):
            verdict = await outbound(text)
            self.svc.same_owner.consent(self.b, False)
            return verdict
        with patch.object(self.svc, '_outbound', revoke):
            await self.svc.same_owner.reply_status(self.b, 'd'*32)
        packet = self.net.of('owner_status_res')[-1][1]
        self.assertEqual(packet['code'],403)
        self.assertNotIn('status',packet)

    async def test_source_name_is_filtered_before_main_history(self):
        await self.confirm()
        for name in (SECRET, '内部报价'):
            self.store.set_setting('never_tell_extra', ['内部报价'])
            self.store.set_card(self.b, {'name':name})
            self.assertFalse(await self.svc.same_owner.forward(self.b, pmsg('检查服务')))
        self.assertFalse(self.pages)
        self.assertFalse(self.sends)

    async def test_confirmed_tell_goes_directly_to_main_without_question(self):
        await self.confirm()
        m = pmsg('检查后台任务进展')
        self.svc._on_pmsg(self.b, m)
        await settle()
        self.assertFalse(self.svc.asks)
        self.assertFalse(self.sess.calls)
        self.assertEqual(len(self.sends), 1)
        self.assertIn(m['text'], self.sends[0].text)

    async def test_tell_between_hosts_routes_to_main_and_marks_source(self):
        await self.confirm()
        res = await self.remote.tell(self.a, '检查后台任务进展')
        self.assertEqual(res['route'], 'main')
        packet = self.remote.net.of('pmsg')[-1][1]
        self.svc.on_app(self.b, packet)
        if self.svc.jobs:
            await asyncio.wait_for(asyncio.gather(*tuple(self.svc.jobs)), 2)
        await settle()
        self.assertEqual(len(self.sends), 1)
        self.assertFalse(self.sess.calls)
        self.assertFalse(self.svc.asks)
        self.assertIn(self.b, self.pages[0][0]['text'])
        self.assertFalse(await self.svc.same_owner.forward(self.b, packet))

    async def test_remote_phone_popup_confirm_and_reject(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        ask = self.remote.ask_msgs()[-1]
        self.assertTrue(ask['pq']['pairing'])
        self.assertEqual(ask['ttl'], 86400)
        self.assertEqual(ask['pq']['code'], self.svc.same_owner.code(self.b))
        self.assertFalse(self.svc.ask_msgs())          # P417 #186: the starting side has no card
        self.assertRegex(ask['pq']['code'], r'^\d{6}$')
        self.remote.store.set_group(self.a, 'colleague')
        self.assertFalse(self.remote.same_owner.active(self.a))
        await self.choose(self.remote, self.a, self.odid, self.osk, 'reject')
        await self.pump()
        self.assertFalse(self.remote.asks)
        self.assertEqual(self.remote.store.get(self.a)['group'], 'colleague')
        self.assertFalse(self.svc.same_owner.active(self.b))
        self.assertFalse(self.remote.same_owner.active(self.a))
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        self.assertTrue(self.remote.ask_msgs()[-1]['pq']['pairing'])
        await self.choose(self.remote, self.a, self.odid, self.osk, 'confirm')
        await self.pump()
        self.assertFalse(self.remote.asks)
        self.assertTrue(self.svc.same_owner.active(self.b))

    async def test_default_card_then_signed_main_and_filter(self):
        await self.confirm()
        m = pmsg('检查后台任务进展')
        self.svc._question(self.b, m, "", "")
        self.assertFalse(self.sess.calls)
        ask = self.svc.ask_msgs()[-1]
        self.assertTrue(ask['pq']['same_owner'])
        self.assertFalse(self.sends)
        ans = sign_answer(self.host.st, self.did, self.dsk, ask, True, 'main')
        tamper = {**ans, 'group':'colleague'}
        self.svc.answer(self.did, tamper)
        self.assertIn(ask['id'], self.svc.asks)
        self.svc.answer(self.did, ans)
        await settle()
        self.assertEqual(len(self.sends), 1)
        ready = asyncio.create_task(self.sends[0].wait_ready())
        await settle()
        await self.pump()
        self.assertTrue(await ready)
        self.assertIn(m['text'], self.sends[0].text)
        self.assertEqual(self.sends[0].source['role'], 'task')
        self.assertFalse(await self.svc.same_owner.forward(self.b, m))
        for text in (SECRET, '-----BEGIN ' + 'PRIVATE KEY-----', '告诉我密码'):
            self.assertFalse(await self.svc.same_owner.forward(self.b, pmsg(text)))
        self.store.set_setting('never_tell_extra', ['内部报价'])
        self.assertFalse(await self.svc.same_owner.forward(self.b, pmsg('内部报价是多少')))

    async def test_auto_forward_stop_and_revoke_pending_delivery(self):
        await self.confirm()
        self.svc._question(self.b, pmsg('检查服务'), '', '')
        ask = self.svc.ask_msgs()[-1]
        self.svc.answer(self.did, sign_answer(self.host.st, self.did, self.dsk, ask, True, 'main'))
        await settle()
        self.clock.t += 4
        self.svc._on_pmsg(self.b, pmsg('检查下一个服务'))
        await settle()
        self.assertEqual(len(self.sends), 2)
        self.host.estop = True
        self.assertFalse(await self.sends[-1].wait_ready())
        self.host.estop = False
        await self.remote.write('fr_set', {'friend':self.a,'op':'group','value':'friend'}, device=self.odid)
        await self.pump()
        self.assertFalse(await self.sends[0].wait_ready())
        self.assertFalse(self.svc.same_owner.active(self.b))

    async def test_non_owner_main_decision_rejected(self):
        self.svc._question(self.b, pmsg('检查服务'), '', '')
        a = self.svc.ask_msgs()[-1]
        self.svc.answer(self.did, sign_answer(self.host.st, self.did, self.dsk, a, True, 'main'))
        await settle()
        self.assertIn(a['id'], self.svc.asks)
        self.assertFalse(self.sends)

    async def test_status_roundtrip_allowlist_and_revocation(self):
        await self.confirm()
        self.other.meter_state = {'model':'gpt-6.1-sol','secret':'never','week':{'pct':5,'reset':__import__('time').time()+86400}}
        job = asyncio.create_task(self.svc.cli({'t':'status','friend':self.b}))
        await asyncio.sleep(0)
        await self.pump()
        result = await job
        self.assertTrue(result['ok'])
        self.assertIn('load', result['status'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('path', json.dumps(result))
        self.assertIn('week', result['status']['quota'])
        self.svc.same_owner.consent(self.b, False)
        await self.pump()
        self.assertEqual((await self.svc.cli({'t':'status','friend':self.b}))['code'], 403)

    async def test_status_counts_and_stale_quota_are_truthful(self):
        now = __import__('time').time()
        self.host.meter_state = {'source_at':now,'h5':{'pct':10,'reset':now+4*3600}}
        self.host.busy = SimpleNamespace(items=lambda:[{'kind':'temp'},{'kind':'ceo'}])
        self.host.agent.status = 'working'
        status = aggregate(self.host)
        self.assertEqual(status['agents'], 2)
        self.assertEqual(status['background'], 1)
        self.assertAlmostEqual(status['quota']['h5']['used_pct_per_hour'], 10, places=2)
        self.host.meter_state['source_at'] = now-600
        self.assertIsNone(aggregate(self.host)['quota']['h5']['used_pct'])

    async def test_status_remote_payload_cannot_leak_arbitrary_fields(self):
        v = clean_status({'harness':'https://private/path','model':'https://private/path', 'load':float('nan'),
                          'quota':{'week':{'used_pct':10,'path':'/secret'}},'text':'secret'})
        self.assertIsNone(v['harness'])
        self.assertIsNone(v['model'])
        self.assertIsNone(v['load'])
        self.assertNotIn('secret', json.dumps(v))

    async def test_restart_confirmation_state_and_block_invalidate(self):
        await self.confirm()
        from agentj.same_owner import SameOwner
        restored = SameOwner(self.svc)
        self.assertTrue(restored.active(self.b))
        await self.svc.write('fr_set', {'friend':self.b,'op':'block','value':''}, device=self.did)
        self.assertFalse(restored.active(self.b))
        await self.svc.write('fr_set', {'friend':self.b,'op':'unblock','value':''}, device=self.did)
        self.assertFalse(restored.active(self.b))

    async def test_peer_consent_without_phone_signature_and_tamper_refused(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        await self.choose(self.remote, self.a, self.odid, self.osk, 'confirm')
        packet = next(o for _,o in reversed(self.remote.net.sent) if o['t']=='same_owner' and o['token'])
        for bad in ({**packet, 'proof':None}, {**packet, 'proof':{**packet['proof'],'sig':'a'*86}},
                    {**packet, 'proof':{**packet['proof'],'device':'fake-device'}},
                    {**packet, 'proof':{**packet['proof'],'pub':wire.b64u(self.remote.keys.ed_pub)}}):
            self.svc.on_app(self.b, bad)
            self.assertFalse(self.svc.same_owner.row(self.b).get('remote'))
            self.assertFalse(self.svc.same_owner.active(self.b))
        self.svc.on_app(self.b, packet)
        self.assertTrue(self.svc.same_owner.row(self.b).get('remote'))

    async def test_delayed_revoke_blocks_delivery_via_fresh_remote_check(self):
        await self.confirm()
        self.assertTrue(await self.svc.same_owner.forward(self.b, pmsg('检查进度')))
        await self.remote.write('fr_set', {'friend':self.a,'op':'group','value':'friend'},device=self.odid)
        # Deliberately withhold the revoke; route only the fresh status request/reply.
        ready = asyncio.create_task(self.sends[-1].wait_ready())
        await settle()
        req = self.net.of('owner_status')[-1][1]
        self.remote.on_app(self.a, req)
        await settle()
        self.svc.on_app(self.b, self.remote.net.of('owner_status_res')[-1][1])
        self.assertFalse(await ready)

    async def test_reply_to_forwarded_task_does_not_create_another_task(self):
        await self.confirm()
        m = pmsg('检查进度')
        await self.svc.same_owner.forward(self.b, m)
        result = await self.svc.tell(self.b, '已完成检查', m['mid'])
        self.assertTrue(result['ok'])
        reply = self.net.of('pmsg')[-1][1]
        self.assertEqual(reply['irt'], m['mid'])
        self.assertTrue(reply['end'])
        self.remote._on_pmsg(self.a, reply)
        await settle()
        self.assertFalse(self.remote.asks)
        self.assertFalse(self.remote.sessions.calls)
        self.assertEqual(len(self.other_sends), 1)
        self.assertIn('不是新任务', self.other_sends[0].text)
        self.assertNotIn('Reply command:', self.other_sends[0].text)
        self.assertFalse((await self.svc.tell(self.b, '完成', 'unknown'))['ok'])

    async def test_main_scope_real_webcrypto_roundtrip(self):
        from test_p396_friend_signature import Roundtrip
        await self.confirm()
        self.svc._question(self.b, pmsg('检查进度'), '', '')
        ask = self.svc.ask_msgs()[-1]
        self.svc.answer(self.did, Roundtrip.signed(self, ask, True, 'main'))
        await settle()
        self.assertNotIn(ask['id'], self.svc.asks)
        self.assertTrue(self.svc.same_owner.active(self.b))
        self.assertEqual(len(self.sends), 1)

    async def test_status_model_uses_secret_filter_and_visible_remote_presence(self):
        await self.confirm()
        self.other.meter_state = {'model':SECRET}
        self.other.sessions = {1:SimpleNamespace(state='ready',fg=True)}
        job = asyncio.create_task(self.svc.same_owner.status(self.b))
        await settle()
        await self.pump()
        res = await job
        self.assertTrue(res['status']['in_use'])
        self.assertIsNone(res['status']['model'])

    async def test_code_change_and_old_signed_confirm_cannot_activate(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        old = self.svc.same_owner.challenge(self.b)
        await self.choose(self.svc, self.b, self.did, self.dsk, 'confirm')
        packet = self.net.of('same_owner')[-1][1]
        # A remote self-reported key cannot inherit the verified challenge.
        row = self.svc.same_owner.row(self.b)
        self.svc.on_app(self.b, {'t':'same_owner','seq':row['remote_seq']+1,'token':'','echo':'',
                               'nonce':'d'*32,'pub':wire.b64u(self.remote.keys.ed_pub)})
        self.assertNotEqual(old, self.svc.same_owner.challenge(self.b))
        self.assertFalse(self.svc.same_owner.active(self.b))
        self.remote.on_app(self.a, {**packet, 'seq':999})
        self.assertFalse(self.remote.same_owner.active(self.a))

    async def test_invitation_expires_after_24h_not_question_ttl(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        self.clock.t += 121
        self.remote.expire_asks()
        self.assertTrue(self.remote.ask_msgs())
        self.remote.restore_asks()
        self.assertTrue(self.remote.ask_msgs())
        self.clock.t += 86400
        self.remote.expire_asks()
        self.assertFalse(self.remote.ask_msgs())
        self.assertFalse(self.remote.same_owner.active(self.a))
        self.assertEqual((await self.choose(self.remote, self.a, self.odid, self.osk, 'confirm'))[1], 'expired')


    # ---------------------------------------------------------------- P417 #186 (Leo 10-21: start = consent, other side once)
    def pairing_asks(self, host):
        return [o for o in host.out if o.get('t') == 'ask' and (o.get('pq') or {}).get('pairing')]

    def info(self, svc, fid):
        return next(f for f in svc.list_view()['friends'] if f['id'] == fid)

    def proof_for(self, svc, fid, did, sk, op='owner-confirm', value=None, signer=None):
        target = {'friend': fid, 'op': op, 'value': value if value is not None else svc.same_owner.challenge(fid)}
        n, ts = __import__('secrets').token_hex(16), int(__import__('time').time()*1000)
        sig = wire.b64u((signer or sk).sign(controls.signed_message(svc.host.channel, did, 'fr_set', n, ts, controls.object_digest('fr_set', target))))
        return target, {'channel': svc.host.channel, 'device': did, 'pub': wire.b64u(sk.public_key().public_bytes_raw()), 'n': n, 'ts': ts, 'sig': sig}

    async def test_p417_start_is_consent_initiator_gets_no_card_and_sees_code_in_place(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        self.assertFalse(self.svc.asks)
        self.assertFalse(self.pairing_asks(self.host))               # the starting side never gets a card
        self.assertEqual(len(self.pairing_asks(self.other)), 1)      # the other side's remotes: one card
        mine, theirs = self.info(self.svc, self.b), self.info(self.remote, self.a)
        code = self.remote.ask_msgs()[-1]['pq']['code']
        self.assertRegex(code, r'^\d{6}$')
        self.assertEqual((mine['same_owner'], mine['same_owner_code'], mine['same_owner_wait']), ('pending', code, True))
        self.assertEqual((theirs['same_owner'], theirs['same_owner_code'], theirs['same_owner_wait']), ('pending', code, False))
        # one tap on the other side completes it — no second tap on the starting side
        rid = self.remote.ask_msgs()[-1]['id']
        self.assertEqual((await self.choose(self.remote, self.a, self.odid, self.osk, 'confirm'))[:2], (True, None))
        await self.pump()
        self.assertTrue(self.svc.same_owner.active(self.b))
        self.assertTrue(self.remote.same_owner.active(self.a))
        self.assertEqual(self.info(self.svc, self.b)['same_owner'], 'active')
        self.assertEqual(self.info(self.remote, self.a)['same_owner'], 'active')
        self.assertNotIn('same_owner_code', self.info(self.svc, self.b))
        self.assertEqual([o['result'] for o in self.other.of('ask_done') if o['id'] == rid], ['gone'])
        self.assertFalse(self.remote.asks)

    async def test_p417_start_by_a_since_unpaired_remote_is_no_consent(self):
        # security review B1: the stored start is checked again when it would settle — an unpaired remote's tap never counts
        await self.choose(self.svc, self.b, self.did, self.dsk)
        self.host.st.remove_device(self.did)
        await self.pump()
        self.assertIsNone(self.svc.same_owner.row(self.b).get('init'))
        self.assertFalse(self.svc.same_owner.row(self.b).get('token'))
        await self.choose(self.remote, self.a, self.odid, self.osk, 'confirm')
        await self.pump()
        self.assertFalse(self.svc.same_owner.active(self.b))
        self.assertFalse(self.remote.same_owner.active(self.a))

    async def test_p417_any_enrolled_remote_confirms_not_first_nor_pinned(self):
        second, ssk = device(self.other.st)
        third, tsk = device(self.other.st)
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        pinned = self.remote.same_owner.row(self.a)['pub']
        first = next(iter(self.other.st.devices()))
        did, sk = next((d, k) for d, k in ((self.odid, self.osk), (second, ssk), (third, tsk))
                       if wire.b64u(k.public_key().public_bytes_raw()) != pinned and d != first)
        self.assertEqual((await self.choose(self.remote, self.a, did, sk, 'confirm'))[:2], (True, None))
        await self.pump()
        self.assertTrue(self.svc.same_owner.active(self.b))
        self.assertTrue(self.remote.same_owner.active(self.a))
        # the starting side may be any of its remotes too: start again from another one = same invitation, no card
        self.assertFalse(self.svc.asks)

    async def test_p417_the_invited_side_puts_its_most_recently_seen_remote_in_the_code(self):
        second, ssk = device(self.other.st)
        with self.other.st.devices_lock():
            d = self.other.st.devices()
            d[second]['seen'] = int(__import__('time').time()) + 1000
            from agentj.state import _write_private
            _write_private(self.other.st.devices_path, json.dumps(d).encode())
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        self.assertEqual(self.remote.same_owner.row(self.a)['pub'], wire.b64u(ssk.public_key().public_bytes_raw()))

    async def test_p417_unenrolled_keyless_or_unsigned_remote_is_refused(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        import os
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        stranger = Ed25519PrivateKey.generate()
        keyless = self.other.st.add_device(os.urandom(32), 'no-key')
        cases = [
            self.proof_for(self.remote, self.a, 'not-a-device', stranger),                 # not on the allowlist
            self.proof_for(self.remote, self.a, keyless, stranger),                         # paired, no approval key
            self.proof_for(self.remote, self.a, self.odid, self.osk, signer=stranger),      # enrolled, wrong signature
        ]
        t, pr = self.proof_for(self.remote, self.a, self.odid, self.osk)
        cases.append((t, {**pr, 'device': keyless}))                                         # proof of another device
        cases.append((t, {**pr, 'pub': wire.b64u(stranger.public_key().public_bytes_raw())}))  # a key it does not hold
        for target, proof in cases:
            res = await self.remote.write('fr_set', target, device=proof['device'], proof=proof)
            self.assertEqual(res[:2], (False, 'phone_required'))
        self.assertEqual((await self.remote.write('fr_set', t, device=self.odid))[:2], (False, 'phone_required'))
        self.assertFalse(self.remote.same_owner.row(self.a).get('token'))
        self.assertTrue(self.remote.asks)
        # a start packet carrying a forged signature never counts as the other side's consent
        await self.pump()
        self.assertFalse(self.svc.same_owner.active(self.b))

    async def test_p417_starting_again_keeps_one_invitation_and_one_card(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        code = self.info(self.svc, self.b)['same_owner_code']
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        self.assertEqual(self.info(self.svc, self.b)['same_owner_code'], code)
        self.assertEqual(len(self.pairing_asks(self.other)), 1)
        self.assertFalse(self.other.of('ask_done'))
        self.assertFalse(self.pairing_asks(self.host))

    async def test_p417_both_sides_start_at_once_merge_into_one_invitation(self):
        # both owners tap 「同一个主人」 before either packet arrives: two signed starts = two consents, one code
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.choose(self.remote, self.a, self.odid, self.osk)
        await self.pump()
        self.assertEqual(self.svc.same_owner.challenge(self.b), self.remote.same_owner.challenge(self.a))
        self.assertTrue(self.svc.same_owner.active(self.b))
        self.assertTrue(self.remote.same_owner.active(self.a))
        self.assertFalse(self.svc.asks or self.remote.asks)

    async def test_p417_other_side_starting_while_its_card_stands_completes_it(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        code = self.remote.ask_msgs()[-1]['pq']['code']
        rid = self.remote.ask_msgs()[-1]['id']
        await self.choose(self.remote, self.a, self.odid, self.osk)        # taps start in Settings instead of the card
        await self.pump()
        self.assertEqual(self.svc.same_owner.code(self.b), code)          # same invitation, same code
        self.assertTrue(self.svc.same_owner.active(self.b))
        self.assertTrue(self.remote.same_owner.active(self.a))
        self.assertEqual([o['result'] for o in self.other.of('ask_done') if o['id'] == rid], ['gone'])

    async def test_p417_old_cards_close_with_gone(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        current = self.remote.ask_msgs()[-1]['id']
        # a card from an older round (its code is not the current one) — e.g. left over from before the update
        self.remote._question(self.a, {'mid': 'e' * 32, 'thread': 'e' * 32, 'text': 'old'}, '', '', pairing=True)
        stale = next(a for a in self.remote.asks.values() if a.rid != current)
        stale.extra['challenge'] = 'f' * 32
        t, pr = self.proof_for(self.remote, self.a, self.odid, self.osk, op='owner-reject', value='f' * 32)
        self.assertEqual((await self.remote.write('fr_set', t, device=self.odid, proof=pr))[:2], (False, 'expired'))
        self.assertEqual([o['result'] for o in self.other.of('ask_done') if o['id'] == stale.rid], ['gone'])
        self.assertIn(current, self.remote.asks)                            # the current card stays
        # rejecting the current one closes it with `gone` too, and blocking closes a friend's cards the same way
        self.assertEqual((await self.choose(self.remote, self.a, self.odid, self.osk, 'reject'))[:2], (True, None))
        self.assertEqual([o['result'] for o in self.other.of('ask_done') if o['id'] == current], ['gone'])
        self.svc._question(self.b, pmsg('检查服务'), '', '')
        rid = self.svc.ask_msgs()[-1]['id']
        await self.svc.write('fr_set', {'friend': self.b, 'op': 'block', 'value': ''}, device=self.did)
        self.assertEqual([o['result'] for o in self.host.of('ask_done') if o['id'] == rid], ['gone'])
        self.assertNotIn('ended', [o['result'] for o in self.host.of('ask_done') + self.other.of('ask_done')])

    async def test_p417_reject_and_expiry_tell_both_pages_why(self):
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await self.pump()
        await self.choose(self.remote, self.a, self.odid, self.osk, 'reject')
        await self.pump()
        for svc, fid in ((self.svc, self.b), (self.remote, self.a)):
            v = self.info(svc, fid)
            self.assertEqual((v['same_owner'], v.get('same_owner_end')), ('none', 'declined'))
        # a new start clears the old reason and never reuses the old code
        await self.choose(self.remote, self.a, self.odid, self.osk)
        self.assertIsNone(self.remote.same_owner.code(self.a))
        self.assertNotIn('same_owner_end', self.info(self.remote, self.a))
        await self.pump()
        self.assertFalse(self.remote.asks)
        self.assertEqual(len(self.svc.ask_msgs()), 1)
        # this time nobody answers for 24 hours
        self.clock.t += 86401
        self.svc.expire_asks()
        await self.pump()
        for svc, fid in ((self.svc, self.b), (self.remote, self.a)):
            v = self.info(svc, fid)
            self.assertEqual((v['same_owner'], v.get('same_owner_end')), ('none', 'expired'))
        self.clock.t += 86401                                               # the hint is kept for a day, then dropped
        self.assertNotIn('same_owner_end', self.info(self.svc, self.b))

    async def test_p417_older_peer_without_start_consent_keeps_the_two_card_flow(self):
        # 0.25.0a1 host on the other side: its packets have no `v`, it only accepts a confirm signed by the key in the code
        rows = []
        async def old_pump():
            for _ in range(20):
                await settle()
                n = 0
                for i, (src, dst, fid, old) in enumerate(((self.svc, self.remote, self.a, False), (self.remote, self.svc, self.b, True))):
                    for _, obj in src.net.sent[self.positions[i]:]:
                        if obj['t'] == 'same_owner':
                            dst.on_app(fid, {k: v for k, v in obj.items() if not (old and k == 'v')})
                            n += 1
                    self.positions[i] = len(src.net.sent)
                if not n:
                    return
        await self.choose(self.svc, self.b, self.did, self.dsk)
        await old_pump()
        self.assertFalse(self.svc.same_owner.row(self.b).get('token'))      # our start is not sent as consent
        self.assertEqual(len(self.svc.ask_msgs()), 1)                       # so this side gets the old card
        self.assertFalse(self.info(self.svc, self.b)['same_owner_wait'])
        second, ssk = device(self.host.st)
        t, pr = self.proof_for(self.svc, self.b, second, ssk)
        self.assertEqual((await self.svc.write('fr_set', t, device=second, proof=pr))[:2], (False, 'peer_old'))
        self.assertEqual((await self.choose(self.svc, self.b, self.did, self.dsk, 'confirm'))[:2], (True, None))
        await self.choose(self.remote, self.a, self.odid, self.osk, 'confirm')
        await old_pump()
        self.assertTrue(self.svc.same_owner.active(self.b))
        self.assertTrue(self.remote.same_owner.active(self.a))


class AdminApprovalTests(__import__('unittest').TestCase):
    def test_same_owner_source_covers_shared_session_continuation(self):
        from agentj import provenance
        so = {'kind':'same_owner','role':'task'}
        self.assertTrue(provenance.same_owner_source(SimpleNamespace(cur_send=SimpleNamespace(source=so))))
        cont = SimpleNamespace(cur_send=None, pending_source=so, untrusted=lambda: False)
        self.assertTrue(provenance.same_owner_source(cont))   # cur_send cleared, native turn still running
        self.assertFalse(provenance.same_owner_source(SimpleNamespace(cur_send=None, pending_source=None)))
        self.assertFalse(provenance.same_owner_source(None))

    def test_elevate_uses_same_owner_source_for_grants(self):
        import inspect
        from agentj import elevate
        src = inspect.getsource(elevate)
        self.assertIn('same_owner_task = provenance.same_owner_source(', src)
        self.assertIn('not same_owner_task and (auto := await self.grants.auto(', src)
        self.assertIn('None if same_owner_task else self.grants.offer(c)', src)

    def test_same_owner_always_shows_admin_card_even_with_live_grant(self):
        from test_p393_elevate_grant import run, A, REAL
        if not REAL:
            self.skipTest('requires existing root-owned read-only fixture')
        async def go(r):
            _, first = await r.approve(A)
            self.assertEqual(first['result'], 'done')
            r.host.agent.cur_send = SimpleNamespace(source={'kind':'same_owner','role':'task'})
            card, result = await r.deny_next(A)
            self.assertIsNotNone(card)
            self.assertNotIn('grant', card)
            self.assertNotEqual(result.get('result'), 'done')
        run(go)

