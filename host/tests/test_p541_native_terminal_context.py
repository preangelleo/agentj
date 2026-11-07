"""Native terminal configuration approval keeps Host context without owner inheritance."""
import _hermetic
import asyncio
import unittest
from unittest.mock import patch
from agentj import term_cli,serve
import test_p408_terminal as fixture

class NativeTerminal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rig=fixture.Rig();self.rig.setUp();self.addCleanup(self.rig.tearDown)
        self.rig.passkey()
        self.h=self.rig.host;self.s=self.rig.s;self.phone=self.rig.phone
        self.pending=asyncio.create_task(term_cli.host_card(self.h,'Synthetic terminal config',{'op':'enable'}))
        for _ in range(100):
            if self.h.asks:break
            await asyncio.sleep(.001)
        self.ask=next(iter(self.h.asks.values()))
        self.addAsyncCleanup(self.close)

    async def close(self):
        if not self.ask.fut.done():self.ask.fut.set_result(('deny',None,None,None))
        await asyncio.gather(self.pending,return_exceptions=True)

    async def approve(self):
        card=self.h._ask_msg(self.ask,self.s)
        await self.h._answer(self.s,{**self.phone.answer(card,True),'fa':fixture.FA})

    async def test_real_native_v1_signature_uv_resolves(self):
        await self.approve()
        self.assertEqual(await asyncio.wait_for(self.pending,1),'allow')

    async def test_replaced_native_controller_refuses(self):
        self.h.term_desk=object()
        await self.approve()
        self.assertFalse(self.ask.fut.done())

    async def test_history_epoch_change_refuses(self):
        self.h.hist.epoch+=1
        await self.approve()
        self.assertFalse(self.ask.fut.done())

    async def test_target_change_refuses(self):
        self.ask.tool_input['op']='widened'
        await self.approve()
        self.assertFalse(self.ask.fut.done())

    async def test_unbound_same_tool_does_not_get_host_authority(self):
        del self.ask.native_authority
        await self.approve()
        self.assertFalse(self.ask.fut.done())
