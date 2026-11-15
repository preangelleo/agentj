"""Actual CLI child -> kernel owner-terminal frame -> UDS Host -> signed UV -> atomic files."""
import _hermetic  # noqa
import copy
import fcntl
import json
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import termios
import unittest
from agentj import tasks

HERE=Path(__file__).resolve().parent

def contract(transfer=False):
    task={'v':1,'id':'new-work','title':{'zh':'新任务','en':'New work'},'schedule':'0 8 * * *','tz':'local',
          'prompt_file':'PROMPT.md','dry_run_prompt_file':'PROMPT.md','mode':'normal','enabled':False,'needs':[],
          'outputs':['reports/result.md']}
    value={'task':task,'prompt':'Do the requested work, then verify and report.'}
    if transfer:value['large_transfer']={'kind':'crypto','limit':'1200.50','currency':'USDT'}
    return value

class Create(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='aj-tc-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'outside').mkdir()
        self.env=os.environ.copy()
        for key in ('CLAUDECODE','CLAUDE_CODE_ENTRYPOINT','CODEX_SANDBOX','CODEX_SANDBOX_NETWORK_DISABLED','OPENCODE','AGENTJ_PERM_TOKEN','AGENTJ_PERM_SOCK','AGENTJ_FENCED'):
            self.env.pop(key,None)
        self.env.update(HOME=str(self.root),AGENTJ_STATE_DIR=str(self.root/'state'),XDG_CONFIG_HOME=str(self.root/'config'),
                        XDG_STATE_HOME=str(self.root/'xdg'),XDG_DATA_HOME=str(self.root/'data'),
                        PYTHONPATH=os.pathsep.join((str(HERE.parent),str(HERE))))
        self.server=None

    def start(self,mode='allow'):
        self.server=subprocess.Popen([sys.executable,str(HERE/'_p0322_create_server.py'),str(self.root),mode],env=self.env,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop)
        while True:
            line=self.server.stdout.readline()
            if line.strip()=='READY':break
            if not line:self.fail('fixture server failed: '+self.server.stderr.read())

    def stop(self):
        if self.server and self.server.poll() is None:
            self.server.terminate()
            try:self.server.communicate(timeout=5)
            except subprocess.TimeoutExpired:self.server.kill();self.server.communicate()
        elif self.server:
            self.server.communicate()

    def cli(self,value,tty=True):
        path=self.root/'request.json';path.write_text(json.dumps(value))
        master,slave=pty.openpty()
        def terminal():
            os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0)
        try:
            return subprocess.run([sys.executable,'-m','agentj.cli','tasks','create','--contract',str(path),'--json'],
                stdin=slave if tty else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                env=self.env,preexec_fn=terminal if tty else None,timeout=10)
        finally:os.close(master);os.close(slave)

    def test_ordinary_real_cli_creates_discoverable_task_without_card(self):
        self.start();r=self.cli(contract());self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        row=tasks.find(str(self.root/'work'),'new-work');self.assertFalse(row['problems']);self.assertTrue(row['tsha'])
        self.assertFalse((self.root/'cards.json').exists())
        self.assertEqual(tasks.rows(self.server_state(),str(self.root/'work'))[0]['enabled'],False)

    def server_state(self):
        from agentj.state import State
        return State(self.root/'state')

    def test_transfer_cli_signed_uv_binds_contract_and_writes_informational_limit_once(self):
        self.start();r=self.cli(contract(True));self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        card=json.loads((self.root/'cards.json').read_text())[0]
        self.assertTrue(card['passkey_required']);self.assertIn('1200.50',card['summary'])
        row=tasks.find(str(self.root/'work'),'new-work');self.assertFalse(row['problems'])
        body=(self.root/'work/new-work/PROMPT.md').read_text();self.assertIn('1200.50 USDT',body)
        self.assertIn('not enforced',body)
        self.assertNotEqual(self.cli(contract(True)).returncode,0)
        self.assertEqual(len(json.loads((self.root/'cards.json').read_text())),1)

    def test_denied_missing_uv_unsigned_revoked_wrong_digest_leave_no_task(self):
        for mode in ('deny','no_uv','unsigned','revoke','wrong_hash','timeout'):
            with self.subTest(mode=mode):
                self.start(mode);r=self.cli(contract(True));self.assertNotEqual(r.returncode,0)
                self.assertFalse((self.root/'work/new-work').exists());self.stop()
                # fresh isolated host keys and state for each transport journey
                import shutil
                for name in ('state','work','cards.json'):
                    p=self.root/name
                    if p.is_dir():shutil.rmtree(p)
                    elif p.exists():p.unlink()

    def test_unverified_nonterminal_source_cannot_create_even_ordinary(self):
        self.start();r=self.cli(contract(),tty=False);self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'work/new-work').exists());self.assertFalse((self.root/'cards.json').exists())

    def test_root_symlink_swap_during_uv_never_writes_outside(self):
        self.start('root_swap');r=self.cli(contract(True));self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'outside/new-work').exists());self.assertFalse((self.root/'old-work/new-work').exists())

    def test_invalid_contract_and_existing_destination_are_not_overwritten(self):
        self.start();v=contract(True);v['large_transfer']['limit']='NaN';self.assertNotEqual(self.cli(v).returncode,0)
        v['large_transfer']=None;self.assertNotEqual(self.cli(v).returncode,0)
        dest=self.root/'work/new-work';dest.mkdir();(dest/'owner.txt').write_text('keep')
        self.assertNotEqual(self.cli(contract()).returncode,0)
        self.assertEqual((dest/'owner.txt').read_text(),'keep');self.assertFalse((self.root/'cards.json').exists())

    def test_failed_publish_cleans_only_new_stage_and_crash_never_exposes_active_task(self):
        for mode in ('publish_fail','crash_before_publish'):
            with self.subTest(mode=mode):
                self.start(mode);r=self.cli(contract(True));self.assertNotEqual(r.returncode,0)
                self.assertFalse((self.root/'work/new-work').exists())
                self.assertFalse(tasks.discover(str(self.root/'work')))
                self.stop()
                import shutil
                for name in ('state','work','cards.json'):
                    p=self.root/name
                    if p.is_dir():shutil.rmtree(p)
                    elif p.exists():p.unlink()

    def test_known_secret_and_incomplete_sources_refuse_before_any_card(self):
        self.start();source=self.root/'work/.env'
        source.write_text('CUSTOM_SECRET=fixture_only_transfer_material_0123456789\n')
        v=contract(True);v['prompt']+=' fixture_only_transfer_material_0123456789'
        self.assertNotEqual(self.cli(v).returncode,0)
        self.assertFalse((self.root/'cards.json').exists())
        source.unlink();source.mkdir()
        self.assertNotEqual(self.cli(contract()).returncode,0)
        self.assertFalse((self.root/'work/new-work').exists())

    def test_decoded_special_secret_fragments_and_rendered_declaration_are_blocked(self):
        self.start()
        source=self.root/'work/.env'
        secret='ab"c\\de"f\\gh'
        source.write_text('CUSTOM_SECRET='+secret+'\n')
        for financial in (False,True):
            for text in (secret,secret[:8]):
                for field in ('prompt','nested_title'):
                    with self.subTest(financial=financial,fragment=len(text),field=field):
                        v=contract(financial)
                        v['task']['id']='secret-'+str(int(financial))+'-'+str(len(text))+'-'+field.replace('_','-')
                        if field=='prompt':v['prompt']='Remember '+text
                        else:v['task']['title']['en']='Remember '+text
                        self.assertNotEqual(self.cli(v).returncode,0)
                        self.assertFalse((self.root/'cards.json').exists())
                        self.assertFalse((self.root/'work'/v['task']['id']).exists())
        source.write_text('CUSTOM_SECRET=1200.50USDT\n')
        self.assertNotEqual(self.cli(contract(True)).returncode,0)
        self.assertFalse((self.root/'cards.json').exists())
        self.assertFalse((self.root/'work/new-work').exists())

    def test_rename_boundary_substitution_never_reports_old_contract_success_or_enables(self):
        for mode in ('stage_swap','published_swap'):
            with self.subTest(mode=mode):
                self.start(mode)
                try:
                    r=self.cli(contract(True));self.assertNotEqual(r.returncode,0)
                    self.assertIn('publication_conflict_recovery_required',r.stdout+r.stderr)
                    self.assertEqual((self.root/'work/new-work/unknown.marker').read_text(),'keep')
                    self.assertTrue(all(not row['enabled'] for row in tasks.rows(self.server_state(),str(self.root/'work'))))
                finally:
                    self.stop()
                    import shutil
                    for name in ('state','work','cards.json'):
                        p=self.root/name
                        if p.is_dir():shutil.rmtree(p)
                        elif p.exists():p.unlink()

    def test_later_enable_uses_existing_path_without_reapproving_financial_declaration(self):
        self.start();r=self.cli(contract(True));self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        r=subprocess.run([sys.executable,'-m','agentj.cli','tasks','enable','new-work'],env=self.env,
                         capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertTrue(tasks.rows(self.server_state(),str(self.root/'work'))[0]['enabled'])
        self.assertEqual(len(json.loads((self.root/'cards.json').read_text())),1)

    def test_old_enable_record_cannot_activate_new_same_contract_creation(self):
        for mode in ('allow','armed_stage_swap','armed_published_swap'):
            with self.subTest(mode=mode):
                self.start(mode)
                try:
                    v=contract(True);r=self.cli(v);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                    r=subprocess.run([sys.executable,'-m','agentj.cli','tasks','enable','new-work'],env=self.env,
                                     capture_output=True,text=True,timeout=10)
                    self.assertEqual(r.returncode,0,r.stdout+r.stderr)
                    before=tasks.load(self.server_state())['on']
                    self.assertTrue(before.get('new-work'))
                    old=self.root/'retained-enabled';(self.root/'work/new-work').rename(old)
                    old_bytes=(old/'task.json').read_bytes(),(old/'PROMPT.md').read_bytes()
                    (self.root/'arm-swap').write_text('armed')
                    r=self.cli(v);self.assertNotEqual(r.returncode,0)
                    self.assertIn('task_authorization_exists',r.stdout+r.stderr)
                    self.assertFalse((self.root/'work/new-work').exists())
                    self.assertFalse(any(row['enabled'] for row in tasks.rows(self.server_state(),str(self.root/'work'))))
                    self.assertEqual(tasks.load(self.server_state())['on'],before)
                    self.assertEqual(((old/'task.json').read_bytes(),(old/'PROMPT.md').read_bytes()),old_bytes)
                    self.assertEqual(len(json.loads((self.root/'cards.json').read_text())),1)
                finally:
                    self.stop()
                    import shutil
                    for name in ('state','work','cards.json','arm-swap','retained-enabled'):
                        p=self.root/name
                        if p.is_dir():shutil.rmtree(p)
                        elif p.exists():p.unlink()

    def test_waiting_old_enable_cannot_attach_to_new_same_bytes_directory(self):
        self.start();v=contract();r=self.cli(v);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        import threading
        from unittest.mock import patch
        waiting, release = threading.Event(), threading.Event()
        original_update=tasks.update
        errors=[]
        def paused_update(st, change):
            waiting.set()
            if not release.wait(10):raise RuntimeError('fixture release timeout')
            return original_update(st,change)
        def enable_old():
            try:tasks.set_enabled(self.server_state(),str(self.root/'work'),'new-work',True,'fixture-owner')
            except tasks.TaskError as error:errors.append(error.reason)
        with patch.object(tasks,'update',paused_update):
            thread=threading.Thread(target=enable_old);thread.start()
            try:
                self.assertTrue(waiting.wait(5))
                (self.root/'work/new-work').rename(self.root/'retained-old')
                r=self.cli(v);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            finally:release.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,['changed'])
        self.assertNotIn('new-work',tasks.load(self.server_state())['on'])
        self.assertTrue(all(not row['enabled'] for row in tasks.rows(self.server_state(),str(self.root/'work'))))
        self.assertTrue((self.root/'retained-old/task.json').exists())

    def test_wrong_wire_hash_and_forged_source_field_do_not_create_or_open_card(self):
        self.start()
        from agentj import task_create
        value=contract(True)
        request={'cmd':'task_create','contract':value,'sha256':'0'*64}
        for extra in ({}, {'source':'owner'}):
            request.update(extra)
            path=self.root/'wire.json';path.write_text(json.dumps(request))
            master,slave=pty.openpty()
            def terminal():
                os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0)
            code='import json,sys;from agentj.state import State;from agentj import names;print(json.dumps(names.ctl_call(State(),json.load(open(sys.argv[1])),timeout=5)))'
            try:
                r=subprocess.run([sys.executable,'-c',code,str(path)],stdin=slave,env=self.env,
                                 preexec_fn=terminal,capture_output=True,text=True,timeout=10)
            finally:os.close(master);os.close(slave)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertFalse(json.loads(r.stdout)['ok'])
        self.assertFalse((self.root/'work/new-work').exists())
        self.assertFalse((self.root/'cards.json').exists())

    def test_paid_api_and_deletion_description_are_ordinary_zero_card_creation(self):
        self.start();v=contract();v['prompt']='Use a paid inference API and delete temporary output files; follow the harness permissions.'
        r=self.cli(v);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse((self.root/'cards.json').exists())
        self.assertEqual((self.root/'work/new-work/PROMPT.md').read_text(),v['prompt'])

    def test_native_readonly_unknown_custom_ceiling_never_becomes_host_write_or_uv_card(self):
        for mode in ('read_only','unknown_ceiling','custom_roots','wrong_native_cwd','disconnected_native'):
            with self.subTest(mode=mode):
                self.start(mode)
                try:
                    for value in (contract(),contract(True)):
                        r=self.cli(value);self.assertNotEqual(r.returncode,0)
                        self.assertIn('native_write_unverified',r.stdout)
                    self.assertFalse((self.root/'work/new-work').exists())
                    self.assertFalse((self.root/'cards.json').exists())
                finally:
                    self.stop()
                    import shutil
                    for name in ('state','work','cards.json'):
                        p=self.root/name
                        if p.is_dir():shutil.rmtree(p)
                        elif p.exists():p.unlink()

    def test_uv_does_not_survive_native_write_ceiling_downgrade(self):
        self.start('drop_write');r=self.cli(contract(True));self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'work/new-work').exists())

    def test_thread_replacement_during_uv_is_not_the_original_creation_context(self):
        self.start('replace_thread');r=self.cli(contract(True));self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'work/new-work').exists())
