"""P103: selected native binary, real spawn boundary and durable budgets."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from agentj import binaries, service
from agentj.bots import native
from agentj.bots.engine import Engine
from agentj.bots.owner import Manager
from agentj.bots.provider import resolve
from agentj.bots.store import Store, BotError


class NativeSelection(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.env={'HOME':str(self.root),'PATH':'','CODEX_HOME':str(self.root)}

    def script(self,name,body):
        p=self.root/name
        p.write_text('#!'+sys.executable+'\n'+body)
        p.chmod(0o700)
        return str(p)

    def test_selected_override_before_scrub_real_process(self):
        selected=self.script('selected-opencode', '''import json, os, sys
assert '--pure' in sys.argv
assert '--agent' in sys.argv
assert not any(k.startswith(('AGENTJ_', 'HERDR_', 'VIBE_REMOTE_', 'TMUX', 'SSH_')) for k in os.environ)
assert json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])['tools']=={'*':False}
assert 'customer fixture' not in ' '.join(sys.argv)
assert 'customer fixture' in sys.stdin.read()
print(json.dumps({'type':'text','part':{'text':json.dumps({'text':'fixture reply','tools':[]})}}))
print(json.dumps({'type':'step_finish','part':{'tokens':{'input':3,'output':4}}}))
''')
        e={**self.env,'AGENTJ_OPENCODE_BIN':selected,'AGENTJ_PRIVATE':'fixture-only','HERDR_PRIVATE':'fixture-only'}
        with patch.dict(os.environ,e,clear=True):
            p=resolve({'source':'main'},'opencode','fixture/model')
            r=native.invoke_native(p,[{'role':'user','content':'customer fixture'}])
        self.assertEqual((r.text,r.usage),('fixture reply',7))

    def test_each_adapter_uses_same_resolver_and_service_capture(self):
        for name in ('claude','codex','gemini','opencode'):
            exe=self.script(name,'pass\n')
            for key in (binaries.ENV[name],binaries.ENV[name].replace('AGENTJ_','AGENTJARVIS_')):
                e={**self.env,key:exe}
                selected=native.executable(name,e)
                self.assertEqual(selected,binaries.resolve(name,e)['path'])
                self.assertEqual(service.service_env(e)[0][binaries.ENV[name]],selected)
                cmd=native.command(resolve({'source':'main'},name),self.root,dict(self.env),selected)
                self.assertEqual(cmd[0],selected)
        with self.assertRaisesRegex(native.NativeNotStarted,'harness_unavailable'):native.executable('unknown',self.env)

    def test_no_fallback_missing_nonexec_and_ambiguous_shim(self):
        self.script('opencode','pass\n')
        for path,code in ((self.root/'missing','native_executable_missing'),(self.root/'noexec','native_executable_not_executable')):
            if path.name=='noexec':path.write_text('fixture');path.chmod(0o600)
            with self.assertRaisesRegex(native.NativeNotStarted,code):native.executable('opencode',{**self.env,'PATH':str(self.root),'AGENTJ_OPENCODE_BIN':str(path)})
        shim=self.root/'shim';shim.write_text('#!/bin/sh\nexec mise x opencode\n');shim.chmod(0o700)
        for ver in ('1','2'):
            folder=self.root/'.local/share/mise/installs/opencode'/ver;folder.mkdir(parents=True)
            p=folder/'opencode';p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o700)
        with self.assertRaisesRegex(native.NativeNotStarted,'native_executable_selection_failed'):native.executable('opencode',{**self.env,'AGENTJ_OPENCODE_BIN':str(shim)})

    def test_spawn_classification_only_at_popen(self):
        for err,code in ((FileNotFoundError(),'native_executable_missing'),(PermissionError(),'native_executable_not_executable'),(OSError(),'native_spawn_failed')):
            with patch('agentj.bots.native.subprocess.Popen',side_effect=err):
                with self.assertRaisesRegex(native.NativeNotStarted,code):native.run(['/fixture/native'],'',self.env,self.root)
        failed=self.script('started','import sys\nsys.stdin.read()\nsys.exit(1)\n')
        try:native.run([failed],'data',self.env,self.root)
        except BotError as e:self.assertNotIsInstance(e,native.NativeNotStarted);self.assertEqual(str(e),'native_failed')
        else:self.fail('started nonzero process must fail')

    def test_owner_detail_reports_missing_without_paths_or_raw_errors(self):
        store=Store(self.root/'bots');self.addCleanup(store.close)
        bid=store.create({'slug':'sample-shop'})['id']
        host=SimpleNamespace(st=SimpleNamespace(perm_dir=self.root),agent=None,agent_cfg={'kind':'opencode'})
        manager=Manager(host);manager.store=store
        with patch.dict(os.environ,self.env,clear=True):detail=manager.read({'op':'detail','id':bid})
        self.assertEqual(detail['provider_problem'],'native_executable_missing')


class Budget(unittest.IsolatedAsyncioTestCase):
    async def test_not_started_zero_but_started_or_untrusted_failure_stays_reserved(self):
        for mode in ('missing','nonexec','spawn','started','untrusted'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);store=Store(root/'bots');bid=store.create({'slug':'sample-shop'})['id']
                p=resolve({'source':'main'},'opencode')
                env={'HOME':folder,'PATH':''}
                exe=root/'native';exe.write_text('#!'+sys.executable+'\nimport sys\nsys.stdin.read()\nsys.exit(1)\n');exe.chmod(0o700)
                if mode=='nonexec':exe.chmod(0o600)
                env['AGENTJ_OPENCODE_BIN']=str(root/'missing' if mode=='missing' else exe)
                engine=Engine(store,'opencode')
                with patch.dict(os.environ,env,clear=True):
                    if mode=='spawn':
                        with patch('agentj.bots.native.subprocess.Popen',side_effect=OSError('private detail')):
                            with self.assertRaises(BotError):await engine.call(p,bid,'','',[])
                    else:
                        if mode=='untrusted':engine.model_call=lambda *a:(_ for _ in ()).throw(BotError('native_executable_missing'))
                        with self.assertRaises(BotError):await engine.call(p,bid,'','',[])
                expected=0 if mode in ('missing','nonexec','spawn') else 100000
                self.assertEqual(store.statistics(bid)[0]['charged_tokens'],expected)
                if expected==0:
                    self.assertEqual(store.statistics(bid)[0]['unknown_calls'],0)
                    store.reserve(bid,'','',3000,'jev')
                    code=store.db.execute("SELECT outcome FROM activity WHERE kind='native_start'").fetchone()[0]
                    self.assertNotIn('private detail',code)
                store.close();store=Store(root/'bots')
                try:
                    if expected: self.assertEqual(store.statistics(bid)[0]['charged_tokens'],expected)
                finally:store.close()

    async def test_visitor_failure_is_generic_but_owner_audit_is_specific(self):
        from agentj.bots.provider import Result
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=Store(root/'bots')
            try:
                bid=store.create({'slug':'sample-shop','enabled':True,'terms_accepted':True})['id'];vid,cap=store.visitor(bid)
                engine=Engine(store,'opencode',decision_call=lambda *a:Result('{"allow":true}',1))
                with patch.dict(os.environ,{'HOME':folder,'PATH':'','AGENTJ_OPENCODE_BIN':str(root/'missing')},clear=True):
                    r=await engine.reply(bid,vid,cap,'a'*32,'Hello')
                self.assertEqual(r['status'],'refused');self.assertNotIn('native_',r['text'])
                rows=list(store.db.execute('SELECT kind,actual FROM calls'))
                self.assertEqual([(r['kind'],r['actual']) for r in rows],[('jev',1),('model',0)])
                self.assertEqual(store.db.execute("SELECT outcome FROM activity WHERE kind='native_start'").fetchone()[0],'native_executable_missing')
                engine.model_call=lambda *a:Result('hello',7)
                await engine.call(resolve({'source':'main'},'opencode'),bid,vid,'b'*32,[])
                host=SimpleNamespace(st=SimpleNamespace(perm_dir=root),agent=None,agent_cfg={'kind':'opencode'})
                manager=Manager(host);manager.store=store
                with patch('agentj.bots.native.executable',return_value='/fixture/native'):
                    detail=manager.read({'op':'detail','id':bid})
                self.assertIsNone(detail['native_start_failure']);self.assertIsNone(detail['provider_problem'])
            finally:store.close()
