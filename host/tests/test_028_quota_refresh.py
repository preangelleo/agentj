"""#398 updated contract: existing phone flow, never a next-message restart substitute.

The fake fixture loads real recovery/Login source with fake RPC and account commits.
"""
import unittest
from unittest.mock import AsyncMock
import test_0280_recovery_contract as contract

class Recheck(contract.Phone):
    async def test_pending_or_unknown_turn_is_not_touched(self):
        import asyncio
        self.a.turn_done=asyncio.Event()
        result=await self.q.recheck(self.h,self.remote,'pending')
        self.assertEqual(result['why'],'busy'); self.a.call.assert_not_awaited()
        self.assertEqual(self.jobs,[]); self.assertEqual(self.commits,[])
    async def test_shared_harness_refuses_phone_login(self):
        self.a.owns_harness=lambda:False
        self.assertEqual((await self.q.recheck(self.h,self.remote,'shared'))['why'],'unsupported')
        self.assertEqual(self.jobs,[]); self.h.auth_rescue._confirm.assert_not_awaited()
    async def test_unpaired_and_denied_do_nothing(self):
        self.h._paired_live=lambda _:False
        self.assertEqual((await self.q.recheck(self.h,self.remote,'unpaired'))['why'],'not_ready')
        self.a.call.assert_not_awaited()
        self.h._paired_live=lambda _:True; self.h.auth_rescue._confirm.return_value=False
        self.assertFalse((await self.q.recheck(self.h,self.remote,'denied'))['ok'])
        self.assertEqual(self.jobs,[]); self.assertEqual(self.commits,[])
    async def test_busy_flow_not_replaced(self):
        self.h.auth_rescue.flow={'stage':'device'}
        self.assertFalse((await self.q.recheck(self.h,self.remote,'busy'))['ok'])
        self.assertEqual(self.jobs,[]); self.assertEqual(self.commits,[])
    async def test_unknown_current_identity_refuses_phone_login(self):
        self.a.call.return_value={'account':None,'requiresOpenaiAuth':True}
        self.assertEqual((await self.q.recheck(self.h,self.remote,'unknown'))['why'],'NOT_VERIFIED')
        self.assertEqual(self.jobs,[]); self.h.auth_rescue._confirm.assert_not_awaited()
    async def test_passkey_is_bound_to_requesting_phone(self):
        await self.q.recheck(self.h,self.remote,'confirm')
        self.h.auth_rescue._confirm.assert_awaited_once_with('codex',remote=self.remote)
        await self.drain()

if __name__=='__main__': unittest.main()
