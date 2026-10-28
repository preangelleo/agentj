"""Offline external-channel policy and regression contracts."""
import _hermetic
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from agentj import channel_policy as policy
from agentj.channel_avatar import choice, choose, public_reply, handoff


class Policy(unittest.TestCase):
    def test_program_levels_and_unknown_fail_closed(self):
        for action in ('file.write', 'mail.send', 'secrets.read', 'payment', 'admin', 'settings.write', 'unknown'):
            self.assertTrue(policy.channel_forbidden(action), action)
        for action in ('answer', 'translate', 'image.generate', 'image.edit', 'web.search'):
            self.assertFalse(policy.channel_forbidden(action), action)

    def test_selection_inherits_single_and_prompts_once_for_multiple(self):
        main = {'kind':'claude', 'model':'sonnet', 'profile':'subscription'}
        self.assertEqual(choice(main, {}, ['claude'])['config'], main)
        first = choice(main, {}, ['claude', 'codex'])
        self.assertTrue(first['ask']);self.assertEqual(first['recommend'], 'codex')
        self.assertFalse(choice(main, {'asked':True}, ['claude','codex'])['ask'])

    def test_free_and_openrouter_never_implicit_paid_fallback(self):
        with self.assertRaises(ValueError):choose('openrouter', {}, ['codex'], router_bound=False)
        with self.assertRaises(ValueError):choose('openrouter', {}, ['opencode'], router_bound=True)
        with self.assertRaisesRegex(ValueError, 'free_temporarily_unavailable'):
            choose('免费', {}, ['opencode'], free_model='opencode/free-fixture')
        self.assertEqual(choose('codex', {}, ['codex'])['kind'], 'codex')

    def test_copy_runtime_name_and_no_internal_leak(self):
        host=NS(st=NS(agent_name=lambda:'Maya'))
        self.assertEqual(handoff(host), '已转给 Agent J - Maya，请回 Agent J 网页跟它确认')
        for text in ('saved at /work/user/out.png', '调用 image_generation 完成', '贾维斯做好了', '目录在 ~/work', 'C:\\Users\\me\\out.png'):
            self.assertIsNone(public_reply(text), text)
        self.assertEqual(public_reply('画好了，一只橘猫。'), '画好了，一只橘猫。')

    def test_owner_chosen_legacy_name_stays_data_in_handoff(self):
        for name in ('Jarvis', '贾维斯', 'Jarvis Maya'):
            host=NS(st=NS(agent_name=lambda:name))
            self.assertIn('Agent J - '+name, handoff(host))
        for name in ('Jarvis /private/file', '贾维斯 image_generation'):
            host=NS(st=NS(agent_name=lambda:name))
            self.assertIn('已转给 Agent J，', handoff(host))


class Routing(unittest.IsolatedAsyncioTestCase):
    """P562 (#337, chairman 10-28): the owner's own channels are answered by the main conversation — no avatar."""
    async def test_real_telegram_owner_reaches_the_main_conversation(self):
        import test_p118_telegram as fixture
        from agentj import provenance
        base = fixture._Base()
        base.setUp()
        try:
            avatar = NS(receive_owner=AsyncMock(return_value='分身答复'))
            base.host.external_channels = avatar
            self.assertTrue(await base.t.incoming(fixture.msg('你好'), base.cfg))
            avatar.receive_owner.assert_not_awaited()
            base.host._accept.assert_awaited_once()
            session = base.host._accept.call_args.args[0]
            self.assertEqual(session.provenance, provenance.telegram_owner(fixture.OWNER))
            self.assertTrue(callable(session.on_card), 'a waiting card tells this chat where to confirm')
        finally:
            base.doCleanups()

    async def test_without_the_channel_adapter_the_owner_still_reaches_main(self):
        import test_p118_telegram as fixture
        base = fixture._Base()
        base.setUp()
        try:
            base.host.external_channels = None
            self.assertTrue(await base.t.incoming(fixture.msg('你好'), base.cfg))
            base.host._accept.assert_awaited_once()
        finally:
            base.doCleanups()

    async def test_compact_with_quote_compacts_the_main_conversation(self):
        import test_p118_telegram as fixture
        base=fixture._Base();base.setUp()
        try:
            base.host.external_channels=NS(receive_owner=AsyncMock(return_value='收到'))
            base.t.compact=AsyncMock(return_value=True)
            await base.t.incoming(fixture.msg('/compact',reply_to_message={'from':{'id':99,'is_bot':True},'text':'quoted'}),base.cfg)
            base.host.external_channels.receive_owner.assert_not_awaited()
            base.host._accept.assert_not_awaited();base.t.compact.assert_awaited_once()
        finally:
            base.doCleanups()

    async def _mail(self, auth):
        from email import policy as ep
        from email.parser import Parser
        from agentj import mail
        msg = Parser(policy=ep.default).parsestr('From: owner@example.test\nMessage-ID: <one@example.test>\nSubject: Hello\n\nHello')
        cfg = mail.blank_config()
        cfg.update(enabled=True, generation='fixture', allow=[{'addr':'owner@example.test', 'role':'owner'}])
        avatar = NS(receive_owner=AsyncMock(return_value='分身答复'))
        host = NS(stopped=lambda:False, agent_cfg={}, external_channels=avatar, _accept=AsyncMock(return_value=NS(turn=7)), hist_add=lambda *a:{'id':1})
        manager = object.__new__(mail.Mail)
        manager.host=host;manager.st=NS();manager.lang=lambda:'zh';manager.reply=AsyncMock()
        verdict=NS(addr='owner@example.test', role='owner', auth=auth, name='')
        from unittest.mock import patch
        state={'routes':{}}
        with patch.object(mail,'read_config',return_value=cfg), patch.object(mail,'read_state',return_value=state), patch.object(mail,'save_state'):
            self.assertTrue(await manager.deliver(cfg,msg,verdict))
        avatar.receive_owner.assert_not_awaited();manager.reply.assert_not_awaited()
        host._accept.assert_awaited_once()
        return host._accept.call_args.args[0], state['routes']['7']

    async def test_authenticated_owner_mail_reaches_the_main_conversation(self):
        from agentj import provenance
        session, route = await self._mail('dkim')
        self.assertEqual(session.provenance, provenance.email_owner('owner@example.test'))
        self.assertEqual((route['role'], route['verified']), ('owner', True))

    async def test_unverified_owner_address_keeps_the_guest_envelope(self):
        """The owner turned the sender check off: the address alone does not prove the owner — untrusted, approvals refused."""
        from agentj import provenance
        session, route = await self._mail('off')
        self.assertEqual(session.provenance, provenance.email_guest('owner@example.test'))
        self.assertIsNone(provenance.owner_channel(session.provenance))
        self.assertFalse(route['verified'])


class Runtime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        from _testtmp import TEST_TMP
        from agentj.state import State
        from agentj.external_channels import ExternalChannels
        from agentj.peer_session import TurnResult
        from unittest.mock import MagicMock
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.st=State(Path(self.tmp.name)/'state');self.st.init()
        work=Path(self.tmp.name)/'work';work.mkdir()
        self.tasks=[]
        def bg(c,*_):
            import asyncio
            task=asyncio.create_task(c);self.tasks.append(task);return task
        self.host=NS(st=self.st,agent_cfg={'kind':'claude','model':'sonnet','dir':str(work)},preferences={},
                     stopped=lambda:False,agent_notice=MagicMock(),_bg=bg,lang='zh',
                     hist=NS(turns={}),hist_add=MagicMock(return_value={'id':7}),agent=NS(submit=MagicMock(),cur_send=None))
        self.model=AsyncMock(return_value=TurnResult('reply','你好','',None,None))
        self.channel=ExternalChannels(self.host,model=self.model)
        self.host.external_channels=self.channel
        self.channel.index=__import__('agentj.peer_guard',fromlist=['SecretIndex']).SecretIndex()

    async def asyncTearDown(self):
        import asyncio
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.channel.closed=True
        await self.channel.sessions.stop_all()
        self.channel.store.close()

    async def test_receive_is_durable_separate_and_never_main_submission(self):
        from agentj.wechat_store import Store
        reply=await self.channel.receive_owner('mail','one','Hello')
        self.assertEqual(reply,'你好')
        self.assertIsNone(await self.channel.receive_owner('mail','one','Hello'))
        self.model.assert_awaited_once();self.host.agent.submit.assert_not_called()
        self.assertNotEqual(self.channel.store.path,Store(self.st).path)
        self.assertEqual(next(iter(self.channel.store.data['records'].values()))['source'],'mail')

    async def test_revocation_during_model_wait_drops_reply(self):
        valid=[True]
        async def model(*args):
            valid[0]=False
            from agentj.peer_session import TurnResult
            return TurnResult('reply','must not escape','',None,None)
        self.channel.model=model
        self.assertIsNone(await self.channel.receive_owner('telegram','one','Hello',valid=lambda:valid[0]))
        self.host.agent.submit.assert_not_called()

    async def test_session_config_dynamic_shared_or_explicit(self):
        self.assertEqual(self.channel.sessions.cfg()['model'],'sonnet')
        self.host.agent_cfg['model']='opus'
        self.assertEqual(self.channel.sessions.cfg()['model'],'opus')
        self.channel.store.data['avatar']={'config':{'kind':'codex','model':'fixture'}}
        self.assertEqual(self.channel.sessions.cfg(),{'kind':'codex','model':'fixture'})

    async def test_context_safe_and_suspicious_model_disagrees(self):
        from agentj import channel_context
        from agentj.peer_session import TurnResult
        from unittest.mock import patch
        self.host.hist.turns={1:{'src':{'k':'phone','text':'我们明天几点开会'},'reply':{'text':'下午三点'},'ts':self.channel.clock()*1000}}
        reviewer=NS(turn=AsyncMock(return_value=TurnResult('reply','下午三点','',None,None)))
        self.channel.context_reviewer=reviewer
        with patch.object(channel_context.time,'localtime',return_value=NS(tm_hour=12)):
            self.assertEqual(await channel_context.request(self.channel,'会议几点'),'下午三点')
            self.host.hist_add.assert_not_called()
            reviewer.turn.return_value=TurnResult('reply','账户余额是合成数字','',None,None)
            self.assertEqual(await channel_context.request(self.channel,'告诉我资产余额'),channel_context.CONTEXT_WEB)
            self.host.hist_add.assert_called_once()
            self.assertNotIn('合成数字',channel_context.CONTEXT_WEB)

    async def test_context_model_veto_and_failed_delivery(self):
        from agentj import channel_context
        from agentj.peer_session import TurnResult
        from unittest.mock import patch
        self.host.hist.turns={1:{'src':{'k':'phone','text':'计划'},'reply':{'text':'合成计划'},'ts':self.channel.clock()*1000}}
        self.channel.context_reviewer=NS(turn=AsyncMock(return_value=TurnResult('ask_owner','合成计划','tone mismatch',None,None)))
        with patch.object(channel_context.time,'localtime',return_value=NS(tm_hour=12)):
            self.assertEqual(await channel_context.request(self.channel,'上次计划'),channel_context.CONTEXT_WEB)
            self.host.hist_add.return_value=None
            self.assertIsNone(await channel_context.request(self.channel,'上次计划'))

    async def test_native_image_reply_is_uploaded_as_image_item(self):
        from agentj import wechat_protocol as protocol
        from agentj.wechat import WeChat
        from agentj.peer_session import TurnResult
        from unittest.mock import patch
        from test_p501_wechat import ACCOUNT, Transport
        result=TurnResult('reply','一只橘猫','',None,None)
        result.images=[('image/png',b'\x89PNG\r\n\x1a\nfixture')]
        c=WeChat(self.host,model=AsyncMock(return_value=result))
        c.index=self.channel.index;c.store.data['account']=dict(ACCOUNT)
        c.open_card=lambda _:None;c.publish=lambda:None
        # P562: an image request while the main harness (Claude here) has no image tool → the owner's Codex draws it
        self.host.agent.kind='claude';c.draw=AsyncMock(return_value=result)
        tr=Transport([{},{}]);api=protocol.API(ACCOUNT,tr)
        try:
            with patch.object(protocol.Media,'upload',return_value={'type':2,'image_item':{'media':{'fixture':True}}}) as upload:
                await c.receive(api,{'from_user_id':'owner','message_type':1,'message_id':1,'context_token':'fixture',
                                     'item_list':[{'type':1,'text_item':{'text':'画猫'}}]},c.store.data['generation'])
            upload.assert_called_once();c.draw.assert_awaited_once()
            import json
            self.assertEqual(json.loads(tr.calls[1][0][3])['msg']['item_list'][0]['type'],2)
            self.host.agent.submit.assert_not_called()
            self.assertEqual(self.host.hist_add.call_args.args[0]['k'],'wechat','the exchange shows on the Web App page')
        finally:
            c.closed=True;c.store.close()


class Native(unittest.IsolatedAsyncioTestCase):
    def test_command_has_no_owner_tree_or_shell_and_only_native_auth(self):
        import os
        from pathlib import Path
        from unittest.mock import patch
        from agentj.channel_native import command
        with patch('agentj.channel_native.shutil.which',return_value='/usr/bin/bwrap'), \
             patch.object(Path,'is_file',return_value=True), \
             patch('agentj.channel_native.peer_session.codex_user_model',return_value={}), \
             patch('agentj.channel_native.peer_session.codex_features',return_value=set(__import__('agentj.peer_session',fromlist=['CODEX_DISABLE']).CODEX_DISABLE)|{'image_generation','code_mode_host'}):
            cmd,env=command('/fixture/bin/codex',Path('/fixture/avatar'),{'model':'fixture'}, {'HOME':'/fixture/owner'},images=['fixture.png'])
        self.assertNotIn('--clearenv',cmd)
        self.assertEqual(env['HOME'],'/avatar-home')
        self.assertNotIn('--proc',cmd)
        self.assertEqual(cmd[cmd.index('/fixture/avatar')-1], '--ro-bind')
        self.assertIn('image_generation',cmd)
        self.assertIn('code_mode_host',cmd)
        self.assertEqual(cmd[cmd.index('-i')+1],'/avatar/input/fixture.png')
        for feature in ('shell_tool','unified_exec','plugins','hooks','multi_agent','code_mode'):
            i=cmd.index(feature)
            self.assertEqual(cmd[i-1],'--disable')
        self.assertNotIn('/fixture/owner',cmd)
        self.assertIn('/fixture/owner/.codex/auth.json',cmd)
        i=cmd.index('/fixture/owner/.codex/auth.json')
        self.assertEqual(cmd[i-1],'--ro-bind')

    async def test_native_image_bytes_are_discovered_and_unknown_tool_fails(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from agentj.channel_native import NativeCodex
        from agentj import peer_session
        from _testtmp import TEST_TMP
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root=Path(tmp)
            store=NS(session_id=lambda *a:None,set_session_id=lambda *a:None)
            state=NS(write_private=lambda p,data:p.write_bytes(data))
            sessions=NS(st=state,store=store,_gen=0,_exe=lambda _: '/fixture/codex',cfg=lambda:{},system_prompt=lambda *a:'policy',input_images=(b'\x89PNG\r\n\x1a\ninput-fixture',))
            async def run(*args):
                self.assertEqual(args[2], {'HOME':'/avatar-home'})
                self.assertEqual([p.read_bytes() for p in (root/'native-owner'/'input').iterdir()],
                                 [b'\x89PNG\r\n\x1a\ninput-fixture'])
                outbox=root/'native-owner'/'outbox'
                (outbox/'image.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
                return '\n'.join((__import__('json').dumps({'type':'thread.started','thread_id':'fixture'}),
                                  __import__('json').dumps({'type':'item.completed','item':{'type':'agent_message','text':'{"decision":"reply","text":"一只猫","topic":""}'}}),
                                  '{"type":"turn.completed"}')), '', 0
            sessions._run=run
            native=NativeCodex(sessions)
            with patch.object(peer_session,'sandbox_root',return_value=root), patch('agentj.channel_native.command',return_value=(['fixture'], {'HOME':'/avatar-home'})):
                reply=await native.turn({'id':'owner'},{},'画猫')
                self.assertEqual(reply.images,[('image/png',b'\x89PNG\r\n\x1a\nfixture')])
                self.assertEqual(reply.text,'一只猫')
                sessions._run=AsyncMock(return_value=('{"type":"item.completed","item":{"type":"command_execution"}}','',0))
                denied=await native.turn({'id':'owner'},{},'unsafe')
                self.assertEqual(denied.error,'channel_forbidden')


class FailureSemantics(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Runtime.asyncSetUp
    asyncTearDown = Runtime.asyncTearDown
    async def test_model_unavailable_waits_without_handoff_or_main_execution(self):
        from agentj.peer_session import TurnResult
        self.channel.model=AsyncMock(return_value=TurnResult('ask_owner','','',None,'login'))
        self.channel.open_card=__import__('unittest.mock',fromlist=['MagicMock']).MagicMock()
        reply=await self.channel.receive_owner('mail','unavailable','Hello')
        self.assertIn('没有登录或登录已过期',reply)   # P540: says why, not a bare 「暂时不可用」
        self.assertNotIn('已转给',reply)
        self.assertEqual(next(iter(self.channel.store.data['records'].values()))['state'],'waiting_model')
        self.channel.open_card.assert_not_called();self.host.agent.submit.assert_not_called()

    async def test_revoked_channel_cannot_claim_or_deliver_queued_handoff(self):
        from agentj import provenance,telegram
        from unittest.mock import patch
        cfg={'owner_id':1,'key_env':'FIXTURE','generation':'one'}
        record=self.channel.store.put('revoked',[],'test')
        record.update(source='telegram',state='pending',admission=dict(cfg))
        with patch.object(telegram,'configuration',return_value=cfg):
            result=await self.channel.claim('revoked',source=provenance.phone())
            self.assertTrue(result['ok'])
            send=self.host.agent.submit.call_args.args[0]
            cfg['generation']='two'
            self.assertFalse(await send.wait_ready())
            record['state']='pending'
            self.assertEqual((await self.channel.claim('revoked',source=provenance.phone()))['why'],'channel_revoked')

class ReworkE(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Runtime.asyncSetUp
    asyncTearDown = Runtime.asyncTearDown

    async def test_mail_and_telegram_images_and_text_reach_avatar(self):
        from unittest.mock import patch
        image = b'\x89PNG\r\n\x1a\nfixture'
        wd = self.host.agent_cfg['dir']
        for source in ('mail', 'telegram'):
            path = self.channel.store.place(wd, image, 'photo.png', 'image/png')
            text_path = self.channel.store.place(wd, b'Summarize this fixture document.', 'notes.txt', 'text/plain')
            blobs = [NS(path=path, mime='image/png'), NS(path=text_path, mime='text/plain')]
            with patch.object(self.channel, 'assistant', AsyncMock(return_value='收到')) as assistant:
                await self.channel.receive_owner(source, source, 'Look and summarize', [path, text_path], blobs=blobs)
            self.assertEqual(assistant.call_args.args[1], [image])
            self.assertIn('Summarize this fixture document.', assistant.call_args.args[0])
            self.assertNotIn(wd, assistant.call_args.args[0])
            self.host.agent.submit.assert_not_called()

    async def test_paths_only_image_reaches_avatar(self):
        from unittest.mock import patch
        image = b'\x89PNG\r\n\x1a\nfixture'
        path = self.channel.store.place(self.host.agent_cfg['dir'], image, 'photo.png', 'image/png')
        with patch.object(self.channel, 'assistant', AsyncMock(return_value='收到')) as assistant:
            await self.channel.receive_owner('mail', 'paths-only', 'Look', [path])
        self.assertEqual(assistant.call_args.args[1], [image])

    async def test_attachment_symlink_or_outside_inbox_never_read(self):
        from pathlib import Path
        from unittest.mock import patch
        wd = self.host.agent_cfg['dir']
        secret = Path(wd) / 'private.txt'
        secret.write_text('fixture-owner-secret')
        link = Path(self.channel.store.place(wd, b'placeholder', 'linked.txt', 'text/plain'))
        link.unlink();link.symlink_to(secret)
        with patch.object(self.channel, 'assistant', AsyncMock(return_value='收到')) as assistant:
            await self.channel.receive_owner('telegram', 'unsafe-paths', 'Look', [str(link), str(secret)])
        self.assertNotIn('fixture-owner-secret', assistant.call_args.args[0])
        self.assertEqual(assistant.call_args.args[1], [])

    async def test_old_domestic_config_waits_without_starting_unsupported_cli(self):
        from unittest.mock import patch
        for kind in ('kimi', 'qwen', 'dsh'):
            self.channel.store.data['avatar'] = {'config':{'kind':kind}}
            with patch.object(self.channel.sessions, '_exe') as resolve:
                result=await self.channel.sessions.turn({'id':'owner'}, {}, 'Hello')
            self.assertEqual(result.error, 'harness_unavailable')
            resolve.assert_not_called()

class NativeEnvironment(unittest.TestCase):
    def test_proxy_values_only_in_child_environment(self):
        from pathlib import Path
        from unittest.mock import patch
        from agentj import channel_native, peer_session
        proxy = 'http://fixture-user:fixture-password@127.0.0.1:8080'
        proxies={k:proxy + '/' + k for k in ('HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','NO_PROXY','https_proxy','http_proxy','all_proxy','no_proxy')}
        with patch.object(channel_native.shutil, 'which', return_value='/usr/bin/bwrap'), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(peer_session, 'codex_user_model', return_value={}), \
             patch.object(peer_session, 'codex_features', return_value=set(peer_session.CODEX_DISABLE)|{'image_generation','code_mode_host'}):
            argv, env = channel_native.command('/fixture/bin/codex', Path('/fixture/avatar'), {},
                {'HOME':'/fixture/owner', **proxies, 'UNRELATED_SECRET':'fixture-never-forward'})
        self.assertNotIn(proxy, ' '.join(argv))
        self.assertNotIn('fixture-password', ' '.join(argv))
        for name, value in proxies.items():
            self.assertEqual(env[name], value)
            self.assertNotIn(value, ' '.join(argv))
        self.assertNotIn('UNRELATED_SECRET', env)
        self.assertEqual(env['HOME'], '/avatar-home')
        self.assertNotIn('--clearenv', argv)
