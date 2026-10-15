"""P245: real catalog bundles, test-only signatures, actual installer, isolated HOME/state."""
import _hermetic  # noqa: F401
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_fixtures as fx
from agentj import bundle, market, starters, wizard

CATALOG = fx.CATALOG

@unittest.skipUnless((CATALOG/'workflows/llm-wiki').exists(), 'catalog required')
class Starters(unittest.TestCase):
    def setUp(self):
        self.e=fx.Env();self.addCleanup(self.e.close)
        self.root=self.e.root/'workspace';self.root.mkdir()
        self.key=fx.TestKey();self.srv=fx.PkgServer()
        for name in starters.STARTERS:
            data,m=bundle.build(CATALOG/'workflows'/name)
            self.srv.add(data,signature=self.key.sign_package(data,m),track='official',certified=True)
        self.kw=dict(st=self.e.st,post=self.srv,get=self.srv.get,keyring=self.key.keyring)

    def ensure(self,h='claude'):
        return starters.ensure(self.root,[h],**self.kw)

    def test_new_install_three_cards_all_harnesses(self):
        for h in ('claude','codex','opencode'):
            self.root=self.e.root/('new-'+h);self.root.mkdir()
            rows=self.ensure(h)
            self.assertEqual(len([r for r in rows if r['status']=='starter-installed']),3,rows)
            self.assertEqual(set(starters.wdb_folders(self.root)),set(starters.STARTERS))
            for n in starters.STARTERS:
                for skill in ('start-session', 'finalize-session', 'self-reflection', 'self-reflection-cli', 'slim-docs'):
                    self.assertTrue((self.root/n/'.claude/skills'/skill/'SKILL.md').is_file())
                self.assertIn('verified: []',(self.root/n/'documentation/CONSTITUTION.md').read_text())
            self.assertEqual(self.ensure(h),[])
            if h=='codex':
                self.assertTrue(list((self.root/'llm-wiki/.codex/agents').glob('*.toml')))
            # The normal market ledger, not a parallel package inventory.
            self.assertEqual(len([r for r in market.read_installed(self.e.st) if r['target'].startswith(str(self.root))]),3)

    def test_old_user_upgrade_preserves_other_folders_and_announcement(self):
        (self.root/'old-code').mkdir();(self.root/'old-code/user.txt').write_text('keep')
        self.ensure()
        self.assertEqual((self.root/'old-code/user.txt').read_text(),'keep')
        line=starters.take_announcement(self.root,'en')
        for n in starters.STARTERS:self.assertIn(n,line)
        self.assertIn('organise my other folders',line)
        self.assertEqual(starters.take_announcement(self.root),'')

    def test_collision_and_deleted_stay_gone(self):
        import shutil
        (self.root/'llm-wiki').mkdir();(self.root/'llm-wiki/mine').write_text('mine')
        self.ensure();self.assertEqual((self.root/'llm-wiki/mine').read_text(),'mine')
        shutil.rmtree(self.root/'script-article-writing');self.ensure()
        self.assertFalse((self.root/'script-article-writing').exists())
        states={r['name']:r['state'] for r in starters.status(self.root)}
        self.assertEqual(states['script-article-writing'],'removed')
        self.assertEqual(states['llm-wiki'],'skipped')

    def test_interrupted_manifest_recovers_market_ledger_and_deleted_stays_gone(self):
        import shutil
        with mock.patch('agentj.wizard._write_manifest', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):self.ensure()
        self.assertTrue((self.root/'llm-wiki').is_dir())
        shutil.rmtree(self.root/'llm-wiki')
        self.ensure()
        self.assertFalse((self.root/'llm-wiki').exists())
        self.assertEqual(starters.status(self.root)[0]['state'],'removed')
        self.assertEqual(wizard.read_manifest(self.root)['starters']['llm-wiki']['state'],'removed')

    def test_three_existing_wdb_skip_no_network(self):
        for i in range(3):
            d=self.root/str(i)/'documentation';d.mkdir(parents=True)
            (d/'STRUCTURE.json').write_text('{"bible_version":"2.11.0"}')
        self.ensure();self.assertEqual(self.srv.calls,[])
        self.assertTrue(all(r['state']=='skipped' for r in starters.status(self.root)))

    def test_unlinked_and_download_failure_retry(self):
        with mock.patch('agentj.cloud.read_cloud',return_value=None):self.assertEqual(self.ensure(),[])
        self.assertTrue(all(r['state']=='pending' for r in starters.status(self.root)))
        with mock.patch('agentj.market.fetch',side_effect=market.PlazaError('offline')):
            self.assertEqual(len(self.ensure()),3)
        self.assertTrue(all(r['state']=='pending' for r in starters.status(self.root)))
        self.ensure();self.assertEqual(len(starters.wdb_folders(self.root)),3)

    def test_unsigned_or_tampered_refused(self):
        for v in self.srv.packages.values():v['detail']['signature']=None
        self.ensure();self.assertEqual(starters.wdb_folders(self.root),[])
        self.assertTrue(all(r['state']=='pending' for r in starters.status(self.root)))
        for v in self.srv.packages.values():v['detail']['signature']='invalid'
        self.ensure();self.assertEqual(starters.wdb_folders(self.root),[])

    def test_signed_skill_cannot_be_automatic_workflow(self):
        d=fx.make_skill(self.e.root/'source',name='llm-wiki')
        data,m=bundle.build(d);self.srv.add(data,signature=self.key.sign_package(data,m),certified=True)
        self.ensure();self.assertFalse((self.root/'llm-wiki').exists())

    def test_required_credentials_and_unsafe_starter_options_refused(self):
        d=fx.make_workflow(self.e.root/'source',name='llm-wiki',
                           credentials=[{'id':'demo','required':True,'purpose_zh':'示例',
                                         'one_of':[{'provider':'Example','manual_zh':'external account'}]}])
        data,m=bundle.build(d);self.srv.add(data,signature=self.key.sign_package(data,m),certified=True)
        self.ensure();self.assertFalse((self.root/'llm-wiki').exists())
        # Other curated packages still install despite this one failing.
        self.assertTrue((self.root/'script-article-writing').is_dir())
        for opts in ({'skip_verify':True},{'sign_as':'Example owner'},{'replace':True},{'params':{'brand':'test'}}):
            with self.assertRaises(market.PlazaError):
                market.run_install('ebook-writing',_starter=True,harness='claude_code',
                                   workspace=str(self.root),out=io.StringIO(),**opts,**self.kw)

    def test_upgrade_phone_line_lists_installed_examples_once(self):
        import asyncio
        from types import SimpleNamespace
        from agentj.serve import Host
        self.ensure()
        host=SimpleNamespace(agent_cfg={'working_root':str(self.root)},preferences={})
        line=asyncio.run(Host._starters_line(host))
        self.assertIn('已从广场预装示例工作流',line)
        for n in starters.STARTERS:self.assertIn(n,line)
        self.assertEqual(asyncio.run(Host._starters_line(host)),'')

    def test_symlink_target_untouched_and_disable(self):
        outside=self.e.root/'outside';outside.mkdir();(self.root/'llm-wiki').symlink_to(outside)
        self.ensure();self.assertEqual(list(outside.iterdir()),[])
        with mock.patch.dict(os.environ,{'AGENTJ_NO_STARTERS':'1'}):self.assertEqual(self.ensure(),[])

    def test_verify_failure_does_not_mark_installed(self):
        with mock.patch('agentj.market.sandbox_kind',return_value='bubblewrap'), \
             mock.patch('agentj.market.run_verify',return_value=(1,'synthetic failure')):
            self.ensure()
        self.assertEqual(starters.wdb_folders(self.root),[])
        self.assertTrue(all(r['state']=='pending' for r in starters.status(self.root)))

    def test_download_bytes_corrupt_refused(self):
        self.srv.serve_bytes=b'not a valid bundle'
        self.ensure();self.assertEqual(starters.wdb_folders(self.root),[])

    def test_catalog_music_install_and_plan_check(self):
        import subprocess
        data,m=bundle.build(CATALOG/'workflows/ambient-music')
        self.srv.add(data,signature=self.key.sign_package(data,m),certified=True)
        for h in ('claude_code','codex','opencode'):
            ws=self.e.root/('music-'+h)
            out=io.StringIO()
            kw=dict(harness=h,workspace=str(ws),**self.kw)
            self.assertEqual(market.run_install('ambient-music',out=out,**kw),0)
            digest=out.getvalue().split('digest: ')[1].split()[0]
            self.assertEqual(market.run_install('ambient-music',owner_confirmed=True,digest=digest,
                                               out=io.StringIO(),**kw),0)
            target=ws/'ambient-music';task=target/'tasks/planning-fixture';task.mkdir()
            for name in ('brief.md','plan.md','prompt.md'):(task/name).write_text('Synthetic offline planning sample')
            result=subprocess.run([sys.executable,str(target/'tools/check.py'),'--task',str(task)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            (task/'prompt.md').unlink()
            bad=subprocess.run([sys.executable,str(target/'tools/check.py'),'--task',str(task)],capture_output=True)
            self.assertNotEqual(bad.returncode,0)
            (target/'.claude/agents/music-designer.md').unlink()
            drift=subprocess.run([sys.executable,str(target/'tools/check.py'),'--self-test'],capture_output=True)
            self.assertNotEqual(drift.returncode,0)

    def test_root_refresh_before_host_init_keeps_starters_pending(self):
        with mock.patch('agentj.market._state', side_effect=market.PlazaError('not initialised')):
            wizard.refresh_root(self.root,['codex'],'en')
        self.assertTrue((self.root/'.agents/skills'/wizard.SKILL_NAME/'SKILL.md').is_file())
        self.assertTrue(all(r['state']=='pending' for r in starters.status(self.root)))

    def test_wizard_refresh_calls_starters(self):
        with mock.patch('agentj.starters.ensure',return_value=[{'path':'x','status':'starter-installed'}]) as call:
            rows=wizard.refresh_root(self.root,['codex'],'en')
        call.assert_called_once_with(self.root,['codex'],'en',st=None)
        self.assertIn({'path':'x','status':'starter-installed'},rows)

    def test_p244_actual_scanner_recognizes_three_cards(self):
        path=Path(os.environ.get('P244_SCANNER',''))
        if not path.is_file():self.skipTest('set P244_SCANNER for cross-worktree contract test')
        spec=importlib.util.spec_from_file_location('agentj._p244_contract',path)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        self.ensure()
        for name in starters.STARTERS:
            card=mod.scan_folder(str(self.root),name,[])
            self.assertTrue(card['wdb']);self.assertTrue(card.get('ceo') or card.get('company'))
            self.assertTrue(card['docs']);self.assertTrue(card['agents'])

if __name__=='__main__':unittest.main()
