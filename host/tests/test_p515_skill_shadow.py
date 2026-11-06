"""Bundled logic updates automatically; user configuration never moves."""
import _hermetic
import contextlib, hashlib, io, json, os, shutil, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from agentj import personalize as p, skill_links as sl, doctor
FIXTURES=Path(__file__).with_name('fixtures')/'skill_shadow'
NAME='agentj-config'

class SkillShadow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'home';self.home.mkdir()
        self.target=self.home/p.HARNESS_DIRS[0]/NAME;self.target.parent.mkdir(parents=True)
        self.source=p.SKILLS/NAME
    def copy(self):shutil.copytree(self.source,self.target)
    def backup(self):return Path(sl._read(self.target.parent)[NAME]['backup'])
    def test_frozen_37_builtins_no_additions(self):
        expected=json.loads(p.SKILLS.with_name('bundled_skills.json').read_text())['skills']
        self.assertEqual(len(expected),37)
        self.assertEqual(sorted(expected),sorted(x.name for x in p.SKILLS.iterdir() if x.is_dir()))
        self.assertEqual(set(x.name for x in p.SKILLS.iterdir()),set(expected)|{'aliases.json'})
        inventory=json.loads(p.SKILLS.with_name('bundled_skills.json').read_text())['logic_files']
        for name in expected:
            self.assertEqual(inventory[name],sorted(str(x.relative_to(p.SKILLS/name)) for x in (p.SKILLS/name).rglob('*') if x.is_file() and x.suffix in sl.LOGIC_SUFFIXES))
    def test_real_released_copies_across_four_harnesses(self):
        meta=json.loads((FIXTURES/'sources.json').read_text())
        self.assertEqual(hashlib.sha256((FIXTURES/'releases.zip').read_bytes()).hexdigest(),meta.pop('zip_sha256'))
        with zipfile.ZipFile(FIXTURES/'releases.zip') as z:z.extractall(Path(self.tmp.name)/'old')
        for tag in meta:
            with self.subTest(tag=tag):
                home=self.home/tag;old=Path(self.tmp.name)/'old'/tag/NAME
                for h in p.HARNESS_DIRS:shutil.copytree(old,home/h/NAME)
                p.ensure(home,force=True)
                for h in p.HARNESS_DIRS:
                    target=home/h/NAME
                    self.assertEqual(sl.classify(NAME,self.source,target)[0],'installed')
                    backup=Path(sl._read(target.parent)[NAME]['backup'])
                    self.assertEqual((backup/'SKILL.md').read_bytes(),(old/'SKILL.md').read_bytes())
                self.assertEqual(p.ensure(home,force=True),{'linked':0,'conflicts':0})
    def test_modified_logic_backup_and_config_same_inode_mode_mtime(self):
        self.copy();(self.target/'SKILL.md').write_text('Owner logic')
        for rel in ('config.json5','data/nested.csv','personal.md'):
            f=self.target/rel;f.parent.mkdir(exist_ok=True);f.write_text('User preferences');f.chmod(0o600)
        files=[self.target/x for x in ('config.json5','data/nested.csv','personal.md')]
        old=[(f.read_bytes(),f.stat()) for f in files]
        p.ensure(self.home,force=True)
        self.assertEqual((self.target/'SKILL.md').read_bytes(),(self.source/'SKILL.md').read_bytes())
        self.assertEqual((self.backup()/'SKILL.md').read_text(),'Owner logic')
        self.assertIn('你改过的旧版本',self.backup().name)
        for f,(data,st) in zip(files,old):
            self.assertEqual(f.read_bytes(),data)
            self.assertEqual((f.stat().st_ino,f.stat().st_mode,f.stat().st_mtime_ns),(st.st_ino,st.st_mode,st.st_mtime_ns))
        summary=p.summary(self.home)
        self.assertIn('你改过的 1 个',summary);self.assertIn('用户配置已保留',summary)
        self.assertIn(str(self.backup().parent),summary)
        row=doctor.check_skills(self.home);self.assertFalse(row.get('hint'))
    def test_unchanged_logic_with_extra_config_does_not_claim_modified(self):
        self.copy();(self.target/'config.json').write_text('User')
        p.ensure(self.home,force=True)
        self.assertFalse(sl._read(self.target.parent)[NAME]['modified'])
    def test_unreadable_logic_real_nonroot_file_permissions(self):
        self.copy();old=self.target/'SKILL.md';old.write_text('Unreadable owner logic');old.chmod(0)
        self.addCleanup(lambda: self.backup().joinpath('SKILL.md').chmod(0o600) if self.target.exists() else None)
        # Normal CI is non-root: prove a true EACCES, not a mocked tree hash.
        self.assertNotEqual(os.geteuid(),0)
        with self.assertRaises(PermissionError):old.read_bytes()
        p.ensure(self.home,force=True)
        self.assertEqual(old.read_bytes(),(self.source/'SKILL.md').read_bytes())
        saved=self.backup()/'SKILL.md';self.assertEqual(saved.stat().st_mode&0o777,0)
        self.assertEqual(json.loads(self.backup().with_name(self.backup().name+'.json').read_text())['reason'],'unreadable_or_unverified')
        saved.chmod(0o600);self.assertEqual(saved.read_text(),'Unreadable owner logic')
    def test_packaged_config_collision_preserved_and_missing_seeded(self):
        source=Path(self.tmp.name)/'package';source.mkdir();(source/'SKILL.md').write_text('new')
        (source/'settings.json').write_text('package config');(source/'default.json').write_text('default')
        self.target.mkdir();(self.target/'SKILL.md').write_text('old');(self.target/'settings.json').write_text('User config')
        sl.link(NAME,source,self.target)
        self.assertEqual((self.target/'settings.json').read_text(),'User config')
        self.assertEqual((self.target/'default.json').read_text(),'default')
    def test_external_relative_link_overlay_keeps_config_and_original_untouched(self):
        external=self.target.parent/'external';shutil.copytree(self.source,external)
        (external/'SKILL.md').write_text('external owner logic');(external/'settings.json').write_text('User')
        (external/'relative').symlink_to('settings.json')
        self.target.symlink_to('external');p.ensure(self.home,force=True)
        self.assertEqual((external/'SKILL.md').read_text(),'external owner logic')
        self.assertEqual((self.target/'SKILL.md').read_bytes(),(self.source/'SKILL.md').read_bytes())
        self.assertEqual((self.target/'relative').read_text(),'User')
        self.assertEqual((self.target/'settings.json').read_text(),'User')
        meta=json.loads(self.backup().with_name(self.backup().name+'.json').read_text())
        self.assertEqual(meta['link'],'external')
        self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'installed')
        self.assertEqual(p.ensure(self.home,force=True),{'linked':0,'conflicts':0})
    def test_unknown_dangling_and_loop_links_automatically_replaced(self):
        for value in ('/missing-agentj-package/agentj-config',str(self.target)):
            with self.subTest(value=value):
                self.target.symlink_to(value);p.ensure(self.home,force=True)
                self.assertEqual(self.target.resolve(),self.source.resolve())
                self.assertEqual(os.readlink(self.backup()),value);self.target.unlink()
    def test_no_decision_cli_or_doctor_queue(self):
        self.copy();(self.target/'SKILL.md').write_text('User')
        with patch.object(Path,'home',return_value=self.home):
            for action in ('keep','replace'):
                self.assertEqual(p.command(['skill',action,NAME]),1)
        row=doctor.check_skills(self.home)
        self.assertFalse(row.get('hint'));self.assertNotIn('agentj skill replace',row['summary'])
    def test_directory_denial_records_reason_and_preserves_configuration(self):
        self.copy();(self.target/'config.json').write_text('User');self.target.chmod(0o500)
        try:
            result=p.ensure(self.home,force=True)
            self.assertGreater(result['conflicts'],0)
            self.assertEqual(sl._read(self.target.parent)[NAME]['error'],'directory_permission_denied')
            self.assertEqual((self.target/'config.json').read_text(),'User')
        finally:self.target.chmod(0o700)
    def test_missing_entry_never_overwrites_concurrent_owner(self):
        original=Path.mkdir
        def race(path,*args,**kw):
            if path==self.target and not os.path.lexists(path):path.write_text('Concurrent owner file')
            return original(path,*args,**kw)
        with patch.object(Path,'mkdir',race):p.ensure(self.home,force=True)
        self.assertEqual(self.target.read_text(),'Concurrent owner file')

    def test_installed_layout_startup_without_force(self):
        installed=Path(self.tmp.name)/'site-packages/agentj/skills';shutil.copytree(p.SKILLS,installed);self.copy()
        with patch.object(p,'SKILLS',installed),patch.dict(os.environ,{'AGENTJ_SKILL_LINK':'on'}):p.ensure(self.home)
        self.assertTrue(sl.managed(installed/NAME,self.target))
    def test_replacement_never_overwrites_concurrent_logic(self):
        self.copy();old=self.target/'SKILL.md';old.write_text('Old owner logic');original=Path.symlink_to
        def race(path,*args,**kw):
            if path==old and not os.path.lexists(path):path.write_text('Concurrent owner logic')
            return original(path,*args,**kw)
        with patch.object(Path,'symlink_to',race):p.ensure(self.home,force=True)
        self.assertEqual(old.read_text(),'Concurrent owner logic')
        backups=list((self.target.parent.parent/'.agentj-skill-backups').glob(NAME+'-*/SKILL.md'))
        self.assertTrue(any(x.read_text()=='Old owner logic' for x in backups))
    def test_scan_depth_bounded_without_blocking_logic_update(self):
        self.copy();cursor=self.target
        for _ in range(40):cursor=cursor/'deep';cursor.mkdir()
        self.assertIsNone(sl.fingerprint(self.target));p.ensure(self.home,force=True)
        self.assertTrue(sl.managed(self.source,self.target));self.assertTrue(cursor.is_dir())
    def test_new_install_and_old_root_link_config_survive_package_removal(self):
        for prior_link in (False,True):
            with self.subTest(prior_link=prior_link):
                base=Path(self.tmp.name)/str(prior_link);base.mkdir()
                old=base/'old-package'/NAME;new=base/'new-package'/NAME;shutil.copytree(self.source,old);shutil.copytree(self.source,new)
                target=base/'skills'/NAME;target.parent.mkdir()
                if prior_link:
                    target.symlink_to(old);(old/'config.json').write_text('User config');(old/'personal.md').write_text('Personal instruction');(old/'relative-config').symlink_to('config.json')
                sl.link(NAME,old,target)
                (target/'config.json').write_text('User config');(target/'personal.md').write_text('Personal instruction')
                shutil.rmtree(old)
                sl.link(NAME,new,target)
                self.assertEqual((target/'config.json').read_text(),'User config')
                self.assertEqual((target/'personal.md').read_text(),'Personal instruction')
                if prior_link:self.assertEqual((target/'relative-config').read_text(),'User config')
                self.assertTrue(sl.managed(new,target))
                self.assertFalse(sl.link(NAME,new,target))
    def test_nested_external_directory_is_not_backup_ancestor(self):
        source=Path(self.tmp.name)/'package';(source/'scripts').mkdir(parents=True);(source/'SKILL.md').write_text('new');(source/'scripts/run.py').write_text('new script')
        external=Path(self.tmp.name)/'external';external.mkdir();(external/'run.py').write_text('Original external script');(external/'config.json').write_text('User config')
        self.target.mkdir();(self.target/'SKILL.md').write_text('old');(self.target/'scripts').symlink_to(external)
        sl.link(NAME,source,self.target)
        self.assertEqual((external/'run.py').read_text(),'Original external script')
        self.assertEqual((self.target/'scripts/config.json').read_text(),'User config')
        self.assertEqual((self.target/'scripts/run.py').read_text(),'new script')

    def test_package_root_backup_logic_survives_old_package_deletion(self):
        old=Path(self.tmp.name)/'old'/NAME;shutil.copytree(self.source,old)
        (old/'SKILL.md').write_text('Edited old logic');self.target.symlink_to(old)
        sl.link(NAME,old,self.target)
        meta=json.loads(self.backup().with_name(self.backup().name+'.json').read_text())
        snapshot=Path(meta['logic_backup']);shutil.rmtree(old)
        self.assertEqual((snapshot/'SKILL.md').read_text(),'Edited old logic')

if __name__=='__main__':unittest.main()
