"""Real local CLI inspections never apply a patch or trust an edited manifest as repair proof."""
import _hermetic  # noqa: F401
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HOST = Path(__file__).resolve().parents[1]


class Journey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / '.agentj-local'
        self.env = {**os.environ, 'HOME': str(self.home), 'XDG_CONFIG_HOME': str(self.home/'cfg'),
                    'AGENTJ_STATE_DIR': str(self.home/'state'), 'PYTHONPATH': str(HOST)}

    def run_cli(self):
        p = subprocess.run([sys.executable, '-m', 'agentj.cli', 'local', 'check', '--json'],
                           env=self.env, text=True, capture_output=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr+p.stdout)
        return json.loads(p.stdout)

    def manifest(self, **kw):
        self.root.mkdir(exist_ok=True)
        p = self.root/'MANIFEST.json'
        p.write_text(json.dumps({'schema': 1, 'patches': [], 'features': [], **kw}))
        p.chmod(0o600)
        return p

    def test_no_workspace_is_created_for_a_clean_install(self):
        self.assertEqual(self.run_cli()['status'], 'empty')
        self.assertFalse(self.root.exists())
        self.assertFalse((self.home/'state').exists(), 'read-only command must not migrate/init state')

    def test_patch_remains_unverified_even_if_manifest_claims_official_fix(self):
        p=self.manifest(patches=[{'id':'p1','patch':'patches/p1/fix.patch', 'files':['agentj/example.py'],
                                 'fixed_version':'999.0.0','status':'officially_fixed'}])
        patch=self.root/'patches/p1/fix.patch';patch.parent.mkdir(parents=True)
        patch.write_text('--- a/agentj/example.py\n+++ b/agentj/example.py\n@@ -1 +1 @@\n-old\n+new\n')
        before={x.relative_to(self.root):x.read_bytes() for x in self.root.rglob('*') if x.is_file()}
        r=self.run_cli()
        self.assertEqual(r['patches'][0], {'id':'p1','status':'pending_review','reason':'official_fix_unverified'})
        self.assertEqual(before,{x.relative_to(self.root):x.read_bytes() for x in self.root.rglob('*') if x.is_file()})
        self.assertIn('reapply',r['unavailable'])

    def test_corrupt_and_symlinked_manifest_return_fixed_metadata_only(self):
        p=self.manifest();p.write_text('private text not valid JSON')
        r=self.run_cli();self.assertEqual(r['status'],'invalid_manifest');self.assertNotIn('private',json.dumps(r))
        p.unlink();outside=self.home/'private';outside.write_text('not read')
        p.symlink_to(outside)
        self.assertEqual(self.run_cli()['status'],'unreadable_manifest')
        self.assertEqual(outside.read_text(),'not read')

    def test_patch_traversal_and_symlink_component_are_refused(self):
        self.manifest(patches=[{'id':'p1','patch':'../private','files':['agentj/a.py']},
                               {'id':'p2','patch':'patches/p2/fix.patch','files':['agentj/a.py']}])
        target=self.home/'outside';target.mkdir();(target/'p2').mkdir();(target/'p2/fix.patch').write_text('do not read')
        (self.root/'patches').symlink_to(target,target_is_directory=True)
        rows=self.run_cli()['patches']
        self.assertEqual([x['status'] for x in rows],['conflict','conflict'])

    def test_arbitrary_manifest_text_never_becomes_an_output_identifier(self):
        self.manifest(patches=[{'id':'do-not-echo-this-private-value','patch':'patches/p1/fix.patch','files':['agentj/a.py']}])
        r=self.run_cli()
        self.assertEqual(r['status'],'invalid_manifest')
        self.assertNotIn('private-value',json.dumps(r))

    def test_hardlinked_patch_and_patch_target_escape_are_refused(self):
        self.manifest(patches=[{'id':'p1','patch':'patches/p1/fix.patch','files':['agentj/a.py']},
                               {'id':'p2','patch':'patches/p2/fix.patch','files':['agentj/a.py']}])
        source=self.home/'keep-private';source.write_text('do not read')
        p=self.root/'patches/p1/fix.patch';p.parent.mkdir(parents=True);os.link(source,p)
        q=self.root/'patches/p2/fix.patch';q.parent.mkdir(parents=True)
        q.write_text('--- a/agentj/a.py\n+++ b/../../escape.py\n@@ -1 +1 @@\n-old\n+new\n')
        rows=self.run_cli()['patches']
        self.assertEqual([r['status'] for r in rows],['conflict','conflict'])
        self.assertEqual(source.read_text(),'do not read')

    def test_known_secret_and_fragment_never_count_as_reviewable_patch(self):
        self.manifest(patches=[{'id':'p1','patch':'patches/p1/fix.patch','files':['agentj/a.py']}])
        p=self.root/'patches/p1/fix.patch';p.parent.mkdir(parents=True)
        value=''.join(['fixture','rosepetals','inawindy','garden','onlyfortest'])
        self.env['LOCAL_PATCH_TOKEN']=value
        for content in (value,value[8:24]):
            p.write_text('--- a/agentj/a.py\n+++ b/agentj/a.py\n@@ -1 +1 @@\n-old\n+'+content+'\n')
            result=self.run_cli()
            self.assertEqual(result['patches'][0]['status'],'conflict')
            self.assertEqual(result['patches'][0]['reason'],'unsafe_patch')
            self.assertNotIn(content,json.dumps(result))

    def test_incomplete_secret_index_is_explicit_unknown_not_safe(self):
        from agentj import local_patches
        from agentj.peer_guard import SecretIndex
        self.manifest(patches=[{'id':'p1','patch':'patches/p1/fix.patch','files':['agentj/a.py']}])
        p=self.root/'patches/p1/fix.patch';p.parent.mkdir(parents=True)
        p.write_text('--- a/agentj/a.py\n+++ b/agentj/a.py\n@@ -1 +1 @@\n-old\n+new\n')
        index=SecretIndex();index.complete=False
        with patch.object(SecretIndex,'build',return_value=index):
            r=local_patches.check(self.root)
        self.assertEqual(r['patches'][0]['reason'],'secret_scan_unavailable')

    def test_upgrade_doctor_inspects_without_blocking_or_touching_inventory(self):
        from agentj import update
        self.manifest().write_text('invalid owner manifest; do not echo')
        before=(self.root/'MANIFEST.json').read_bytes()
        proc=subprocess.run([sys.executable,'-m','agentj.cli','doctor','--upgrade-only','--json'],
                            env=self.env,text=True,capture_output=True,timeout=30)
        rows=json.loads(proc.stdout)['checks']
        row=next(r for r in rows if r['id']=='local-patches')
        self.assertEqual(row['status'],'warn')
        self.assertEqual(update.upgrade_blockers([row]),([],[]))
        self.assertNotIn('do not echo',json.dumps(row))
        self.assertEqual(before,(self.root/'MANIFEST.json').read_bytes())

    def test_feature_broken_entry_is_reported_without_relink(self):
        source=self.home/'my-feature';source.mkdir()
        entry=self.home/'release'/'extra.py';entry.parent.mkdir()
        self.manifest(features=[{'id':'f1','path':str(source),'entry':str(entry),'kind':'symlink'}])
        r=self.run_cli();self.assertEqual(r['features'][0]['status'],'broken_link');self.assertFalse(entry.exists())
        entry.symlink_to(source)
        self.assertEqual(self.run_cli()['features'][0]['status'],'present_unverified')
        self.assertTrue(entry.is_symlink())


if __name__ == '__main__': unittest.main()
