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
        task=asyncio.create_task(self.host.elevate.request(self.card));c=await self.wait_card()
        try:
            self.host._remind_offline.assert_called_once()
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
