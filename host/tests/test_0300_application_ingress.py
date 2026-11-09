"""Prepared only: real paired Host frame and actual UDS kernel peer; no paid model."""
import _hermetic
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from agentj import application_ingress as entry, codex_procs, provenance, serve
import test_uv_list as uv_fixture


class ApplicationFrames(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = uv_fixture.ListApproval.asyncSetUp

    async def frame(self, callback, obj=None):
        with patch.object(self.h, '_app_frame', side_effect=callback):
            with provenance.paired_request(lambda: self.h._paired_live(self.s)):
                await serve.Host._app(self.h, self.s, obj or {'t': 'fixture-only'})

    async def test_unframed_flags_and_tools_are_not_ingress(self):
        result = await self.h.ask('agentj tasks migrate', {'owner': True}, grant_info=True, _host_card=True)
        self.assertEqual(result['reason'], 'application_ingress_unverified')
        self.assertFalse(self.h.asks)

    async def test_real_paired_frame_has_no_second_card_or_fictitious_proof(self):
        async def control(s, obj):
            receipt = {}
            result = await self.h.ask('agentj tasks migrate', {'ids': ['fixture']}, grant_info=True, _receipt=receipt)
            self.assertEqual(result['behavior'], 'allow')
            self.assertFalse(set(result) & {'grant_info', 'device', 'sig', 'rid', 'uv'})
            self.assertEqual(receipt, {'reason': 'harness', 'approval_card_shown': False})
            self.assertFalse(self.h.asks)
        await self.frame(control)
        self.assertIsNone(entry.current(self.h))

    async def test_child_task_and_changed_device_session_cannot_borrow_frame(self):
        async def control(s, obj):
            self.assertTrue(entry.allowed(self.h))
            self.assertFalse(await asyncio.create_task(self.child_allowed()))
            self.h.sessions.pop(s.cid)
            self.assertFalse(entry.allowed(self.h))
        await self.frame(control)

    async def child_allowed(self):
        return entry.allowed(self.h)

    async def test_signing_identity_and_stop_invalidate_current_frame(self):
        async def control(s, obj):
            self.assertTrue(entry.allowed(self.h))
            before = self.h.st.sign_key(s.device)
            with patch.object(self.h.st, 'sign_key', return_value=b'changed'):
                self.assertFalse(entry.allowed(self.h))
            self.assertEqual(self.h.st.sign_key(s.device), before)
            with patch.object(self.h, 'stopped', return_value=True):
                self.assertFalse(entry.allowed(self.h))
        await self.frame(control)

    async def test_list_uv_and_native_signed_tap_are_not_skipped(self):
        for tool, inp, native, uv in (
                ('agentj secret request', {'name': 'fixture-only'}, False, True),
                ('Bash', {'command': 'date'}, True, False)):
            with self.subTest(tool=tool):
                async def control(s, obj):
                    result = await self.h.ask(tool, inp, _harness_permission=native)
                    self.assertEqual(result['behavior'], 'allow')
                task = asyncio.create_task(self.frame(control))
                try:
                    for _ in range(100):
                        if self.h.asks: break
                        await asyncio.sleep(.005)
                    self.assertFalse(task.done())
                    ask = next(iter(self.h.asks.values()))
                    card = self.h._ask_msg(ask, self.s)
                    self.assertEqual(ask.require_passkey, uv)
                    answer = self.ph.answer(card, True)
                    if uv:
                        no_uv = dict(answer); no_uv.pop('fa')
                        await self.h._answer(self.s, no_uv)
                        self.assertFalse(ask.fut.done())
                    await self.h._answer(self.s, answer)
                    await task
                finally:
                    if not task.done(): task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_actual_ctl_kernel_peer_two_distinct_frames(self):
        path = Path(self.st.root)/'entry-test.sock'
        seen = []
        previous = []
        from agentj import plaza_perms
        original_controller = plaza_perms.serve_card
        async def controller(host, req, emit=None):
            current = entry.current(host)
            self.assertIsNotNone(current)
            if previous:
                self.assertTrue(previous[-1].closed)
                self.assertFalse(previous[-1].live())
            previous.append(current)
            binding = dict(host.agent.session)
            host.agent.session['sessionId'] = 'different-session'
            self.assertFalse(current.live())
            host.agent.session.update(binding)
            session = host.agent.session
            host.agent.session = dict(session)
            self.assertFalse(current.live())
            host.agent.session = session
            directory = host.agent.cfg['dir']
            host.agent.cfg['dir'] = str(Path(directory)/'changed')
            self.assertFalse(current.live())
            host.agent.cfg['dir'] = directory
            with patch.object(entry, '_process', return_value='reused-pid'):
                self.assertFalse(current.live())
            self.assertTrue(current.live())
            result = await original_controller(host, req, emit)
            seen.append(result)
            return result
        server = await asyncio.start_unix_server(self.h.on_ctl, path=str(path))
        inner = """import json,socket,sys
s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);f=s.makefile('rwb')
for _ in range(2):
 req={'cmd':'plaza_perm','name':'fixture-package','version':'1.0.0','sha256':'0'*64,'items':[{'kind':'secret','name':'SERVICE_KEY'}]}
 f.write((json.dumps(req)+'\\n').encode());f.flush();r=json.loads(f.readline());assert r['ok'],r
s.close()
"""
        script = "import subprocess,sys;sys.stdin.readline();sys.exit(subprocess.call([sys.executable,'-c',sys.argv[2],sys.argv[1]]))"
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(path), inner, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        old = self.h.agent
        self.h.agent = NS(kind='claude',session={'sessionId':'entry-test','pid':proc.pid,'procStart':codex_procs.start_token(proc.pid)},
                          native_source=provenance.system(),research=False,phone_turn=False,cfg={'kind':'claude','dir':str(self.st.root)})
        self.h.desktop_turn = self.origin['message_id']
        try:
            with patch('agentj.plaza_perms.serve_card', side_effect=controller):
                await asyncio.sleep(.03)
                proc.stdin.write(b'go\n');await proc.stdin.drain()
                out, err = await asyncio.wait_for(proc.communicate(), 10)
                self.assertEqual(proc.returncode, 0, err.decode())
            self.assertEqual(len(seen), 2)
            self.assertTrue(all('grant_info' not in result for result in seen))
            for _ in range(100):
                if previous[-1].closed: break
                await asyncio.sleep(.005)
            self.assertTrue(previous[-1].closed)
        finally:
            self.h.agent = old
            if proc.returncode is None:
                proc.kill();await proc.wait()
            server.close();await server.wait_closed()

    async def test_startup_migration_is_proposal_not_an_execution(self):
        from agentj import sysmigrate
        from unittest.mock import Mock
        import os
        planned = [{'id': 'fixture', 'title': 'fixture', 'result': 'plan'}]
        migrate = Mock(return_value=planned)
        with patch.dict(os.environ, {'AGENTJ_TEST_MIGRATE_CARD': '1'}), \
                patch.object(self.h, 'agent_cfg', {'kind':'claude','dir':str(self.st.root)}), \
                patch.object(sysmigrate, 'candidates', return_value=planned), patch.object(sysmigrate, 'migrate', migrate):
            self.assertEqual(await sysmigrate.offer(self.h, plat='linux'), 'proposal')
        migrate.assert_not_called()
        self.assertFalse(self.h.asks)

    async def test_authenticated_migration_runs_once_without_device_proof(self):
        from agentj import sysmigrate
        executed = []
        def migrate(st, ids=None, dry=False, *args, **kwargs):
            if not dry: executed.append(tuple(ids))
            return [{'id': 'fixture', 'result': 'plan' if dry else 'migrated'}]
        async def control(s, obj):
            with patch.object(sysmigrate, 'migrate', side_effect=migrate):
                result = await sysmigrate.ctl(self.h, {'ids':['fixture']})
            self.assertTrue(result['ok'], result)
            self.assertFalse(self.h.asks)
            self.assertFalse(set(result) & {'grant_info','device','sig','rid','uv'})
        await self.frame(control)
        self.assertEqual(executed, [('fixture',)])

    async def test_goal_activation_is_state_not_a_durable_approval_grant(self):
        from agentj import goal
        async def control(s, obj):
            result = await self.h.goals.propose({'goal':'fixture-only','deadline':'2h',
                'grants':[{'cat':'send','what':'synthetic publish','match':['git push'],'max':2}]})
            self.assertEqual(result['result'], 'active')
            stored = goal.load(self.st)
            self.assertEqual(stored['grants'], [])
            self.assertNotIn('grant', stored)
            self.assertNotIn('preauth', stored)
            self.assertFalse(self.h.asks)
        await self.frame(control)

    async def test_google_and_plaza_ordinary_ack_are_not_signature_proofs(self):
        from agentj import google_api, plaza_perms
        async def control(s, obj):
            for tool in google_api.TOOLS.values():
                result = await google_api.card(self.h, self.st, tool, 'Synthetic nonsecret request')
                self.assertTrue(result['ok'], result)
                self.assertFalse(set(result) & {'grant_info','device','rid','sig','uv'})
            result = await plaza_perms._signed_card(self.h, 'fixture', {'items':[]}, None)
            self.assertTrue(result['ok'], result)
            self.assertFalse(set(result) & {'grant_info','device','rid','sig','uv'})
            self.assertFalse(self.h.asks)
        await self.frame(control)

    async def test_worker_handoff_rechecks_frame_after_revocation(self):
        import threading
        from unittest.mock import Mock
        entered = threading.Event()
        effect = Mock()
        async def control(s, obj):
            # Revoke in the actual IO handoff before effect's final live check.
            original = asyncio.to_thread
            async def handoff(callback, *args, **kwargs):
                self.h.sessions.pop(s.cid)
                entered.set()
                return await original(callback, *args, **kwargs)
            with patch('asyncio.to_thread', side_effect=handoff):
                with self.assertRaises(entry.Expired):
                    await entry.run(self.h, effect)
        await self.frame(control)
        self.assertTrue(entered.is_set())
        effect.assert_not_called()

    async def test_hook_and_external_mcp_record_real_actor_without_device_grant(self):
        from agentj.hook_service import HookService
        service = HookService(self.h)
        async def control(s, obj):
            with patch.object(service, 'ensure_net'), patch.object(service, 'changed'):
                result = await service._create({'hook':{'preset':'bearer','name':'fixture','life':'resident'}})
                self.assertTrue(result['ok'], result.get('why'))
                hook = service.store.get(result['hook']['id'])
                self.assertEqual(hook['state'], 'active')
                self.assertEqual(hook['approved']['actor'], 'paired')
                self.assertNotIn('device', hook['approved'])
                result = await service.mcp.manage('mcp-create', {'name':'fixture-mcp','origins':['https://example.test']})
                self.assertTrue(result['ok'], result.get('why'))
                connector = next(iter(service.mcp.doc['connectors'].values()))
                linked = service.store.get(connector['id'])
                self.assertEqual(linked['approved']['actor'], 'paired')
                self.assertNotIn('device', linked['approved'])
                self.assertFalse(self.h.asks)
                # No second ordinary card; actual secret disclosure still owns
                # its separate request-bound UV pickup controller.
                from agentj import secret_out, passkey
                box = self.h.elevate.out
                self.assertEqual(len(box.cards), 1)
                card = next(iter(box.cards.values()))
                shown = box.card_msg(card, s)
                self.assertNotIn('value', shown)
                self.assertNotIn('data', shown)
                request = {'t':'secret_out_take','id':card['id'],'n':card['nonce']}
                await box.on_phone(s, request)
                self.assertFalse(any(obj.get('t') == 'secret_out_val' for _, obj in self.sent))
                challenge = passkey.elevate_challenge(self.h.channel,s.device,card['id'],secret_out.KIND,card['nonce'],card['digest'])
                request['fa'] = self.ph.auth.assert_(challenge,b'0',nonce=b'0')
                await box.on_phone(s, request)
                self.assertEqual(sum(obj.get('t') == 'secret_out_val' for _, obj in self.sent), 1)
                self.assertFalse(box.cards)
        await self.frame(control)

    async def test_native_guest_research_reused_pid_and_changed_anchor_are_refused(self):
        from unittest.mock import Mock
        import os
        writer = Mock(is_closing=lambda:False)
        old = self.h.agent
        pid = os.getpid()
        token = codex_procs.start_token(pid)
        self.h.agent = NS(session={'pid':pid,'procStart':token}, native_source=provenance.system(),research=False)
        # No native decision is mocked; the boundary is real PID/start evidence.
        try:
            with entry.native(self.h, writer, b'fixture', pid, os.getuid()):
                self.assertFalse(entry.allowed(self.h), 'ancestry without a Host-held invocation is insufficient')
                self.h.agent.native_source=provenance.email_guest('guest@example.test')
                self.assertFalse(entry.allowed(self.h))
                self.h.agent.native_source=provenance.system()
                self.h.agent.research=True
                self.assertFalse(entry.allowed(self.h))
                self.h.agent.research=False
                self.h.agent.session={'pid':pid,'procStart':'different'}
                self.assertFalse(entry.allowed(self.h))
            self.h.agent.session={'pid':pid,'procStart':token}
            with entry.native(self.h, writer, b'fixture', pid, os.getuid()):
                with patch.object(entry, '_process', return_value='reused-pid'):
                    self.assertFalse(entry.allowed(self.h))
        finally:
            self.h.agent=old

    async def test_actual_elevate_google_route_keeps_secret_egress_guard(self):
        from agentj import google_api, peer_guard
        from unittest.mock import Mock
        path = Path(self.st.root)/'google-entry-test.sock'
        server = await asyncio.start_unix_server(self.h.elevate.on_client, path=str(path))
        inner = """import json,socket,sys
req={'t':'google_api','op':'gmail_send','to':['receiver@example.test'],'subject':'fixture','body':sys.argv[2]}
s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall((json.dumps(req)+'\\n').encode());r=json.loads(s.makefile().readline())
assert r.get('ok') if sys.argv[2]=='ordinary fixture' else r.get('why')=='secret',r
"""
        script = "import subprocess,sys;sys.stdin.readline();a=[sys.executable,'-c',sys.argv[2],sys.argv[1]];sys.exit(subprocess.call(a+['ordinary fixture']) or subprocess.call(a+['fixture-egress-value-23894756']))"
        proc = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(path), inner, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        old = self.h.agent
        self.h.agent = NS(kind='claude', session={'sessionId':'entry-google','pid':proc.pid,'procStart':codex_procs.start_token(proc.pid)},
                          native_source=provenance.system(), research=False, phone_turn=False,cfg={'kind':'claude','dir':str(self.st.root)})
        self.h.desktop_turn = self.origin['message_id']
        effect = Mock(return_value={'ok':True})
        try:
            # Only the connected-account/external operation and index's input
            # source are fixtures; the actual SecretIndex scanner and ingress run.
            index = peer_guard.SecretIndex(['fixture-egress-value-23894756'])
            with patch.object(google_api, 'ready', return_value=True), patch.object(google_api, 'run', effect), \
                    patch.object(peer_guard.SecretIndex, 'build', return_value=index):
                await asyncio.sleep(.03)
                proc.stdin.write(b'go\n');await proc.stdin.drain()
                _, err = await asyncio.wait_for(proc.communicate(), 10)
                self.assertEqual(proc.returncode, 0, err.decode())
            effect.assert_called_once()
            self.assertFalse(self.h.asks)
        finally:
            self.h.agent = old
            if proc.returncode is None:
                proc.kill();await proc.wait()
            server.close();await server.wait_closed()

    async def test_actual_hook_rotation_uv_binds_target_and_store_generation(self):
        from agentj import hooks
        from agentj.hook_service import HookService
        service = HookService(self.h)
        new_key = b'fixture-rotation-new-key'
        for changed in ('none', 'hook', 'keys'):
            with self.subTest(changed=changed):
                hook = hooks.build({'preset':'bearer','name':'fixture-'+changed,'life':'resident'}, 'main', service.clock())
                hook['state'] = 'active'
                service.store.add(hook, b'fixture-old-key')
                target = 'file:'+str(Path(self.st.root).parent/'fixture-unused.secret')
                with patch.object(service, '_to_agent'):
                    service._rotate_card(hook, new_key, 0, target)
                    pending = list(service.cards)
                    try:
                        for _ in range(100):
                            if self.h.asks: break
                            await asyncio.sleep(.005)
                        ask = next(iter(self.h.asks.values()))
                        self.assertTrue(ask.require_passkey)
                        self.assertEqual(ask.tool, 'agentj secret request')
                        self.assertEqual(ask.tool_input['destination'], target)
                        self.assertNotIn(new_key.decode(), json.dumps(ask.tool_input))
                        if changed == 'hook': hook['name']='modified while awaiting UV'
                        if changed == 'keys': service.store.set_key(hook['id'], b'fixture-other-key')
                        await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask,self.s),True))
                        await asyncio.gather(*pending)
                        keys = service.store.keys(hook['id'])
                        if changed == 'none': self.assertEqual(keys[0], new_key)
                        else: self.assertNotIn(new_key, keys)
                    finally:
                        for task in pending:
                            if not task.done(): task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)


    async def test_invalid_json_refuses_before_inventory_and_hash_without_echo(self):
        from agentj import approvals, approval_review, uv_list
        import math
        cycle = {}; cycle['self'] = cycle
        sentinel = 'private-fixture-never-echoed'
        cases = [
            ('Bash', {'amount':math.nan}, {}),
            ('mcp__shell__execute_command', {'parameters':{sentinel}}, {}),
            ('agentj secret request', cycle, {}),
            ('agentj tasks migrate', {'bad':math.inf}, {}),
            ('Bash', {'command':'date'}, {'task_actions':[{'bad':{sentinel}}]}),
            ('Bash', {'command':'date'}, {'task_target':cycle}),
            ('Bash', {'value':'\ud800'}, {}),
        ]
        for tool, inp, extra in cases:
            with self.subTest(tool=tool, extra=tuple(extra)):
                receipt = {}
                with patch.object(uv_list, 'required', side_effect=AssertionError('invalid input reached inventory')), \
                        patch.object(approvals, 'input_digest', side_effect=AssertionError('invalid input reached hash')):
                    result = await self.h.ask(tool, inp, _receipt=receipt, **extra)
                self.assertEqual(result['reason'], 'invalid_input')
                self.assertEqual(result['behavior'], 'deny')
                self.assertFalse(self.h.asks)
                self.assertEqual(receipt, {'reason':'invalid_input','approval_card_shown':False})
                self.assertNotIn(sentinel, json.dumps(result))
                self.assertNotIn(sentinel, self.st.log_path.read_text())
        # The complete final display is signed; raw input/hash stay canonical.
        inp = {'command':'printf ok','note':'Unicode ✓','values':[None,True,1,-0.0]}
        task = asyncio.create_task(self.h.ask('Bash', inp, _harness_permission=True))
        try:
            for _ in range(100):
                if self.h.asks: break
                await asyncio.sleep(.005)
            ask = next(iter(self.h.asks.values()))
            self.assertEqual(ask.summary, 'Bash\n{"command": "printf ok", "note": "Unicode ✓", "values": [null, true, 1, -0.0]}')
            self.assertEqual(self.h._ask_msg(ask,self.s)['envelope']['details'], ask.summary)
            self.assertEqual(ask.input_sha, approvals.input_digest(inp))
            await self.h._answer(self.s, self.ph.answer(self.h._ask_msg(ask,self.s), True))
            self.assertEqual((await task)['behavior'], 'allow')
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_display_worker_cannot_rebind_native_subject_but_settings_is_independent(self):
        from agentj import approval_review
        from agentj.list_approval import NativeContext, SettingsContext
        for change in ('main_turn','gone','native_process','native_session','settings_main','settings_session','settings_stop'):
            with self.subTest(change=change):
                entered, release = asyncio.Event(), asyncio.Event()
                original = asyncio.to_thread
                async def delayed(callback, *args, **kwargs):
                    entered.set(); await release.wait()
                    return await original(callback, *args, **kwargs)
                gone = asyncio.get_running_loop().create_future()
                inp = {'summary':'Synthetic official login'} if change.startswith('settings_') else {'command':'date'}
                extra = {'_harness_permission':True}
                context = None
                adapter = NS(host=self.h,kind='codex',proc=object(),tid='fixed-thread',cfg={'dir':str(self.st.root)})
                if change.startswith('native_'):
                    extra['_native_context'] = NativeContext.capture(adapter)
                if change.startswith('settings_'):
                    context = SettingsContext.capture(self.h,self.h.auth_rescue,self.s,inp)
                    extra = {'_host_card':True,'_settings_context':context,'_settings_session':self.s,'only':self.s.device}
                original_session = self.h.sessions.get(self.s.cid)
                task = None
                try:
                    with patch('asyncio.to_thread',side_effect=delayed):
                        task = asyncio.create_task(self.h.ask('AuthRescue' if context else 'Bash',inp,gone=gone,**extra))
                        await asyncio.wait_for(entered.wait(),1)
                        old_agent, old_proc = self.h.agent, getattr(self.h.agent,'proc',None)
                        if change in ('main_turn','settings_main'):
                            self.h.desktop_input('Next unrelated genuine synthetic owner turn')
                            self.assertIs(self.h.agent,old_agent)
                            self.assertIs(getattr(self.h.agent,'proc',None),old_proc)
                        elif change == 'gone': gone.set_result(None)
                        elif change == 'native_process': adapter.proc=object()
                        elif change == 'native_session': adapter.tid='replaced-thread'
                        elif change == 'settings_session': self.h.sessions[self.s.cid]=NS()
                        elif change == 'settings_stop': self.h.estop['on']=True
                        release.set()
                        if change == 'settings_main':
                            for _ in range(100):
                                if self.h.asks or task.done(): break
                                await asyncio.sleep(.005)
                            self.assertTrue(self.h.asks)
                            ask = next(iter(self.h.asks.values()))
                            self.assertTrue(ask.require_passkey)
                            self.assertIn('Remote settings',ask.review_origin['initiator'])
                            await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
                            self.assertEqual((await asyncio.wait_for(task,2))['behavior'],'allow')
                        else:
                            result = await asyncio.wait_for(task,2)
                            self.assertEqual(result['reason'],'authority_changed')
                            self.assertEqual(result['behavior'],'deny')
                            self.assertFalse(self.h.asks)
                finally:
                    release.set()
                    self.h.estop['on']=False
                    self.h.sessions[self.s.cid]=original_session
                    if context: context.discard()
                    if task and not task.done(): task.cancel()
                    if task: await asyncio.gather(task,return_exceptions=True)

    async def test_ready_signed_answer_and_gone_same_tick_cannot_execute(self):
        gone=asyncio.get_running_loop().create_future()
        task=asyncio.create_task(self.h.ask('Bash',{'command':'date'},gone=gone,_harness_permission=True))
        try:
            for _ in range(100):
                if self.h.asks or task.done(): break
                await asyncio.sleep(.005)
            ask=next(iter(self.h.asks.values()))
            await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
            self.assertTrue(ask.fut.done())
            gone.set_result(None)
            result=await asyncio.wait_for(task,2)
            self.assertEqual(result['behavior'],'deny')
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_actual_ctl_migration_has_no_fictitious_phone_progress(self):
        """Real names client/Unix kernel peer; no phone notification for no card."""
        import os
        import signal
        import time
        import contextlib
        from agentj import sysmigrate
        path = self.st.sock_path
        for mode in ('valid','invalid_start'):
            with self.subTest(mode=mode):
                executed=[]
                handlers=set()
                async def handler(reader, writer):
                    task=asyncio.current_task(); handlers.add(task)
                    try: await self.h.on_ctl(reader,writer)
                    finally: handlers.discard(task)
                server=await asyncio.start_unix_server(handler,path=str(path))
                home=Path(self.st.root).parent/'progress-cli-home';home.mkdir(exist_ok=True)
                child_env={**os.environ,'AGENTJ_STATE_DIR':str(self.st.root),'HOME':str(home),
                           'XDG_CONFIG_HOME':str(home/'config'),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
                client="""import json
from agentj import names
from agentj.state import State
progress=[]
result=names.ctl_call(State(),{'cmd':'tasks_migrate','ids':['fixture']},timeout=3,on_sent=progress.append)
print(json.dumps({'result':result,'phone_sent_frames':progress}))
"""
                anchor="import subprocess,sys;sys.stdin.readline();sys.exit(subprocess.call([sys.executable,'-c',sys.argv[1]]))"
                proc=await asyncio.create_subprocess_exec(sys.executable,'-c',anchor,client,env=child_env,
                        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                        start_new_session=True)
                old_agent,old_turn=self.h.agent,getattr(self.h,'desktop_turn',None)
                self.h.agent=NS(host=self.h,kind='claude',session={'sessionId':'migration-progress-fixture','pid':proc.pid,
                    'procStart':codex_procs.start_token(proc.pid)},native_source=provenance.system(),research=False,
                    phone_turn=False,cfg={'kind':'claude','dir':str(self.st.root)})
                self.h.desktop_turn=self.origin['message_id']
                if mode=='invalid_start': self.h.agent.session['procStart']='not-the-live-kernel-start'
                def migrate(st,ids=None,dry=False,*args,**kwargs):
                    if not dry:
                        self.assertTrue(kwargs['_live']())
                        time.sleep(.02)  # final response may follow actual work, with no early phone ACK
                        self.assertTrue(kwargs['_live']())
                        executed.append(tuple(ids))
                    return [{'id':'fixture','result':'plan' if dry else 'migrated'}]
                try:
                    with patch.object(sysmigrate,'migrate',side_effect=migrate):
                        await asyncio.sleep(.03)
                        proc.stdin.write(b'go\n');await proc.stdin.drain()
                        out,err=await asyncio.wait_for(proc.communicate(),5)
                    self.assertEqual(proc.returncode,0,err.decode())
                    received=json.loads(out.decode().splitlines()[-1])
                    self.assertEqual(received['phone_sent_frames'],[])
                    result=received['result']
                    self.assertIs(result['ok'],mode=='valid')
                    self.assertEqual(executed,[('fixture',)] if mode=='valid' else [])
                    if mode=='invalid_start': self.assertEqual(result['error'],'proposal')
                    self.assertFalse(self.h.asks)
                    self.assertFalse(any(obj.get('t')=='ask' for _,obj in self.sent))
                    self.assertFalse(set(result)&{'grant_info','device','sig','rid','uv'})
                finally:
                    self.h.agent,self.h.desktop_turn=old_agent,old_turn
                    if proc.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            if os.getpgid(proc.pid)==proc.pid: os.killpg(proc.pid,signal.SIGKILL)
                        await proc.wait()
                    server.close();await server.wait_closed()
                    if handlers: await asyncio.wait_for(asyncio.gather(*list(handlers)),1)
                    path.unlink(missing_ok=True)
