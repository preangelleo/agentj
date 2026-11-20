"""Closed diagnostic vocabulary, exact receipt and local 24-hour incident state."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from agentj import cloud, support, support_incident as incident
from agentj.state import State


class ProductFault(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.st = State(self.root / 'state')
        self.st.init(relay='ws://127.0.0.1:1')
        cloud.write_cloud(self.st, {'api': 'http://127.0.0.1:12345', 'host_id':'h_fixture','tenant':{'slug':'fixture','name':'QA'},'linked_at':1,'last_seq':0})
        self.patch = mock.patch.dict(os.environ, {'XDG_CONFIG_HOME':str(self.root/'etc')})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.channel = mock.Mock(st=self.st, signed=True)
        self.thread = 'st_' + 'a'*22
        self.channel.send.return_value=(201, {'thread':self.thread,'seq':1})
        self.sent=[]
        self.at=1000000

    def submit(self, text):
        def send(body, parent, ch):
            self.sent.append(body)
            ch.send({'thread':parent} if parent else {})
            return 0
        return incident.submit('product-fault '+text, self.channel, send, io.StringIO(), clock=lambda:self.at)

    def test_first_followup_dedupe_and_boundary(self):
        self.st.log_path.write_text(json.dumps({'ev':'config_migration_failed','text':'customer private text'})+'\n')
        spec='config invalid-config at:heartbeat.enabled'
        for t in [spec,spec,spec+' config-default',spec+' config-default']:
            self.assertEqual(self.submit(t),0)
        self.assertEqual(self.channel.send.call_count,2)
        self.assertEqual(self.channel.send.call_args.args[0],{'thread':self.thread})
        self.assertIn('config_migration_failed',self.sent[0])
        self.assertIn('heartbeat.enabled',self.sent[1])
        self.assertNotIn('customer private',str(self.sent))
        self.at+=86399
        self.submit(spec)
        self.assertEqual(self.channel.send.call_count,2)
        self.at+=1
        self.submit(spec)
        self.assertEqual(self.channel.send.call_count,3)

    def test_ambiguous_failure_privacy_and_off(self):
        self.channel.send.side_effect=OSError('lost acknowledgment')
        with self.assertRaises(OSError): self.submit('service timeout')
        self.assertEqual(self.submit('service timeout'),1)
        self.assertEqual(self.channel.send.call_count,1)
        for text in ['config private-text','relay unavailable at:private.txt','other timeout personal-data']:
            with self.assertRaises(support.SupportError): self.submit(text)
        self.channel.send.reset_mock()
        for config in ['{support:{auto_report:false}}','{','{support:42}']:
            from agentj import preferences
            p=preferences.path();p.parent.mkdir(exist_ok=True,parents=True);p.write_text(config)
            self.assertEqual(self.submit('agent unavailable'),1)
        self.channel.send.assert_not_called()

    def test_source_change_requires_public_cause_and_summary(self):
        spec = 'agent startup-failed at:serve.py'
        self.submit(spec)
        with self.assertRaises(support.SupportError):
            self.submit(spec + ' source-patch')
        self.submit(spec + ' source-patch cause:stale-state change:reset-state')
        self.assertEqual(self.channel.send.call_count, 2)
        self.assertIn('Cause: stale-state', self.sent[-1])
        self.assertIn('Change: reset-state', self.sent[-1])
        self.assertIn('serve.py', self.sent[-1])

    def test_rejected_model_review_preserves_original_privacy_floor(self):
        from agentj import privacy
        denied = privacy.Verdict('blocked', reason='fixture', kind='credential')
        with mock.patch.object(privacy, 'outbound_check', return_value=denied):
            rc = support.run_send('question', 'product-fault config invalid-config', ch=self.channel,
                                  wait=0, out=io.StringIO())
        self.assertEqual(rc, support.EXIT_BLOCKED)
        self.channel.send.assert_not_called()
        self.assertFalse((self.st.root / 'support-product-faults.json').exists())

    def test_no_repair_without_open_thread(self):
        with self.assertRaises(support.SupportError): self.submit('config unavailable config-default')
        self.channel.send.assert_not_called()
