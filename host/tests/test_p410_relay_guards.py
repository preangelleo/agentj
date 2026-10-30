"""P410: deterministic lifecycle, chain propagation and phone-only handover."""
import _hermetic  # noqa: F401
from types import SimpleNamespace
from unittest.mock import patch

from test_p71_peer_service import Base, pmsg, settle, sign_answer
from agentj import controls, provenance, wire
from agentj.relay_guards import RelayGuards, message


class Guards(Base):
    def env(self, origin=None, hops=1):
        return {'chain': 'c' * 32, 'origin': origin or self.friend().id, 'hops': hops}

    async def test_loop_hops_and_malformed_rejected_both_sides(self):
        fid = self.friend().id
        for env, reason in ((self.env(self.svc.keys.id), 'loop'), (self.env(fid, 5), 'hops'),
                            ({'chain': 'bad'}, 'shape'), (self.env(fid, True), 'shape')):
            self.svc._on_pmsg(fid, pmsg('检查进度', relay=env))
            self.assertEqual(self.net.of('relay_state')[-1][1]['state'], reason)
            self.assertEqual(self.host.notices[-1], message(reason, 'zh'))
        await settle()
        self.assertFalse(self.sess.calls)
        self.assertFalse(self.store._hist_all(fid))

    async def test_sliding_rate_default_config_and_restart_dedup(self):
        fid = self.friend().id
        self.assertEqual(self.store.settings()['relay_per_minute'], 30)
        self.assertIsNone(self.store.set_setting('relay_per_minute', 2))
        self.assertEqual(self.store.set_setting('relay_per_minute', False), 'bad_value')
        a, b, c = (pmsg('check') for _ in range(3))
        for m in (a, b):
            self.assertTrue(self.svc.guards.accept(fid, m))
        fresh = RelayGuards(self.svc)
        self.assertFalse(fresh.accept(fid, a))
        self.assertFalse(fresh.accept(fid, c))
        self.assertEqual(self.net.of('relay_state')[-1][1]['state'], 'rate')
        self.clock.t += 60
        self.assertTrue(fresh.accept(fid, pmsg('later')))
        # Rate is per friend, not global.
        self.assertTrue(fresh.accept(self.friend().id, pmsg('other')))

    async def test_chain_inherits_from_current_and_shared_pending_source(self):
        a, b = self.friend().id, self.friend().id
        env = self.env(a, 2)
        for agent in (SimpleNamespace(cur_send=SimpleNamespace(source={'relay': env})),
                      SimpleNamespace(cur_send=None, pending_source={'relay': env})):
            self.host.agent = agent
            self.assertTrue((await self.svc.tell(b, '检查任务'))['ok'])
            packet = self.net.of('pmsg')[-1][1]
            self.assertEqual(packet['relay'], {**env, 'hops': 3})
        self.host.agent.pending_source['relay']['hops'] = 4
        self.assertFalse((await self.svc.tell(b, '检查任务'))['ok'])

    async def test_return_to_origin_even_with_rewritten_origin(self):
        fid = self.friend().id
        sent = await self.svc.tell(fid, '检查任务')
        packet = self.net.of('pmsg')[-1][1]
        self.assertEqual(packet['relay']['origin'], self.svc.keys.id)
        bounce = pmsg('再转交', relay={**packet['relay'], 'hops': 2, 'origin': fid})
        self.assertFalse(self.svc.guards.accept(fid, bounce))
        self.assertEqual(self.net.of('relay_state')[-1][1]['state'], 'loop')
        self.assertTrue(sent['ok'])

    async def test_claim_release_pending_expiry_persist_no_reexecution(self):
        fid = self.friend().id
        m = pmsg('check')
        g = self.svc.guards
        self.assertTrue(g.accept(fid, m))
        self.assertTrue(g.claim(fid, m))
        self.clock.t += 1799
        g.sweep()
        self.assertEqual(g.row(fid, m['mid'])['state'], 'claimed')
        self.clock.t += 1
        RelayGuards(self.svc).sweep()
        self.assertEqual(g.row(fid, m['mid'])['state'], 'released')
        self.assertFalse(g.claim(fid, m))
        self.assertFalse(g.accept(fid, m))
        self.assertFalse(self.sess.calls)
        self.assertEqual(self.net.of('relay_state')[-1][1]['state'], 'released')
        n = len(self.host.notices)
        g.sweep()
        self.assertEqual(len(self.host.notices), n)
        self.assertIsNone(self.store.set_setting('relay_pending_seconds', 12))
        pending = pmsg('waiting')
        self.assertTrue(g.accept(fid, pending))
        self.clock.t += 12
        g.sweep()
        self.assertEqual(g.row(fid, pending['mid'])['state'], 'expired')
        self.assertFalse(g.claim(fid, pending))

    async def test_sender_receives_reason_and_late_pack_does_not_erase(self):
        fid = self.friend().id
        res = await self.svc.tell(fid, '检查任务')
        mid = res['mid']
        self.svc.on_app(fid, {'t': 'relay_state', 'mid': mid, 'state': 'claimed'})
        self.assertEqual(self.svc.guards.row(fid, mid, 'out')['state'], 'claimed')
        self.svc.on_app(fid, {'t': 'relay_state', 'mid': mid, 'state': 'released'})
        self.svc.on_app(fid, {'t': 'pack', 'mid': mid, 's': 'got'})
        self.assertEqual(self.store._hist_all(fid)[-1]['s'], 'released')
        self.assertIn(message('released', 'zh'), self.host.notices)
        before = len(self.host.notices)
        self.svc.on_app(fid, {'t': 'relay_state', 'mid': '0'*16, 'state': 'loop'})
        self.assertEqual(before, len(self.host.notices))
        self.assertNotEqual(message('released', 'zh'), message('released', 'en'))

    async def hold(self, fid):
        target = {'friend': fid, 'op': 'hold-for-owner', 'value': 'on'}
        self.assertEqual((await self.svc.write('fr_set', target))[1], 'phone_required')
        n, ts = 'a'*32, int(self.clock()*1000)
        sig = wire.b64u(self.dsk.sign(controls.signed_message(self.host.channel, self.did, 'fr_set', n, ts, controls.object_digest('fr_set', target))))
        proof = {'n': n, 'ts': ts, 'sig': sig}
        self.assertIsNone(controls.check(self.host.st, controls.Nonces(), self.host.channel, self.did, proof, 'fr_set', target))
        self.assertTrue((await self.svc.write('fr_set', target, device=self.did, proof=proof))[0])

    async def test_hold_requires_signed_phone_and_allows_only_one_main_delivery(self):
        fid = self.friend().id
        self.assertFalse(self.store.get(fid).get('hold_for_owner', False))
        await self.hold(fid)
        self.assertTrue(self.svc.list_view()['friends'][0]['hold_for_owner'])
        sends = []
        self.host.agent = SimpleNamespace(submit=sends.append)
        self.host.hist_add = lambda *a: {'id': 1}
        m = pmsg('检查任务')
        self.svc._on_pmsg(fid, m)
        await settle()
        self.assertFalse(self.sess.calls)
        self.assertFalse(sends)
        ask = self.svc.ask_msgs()[-1]
        self.assertTrue(ask['pq']['handover'])
        # Wrong key/signature does not release the card.
        self.svc.answer(self.did, {**sign_answer(self.host.st,self.did,self.dsk,ask,True), 'sig': 'bad'})
        self.assertTrue(self.svc.has_ask(ask['id']))
        self.svc.answer(self.did, sign_answer(self.host.st,self.did,self.dsk,ask,True))
        await settle()
        self.assertEqual(len(sends), 1)
        self.assertFalse(provenance.is_owner(sends[0].source))
        self.assertTrue(provenance.delegated_turn(SimpleNamespace(cur_send=sends[0])))
        self.assertEqual(provenance.from_name(sends[0].source), provenance.FRIEND_TASK)
        self.assertTrue(provenance.same_owner_source(SimpleNamespace(cur_send=None, pending_source=sends[0].source)))
        self.assertEqual(self.svc.guards.row(fid,m['mid'])['state'], 'pending')
        self.assertTrue(await sends[0].wait_ready())
        self.assertEqual(self.svc.guards.row(fid,m['mid'])['state'], 'claimed')
        self.svc._on_pmsg(fid,m)
        await settle()
        self.assertEqual(len(sends),1)
        reply = await self.svc.tell(fid, '任务完成', reply_to=m['mid'])
        self.assertTrue(reply['ok'])
        self.assertEqual(self.svc.guards.row(fid,m['mid'])['state'],'done')

    async def test_hold_denied_or_expired_never_calls_main(self):
        fid = self.friend().id
        await self.hold(fid)
        for timeout in (False, True):
            self.clock.t += 11
            m=pmsg('check')
            self.svc._on_pmsg(fid,m)
            ask=self.svc.ask_msgs()[-1]
            if timeout:
                self.clock.t += self.store.settings()["relay_pending_seconds"]
                self.svc.expire_asks()
            else:
                self.svc.answer(self.did,sign_answer(self.host.st,self.did,self.dsk,ask,False))
            await settle()
            self.assertEqual(self.svc.guards.row(fid,m['mid'])['state'],'cancelled')
        self.assertFalse(self.sess.calls)

    async def test_queued_handover_expiry_cancels_before_adapter(self):
        fid=self.friend().id
        await self.hold(fid)
        sends=[]
        self.host.agent=SimpleNamespace(submit=sends.append)
        self.host.hist_add=lambda *a: {'id':1}
        self.svc._on_pmsg(fid,pmsg('check'))
        ask=self.svc.ask_msgs()[-1]
        self.svc.answer(self.did,sign_answer(self.host.st,self.did,self.dsk,ask,True))
        await settle()
        self.clock.t += 86400
        self.svc.guards.sweep()
        self.assertFalse(await sends[0].wait_ready())

    async def test_policy_cli_validation_and_cancel_pending_request(self):
        fid=self.friend().id
        res=await self.svc.cli({'t':'relay-policy','relay_per_minute':5,'relay_claim_seconds':20})
        self.assertEqual(res['relay_per_minute'],5)
        self.assertEqual(res['relay_claim_seconds'],20)
        self.assertFalse((await self.svc.cli({'t':'relay-policy','relay_claim_seconds':0}))['ok'])
        result=await self.svc.tell(fid,'检查任务')
        self.assertTrue((await self.svc.cli({'t':'cancel','friend':fid,'mid':result['mid']}))['ok'])
        self.assertEqual(self.net.of('relay_cancel')[-1][1]['mid'],result['mid'])
        self.assertEqual(self.svc.guards.row(fid,result['mid'],'out')['state'],'cancelled')
        m=pmsg('incoming')
        self.assertTrue(self.svc.guards.accept(fid,m))
        self.svc.on_app(fid,{'t':'relay_cancel','mid':m['mid']})
        self.assertFalse(self.svc.guards.claim(fid,m))

    async def test_late_claim_cannot_restart_or_extend_lease(self):
        fid=self.friend().id
        m=pmsg('check')
        self.assertTrue(self.svc.guards.accept(fid,m))
        self.clock.t += 86400
        self.assertFalse(self.svc.guards.claim(fid,m))
        result=await self.svc.tell(fid,'check')
        mid=result['mid']
        self.svc.on_app(fid,{'t':'relay_state','mid':mid,'state':'claimed'})
        claimed=self.svc.guards.row(fid,mid,'out')['claimed']
        self.clock.t += 100
        for state in ('pending','claimed',[],{}):
            self.svc.on_app(fid,{'t':'relay_state','mid':mid,'state':state})
        self.assertEqual(self.svc.guards.row(fid,mid,'out')['claimed'],claimed)
        self.svc.on_app(fid,{'t':'pack','mid':mid,'s':'got'})
        self.assertEqual(self.store._hist_all(fid)[-1]['s'],'claimed')

    async def test_hold_cannot_approve_unshown_tail(self):
        fid=self.friend(group='colleague').id
        await self.hold(fid)
        self.svc._on_pmsg(fid,pmsg('a'*4001))
        await settle()
        self.assertFalse(self.svc.asks)
        self.assertFalse(self.sess.calls)
        self.assertEqual(self.net.of('relay_state')[-1][1]['state'],'shape')

    async def test_same_owner_hold_overrides_direct_route_and_switch_off_keeps_card(self):
        fid=self.friend().id
        await self.hold(fid)
        with patch.object(self.svc.same_owner,'active',return_value=True):
            self.svc._on_pmsg(fid,pmsg('检查进度'))
            await settle()
        self.assertFalse(self.sess.calls)
        ask=self.svc.ask_msgs()[-1]
        self.assertTrue(ask['pq']['handover'])
        with self.store._edit('friends.json',{}) as rows:
            rows[fid]['hold_for_owner']=False
        await settle()
        self.assertTrue(self.svc.has_ask(ask['id']))

    async def test_friend_task_admin_never_reuses_owner_grant(self):
        from test_p393_elevate_grant import run, A, REAL
        if not REAL:
            self.skipTest('requires existing root-owned read-only fixture')
        async def go(r):
            _,first=await r.approve(A)
            self.assertEqual(first['result'],'done')
            r.host.agent.cur_send=SimpleNamespace(source={'kind':'friend_task','role':'task'})
            card,result=await r.deny_next(A)
            self.assertIsNotNone(card)
            self.assertNotIn('grant',card)
            self.assertNotEqual(result.get('result'),'done')
        await __import__('asyncio').to_thread(run,go)

    async def test_cancel_before_message_is_a_persistent_tombstone(self):
        fid=self.friend().id
        m=pmsg('late task')
        self.svc.on_app(fid,{'t':'relay_cancel','mid':m['mid']})
        self.assertFalse(RelayGuards(self.svc).accept(fid,m))
        self.assertFalse(self.sess.calls)

    async def test_terminal_sender_withdraws_only_its_message_from_outbox(self):
        fid=self.friend().id
        result=await self.svc.tell(fid,'check')
        with patch.object(self.net,'cancel_message',create=True) as cancel:
            self.svc.on_app(fid,{'t':'relay_state','mid':result['mid'],'state':'expired'})
            cancel.assert_called_once_with(fid,result['mid'])

    async def test_real_outbox_withdrawal_preserves_other_friends_and_receipts(self):
        from agentj.peer import PeerNet
        fid,other=self.friend().id,self.friend().id
        net=PeerNet(self.host.st,self.svc.keys,'ws://127.0.0.1:9',None,self.svc,clock=self.clock)
        try:
            a=pmsg('task',mid='1'*16)
            net.send(fid,a)
            net.send(other,{**a,'mid':'1'*16})
            net.send(fid,{'t':'relay_state','mid':'1'*16,'state':'done'})
            net.cancel_message(fid,'1'*16)
            rows=net.outbox_rows()
            self.assertEqual(len(rows),2)
            self.assertTrue(any(r['peer']==other and r['t']=='pmsg' for r in rows))
            self.assertTrue(any(r['peer']==fid and r['t']=='relay_state' for r in rows))
        finally:
            net.db.close()

    async def test_owner_turn_does_not_inherit_previous_shared_chain(self):
        fid=self.friend().id
        env=self.env(fid,4)
        self.host.agent=SimpleNamespace(cur_send=SimpleNamespace(source=None),pending_source={'relay':env})
        self.assertTrue((await self.svc.tell(fid,'new owner request'))['ok'])
        self.assertEqual(self.net.of('pmsg')[-1][1]['relay']['hops'],1)
        self.assertNotEqual(self.net.of('pmsg')[-1][1]['relay']['chain'],env['chain'])

    async def test_secret_policy_rejection_is_not_later_reported_as_unclaimed(self):
        fid=self.friend().id
        result=await self.svc.tell(fid,'check')
        self.svc.on_app(fid,{'t':'pack','mid':result['mid'],'s':'blocked'})
        self.assertEqual(self.svc.guards.row(fid,result['mid'],'out')['state'],'blocked')
        self.assertIn(self.svc.tx('send_blocked',name=self.store.name_of(fid)),self.host.notices)
        before=len(self.host.notices)
        self.clock.t += 86400
        self.svc.guards.sweep()
        self.assertEqual(len(self.host.notices),before)
