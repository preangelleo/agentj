"""Host-owned frozen report jobs work on disk TMPDIR without extending public path authority."""
import _hermetic  # noqa: F401
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agentj import report_host as h, report_local as local

class FrozenJob(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='agentj-report-host-',dir='/var/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.files={'input-data/data.json':b'{"synthetic":true}', 'input-template/nested/template.json':b'{}'}
        for name,data in self.files.items():
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        self.job=h._FrozenJob(self.root,self.files)

    def test_private_disk_job_preserves_public_path_refusal(self):
        self.job.verify(self.root/'input-data/data.json')
        self.job.verify(self.root/'input-template/nested')
        with self.assertRaises(ValueError):local.safe_path(str(self.root/'input-data/data.json'))
        for value in ('/etc/passwd', self.root/'../outside', self.root/'input-data/unfrozen.json'):
            with self.subTest(value=str(value)),self.assertRaises(h.LocalFailure):self.job.verify(value)

    def test_root_permissions_and_identity_cannot_change(self):
        self.root.chmod(0o755)
        with self.assertRaises(h.LocalFailure):self.job.verify()
        self.root.chmod(0o700)
        saved=self.root.with_name(self.root.name+'-saved')
        self.root.rename(saved);self.root.mkdir(mode=0o700)
        try:
            with self.assertRaises(h.LocalFailure):self.job.verify()
        finally:
            self.root.rmdir();saved.rename(self.root)

    def test_replaced_owner_and_root_symlink_are_refused(self):
        fields=list(self.root.stat());fields[4]=os.getuid()+1
        with patch.object(h.os,'fstat',return_value=os.stat_result(fields)):
            with self.assertRaises(h.LocalFailure):self.job.verify()
        saved=self.root.with_name(self.root.name+'-saved')
        self.root.rename(saved);self.root.symlink_to(saved,target_is_directory=True)
        try:
            with self.assertRaises(OSError):self.job.verify()
        finally:
            self.root.unlink();saved.rename(self.root)

    def test_input_links_truncation_and_drift_cannot_change_frozen_bytes(self):
        p=self.root/'input-data/data.json'
        for data in (b'',b'{"synthetic":false}'):
            p.write_bytes(data)
            with self.assertRaises(h.LocalFailure):self.job.verify()
        p.write_bytes(self.files['input-data/data.json']);p.unlink()
        p.symlink_to(self.root/'input-template/nested/template.json')
        with self.assertRaises(h.LocalFailure):self.job.verify()

    def test_unfrozen_files_and_directory_links_are_refused(self):
        extra=self.root/'input-data/extra';extra.write_text('not frozen')
        with self.assertRaises(h.LocalFailure):self.job.verify()
        extra.unlink()
        (self.root/'input-template/nested/template.json').unlink()
        (self.root/'input-template/nested').rmdir()
        (self.root/'input-template/nested').symlink_to(self.root/'input-data',target_is_directory=True)
        with self.assertRaises(h.LocalFailure):self.job.verify()

    def test_special_entry_is_not_an_unlisted_input(self):
        os.mkfifo(self.root/'input-data/unlisted.json')
        with self.assertRaises(h.LocalFailure):self.job.verify()

    def test_capability_is_bound_to_root_and_not_json_or_environment(self):
        with tempfile.TemporaryDirectory(dir='/var/tmp') as other,patch.object(local,'digest',return_value=local.TRUSTED_SHA256),patch.object(h,'bounded_run',side_effect=AssertionError('must not execute')):
            for job,root in ((self.job,Path(other)),({'root':str(self.root)},self.root)):
                with self.subTest(root=str(root)),self.assertRaises(h.LocalFailure):
                    local.run(Path(other),['templates'],['templates'],root,capture=True,_host=True,_job=job)
        with patch.dict(os.environ,{'AGENT_WORKSPACE':'/etc','TMPDIR':'/var/tmp'}):
            with self.assertRaises(h.LocalFailure):self.job.verify('/etc/passwd')
        req={'t':'report_local','args':['templates'],'files':{},'_job':str(self.root)}
        self.assertEqual(h.execute(req)['code'],'shape')

    def test_export_is_private_exclusive_and_cannot_escape(self):
        with self.assertRaises(h.LocalFailure):self.job.export(self.root/'../outside.html',b'no')
        p=self.job.export(self.root/'email.html',b'synthetic output')
        self.assertEqual(p.read_bytes(),b'synthetic output')
        self.assertEqual(p.stat().st_mode & 0o777,0o600)
        with self.assertRaises(FileExistsError):self.job.export(p,b'overwrite')
        self.assertEqual(p.read_bytes(),b'synthetic output')
