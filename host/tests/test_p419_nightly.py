import _hermetic
import base64
import json
import time
import unittest
from unittest import mock
from agentj import auto_update as au, nightly_report as nr, cloud, __version__
import test_p83_auto_update as baseline

class Nightly(baseline.Nightly):
    def test_disabled_checks_and_reports_once_per_local_date(self):
        check=mock.Mock(return_value={'latest':__version__,'status':'current','why':'ok'})
        with mock.patch.object(au,'enabled',return_value=False):
            self.assertIsNotNone(nr.check(self.st,now=self.now,rand=lambda _:0,check_fn=check))
            self.assertEqual(nr.field(self.st)['auto_update']['state'],'off')
            self.assertFalse(self.due())
            self.assertIsNone(nr.check(self.st,now=self.now+1,check_fn=check))
            self.assertEqual(check.call_count,1)
            self.assertIsNotNone(nr.check(self.st,now=self.now+86400,rand=lambda _:0,check_fn=check))
            self.assertEqual(check.call_count,2)
    def test_window_restart_random_and_network_failure(self):
        check=mock.Mock(side_effect=OSError('never outbound'))
        self.assertIsNone(nr.check(self.st,now=self.now-1,rand=lambda _:60,check_fn=check))
        self.assertIsNone(nr.check(self.st,now=self.now+59,check_fn=check))
        self.assertIsNotNone(nr.check(self.st,now=self.now+60,check_fn=check))
        self.assertEqual(set(nr.field(self.st)['auto_update']),{'state','at','last'})
        self.assertEqual(check.call_count,1)
    def test_missed_window_does_not_check_or_report_at_noon(self):
        check=mock.Mock()
        self.assertIsNone(nr.check(self.st,now=self.now+3*3600,rand=lambda _:0,check_fn=check))
        check.assert_not_called();self.assertEqual(nr.field(self.st),{})
    def test_last_automatic_result_is_metadata_only_and_ack_is_generation_bound(self):
        rec=au.read(self.st);rec.update(result={'from':'0.1.0','to':'0.2.0','result':'rolled_back','reason':'install_failed','recover':'private-command','stderr':'private-output'},result_at=int(self.now)-10);au.write(self.st,rec)
        nr.check(self.st,now=self.now,rand=lambda _:0,check_fn=lambda:{'latest':__version__})
        sent=nr.field(self.st)['auto_update']
        self.assertEqual(set(sent['last']),{'from','to','result','reason','at'})
        self.assertNotIn('private-',json.dumps(sent))
        nr.acknowledge(self.st,{**sent,'at':sent['at']-1});self.assertTrue(nr.field(self.st))
        nr.acknowledge(self.st,sent);self.assertEqual(nr.field(self.st),{})
    def test_arbitrary_local_fields_do_not_cross_the_report(self):
        au.write(self.st,{'state':'off','at':int(self.now),'last':None,'private':'never-send'},nr.FILE)
        self.assertNotIn('never-send',json.dumps(nr.field(self.st)))
        au.write(self.st,{'state':'off','at':int(self.now),'last':{'text':'never-send'}},nr.FILE)
        self.assertEqual(nr.field(self.st),{})
    def test_report_ack_and_legacy_server_fallback(self):
        cloud._NO_AUTO_UPDATE.clear();self.addCleanup(cloud._NO_AUTO_UPDATE.clear)
        cloud.write_cloud(self.st,{'api':'http://127.0.0.1:9','host_id':'h_1','tenant':{'slug':'acme-co','name':'Acme'},'linked_at':int(self.now),'last_seq':0})
        nr.check(self.st,now=self.now,rand=lambda _:0,check_fn=lambda:{'latest':__version__})
        seen=[]
        def post(url,env):
            inner=json.loads(base64.urlsafe_b64decode(env['body']+'='*(-len(env['body'])%4)))
            seen.append('auto_update' in inner)
            return (400,{'error':'bad_request'}) if 'auto_update' in inner else (200,{'ok':True})
        self.assertEqual(cloud.send_report(self.st,[],None,post=post).kind,'ok')
        self.assertEqual(seen,[True,False]);self.assertTrue(nr.field(self.st))
        cloud._NO_AUTO_UPDATE.clear()
        self.assertEqual(cloud.send_report(self.st,[],None,post=lambda *a:(200,{'ok':True})).kind,'ok')
        self.assertEqual(nr.field(self.st),{})
