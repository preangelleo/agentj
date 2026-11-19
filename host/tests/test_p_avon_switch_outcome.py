"""Customer switch results use the existing reply page with verified program metadata."""
import os
import time
import unittest
from unittest.mock import patch

from agentj import switch_harness as switch


class Outcome(unittest.TestCase):
    def row(self, reason="older_version"):
        return dict(job="a" * 16, at=time.time(), target="opencode", status="ok", version="2.0.22",
                    executable="/opt/homebrew/Cellar/opencode/2.0.22/bin/opencode", executable_source="system",
                    binary_candidates=[dict(path="/usr/local/Cellar/opencode/1.18.34/bin/opencode", version="1.18.34", source="system", reason=reason)])

    def test_path_and_actual_fallback(self):
        for reason, timed in (("older_version", False), ("native_timeout", True)):
            row = self.row(reason)
            meta = switch._result_meta(row)
            for lang, timeout in (("zh", "超时"), ("en", "timed out")):
                text = switch._say_receipt(meta, lang)
                self.assertIn(row["executable"], text)
                self.assertIn(row["binary_candidates"][0]["path"], text)
                self.assertEqual(timed, timeout in text)
            self.assertEqual(meta["binary_candidates"], row["binary_candidates"])

    def test_every_failure_and_rollback_stays_inside_product(self):
        for lang in ("zh", "en"):
            for code in sorted(set(switch.REASON[lang]) | switch.switch_outcome.PLAIN.keys()):
                with self.subTest(lang=lang, reason=code):
                    row = {**self.row(), "from":"codex", "reason":code, "status":"failed"}
                    for text in (switch._say(row, lang), switch._say_receipt(row, lang),
                                 switch._say_receipt({**row, "status":"rolled_back", "detail":"prompt", "err":"auth"}, lang)):
                        self.assertNotRegex(text.lower(), r"agentj (agent|doctor|config|secret|service|setup)|codex login|网页终端|web terminal|--force")
                        self.assertRegex(text, r"当前对话|手机|模型和 API Key|紧急修复|配对|密钥卡|对话|掌枢|chat|phone|Agent J|Models & API|Emergency repair")

    def test_newer_first_and_both_directions_of_real_timeout(self):
        discovered = self.row()
        self.assertIn("已选用新版", switch._say_receipt(discovered, "zh"))
        self.assertNotIn("超时", switch._say_receipt(discovered, "zh"))
        actual = self.row("native_timeout")
        self.assertIn("较旧版本", switch._say_receipt(actual,"zh"))
        self.assertIn("已改用", switch._say_receipt(actual,"zh"))
        actual['binary_candidates'][0].update(version='3.0.0',path='/opt/3.0.0/bin/opencode')
        self.assertNotIn("较旧版本", switch._say_receipt(actual,"zh"))
        self.assertNotIn("已选用新版", switch._say_receipt(actual,"zh"))
        self.assertIn("实际模型检查中超时", switch._say_receipt(actual,"zh"))

    def test_legacy_receipt_with_no_selected_version_and_true_timeout(self):
        for missing in (None,"invalid"):
            row = {**self.row("native_timeout"), "version":missing}
            text = switch._say_receipt(switch._result_meta(row), "zh")
            self.assertIn("实际模型检查中超时",text)
            self.assertNotIn("已改用通过检查的",text)

    def test_home_installations_survive_host_agent_and_phone_projection(self):
        for home in ('/home/qa-owner','/home/qa-owner/.cache/aq45-isolated/h',os.path.join(os.sep,'Users','QA Owner')):
            with self.subTest(home=home), patch.dict(os.environ,{'HOME':home}):
                row = self.row()
                selected = '/.opencode/bin/opencode'
                older = '/.local/share/mise/installs/opencode/1.18.34/opencode'
                row.update(executable=home+selected)
                row['binary_candidates'][0].update(path=home+older)
                projected = switch._result_meta(row)
                self.assertEqual(projected.get('executable'), '~'+selected)
                self.assertEqual(projected, switch._result_meta(projected))
                for lang in ('zh','en'):
                    text = switch._say_receipt(projected, lang)
                    for wanted in ('2.0.22 · ~'+selected, '1.18.34 · ~'+older):
                        self.assertIn(wanted, text)
                    self.assertNotIn(home, text)
                self.assertEqual(row['executable'],home+selected)

    def test_invalid_home_paths_do_not_hide_verified_versions(self):
        with patch.dict(os.environ,{'HOME':'/home/qa-owner'}):
            for invalid in ('/home/qa-owner/QA-CANARY/bin/opencode','~/QA-CANARY/bin/opencode',
                            '~/.opencode/../QA-CANARY/opencode','~/.opencode/bin/opencode\nQA-CANARY',
                            '/home/another-owner/.opencode/bin/opencode',os.path.join(os.sep,'Users','someone','.opencode','bin','opencode')):
                with self.subTest(invalid=invalid):
                    row = self.row()
                    row['executable'] = invalid
                    row['binary_candidates'][0]['path'] = invalid
                    meta = switch._result_meta(row)
                    self.assertNotIn('executable',meta)
                    self.assertEqual(meta, switch._result_meta(meta))
                    text = switch._say_receipt(meta,'zh')
                    self.assertIn('OpenCode 2.0.22',text)
                    self.assertIn('OpenCode 1.18.34',text)
                    self.assertNotIn('QA-CANARY',str(meta))
                    self.assertNotIn(invalid,text)

    def test_public_system_locations_are_closed_under_projection(self):
        for prefix in ('/usr','/usr/local','/opt/homebrew',os.path.join(os.sep,'home','linuxbrew','.linuxbrew')):
            for installed in ('bin/opencode','Cellar/opencode/2.0.22_1/bin/opencode',
                              'lib/node_modules/@opencode/cli/bin/opencode.exe'):
                path = prefix+'/'+installed
                row = {**self.row(), 'executable':path}
                with self.subTest(path=path):
                    meta = switch._result_meta(row)
                    self.assertEqual(meta.get('executable'),path)
                    self.assertEqual(switch._result_meta(meta),meta)
                    self.assertIn(path,switch._say_receipt(meta,'zh'))

    def test_private_system_paths_and_unbounded_versions_cannot_cross_either_projection(self):
        for path in ('/srv/QA customer name/opencode','/etc/service/private-name','/root/private-note',
                     '/usr/local/Cellar/opencode/private-note/bin/opencode','/usr/local/bin/opencode\nprivate-note',
                     os.path.join(os.sep,'home','linuxbrew','.linuxbrew','lib/node_modules/private-note/bin/opencode.exe')):
            row = self.row()
            row['executable'] = row['binary_candidates'][0]['path'] = path
            with self.subTest(path=path):
                meta = switch._result_meta(row)
                self.assertNotIn('executable',meta)
                self.assertNotIn(path,str(switch._result_meta(meta)))
                self.assertNotIn(path,switch._say_receipt(meta,'en'))
                self.assertIn('OpenCode 2.0.22',switch._say_receipt(meta,'en'))
        for malformed in ('1.'+'8'*80, '1.2.3-rc'+'8'*80):
            row = self.row()
            row.update(version=malformed)
            row['binary_candidates'][0]['version'] = malformed
            self.assertNotIn(malformed,str(switch._result_meta(row)))
            self.assertNotIn(malformed,switch._say_receipt(row,'en'))

    def test_binary_projection_ignores_non_paths_and_extra_fields(self):
        row = self.row()
        row["line"] = "RAW-CANARY"
        row["binary_candidates"][0]["stderr"] = "RAW-CANARY"
        row["binary_candidates"].extend([dict(path="/tmp/../RAW-CANARY", version="1.0.0", source="path", reason="selected"),
                                         dict(path="/tmp/one", version="RAW-CANARY", source="path", reason="selected")])
        self.assertNotIn("RAW-CANARY", str(switch._result_meta(row)))


class LocalPublication(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from agentj import history, switch_report as report
        from test_p155_switch_harness import mk_state

        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.st = mk_state(root.name, "opencode")
        units = Path(root.name)/'units'
        receipt_path = patch.object(switch, "receipt_path", side_effect=lambda name=switch.RECEIPT: units/name)
        receipt_path.start()
        self.addCleanup(receipt_path.stop)
        self.report = report
        self.device = self.st.add_device(b'd' * 32, "QA", b's' * 32)
        hist = history.History(self.st, on=True)
        self.host = SimpleNamespace(st=self.st, hist=hist, hist_add=hist.add, lang="zh", _agent_pids=lambda:set())
        self.message = hist.add({'k':'phone','dev':self.device,'text':'QA'},end='done')['id']
        self.origin = SimpleNamespace(epoch=hist.epoch,device=self.device,source={'message_id':self.message,'channel':'phone'}, signing_key=b's'*32)
        self.job = "a" * 16
        self.record = Outcome().row()

    def admission(self, job=None):
        self.report.bind(self.st, job or self.job, self.origin)

    def finish(self, **fields):
        return switch.write_receipt(self.st, {**self.record, **fields})

    def test_owner_reply_body_exactly_once_across_worker_retry_and_host_recovery(self):
        self.admission()
        self.finish(line="PRIVATE-CANARY", config="PRIVATE-CANARY")
        self.assertTrue(self.report.deliver(self.host, self.job)['ok'])
        self.assertTrue(self.report.deliver(self.host)['duplicate'])
        rows = [row for row in self.host.hist.turns.values() if row['src'].get('switch_job') == self.job]
        self.assertEqual(len(rows), 1)
        turn, = rows
        self.assertEqual(turn['src']['k'], 'agent')
        self.assertEqual(turn['src']['quote'], {'id':self.message})
        self.assertIn(self.record['executable'], turn['reply']['text'])
        self.assertNotIn('PRIVATE-CANARY', str(turn))

    def test_superseded_job_or_replayed_worker_cannot_publish_to_new_job(self):
        self.admission()
        self.finish()
        other = 'b' * 16
        self.admission(other)
        self.assertEqual(self.report.deliver(self.host,self.job)['error'], 'wrong_job')
        self.assertEqual(self.report.deliver(self.host,other)['error'], 'no_result')
        self.finish(job=other, status='failed', reason='probe_model_unconfirmed')
        self.assertTrue(self.report.deliver(self.host,other)['ok'])
        self.assertEqual(len(self.host.hist.turns), 2)
        self.assertIn('模型和 API Key', self.host.hist.turns[2]['reply']['text'])

    def test_changed_pair_reset_and_unconfirmed_active_agent_block_publication(self):
        self.admission()
        self.finish()
        self.st.set_agent_config('codex',self.st.agent_config()['dir'],record_root=False)
        self.assertEqual(self.report.deliver(self.host)['error'],'context_changed')
        self.finish(status='failed',reason='version_unavailable')
        self.st.add_device(b'd'*32,'repaired',b'n'*32)
        self.assertEqual(self.report.deliver(self.host)['error'],'context_changed')
        self.origin.signing_key = b'n'*32
        self.admission()
        self.finish(status='failed',reason='version_unavailable')
        self.host.hist.reset('qa')
        self.assertEqual(self.report.deliver(self.host)['error'],'context_changed')
        self.assertFalse(self.host.hist.turns)

    def test_wrong_peer_arbitrary_text_and_nonfinal_or_expired_result_are_rejected(self):
        import json
        self.admission()
        self.finish(status='waiting')
        self.assertEqual(self.report.deliver(self.host)['error'],'no_result')
        self.finish()
        frame = {'cmd':'switch_result','job':self.job}
        self.assertEqual(self.report.receive(self.host,{**frame,'text':'FAKE'},1,os.getuid())['error'],'refused')
        self.assertEqual(self.report.receive(self.host,frame,1,-1)['error'],'refused')
        with patch.object(self.report.ctl_auth,'refusal',return_value='agent'):
            self.assertEqual(self.report.receive(self.host,frame,100,os.getuid())['error'],'refused')
        scope = self.st.root/self.report.SCOPE
        obj = json.loads(scope.read_text())
        obj['at'] -= switch.JOB_MAX_AGE + 5
        self.st.write_private(scope,json.dumps(obj).encode())
        self.assertEqual(self.report.deliver(self.host)['error'],'stale_job')
        self.assertEqual(len(self.host.hist.turns),1)

    def test_unknown_origin_has_no_main_page_and_disk_retry_keeps_one_page(self):
        self.report.bind(self.st, self.job)
        self.finish()
        self.assertEqual(self.report.deliver(self.host)['error'],'unbound')
        self.admission()
        self.finish()
        with patch.object(self.st, 'append_private', side_effect=OSError):
            self.assertEqual(self.report.deliver(self.host)['error'],'history_unavailable')
            self.assertEqual(self.report.deliver(self.host)['error'],'history_unavailable')
        self.assertTrue(self.report.deliver(self.host)['ok'])
        from agentj import history
        restored = history.History(self.st,on=True)
        self.assertEqual(len(restored.turns),2)
        self.assertEqual(restored.turns[2]['src']['switch_job'],self.job)
        self.assertTrue(self.report.deliver(self.host)['duplicate'])
