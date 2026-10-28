"""P501 offline fixtures: same wire shapes as the verified Tencent protocol PoC.
No real accounts, providers, network, credentials, or QA seats are touched.
"""
import _hermetic  # noqa: F401
from _testtmp import TEST_TMP
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from agentj import approvals, controls, provenance, wire
from agentj import wechat_protocol as p
from agentj.wechat import WeChat, Sessions, SYSTEM, HANDOFF
from agentj.wechat_store import Store
from agentj.peer_session import TurnResult
from agentj.state import State

ACCOUNT = {'bot_token': 'fixture-bot', 'ilink_bot_id': 'bot', 'ilink_user_id': 'owner', 'baseurl': p.BASE}


class Transport:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, tuple) else (200, {}, json.dumps(r).encode())


class Protocol(unittest.TestCase):
    def test_headers_and_credentials(self):
        tr = Transport([{}, {}, {}]); api = p.API(ACCOUNT, tr)
        api.call('get_bot_qrcode', {'local_token_list': []}, {'bot_type':'3'}, auth=False)
        api.call('get_qrcode_status', query={'qrcode':'fixture-qr'}, auth=False)
        api.call('getupdates', {'get_updates_buf':'cursor'})
        post, get, auth = [r[0] for r in tr.calls]
        self.assertIn('bot_type=3', post[1]); self.assertNotIn('Authorization', post[2])
        self.assertNotIn('base_info', json.loads(post[3])); self.assertNotIn('X-WECHAT-UIN', get[2])
        self.assertEqual(auth[2]['Authorization'], 'Bearer fixture-bot')
        self.assertEqual(auth[2]['iLink-App-ClientVersion'], '132106')
        self.assertTrue(base64.b64decode(auth[2]['X-WECHAT-UIN']).isdigit())
        self.assertEqual(json.loads(auth[3])['base_info']['channel_version'], '2.4.10')

    def test_urls_and_redirects_fail_closed(self):
        for url in ('http://ilinkai.weixin.qq.com', 'https://evil.test', 'https://weixin.qq.com.evil.test',
                    'https://user:pass@ilinkai.weixin.qq.com', 'https://ilinkai.weixin.qq.com:444', 'https://openrouter.ai'):
            with self.subTest(url=url), self.assertRaises(p.Failure): p.safe_url(url)
        with self.assertRaises(p.Failure):
            p.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test')
        with self.assertRaises(p.Failure): p.API(ACCOUNT, Transport([])).call('getupdates', {}, base='https://evil.test')

    def test_aes_vectors_padding_keys(self):
        key = bytes(range(16)); plain = bytes.fromhex('00112233445566778899aabbccddeeff')
        self.assertEqual(p.encrypt(plain,key)[:16].hex(), '69c4e0d86a7b0430d8cdb78070b4c55a')
        for n in (0,1,15,16,17,100):
            b = b'x'*n; self.assertEqual(p.decrypt(p.encrypt(b,key),key),b)
        for k in (key, key.hex().encode()): self.assertEqual(p.key_bytes(base64.b64encode(k).decode()),key)
        for bad in ('!', base64.b64encode(b'k').decode()):
            with self.assertRaises(p.Failure): p.key_bytes(bad)
        with self.assertRaises(p.Failure): p.decrypt(b'bad',key)

    def test_media_download_and_upload_original_bytes(self):
        for typ in (2,3,4,5):
            kind=p.KINDS[typ][0]; key=b'k'*16; plain=b'exact fixture bytes'
            detail={'media':{'full_url':p.CDN+'/download', 'aes_key':base64.b64encode(key).decode()}, 'file_name':'../../file.txt'}
            if typ==2: detail.update(aeskey=key.hex()); detail['media']['aes_key']='invalid'
            item={'type':typ, kind+'_item':detail}
            tr=Transport([(200,{},p.encrypt(plain,key))]); media=p.Media(p.API(ACCOUNT,tr))
            self.assertEqual(media.download(item),plain)
            if typ==3: continue             # outgoing audio must use file type 4
            tr.replies.extend([{'upload_param':'fixture-up'}, (200,{'x-encrypted-param':'fixture-down'},b'')])
            out=media.upload('owner',item,plain)
            spec=json.loads(tr.calls[1][0][3]); encrypted=tr.calls[2][0][3]
            self.assertEqual(spec['rawfilemd5'],hashlib.md5(plain).hexdigest())
            self.assertEqual(spec['media_type'],{2:1,4:3,5:2}[typ])
            self.assertEqual(p.decrypt(encrypted,bytes.fromhex(spec['aeskey'])),plain)
            self.assertEqual(out['type'],typ)
            if typ==4:self.assertEqual(out['file_item']['file_name'],'file.txt')

    def test_upload_retry_and_4xx(self):
        for error, attempts in [(p.Failure('http_error',http=403),2), ((200,{},b''),3)]:
            tr=Transport([{'upload_full_url':p.CDN+'/upload'},error,(200,{'x-encrypted-param':'d'},b'')])
            media=p.Media(p.API(ACCOUNT,tr))
            if attempts==2:
                with self.assertRaises(p.Failure):media.upload('owner',{'type':4},b'hello')
            else: self.assertEqual(media.upload('owner',{'type':4},b'hello')['type'],4)
            self.assertEqual(len(tr.calls),attempts)

    def test_plain_image_and_limits_errors(self):
        tr=Transport([(200,{},b'plain')]); media=p.Media(p.API(ACCOUNT,tr))
        self.assertEqual(media.download({'type':2,'image_item':{'media':{'encrypt_query_param':'a+b/='}}}),b'plain')
        self.assertIn('a%2Bb%2F%3D',tr.calls[0][0][1])
        with self.assertRaises(p.Failure) as e:p.checked({'errcode':-14,'errmsg':'private raw error'})
        self.assertEqual(str(e.exception),'api_error');self.assertEqual(e.exception.ret,-14)
        with self.assertRaises(p.Failure):p.parse_json(b'[]')


class Channel(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP); self.root=Path(self.tmp.name)
        self.st=State(self.root/'state');self.st.init()
        self.st.log=MagicMock()
        self.work=self.root/'work';self.work.mkdir()
        self.jobs=[];self.posts=[];self.sent=[];self.stopped=False
        self.h=NS(st=self.st,lang='zh',channel=self.st.config()['channel'],agent_cfg={'dir':str(self.work),'kind':'claude'},
                  preferences={},asks={},peers=None,nonces=controls.Nonces(),agent=NS(submit=MagicMock(),cur_send=None),
                  stopped=lambda:self.stopped,_send_ready=None,status_changed=MagicMock(),push_notify=MagicMock(),
                  agent_notice=MagicMock(),send_app=AsyncMock(),_ctl_res=AsyncMock(),_answer_refused=AsyncMock())
        def bg(coro,*_):
            t=asyncio.create_task(coro);self.jobs.append(t);return t
        self.h.hist_add=MagicMock(return_value={'id':101})
        # P562 (#337): the owner's WeChat message is a main-conversation message (serve._accept stand-in)
        self.h._accept=AsyncMock(side_effect=lambda session,text,sid,blobs=None:NS(turn=200+self.h._accept.await_count))
        self.h._bg=bg;self.h._post=lambda fn,body:self.posts.append(body(None))
        self.h._ask_msg=lambda a,s:{'t':'ask','id':a.rid,'tool':a.tool,'summary':a.summary}
        self.c=WeChat(self.h,model=AsyncMock(return_value=TurnResult('reply','译文','',None,None)))
        self.h.wechat=self.c
        self.c.index=__import__('agentj.peer_guard',fromlist=['SecretIndex']).SecretIndex()
        self.c.store.data['account']=dict(ACCOUNT);self.c.store.save()
        self.tr=Transport([{}]*30);self.api=p.API(ACCOUNT,self.tr)
        self.c.api_factory=lambda *_a:self.api      # P562: the main answer is sent later with a fresh client
        self.gen=self.c.store.data['generation']
    async def asyncTearDown(self):
        await self.c.stop()
        for t in self.jobs:t.cancel()
        await asyncio.gather(*self.jobs,return_exceptions=True)
        self.tmp.cleanup()
    def msg(self,**extra):
        return dict({'message_type':1,'from_user_id':'owner','message_id':1,'context_token':'fixture-context',
                     'item_list':[{'type':1,'text_item':{'text':'翻译这段话'}}]},**extra)
    async def receive(self,**extra):await self.c.receive(self.api,self.msg(**extra),self.gen)
    async def pending(self):
        """A held request as 0.25.15a1 (P501…P559) left it in wechat.bin: P562 no longer makes new ones, but a stored one
        still opens its signed card and can be claimed (published-format regression)."""
        path=self.c.store.place(str(self.work),json.dumps({'source':'wechat','untrusted':True,'execute':False,'text':'翻译这段话'}).encode(),
                                'message.json','application/json')
        r=self.c.store.put('legacy-'+str(len(self.c.store.data['records'])),[path],'翻译这段话')
        r['state']='pending';self.c.store.save()
        self.c.open_card(r);await asyncio.sleep(0)
        return r
    async def complete(self,turn,text,end='done'):
        self.c.completed({'id':turn,'src':{'k':'wechat'},'reply':{'text':text},'end':end})
        await asyncio.gather(*list(self.c.card_tasks))
    async def answer(self,a,ok=True,*,wrong=False,did='paired'):
        from agentj.serve import Host
        sk=Ed25519PrivateKey.generate();pk=sk.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.st.sign_key=lambda dev:pk if dev=='paired' else None
        self.st.passkey_of_device=lambda _:None
        digest='0'*64 if wrong else a.digest
        sig=sk.sign(approvals.signed_message(self.h.channel,did,a.rid,'allow' if ok else 'deny',digest))
        await Host._answer(self.h,NS(device=did),{'id':a.rid,'ok':ok,'sig':wire.b64u(sig)})
    async def test_owner_group_bot_rejected_before_model_or_context(self):
        for extra in ({'from_user_id':'other'},{'group_id':'g'},{'message_type':2},{'delete_time_ms':1}):await self.receive(**extra)
        self.assertEqual(self.c.store.data['counts']['ignored_non_owner_group_bot'],4)
        self.h._accept.assert_not_awaited();self.assertFalse(self.tr.calls);self.assertEqual(self.c.store.data['context'],'')
        self.assertFalse(list(self.work.rglob('*.json')))
    async def test_one_merged_message_reaches_main_once_and_the_answer_goes_back(self):
        await self.receive(item_list=[{'type':1,'text_item':{'text':'one'}},{'type':1,'text_item':{'text':'two'}}])
        await self.receive()
        self.c.model.assert_not_awaited();self.assertEqual(self.h._accept.await_count,1,'deduplicated, one main message')
        session,text=self.h._accept.call_args.args[:2]
        self.assertEqual((session.source_kind,session.provenance),('wechat',provenance.wechat_owner()))
        self.assertEqual(provenance.owner_channel(session.provenance),'wechat');self.assertIn('one\ntwo',text)
        self.assertTrue(text.startswith('【主人本人 · 从主人绑定的微信'))
        self.assertFalse(self.tr.calls,'nothing is said before the main conversation answers')
        rec=next(iter(self.c.store.data['records'].values()));data=json.loads(Path(rec['paths'][0]).read_text())
        self.assertEqual(data['source'],'wechat');self.assertFalse(data['execute']);self.assertEqual(data['text'],'one\ntwo')
        self.assertEqual((rec['state'],rec['turn']),('main',201))
        await self.complete(201,'译文')
        self.assertEqual(len(self.tr.calls),1)
        request=json.loads(self.tr.calls[0][0][3])['msg'];self.assertEqual(request['context_token'],'fixture-context')
        self.assertEqual(request['item_list'][0]['text_item']['text'],'译文')
        self.assertEqual(rec['state'],'replied');self.assertEqual(rec['delivery'],'sent')
        await self.complete(201,'译文');self.assertEqual(len(self.tr.calls),1,'answered once')
    async def test_attachment_bytes_in_inbox_no_owner_files_given_to_model(self):
        key=b'k'*16;plain=b'only incoming content'
        self.tr.replies=[(200,{},p.encrypt(plain,key)),{}]
        await self.receive(item_list=[{'type':4,'file_item':{'file_name':'../../x.txt','media':{'full_url':p.CDN+'/d','aes_key':base64.b64encode(key).decode()}}}])
        r=next(iter(self.c.store.data['records'].values()))
        self.assertEqual(Path(r['paths'][1]).read_bytes(),plain)
        blobs=self.h._accept.call_args.kwargs['blobs']      # P562: the file is the main message's attachment
        self.assertEqual([Path(b.path).read_bytes() for b in blobs],[plain]);self.assertEqual(blobs[0].name,'../../x.txt')
        self.assertNotIn('../',Path(blobs[0].path).name)
    async def test_storage_encrypted_status_logs_omit_protocol_secrets(self):
        await self.receive()
        blob=self.c.store.path.read_bytes()
        for value in ('fixture-bot','fixture-context','翻译这段话'):
            self.assertNotIn(value.encode(),blob);self.assertNotIn(value,str(self.st.log.call_args_list));self.assertNotIn(value,str(self.c.status()))
        self.assertEqual(self.c.store.path.stat().st_mode&0o777,0o600)
        self.assertEqual(Store(self.st).data['context'],'fixture-context')
        self.c.store.path.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'wechat_state_invalid'):Store(self.st)
    async def test_no_main_conversation_says_so_and_never_runs_an_avatar(self):
        self.h.agent=None
        await self.receive()
        self.h._accept.assert_not_awaited();self.c.model.assert_not_awaited()
        [r]=self.c.store.data['records'].values();self.assertEqual((r['state'],r['outcome']),('replied','not_running'))
        self.assertIn('主对话现在没有在运行',json.loads(self.tr.calls[0][0][3])['msg']['item_list'][0]['text_item']['text'])
    async def test_failed_turn_and_credentials_never_cross_the_channel(self):
        await self.receive();await self.complete(201,'',end='failed')
        self.assertIn('没有完成',json.loads(self.tr.calls[-1][0][3])['msg']['item_list'][0]['text_item']['text'])
        await self.receive(message_id=2);await self.complete(202,'key: sk-ant-api03-'+'A'*40)
        said=json.loads(self.tr.calls[-1][0][3])['msg']['item_list'][0]['text_item']['text']
        self.assertNotIn('sk-ant',said);self.assertIn('Agent J 网页',said)
        await self.receive(message_id=3);await self.complete(203,'〔不回群〕')
        self.assertEqual(len(self.tr.calls),2,'a silent answer sends nothing')
    async def test_signed_confirmation_exact_summary_and_once(self):
        r=await self.pending();a=self.c.cards[r['id']]
        await self.answer(a,wrong=True);self.assertFalse(a.fut.done())
        await self.answer(a,did='unpaired');self.assertFalse(a.fut.done())
        self.assertFalse(self.h.asks, 'held channel cards cannot occupy main approval slots')
        self.assertIn(r['text'],a.summary);self.assertIsNone(a.scope)
        await self.answer(a);await asyncio.sleep(.02)
        self.h.agent.submit.assert_called_once();send=self.h.agent.submit.call_args.args[0]
        self.assertFalse(provenance.is_owner(send.source));self.assertTrue(provenance.delegated_turn(NS(cur_send=send)))
        self.assertIn('wechat_handoff',send.text);self.assertEqual(send.turn,101);self.assertTrue(await send.wait_ready())
        self.assertFalse((await self.c.claim(r['id'],approved=True))['ok'])
        records=[json.loads(l) for l in self.st.approvals_path.read_text().splitlines()]
        self.assertEqual(records[-1]['reason'],'device');self.assertEqual(records[-1]['decision'],'allow')
    async def test_denial_timeout_expiry_never_execute(self):
        r=await self.pending();a=self.c.cards[r['id']]
        await self.answer(a,ok=False);await asyncio.sleep(.02)
        self.assertEqual(r['state'],'denied');self.h.agent.submit.assert_not_called()
        r['state']='pending';r['until']=0;self.c.store.save()
        self.assertFalse((await self.c.claim(r['id'],source=provenance.phone()))['ok'])
        self.c.store.expire();self.assertEqual(r['state'],'expired')
    async def test_natural_owner_claim_requires_explicit_phone_provenance(self):
        r=await self.pending()
        for src in (None,{},provenance.system(),{'kind':'friend_task','role':'task'},provenance.telegram_owner(1)):
            self.assertFalse((await self.c.claim(r['id'],source=src))['ok'])
        self.h.agent.cur_send=NS(source=provenance.phone())
        self.assertTrue((await self.c.agent_request({'op':'claim','id':r['id']}))['ok'])
        await asyncio.sleep(.02);self.h.agent.submit.assert_called_once()
    async def test_unbind_and_stop_invalidate_queued_handoff(self):
        r=await self.pending();await self.c.claim(r['id'],source=provenance.phone())
        send=self.h.agent.submit.call_args.args[0]
        await self.c.unbind();self.assertFalse(await send.wait_ready())
        self.assertFalse(self.c.store.data['account']);self.assertFalse(self.c.store.data['context'])
        self.assertFalse((await self.c.claim(r['id'],approved=True))['ok'])
    async def test_stop_before_delivery_drops_the_message(self):
        original=self.c.store.place
        def place(*args):
            self.stopped=True
            return original(*args)
        with patch.object(self.c.store,'place',side_effect=place):
            await self.receive()
        self.assertFalse(self.tr.calls);self.h._accept.assert_not_awaited()
    async def test_inbox_symlink_is_refused(self):
        (self.work/'.agentj').symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(Exception):await self.receive()
        self.h._accept.assert_not_awaited();self.assertFalse(self.tr.calls)
    async def test_missing_context_never_blindly_push(self):
        self.c.store.data.update(context='',context_at=self.c.clock())   # P554: no time window; only a missing context refuses
        with self.assertRaises(p.Failure):await self.c.send(self.api,'text',self.gen)
        self.assertFalse(self.tr.calls)
    async def test_session_identity_no_owner_context(self):
        s=Sessions(self.h,self.c.store,max_active=1)
        self.assertEqual(s.system_prompt({'id':'other'},{'auto':{'mode':'all'}}),SYSTEM)
        self.assertNotIn(str(self.work),SYSTEM);self.assertIn('capabilities should match',SYSTEM)
        self.c.store.set_session_id('wechat','claude','private-session')
        self.assertEqual(Store(self.st).session_id('wechat','claude'),'private-session')
    async def test_unsigned_phone_setup_cannot_change_binding(self):
        from unittest.mock import AsyncMock, Mock
        self.h.owner_uv_offer=AsyncMock(return_value=('pk','ch'));self.h.owner_uv_check=Mock(return_value=None)   # P551: Face ID passed; the signature still fails
        await self.c.on_phone(NS(device='x'),{'op':'unbind','r':'r'})
        self.assertTrue(self.c.status()['bound']);self.h._ctl_res.assert_awaited()
    async def test_login_refresh_redirect_verify_and_encrypted_binding(self):
        self.c.store.data['account']={}
        tr=Transport([{'qrcode':'qr1','qrcode_img_content':'https://example.invalid/qr1'}, {'status':'expired'},
                      {'qrcode':'qr2','qrcode_img_content':'https://example.invalid/qr2'}, {'status':'need_verifycode'},
                      {'status':'scaned_but_redirect','redirect_host':'alt.weixin.qq.com'}, {'status':'scaned'},
                      dict(ACCOUNT,status='confirmed')])
        self.c.api_factory=lambda *a:p.API(transport=tr)
        async def tick(*_):
            if self.c.qr and self.c.qr['status']=='need_verifycode':self.c.qr['code']='123456'
        with patch('agentj.wechat.asyncio.sleep',side_effect=tick):await self.c.login()
        self.assertTrue(self.c.status()['bound']);self.assertIsNone(self.c.qr)
        self.assertIn('verify_code=123456',tr.calls[4][0][1]);self.assertIn('alt.weixin.qq.com',tr.calls[5][0][1]);self.assertNotIn('verify_code',tr.calls[6][0][1])
        self.assertNotIn('123456',str(self.st.log.call_args_list));self.assertNotIn(b'fixture-bot',self.c.store.path.read_bytes())
    async def test_poll_cursor_nonempty_dedup_and_error_boundary(self):
        self.tr.replies=[{'msgs':[self.msg()], 'get_updates_buf':'cursor-one', 'longpolling_timeout_ms':35000},
                         {'msgs':[self.msg()], 'get_updates_buf':''}, {'errcode':-14,'errmsg':'raw-secret'}]   # P562: no reply until main answers
        await self.c.poll_once(self.api,self.gen)
        await self.c.poll_once(self.api,self.gen)
        self.assertEqual(self.c.store.data['cursor'],'cursor-one');self.assertEqual(self.c.poll_timeout,40)
        self.assertEqual(self.h._accept.await_count,1)
        with self.assertRaises(p.Failure):await self.c.poll_once(self.api,self.gen)
        self.assertEqual(Store(self.st).data['cursor'],'cursor-one')
        self.assertNotIn(b'cursor-one',self.c.store.path.read_bytes())
    async def test_unbind_during_poll_drops_delayed_updates(self):
        async def call(*a,**kw):
            await self.c.unbind()
            return {'msgs':[self.msg()], 'get_updates_buf':'stale'}
        self.c.call=call
        await self.c.poll_once(self.api,self.gen)
        self.h._accept.assert_not_awaited();self.assertEqual(self.c.store.data['cursor'],'')
    async def test_restart_holds_but_never_executes_pending(self):
        r=await self.pending()
        await self.c.stop()
        c=WeChat(self.h,model=AsyncMock())
        self.assertEqual(c.store.data['records'][r['id']]['state'],'pending')
        self.h.agent.submit.assert_not_called()
        self.assertEqual(Store(self.st).data['records'][r['id']]['state'],'pending')
        self.assertFalse(self.c.card_tasks)
        await c.stop()
    async def test_setup_catalog_and_probe(self):
        from agentj import first_run
        self.assertIn('wx',first_run.BY_ID)
        # A persisted QR / a claim of readiness does not count as a live bound-channel reading.
        with patch('sys.platform','linux'):
            self.assertEqual(first_run.probe(self.st,'wx',{'system':'linux','serve':{'wechat':{'bound':True}}})[0],'verified')
            self.assertEqual(first_run.probe(self.st,'wx',{'system':'linux','serve':{}})[0],'pending')
    async def test_restart_waiters_finish_before_storage_lock_releases(self):
        r=await self.pending()
        await self.c.stop()
        self.assertFalse(self.c.card_tasks)
        self.assertEqual(Store(self.st).data['records'][r['id']]['state'],'pending')
        replacement=WeChat(self.h,model=AsyncMock())
        try:
            self.assertEqual(replacement.store.data['records'][r['id']]['state'],'pending')
            await asyncio.sleep(.01)
            self.assertEqual(Store(self.st).data['records'][r['id']]['state'],'pending')
        finally:
            await replacement.stop()
    async def test_stop_mid_batch_keeps_cursor_for_resume(self):
        self.tr.replies=[{'msgs':[self.msg(),self.msg(message_id=2)],'get_updates_buf':'do-not-ack'}]
        async def accept(*_a,**_k):
            self.stopped=True
            return NS(turn=201)
        self.h._accept=AsyncMock(side_effect=accept)
        await self.c.poll_once(self.api,self.gen)
        self.assertEqual(self.c.store.data['cursor'],'')
        self.assertEqual(len(self.c.store.data['records']),1)
        await self.c.pause()
        row=next(iter(self.c.store.data['records'].values()))
        self.assertEqual(row['state'],'main');self.h._accept.assert_awaited_once()
        self.assertFalse(self.tr.calls[1:]);self.h.agent.submit.assert_not_called()
    async def test_finished_record_retention_does_not_lock_chat(self):
        now=self.c.clock()
        self.c.store.data['records']={str(i):{'id':str(i),'state':'replied','at':now-10,'until':now+10,'text':'old','paths':[]} for i in range(250)}
        self.c.store.data['records']['0']['state']='pending'
        self.c.store.put('next',[],'new',now=now)
        self.assertEqual(len(self.c.store.data['records']),250)
        self.assertIn('0',self.c.store.data['records'])
        self.assertIn('next',Store(self.st).data['records'])
    async def test_real_phone_send_producer_and_private_pending_list(self):
        from agentj.serve import Host, Session
        from agentj import compose
        row=await self.pending()
        for source in (None,provenance.system(),{'kind':'wechat_task','role':'task'},provenance.telegram_owner(1)):
            self.h.agent.cur_send=NS(source=source)
            self.assertFalse((await self.c.agent_request({'op':'pending'}))['ok'])
        self.h._setup_round_trip=AsyncMock();self.h.emit=MagicMock();self.h._remember=MagicMock()
        self.h.sessions={};self.h.speaker_note=lambda *_:'';self.h.onboarding_note=lambda:'';self.h.key_import_note=AsyncMock(return_value='')
        self.h.skill_turn_note=lambda *_:'';self.h.native_text=False;self.h.sends=compose.Sends()
        self.h.quota_resume=NS(owner_spoke=MagicMock());self.h._model_set_flush=AsyncMock()
        self.h._meeting_text=lambda session,text,voice:text
        self.st.is_allowed=lambda pub:pub==b'p'*32
        session=Session(1,state='ready',pub=b'p'*32,device='paired',name='Owner',provenance=provenance.phone())
        self.h.sessions[session.cid]=session  # registered authenticated transport, as required by P506c
        send=await Host._accept(self.h,session,'handle my WeChat request',sid=None)
        self.assertEqual(send.source,provenance.phone(),'real paired producer must not leave source=None')
        self.h.agent.cur_send=send
        listing=await self.c.agent_request({'op':'pending'})
        self.assertTrue(listing['ok']);self.assertEqual(listing['items'][0]['id'],row['id'])
        self.assertFalse(listing['items'][0]['execute'])
        self.assertTrue((await self.c.agent_request({'op':'claim','id':row['id']}))['ok'])
    async def test_single_poller_and_unbind_during_inbox_write(self):
        with self.assertRaisesRegex(ValueError,'wechat_already_running'):
            WeChat(self.h)
        original=self.c.store.place
        def place(*args):
            self.c.store.data.update(account={},context='',generation='new-binding')
            return original(*args)
        with patch.object(self.c.store,'place',side_effect=place):
            await self.receive()
        self.h._accept.assert_not_awaited();self.assertFalse(self.c.store.data['context'])
        self.assertFalse(self.c.store.data['records'])
    async def test_confirm_missing_identity_and_bad_redirect_fail(self):
        for response in ({'status':'confirmed','bot_token':'b'}, {'status':'scaned_but_redirect','redirect_host':'evil.test'}, {'status':'binded_redirect'}):
            self.c.store.data['account']={};tr=Transport([{'qrcode':'q','qrcode_img_content':'https://example.invalid/q'},response])
            self.c.api_factory=lambda *a:p.API(transport=tr)
            await self.c.login();self.assertFalse(self.c.status()['bound']);self.assertIsNone(self.c.qr)


if __name__=='__main__':unittest.main()
