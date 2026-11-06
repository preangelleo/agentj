"""Public release fixtures upgrade automatically; edits and unknown origins survive."""
import _hermetic
import contextlib, hashlib, io, json, os, shutil, tempfile, unittest, zipfile
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
    def command(self,*args):
        output=io.StringIO()
        with patch.object(Path,'home',return_value=self.home),contextlib.redirect_stdout(output):
            rc=p.command(['skill',*args])
        return rc,json.loads(output.getvalue())
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
                    self.assertTrue(target.is_symlink());self.assertEqual(target.resolve(),self.source.resolve())
                    backup=next((target.parent.parent/'.agentj-skill-backups').glob(NAME+'-*'))
                    self.assertEqual(sl.fingerprint(backup),sl.fingerprint(old))
                self.assertEqual(p.ensure(home,force=True),{'linked':0,'conflicts':0})
    def test_installed_layout_startup_without_force(self):
        installed=Path(self.tmp.name)/'site-packages/agentj/skills';shutil.copytree(p.SKILLS,installed);self.copy()
        with patch.object(p,'SKILLS',installed),patch.dict(os.environ,{'AGENTJ_SKILL_LINK':'on'}):p.ensure(self.home)
        self.assertEqual(self.target.resolve(),(installed/NAME).resolve())
    def test_modified_extra_empty_and_nested_link_remain(self):
        for kind in ('modified','extra','empty-directory','nested-link'):
            with self.subTest(kind=kind):
                if self.target.exists():shutil.rmtree(self.target)
                self.copy()
                if kind=='modified':(self.target/'SKILL.md').write_text('Owner customization')
                elif kind=='extra':(self.target/'owner.txt').write_text('Owner')
                elif kind=='empty-directory':(self.target/'empty').mkdir()
                else:(self.target/'nested').symlink_to(self.source)
                p.ensure(self.home,force=True)
                self.assertFalse(self.target.is_symlink());self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'conflict')
    def test_unknown_dangling_and_loop_links_remain(self):
        for value in ('/missing-agentj-package/agentj-config',str(self.target)):
            with self.subTest(value=value):
                self.target.symlink_to(value);p.ensure(self.home,force=True)
                self.assertEqual(os.readlink(self.target),value);self.target.unlink()
    def test_relative_old_link_backup_preserves_resolution(self):
        old=self.target.parent/'example-old';shutil.copytree(self.source,old);self.target.symlink_to('example-old')
        p.ensure(self.home,force=True);self.assertEqual(self.target.resolve(),self.source.resolve())
        backup_root=self.target.parent.parent/'.agentj-skill-backups'
        row=json.loads(next(backup_root.glob('*.json')).read_text())
        self.assertEqual((row['path'],row['link']),(str(self.target),'example-old'))
        self.assertEqual(sl.fingerprint(Path(row['resolved_from'])),sl.fingerprint(old))
        self.assertEqual(sl.fingerprint(next(backup_root.glob('*.resolved'))),sl.fingerprint(old))
        self.assertEqual(backup_root.stat().st_mode&0o777,0o700)
    def test_only_receipted_dangling_is_repaired(self):
        old=Path(self.tmp.name)/'old-package'/NAME;shutil.copytree(self.source,old)
        sl.link(NAME,old,self.target);shutil.rmtree(old)
        self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'legacy')
        p.ensure(self.home,force=True);self.assertEqual(self.target.resolve(),self.source.resolve())
    def test_keep_is_content_bound_replace_preserves_backup(self):
        self.copy();custom=self.target/'SKILL.md';custom.write_text('Owner customization')
        self.assertEqual(self.command('keep',NAME)[0],0)
        self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'kept')
        p.ensure(self.home,force=True);self.assertEqual(custom.read_text(),'Owner customization')
        custom.write_text('Owner changed again');self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'conflict')
        self.assertEqual(self.command('replace',NAME)[0],0)
        backup=next((self.target.parent.parent/'.agentj-skill-backups').glob(NAME+'-*'))
        self.assertEqual((backup/'SKILL.md').read_text(),'Owner changed again')
    def test_keep_nested_and_dangling_uses_separate_nonfollowing_fingerprint(self):
        self.copy();nested=self.target/'owner-link';nested.symlink_to('/owner-missing')
        self.assertIsNone(sl.fingerprint(self.target))
        self.assertEqual(self.command('keep',NAME)[0],0)
        p.ensure(self.home,force=True)
        self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'kept')
        nested.unlink();nested.symlink_to('/owner-changed')
        self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'conflict')
        shutil.rmtree(self.target);self.target.symlink_to('/unknown-package/agentj-config')
        self.assertEqual(self.command('keep',NAME)[0],0)
        p.ensure(self.home,force=True);self.assertEqual(os.readlink(self.target),'/unknown-package/agentj-config')
        self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'kept')
        self.target.unlink();self.target.symlink_to(self.target)
        self.assertEqual(self.command('keep',NAME)[0],0)
        self.assertEqual(sl.classify(NAME,self.source,self.target)[0],'kept')
    def test_unreadable_choice_has_no_false_keep_command(self):
        self.copy();(self.target/'SKILL.md').write_text('Owner')
        with patch.object(sl,'fingerprint',return_value=None),patch.object(sl,'choice_fingerprint',return_value=None):
            row=doctor.check_skills(self.home)
        self.assertNotIn('agentj skill keep '+NAME,row['hint'])
        self.assertIn('agentj skill replace '+NAME,row['hint'])
    def test_doctor_executable_choices_and_factual_copy_wording(self):
        self.copy();(self.target/'SKILL.md').write_text('Owner custom');row=doctor.check_skills(self.home)
        self.assertIn('agentj skill replace '+NAME,row['hint']);self.assertIn('agentj skill keep '+NAME,row['hint'])
        self.assertIn('unverified',row['summary']);self.assertNotIn('your own copy',row['summary'])
    def test_missing_and_replaced_entries_never_overwrite_concurrent_owner(self):
        for exists in (False,True):
            with self.subTest(exists=exists):
                if os.path.lexists(self.target):self.target.unlink()
                if exists:self.copy()
                original=Path.symlink_to
                def race(path,*args,**kw):
                    if path==self.target and not os.path.lexists(path):path.write_text('Concurrent owner file')
                    return original(path,*args,**kw)
                with patch.object(Path,'symlink_to',race):p.ensure(self.home,force=True)
                self.assertEqual(self.target.read_text(),'Concurrent owner file')
    def test_migration_rechecks_saved_directory(self):
        self.copy();original=Path.rename
        def race(path,target):
            result=original(path,target)
            if path==self.target:(target/'owner.txt').write_text('Changed during migration')
            return result
        with patch.object(Path,'rename',race):p.ensure(self.home,force=True)
        self.assertEqual((self.target/'owner.txt').read_text(),'Changed during migration')
        self.assertNotEqual(self.target.resolve(),self.source.resolve())
    def test_scan_depth_is_bounded(self):
        self.target.mkdir();cursor=self.target
        for _ in range(40):cursor=cursor/'deep';cursor.mkdir()
        self.assertIsNone(sl.fingerprint(self.target));p.ensure(self.home,force=True)
        self.assertFalse(self.target.is_symlink())

if __name__=='__main__':unittest.main()
