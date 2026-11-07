"""A group preparation notification must fit individual encrypted app frames."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from agentj import initialization

class Frames(unittest.IsolatedAsyncioTestCase):
    async def test_group_cards_are_individually_bounded(self):
        owner=SimpleNamespace(device='owner')
        h=SimpleNamespace(sessions={'one':owner},_paired_live=lambda s:True,
                          st=SimpleNamespace(sign_key=lambda d:b'public'),send_app=AsyncMock())
        row={'id':'local','stage':'字'*300,'if_missing':'字'*300,'degrade':'字'*300,
             'status':'missing','providers':[{'missing':['A'*64]*24}]*8}
        cards=[{'name':'demo-'+str(i),'rows':[dict(row) for _ in range(24)],'key_requests':[],'config':[]} for i in range(20)]
        await initialization.deliver(h,'owner',cards)
        self.assertEqual(h.send_app.await_count,20)
        for call in h.send_app.await_args_list:
            self.assertLessEqual(len(json.dumps(call.args[1],ensure_ascii=False).encode()),48*1024)
            self.assertEqual(len(call.args[1]['initialization']),1)
