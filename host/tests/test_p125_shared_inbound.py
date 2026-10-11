"""Shared cutover: actual file precedence, safe repair and stale-session guidance."""
from test_p80_claude_inbound import Defaults
from agentj import claude_inbound as inbound, doctor
from unittest.mock import patch
import json

class SharedPolicy(Defaults):
    def setUp(self):
        super().setUp()
        mocked = patch.object(inbound, 'managed_paths', return_value=[self.home/'managed.json'])
        mocked.start(); self.addCleanup(mocked.stop)
    def test_project_hold_overridden_locally_shared_bytes_preserved(self):
        p=self.home/'.claude/settings.json';p.parent.mkdir();p.write_text('{"crossSessionInbound":"hold","other":7}')
        before=p.read_bytes()
        self.assertTrue(inbound.ensure_shared_default(self.st))
        self.assertEqual(p.read_bytes(),before)
        self.assertEqual(inbound.effective(str(self.home))[::2],('local','accept'))
        self.assertIn('需新会话',inbound.diagnostic(self.st))
        self.assertIn('/clear',inbound.diagnostic(self.st,'en'))
    def test_local_refuse_repaired_only_owned_key_and_backup_once(self):
        # P130 retains the original parity id: P127 respects refuse until the owner explicitly enables it.
        p=self.home/'.claude/settings.local.json';p.parent.mkdir();p.write_text('{"crossSessionInbound":"refuse","other":7}')
        self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertEqual(json.loads(p.read_text()),{'crossSessionInbound':'refuse','other':7})
        self.assertTrue(inbound.set_enabled(True,str(self.home)))
        self.assertEqual(json.loads(p.read_text()),{'crossSessionInbound':'accept','other':7})
        self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertEqual(len(list(p.parent.glob('settings.local.json.agentj-backup-*'))),1)
    def test_managed_refuse_read_only_and_phone_specific_reason(self):
        p=self.home/'managed.json';p.write_text('{"crossSessionInbound":"refuse"}')
        before=p.read_bytes()
        self.assertFalse(inbound.ensure_shared_default(self.st))
        self.assertEqual(p.read_bytes(),before)
        self.assertFalse(inbound.path().exists())
        self.assertEqual(doctor.check_shared_inbound(self.st)['status'],'fail')
        self.assertIn('managed',inbound.diagnostic(self.st))
        self.assertIn('refuse',inbound.diagnostic(self.st,'en'))
    def test_doctor_repairs_hold_and_exposes_session_cache_limit(self):
        inbound.path().parent.mkdir(exist_ok=True)
        inbound.path().write_text('{"crossSessionInbound":"hold"}')
        row=doctor.check_shared_inbound(self.st)
        self.assertEqual(row['status'],'ok');self.assertEqual(inbound.value(),'accept')
        self.assertIn('/clear',str(row));self.assertIn('CLI',str(row))
    def test_managed_accept_no_writable_override(self):
        (self.home/'managed.json').write_text('{"crossSessionInbound":"accept"}')
        inbound.set_enabled(False)
        self.assertFalse(inbound.ensure_shared_default(self.st));self.assertEqual(inbound.effective(str(self.home))[2],'accept')
        self.assertEqual(inbound.effective(str(self.home))[::2],('managed','accept'))
    def test_invalid_local_json_not_overwritten_by_doctor(self):
        p=self.home/'.claude/settings.local.json';p.parent.mkdir();p.write_text('bad-private-body')
        row=doctor.check_shared_inbound(self.st)
        self.assertEqual(row['status'],'fail');self.assertEqual(p.read_text(),'bad-private-body')
        self.assertNotIn('bad-private-body',str(row))

    def test_nested_git_uses_root_local_and_cwd_project(self):
        import subprocess
        repo=self.home/'repo';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True)
        cwd=repo/'src';cwd.mkdir();(cwd/'.claude').mkdir()
        (cwd/'.claude/settings.local.json').write_text('{"crossSessionInbound":"refuse"}')
        (cwd/'.claude/settings.json').write_text('{"crossSessionInbound":"hold"}')
        cfg={'kind':'claude','session_mode':'shared','dir':str(cwd)}
        self.assertTrue(inbound.set_enabled(True,cfg['dir']))
        self.assertEqual(inbound.effective(cwd)[::2],('local','accept'))
        self.assertEqual(json.loads((repo/'.claude/settings.local.json').read_text())['crossSessionInbound'],'accept')
        self.assertEqual(json.loads((cwd/'.claude/settings.local.json').read_text())['crossSessionInbound'],'refuse')
    def test_worktree_root_local_shadows_legacy_local(self):
        import subprocess
        repo=self.home/'repo';repo.mkdir();subprocess.run(['git','init','-q',str(repo)],check=True)
        subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-qm','fixture'],check=True)
        wt=self.home/'wt';subprocess.run(['git','-C',str(repo),'worktree','add','-qb','fixture-wt',str(wt)],check=True,stdout=subprocess.DEVNULL)
        (repo/'.claude').mkdir();root=repo/'.claude/settings.local.json';root.write_text('{"crossSessionInbound":"hold"}')
        (wt/'.claude').mkdir();old=wt/'.claude/settings.local.json';old.write_text('{"crossSessionInbound":"accept"}')
        self.assertEqual(inbound.effective(wt)[::2],('local','hold'))
        cfg={'kind':'claude','session_mode':'shared','dir':str(wt)}
        self.assertTrue(inbound.set_enabled(True,cfg['dir']));self.assertEqual(inbound.effective(wt)[::2],('local','accept'))
        self.assertEqual(old.read_text(),'{"crossSessionInbound":"accept"}')
