"""#506 internal results belong to main-session verification; native completion metadata survives restart."""
import _hermetic
import datetime
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from agentj import busy, tasks
from agentj.serve import Host
from agentj.state import State

class CompletionRoutes(unittest.TestCase):
    def test_internal_result_never_creates_phone_page_or_push(self):
        with tempfile.TemporaryDirectory() as td:
            st=State(Path(td));st.init(relay='ws://127.0.0.1:1')
            d=tasks.load(st);d['runs']['a'*32]={'id':'qa','trigger':'dispatch','completion_known':True};tasks.save(st,d)
            h=Host(st,events='jsonl',read_stdin=False)
            h.hist_add=Mock();h._remember=Mock();h.push_notify=Mock();h._post=Mock()
            res={'id':'qa','run_id':'a'*32,'title':'一次性验收','trigger':'manual','verdict':'ok',
                 'line':'the grant is still active with no end date','report':'workflows/qa/reports/run.md','secs':3,'exit_code':7}
            h.task_finished(res)
            h.hist_add.assert_not_called();h._remember.assert_not_called();h.push_notify.assert_not_called()
            self.assertEqual(len(h.busy.finished),1)
            v=h.busy.finished[0];self.assertFalse(v['foreign']);self.assertIn('run.md',v['report']);self.assertEqual(v['exit_code'],7)

    def test_only_host_recorded_owner_schedule_notifies_and_localizes(self):
        with tempfile.TemporaryDirectory() as td:
            st=State(Path(td));st.init(relay='ws://127.0.0.1:1')
            d=tasks.load(st)
            d['runs']['b'*32]={'id':'qa','trigger':'schedule','tsha':'hash','owner_notify':True}
            tasks.save(st,d)
            h=Host(st,events='jsonl',read_stdin=False)
            h._remember=Mock();h._post=Mock();h.hist_add=Mock(return_value={});h.push_notify=Mock()
            h.task_finished({'id':'qa','run_id':'b'*32,'title':'每日简报','verdict':'ok','line':'English secret summary'})
            self.assertEqual(h._remember.call_args.args[1],'定时任务「每日简报」已完成。')
            h.lang='en';h.task_finished({'id':'qa','run_id':'b'*32,'title':'Daily brief','verdict':'fail','line':'原文'})
            self.assertEqual(h._remember.call_args.args[1],'Scheduled task “Daily brief” failed.')

    def test_untrusted_result_flag_and_legacy_run_cannot_notify(self):
        for run in ({}, {"id":"qa","trigger":"schedule"}, {"id":"qa","trigger":"manual","owner_notify":True},
                    {"id":"other","trigger":"schedule","owner_notify":True}):
            with self.subTest(run=run), tempfile.TemporaryDirectory() as td:
                st=State(Path(td));st.init(relay='ws://127.0.0.1:1')
                d=tasks.load(st);d['runs']['c'*32]=run;tasks.save(st,d)
                h=Host(st,events='jsonl',read_stdin=False)
                h.hist_add=Mock();h._remember=Mock();h.push_notify=Mock()
                h.task_finished({'id':'qa','run_id':'c'*32,'owner_notify':True,'trigger':'schedule','verdict':'ok','line':'raw'})
                h._remember.assert_not_called();h.hist_add.assert_not_called();h.push_notify.assert_not_called()
                self.assertTrue(h.busy.finished[0]['foreign'])

    def test_owner_notification_provenance_is_from_host_enable_only(self):
        from test_hotfix492_scheduler import Host as FakeHost, task_dir
        for by,trigger,expected in [('terminal','schedule',False),('phone:paired','manual',False),
                                    ('phone:paired','schedule',True),('account:signed','catch_up',True),
                                    ('','schedule',False),('phone:paired','dispatch',False)]:
            with self.subTest(by=by,trigger=trigger),tempfile.TemporaryDirectory() as td:
                root=Path(td);h=FakeHost(root);dpath=task_dir(root/'work','qa','0 1 * * *')
                t=json.loads((dpath/'task.json').read_text());t['notify_webapp']=True;(dpath/'task.json').write_text(json.dumps(t))
                e=tasks.find(str(root/'work'),'qa');d=tasks.load(h.st)
                d['on']['qa']={'tsha':e['tsha'],'by':by};tasks.save(h.st,d)
                rid=h.scheduler.request('qa',trigger)
                self.assertEqual(tasks.load(h.st)['runs'][rid]['owner_notify'],expected)

    def test_dispatch_source_is_checked_and_request_taint_wins(self):
        from types import SimpleNamespace
        from agentj import provenance
        from test_hotfix492_scheduler import Host as FakeHost, task_dir
        for src,taint,expected in [(provenance.phone(),False,True),(None,False,False),(provenance.phone(),True,False)]:
            with self.subTest(src=src,taint=taint),tempfile.TemporaryDirectory() as td:
                root=Path(td);h=FakeHost(root);task_dir(root/'work','qa','0 1 * * *')
                h.agent=SimpleNamespace(native_source=src)
                with provenance.request_source(None if taint else src):
                    rid=h.scheduler.request('qa','dispatch')
                self.assertEqual(tasks.load(h.st)['runs'][rid]['completion_known'],expected)

    def test_disabled_owner_notifications_stay_internal(self):
        from test_hotfix492_scheduler import Host as FakeHost, task_dir
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);h=FakeHost(root);task_dir(root/'work','qa','0 1 * * *')
            e=tasks.find(str(root/'work'),'qa');d=tasks.load(h.st)
            d['on']['qa']={'tsha':e['tsha'],'by':'phone:paired'};tasks.save(h.st,d)
            rid=h.scheduler.request('qa','schedule');r=tasks.load(h.st)['runs'][rid]
            self.assertFalse(r['owner_notify']);self.assertTrue(r['completion_known'])

    def test_native_restart_uses_original_description_and_exit_code(self):
        for code in (0,7):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as td:
                root=Path(td);owner='shared-claude:qa';key=owner+':native-job'
                b=busy.Board(root);b.trusted=lambda:True
                b.start(key,None,label='Round 5 owner turn background task',session=owner);b.mark_bg(key)
                b=busy.Board(root);p=root/'qa.jsonl'
                body=f'<task-notification><task-id>native-job</task-id><status>completed</status><summary>Background command "wrong summary" completed (exit code {code})</summary></task-notification>'
                p.write_text(json.dumps({'type':'user','origin':{'kind':'task-notification'},'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'message':{'content':body}})+'\n')
                b.reconcile_claude(owner,str(p));self.assertEqual(len(b.finished),1)
                self.assertEqual(b.finished[0]['label'],'Round 5 owner turn background task')
                self.assertEqual(b.finished[0]['exit_code'],code)

    def test_finite_grant_replacement_preserves_deadline(self):
        from agentj import workflow_mail as wm
        service=object.__new__(wm.Service)
        service.make=Mock(return_value={'issued':int(time.time()),'expires':253402300799,'never_expires':False})
        old={'issued':100,'expires':100+7*86400,'never_expires':False,'artifact_root':'/tmp/qa-artifacts','max':3,'per_day':3}
        core=service.replacement({'workflow_id':'qa'},old,'account_changed')
        self.assertEqual((core['issued'],core['expires']),(old['issued'],old['expires']))
        self.assertEqual(service.make.call_args.args[3],7)
        self.assertFalse(core['never_expires'])

    def test_legacy_native_start_uses_description_not_harness_summary(self):
        for summary,expected in [('Background command "Round 5 owner turn background task" completed (exit code 0)',
                                  'Round 5 owner turn background task'),
                                 ('unrecognized harness prose completed exit 0', '后台活')]:
            with self.subTest(summary=summary),tempfile.TemporaryDirectory() as td:
                root=Path(td);owner='shared-claude:qa';key=owner+':native-job'
                b=busy.Board(root);b.trusted=lambda:True
                b.start(key,None,label='old description not persisted',session=owner)
                pstart=root/'busy/native-starts.json';saved=json.loads(pstart.read_text());saved[key].pop('label')
                pstart.write_text(json.dumps(saved));b=busy.Board(root)
                p=root/'qa.jsonl';body=f'<task-notification><task-id>native-job</task-id><status>completed</status><summary>{summary}</summary></task-notification>'
                p.write_text(json.dumps({'type':'user','origin':{'kind':'task-notification'},
                    'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'message':{'content':body}})+'\n')
                b.reconcile_claude(owner,str(p));self.assertEqual(len(b.finished),1)
                self.assertEqual(b.finished[0]['label'],expected)
        self.assertEqual(busy.notification_label('<summary>Background command "summary" completed (exit code 0)</summary>',
                                                'host original description'),'host original description')
        self.assertEqual(busy.notification_label('<summary>prefix Background command "desc" completed (exit code 0)</summary>'),'后台活')

    def test_native_unknown_exit_stays_unknown(self):
        self.assertIsNone(busy.notification_exit('<summary>finished with no exit metadata</summary>'))
        self.assertEqual(busy.notification_exit('<exit-code>-9</exit-code>'),-9)

if __name__=='__main__':unittest.main()
