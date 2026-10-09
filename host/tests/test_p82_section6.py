import asyncio,json,os,tempfile,unittest,hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from agentj.bots import audit,native
from agentj.bots.provider import Provider,Result,invoke_jev
from agentj.bots.store import Store,BotError
from agentj.bots.owner import Manager

class Section6(unittest.TestCase):
    def test_jev_cannot_use_model_account_or_another_endpoint(self):
        for p in (Provider('https://example.com','model','responses','fixture-key'),Provider('https://example.com','jev','jev','fixture-key')):
            with self.assertRaisesRegex(BotError,'openrouter_required'):invoke_jev(p,[{'content':'{}'}],transport=lambda *a,**k:self.fail('network must not run'))
    def test_audit_reads_only_bot_secret_no_environment_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'bots');bid=store.create({'slug':'sample-shop'})['id']
            try:
                with patch.dict(os.environ,{'OPENROUTER_API_KEY':'operator-fixture'}):
                    with self.assertRaisesRegex(BotError,'openrouter_required'):audit.resolve(store,bid)
                p=store.secrets(bid)/audit.NAME;p.write_text('owner-fixture');p.chmod(0o600)
                resolved=audit.resolve(store,bid);self.assertEqual(resolved.key,'owner-fixture');self.assertNotIn('owner-fixture',repr(resolved)+json.dumps(resolved.public()))
                seen=[]
                r=audit.probe(resolved,lambda *a,**k:(seen.append(a) or {'answers':{'unsafe':{'noul':0.01}},'usage':{'total_tokens':7}}))
                self.assertEqual(r.usage,7);self.assertEqual(seen[0][0],'https://openrouter.ai/api/alpha/decisions')
                self.assertNotIn('owner-fixture',json.dumps(seen[0][2]))
            finally:store.close()
    def test_native_commands_disable_privileged_tools_and_keep_auth_native(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for kind in ('claude','codex','gemini','opencode'):
                env={'HOME':d,'CODEX_HOME':d};cmd=native.command(Provider('','native-model','native','',kind),root,env)
                self.assertNotIn('bypassPermissions',cmd);self.assertNotIn('--yolo',cmd)
                if kind=='claude':self.assertEqual(cmd[cmd.index('--tools')+1],'');self.assertIn('--safe-mode',cmd)
                if kind=='codex':self.assertIn('--ignore-user-config',cmd);self.assertIn('features.shell_tool=false',cmd);self.assertIn('features.view_image=false',cmd)
                if kind=='gemini':self.assertIn('--admin-policy',cmd);self.assertIn('decision = "deny"',(root/'deny.toml').read_text());self.assertEqual(json.loads(Path(env['GEMINI_CLI_TRUSTED_FOLDERS_PATH']).read_text()),{str(root):'TRUST_FOLDER'});self.assertNotIn('--skip-trust',cmd)
                if kind=='opencode':self.assertEqual(json.loads(env['OPENCODE_CONFIG_CONTENT'])['permission'],{'*':'deny'})
    def test_codex_malformed_or_unknown_provider_never_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=Provider('', '', 'native', '', 'codex');env={'CODEX_HOME':d}
            for data in ('not valid toml !!!', 'model_provider="missing"', 'model_provider="custom"\n[model_providers.custom]\nbase_url="https://user:secret@example.com"'):
                (root/'config.toml').write_text(data)
                with self.assertRaises(BotError):native.command(p,root,env)

    def test_host_storage_migration_preserves_private_history_and_budget(self):
        import shutil
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from agentj.bots.visitor import VisitorSession,issue_claim,public
        from agentj.wire import b64u
        import time
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'old';store=Store(root)
            bid=store.create({'slug':'sample-shop','enabled':True,'terms_accepted':True})['id']
            browser=X25519PrivateKey.generate();binding=hashlib.sha256(public(browser)).hexdigest()[:32]
            vid,cap=store.admit_visitor(bid,binding);store.start(bid,vid,'a'*32,'my question');store.finish(bid,vid,'a'*32,'private answer')
            call=store.reserve(bid,vid,'a'*32,50,'model');store.settle(call,17);store.close()
            shutil.copytree(root,Path(d)/'new');moved=Store(Path(d)/'new')
            try:
                resumed,newcap=moved.admit_visitor(bid,binding);self.assertEqual(resumed,vid);self.assertTrue(moved.history(bid,vid));self.assertEqual(moved.statistics(bid)[0]['charged_tokens'],17)
                with self.assertRaises(BotError):moved.authenticate(bid,vid,cap)
                issuer=Ed25519PrivateKey.generate();old=Ed25519PrivateKey.generate();new=Ed25519PrivateKey.generate()
                claim=issue_claim(issuer,bid,b64u(public(old)),b64u(public(browser)),1,int(time.time())+299)
                with self.assertRaises(BotError):VisitorSession(bid,2,new,public(issuer)).accept(claim)
            finally:moved.close()

    def test_native_holds_remaining_budget_and_unknown_usage_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'bots';store=Store(root);bid=store.create({'slug':'sample-shop'})['id']
            call=store.reserve(bid,'','',3000,'model',remaining=True)
            with self.assertRaises(BotError):store.reserve(bid,'','',1,'model',remaining=True)
            store.settle(call,14438);second=store.reserve(bid,'','',3000,'model',remaining=True)
            store.close();store=Store(root)
            try:
                self.assertEqual(store.statistics(bid)[0]['charged_tokens'],100000)
                with self.assertRaises(BotError):store.reserve(bid,'','',1,'jev')
            finally:store.close()

    def test_native_events_fail_closed_on_tool_execution_and_errors(self):
        for raw in ({'type':'item.completed','item':{'type':'command_execution'}},{'type':'turn.failed'}):
            with self.assertRaises(BotError):native.parse('codex',json.dumps(raw))
        with self.assertRaises(BotError):native.parse('opencode','{"type":"tool_use"}')
        raw='{"type":"item.completed","item":{"type":"agent_message","text":"hello"}}\n{"type":"turn.completed","usage":{"input_tokens":4,"output_tokens":3}}'
        self.assertEqual(native.parse('codex',raw),('hello',7))
    def test_native_company_tool_envelope_is_data_and_strict(self):
        p=Provider('','native-model','native','','claude')
        with patch('agentj.bots.native.run',return_value=json.dumps({'result':'{"text":"","tools":[{"name":"lookup","args":{}}]}','usage':{'input_tokens':3,'output_tokens':4}})) as run:
            result=native.invoke_native(p,[{'role':'user','content':'look up my order'}],[{'function':{'name':'lookup'}}])
            self.assertEqual(result.tools[0]['name'],'lookup');self.assertEqual(result.usage,7)
            self.assertIn('available_company_tools',run.call_args.args[1]);self.assertNotIn('look up my order',' '.join(run.call_args.args[0]))
        with patch('agentj.bots.native.run',return_value=json.dumps({'result':'{"text":"hello","tools":[],"shell":"whoami"}'})):
            with self.assertRaises(BotError):native.invoke_native(p,[])

class Activation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'XDG_CONFIG_HOME':self.tmp.name+'/config'});self.env.start()
        root=Path(self.tmp.name)/'state';root.mkdir()
        self.host=SimpleNamespace(st=SimpleNamespace(root=root,perm_dir=root/'agentperm'),channel='fixture',agent=None,agent_cfg={'kind':'claude'},elevate=SimpleNamespace(request=AsyncMock(return_value={'result':'saved'})))
        self.manager=Manager(self.host);self.bid=(await self.manager.write({'op':'create','config':{'slug':'sample-shop'}}))['bot']['id']
    async def asyncTearDown(self):
        self.manager.store.close();self.env.stop();self.tmp.cleanup()
    async def test_enable_requires_consent_valid_owner_key_and_real_probe(self):
        c=self.manager.state().get(self.bid);c.update(enabled=True,terms_accepted=True)
        with self.assertRaisesRegex(BotError,'accept_subscription_risk'):await self.manager.write({'op':'save','id':self.bid,'config':c})
        c['subscription_risk_accepted']=True
        with self.assertRaisesRegex(BotError,'openrouter_required'):await self.manager.write({'op':'save','id':self.bid,'config':c})
        p=self.manager.state().secrets(self.bid)/audit.NAME;p.write_text('owner-fixture');p.chmod(0o600)
        with patch('agentj.bots.audit.probe',side_effect=BotError('upstream_failed')):
            with self.assertRaises(BotError):await self.manager.write({'op':'save','id':self.bid,'config':c})
        self.assertFalse(self.manager.state().get(self.bid)['enabled'])
        with patch('agentj.bots.audit.probe',return_value=Result('{"allow":true}',9)) as probe:
            await self.manager.write({'op':'save','id':self.bid,'config':c});self.assertTrue(self.manager.state().get(self.bid)['enabled']);self.assertEqual(probe.call_count,1)
        self.assertEqual(self.manager.state().statistics(self.bid)[0]['charged_tokens'],4105)
    async def test_key_setup_uses_secret_card_only_name_in_receipt(self):
        reply=await self.manager.write({'op':'audit_key','id':self.bid})
        self.assertEqual(reply,{'name':audit.NAME,'stored':True});self.assertEqual(self.host.elevate.request.call_args.args[0]['kind'],'secret')
        self.host.elevate.request.return_value={'result':'denied'}
        with self.assertRaises(BotError):await self.manager.write({'op':'audit_key','id':self.bid})

class Resident(unittest.IsolatedAsyncioTestCase):
    async def test_shared_resident_rewrite_only_releases_approved_candidate(self):
        from agentj.bots.resident import guarded_reply
        seen=[]
        def model(reasons):seen.append(list(reasons));return 'unsafe' if not reasons else 'safe'
        result=await guarded_reply(model,lambda text:['refused'] if text=='unsafe' else [],attempts=2)
        self.assertEqual(result,'safe');self.assertEqual(seen,[[],['refused']])
    async def test_shared_resident_unknown_or_refused_gate_never_releases(self):
        from agentj.bots.resident import guarded_reply
        self.assertIsNone(await guarded_reply(lambda r:'candidate',lambda c:['refused'],attempts=2))
        with self.assertRaises(ValueError):await guarded_reply(lambda r:'candidate',lambda c:True)
        def unavailable(c):raise RuntimeError('fixed fixture failure')
        with self.assertRaises(RuntimeError):await guarded_reply(lambda r:'candidate',unavailable)
        with self.assertRaises(ValueError):await guarded_reply(lambda r:'candidate',lambda c:[],attempts=5)
