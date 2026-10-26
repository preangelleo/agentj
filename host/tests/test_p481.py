"""P481: multi-version Claude and bounded post-upgrade repair."""
import _hermetic  # noqa: F401
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from agentj import binaries, harness, update, doctor, switch_harness
from test_p282_switch_probe import exe
from test_p277_upgrade_diag import _Env


class Claude(unittest.TestCase):
    def test_highest_executable_and_explicit_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            shim = exe(home / '.local/share/mise/shims/claude', '#!/bin/sh\nexec mise x claude -- "$@"\n')
            base = home / '.local/share/mise/installs/claude'
            older = exe(base / '2.1.97/bin/claude')
            newer = exe(base / '2.1.308/bin/claude')
            broken = exe(base / '2.2.0/bin/claude'); broken.chmod(0o644)
            env = {'HOME': str(home), 'PATH': str(shim.parent)}
            r = binaries.resolve('claude', env)
            self.assertEqual(r['path'], str(newer))
            self.assertEqual(r['reason'], 'ambiguous_install')
            self.assertIn('AGENTJ_CLAUDE_BIN', r['hint'])
            self.assertIn('mise', r['hint'])
            with mock.patch.object(harness, 'version_of', return_value='2.1.308'), mock.patch.object(harness, 'claude_login', return_value=('ok', 'fixture')):
                h = harness._one('claude', env)
            self.assertTrue(h['installed'])
            where = switch_harness._where(h)
            self.assertEqual(where['selection'], 'ambiguous_install')
            self.assertIn('AGENTJ_CLAUDE_BIN', where['selection_hint'])
            self.assertIn('ambiguous_install', switch_harness._say_scan({'ok':True,'current':'codex','harnesses':[{**h, **where, 'callable':True}]}, 'zh'))
            self.assertEqual(h['resolution']['reason'], 'ambiguous_install')
            self.assertEqual(binaries.resolve('claude', {**env, 'AGENTJ_CLAUDE_BIN': str(older)})['path'], str(older))
            cfg = home / '.config/mise/config.toml'; cfg.parent.mkdir(parents=True)
            cfg.write_text('[tools]\nclaude = "2.1.97"\n')
            self.assertEqual(binaries.resolve('claude', env)['path'], str(older))
            self.assertEqual(binaries.resolve('claude', {**env, 'AGENTJ_CLAUDE_BIN': str(newer)})['path'], str(newer))

    def test_asdf_and_unknown_versions_fail_informatively(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            shim = exe(home / '.asdf/shims/claude', '#!/bin/sh\nexec asdf exec claude "$@"\n')
            base = home / '.asdf/installs/claude'
            exe(base / '2.1.97/bin/claude'); new = exe(base / '2.1.308/bin/claude')
            env = {'HOME': str(home), 'PATH': str(shim.parent)}
            self.assertEqual(binaries.resolve('claude', env)['path'], str(new))
            (home / '.tool-versions').write_text('claude missing\n')
            r = binaries.resolve('claude', env)
            self.assertIsNone(r['path'])
            self.assertEqual(r['reason'], 'ambiguous_install')


class Upgrade(_Env):
    def test_receipt_counts_waiting_and_other_todos(self):
        rows = [{'id':'version','status':'ok'}, {'id':'main-inject','status':'fail','summary':'no verified main Agent launch yet'},
                {'id':'root-structure','status':'fail'}, {'id':'serve','status':'warn'}]
        text = update.completion_text({'to':'9.0','from':'8.0'}, update.doctor_counts(rows), 'zh', rows)
        self.assertIn('升级成功，一切正常', text)
        self.assertIn('Upgrade succeeded; everything is working', update.completion_text({'to':'9.0'}, update.doctor_counts(rows), 'en', rows))
        self.assertNotIn('升级成功', update.completion_text({'to':'9.0'}, (0,0,1), 'zh', [{'id':'state','status':'fail'}]))

    def test_repair_only_named_findings_and_keep_json_clean(self):
        rows = [{'id':'root-structure','status':'fail'}, {'id':'serve','status':'warn','summary':'relay reconnecting'},
                {'id':'main-inject','status':'warn','summary':'no verified main Agent launch yet'}]
        (self.st.root / 'work').mkdir()
        with mock.patch('agentj.working_root.select', return_value=self.st.root / 'work'), \
             mock.patch('agentj.working_root.record') as record, mock.patch('agentj.wizard.bootstrap_root') as boot, \
             mock.patch('agentj.wizard.install'), mock.patch('agentj.wizard.refresh_root') as refresh, mock.patch('agentj.reconnect.run', return_value=0) as reconnect:
            ids = update.repair_upgrade(self.st, rows)
        self.assertEqual(ids, ['root-structure', 'serve'])
        record.assert_called_once(); boot.assert_called_once(); refresh.assert_called_once(); reconnect.assert_called_once()
        with mock.patch('agentj.reconnect.run') as reconnect:
            self.assertEqual(update.repair_upgrade(self.st, [{'id':'state','status':'fail'}]), [])
            reconnect.assert_not_called()

    def test_repair_failure_remains_hard_failure(self):
        rows = [{'id':'serve','status':'fail','summary':'not answering'}]
        with mock.patch('agentj.reconnect.run', return_value=1):
            ids = update.repair_upgrade(self.st, rows)
        self.assertEqual(ids, ['serve'])
        self.assertEqual(update.upgrade_blockers(rows)[0], ['serve'])

    def test_real_root_repair_preserves_owner_text_and_keys(self):
        from agentj import preferences, working_root, wizard
        root = self.st.root.parent / 'work'; root.mkdir()
        original = '# My instructions\nKeep my words.\n'
        (root / 'CLAUDE.md').write_text(original)
        working_root.record(self.st, root)
        before = self.st.config()
        with mock.patch('agentj.starters.ensure', return_value=[]):
            update.repair_upgrade(self.st, [{'id':'root-structure','status':'fail'}])
        text = (root / 'CLAUDE.md').read_text()
        self.assertTrue(text.startswith(original))
        self.assertIn('agentj:main-core', text)
        self.assertEqual(self.st.config(), before)
        with mock.patch('agentj.starters.ensure', return_value=[]):
            update.repair_upgrade(self.st, [{'id':'root-structure','status':'fail'}])
        self.assertEqual((root / 'CLAUDE.md').read_text(), text)

    def test_old_doctor_marker_survives_receipt_consumption(self):
        from agentj import __version__, service_recovery
        update.write_marker(self.st, '0.15.5a1', __version__)
        self.assertTrue(update.upgrade_due(self.st))
        (self.st.root / update.UPGRADED).unlink()
        self.assertFalse(update.upgrade_due(self.st))
        service_recovery.record(self.st, 'ok', __version__)
        self.assertTrue(update.upgrade_due(self.st))

    def test_doctor_repairs_then_rechecks_without_masking_real_failure(self):
        import contextlib, io
        before = [{'id':'serve','status':'fail','summary':'not answering','hint':''}]
        after = [{'id':'state','status':'fail','summary':'bad local state','hint':''}]
        with mock.patch.object(doctor, 'State', return_value=self.st), mock.patch.object(doctor, 'run', side_effect=[before, after]) as run, \
             mock.patch.object(update, 'repair_upgrade', return_value=['serve']) as repair:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(doctor.main(as_json=True, post_upgrade=True), 1)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(json.loads(out.getvalue())['checks'], after)
        repair.assert_called_once_with(self.st, before)

    def test_failed_upgrade_never_prints_success_from_pending_issues(self):
        res = {'result':'failed','reason':'doctor_failed','from':'1.0','to':'2.0','service':'failed',
               'pending_checks':['serve'], 'existing_issues':['root-structure']}
        text = update.result_block(res)
        self.assertNotIn('升级成功', text)
        self.assertNotIn('升级已完成', text)
        self.assertNotIn('upgrade completed', text)

    def test_success_pending_count_is_not_truncated_to_id_display_limit(self):
        res = {'result':'ok','reason':'upgraded','from':'1.0','to':'2.0','service':'ok',
               'pending_checks':['root-structure'], 'pending_count':15}
        self.assertNotIn('待办', update.result_block(res))
        self.assertNotIn('agentj doctor', update.result_block(res))

    def test_old_updater_exit_code_grades_upgrade_but_offline_doctor_keeps_findings(self):
        import contextlib, io
        rows = [{'id':'root-structure','status':'fail','summary':'owner workflow issue','hint':''},
                {'id':'main-inject','status':'fail','summary':'no verified main Agent launch yet','hint':''}]
        update.write_marker(self.st, '0.15.5a1', update.__version__)
        with mock.patch.object(doctor, 'State', return_value=self.st), mock.patch.object(doctor, 'run', return_value=rows), \
             mock.patch.object(update, 'repair_upgrade', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(doctor.main(as_json=True), 0)
            self.assertEqual(doctor.main(as_json=True, offline=True), 1)
        (self.st.root / update.UPGRADED).write_text('[]')
        self.assertFalse(update.upgrade_due(self.st))
