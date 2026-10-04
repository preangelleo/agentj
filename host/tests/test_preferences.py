import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agentj import preferences as p
from agentj.state import State

class PreferencesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='aj-config-',dir='/var/tmp'); self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name); self.env=patch.dict(os.environ,{'HOME':str(self.home),'XDG_CONFIG_HOME':str(self.home/'.config'),'AGENTJ_STATE_DIR':str(self.home/'state'),'PATH':'/usr/bin:/bin'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.st=State(); self.st.init()
    def test_bilingual_aliases_resolve_and_unknown_reset_is_rejected(self):
        import io
        for key,meta in p.SCHEMA.items():
            self.assertTrue(any(any('\u3400'<=c<='\u9fff' for c in alias) for alias in meta['aliases']),key)
            self.assertTrue(any(alias.isascii() for alias in meta['aliases']),key)
        self.assertEqual(p.resolve_key('唤醒词'),'voice.wake_word')
        self.assertEqual(p.resolve_key('VOICE TTS RATE'),'voice.tts.rate')
        with patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(p.command(['set','主题','dark']),0)
            self.assertEqual(p.command(['reset','unknown.section']),1)
        self.assertEqual(p.get(p.effective(self.st),'appearance.theme'),'dark')

    def test_012_upgrade_defaults_shared_without_repairing_device(self):
        self.st.set_agent_config('claude',str(self.home))
        self.st.add_device(bytes(range(32)),'existing phone',bytes(range(32)))
        paths=[self.st.x25519_path,self.st.ed25519_path,self.st.devices_path]
        before={p:p.read_bytes() for p in paths}
        self.assertEqual(self.st.agent_config()['session_mode'],'shared')
        self.assertTrue(self.st.agent_config()['high_risk_warnings'])
        self.assertEqual({p:p.read_bytes() for p in paths},before)
        self.assertEqual(len(self.st.devices()),1)

    def test_json5_comments_unicode_and_trailing_commas(self):
        doc=p.parse("{version:1, // retained\n appearance:{theme:'dark',}, voice:{wake_word:'嘿小J'},}")
        self.assertEqual(p.get(p.validate(doc),'voice.wake_word'),'嘿小J')
    def test_locked_human_unknown_and_duplicate_rejected(self):
        for raw in ['{security:{e2e:false}}','{human:{fence:"off"}}','{appearance:{themes:"light"}}','{voice:{},voice:{}}']:
            with self.subTest(raw=raw),self.assertRaises(p.ConfigError):p.parse(raw)
    def test_line_and_legal_values(self):
        with self.assertRaises(p.ConfigError) as cm:p.parse('{\n appearance: {\n theme:"wrong"\n }}')
        self.assertEqual(cm.exception.line,3);self.assertIn('system',cm.exception.detail)
    def test_defaults_upgrade_preserves_user_and_adds_unmodified(self):
        user={'appearance':{'theme':'dark'},'menu':{'items':[{'id':'mine','cmd':'/mine','desc':'Mine'}]}}
        old=p.defaults(); new=p.defaults(); new['appearance']['language']='en';new['menu']['items']=[{'id':'new','cmd':'/new','desc':'New'}]
        before=json.dumps(user)
        result=p.merge(new,user)
        self.assertEqual(result['appearance'],{'theme':'dark','language':'en'})
        self.assertEqual([x['id'] for x in result['menu']['items']],['new','mine'])
        self.assertEqual(json.dumps(user),before)
    def test_id_merge_removal_and_duplicate(self):
        self.assertEqual(p.merge([{'id':'a','x':1},{'id':'b','x':2}],[{'id':'a','x':3},{'id':'b','disabled':True}]),[{'id':'a','x':3}])
        with self.assertRaises(p.ConfigError):p.parse('{menu:{items:[{id:"a",cmd:"/a",desc:"A"},{id:"a",cmd:"/a",desc:"A"}]}}')
    def test_editor_preserves_other_lines_and_comments(self):
        raw="// MY HEADER\n{version:1, appearance:{theme:'light', // theme note\n language:'en'}, // intact\n voice:{wake_word:'Hey Ada'},}\n"
        changed=p.edit(raw,'appearance.theme','dark')
        self.assertIn('// MY HEADER',changed); self.assertIn('// theme note',changed);self.assertIn("language:'en'",changed)
        self.assertEqual(p.get(p.parse(changed),'appearance.theme'),'dark')
        removed=p.edit(changed,'appearance.theme',None,remove=True)
        self.assertIsNone(p.get(p.parse(removed),'appearance.theme'))
        added=p.edit(removed,'voice.tts.rate',1.4);self.assertEqual(p.get(p.parse(added),'voice.tts.rate'),1.4)
    def test_editor_empty_and_last(self):
        for raw in ['{}','{appearance:{theme:"dark"}}','{appearance:{theme:"dark",language:"en"}}']:
            new=p.edit(raw,'voice.wake_word','Hey J');p.parse(new)
            if 'theme' in raw:p.parse(p.edit(raw,'appearance.theme',None,remove=True))
    def test_apply_failure_restores_file_and_no_history(self):
        p.ensure(); old=p.path().read_text()
        with patch('agentj.names.ctl_call',return_value={'ok':False,'key':'voice.tts.voice','error':'unavailable'}):
            with self.assertRaises(p.ConfigError) as cm:p.transact(self.st,p.edit(old,'appearance.theme','dark'))
        self.assertEqual(cm.exception.code,3);self.assertEqual(p.path().read_text(),old)
        self.assertFalse(list((self.st.root/'config-history').glob('*')))
    def test_success_history_bounded_and_effective_name_changes(self):
        p.ensure(); self.st.set_agent_name('小J');self.assertEqual(p.get(p.effective(self.st),'voice.wake_word'),'嘿 小J')
        self.st.set_agent_name('Ada');self.assertEqual(p.get(p.effective(self.st),'voice.wake_word'),'嘿 Ada')
        with patch('agentj.names.ctl_call',return_value=None):
            for i in range(22):p.transact(self.st,p.edit(p.read()[0],'appearance.theme','dark' if i%2 else 'light'))
        self.assertEqual(len(list((self.st.root/'config-history').glob('*'))),20)
        self.assertEqual(p.path().stat().st_mode&0o777,0o600)
    def test_durable_failure_restores_last_good_and_user_file(self):
        p.ensure();old=p.path().read_bytes();p.save_good(self.st,p.parse(old.decode()));good=p.runtime(self.st).read_bytes()
        write=p._write_private
        def fail_candidate(target,data):
            if target==p.path() and b'dark' in data:raise OSError('controlled write failure')
            return write(target,data)
        with patch('agentj.preferences._write_private',side_effect=fail_candidate):
            with self.assertRaises(OSError):p.commit_files(self.st,p.edit(old.decode(),'appearance.theme','dark'),{'appearance':{'theme':'dark'}})
        self.assertEqual(p.path().read_bytes(),old);self.assertEqual(p.runtime(self.st).read_bytes(),good)
        self.assertFalse(list((self.st.root/'config-history').glob('*')))
    def test_startup_invalid_uses_previous_confirmed_preferences(self):
        p.ensure();p.save_good(self.st,{'appearance':{'theme':'dark'}});p.path().write_text('{invalid: true}')
        from agentj.serve import Host
        h=Host(self.st,read_stdin=False)
        self.assertEqual(h.preferences['appearance']['theme'],'dark');self.assertIsNotNone(h.config_problem)
    def test_skill_three_harness_links_clean_uninstall_and_owner_required(self):
        import io
        from agentj.personalize import command
        with patch('sys.stdout',new_callable=io.StringIO),patch('os.isatty',return_value=False):
            self.assertEqual(command(['skill','install']),2)
            self.assertEqual(command(['skill','install','--owner-confirmed']),0)
            for rel in ['.claude/skills/agentj-config','.codex/skills/agentj-config','.config/opencode/skills/agentj-config']:
                self.assertTrue((self.home/rel).is_symlink())
            self.assertEqual(command(['skill','uninstall']),0)
            self.assertFalse((self.home/'.claude/skills/agentj-config').exists())
    def test_secret_parser_error_not_echoed(self):
        with self.assertRaises(p.ConfigError) as cm:p.parse('{voice: "SECRET_SENTINEL" invalid}')
        self.assertNotIn('SECRET_SENTINEL',str(cm.exception))
    def test_shortcut_conflict_and_invalid_channel(self):
        with self.assertRaises(p.ConfigError):p.parse('{keyboard:{bindings:[{id:"a",combo:"ctrl+j",action:"focus-input"},{id:"b",combo:"ctrl+j",action:"open-menu"}]}}')
        with self.assertRaises(p.ConfigError):p.parse('{channels:{items:[{id:"unsupported",type:"slack"}]}}')

    def test_timestamp_migrations_preserve_bytes_and_are_idempotent(self):
        from agentj import config_migrations as m
        p.ensure(); raw="// owner comment\n{appearance:{theme:'dark'}}\n";p.path().write_text(raw)
        self.assertEqual(m.run(self.st,pending=True)['pending'],['202610040001'])
        self.assertEqual(m.run(self.st)['applied'],['202610040001'])
        self.assertEqual(m.run(self.st)['applied'],[]);self.assertEqual(p.path().read_text(),raw)
    def test_migration_marker_failure_restores_both_files_and_retries(self):
        from agentj import config_migrations as m
        p.ensure();old=p.path().read_bytes();p.save_good(self.st,p.defaults());good=p.runtime(self.st).read_bytes()
        step=('202610040002',lambda raw:p.edit(raw,'appearance.theme','dark'))
        original=m._write_private
        def fail_marker(target,data):
            if target.parent.name=='config-migrations':raise OSError('controlled marker failure')
            return original(target,data)
        with patch.object(m,'MIGRATIONS',(step,)),patch.object(m,'_write_private',side_effect=fail_marker):
            with self.assertRaises(OSError):m.run(self.st)
        self.assertEqual(p.path().read_bytes(),old);self.assertEqual(p.runtime(self.st).read_bytes(),good)
        self.assertFalse((self.st.root/'config-migrations/202610040002.json').exists())
        with patch.object(m,'MIGRATIONS',(step,)):
            self.assertEqual(m.run(self.st)['applied'],['202610040002'])
            self.assertEqual(m.run(self.st)['applied'],[])
        self.assertEqual(p.get(p.effective(self.st),'appearance.theme'),'dark')
    def test_migration_refuses_live_host_or_invalid_transformation(self):
        from agentj import config_migrations as m
        p.ensure();old=p.path().read_bytes()
        with patch('agentj.names.ctl_call',return_value={'revision':'live'}):
            with self.assertRaises(p.ConfigError):m.run(self.st)
        with patch.object(m,'MIGRATIONS',(('202610040003',lambda raw:'{security:{e2e:false}}'),)):
            with self.assertRaises(p.ConfigError):m.run(self.st)
        self.assertEqual(p.path().read_bytes(),old)
        self.assertFalse((self.st.root/'config-migrations/202610040003.json').exists())


class HostPreferencesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='aj-hot-',dir='/var/tmp');self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name); self.env=patch.dict(os.environ,{'HOME':str(root),'XDG_CONFIG_HOME':str(root/'.config'),'AGENTJ_STATE_DIR':str(root/'state'),'PATH':'/usr/bin:/bin'})
        self.env.start();self.addCleanup(self.env.stop)
        self.st=State();self.st.init();p.ensure()
        from agentj.serve import Host
        self.host=Host(self.st,read_stdin=False)
    async def test_private_directories_traversable_under_restrictive_umask(self):
        # Mirrors startup: an async Unix-socket creation used to mask execute bits
        # while the preferences watcher created its first directory.
        old=os.umask(0o177)
        try:
            raw=p.edit(p.read()[0],'appearance.theme','dark')
            result=await self.host.apply_preferences(raw)
        finally:os.umask(old)
        self.assertTrue(result['applied'])
        self.assertEqual(p.get(p.effective(self.st),'appearance.theme'),'dark')
        for directory in (p.path().parent,p.runtime(self.st).parent,self.st.root/'config-history'):
            self.assertEqual(directory.stat().st_mode&0o777,0o700)
        self.assertEqual(p.runtime(self.st).stat().st_mode&0o777,0o600)

    async def test_transaction_changes_host_and_rolls_back_unavailable_provider(self):
        p.path().write_text(p.edit(p.read()[0],'appearance.theme','dark'))
        result=await self.host.apply_preferences();self.assertTrue(result['applied']);self.assertEqual(self.host.preferences['appearance']['theme'],'dark')
        old=copy_json(self.host.preferences)
        p.path().write_text('{voice:{asr:{mode:"cloud",model:"qwen/qwen3-asr-1.7b",key_env:"MISSING_AGENTJ_TEST_KEY"}}}')
        result=await self.host.apply_preferences();self.assertFalse(result['ok']);self.assertEqual(self.host.preferences,old)
        self.assertIsNotNone(self.host.preferences_msg()['problem'])
    async def test_timeout_configuration_preserves_test_only_safety_cap(self):
        from agentj import serve
        raw=p.edit(p.read()[0],"approval.timeout",10)
        result=await self.host.apply_preferences(raw)
        self.assertTrue(result["applied"]);self.assertEqual(self.host.ask_ttl,10)
        with patch.object(serve,"ASK_TTL",2):
            result=await self.host.apply_preferences(p.edit(raw,"approval.timeout",120))
        self.assertTrue(result["applied"]);self.assertEqual(self.host.ask_ttl,2)

    async def test_bad_startup_reports_and_keeps_safe_floor(self):
        p.path().write_text('{security:{e2e:false}}')
        from agentj.serve import Host
        h=Host(self.st,read_stdin=False);self.assertIsNotNone(h.config_problem);self.assertEqual(h.ask_ttl,120)
    async def test_name_tracks_without_persisting_automatic_phrase(self):
        self.st.set_agent_name('Ada');await self.host.apply_preferences();self.assertEqual(self.host.preferences_msg()['value']['voice']['wake_word'],'嘿 Ada')
        self.st.set_agent_name('小J');self.assertEqual(self.host.preferences_msg()['value']['voice']['wake_word'],'嘿 小J')

def copy_json(value):return json.loads(json.dumps(value))

if __name__=='__main__':unittest.main()
