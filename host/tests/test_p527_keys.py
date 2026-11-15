"""P527: real receive admission → background key card, no provider/credential access."""
import asyncio
from unittest.mock import patch
from test_p352_keys import Base
from agentj import elevate, keys, provenance

class KeyDelivery(Base):
    def setUp(self):
        super().setUp()
        self.host.push_notify=lambda *_:None

    def test_background_card_is_delivered_to_clicking_remote(self):
        async def go():
            await self.host._app(self.s, {'t':'keys_set','r':'p527','name':'OPENAI_API_KEY'})
            for _ in range(30):
                if self.of('elev') or any(x.get('card') for x in self.of('keys_res')): break
                await asyncio.sleep(.01)
            replies=self.of('keys_res')
            self.host.elevate.cancel_all('stopped')
            self.assertTrue(replies and replies[-1]['ok'])
            card=replies[-1].get('card')
            self.host.elevate.cancel_all('stopped')
            self.assertIsNotNone(card, 'success must carry a real input card, not a premature toast')
            self.assertEqual(card['name'],'OPENAI_API_KEY')
            self.assertTrue(card['passkey_required'])  # Inserting/replacing secrets always requires fresh UV.
            self.assertFalse(card.get('button_only',False))
            self.assertEqual(self.host.elevate.cards[card['id']]['only'],self.s.device)
            self.host.elevate.cancel_all('stopped')
            await asyncio.gather(*list(self.host.__dict__.get('_bg_tasks', [])), return_exceptions=True)
        asyncio.run(go())

    def test_early_refusal_is_not_success(self):
        async def go():
            self.host.stopped=lambda:True
            await self.host._app(self.s, {'t':'keys_set','r':'p527','name':'OPENAI_API_KEY'})
            await asyncio.sleep(.04)
            self.assertFalse(self.of('keys_res')[-1]['ok'])
            self.assertEqual(self.of('keys_res')[-1]['why'],'stopped')
        asyncio.run(go())

    def test_agent_request_still_rejects_unknown_source(self):
        async def go():
            before=self.envf.read_bytes()
            # An invalid paired creator is unknown at the actual admission
            # boundary; an unset turn label alone is not request authority.
            checked=[]
            def no_creator():
                checked.append(False)
                return False
            async def rejected():
                with provenance.paired_request(no_creator):
                    return await self.host.elevate.request(keys.build_card(self.host,'OPENAI_API_KEY'))
            result=await asyncio.wait_for(rejected(),2)
            self.assertTrue(checked, 'the actual same-task creator verifier must run')
            self.assertEqual((result['result'],result.get('why')),('refused','authority_changed'))
            self.assertFalse(self.host.elevate.cards)
            self.assertFalse(self.of('elev'))
            self.assertEqual(self.envf.read_bytes(),before)
        asyncio.run(go())

    def test_unknown_offer_is_not_authority_and_gone_cancels_without_write(self):
        async def go():
            before=self.envf.read_bytes()
            gone=asyncio.get_running_loop().create_future()
            delivered=[]
            async def shown(card):
                delivered.append(card)
                gone.set_result(True)  # the synthetic caller withdraws, never answers the card
            try:
                with provenance.request_source(provenance.normalize(None)), \
                        patch.object(elevate,'write_secret',side_effect=AssertionError('unsigned card wrote')) as write:
                    result=await asyncio.wait_for(self.host.elevate.request(
                        keys.build_card(self.host,'OPENAI_API_KEY'),gone=gone,on_card=shown),2)
                    write.assert_not_called()
                self.assertEqual(result['result'],'gone')
                self.assertEqual(len(delivered),1)
                self.assertIsNot(delivered[0]['review_adapter'].envelope['source'].get('verified'),True)
                self.assertFalse(self.host.elevate.cards)
                self.assertEqual(self.envf.read_bytes(),before)
            finally:
                self.host.elevate.cancel_all('stopped')
        asyncio.run(go())

    def test_every_catalog_key_uses_a_real_card(self):
        async def go():
            for _, _, rows in keys.CATALOG:
                for kind, name in rows:
                    if kind != 'key': continue
                    await self.host._app(self.s, {'t':'keys_set','r':'catalog','name':name})
                    for _ in range(100):
                        if self.of('keys_res') and self.of('keys_res')[-1].get('name')==name: break
                        await asyncio.sleep(.005)
                    res=self.of('keys_res')[-1]
                    self.assertTrue(res['ok'], name)
                    self.assertEqual(res['card']['name'],name)
                    self.host.elevate.cancel_all('stopped')
                    await asyncio.gather(*list(self.host._bg_tasks),return_exceptions=True)
        asyncio.run(go())

    def test_duplicate_and_second_remote(self):
        from test_l1 import Phone, _ready
        async def go():
            second=_ready(self.host,Phone(self.st,name='second'),cid=12)
            for r in ('one','two'):
                await self.host._app(self.s,{'t':'keys_set','r':r,'name':'OPENAI_API_KEY'})
            for _ in range(100):
                if self.of('elev'): break
                await asyncio.sleep(.005)
            self.assertTrue(any(m.get('why')=='busy' for m in self.of('keys_res')))
            self.assertFalse(any(cid==second.cid and m['t']=='elev' for cid,m in self.sent))
            self.host.elevate.cancel_all('stopped')
            await asyncio.gather(*list(self.host._bg_tasks),return_exceptions=True)
        asyncio.run(go())

    def test_auth_rescue_revalidates_remote_in_background(self):
        from agentj.auth_rescue import Rescue
        async def go():
            rescue=self.host.auth_rescue
            rescue.flow={'_remote':self.s}
            observed=[]
            async def request(card, **kw):
                observed.append((provenance.untrusted_turn(None),card.get('only')))
                return {'result':'saved'}
            original=self.host.elevate.request
            self.host.elevate.request=request
            try:
                await rescue._request_secret(keys.build_card(self.host,'OPENAI_API_KEY'))
                self.host.sessions.pop(self.s.cid)
                await rescue._request_secret(keys.build_card(self.host,'OPENAI_API_KEY'))
            finally:self.host.elevate.request=original
            self.assertEqual(observed,[(False,self.s.device),(True,self.s.device)])
        asyncio.run(go())

    def test_legacy_provider_reply_reports_failure(self):
        from unittest.mock import AsyncMock, patch
        from agentj import provider_runtime
        async def go():
            with patch.object(provider_runtime,'key_name',return_value='OPENAI_API_KEY'), patch.object(provider_runtime,'request_key',new=AsyncMock(return_value={'result':'stopped'})):
                await self.host._app(self.s,{'t':'provider_key','r':'legacy','provider':'relay'})
                await asyncio.gather(*list(self.host._bg_tasks),return_exceptions=True)
            res=self.of('provider_res')[-1]
            self.assertFalse(res['ok'])
            self.assertEqual(res['why'],'stopped')
        asyncio.run(go())

    def test_cancelled_preparation_does_not_strand_key(self):
        from unittest.mock import patch
        async def go():
            with patch.object(keys.asyncio,'to_thread',side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await keys._set(self.host,self.s,'cancelled',{'name':'OPENAI_API_KEY'})
            self.assertNotIn('OPENAI_API_KEY',self.host._keys_cards)
        asyncio.run(go())
