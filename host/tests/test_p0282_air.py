"""Agent Air: Darwin secret-card origin uses a stable current-turn process chain."""
import _hermetic
import asyncio
import copy
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from agentj import approval_material, approval_review, secret_out
from agentj.core import process_evidence
from test_p541_special import Special
from test_p73_secret import fa_out


class DarwinOrigin(unittest.TestCase):
    def setUp(self):
        now = time.monotonic()
        self.origin = {'session':'1','message_id':1,'root_message_id':1,'verified':True,'suspicious':False,
                       'channel':'phone','text':'取回自己的测试链接','chain':[],'host_received_monotonic':str(now-5)}
        self.send = SimpleNamespace(created=now-5, approval_origin=self.origin)
        self.agent = SimpleNamespace(session={'pid':100,'procStart':'anchor-start'}, phone_turn=True,
                                     phone_seen=True, cur_send=self.send)
        self.h = SimpleNamespace(agent=self.agent,hist=SimpleNamespace(epoch=1), desktop_turn=None)
        self.rows = {100:(1,time.time()-100,'anchor-start'),101:(100,time.time()-2,'child-start'),
                     102:(101,time.time()-1,'peer-start')}
        self.stack = mock.patch.object(process_evidence,'darwin_row',side_effect=lambda p:self.rows.get(p))
        self.stack.start();self.addCleanup(self.stack.stop)
        p=mock.patch.object(approval_material.sys,'platform','darwin');p.start();self.addCleanup(p.stop)

    def test_fresh_child_resolves_exact_host_origin(self):
        self.assertEqual(approval_material.verified_caller_origin(self.h,102),self.origin)

    def test_history_session_changed_refused(self):
        scope=approval_material._direct_scope(self.h,102)
        self.h.hist.epoch=2
        self.assertIsNone(approval_material.verified_caller_origin(self.h,102))
        self.assertFalse(approval_material._scope_live(self.h,scope,102))

    def test_unknown_peer_and_same_uid_unrelated_process_refused(self):
        self.assertIsNone(approval_material.verified_caller_origin(self.h,None))
        self.rows[102]=(999,time.time()-1,'peer-start')
        self.assertIsNone(approval_material.verified_caller_origin(self.h,102))

    def test_old_background_process_cannot_borrow_new_owner(self):
        self.rows[101]=(100,time.time()-20,'old-child')
        self.assertIsNone(approval_material.verified_caller_origin(self.h,102))

    def test_pid_reuse_refused(self):
        self.agent.session['procStart']='old-anchor'
        self.assertIsNone(approval_material.verified_caller_origin(self.h,102))

    def test_peer_race_refused(self):
        scope=approval_material._direct_scope(self.h,102)
        self.rows[102]=(101,time.time(),'replaced-peer')
        self.assertFalse(approval_material._scope_live(self.h,scope,102))

    def test_live_owner_changed_refused(self):
        scope=approval_material._direct_scope(self.h,102)
        self.send.approval_origin={**self.origin,'root_message_id':99}
        self.assertFalse(approval_material._scope_live(self.h,scope,102))

    def test_queued_phone_message_not_current_refused(self):
        self.agent.phone_seen=False
        self.assertIsNone(approval_material.verified_caller_origin(self.h,102))


class AirPickup(Special):
    """Exercise the actual sealed controller and UV decision after Darwin broker resolution."""
    def test_darwin_secret_origin_and_uv_value_delivery(self):
        async def run():
            original=approval_review.snapshot(self.host)
            origin={**original,'chain':[{'kind':'subagent','id':'verified-native-child'}]}
            with mock.patch.object(approval_material,'_direct_scope',return_value={'kind':'agent','origin':origin}), \
                 mock.patch.object(approval_material,'_scope_live',return_value=True):
                resolved=approval_material.verified_caller_origin(self.host,123)
            with approval_review.origin_scope(resolved):
                card=await self.pickup()
            self.assertTrue(card['envelope']['source']['verified'])
            self.assertEqual(card['envelope']['source']['chain'],origin['chain'])
            await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n'],
                'fa':fa_out(self.a.auth,self.a,card)})
            self.assertEqual(len(self.messages('secret_out_val')),1)
            self.assertEqual(self.host.elevate.out.cards,{})
        asyncio.run(run())

    def test_external_origin_never_releases_secret(self):
        async def run():
            origin={**approval_review.snapshot(self.host),'verified':False,'suspicious':True,'channel':'support'}
            with approval_review.origin_scope(origin):card=await self.pickup()
            await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n'],
                'fa':fa_out(self.a.auth,self.a,card)})
            self.assertEqual(self.messages('secret_out_val'),[])
            self.host.elevate.out.end_all('stopped')
        asyncio.run(run())

if __name__=='__main__':unittest.main()

class DarwinNativeRow(unittest.TestCase):
    def test_exact_libproc_fields_and_uid_boundary(self):
        import os, struct
        from agentj import shared_hook
        uid=os.getuid()
        wrong=[False]
        def fill(pid, flavor, arg, buf, size):
            struct.pack_into('4I',buf,4,2,0,pid,99)
            for offset in (20,28,36):struct.pack_into('I',buf,offset,uid+1 if wrong[0] else uid)
            struct.pack_into('2Q',buf,120,1762480000,123456)
            return 136
        with mock.patch.object(process_evidence.sys,'platform','darwin'), \
             mock.patch.object(shared_hook,'_LIBPROC',SimpleNamespace(proc_pidinfo=fill)):
            row=process_evidence.darwin_row(100)
            self.assertEqual(row[:2],(99,1762480000.123456))
            wrong[0]=True
            self.assertIsNone(process_evidence.darwin_row(100))

class UnknownPickup(Special):
    def test_unknown_source_allows_only_bound_uv_owner_pickup(self):
        async def run():
            with approval_review.origin_scope({}):card=await self.pickup()
            self.assertEqual(card['envelope']['source']['channel'],'unknown')
            self.assertTrue(card['envelope']['special']['pickup_uv_allowed'])
            self.assertTrue(card['pickup_uv_allowed'])
            await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n']})
            self.assertEqual(self.messages('secret_out_val'),[])
            await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n'],
                'fa':fa_out(self.a.auth,self.a,card)})
            self.assertEqual(len(self.messages('secret_out_val')),1)
        asyncio.run(run())

    def test_pairing_key_replaced_after_card_cannot_claim(self):
        async def run():
            with approval_review.origin_scope({}):card=await self.pickup()
            real=self.st.sign_key
            with mock.patch.object(self.st,'sign_key',side_effect=lambda d:b'replaced-key' if d==self.a.did else real(d)):
                await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n'],
                    'fa':fa_out(self.a.auth,self.a,card)})
            self.assertEqual(self.messages('secret_out_val'),[])
            self.host.elevate.out.end_all('stopped')
        asyncio.run(run())

    def test_new_owner_task_does_not_revoke_sealed_pickup(self):
        async def run():
            from test_p541_special import intent
            with approval_review.origin_scope({}):card=await self.pickup()
            self.host.desktop_turn=12345
            await self.host._app(self.sa,intent(self.a,card,'question'))
            revised=self.messages('approval_review_result')[-1]['ask']
            await self.host._app(self.sa,intent(self.a,revised,'reopen'))
            reopened=self.messages('approval_review_result')[-1]['ask']
            await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':reopened['n'],
                'fa':fa_out(self.a.auth,self.a,reopened)})
            self.assertEqual(len(self.messages('secret_out_val')),1)
        asyncio.run(run())


class ExternalMaterialPickup(Special):
    def test_known_external_material_cannot_be_laundered_as_unknown_pickup(self):
        async def run():
            origin=approval_material._unknown(approval_review.snapshot(self.host),'external-receipt-expired')
            with approval_review.origin_scope(origin):card=await self.pickup()
            self.assertEqual(card['envelope']['source']['role'],'unknown_external')
            self.assertFalse(card['envelope']['special']['pickup_uv_allowed'])
            self.assertFalse(card['pickup_uv_allowed'])
            await self.host._app(self.sa,{'t':'secret_out_open','id':card['id'],'n':card['n'],
                'fa':fa_out(self.a.auth,self.a,card)})
            self.assertEqual(self.messages('secret_out_val'),[])
            self.host.elevate.out.end_all('stopped')
        asyncio.run(run())
