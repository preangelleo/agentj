"""0342 owner decisions preserve existing tools while holding new runtime entry points."""
import _hermetic
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agentj.voice_stream import Manager
from agentj.bots.tools import ToolRegistry, definition
from agentj.bots.store import BotError
from test_script_tools import manifest

class PendingScopes(unittest.TestCase):
    def test_service_environment_cannot_enable_unreleased_streaming(self):
        with patch.dict(os.environ, {'AGENTJ_VOICE_STREAM':'1'}), patch('agentj.voice_stream.endpoint') as endpoint:
            self.assertFalse(Manager(SimpleNamespace()).available())
            endpoint.assert_not_called()

    def registry(self):
        reg=ToolRegistry.__new__(ToolRegistry)
        reg.bid='synthetic'; reg.root=Path(os.environ['TMPDIR'])/'pending-tool-never-written'
        reg.store=SimpleNamespace(get=lambda bid: {'outbound_domains':['docs.example.org']})
        return reg

    def test_new_script_cannot_be_saved_or_prepared(self):
        reg=self.registry(); raw=manifest("print('{}')",enabled=True)
        with self.assertRaisesRegex(BotError,'script_pending_owner_decision'): reg.save(raw)
        self.assertFalse(reg.root.exists())
        reg.load=lambda name:definition(raw,[])
        with self.assertRaisesRegex(BotError,'script_pending_owner_decision'):reg.prepare('synthetic','script_sample',{})

    def test_existing_https_tools_remain_offered(self):
        reg=self.registry()
        ordinary=definition({'name':'existing_read','description':'Read public fixture','url':'https://docs.example.org/status','method':'GET','parameters':{'type':'object','properties':{},'additionalProperties':False},'response_fields':['ok'],'enabled':True},['docs.example.org'])
        script=definition(manifest("print('{}')",enabled=True),[])
        reg.listing=lambda:[script,ordinary]
        self.assertEqual([x['function']['name'] for x in reg.offered()],['existing_read'])

    def test_new_core_order_cannot_launch_or_resume(self):
        from agentj.core import dispatcher, orders
        with self.assertRaisesRegex(orders.OrderError,'pending_owner_decision'):
            dispatcher.launch(SimpleNamespace())
        with self.assertRaisesRegex(orders.OrderError,'pending_owner_decision'):
            dispatcher.resume(SimpleNamespace())
        self.assertEqual(dispatcher.tool({'action':'new'},parent={},allowed_roots=[]),'Error: pending_owner_decision')

    def test_new_forms_cannot_create_cards(self):
        from agentj.forms import FormService
        service=FormService.__new__(FormService)
        self.assertEqual(service.send({})['why'],'pending_owner_decision')

    def test_new_spoken_rewrite_stays_off_even_if_selected(self):
        from agentj import spoken
        for value in ('on','auto','off'):
            self.assertFalse(spoken.effective({'voice':{'tts':{'spoken':value,'mode':'cloud'}}}))

    def test_event_routes_cannot_deliver_from_preexisting_enabled_state(self):
        from agentj.subscriptions import Router
        router=Router.__new__(Router)
        self.assertFalse(router.live('synthetic'))

class PendingLearning(unittest.IsolatedAsyncioTestCase):
    async def test_new_distillation_cannot_prepare_or_propose(self):
        from agentj.learn import Learner
        service=Learner.__new__(Learner)
        for t in ('learn_draft','learn_propose'):
            self.assertIn('pending_owner_decision',str(await service.handle({'t':t})))
