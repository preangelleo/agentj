"""Actual fresh migration bookkeeping is a one-process, one-use setup handoff."""
import _hermetic
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agentj import migrate, preferences, working_root
from agentj.state import State, default_state, legacy_state


class FreshMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fresh-migration-'); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name); self.work = self.home/'work'
        env = {k:v for k,v in os.environ.items() if not k.startswith(('AGENTJ_','AGENTJARVIS_'))}
        env.update(HOME=str(self.home),XDG_CONFIG_HOME=str(self.home/'config'),
                   AGENTJ_TEST='1',AGENTJ_SKILL_LINK='off',AGENTJ_HARNESS_PREPARE='off',
                   AGENTJ_GOOGLE_PREINSTALL='off',AGENTJ_BROWSER_DETECT='off',AGENTJ_UPGRADE_REPORT='off',
                   AGENTJ_REACH_PROBE='off',PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        self.env = env
        scope = patch.dict(os.environ,env,clear=True); scope.start(); self.addCleanup(scope.stop)
        self.old = State(legacy_state()); self.old.init(relay='ws://127.0.0.1:1')
        self.old.add_device(b'\x01'*32,'synthetic old device',sign_pub=b'\x02'*32)
        self.new = default_state(); self.new.parent.mkdir(parents=True,exist_ok=True)
        preferences.ensure()

    def cli(self,*args):
        return subprocess.run([sys.executable,'-m','agentj.cli',*args],env=self.env,
                              capture_output=True,text=True,timeout=15)

    def fresh(self,path=None):
        target=path or self.new
        self.assertEqual(migrate._fresh(self.old.root,target,out=io.StringIO()),'fresh')
        return State(target)

    def record_refused(self,st,root=None):
        before=preferences.path().read_bytes()
        with self.assertRaises(preferences.ConfigError):working_root.record(st,root or self.work)
        self.assertEqual(preferences.path().read_bytes(),before)

    def legacy_snapshot(self):
        return {str(p.relative_to(self.old.root)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.old.root.rglob('*') if p.is_file()}

    def test_actual_cli_fresh_init_preserves_old_bytes_and_sets_root_once(self):
        before=self.legacy_snapshot()
        result=self.cli('--fresh','init','--relay','ws://127.0.0.1:1','--working-root',str(self.work))
        self.assertEqual(result.returncode,0,result.stderr)
        new=State(self.new)
        self.assertEqual(new.devices(),{})
        self.assertNotEqual(new.signing_pub(),self.old.signing_pub())
        self.assertEqual(self.legacy_snapshot(),before)
        self.assertEqual(migrate.read_record(new)['legacy_left'],str(self.old.root))
        self.assertEqual(preferences.get(preferences.effective(new),'agent.working_root'),str(self.work))
        identity=new.signing_pub(); raw=preferences.path().read_bytes()
        second=self.cli('init','--working-root',str(self.home/'different'))
        self.assertNotEqual(second.returncode,0)
        self.assertIn('fresh paired-device UV',second.stderr)
        self.assertEqual(new.signing_pub(),identity)
        self.assertEqual(preferences.path().read_bytes(),raw)
        self.assertEqual(self.legacy_snapshot(),before)

    def test_actual_cli_forged_marker_and_other_process_do_not_gain_handoff(self):
        for mode in ('forged','parent_created'):
            with self.subTest(mode=mode):
                if self.new.exists():shutil.rmtree(self.new)
                if mode=='forged':
                    self.new.mkdir();(self.new/'migrated.json').write_text(json.dumps({'v':1,'legacy_left':str(self.old.root)}))
                else:self.fresh()
                before=preferences.path().read_bytes()
                result=self.cli('init','--relay','ws://127.0.0.1:1','--working-root',str(self.work))
                self.assertNotEqual(result.returncode,0,result.stderr)
                self.assertIn('fresh paired-device UV',result.stderr)
                self.assertEqual(preferences.path().read_bytes(),before)

    def test_in_process_success_consumes_handoff_and_final_selected_root_receipt(self):
        st=self.fresh(); marker=st.migrated_path.read_bytes()
        st.init(_setup_root=self.work)
        self.assertIsNotNone(st._initial_root_setup)
        self.assertNotIn(os.path.abspath(st.root),migrate._fresh_setups)
        working_root.record(st,self.work)
        self.assertIsNone(st._initial_root_setup)
        self.assertEqual(st.migrated_path.read_bytes(),marker)
        self.record_refused(st,self.home/'other')

    def test_tampered_missing_extra_copy_or_replaced_state_never_mints_setup(self):
        for mode in ('missing_marker','marker_bytes','same_marker_new_inode','extra','new_directory_inode','symlink','mode','copied_state'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir=self.home)as tmp:
                parent=Path(tmp); selected=parent/'work';st=self.fresh(parent/'state')
                if mode=='missing_marker':st.migrated_path.unlink()
                elif mode=='marker_bytes':st.migrated_path.write_bytes(st.migrated_path.read_bytes()+b' ')
                elif mode=='same_marker_new_inode':
                    data=st.migrated_path.read_bytes();st.migrated_path.rename(parent/'old-marker');st.migrated_path.write_bytes(data)
                elif mode=='extra':(st.root/'history.json').write_text('{}')
                elif mode in ('new_directory_inode','symlink'):
                    previous=parent/'original';st.root.rename(previous)
                    if mode=='symlink':st.root.symlink_to(previous,target_is_directory=True)
                    else:shutil.copytree(previous,st.root)
                elif mode=='mode':st.root.chmod(0o777)
                elif mode=='copied_state':
                    copied=parent/'copy';shutil.copytree(st.root,copied);st=State(copied)
                st.init(_setup_root=selected)
                self.assertIsNone(st._initial_root_setup)
                self.record_refused(st,selected)

    def test_file_exists_fresh_branch_cannot_renew_private_handoff(self):
        st=self.fresh()
        migrate._fresh(self.old.root,st.root,out=io.StringIO())
        st.init(_setup_root=self.work)
        self.assertIsNone(st._initial_root_setup)
        self.record_refused(st)

    def test_directory_replaced_during_marker_write_cannot_mint_handoff(self):
        original = migrate._write_record
        def replace(st, record):
            prior = self.home/'replaced-before-marker'
            st.root.rename(prior)
            st.root.mkdir(mode=0o700)
            original(st, record)
        with patch.object(migrate, '_write_record', side_effect=replace):
            st = self.fresh()
        st.init(_setup_root=self.work)
        self.assertIsNone(st._initial_root_setup)
        self.record_refused(st)

    def test_force_and_preexisting_pairing_do_not_gain_setup(self):
        for mode in ('force','paired'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir=self.home)as d:
                st=self.fresh(Path(d)/'state')
                if mode=='paired':
                    st.init();st.add_device(b'\x03'*32,'new paired device')
                st.init(force=True,_setup_root=Path(d)/'work')
                self.assertIsNone(st._initial_root_setup)
                self.record_refused(st,Path(d)/'work')

    def test_actual_cli_force_on_existing_paired_new_identity_has_no_root_receipt(self):
        new=State(self.new);new.init();new.add_device(b'\x03'*32,'already paired')
        before=preferences.path().read_bytes()
        result=self.cli('--fresh','init','--force','--working-root',str(self.work))
        self.assertNotEqual(result.returncode,0)
        self.assertIn('fresh paired-device UV',result.stderr)
        self.assertEqual(preferences.path().read_bytes(),before)

    def test_selected_root_change_consumes_final_receipt(self):
        st=self.fresh();st.init(_setup_root=self.work)
        self.record_refused(st,self.home/'wrong')
        self.record_refused(st,self.work)

    def test_late_same_identity_directory_symlink_cannot_reuse_final_receipt(self):
        st=self.fresh();st.init(_setup_root=self.work)
        self.assertIsNotNone(st._initial_root_setup)
        identity=st.signing_pub()
        prior=self.home/'same-identity-at-different-directory'
        st.root.rename(prior)
        st.root.symlink_to(prior,target_is_directory=True)
        self.assertEqual(st.signing_pub(),identity)
        self.record_refused(st,self.work)
        self.assertIsNone(st._initial_root_setup)

    def test_existing_global_security_root_is_never_overwritten(self):
        prior=self.home/'prior'
        preferences.path().write_text(preferences.edit(preferences.read()[0],'agent.working_root',str(prior)))
        st=self.fresh();st.init(_setup_root=self.work)
        self.record_refused(st)
        self.assertEqual(preferences.get(preferences.read()[1],'agent.working_root'),str(prior))

    def test_mutation_after_consume_before_identity_completion_invalidates_handoff(self):
        for mode in ('extra','marker','replace_directory'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir=self.home)as d:
                st=self.fresh(Path(d)/'state');original=st.log
                def log(*a,**kw):
                    original(*a,**kw)
                    if mode=='extra':(st.root/'history.json').write_text('{}')
                    elif mode=='marker':st.migrated_path.write_bytes(st.migrated_path.read_bytes()+b' ')
                    else:
                        prior=Path(d)/'prior';st.root.rename(prior);shutil.copytree(prior,st.root)
                with patch.object(st,'log',side_effect=log):st.init(_setup_root=Path(d)/'work')
                self.assertIsNone(st._initial_root_setup)
                self.record_refused(st,Path(d)/'work')
