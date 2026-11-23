"""P495 #254: real Host card lifetime over offline/reconnect and transport failures."""
import _hermetic
import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from agentj import elevate
from test_l1 import Phone, _owner_host as _host, _ready, _state
from test_f17 import answer


class Offline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name);self.st=_state(self.tmp.name);self.sent=[]
        self.host=_host(self.st,self.sent);self.phone=Phone(self.st)
        self.host._remind_offline=Mock()
        self.card=elevate.norm_sudo({'argv':['/usr/bin/true'],'why':'offline test','cwd':str(self.root)})

    async def wait_card(self):
        for _ in range(100):
            if self.host.elevate.cards: return next(iter(self.host.elevate.cards.values()))
            await asyncio.sleep(.001)
        self.fail('no card')

    async def test_no_remote_is_audited_without_waiting(self):
        with patch.object(self.host,'_approvers',return_value=[]), patch.object(elevate,'audit') as audit:
            self.assertEqual((await self.host.elevate.request(self.card))['result'],'no_device')
        self.assertTrue(audit.called)

    async def test_offline_sudo_reminds_and_replays_then_signed_deny(self):
        # 5d3af65600 (2026-11-14, persisted card delivery retries): a queued delivery asks for the offline reminder as
        # well as the card itself, so the call count of the entry point is 2; "the owner is reminded ONCE" is the real
        # Host._remind_offline's own gap (approval_level.OFFLINE_GAP). Assert that — one reminder sent, for one card.
        from agentj import approval_level
        del self.host._remind_offline                      # the real method, not asyncSetUp's stand-in
        real=self.host._remind_offline;entries=[]
        def counted():
            entries.append(1);return real()                # the real method runs; only its entries are counted
        self.host._remind_offline=counted
        sent=AsyncMock(return_value=['push'])
        with patch.object(approval_level,'remind_offline',new=sent):
            task=asyncio.create_task(self.host.elevate.request(self.card));c=await self.wait_card()
            # Deterministic: wait until BOTH entry calls (the card and its queued delivery) have happened and the
            # reminder went out, then let every task they could have started run - a late second reminder is seen.
            for _ in range(2000):
                if len(entries)>=2 and sent.await_count: break
                await asyncio.sleep(.005)
            for _ in range(20):await asyncio.sleep(0)
            self.assertEqual(len(entries),2)
        try:
            sent.assert_awaited_once()
            self.assertEqual(sent.await_args.args[1],1)
            self.assertTrue(c.get('offline_reminded'))
            s=_ready(self.host,self.phone)
            await self.host.elevate.on_ready(s)
            m=next(o for _,o in self.sent if o['t']=='elev')
            self.assertEqual(m['id'],c['id'])
            await self.host.elevate.on_phone(s,answer(self.phone,m,False))
            self.assertEqual((await task)['result'],'denied')
        finally:
            self.host.elevate.cancel_all('gone');await task

    async def test_initial_send_error_does_not_destroy_card(self):
        self.host._send_ready=AsyncMock(side_effect=ConnectionError('private transport failure'))
        task=asyncio.create_task(self.host.elevate.request(self.card));c=await self.wait_card()
        await asyncio.sleep(.01)
        try:
            self.assertFalse(task.done(),'card must survive transient relay write failure')
            s=_ready(self.host,self.phone);await self.host.elevate.on_ready(s)
            m=next(o for _,o in self.sent if o['t']=='elev')
            self.assertEqual(m['id'],c['id'])
            self.host.elevate._finish(c,{'result':'denied'})
            self.assertEqual((await task)['result'],'denied')
        finally:
            self.host.elevate.cancel_all('gone')
            await asyncio.gather(task,return_exceptions=True)

    async def test_timeout_reconnect_has_terminal_receipt_not_open_card(self):
        with patch.dict(elevate.TTL,{'sudo':.03}):
            result=await self.host.elevate.request(self.card)
        self.assertEqual(result['result'],'timeout')
        s=_ready(self.host,self.phone);await self.host.elevate.on_ready(s)
        self.assertFalse(self.host.elevate.open_msgs(s))
        self.assertTrue(any(o['t']=='elev_done' and o['id']==result['id'] and o['result']=='timeout' for _,o in self.sent))
        self.assertIn('timeout',(self.st.root/'elevate.log').read_text())

    async def test_remote_going_offline_while_waiting_also_reminds(self):
        _ready(self.host,self.phone)
        task=asyncio.create_task(self.host.elevate.request(self.card));c=await self.wait_card()
        try:
            self.host._remind_offline.assert_not_called()
            self.host.sessions.clear()
            await asyncio.sleep(1.05)
            self.host._remind_offline.assert_called_once()
            self.host.elevate._finish(c,{'result':'denied'})
            await task
            self.assertIn(c['id'],self.st.log_path.read_text())
        finally:
            self.host.elevate.cancel_all('gone');await asyncio.gather(task,return_exceptions=True)
