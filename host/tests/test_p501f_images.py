"""Image regressions cross Sessions.turn and the real executor input construction."""
import _hermetic
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from agentj import peer_session, provider_profiles as profiles
from agentj.state import State
from agentj.wechat import Sessions, WeChat
from agentj.wechat_store import Store

PNG = b'\x89PNG\r\n\x1a\nfixture'
REPLY = '{"decision":"reply","text":"OK","topic":""}'

class Images(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = patch.object(profiles, 'root', return_value=self.root/'profiles'); p.start(); self.addCleanup(p.stop)
        self.st = State(self.root/'state'); self.st.init()
        self.store = Store(self.st, namespace='external'); self.addCleanup(self.store.close)
        self.host = NS(st=self.st, agent_cfg={'kind':'claude'}, preferences={}, lang='zh')
        self.s = Sessions(self.host, self.store)
        self.s.input_images = (PNG,)
        for target, value in [('problem',None), ('protected_paths',[])]:
            p = patch.object(peer_session.fence,target,return_value=value);p.start();self.addCleanup(p.stop)
        p = patch.object(peer_session,'fenced',side_effect=lambda st,argv,*a:argv);p.start();self.addCleanup(p.stop)
        p = patch.object(self.s,'_exe',return_value='/fixture/executor');p.start();self.addCleanup(p.stop)
        p = patch.object(peer_session,'claude_flags',return_value=set(peer_session.CLAUDE_REQUIRED));p.start();self.addCleanup(p.stop)
        p = patch.object(peer_session,'opencode_flags',return_value={'--agent','--format','--dir'});p.start();self.addCleanup(p.stop)

    def profile(self, kind, vision=None):
        profiles.define('fixture',kind,'https://provider.example.test/v1','fixture-model',vision=vision)
        profiles.atomic(profiles.profile('fixture')/'key',b'fixture-material')
        self.store.data['avatar']={'config':{'kind':kind,'channel_profile':'fixture',
            'model':'fixture/fixture-model' if kind=='opencode' else 'fixture-model'}}

    async def turn(self):
        return await self.s.turn({'id':'owner'},{'auto':{'mode':'on'}},'Look at this image')

    async def test_claude_shared_and_profile_send_base64_on_stdin(self):
        async def run(argv,cwd,env,text,gen):
            self.assertIn('--input-format',argv)
            self.assertEqual(argv[argv.index('--input-format')+1],'stream-json')
            blocks=json.loads(text)['message']['content']
            self.assertEqual(blocks[0],{'type':'text','text':'Look at this image'})
            self.assertEqual(base64.b64decode(blocks[1]['source']['data']),PNG)
            self.assertEqual(blocks[1]['source']['media_type'],'image/png')
            self.assertNotIn(base64.b64encode(PNG).decode(),' '.join(argv))
            self.assertIn('--tools',argv);self.assertEqual(argv[argv.index('--tools')+1],'')
            return json.dumps({'type':'result','result':REPLY}), '', 0
        self.s._run=AsyncMock(side_effect=run)
        self.assertEqual((await self.turn()).text,'OK')
        self.profile('claude')
        self.assertEqual((await self.turn()).text,'OK')
        self.assertEqual(self.s._run.await_count,2)

    async def test_text_only_turn_stays_plain(self):
        self.s.input_images=()
        self.s._run=AsyncMock(return_value=(json.dumps({'type':'result','result':REPLY}),'',0))
        self.assertEqual((await self.turn()).text,'OK')
        self.assertEqual(self.s._run.call_args.args[3],'Look at this image')

    async def test_nonvisual_profile_refuses_before_execution_and_has_public_reply(self):
        self.profile('opencode',False)
        self.s._run=AsyncMock()
        result=await self.turn()
        self.assertEqual(result.error,'vision_unsupported')
        self.s._run.assert_not_called()
        channel=object.__new__(WeChat);channel.h=self.host;channel.assistant_error=result.error
        self.assertIn('当前套餐不支持看图',channel.waiting_reply())
        self.host.lang='en';self.assertIn('does not support images',channel.waiting_reply())

    async def test_claude_provider_rejection_is_truthful(self):
        self.profile('claude')
        self.s._run=AsyncMock(return_value=(json.dumps({'type':'result','is_error':True,
            'result':'This model does not support image input'}),'',1))
        self.assertEqual((await self.turn()).error,'vision_unsupported')

    async def test_opencode_serve_receives_file_parts_and_denies_tools(self):
        # Real Sessions.turn builds the request. Only OS/HTTP boundaries are replaced.
        from agentj.agent_opencode import Client
        self.profile('opencode',True)
        proc=MagicMock(returncode=None);proc.wait=AsyncMock()
        self.s._kill=AsyncMock()
        seen=[]
        async def request(client,method,path,body=None,**kw):
            seen.append((method,path,body))
            if path=='/global/health':return 200,{'healthy':True}
            if path=='/provider':return 200,{'all':[{'id':'fixture','models':{'fixture-model':{'capabilities':{'input':{'image':True}}}}}]}
            if path=='/session':return 200,{'id':'ses_fixture'}
            if path.endswith('/message'):
                parts=body['parts'];self.assertEqual(parts[0]['text'],'Look at this image')
                self.assertEqual(parts[1]['type'],'file');self.assertEqual(parts[1]['mime'],'image/png')
                self.assertEqual(base64.b64decode(parts[1]['url'].split(',',1)[1]),PNG)
                self.assertEqual(body['tools'],{'*':False})
                return 200,{'info':{'role':'assistant','tokens':{'input':2,'output':3}},'parts':[{'type':'text','text':REPLY}]}
            raise AssertionError(path)
        with patch.object(Client,'request',request),patch('asyncio.create_subprocess_exec',AsyncMock(return_value=proc)) as spawn:
            result=await self.turn()
            self.assertIsNone(result.error);self.assertEqual(result.text,'OK')
            self.store.data['avatar']={'config':{'kind':'opencode','model':'fixture/fixture-model'}}
            with patch.object(self.s,'opencode_settings',return_value={'model':'fixture/fixture-model'}):
                again=await self.turn()
            self.assertIsNone(again.error);self.assertEqual(again.text,'OK')
        self.assertIsNone(result.error);self.assertEqual(result.text,'OK')
        self.assertIn('serve',spawn.call_args.args)
        self.assertNotIn('fixture-material',' '.join(spawn.call_args.args))
        self.assertEqual(self.store.session_id('owner','opencode'),'ses_fixture')
        self.assertEqual(self.s._kill.await_count,2);self.assertFalse(self.s._procs)
        self.assertEqual(sum(p=='/session' for _,p,_ in seen),1)
        self.assertEqual(sum(p=='/session/ses_fixture/message' for _,p,_ in seen),2)
        self.assertTrue(any(x[1]=='/session/ses_fixture/message' for x in seen))

    async def test_opencode_metadata_refuses_text_only_and_unknown_models(self):
        from agentj.agent_opencode import Client
        self.profile('opencode',True)
        self.s._kill=AsyncMock()
        for caps in ({'image':False}, {}):
            seen=[]
            async def request(client,method,path,body=None,**kw):
                seen.append(path)
                if path=='/global/health':return 200,{'healthy':True}
                if path=='/provider':return 200,{'all':[{'id':'fixture','models':{'fixture-model':{'capabilities':{'input':caps}}}}]}
                raise AssertionError('must refuse before model inference')
            proc=MagicMock(returncode=None)
            with patch.object(Client,'request',request),patch('asyncio.create_subprocess_exec',AsyncMock(return_value=proc)):
                self.assertEqual((await self.turn()).error,'vision_unsupported')
            self.assertNotIn('/session',seen);self.assertFalse(self.s._procs)

    async def test_opencode_tool_event_and_cancel_stay_closed(self):
        import asyncio
        from agentj.agent_opencode import Client
        self.profile('opencode',True)
        self.s._kill=AsyncMock()
        for mode in ('tool','cancel'):
            async def request(client,method,path,body=None,**kw):
                if path=='/global/health':return 200,{'healthy':True}
                if path=='/provider':return 200,{'all':[{'id':'fixture','models':{'fixture-model':{'capabilities':{'input':{'image':True}}}}}]}
                if path=='/session':return 200,{'id':'ses_fixture'}
                if path.endswith('/message'):
                    if mode=='cancel':raise asyncio.CancelledError()
                    return 200,{'info':{},'parts':[{'type':'tool','tool':'bash'}]}
                raise AssertionError(path)
            proc=MagicMock(returncode=None)
            with patch.object(Client,'request',request),patch('asyncio.create_subprocess_exec',AsyncMock(return_value=proc)):
                if mode=='cancel':
                    with self.assertRaises(asyncio.CancelledError):await self.turn()
                else:self.assertEqual((await self.turn()).error,'channel_forbidden')
            self.assertFalse(self.s._procs)
        self.assertEqual(self.s._kill.await_count,2)

    async def test_codex_profile_receives_private_images_and_removes_them(self):
        self.profile('codex')
        async def run(argv,cwd,env,text,gen):
            p=Path(argv[argv.index('--image')+1])
            self.assertEqual(p.read_bytes(),PNG)
            self.assertTrue(p.is_relative_to(cwd))
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            self.assertNotIn('fixture-material',' '.join(argv))
            return json.dumps({'type':'item.completed','item':{'type':'agent_message','text':REPLY}}),'',0
        self.s._run=AsyncMock(side_effect=run)
        with patch.object(peer_session,'codex_features',return_value=set(peer_session.CODEX_REQUIRED)):
            self.assertEqual((await self.turn()).text,'OK')
        self.assertFalse(list(peer_session.sandbox_dir(self.st,'owner').glob('image-*')))


class ChannelPath(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_p501b_channels import Runtime
        await Runtime.asyncSetUp(self)
        self.host._post=MagicMock();self.host._send_ready=MagicMock()
        self.channel.model=self.channel.sessions.turn
        self.s=self.channel.sessions
        for target,value in [('problem',None),('protected_paths',[])]:
            p=patch.object(peer_session.fence,target,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch.object(peer_session,'fenced',side_effect=lambda st,argv,*a:argv);p.start();self.addCleanup(p.stop)
        p=patch.object(self.s,'_exe',return_value='/fixture/claude');p.start();self.addCleanup(p.stop)
        p=patch.object(peer_session,'claude_flags',return_value=set(peer_session.CLAUDE_REQUIRED));p.start();self.addCleanup(p.stop)

    async def asyncTearDown(self):
        from test_p501b_channels import Runtime
        await Runtime.asyncTearDown(self)

    async def test_mail_tg_and_wechat_reach_real_assistant_and_executor(self):
        from agentj.wechat_protocol import Media
        from test_p501_wechat import ACCOUNT
        async def run(argv,cwd,env,text,gen):
            message=json.loads(text)['message']['content']
            self.assertEqual(base64.b64decode(message[1]['source']['data']),PNG)
            self.assertNotIn('binary content unavailable', message[0]['text'])
            return json.dumps({'type':'result','result':REPLY}),'',0
        self.s._run=AsyncMock(side_effect=run)
        for source in ('mail','telegram'):
            path=self.channel.store.place(self.host.agent_cfg['dir'],PNG,'fixture.png','image/png')
            self.assertEqual(str(await self.channel.receive_owner(source,'image','Look',[path])),'OK')
            self.assertEqual(self.s.input_images,())
        self.channel.store.data['account']=dict(ACCOUNT)
        self.channel.send=AsyncMock()
        msg={'from_user_id':'owner','message_type':1,'message_id':'wechat-image',
             'item_list':[{'type':1,'text_item':{'text':'Look'}},{'type':2,'image_item':{}}]}
        # P562 (#337): the owner's WeChat picture goes to the main conversation as an attachment (no avatar run)
        self.host._accept=AsyncMock(return_value=NS(turn=9))
        with patch.object(Media,'download',return_value=PNG):
            await self.channel.receive(NS(),msg,self.channel.store.data['generation'])
        self.channel.send.assert_not_called()
        self.assertEqual(self.s._run.await_count,2)
        blobs=self.host._accept.call_args.kwargs['blobs']
        self.assertEqual([b.kind for b in blobs],['image'])
        self.assertEqual(Path(blobs[0].path).read_bytes(),PNG)
