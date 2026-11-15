"""Real CLI/UDS/Host/paired-UV private record creation; no installed code writes."""
import _hermetic  # noqa
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import termios
import unittest

HERE=Path(__file__).resolve().parent

class Record(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='aj-local-record-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ('baseline','install','work','feature'):(self.root/name).mkdir()
        (self.root/'baseline/example.py').write_text('value = 1\n')
        (self.root/'install/example.py').write_text('value = 2\n')
        (self.root/'feature/run.py').write_text('print("local feature")\n')
        self.env=os.environ.copy()
        for key in ('CLAUDECODE','CLAUDE_CODE_ENTRYPOINT','CODEX_SANDBOX','CODEX_SANDBOX_NETWORK_DISABLED','OPENCODE','AGENTJ_PERM_TOKEN','AGENTJ_PERM_SOCK','AGENTJ_FENCED'):self.env.pop(key,None)
        self.env.update(HOME=str(self.root),AGENTJ_STATE_DIR=str(self.root/'state'),XDG_CONFIG_HOME=str(self.root/'cfg'),
                        PYTHONPATH=os.pathsep.join((str(HERE.parent),str(HERE))))
        self.server=None

    def value(self):
        return {'kind':'patch','baseline_root':str(self.root/'baseline'),'baseline_sha256':{'example.py':hashlib.sha256(b'value = 1\n').hexdigest()},
                'files':['example.py'],'description':'Change example value','validation':'Review the value in an isolated check',
                'rollback':'Restore recorded baseline after separate approval','father_ticket':'local-123'}

    def start(self, mode='allow'):
        self.server=subprocess.Popen([sys.executable,str(HERE/'_p0308_local_record_server.py'),str(self.root),mode],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop)
        while True:
            line=self.server.stdout.readline()
            if line.strip()=='READY':break
            if not line:self.fail(self.server.stderr.read())

    def stop(self):
        if self.server:
            if self.server.poll() is None:self.server.terminate()
            try:self.server.communicate(timeout=5)
            except subprocess.TimeoutExpired:self.server.kill();self.server.communicate()
            self.server=None

    def cli(self, value, tty=True):
        path=self.root/'request.json';path.write_text(json.dumps(value))
        master,slave=pty.openpty()
        def terminal():os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0)
        try:
            r=subprocess.run([sys.executable,'-m','agentj.cli','local','record','--contract',str(path),'--json'],env=self.env,
                stdin=slave if tty else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                preexec_fn=terminal if tty else None,timeout=12)
            return r, json.loads(r.stdout) if r.stdout.strip() else {}
        finally:os.close(master);os.close(slave)

    def test_real_cli_uv_records_local_baseline_and_does_not_apply(self):
        self.start();r,out=self.cli(self.value());self.assertEqual(r.returncode,0,r.stderr+r.stdout)
        self.assertEqual(out['id'],'p1');root=self.root/'.agentj-local'
        manifest=json.loads((root/'MANIFEST.json').read_text());row=manifest['patches'][0]
        self.assertEqual(row['baseline_origin'],'owner_local_sha256');self.assertNotIn('official_fixed',json.dumps(row))
        self.assertEqual((root/'patches/p1/base/example.py').read_text(),'value = 1\n')
        diff=(root/'patches/p1/fix.patch').read_text();self.assertIn('-value = 1',diff);self.assertIn('+value = 2',diff)
        self.assertEqual((self.root/'install/example.py').read_text(),'value = 2\n')
        self.assertEqual(root.stat().st_mode&0o777,0o700)
        cards=json.loads((self.root/'cards.json').read_text());self.assertEqual(len(cards),1);self.assertTrue(cards[0]['passkey_required'])
        r2,out2=self.cli(self.value());self.assertEqual(r2.returncode,0,r2.stdout+r2.stderr);self.assertEqual(out2['id'],'p2')
        check=subprocess.run([sys.executable,'-m','agentj.cli','local','check','--json'],env=self.env,capture_output=True,text=True)
        self.assertEqual([x['status'] for x in json.loads(check.stdout)['patches']],['pending_review','pending_review'])

    def test_feature_records_exact_external_source_without_relink(self):
        self.start();v={'kind':'feature','source':str(self.root/'feature'),'entry':'extras/tool','description':'Local feature',
                        'validation':'Inspect local tool','rollback':'Remove only the registered entry'}
        r,out=self.cli(v);self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertEqual(out['id'],'f1')
        row=json.loads((self.root/'.agentj-local/MANIFEST.json').read_text())['features'][0]
        self.assertEqual(row['path'],str(self.root/'feature'));self.assertEqual(row['entry'],str(self.root/'install/extras/tool'))
        self.assertFalse((self.root/'install/extras').exists());self.assertEqual((self.root/'feature/run.py').read_text(),'print("local feature")\n')

    def test_no_terminal_source_creates_no_card_or_record(self):
        self.start();r,out=self.cli(self.value(),tty=False);self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'.agentj-local').exists());self.assertFalse((self.root/'cards.json').exists())

    def test_wrong_baseline_or_path_escape_has_zero_writes(self):
        self.start()
        for change in ({'baseline_sha256':{'example.py':'0'*64}},{'files':['../outside']},{'files':['.env']}):
            v=self.value();v.update(change);r,out=self.cli(v);self.assertNotEqual(r.returncode,0)
            self.assertFalse((self.root/'.agentj-local').exists())
        self.assertFalse((self.root/'cards.json').exists())

    def test_denial_invalid_uv_revocation_and_unknown_native_have_zero_record_writes(self):
        for mode in ('deny','no_uv','uv_bit_clear','unsigned','revoke','wrong_hash','timeout','workspace','read_only','unknown','drop_write'):
            with self.subTest(mode=mode):
                self.start(mode);r,out=self.cli(self.value());self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertFalse((self.root/'.agentj-local').exists());self.stop()
                import shutil
                shutil.rmtree(self.root/'state')
                if (self.root/'cards.json').exists():(self.root/'cards.json').unlink()

    def test_changed_file_and_new_secret_during_uv_refuse_without_snapshot(self):
        for mode in ('change_source','known_secret','incomplete'):
            with self.subTest(mode=mode):
                if mode=='known_secret':(self.root/'install/example.py').write_text('value = \"recorded_fixture_value_314159_local_only\"\n')
                self.start(mode);r,out=self.cli(self.value());self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertFalse((self.root/'.agentj-local').exists());self.stop()
                import shutil
                shutil.rmtree(self.root/'state');(self.root/'install/example.py').write_text('value = 2\n')
                if (self.root/'cards.json').exists():(self.root/'cards.json').unlink()

    def test_symbolic_hard_links_and_binary_sources_refuse_before_card(self):
        self.start();path=self.root/'install/example.py';path.unlink();path.symlink_to(self.root/'baseline/example.py')
        r,out=self.cli(self.value());self.assertNotEqual(r.returncode,0);path.unlink();os.link(self.root/'baseline/example.py',path)
        r,out=self.cli(self.value());self.assertNotEqual(r.returncode,0);path.unlink();path.write_bytes(b'private\x00binary')
        r,out=self.cli(self.value());self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'cards.json').exists());self.assertFalse((self.root/'.agentj-local').exists())

    def test_workspace_symlink_swap_during_uv_never_writes_target(self):
        self.start('swap_workspace');before=(self.root/'feature/run.py').read_bytes()
        r,out=self.cli(self.value());self.assertNotEqual(r.returncode,0)
        self.assertEqual((self.root/'feature/run.py').read_bytes(),before)
        self.assertFalse((self.root/'feature/MANIFEST.json').exists())

    def test_published_object_swaps_retain_recovery_data_and_do_not_register(self):
        for mode in ('stage_swap','publish_swap'):
            with self.subTest(mode=mode):
                self.start(mode);r,out=self.cli(self.value());self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
                private=self.root/'.agentj-local'
                self.assertFalse((private/'MANIFEST.json').exists())
                self.assertEqual((private/'patches/p1/unknown.marker').read_text(),'keep unknown data')
                self.assertTrue(list(private.rglob('retained-original')))
                self.assertEqual((self.root/'install/example.py').read_text(),'value = 2\n')
                self.stop()
                import shutil
                shutil.rmtree(self.root/'state');shutil.rmtree(private)
                if (self.root/'cards.json').exists():(self.root/'cards.json').unlink()

    def test_manifest_read_then_name_swaps_are_preserved_without_byte_only_rollback(self):
        for mode in ('manifest_swap','manifest_same_bytes_swap'):
            with self.subTest(mode=mode):
                private=self.root/'.agentj-local';private.mkdir(mode=0o700)
                original=b'{"schema":1,"patches":[],"features":[]}'
                (private/'MANIFEST.json').write_bytes(original);(private/'MANIFEST.json').chmod(0o600)
                self.start(mode);r,out=self.cli(self.value());self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
                self.assertEqual(out['error'],'record_recovery_required')
                proof=json.loads((self.root/'race-proof.json').read_text());unknown=private/proof['path'];info=unknown.stat()
                self.assertEqual([info.st_dev,info.st_ino],proof['inode'])
                self.assertEqual(unknown.read_bytes(),original if proof['same'] else b'unknown recovery marker')
                self.assertEqual((private/'retained-old-manifest').read_bytes(),original)
                self.assertNotEqual((private/'MANIFEST.json').read_bytes(),original,'must not restore an unknown mutable candidate')
                self.stop()
                import shutil
                shutil.rmtree(self.root/'state');shutil.rmtree(private);(self.root/'race-proof.json').unlink()
                if (self.root/'cards.json').exists():(self.root/'cards.json').unlink()

    def test_known_unicode_raw_source_path_and_actual_feature_bytes_never_reach_card(self):
        value='本地秘密路径fixture_value_271828_only_for_test'
        self.env['LOCAL_RECORD_TOKEN']=value
        source=self.root/value;(self.root/'feature').rename(source)
        self.start();contract={'kind':'feature','source':str(source),'entry':'extras/tool','description':'Feature',
                              'validation':'Inspect','rollback':'Remove approved entry'}
        r,out=self.cli(contract);self.assertNotEqual(r.returncode,0);self.assertNotIn(value,r.stdout+r.stderr)
        self.stop()
        import shutil
        shutil.rmtree(self.root/'state');source.rename(self.root/'feature')
        (self.root/'feature/run.py').write_text('message = '+repr(value)+'\n')
        self.start();contract['source']=str(self.root/'feature')
        r,out=self.cli(contract);self.assertNotEqual(r.returncode,0);self.assertNotIn(value,r.stdout+r.stderr)
        self.assertFalse((self.root/'.agentj-local').exists());self.assertFalse((self.root/'cards.json').exists())

    def test_unsupported_platform_refuses_before_any_host_or_filesystem_access(self):
        import asyncio
        from unittest.mock import patch
        from agentj import local_record
        with patch.object(local_record.sys,'platform','darwin'):
            result=asyncio.run(local_record.handle(None,{}))
        self.assertEqual(result,{'ok':False,'error':'record_platform_unsupported'})

if __name__=='__main__':unittest.main()
