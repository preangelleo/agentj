import _hermetic
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from agentj import provenance
from agentj.core import orders, order_gates, dispatcher
from test_p53_orders import BRIEF

class Gates(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.project=self.root/'project'; self.project.mkdir()
        self.sent=[]; self.parent={'kind':'owner_turn','session':'current','harness':'core'}
        self.host=NS(stopped=lambda:False,st=NS(root=self.root,agent_session=lambda k:'current'),
                     agent=NS(kind='core',native_source=provenance.phone(),cfg={'dir':str(self.project)},submit=self.sent.append))
        self.o=orders.create(project=str(self.project),prompt_text='x',brief=BRIEF,title='x',parent=self.parent,st_root=self.root)
        self.o.event('origin',source=provenance.phone())
        patch=mock.patch.dict(os.environ,{'AGENTJ_CORE':'1'}); patch.start(); self.addCleanup(patch.stop)
    def test_default_is_bounded_once_and_returns_as_unsigned_data(self):
        gid=self.o.gate('Preference?', ['A','B'],'B',deadline_min=0)
        self.assertEqual(order_gates.expire(self.host),1)
        self.assertEqual(order_gates.expire(self.host),0)
        self.assertEqual(len(self.o.inbox_all()),1)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.sent[0].source,provenance.system())
        self.assertIn('"approval": false',self.sent[0].text)
        self.assertIn(gid,self.sent[0].text)
        self.assertEqual(order_gates.pending(self.o),[])
        self.assertEqual(self.o.meta['grants'],{})
    def test_future_stop_pause_wrong_parent_and_unknown_never_default(self):
        self.o.gate('Preference?',['A'],'A',deadline_min=1)
        self.assertEqual(order_gates.expire(self.host),0)
        self.o.gate('Now?',['A'],'A',deadline_min=0)
        self.host.stopped=lambda:True
        self.assertEqual(order_gates.expire(self.host),0)
        self.host.stopped=lambda:False
        dispatcher.set_paused(self.root,True)
        self.assertEqual(order_gates.expire(self.host),0)
        dispatcher.set_paused(self.root,False)
        self.host.agent.native_source=provenance.normalize(None)
        self.assertEqual(order_gates.expire(self.host),0)
        self.host.agent.native_source=provenance.phone()
        self.host.st.agent_session=lambda k:'other'
        self.assertEqual(order_gates.expire(self.host),0)
        self.assertEqual(self.sent,[])
    def test_batch_ceiling_no_arbitrary_executor(self):
        for i in range(70): self.o.gate('rm -rf data?',['$(command)'],'$(command)',deadline_min=0)
        self.assertEqual(order_gates.expire(self.host),64)
        self.assertEqual(len(self.sent),64)
        self.assertEqual(order_gates.expire(self.host),6)
    def test_owner_preference_settles_without_later_default_or_grant(self):
        gid=self.o.gate('Preference?',['A','B'],'B',deadline_min=1)
        self.assertEqual(order_gates.answer(self.o,gid,'A'),1)
        self.assertEqual(order_gates.expire(self.host,now=10**12),0)
        self.assertEqual(order_gates.pending(self.o),[])
        with self.assertRaises(orders.OrderError): order_gates.answer(self.o,gid,'B')
        self.assertEqual(len(self.o.inbox_all()),1)
        self.assertEqual(self.o.meta['grants'],{})

    def test_idle_host_uses_frozen_order_source_without_owner_impersonation(self):
        self.o.gate('Preference?',['A'],'A',deadline_min=0)
        self.host.agent.status='idle'
        self.host.agent.native_source=provenance.normalize(None)
        self.assertEqual(order_gates.expire(self.host),1)
        self.assertEqual(self.sent[0].source,provenance.system())
        self.assertFalse(provenance.is_owner(self.sent[0].source))

class ApprovalProjection(unittest.TestCase):
    def test_product_host_projects_origin_without_changing_card_fields(self):
        import time
        from agentj.serve import Host, Ask
        card=Ask(rid='request', tool='Bash', summary='signed command', digest='synthetic',
                 tool_input={}, input_sha='synthetic', deadline=time.monotonic()+60,
                 fut=None, cats=['delete'], why='reason', reason='signed reason')
        card.order_origin=['Main › CEO · order']
        result=Host._ask_msg(NS(),card)
        self.assertEqual(result['order_origin'],card.order_origin)
        self.assertEqual(result['summary'],'signed command')
        self.assertEqual(result['reason'],'signed reason')
        self.assertEqual(result['id'],'request')
        card.order_origin=None
        self.assertNotIn('order_origin',Host._ask_msg(NS(),card))
