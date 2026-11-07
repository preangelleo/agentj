"""A progress ACK must not open a concurrent signed-install admission window."""
import unittest
from unittest.mock import AsyncMock
import test_install_assist_host as boundary

class Pending(unittest.IsolatedAsyncioTestCase):
    setUp=boundary.Boundary.setUp
    tearDown=boundary.Boundary.tearDown
    request=boundary.Boundary.request

    async def test_pending_ack_keeps_single_active_operation(self):
        self.runner._operation=AsyncMock(return_value={'installed':True})
        nested=[]; entered=False
        async def send(s,obj):
            nonlocal entered
            if not entered:
                entered=True
                nested.append(await self.runner.run(self.s,self.request('ai.install',{'cli':'opencode'})))
            return True
        self.h.send_app=send
        first=await self.runner.run(self.s,self.request('ai.install',{'cli':'opencode'}))
        self.assertEqual(nested[0].get('why'),'busy')
        self.assertTrue(first['ok'])
        self.assertEqual(self.runner._operation.await_count,1)
