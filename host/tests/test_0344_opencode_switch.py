"""The native reply, the phone key card, and the original switch must describe the same route."""
import _hermetic
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import agent_opencode2, opencode_free, switch_harness


class Route(unittest.TestCase):
    def test_ark_auth_body_without_status(self):
        error = {'name': 'APIError', 'data': {'message': 'the API key or AK/SK in the request is missing or invalid'}}
        self.assertEqual(opencode_free.classify(error), 'provider_key')
        self.assertEqual(opencode_free.classify({'name':'APIError', 'data':{'statusCode':403}}), 'native_failed')

    def test_phone_and_receipt_identify_selected_provider(self):
        rec = {'reason':'provider_key', 'target':'opencode', 'model':'ark-agent-plan/doubao-seed-2.1-pro',
               'ok':False, 'status':'failed', 'at':123}
        for text in (switch_harness._say(rec,'zh'),switch_harness._say_receipt(rec,'zh')):
            self.assertIn('火山方舟 Agent Plan',text)
            self.assertIn('缺失或无效',text)

    def test_fallback_greeting_never_proves_selected_route(self):
        for actual in ({'id':'free','providerID':'opencode'}, {}, None):
            with self.assertRaises(opencode_free.ProbeError):
                opencode_free.check_reply_model('ark-agent-plan/doubao-seed-2.1-pro',actual)
        opencode_free.check_reply_model('ark-agent-plan/doubao-seed-2.1-pro',
                                        {'providerID':'ark-agent-plan','id':'doubao-seed-2.1-pro'})


class PhoneRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import os
        import time
        from pathlib import Path
        from types import SimpleNamespace
        from test_p541_special import Special
        from agentj import ark, approval_review, opencode_switch_key as repair, provenance

        rig = Special()
        rig.setUp()
        self.addCleanup(rig.doCleanups)
        self.rig, self.host = rig, rig.host
        self.addAsyncCleanup(rig.host.elevate.stop)
        self.root = Path(rig.tmp.name)
        self.target = self.root/'service.env'
        self.target.touch(mode=0o600)
        self.model = 'ark-agent-plan/doubao-seed-2.1-pro'
        self.job = 'a1' * 8
        self.enterContext(patch.dict(os.environ, {'XDG_CONFIG_HOME':str(self.root/'config')}))
        self.enterContext(patch('agentj.provider_runtime.env_path', return_value=str(self.target)))
        ark.install(plan='agent')
        self.cfg = {'dir':str(self.root/'project'), 'kind':'codex', 'fence':True}
        Path(self.cfg['dir']).mkdir()
        self.host.agent.cfg = self.cfg
        self.enterContext(patch.object(rig.st,'agent_config',return_value=self.cfg))
        row = self.host.hist_add({'k':'phone','dev':rig.a.did,'name':'owner','text':'Switch to OpenCode'},'','done')
        src = approval_review.remember(self.host, row, provenance.phone())
        self.host.agent.cur_send = SimpleNamespace(device=rig.a.did, source=provenance.phone(), approval_origin=src, turn=row['id'])
        rec = {'status':'failed','target':'opencode','from':'codex','model':self.model,'job':self.job,
               'reason':'provider_key','at':time.time()}
        self.receipt = self.enterContext(patch.object(switch_harness,'read_receipt',return_value=rec))
        self.retry = self.enterContext(patch.object(switch_harness,'host_job',return_value={'ok':True}))
        req = {'kind':'switch','target':'opencode','model':self.model}
        with patch('agentj.approval_material.verified_phone_chat',return_value=(self.host.agent,src)):
            self.origin = repair.capture(self.host, req, 1234)

    def reports(self):
        return [r['reply']['text'] for r in self.host.hist.turns.values()
                if r['src'].get('k') == 'agent' and r['src'].get('quote', {}).get('id') == self.origin.latest]

    async def test_signed_saved_key_has_main_receipt_before_retry(self):
        from test_f17 import answer
        self.host.lang = 'en'
        card = await self.open()
        def retry(*_, **kw):
            self.assertEqual(self.reports(), ['Received. Your key has been saved. Agent J will check the selected OpenCode model again.'])
            return {'ok':True}
        self.retry.side_effect = retry
        await self.host._app(self.rig.sa, answer(self.rig.a,card,True,'synthetic-phone-value'))
        await self.follow
        self.retry.assert_called_once()
        self.assertEqual(len(self.reports()),1)

    async def test_rejected_retry_reports_saved_but_unswitched(self):
        from test_f17 import answer
        self.host.lang = 'en'
        card = await self.open()
        self.retry.return_value = {'ok':False}
        await self.host._app(self.rig.sa, answer(self.rig.a,card,True,'synthetic-phone-value'))
        await self.follow
        self.assertIn('Select the OpenCode model again.', self.reports()[-1])
        self.assertNotIn('synthetic-phone-value', str(self.reports()))

    async def test_expired_key_card_returns_to_original_main_page(self):
        import time
        self.host.lang = 'en'
        card = await self.open()
        self.host.elevate.cards[card['id']]['deadline'] = time.monotonic()-1
        await self.follow
        self.assertEqual(len(self.reports()),1)
        self.assertIn('expired',self.reports()[0])
        self.retry.assert_not_called()

    async def test_declined_key_card_returns_to_original_main_page(self):
        from test_f17 import answer
        self.host.lang = 'en'
        card = await self.open()
        await self.host._app(self.rig.sa, answer(self.rig.a,card,False))
        await self.follow
        self.assertEqual(len(self.reports()),1)
        self.assertIn('declined',self.reports()[0])
        self.assertFalse(self.target.read_text())
        self.retry.assert_not_called()

    async def test_unconfirmed_directory_sync_never_reports_a_saved_switch_key(self):
        import os, stat
        from test_f17 import answer
        self.host.lang = 'en'
        card = await self.open()
        sync = os.fsync
        def fault(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('synthetic directory sync failure')
            return sync(fd)
        with patch.object(os,'fsync',side_effect=fault):
            await self.host._app(self.rig.sa, answer(self.rig.a,card,True,'synthetic-phone-value'))
            await self.follow
        self.assertIn('could not be confirmed',self.reports()[0])
        self.retry.assert_not_called()

    async def test_replaced_native_thread_gets_neither_retry_nor_receipt(self):
        from test_f17 import answer
        self.origin.native_thread = self.host.agent.tid = 'original-codex'
        card = await self.open()
        self.host.agent.tid = 'new-codex'
        await self.host._app(self.rig.sa, answer(self.rig.a,card,True,'synthetic-phone-value'))
        self.follow.cancel()
        await __import__('asyncio').gather(self.follow, return_exceptions=True)
        self.assertFalse(self.target.read_text())
        self.assertFalse(self.reports())
        self.retry.assert_not_called()

    async def open(self):
        import asyncio
        from agentj import opencode_switch_key as repair
        self.follow = asyncio.create_task(repair._follow(self.host,self.job,False,self.origin))
        card = await self.rig.wait_card('elev')
        self.assertEqual([cid for cid, msg in self.rig.sent if msg.get('t') == 'elev'],[self.rig.sa.cid])
        return card

    async def test_signed_save_retries_the_original_model_once(self):
        from test_f17 import answer
        card = await self.open()
        await self.host._app(self.rig.sb,answer(self.rig.b,card,True,'wrong-device-fixture'))
        self.assertEqual(self.target.read_text(),'')
        await self.host._app(self.rig.sa,answer(self.rig.a,card,True,'fixture-saved-through-phone'))
        await self.follow
        self.assertIn('ARK_AGENT_PLAN_API_KEY=',self.target.read_text())
        self.retry.assert_called_once()
        self.assertEqual(self.retry.call_args.args[1],{'kind':'switch','target':'opencode','model':self.model,'force':False})
        self.assertEqual(self.retry.call_args.kwargs['after'],self.job)
        self.assertNotIn('fixture-saved-through-phone',json.dumps(self.rig.sent))

    async def test_changed_job_and_device_are_not_live(self):
        from test_f17 import answer
        card = await self.open()
        original = self.receipt.return_value
        self.receipt.return_value = {**original,'job':'bb'*8}
        await self.host._app(self.rig.sa,answer(self.rig.a,card,True,'stale-fixture'))
        self.assertEqual(self.target.read_text(),'')
        self.retry.assert_not_called()
        self.receipt.return_value = original
        with patch.object(self.rig.st,'sign_key',return_value=b'other signing key'):
            self.assertFalse(self.origin.live(self.host))
        self.follow.cancel()
        import asyncio
        await asyncio.gather(self.follow,return_exceptions=True)

    async def test_revoked_while_tempfile_is_syncing_never_replaces_the_key(self):
        import asyncio
        import os
        import sys
        import threading
        if sys.platform != "linux":
            self.skipTest("Uses /proc/self/fd to stop at the fixture destination only")
        from test_f17 import answer
        from agentj import diskq
        card = await self.open()
        entered, release = threading.Event(), threading.Event()
        real_sync = diskq.fsync
        def pause(fd):
            try:
                path = os.readlink('/proc/self/fd/' + str(fd))
            except OSError:
                path = ''
            if '/.agentj-' in path and self.root == __import__('pathlib').Path(path).parent:
                entered.set()
                if not release.wait(4):
                    raise TimeoutError('test timeout')
            real_sync(fd)
        with patch.object(diskq,'fsync',side_effect=pause):
            task = asyncio.create_task(self.host._app(self.rig.sa,answer(self.rig.a,card,True,'stale')))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,3))
                self.rig.st.remove_device(self.rig.a.did)
            finally:
                release.set()
            await task
        self.assertEqual(self.target.read_text(),'')
        self.retry.assert_not_called()
        self.follow.cancel()
        await asyncio.gather(self.follow, return_exceptions=True)

    async def test_project_route_override_never_opens_ark_key_card(self):
        from agentj import opencode_switch_key as repair
        from pathlib import Path
        path = Path(self.cfg['dir'])/'opencode.json'
        path.write_text(json.dumps({'provider':{'ark-agent-plan':{'options':{'baseURL':'https://other.invalid'}}}}))
        self.assertFalse(repair._official('ark-agent-plan',self.cfg['dir'],self.model))
        await repair._follow(self.host,self.job,False,self.origin)
        self.assertFalse(self.rig.messages('elev'))
        self.retry.assert_not_called()


class IsolatedRoute(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from agentj import ark
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name).resolve()
        self.project = self.root/'work'
        self.project.mkdir()
        self.out = self.root/'scratch'
        self.out.mkdir()
        self.env = {'HOME':str(self.root),'XDG_CONFIG_HOME':str(self.root/'config'),
                    'PATH':'/usr/bin','ARK_AGENT_PLAN_API_KEY':'fixture-key',
                    'OPENAI_API_KEY':'fixture-unrelated','SECRETOTHERVALUE':'fixture-private'}
        self.model = 'ark-agent-plan/doubao-seed-2.1-pro'
        ark.install(plan='agent',environ=self.env)
        self.global_cfg = self.root/'config/opencode/opencode.json'
        self.local = self.project/'opencode.json'

    def route(self):
        from agentj import opencode_probe_route
        return opencode_probe_route.environment(self.env,self.project,self.model,str(self.out))

    def test_child_has_only_selected_secret_and_no_owner_instructions(self):
        self.local.write_text(json.dumps({'instructions':['private-instructions.md'],
                                          'plugin':['private-plugin'],'mcp':{'private':{}}}))
        child = self.route()
        self.assertEqual(child['ARK_AGENT_PLAN_API_KEY'],'fixture-key')
        self.assertFalse({'OPENAI_API_KEY','SECRETOTHERVALUE'} & child.keys())
        self.assertEqual(child['HOME'],str(self.out))
        config = (self.out/'config/opencode/opencode.json').read_text()
        for private in ('private-instructions','private-plugin','fixture-key','fixture-private'):
            self.assertNotIn(private,config)

    def test_extra_model_route_and_file_expansion_refused(self):
        raw = json.loads(self.global_cfg.read_text())
        block = raw['provider']['ark-agent-plan']
        for field,value in (('provider',{'npm':'private-package'}),('options',{'baseURL':'https://other.invalid'}),
                            ('name','{file:private-notes.md}'),('limit',{'context':{'other': 'secret'}})):
            with self.subTest(field=field):
                block['models']['doubao-seed-2.1-pro']={field:value}
                self.global_cfg.write_text(json.dumps(raw))
                with self.assertRaises(opencode_free.ProbeError):
                    self.route()

    def test_project_cannot_bind_host_key_to_its_own_endpoint(self):
        self.global_cfg.unlink()
        self.local.write_text(json.dumps({'provider':{'ark-agent-plan':{'npm':'@ai-sdk/openai-compatible',
                             'options':{'baseURL':'https://elsewhere.invalid','apiKey':'{env:SECRETOTHERVALUE}'}}}}))
        with self.assertRaises(opencode_free.ProbeError):
            self.route()

    def test_special_files_and_links_cannot_supply_provider_settings(self):
        import os
        os.mkfifo(self.local)
        with self.assertRaises(opencode_free.ProbeError):
            self.route()
        self.local.unlink()
        self.local.symlink_to(self.global_cfg)
        with self.assertRaises(opencode_free.ProbeError):
            self.route()


class FreshSelection(IsolatedRoute):
    def test_new_home_private_probe_then_additive_install(self):
        from agentj import ark_connect, opencode_probe_route
        self.global_cfg.unlink()
        self.global_cfg.write_text('{"provider":{"other":{"options":{"apiKey":"fixture-owner-setting"}}}}')
        original = self.global_cfg.read_bytes()
        child = opencode_probe_route.environment(self.env,self.project,self.model,str(self.out),agent_plan=True)
        shadow = __import__('pathlib').Path(child['XDG_CONFIG_HOME'])/'opencode/opencode.json'
        provider = json.loads(shadow.read_text())['provider']['ark-agent-plan']
        self.assertEqual(provider['options'], {'apiKey':'{env:ARK_AGENT_PLAN_API_KEY}','baseURL':'https://ark.cn-beijing.volces.com/api/plan/v3'})
        target = self.global_cfg.with_suffix('.jsonc')
        self.assertFalse(target.exists())
        ark_connect.install(self.env,self.project,self.model)
        self.assertEqual(self.global_cfg.read_bytes(),original)
        self.assertEqual(target.stat().st_mode & 0o777,0o600)
        self.assertNotIn('fixture-owner-setting',target.read_text())
        self.assertFalse(list(self.global_cfg.parent.glob('*.agentj-*')))
        self.assertIsNotNone(ark_connect.official(self.env,self.project,self.model)[0])

    def test_wrong_route_preserved_and_not_probed_as_official(self):
        from agentj import ark_connect
        raw = json.loads(self.global_cfg.read_text())
        raw['provider']['ark-agent-plan']['options']['baseURL']='https://other.invalid'
        self.global_cfg.write_text(json.dumps(raw))
        with self.assertRaisesRegex(opencode_free.ProbeError,'ark_route_conflict'):
            ark_connect.install(self.env,self.project,self.model)
        self.assertEqual(json.loads(self.global_cfg.read_text()),raw)

    def test_integrations_have_plan_specific_key_guidance(self):
        from agentj import opencode_switch_key
        for provider in ('volcengine','volcengine-coding-plan','byteplus','byteplus-modelark'):
            message = opencode_switch_key.explanation({'model':provider+'/doubao'},'zh')
            self.assertIn('Agent Plan 专属密钥不能用于内置',message)
            self.assertIn(provider,message)


class FirstSetup(unittest.IsolatedAsyncioTestCase):
    async def test_original_setup_card_is_the_only_approval_that_can_retry_first_connection(self):
        import asyncio
        import os
        import tempfile
        import time
        from pathlib import Path
        from agentj import ark_connect, cli, elevate, opencode_switch_key as repair
        from test_l1 import _state, _host, _ready
        from test_f17 import enroll, answer
        from _p541_device import Phone
        from types import SimpleNamespace
        td=tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root=Path(td.name)
        work=root/'work'
        work.mkdir()
        self.enterContext(patch.dict(os.environ, {'XDG_CONFIG_HOME':str(root/'cfg')}))
        self.enterContext(patch('agentj.working_root.select',return_value=work))
        target=root/'service.env'
        target.touch(mode=0o600)
        self.enterContext(patch('agentj.provider_runtime.env_path',return_value=str(target)))
        st=_state(td.name)
        sent=[]
        host=_host(st,sent)
        self.addAsyncCleanup(host.elevate.stop)
        self.assertIsNone(host.agent)
        model='ark-agent-plan/doubao-seed-2.1-pro'
        request={'kind':'switch','target':'opencode','model':model}
        args=SimpleNamespace(mode='switch',target='opencode',preset='ark',model='doubao-seed-2.1-pro')
        with patch('agentj.fence.inside',return_value=False):
            self.assertFalse(cli._first_connect(st,args))
        launch=Mock()
        self.assertEqual(switch_harness.host_job(st,request,launch)['error'],'first_pair')
        phone=enroll(st,Phone(st))
        session=_ready(host,phone)
        with patch('agentj.ctl_auth.refusal',return_value=None), patch('agentj.ctl_auth.has_tty',return_value=True):
            origin=repair.capture(host,request,1234)
        self.assertIsNotNone(origin)
        other=enroll(st,Phone(st,name='another'))
        self.assertEqual(switch_harness.host_job(st,request,launch,origin=origin)['error'],'first_device')
        st.remove_device(other.did)
        def handle(st,argv,**kw):
            job=argv[argv.index('--job')+1]
            def gate(only,model):
                return {'harnesses':[{'name':'opencode','installed':True,'callable':bool(target.read_text()),'probe_reason':'provider_key'}]}
            ctl=lambda req:{'ok':True,'at':1,'agent':'opencode','agent_status':'idle','agent_said_at':2}
            switch_harness.worker(st,'opencode',model,job=job,probe=gate,ctl=ctl,restart=lambda:None)
        first=switch_harness.host_job(st,request,handle,origin=origin)
        self.assertEqual(switch_harness.read_receipt(st)['reason'],'provider_key')
        self.assertFalse(st.agent_config())
        original=switch_harness.host_job
        def retry(st,req,**options):
            return original(st,req,handle,**options)
        with patch.object(switch_harness,'host_job',side_effect=retry):
            follow=asyncio.create_task(repair._follow(host,first['job'],False,origin))
            for _ in range(150):
                card=next((m for _,m in sent if m['t']=='elev'),None)
                if card or follow.done():break
                await asyncio.sleep(.01)
            self.assertIsNotNone(card)
            pending=host.elevate.cards[card['id']]
            unrelated={**pending,'id':'other','fut':asyncio.get_running_loop().create_future()}
            unrelated.pop('_switch_input')
            host.elevate.cards['other']=unrelated
            host.elevate._saved_elsewhere(unrelated,{'result':'saved'})
            self.assertFalse(pending['fut'].done())
            await host._app(session,answer(phone,card,True,'fixture-in-phone'))
            self.assertFalse(unrelated['fut'].done())
            host.elevate.cards.pop('other')
            await asyncio.wait_for(follow,2)
        self.assertEqual(host.hist.get(origin.latest)['src']['k'], 'host')
        receipts = [r for r in host.hist.turns.values() if r['src'].get('k') == 'agent'
                    and r['src'].get('quote', {}).get('id') == origin.latest]
        self.assertEqual(len(receipts), 1)
        self.assertTrue('已保存' in receipts[0]['reply']['text'] or 'has been saved' in receipts[0]['reply']['text'])
        self.assertEqual(st.agent_config()['model'],model)
        self.assertEqual(switch_harness.read_receipt(st)['status'],'ok')
        self.assertIsNotNone(ark_connect.official(os.environ,work,model)[0])
