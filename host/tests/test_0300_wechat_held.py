"""Real Host/Noise held-channel decisions; synthetic storage and no vendor I/O."""
import _hermetic
import asyncio
import copy
import json
import time
import unittest
from unittest.mock import AsyncMock
from agentj import approvals, passkey, wire, serve
import test_p501_wechat as fixture


class HeldChannel(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f=fixture.Channel('test_signed_confirmation_exact_summary_and_once')
        await self.f.asyncSetUp()
        self.h,self.c,self.st,self.phone,self.session=self.f.h,self.f.c,self.f.st,self.f.phone,self.f.s

    async def asyncTearDown(self):
        await self.f.asyncTearDown()
        self.f.doCleanups()

    async def card(self, task):
        for _ in range(200):
            cards=[o for _,o in self.f.sent if o.get('t')=='ask' and any(a.rid==o['id'] for a in self.c.cards.values())]
            if cards:return cards[-1]
            if task.done():self.fail('controller ended before actual wire card: '+str(task.result()))
            await asyncio.sleep(.005)
        self.fail('no actual wire card within fixture budget')

    async def tap(self, card, *, uv=True, allow=True):
        a=next(a for a in self.c.cards.values() if a.rid==card['id'])
        answer={'t':'answer','id':card['id'],'ok':allow,'sig':wire.b64u(self.phone.sk.sign(
            approvals.signed_message(self.h.channel,self.phone.did,card['id'],'allow' if allow else 'deny',
                                     approvals.shown_digest(card['tool'],card['summary']))))}
        if uv and allow and card.get('passkey_required'):
            challenge=passkey.elevate_challenge(self.h.channel,self.phone.did,a.rid,a.pk_kind,a.rid,a.digest)
            answer['fa']=self.phone.auth.assert_(challenge,b'0',nonce=b'0')
        await self.h._app(self.session,answer)
        return answer

    async def owner(self):
        # Real paired producer; only the vendor's eventual submit remains local.
        self.h.hist_add=serve.Host.hist_add.__get__(self.h)
        self.h._accept=serve.Host._accept.__get__(self.h)
        await self.h._app(self.session,{'t':'msg','text':'Synthetic channel settings request'})
        self.h.agent.cur_send=self.h.agent.submit.call_args.args[0]
        self.h.agent.submit.reset_mock()

    async def test_actual_wire_transfer_main_turn_change_and_replay_are_data_only(self):
        r=await self.f.pending()
        self.h.cur_turn=999
        a=self.c.cards[r['id']]
        await self.f.answer(a)
        await asyncio.sleep(.02)
        self.h.agent.submit.assert_called_once()
        send=self.h.agent.submit.call_args.args[0]
        wrapper=json.loads(send.text.splitlines()[-1])
        payload=json.loads(wrapper['text'])
        self.assertIs(wrapper['untrusted'],True)
        self.assertIs(payload['execute'],False)
        self.assertIs(payload['untrusted'],True)
        self.assertNotEqual(send.source.get('role'),'owner')
        self.assertTrue(await send.wait_ready())
        self.assertFalse(self.h.asks)
        self.assertFalse(self.h.__dict__.get('_held_channel_asks'))
        self.assertEqual(approvals.check_record(self.st,approvals.read_log(self.st)[-1]),'ok')
        await self.f.answer(a)  # replay is ignored, never a second delivery
        self.h.agent.submit.assert_called_once()

    async def test_changed_record_controller_slot_generation_and_stop_never_transfer(self):
        changes=('text','paths','admission','state','until','record','store','controller','slot','nonce','target','generation','stop')
        for change in changes:
            with self.subTest(change=change):
                r=await self.f.pending();a=self.c.cards[r['id']]
                original_store=self.c.store;original=self.c.store.data['records'][r['id']]
                old=copy.deepcopy(original);generation=self.c.store.data['generation']
                try:
                    if change=='text':r['text']+=' changed'
                    elif change=='paths':r['paths']=['/synthetic/changed']
                    elif change=='admission':r['admission']={'generation':'changed'}
                    elif change=='state':r['state']='claimed'
                    elif change=='until':r['until']=0
                    elif change=='record':self.c.store.data['records'][r['id']]=copy.deepcopy(r)
                    elif change=='store':self.c.store=copy.copy(self.c.store)
                    elif change=='controller':self.h.wechat=None
                    elif change=='slot':self.c.cards.pop(r['id'])
                    elif change=='nonce':a._held_nonce='changed'
                    elif change=='target':a.tool_input={'id':'changed'}
                    elif change=='generation':self.c.store.data['generation']='changed'
                    else:self.f.stopped=True
                    await self.f.answer(a)
                    self.assertFalse(a.fut.done())
                    self.h.agent.submit.assert_not_called()
                finally:
                    self.f.stopped=False;self.h.wechat=self.c;self.c.store=original_store
                    self.c.store.data['generation']=generation
                    self.c.store.data['records'][r['id']]=original
                    original.clear();original.update(old)
                    self.c.cards[r['id']]=a
                    if not a.fut.done():a.fut.set_result(('deny',None,None,None))
                    await asyncio.sleep(.01)
                    self.assertFalse(self.h.__dict__.get('_held_channel_asks'))

    async def test_valid_tap_cannot_cross_record_change_while_waiting_for_claim_lock(self):
        await self.c.handoff_lock.acquire()
        try:
            r=await self.f.pending();a=self.c.cards[r['id']]
            await self.f.answer(a)
            await asyncio.sleep(.01)
            self.assertTrue(a.fut.done())
            self.assertIs(self.c.cards[r['id']],a)
            r['text']+=' changed after tap'
        finally:self.c.handoff_lock.release()
        await asyncio.sleep(.02)
        self.h.agent.submit.assert_not_called()
        self.assertFalse(self.h.__dict__.get('_held_channel_asks'))
        self.assertFalse(self.c.__dict__.get('_approval_receipts'))

    async def test_setup_requires_fresh_uv_and_consumes_once(self):
        await self.owner()
        self.c.store.data['account']={};self.c.store.save()
        self.c.login=AsyncMock()
        task=asyncio.create_task(self.c.agent_request({'op':'setup'}))
        try:
            card=await self.card(task)
            self.assertTrue(card['passkey_required'])
            start=len(self.f.sent)
            await self.tap(card,uv=False)
            reasons=[o['why'] for _,o in self.f.sent[start:] if o.get('t')=='answer_refused' and o.get('id')==card['id']]
            self.assertEqual(reasons,['passkey_required'])
            self.c.login.assert_not_called();self.assertFalse(task.done())
            answer=await self.tap(card)
            self.assertTrue((await asyncio.wait_for(task,2))['ok'])
            await asyncio.sleep(0)
            self.c.login.assert_awaited_once()
            await self.h._app(self.session,answer)
            self.c.login.assert_awaited_once()
            self.assertFalse(self.h.__dict__.get('_held_channel_asks'))
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_setup_lock_generation_change_invalidates_approved_receipt(self):
        await self.owner()
        self.c.store.data['account']={};self.c.store.save();self.c.login=AsyncMock()
        await self.c.setup_lock.acquire()
        task=asyncio.create_task(self.c.agent_request({'op':'setup'}))
        try:
            card=await self.card(task)
            await self.tap(card)
            await asyncio.sleep(.01)
            self.c.store.data['generation']='changed-after-UV'
        finally:self.c.setup_lock.release()
        try:
            result=await asyncio.wait_for(task,2)
            self.assertEqual(result['why'],'approval_changed')
            self.c.login.assert_not_called()
            self.assertFalse(self.h.__dict__.get('_held_channel_asks'))
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_external_actual_mail_admission_revocation_is_not_just_record_hash(self):
        from agentj import external_channels, mail
        ext=external_channels.ExternalChannels(self.h,model=AsyncMock())
        self.h.external_channels=ext
        cfg=mail.blank_config()
        cfg.update(enabled=True,address='fixture@example.test',generation='synthetic-mail-generation',
                   allow=[{'addr':'owner@example.test','role':'owner'}])
        cfg['imap']['host']='localhost';cfg['smtp']['host']='localhost'
        mail.save_config(self.st,cfg)
        r=ext.store.put('held-mail',[],'Synthetic external data')
        r.update(state='pending',source='mail',admission={'generation':cfg['generation'],'addr':'owner@example.test'})
        ext.store.save();ext.open_card(r)
        try:
            for _ in range(200):
                if r['id'] in ext.cards:break
                await asyncio.sleep(.005)
            self.assertIn(r['id'],ext.cards)
            a=ext.cards[r['id']]
            # Keep the actual stored record byte-identical; revoke the real live
            # enrollment config read by ExternalChannels.admitted instead.
            before=approvals.input_digest(r)
            cfg['allow']=[];mail.save_config(self.st,cfg)
            self.assertEqual(approvals.input_digest(r),before)
            self.assertFalse(ext.admitted(r))
            await self.f.answer(a)
            self.assertFalse(a.fut.done())
            self.h.agent.submit.assert_not_called()
        finally:
            await ext.stop();self.h.external_channels=None
