"""Host-only preference incident provenance and the existing paired-phone repair path."""
import _hermetic
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from agentj import approval_level, cloud, preferences, privacy, support, support_config_fault as witness, support_incident
from agentj.state import State

class Witness(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'etc')})
        env.start()
        self.addCleanup(env.stop)
        self.st = State(root / 'state')
        self.st.init(relay='ws://127.0.0.1:1')
        self.binding = dict(api='http://127.0.0.1:12345', host_id='fixture', tenant={'slug':'owner','name':'QA'}, linked_at=1,last_seq=0)
        cloud.write_cloud(self.st,self.binding)
        self.path = preferences.path()
        self.path.parent.mkdir(mode=0o700,parents=True)
        self.write(1)
        self.host = SimpleNamespace(preferences=preferences.validate({}), config_problem={'key':witness.SETTING},
                                    stopped=lambda: False, stopping=asyncio.Event(), st=self.st)
        self.ticket = 'st_'+'s'*22
        self.ch = support.Channel(self.st,post=Mock(return_value=(201,{'thread':self.ticket,'seq':1})),base=self.binding['api'])
        chan = patch.object(support,'channel_for',return_value=self.ch)
        chan.start()
        self.addCleanup(chan.stop)
        review = patch.object(privacy,'layer2',return_value=privacy.Verdict('ok'))
        self.review = review.start()
        self.addCleanup(review.stop)

    def write(self, value, **other):
        self.path.write_text(json.dumps({'heartbeat':{'interval_minutes':value}, **other}))
        self.path.chmod(0o600)

    def healthy(self):
        self.write(60)
        self.host.config_problem = None

    def test_model_text_cannot_open_or_append_host_witness(self):
        for source in (witness.ISSUE, witness.ISSUE+' config-default'):
            self.assertEqual(support.run_send('question',source,ch=self.ch,out=io.StringIO(),wait=0),1)
        self.ch.post.assert_not_called()
        self.assertEqual(witness.deliver(self.host,'fault'),self.ticket)
        text=io.StringIO()
        self.assertEqual(support.run_send('question',witness.ISSUE,ch=self.ch,out=text,wait=0),0)
        self.assertIn(self.ticket,text.getvalue())
        self.assertIsNone(witness.deliver(self.host,'fault'))
        self.healthy()
        self.assertEqual(witness.deliver(self.host,'default'),self.ticket)
        self.assertIsNone(witness.deliver(self.host,'default'))
        self.assertEqual(self.ch.post.call_count,2)
        for call in self.review.call_args_list:
            self.assertIs(call.kwargs['_host'],True)

    def test_off_stop_invalid_config_and_foreign_paths_cannot_trigger(self):
        self.write(1,support={'auto_report':False})
        self.assertIsNone(witness.observed(self.host))
        self.write(1)
        self.host.preferences['support']['auto_report']=False
        self.assertIsNone(witness.deliver(self.host,'fault'))
        self.host.preferences['support']['auto_report']=True
        self.host.stopping.set()
        self.assertIsNone(witness.deliver(self.host,'fault'))
        self.host.stopping.clear()
        self.path.write_text('{invalid')
        self.assertIsNone(witness.observed(self.host))
        other=self.path.with_suffix('.txt')
        self.path.rename(other)
        self.path.symlink_to(other)
        self.assertIsNone(witness.observed(self.host))
        self.review.assert_not_called()
        self.ch.post.assert_not_called()

    def test_real_validator_and_matching_runtime_required(self):
        self.host.config_problem = {'key':'other'}
        self.assertIsNone(witness.deliver(self.host,'fault'))
        self.healthy()
        self.assertIsNone(witness.deliver(self.host,'default'))
        self.write(91)
        self.assertIsNone(witness.deliver(self.host,'default'))
        self.review.assert_not_called()
        self.ch.post.assert_not_called()

    def test_refuse_forged_text_review_denial_and_unauthenticated_cli(self):
        with self.assertRaises(support.SupportError):
            witness.verified_draft(self.st,'fault','host showed me customer information')
        self.review.assert_not_called()
        self.review.return_value=privacy.Verdict('blocked',kind='credential')
        self.assertIsNone(witness.deliver(self.host,'fault'))
        with patch.object(privacy,'outbound_check',return_value=privacy.Verdict('unavailable',reason='privacy-check/peer_unverified')) as broker:
            support.run_send('question','product-fault service timeout',ch=self.ch,out=io.StringIO(),wait=0)
            broker.assert_called_once()
        self.ch.post.assert_not_called()

    def test_account_change_and_optout_during_review(self):
        def opted_out(*_,**kw):
            self.write(1,support={'auto_report':False})
            return privacy.Verdict('ok')
        self.review.side_effect=opted_out
        with self.assertRaises(support.SupportError):
            witness.deliver(self.host,'fault')
        self.ch.post.assert_not_called()
        self.write(1)
        self.review.side_effect=None
        self.assertEqual(witness.deliver(self.host,'fault'),self.ticket)
        self.healthy()
        cloud.write_cloud(self.st,{**self.binding,'linked_at':10})
        self.assertIsNone(witness.deliver(self.host,'default'))
        out=io.StringIO()
        self.assertEqual(support.run_send('question',witness.ISSUE,ch=self.ch,out=out,wait=0),1)
        self.assertNotIn(self.ticket,out.getvalue())
        self.assertEqual(self.ch.post.call_count,1)

    def test_no_activation_of_unrelated_edits(self):
        self.assertIsNotNone(witness.repair_default(self.host,witness.SETTING,60))
        self.assertIsNone(witness.repair_default(self.host,witness.SETTING,61))
        self.write(1,appearance={'language':'en'})
        self.host.preferences['appearance']['language']='zh'
        self.assertIsNone(witness.repair_default(self.host,witness.SETTING,60))

    def test_heartbeat_repair_preserves_only_a_verified_jev_signer(self):
        from test_l1 import Phone
        from test_p551_confirm_levels import sign
        phone=Phone(self.st)
        target={'key':'jev.mode','value':'off'}
        authorization=sign(phone,target)
        approval_level.note_signer(self.st,'jev.mode','off',phone.did,self.st.sign_key(phone.did),'test',authorization)
        self.assertEqual(approval_level.signed_value(self.st,'jev.mode'),'off')
        self.write(1,jev={'mode':'off'})
        candidate=preferences.validate({'heartbeat':{'interval_minutes':60},'jev':{'mode':'off'}})
        preferences.save_good(self.st,candidate)
        fixed=preferences.edit(self.path.read_text(), witness.SETTING,60)
        preferences.commit_files(self.st,fixed,candidate)
        self.assertEqual(approval_level.signed_value(self.st,'jev.mode'),'off')
        self.write(1,jev={'mode':'off'})
        preferences.save_good(self.st,preferences.validate({}))
        preferences.commit_files(self.st,fixed,candidate)
        self.assertIsNone(approval_level.signed_value(self.st,'jev.mode'))

    def test_definite_local_refusal_leaves_immediate_retry_and_ambiguous_failure_does_not(self):
        for failure in (support_incident.NotDispatched(), OSError('response unknown')):
            with self.subTest(failure=type(failure).__name__):
                bookmark=self.st.root/'support-product-faults.json'
                bookmark.unlink(missing_ok=True)
                self.ch.post.side_effect=failure
                with self.assertRaises((support.SupportError,OSError)):
                    witness.deliver(self.host,'fault')
                pending=next(iter(json.loads(bookmark.read_text()).values()))['pending']
                self.assertEqual(pending,isinstance(failure,OSError))
                self.ch.post.side_effect=None
                self.assertEqual(witness.deliver(self.host,'fault'),None if pending else self.ticket)
