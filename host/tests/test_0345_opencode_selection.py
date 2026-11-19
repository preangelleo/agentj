"""Switch qualification and the running adapter share an inode and the owner's selected model."""
import _hermetic
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agentj import ark, ark_connect, binaries, harness, olares_probe, opencode_binary as images, opencode_free
from agentj.agent_opencode import OpenCodeAgent
from agentj.opencode_probe_route import environment

MODEL = 'ark-agent-plan/doubao-seed-2.1-pro'


class Installations(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.project = self.home/'work'
        self.project.mkdir()
        self.env = {'HOME':str(self.home),'PATH':str(self.project/'bin')}
        self.enterContext(patch.dict(os.environ,self.env,clear=True))
        self.enterContext(patch.object(images,'SYSTEM',()))

    def executable(self,rel):
        path=self.home/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2('/bin/echo',path)
        return path

    def test_home_as_workdir_latest_mise_and_official_global_npm(self):
        for i in range(32):
            self.executable(f'.local/share/mise/installs/opencode/1.0.{i}/opencode')
        npm=self.executable('.local/lib/node_modules/@opencode/cli/bin/opencode.exe')
        link=self.home/'.local/bin/opencode'
        link.parent.mkdir()
        link.symlink_to(npm)
        shadow=self.executable('work/bin/opencode')
        found=images.candidates(self.env,self.home)
        self.assertIn(('npm',npm),found)
        self.assertIn(('mise',self.home/'.local/share/mise/installs/opencode/1.0.31/opencode'),found)
        self.assertNotIn(shadow,dict(found).values())
        self.assertEqual(len(found),images.LIMIT)
        self.assertIn(('npm',npm),images.candidates(self.env,self.project))
        npm.chmod(0o777)
        self.assertNotIn(('npm',npm),images.candidates(self.env,self.project))

    def test_explicit_custom_image_and_changed_file_are_not_replaced(self):
        tool=self.executable('owner/bin/chosen')
        self.executable('.opencode/bin/opencode')
        env={**self.env,'AGENTJ_OPENCODE_BIN':str(tool)}
        self.assertEqual(images.candidates(env,self.project),[('explicit',tool)])
        pin=images.record(tool,'2.0.22','explicit')
        args=images.argv(pin,['model-ok'])
        self.assertEqual(subprocess.check_output(args,text=True).strip(),'model-ok')
        other=self.executable('owner/bin/updating')
        os.replace(other,tool)
        self.assertFalse(images.valid(pin))
        self.assertEqual(subprocess.run(args,check=False).returncode,126)
        with self.assertRaisesRegex(ValueError,'binary_changed'):
            images.argv(pin,['serve'])

    def test_order_real_model_fallback_diagnostics_and_formal_argv(self):
        old=self.executable('.local/share/mise/installs/opencode/1.9.0/opencode')
        newer=self.executable('.opencode/bin/opencode')
        stamps={old:'1.9.0',newer:'2.0.22'}
        calls=[]
        def version(path,source,launch=None):
            return images.record(path,stamps[path],source)
        def first(path,model,*args,**kw):
            calls.append((path,model))
            raise opencode_free.ProbeError('native_timeout')
        def second(path,model,*args,**kw):
            calls.append((path,model))
            return 'Hello'
        with patch.object(images,'version',version),patch('agentj.agent_opencode2.probe',first),patch.object(opencode_free,'probe',second):
            row=images.run(None,{'dir':str(self.project),'fence':False},MODEL,20)
        self.assertEqual(calls,[(str(newer),MODEL),(str(old),MODEL)])
        self.assertEqual([r['reason'] for r in row['binary_candidates']],['native_timeout','selected'])
        pin=row['opencode_binary']
        self.assertEqual(olares_probe.row(row,'opencode')['opencode_binary'],pin)
        agent=object.__new__(OpenCodeAgent)
        agent.cfg={'opencode_binary':pin}
        self.assertIn(str(old),agent.argv(12345))

    def test_native_exit_zero_or_blank_reply_isnt_model_evidence(self):
        exe=self.executable('.opencode/bin/opencode')
        with patch.object(images,'version',return_value=images.record(exe,'2.0.22','native')),patch('agentj.agent_opencode2.probe',return_value=''):
            row=images.run(None,{'dir':str(self.project)},MODEL,10)
        self.assertFalse(row['callable'])
        self.assertEqual(row['probe_reason'],'empty_probe_reply')
        self.assertNotIn('opencode_binary',row)

    def test_external_server_never_selected_from_local_mise(self):
        with patch.object(images,'run',side_effect=AssertionError),patch('agentj.opencode_start.start_probe',return_value={'secs':1,'external_version':'2.0.22'}):
            row=harness.probe(only=('opencode',),model=MODEL,opencode_launch=(None,{'session_mode':'shared','shared_opencode_port':8761}))['harnesses'][0]
        self.assertTrue(row['callable'])
        self.assertEqual((row['executable_source'],row['version']),('external','2.0.22'))
        self.assertNotIn('executable',row)

    def test_file_reference_uses_only_saved_plan_key_and_leaves_config_intact(self):
        block=ark.plan_data('agent')['block']
        block['options']['apiKey']='{file:~/not-opened/missing}'
        cfg=self.home/'.config/opencode/opencode.json'
        cfg.parent.mkdir(parents=True)
        original=json.dumps({'provider':{'ark-agent-plan':block}})
        cfg.write_text(original)
        env={**self.env,'ARK_AGENT_PLAN_API_KEY':'test-fixture','OTHER_KEY':'not-a-provider'}
        child=environment(env,self.project,MODEL,str(self.home/'scratch'),agent_plan=True)
        self.assertEqual(child['ARK_AGENT_PLAN_API_KEY'],'test-fixture')
        self.assertNotIn('OTHER_KEY',child)
        actual=json.loads((Path(child['XDG_CONFIG_HOME'])/'opencode/opencode.json').read_text())
        self.assertEqual(actual['provider']['ark-agent-plan']['options'],ark.plan_data('agent')['block']['options'])
        self.assertEqual(ark_connect.install(env,self.project,MODEL),'native')
        self.assertEqual(cfg.read_text(),original)
        (self.project/'opencode.json').write_text(original.replace('not-opened','project-choice'))
        with self.assertRaises(opencode_free.ProbeError):
            environment(env,self.project,MODEL,str(self.home/'invalid'),agent_plan=True)
        (self.project/'opencode.json').unlink()
        block['options']['baseURL']='https://elsewhere.invalid/v1'
        cfg.write_text(json.dumps({'provider':{'ark-agent-plan':block}}))
        with self.assertRaisesRegex(opencode_free.ProbeError,'ark_route_conflict'):
            ark_connect.official(env,self.project,MODEL)

    def test_reserved_key_rejects_another_provider(self):
        config=self.home/'.config/opencode/opencode.json'
        config.parent.mkdir(parents=True)
        block=ark.plan_data('agent')['block']
        config.write_text(json.dumps({'provider':{'other-ark':block}}))
        with self.assertRaisesRegex(opencode_free.ProbeError,'ark_route_conflict'):
            environment(self.env,self.project,MODEL.replace('ark-agent-plan','other-ark'),str(self.home/'probe'))

    def test_linuxbrew_system_path(self):
        # The owner-checked Homebrew prefix on Linux accepts only this package's executable.
        prefix=Path(os.sep,'home','linuxbrew','.linuxbrew')
        with patch.object(images,'SYSTEM',(str(prefix/'bin'),)):
            self.assertEqual(images._source(prefix/'Cellar/opencode/2.0.22/bin/opencode',self.home,self.env,None),'system')
            self.assertIsNone(images._source(prefix/'Cellar/opencode/2.0.22/bin/other',self.home,self.env,None))

    def test_system_layout_requires_expected_name_and_source(self):
        allowed=self.executable('system/bin/opencode')
        other=self.executable('system/bin/other')
        shadow=self.project/'bin/opencode'
        shadow.parent.mkdir()
        shadow.symlink_to(other)
        with patch.object(images,'SYSTEM',(str(allowed.parent),)):
            self.assertIsNone(images._source(other,self.home,self.env,None))
            self.assertEqual(images.candidates(self.env,self.project),[('system',allowed)])
            self.assertIsNone(images._source(allowed.parent.parent/'Cellar/opencode/2.1.3/bin/other',self.home,self.env,None))

    def test_codex_claude_same_tree_only_move_forward(self):
        for kind in ('claude','codex'):
            new=self.executable(f'{kind}/versions/2.10.0/bin/{kind}')
            old=self.executable(f'{kind}/versions/2.9.9/bin/{kind}')
            for pin,path,want in ((new,old,new),(old,new,new)):
                env={**self.env,binaries.ENV[kind]:str(pin),'PATH':str(path.parent)}
                self.assertEqual(binaries.resolve(kind,env)['path'],str(want))
