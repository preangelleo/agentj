"""P130 migration and live PID-1 environment regression, isolated from owner state."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import _hermetic
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agentj import fence, telegram, tg_cursor, voice, preferences
from agentj.state import State

class TelegramMigration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=State(Path(self.tmp.name)/'state');self.st.init()
        self.env=patch.dict(os.environ, {'AGENTJ_STATE_DIR':str(self.st.root)}, clear=False)
        self.env.start();self.addCleanup(self.env.stop)
        cfg=self.st.config();cfg['telegram']={'id':'telegram','type':'telegram'}
        self.st.write_private(self.st.config_path,json.dumps(cfg).encode())
        self.cur=tg_cursor.load(self.st.root)
        self.cur.update(offset=105,bot=123,enrollment={'owner_id':321,'generation':'old','at':100})
        tg_cursor.save(self.st.root,self.cur)
    def test_default_key_name_and_ledger_recognized_without_writes(self):
        before={p.name:p.read_bytes() for p in self.st.root.iterdir() if p.is_file()}
        with patch.dict(os.environ,{'AGENTJ_TELEGRAM_BOT_TOKEN':'fixture-local-bot'}):
            self.assertEqual(telegram.configuration(self.st)['key_env'],'AGENTJ_TELEGRAM_BOT_TOKEN')
            self.assertEqual(telegram.configuration(self.st)['owner_id'],321)
            cfg=preferences.defaults();cfg['channels']['items']=[{'id':'telegram','type':'telegram'}]
            voice.validate_runtime(cfg)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.st.root.iterdir() if p.is_file()})
    def test_default_key_alone_cannot_enroll(self):
        (self.st.root/tg_cursor.OFFSET).unlink()
        with patch.dict(os.environ,{'AGENTJ_TELEGRAM_BOT_TOKEN':'fixture-local-bot'}):
            self.assertIsNone(telegram.configuration(self.st))
    def test_missing_or_invalid_owner_fails(self):
        for owner in (0,-1,True,'321'):
            self.cur['enrollment']['owner_id']=owner;tg_cursor.save(self.st.root,self.cur)
            with patch.dict(os.environ,{'AGENTJ_TELEGRAM_BOT_TOKEN':'fixture-local-bot'}):
                self.assertIsNone(telegram.configuration(self.st))
    def test_doctor_uses_live_registration_without_reading_service_key(self):
        from agentj import doctor
        cfg=preferences.defaults();cfg['channels']['items']=[{'id':'telegram','type':'telegram'}]
        before={p.name:p.read_bytes() for p in self.st.root.iterdir() if p.is_file()}
        with patch.dict(os.environ,{'AGENTJ_TELEGRAM_BOT_TOKEN':''}),patch('agentj.preferences.effective',return_value=cfg),patch('agentj.voice.hardware',return_value={}):
            with self.assertRaises(preferences.ConfigError):voice.validate_runtime(cfg)
            for live,expected in [({'ok':True,'telegram':{'enrolled':True}},doctor.OK),
                                  ({'ok':True,'telegram':{'enrolled':False}},doctor.FAIL),
                                  ({'ok':True,'telegram':{'enrolled':1}},doctor.FAIL),
                                  ({'ok':False,'telegram':{'enrolled':True}},doctor.FAIL),
                                  (None,doctor.FAIL)]:
                with patch('agentj.doctor._serve_status',return_value=live):
                    row=next(r for r in doctor.check_preferences(self.st) if r['id']=='config')
                    self.assertEqual(row['status'],expected)
            # The service flag cannot invent a missing local owner ledger.
            (self.st.root/tg_cursor.OFFSET).unlink()
            with patch('agentj.doctor._serve_status',return_value={'ok':True,'telegram':{'enrolled':True}}):
                row=next(r for r in doctor.check_preferences(self.st) if r['id']=='config')
                self.assertEqual(row['status'],doctor.FAIL)
        tg_cursor.save(self.st.root,self.cur)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.st.root.iterdir() if p.is_file()})
    def test_explicit_key_never_falls_back(self):
        self.cur['enrollment']['key_env']='P130_MISSING_BOT';tg_cursor.save(self.st.root,self.cur)
        with patch.dict(os.environ,{'AGENTJ_TELEGRAM_BOT_TOKEN':'fixture-local-bot'}):
            self.assertIsNone(telegram.configuration(self.st))

class FenceEnvironment(unittest.TestCase):
    def test_launch_whitelist_removes_control_handles_keeps_native_login(self):
        env={'PATH':'/usr/bin','HOME':'/tmp/fixture','HERDR_NEW_HANDLE':'fixture-hidden','SSH_AUTH_SOCK':'fixture-hidden',
             'DOCKER_HOST':'fixture-hidden','P130_UNRELATED_SECRET':'fixture-private','OPENAI_API_KEY':'fixture-harness','AGENTJ_PERM_SOCK':'/tmp/p'}
        clean=fence.launch_environment(env)
        self.assertFalse({'HERDR_NEW_HANDLE','SSH_AUTH_SOCK','DOCKER_HOST','P130_UNRELATED_SECRET'} & clean.keys())
        self.assertLessEqual(clean.keys(),env.keys())
        self.assertTrue(all(env[k]==v for k,v in clean.items()))
        self.assertEqual(clean['OPENAI_API_KEY'],'fixture-harness')
        self.assertEqual(clean['AGENTJ_PERM_SOCK'],'/tmp/p')
        self.assertIn('DOCKER_HOST',fence.launch_environment(env,True))
    def test_opencode_launch_preserves_constructed_configuration_bytes(self):
        from agentj.agent_opencode import opencode_env
        base={'HOME':'/tmp/p130-config','PATH':'/usr/bin',
              'OPENCODE_CONFIG_CONTENT':json.dumps({'instructions':['请始终用中文回复。'], 'agent':{'build':{'prompt':'主 Agent 指令'}}},ensure_ascii=False),
              'OPENCODE_CONFIG':'/tmp/p130-config/opencode.json',
              'OPENCODE_CONFIG_DIR':'/tmp/p130-config/native',
              'OPENCODE_PERMISSION':'{"bash":"ask"}',
              'OPENCODE_EXPERIMENTAL_PLAN_MODE':'1',
              'OPENCODE_UNRELATED_SECRET':'fixture-hidden',
              'HERDR_NEW_HANDLE':'fixture-hidden','SSH_AUTH_SOCK':'fixture-hidden'}
        env=opencode_env(base,'fixture-local-password')
        clean=fence.launch_environment(env,kind='opencode')
        for name,value in env.items():
            if name.startswith('OPENCODE_') and name!='OPENCODE_UNRELATED_SECRET':
                self.assertEqual(clean.get(name),value,name)
        self.assertEqual(clean['OPENCODE_CONFIG_CONTENT'].encode(),env['OPENCODE_CONFIG_CONTENT'].encode())
        self.assertFalse({'OPENCODE_UNRELATED_SECRET','HERDR_NEW_HANDLE','SSH_AUTH_SOCK'} & clean.keys())
        self.assertLessEqual(clean.keys(),env.keys())
        self.assertNotIn('OPENCODE_PERMISSION',fence.launch_environment(env,kind='codex'))
    def test_native_configuration_directories_preserved_for_codex_and_claude(self):
        env={'HOME':'/tmp/p130-config','PATH':'/usr/bin','CODEX_HOME':'/tmp/p130-config/codex',
             'CLAUDE_CONFIG_DIR':'/tmp/p130-config/claude','UNRELATED_SECRET':'fixture-hidden',
             'HERDR_NEW_HANDLE':'fixture-hidden','SSH_AUTH_SOCK':'fixture-hidden'}
        for kind in ('codex','claude'):
            clean=fence.launch_environment(env,kind=kind)
            for name in ('CODEX_HOME','CLAUDE_CONFIG_DIR'):
                self.assertEqual(clean[name],env[name])
            self.assertFalse({'UNRELATED_SECRET','HERDR_NEW_HANDLE','SSH_AUTH_SOCK'} & clean.keys())
    def test_only_selected_codex_provider_key_name_survives(self):
        with tempfile.TemporaryDirectory(prefix='p130-native-env-') as d:
            cfg=Path(d)/'.codex';cfg.mkdir()
            (cfg/'config.toml').write_text('model_provider="chosen"\n[model_providers.chosen]\nenv_key="OWNER_MODEL_KEY"\n[model_providers.other]\nenv_key="OTHER_MODEL_KEY"\n')
            env={'HOME':d,'PATH':'/usr/bin','OWNER_MODEL_KEY':'fixture-selected',
                 'OTHER_MODEL_KEY':'fixture-unselected','UNRELATED_SECRET':'fixture-hidden',
                 'SSH_AUTH_SOCK':'fixture-hidden'}
            clean=fence.launch_environment(env,kind='codex')
            self.assertEqual(clean['OWNER_MODEL_KEY'],env['OWNER_MODEL_KEY'])
            self.assertFalse({'OTHER_MODEL_KEY','UNRELATED_SECRET','SSH_AUTH_SOCK'} & clean.keys())
            self.assertTrue(all(k in env and env[k]==v for k,v in clean.items()))
            self.assertNotIn('OWNER_MODEL_KEY',fence.launch_environment(env,kind='claude'))
            (cfg/'config.toml').write_text('model_provider="chosen"\n[model_providers.chosen]\nenv_key="SSH_AUTH_SOCK"\n')
            self.assertNotIn('SSH_AUTH_SOCK',fence.launch_environment(env,kind='codex'))
    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('bwrap'),'Linux bwrap required')
    def test_live_proc_pid1_environ_hides_variables(self):
        with tempfile.TemporaryDirectory(prefix='p130-fence-',dir='/var/tmp') as d:
            home=Path(d);work=home/'work';work.mkdir()
            env={'HOME':d,'PATH':os.environ['PATH'],'XDG_CONFIG_HOME':str(home/'config'),
                 'HERDR_TEST_HIDDEN':'fixture-p130-hidden','SSH_AUTH_SOCK':'fixture-p130-hidden','DISPLAY':'fixture-p130-hidden'}
            with patch.dict(os.environ,env):
                st=State(home/'state');st.init();st.perm_dir.mkdir(exist_ok=True)
                argv=fence.bwrap_argv(st,str(work),environ=env)
                self.assertIn('--as-pid-1',argv)
                code="import os; b=open('/proc/1/environ','rb').read(); assert b'fixture-p130-hidden' not in b; assert not any(k in os.environ for k in ('HERDR_TEST_HIDDEN','SSH_AUTH_SOCK','DISPLAY')); print('PASS pid1 clean')"
                r=subprocess.run(argv+[sys.executable,'-c',code],env=fence.launch_environment(env),capture_output=True,text=True,timeout=15)
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertIn('PASS pid1 clean',r.stdout)

from test_p78_remote_pair import HostFlow
from agentj import remote_pair, wire
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization

class DeviceRename(HostFlow):
    async def test_signed_device_name_changes_only_name_and_replay_is_ignored(self):
        pub=X25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        did=self.st.add_device(pub,'old name');before=self.st.devices()[did].copy()
        self.q.update(op='device_name',target={'device':did,'name':'华为平板'})
        await self.h.remote_pair.execute(self.signed())
        after=self.st.devices()[did]
        self.assertEqual(after,{**before,'name':'华为平板'})
        self.assertIn(self.q['id'],self.h.remote_pair.results)
        self.q['target']['name']='replayed'
        await self.h.remote_pair.execute(self.signed())
        self.assertEqual(self.st.devices()[did]['name'],'华为平板')
    async def test_invalid_name_or_device_refused(self):
        for target in [{'device':'bad','name':'ok'}, {'device':wire.b64u(b'x'*12),'name':'bad\nname'}, {'device':wire.b64u(b'x'*12),'name':''}]:
            self.q.update(op='device_name',target=target)
            self.assertIsNone(self.open())
        self.assertFalse(self.st.rename_device('absent','name'))
