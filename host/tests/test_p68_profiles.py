import _hermetic
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from agentj import provider_profiles as p
from agentj.opencode_provider import ProviderError

class Profiles(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.env=patch.dict(os.environ,HOME=str(self.home),XDG_CONFIG_HOME=str(self.home/'.config'),CODEX_HOME=str(self.home/'.codex'))
        self.env.start();self.addCleanup(self.env.stop);self.addCleanup(self.tmp.cleanup)
        select=patch.object(p,'selection',return_value={'ok':True,'applicable':False,'model':None});select.start();self.addCleanup(select.stop)
        self.key='dummy-test-profile-key'
    def save(self, kind, api='openai'):
        r=p.define('example',kind,'https://provider.example/v1','example-model',api)
        p.atomic(Path(r['key_destination'][5:]),self.key.encode())
        return p.target({'harness':kind})
    def test_merge_switch_restore_each_native_harness(self):
        for kind in ('claude','codex','opencode'):
            with self.subTest(kind=kind):
                # Separate profile folder for each native harness.
                with patch.object(p,'root',return_value=self.home/('profiles-'+kind)):
                    target=self.save(kind,'anthropic' if kind=='claude' else 'openai')
                    old=(b'# retained comment\napproval_policy = "untrusted"\n[profiles.research]\nsandbox_mode = "read-only"\n' if kind=='codex'
                         else json.dumps({'unrelated':{'keep':True},'env':{'KEEP':'x'}}).encode())
                    p.atomic(target,old)
                    r=p.use('example',verify=lambda c,k:self.assertEqual(k,self.key))
                    self.assertTrue(r['verified']);self.assertEqual(target.stat().st_mode&0o777,0o600)
                    if kind=='codex':
                        data=tomllib.loads(target.read_text());self.assertEqual(data['approval_policy'],'untrusted')
                        self.assertEqual(data['profiles']['research']['sandbox_mode'],'read-only')
                        self.assertIn('# retained comment',target.read_text())
                        self.assertFalse(data['model_providers']['example']['requires_openai_auth'])
                    else:self.assertTrue(json.loads(target.read_text())['unrelated']['keep'])
                    self.assertNotIn(self.key,json.dumps(p.listing()))
                    p.restore('example');self.assertEqual(target.read_bytes(),old)
    def test_failed_real_probe_does_not_touch_config(self):
        target=self.save('codex');p.atomic(target,b'model="old"\n')
        def fail(c,k):raise ProviderError('provider_probe_failed')
        with self.assertRaises(ProviderError):p.use('example',verify=fail)
        self.assertEqual(target.read_bytes(),b'model="old"\n')
    def test_restore_preserves_later_edits(self):
        target=self.save('codex');p.atomic(target,b'model="old"\n')
        p.use('example',verify=lambda *a:None);target.write_bytes(target.read_bytes()+b'\n# owner later edit\n')
        with self.assertRaisesRegex(ProviderError,'changed_since_switch'):p.restore('example')
        self.assertIn(b'# owner later edit',target.read_bytes())
    def test_key_symlink_and_world_readable_key_rejected(self):
        target=self.save('codex');key=p.profile('example')/'key';key.chmod(0o644)
        with self.assertRaises(ProviderError):p.use('example',verify=lambda *a:None)
        key.unlink();key.symlink_to(self.home/'foreign')
        with self.assertRaises(OSError):p.use('example',verify=lambda *a:None)
        self.assertFalse(target.exists())
    def test_multiple_saved_profiles_and_existing_model_provider_comments(self):
        target=self.save('codex');p.atomic(target,b'# root\n[model_providers.example]\n# custom field\ncustom = 42\n')
        p.use('example',verify=lambda *a:None)
        self.assertIn('# custom field',target.read_text())
        p.define('second','opencode','https://second.example/v1','other')
        self.assertEqual([r['id'] for r in p.listing()],['example','second'])

    def test_fenced_cli_uses_host_socket_without_reading_private_key(self):
        import asyncio,subprocess,sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from agentj import elevate,fence
        from agentj.state import State
        target=self.save('codex');work=self.home/'work';work.mkdir()
        state_env=patch.dict(os.environ,AGENTJ_STATE_DIR=str(self.home/'.local/state/agentj'))
        state_env.start();self.addCleanup(state_env.stop)
        st=State();st.init(force=True)
        host=SimpleNamespace(st=st)
        server=elevate.Elevator(host)
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        argv=fence.wrap(st,[sys.executable,'-m','agentj.cli','provider','profile','use','example'],str(work))
        async def flow():
            await server.start()
            try:
                with patch.object(p,'probe',return_value=None) as verified:
                    run=await asyncio.to_thread(subprocess.run,argv,cwd=work,env=env,capture_output=True,text=True,timeout=30)
                    self.assertEqual(run.returncode,0,run.stderr+run.stdout)
                    self.assertTrue(json.loads(run.stdout)['verified'])
                    self.assertNotIn(self.key,run.stdout+run.stderr)
                    verified.assert_called_once()
            finally:await server.stop()
        asyncio.run(flow())
        self.assertEqual(tomllib.loads(target.read_text())['model'],'example-model')

    def test_working_configuration_turn_schedules_next_turn_without_changing_mode(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import Mock
        from agentj.state import State
        from agentj.serve import Host
        work=self.home/'work';work.mkdir()
        state_env=patch.dict(os.environ,AGENTJ_STATE_DIR=str(self.home/'.local/state/agentj'))
        state_env.start();self.addCleanup(state_env.stop)
        st=State();st.init(force=True);st.set_agent_config('codex',str(work))
        before=st.agent_config()
        host=Host(st,events='quiet',read_stdin=False)
        host.agent=SimpleNamespace(owns_harness=lambda:True,request_restart=Mock(return_value='next_turn'))
        host.eff_status=Mock(return_value='working')
        async def flow():
            server=await asyncio.start_unix_server(host.on_ctl,path=str(st.sock_path))
            async def send(model):
                r,w=await asyncio.open_unix_connection(str(st.sock_path))
                w.write((json.dumps({'cmd':'provider_model','kind':'codex','write':True,'model':model})+'\n').encode());await w.drain()
                result=json.loads(await r.readline());w.close();await w.wait_closed();return result
            try:
                self.assertTrue((await send('gpt-6.1-sol'))['ok'])
                host.eff_status.return_value='waiting'
                self.assertEqual((await send('gpt-6-astra'))['error'],'busy')
            finally:server.close();await server.wait_closed()
        asyncio.run(flow())
        after=st.agent_config();self.assertEqual(after.pop('model'),'gpt-6.1-sol');before.pop('model')
        self.assertEqual(after,before);host.agent.request_restart.assert_called_once_with('provider')
