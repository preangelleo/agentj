"""Actual CLI -> Host paired UV -> typed owner skill link -> existing global discovery."""
import _hermetic  # noqa
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from test_p0308_local_record import Record as BaseRecord

class Relink(BaseRecord):
    # Reuse fixture helpers only; do not rerun already-qualified Record methods.
    def setUp(self):
        super().setUp();self.env.update(CODEX_HOME=str(self.root/'codex'),CLAUDE_CONFIG_DIR=str(self.root/'claude'),
                                      XDG_STATE_HOME=str(self.root/'xdg'),XDG_DATA_HOME=str(self.root/'data'))
        (self.root/'.agents/skills').mkdir(parents=True)
        (self.root/'feature/SKILL.md').write_text('---\nname: owner-tool\ndescription: Owner test skill\n---\n# Owner tool\nInspect the requested local text.\n')
    def value(self):
        return {'kind':'feature','source':str(self.root/'feature'),'entry_kind':'harness_skill','harness':'codex','skill_name':'owner-tool',
                'description':'Owner skill','validation':'Check discovery; functionality still needs owner verification','rollback':'Remove only the exact approved link after inspection'}
    def start(self,mode='allow'):
        from unittest.mock import patch
        helper=Path(__file__).with_name('_p0308_local_relink_server.py')
        with patch('test_p0308_local_record.HERE',helper.parent):
            self._start_custom(mode,helper)
    def _start_custom(self,mode,helper):
        import subprocess
        self.server=subprocess.Popen([sys.executable,str(helper),str(self.root),mode],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop)
        while True:
            line=self.server.stdout.readline()
            if line.strip()=='READY':break
            if not line:self.fail(self.server.stderr.read())
    def relink(self,tty=True):
        import fcntl,pty,subprocess,termios
        master,slave=pty.openpty()
        def terminal():os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0)
        try:
            p=subprocess.run([sys.executable,'-m','agentj.cli','local','relink','f1','--json'],env=self.env,
                stdin=slave if tty else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                preexec_fn=terminal if tty else None,timeout=12)
            return p,json.loads(p.stdout) if p.stdout.strip() else {}
        finally:os.close(master);os.close(slave)
    def register(self):
        p,out=self.cli(self.value());self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(out['id'],'f1');(self.root/'arm-relink').touch()
    def test_typed_record_then_uv_link_and_existing_global_discovery(self):
        self.start();self.register();p,out=self.relink();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(out['status'],'linked');self.assertFalse(out['functional_verified'])
        target=self.root/'.agents/skills/owner-tool';self.assertTrue(target.is_symlink());self.assertEqual(target.resolve(),self.root/'feature')
        from unittest.mock import patch
        from agentj import workflows
        with patch.dict(os.environ,{'HOME':str(self.root),'CODEX_HOME':str(self.root/'codex')}):
            found=next(s for s in workflows.common_skills('codex')[0] if s['name']=='owner-tool')
        self.assertEqual(found['scope'],'global')
        count=len(json.loads((self.root/'cards.json').read_text()));old=target.lstat().st_ino
        p,out=self.relink();self.assertEqual(out['status'],'already_present');self.assertFalse(out['modified'])
        self.assertEqual(target.lstat().st_ino,old);self.assertEqual(len(json.loads((self.root/'cards.json').read_text())),count)
        receipts=list((self.root/'state').glob('local-relink-*.json'));self.assertEqual(len(receipts),2)
        from agentj.state import State
        from agentj import local_relink,wire
        states=[]
        for file in receipts:
            envelope=json.loads(file.read_text());body=wire.unb64u(envelope['payload'])
            State(self.root/'state').signing_key().public_key().verify(wire.unb64u(envelope['signature']),local_relink.DOMAIN+body)
            states.append(json.loads(body)['status'])
        self.assertEqual(set(states),{'prepared','linked'})
    def test_legacy_arbitrary_package_entry_is_not_relinked(self):
        self.start();v={'kind':'feature','source':str(self.root/'feature'),'entry':'agentj/passkey.py','description':'Legacy',
                       'validation':'Inspect','rollback':'Inspect'}
        p,out=self.cli(v);self.assertEqual(p.returncode,0,p.stdout+p.stderr);(self.root/'arm-relink').touch()
        p,out=self.relink();self.assertNotEqual(p.returncode,0);self.assertEqual(out['error'],'legacy_entry_not_relinkable')
        self.assertFalse((self.root/'install/agentj').exists())

    def reset_record(self):
        self.stop()
        for path in (self.root/'state',self.root/'.agentj-local'):
            if path.exists():shutil.rmtree(path)
        for leaf in ('cards.json','arm-relink'):
            if (self.root/leaf).exists():(self.root/leaf).unlink()
        (self.root/'feature/SKILL.md').write_text('---\nname: owner-tool\ndescription: Owner skill\n---\n# Owner tool\nInspect local text.\n')

    def test_denied_invalid_uv_native_ceiling_or_finished_execution_never_link(self):
        for mode in ('deny','no_uv','uv_bit_clear','unsigned','revoke','wrong_hash','timeout','workspace','read_only','unknown','drop_write','execution_done','changed_package','package_root_swap'):
            with self.subTest(mode=mode):
                self.reset_record()
                try:
                    self.start();self.register();self.stop();self.start(mode)
                    p,out=self.relink();self.assertNotEqual(p.returncode,0,p.stdout+p.stderr)
                    self.assertFalse(os.path.lexists(self.root/'.agents/skills/owner-tool'))
                finally:self.stop()

    def test_source_rotation_registry_change_and_incomplete_scan_have_zero_link_effect(self):
        for mode in ('change_source','source_same_bytes_inode_swap','known_secret','incomplete','new_history'):
            with self.subTest(mode=mode):
                self.reset_record()
                try:
                    (self.root/'feature/run.py').write_text('value = "recorded_fixture_value_314159_local_only"\n')
                    self.start();self.register();self.stop();self.start(mode)
                    p,out=self.relink();self.assertNotEqual(p.returncode,0,p.stdout+p.stderr)
                    self.assertFalse(os.path.lexists(self.root/'.agents/skills/owner-tool'))
                finally:self.stop()

    def test_plaza_active_history_publisher_and_corrupt_state_cannot_be_replaced(self):
        self.start();self.register()
        place=self.root/'state/plaza';place.mkdir(mode=0o700)
        for leaf,body in (('installed.json',{'items':[{'name':'different-package','target':str(self.root/'.agents/skills/owner-tool'),'certified':False}]}),
                          ('installed.json',{'items':[],'uninstalled':[{'name':'owner-tool','target':'/old/slot','certified':True,'status':'uninstalled'}]}),
                          ('publishers.json',{'items':{'owner-tool':{'fp':None}}}),('installed.json',{'items':'unknown'})):
            with self.subTest(leaf=leaf,body=body):
                path=place/leaf;path.write_text(json.dumps(body));path.chmod(0o600);before=path.read_bytes()
                p,out=self.relink();self.assertNotEqual(p.returncode,0);self.assertEqual(path.read_bytes(),before)
                self.assertFalse(os.path.lexists(self.root/'.agents/skills/owner-tool'));path.unlink()
        self.assertEqual(len(json.loads((self.root/'cards.json').read_text())),1)

    def test_current_skill_alias_and_unknown_slot_occupancy_refuse_before_card(self):
        self.start();self.register();target=self.root/'.agents/skills/owner-tool'
        other=self.root/'.agents/skills/other-tool';other.mkdir();(other/'SKILL.md').write_text('---\nname: other-tool\naliases: [owner-tool]\n---\nBody\n')
        p,out=self.relink();self.assertNotEqual(p.returncode,0);self.assertEqual(out['error'],'registered_skill_conflict');shutil.rmtree(other)
        target.write_text('unknown marker');p,out=self.relink();self.assertNotEqual(p.returncode,0);self.assertEqual(target.read_text(),'unknown marker');target.unlink()
        target.symlink_to(self.root/'work',target_is_directory=True);p,out=self.relink();self.assertNotEqual(p.returncode,0);self.assertTrue(target.is_symlink())
        self.assertEqual(len(json.loads((self.root/'cards.json').read_text())),1)

    def test_metadata_reserved_name_alias_and_changed_inventory_are_not_restored(self):
        self.start()
        for change in ({'skill_name':'agentj-manual'},{'harness':'claude'},{'entry_kind':'arbitrary'}):
            value=self.value();value.update(change);p,out=self.cli(value);self.assertNotEqual(p.returncode,0)
        (self.root/'feature/SKILL.md').write_text('---\nname: owner-tool\naliases: [agentj-manual]\n---\nBody\n')
        p,out=self.cli(self.value());self.assertNotEqual(p.returncode,0);self.assertFalse((self.root/'.agentj-local').exists())
        self.assertFalse((self.root/'cards.json').exists())

    def test_post_link_slot_swap_keeps_unknown_data_and_reports_recovery(self):
        self.start();self.register();self.stop();self.start('link_swap')
        p,out=self.relink();self.assertNotEqual(p.returncode,0);self.assertEqual(out['error'],'relink_recovery_required')
        target=self.root/'.agents/skills/owner-tool';self.assertEqual(target.read_text(),'unknown slot marker')
        self.assertTrue((target.parent/'retained-approved-link').is_symlink())
        self.assertEqual(len(list((self.root/'state').glob('local-relink-*.json'))),1)
        self.stop();target.rename(self.root/'retained-unknown-slot');(target.parent/'retained-approved-link').rename(self.root/'retained-approved-link')
        self.start('deny');p,out=self.relink();self.assertNotEqual(p.returncode,0)
        self.assertFalse(os.path.lexists(target),'old signed prepared journal must not authorize a retry')
        self.assertEqual((self.root/'retained-unknown-slot').read_text(),'unknown slot marker')
        self.assertEqual(len(list((self.root/'state').glob('local-relink-*.json'))),1)

    def test_same_byte_source_file_replacement_is_not_reapproved(self):
        self.start();self.register();path=self.root/'feature/run.py';raw=path.read_bytes()
        path.rename(self.root/'old-source');path.write_bytes(raw)
        p,out=self.relink();self.assertNotEqual(p.returncode,0);self.assertEqual(out['error'],'source_changed')
        self.assertFalse(os.path.lexists(self.root/'.agents/skills/owner-tool'))

    def test_slot_root_swap_and_unverified_cli_never_write_new_target(self):
        self.start();self.register();p,out=self.relink(tty=False);self.assertNotEqual(p.returncode,0)
        self.stop();self.start('slot_root_swap');p,out=self.relink();self.assertNotEqual(p.returncode,0)
        self.assertFalse(os.path.lexists(self.root/'feature/owner-tool'))
        self.assertFalse(os.path.lexists(self.root/'.agents/retained-skills/owner-tool'))

    def test_typed_record_rejects_source_symlink_hardlink_and_unsafe_directory(self):
        self.start();path=self.root/'feature/run.py';original=path.read_bytes()
        path.unlink();path.symlink_to(self.root/'baseline/example.py')
        p,out=self.cli(self.value());self.assertNotEqual(p.returncode,0);path.unlink()
        os.link(self.root/'baseline/example.py',path)
        p,out=self.cli(self.value());self.assertNotEqual(p.returncode,0);path.unlink();path.write_bytes(original)
        nested=self.root/'feature/subdir';nested.mkdir(mode=0o777);nested.chmod(0o777);(nested/'x.py').write_text('value = 1\n')
        p,out=self.cli(self.value());self.assertNotEqual(p.returncode,0)
        self.assertFalse((self.root/'.agentj-local').exists());self.assertFalse((self.root/'cards.json').exists())

# Inheritance above is for helper implementation only. Keep only the newly declared
# methods so unittest never reruns the previous task's PASS methods.
for _name in tuple(n for n in dir(BaseRecord) if n.startswith('test_')):
    setattr(Relink,_name,None)

del BaseRecord
if __name__=='__main__':unittest.main()
