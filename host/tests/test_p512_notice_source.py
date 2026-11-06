"""#512 launch provenance at the busy wrapper and native workflow IPC seams."""
import _hermetic
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from agentj import busy_launch, tasks
from test_hotfix492_scheduler import Host as FakeHost, task_dir

class WorkflowSource(unittest.TestCase):
    def test_verified_ceo_manual_run_is_known(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);h=FakeHost(root);task_dir(root/'work','qa','0 1 * * *')
            rid=h.scheduler.request('qa','manual',caller_known=True)
            row=tasks.load(h.st)['runs'][rid]
            self.assertTrue(row['completion_known']);self.assertFalse(row['owner_notify'])

    def test_external_manual_even_signed_task_stays_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);h=FakeHost(root);task_dir(root/'work','qa','0 1 * * *')
            e=tasks.find(str(root/'work'),'qa');d=tasks.load(h.st)
            d['on']['qa']={'tsha':e['tsha'],'by':'phone:paired'};tasks.save(h.st,d)
            rid=h.scheduler.request('qa','manual')
            self.assertFalse(tasks.load(h.st)['runs'][rid]['completion_known'])

class NativeWorkflowSource(unittest.TestCase):
    def test_running_scope_requires_known_contract_instance_and_live_signature(self):
        from agentj import workflow_mail
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);h=FakeHost(root);task_dir(root/'work','qa','0 1 * * *')
            e=tasks.find(str(root/'work'),'qa');scheduler=h.scheduler
            rid=scheduler.request('qa','manual',caller_known=True)
            d=tasks.load(h.st);d['runs'][rid]['status']='running'
            d['on']['qa']={'tsha':e['tsha'],'by':'phone:paired'};tasks.save(h.st,d)
            scheduler.mail_scope={'live':True,'mail_contract':workflow_mail.task_contract(e),
                'mail_roots':[(Mock(pid=123,returncode=None),'token')],
                'mail_admission':{'run_id':rid,'instance_id':scheduler.instance_id,'scheduled':True}}
            with patch.object(workflow_mail,'descendant',return_value=True):
                self.assertTrue(scheduler.completion_caller_known(456))
                d=tasks.load(h.st);d['on'].clear();tasks.save(h.st,d)
                self.assertFalse(scheduler.completion_caller_known(456))
                d['on']['qa']={'tsha':e['tsha'],'by':'phone:paired'}
                d['runs'][rid]['completion_known']=False;tasks.save(h.st,d)
                self.assertFalse(scheduler.completion_caller_known(456))

class DescendantSource(unittest.TestCase):
    def test_verified_parent_survives_unrelated_unknown_turn(self):
        with tempfile.TemporaryDirectory() as td:
            l=busy_launch.Launch(td);now=time.time()
            table={10:(1,10,'10',now-20),20:(10,10,'20',now-5),30:(20,10,'30',now)}
            l.accept_verified({'key':'run:parent','pid':20,'proc_start':'20','_ctime':now-5,'label':'CEO'},table)
            l.turn(False,now-2)
            self.assertTrue(l.known_process(30,table))
            l.accept_verified({'key':'run:child','pid':30,'proc_start':'30','_ctime':now,'label':'child'},table)
            self.assertTrue(l.ok('run:child'))
            self.assertNotIn('chain',l.fields('run:child'))
            l.save();again=busy_launch.Launch(td)
            self.assertTrue(again.known_process(30,table))
            table[30]=(20,10,'reused',now)
            table[20]=(10,10,'reused',now-5)
            self.assertFalse(again.known_process(30,table))

    def test_scanner_first_refusal_does_not_race_verified_ipc(self):
        with tempfile.TemporaryDirectory() as td:
            l=busy_launch.Launch(td);now=time.time()
            table={20:(1,20,'20',now)}
            item={'key':'run:race','pid':20,'proc_start':'20','_ctime':now}
            l.turn(False,now-2);l.judge(item,{},table,set())
            self.assertFalse(l.ok('run:race'))
            self.assertTrue(l.accept_verified(item,table))
            self.assertTrue(l.known_process(20,table))

    def test_passive_clean_item_has_no_inheritable_pin(self):
        with tempfile.TemporaryDirectory() as td:
            l=busy_launch.Launch(td);now=time.time()
            table={20:(1,20,'20',now)}
            l.judge({'key':'run:passive','pid':20,'proc_start':'20','_ctime':now}, {},table,set())
            self.assertTrue(l.ok('run:passive'))
            self.assertFalse(l.known_process(20,table))

    def test_forged_item_cannot_claim_live_trusted_pid(self):
        with tempfile.TemporaryDirectory() as td:
            l=busy_launch.Launch(td);now=time.time()
            table={20:(1,20,'20',now-5),30:(20,20,'30',now)}
            l.accept_verified({'key':'run:parent','pid':20,'proc_start':'20','_ctime':now-5},table)
            l.turn(False,now-2)
            l.judge({'key':'run:forged','pid':30,'proc_start':'30','_ctime':now,'report':'/tmp/forged'}, {},table,set())
            self.assertFalse(l.ok('run:forged'))

    def test_external_process_does_not_inherit_named_parent(self):
        with tempfile.TemporaryDirectory() as td:
            l=busy_launch.Launch(td);now=time.time()
            table={20:(1,20,'20',now-5),30:(1,30,'30',now)}
            l.accept_verified({'key':'run:parent','pid':20,'proc_start':'20','_ctime':now-5},table)
            self.assertFalse(l.known_process(30,table))
            l.turn(False,now-2)
            l.judge({'key':'run:child','pid':30,'proc_start':'30','_ctime':now,'parent':'run:parent'}, {},table,set())
            self.assertFalse(l.ok('run:child'))

if __name__=='__main__':unittest.main()
