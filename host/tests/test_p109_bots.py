import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agentj.bots.store import Store,BotError,config
from agentj.bots.engine import Engine
from agentj.bots.provider import Provider,Result,resolve_bot,invoke
from agentj.bots.telegram import Channel,api,token
from agentj.provider_profiles import atomic

class Bots(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.s=Store(self.root/'bots')
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def bot(self,**values):
        c=self.s.create({'slug':'p109-test',**values});return c['id']
    def engine(self,model=None,decision=None):
        self.models=[];self.decisions=[]
        def call(p,messages,*args):
            self.models.append((p,messages));return Result('Hello / 你好',10)
        def safety(p,messages,*args):
            self.decisions.append(messages);return Result('{"allow":true}',1)
        return Engine(self.s,'codex',model_call=model or call,decision_call=decision or safety,provider_resolve=lambda *args:Provider('','m','chat','dummy'))
    def test_templates_and_legacy_defaults(self):
        for name in ('customer_service','companion','paid_qa'):
            c=config({'slug':'p109-test','template':name});self.assertEqual(c['template'],name);self.assertTrue(c['prompt'])
        self.assertIn('AI',config({'slug':'p109-test','template':'companion'})['prompt'])
        with self.assertRaises(BotError):config({'slug':'p109-test','template':'other'})
    def test_strict_public_config(self):
        for changes in ({'telegram':{'enabled':True,'token':'no'}},{'paid_qa':{'free_questions':True,'payment_url':''}}, {'paid_qa':{'free_questions':1,'payment_url':'http://example.com'}},{'paid_qa':{'free_questions':1,'payment_url':'https://user:pass@example.com'}},{'provider':{'source':'own'}},{'own_model':{'base_url':'http://localhost','model':'m','daily_tokens':1}}):
            with self.subTest(changes=changes),self.assertRaises(BotError):config({'slug':'p109-test',**changes})
    def test_own_model_fields_refuse_pasted_secret(self):
        with self.assertRaisesRegex(BotError,'config_contains_secret'):config({'slug':'p109-test','provider':{'source':'own'},'own_model':{'base_url':'https://openrouter.ai/api/v1','model':'sk-'+'x'*48,'daily_tokens':100}})
    async def test_paid_daily_count_idempotency_and_isolation(self):
        bid=self.bot(template='paid_qa',paid_qa={'free_questions':1,'payment_url':'https://example.com/pay'},enabled=True,terms_accepted=True)
        e=self.engine();a,cap=self.s.visitor(bid);b,capb=self.s.visitor(bid)
        first=await e.reply(bid,a,cap,'1'*32,'Hi');self.assertEqual(first['status'],'done')
        paid=await e.reply(bid,a,cap,'2'*32,'Again');self.assertEqual(paid['status'],'payment_required');self.assertIn('https://example.com/pay',paid['text']);self.assertEqual(len(self.models),1)
        self.assertEqual(paid,await e.reply(bid,a,cap,'2'*32,'Again'))
        await e.reply(bid,b,capb,'3'*32,'Hi');self.assertEqual(len(self.models),2)
        paid2=await e.reply(bid,a,cap,'4'*32,'Again');self.assertEqual(paid2['status'],'payment_required');self.assertEqual(len(self.models),2)
        self.assertEqual(len(self.decisions),6)
        tomorrow=self.s.now()+86401
        with patch.object(self.s,'now',return_value=tomorrow):await e.reply(bid,a,cap,'5'*32,'Hi')
        self.assertEqual(len(self.models),3)
    async def test_payment_output_fail_closed(self):
        bid=self.bot(template='paid_qa',paid_qa={'free_questions':0,'payment_url':'https://example.com'},enabled=True,terms_accepted=True)
        e=self.engine(decision=lambda *args:Result('{"allow":false}',1));v,cap=self.s.visitor(bid)
        r=await e.reply(bid,v,cap,'1'*32,'Hi');self.assertEqual(r['status'],'refused');self.assertNotIn('https://example.com',r['text']);self.assertFalse(self.models)
    async def test_companion_uses_template_and_jev(self):
        bid=self.bot(template='companion',enabled=True,terms_accepted=True);e=self.engine();v,cap=self.s.visitor(bid)
        r=await e.reply(bid,v,cap,'1'*32,'How are you?');self.assertEqual(r['status'],'done');self.assertEqual(len(self.decisions),2);self.assertIn('emotional dependence',self.models[0][1][0]['content'])
    def own(self,limit=10000):
        bid=self.bot(provider={'source':'own'},own_model={'base_url':'https://openrouter.ai/api/v1','model':'test/model','daily_tokens':limit})
        atomic(self.s.secrets(bid)/'BOT_MODEL_KEY',b'dummy-test-key');return bid
    def test_own_resolution_and_key_privacy(self):
        bid=self.own();p=resolve_bot(self.s,bid,'codex');self.assertEqual(p.api,'chat');self.assertEqual(p.key,'dummy-test-key');self.assertNotIn(p.key,str(p.public()));self.assertNotIn(p.key,repr(p));self.assertNotIn(p.key,str(self.s.get(bid)))
        (self.s.secrets(bid)/'BOT_MODEL_KEY').unlink()
        with self.assertRaisesRegex(BotError,'key_provider_required'):resolve_bot(self.s,bid,'codex')
    def test_independent_budgets_persist_unknown_usage(self):
        bid=self.own(100);c=self.s.get(bid);c['limits']['host_tokens']=10;self.s.save(bid,c)
        own=self.s.reserve(bid,'','',100,'model');self.assertTrue(own)
        audit=self.s.reserve(bid,'','',10,'jev');self.assertTrue(audit)
        for kind in ('model','jev'):
            with self.assertRaisesRegex(BotError,'token_limit'):self.s.reserve(bid,'','',1,kind)
        self.s.close();self.s=Store(self.root/'bots');self.s.recover()
        with self.assertRaisesRegex(BotError,'token_limit'):self.s.reserve(bid,'','',1,'model')
    def test_openrouter_chat_api(self):
        calls=[]
        def transport(url,method,body,headers,**kw):
            calls.append((url,body,headers));return {'choices':[{'message':{'content':'OK'}}],'usage':{'total_tokens':4}}
        r=invoke(Provider('https://openrouter.ai/api/v1','test/model','chat','dummy'),[{'role':'user','content':'Hi'}],transport=transport)
        self.assertEqual(r.text,'OK');self.assertTrue(calls[0][0].endswith('/chat/completions'));self.assertEqual(calls[0][2]['Authorization'],'Bearer dummy')
    def channel(self,bid,request=None):
        atomic(self.s.secrets(bid)/'TELEGRAM_BOT_TOKEN',b'123456:abcdefghijklmnopqrstuvwxyz')
        e=self.engine();runtime=SimpleNamespace(manager=SimpleNamespace(state=lambda:self.s),host=SimpleNamespace(stopped=lambda:False),engine=lambda:e)
        self.sent=[]
        def api(*args):self.sent.append(args);return {}
        return Channel(runtime,bid,request or api)
    def update(self,uid=123,n=1,text='Hi',kind='private'):
        return {'update_id':n,'message':{'chat':{'id':uid,'type':kind},'from':{'id':uid},'text':text}}
    async def test_telegram_reuses_engine_sessions_and_idempotency(self):
        bid=self.bot(enabled=True,terms_accepted=True,telegram={'enabled':True});ch=self.channel(bid)
        await ch.handle(self.update());await ch.handle(self.update(n=2));await ch.handle(self.update(uid=456,n=3))
        self.assertEqual(len(self.sent),3);self.assertEqual(self.s.db.execute('SELECT count(*) FROM visitors').fetchone()[0],2)
        await ch.handle(self.update(n=2));self.assertEqual(len(self.models),3)
        self.assertEqual(len(self.decisions),6)
    async def test_telegram_chinese_text_reaches_model_wire_for_both_templates(self):
        # Real private Telegram message shape, including Chinese text from Leo's failed QA.
        previous=None
        for template, text in (
            ('companion', 'Agent J 测试：今天有点累，请用一句中文友好地聊聊。'),
            ('paid_qa', 'Agent J 测试：Python list 和 tuple 的区别，用一句话回答。'),
        ):
            with self.subTest(template=template):
                if previous:
                    c=self.s.get(previous);c['enabled']=False;self.s.save(previous,c)
                bid=self.bot(slug='regression-'+template.replace('_','-'),template=template,enabled=True,terms_accepted=True,telegram={'enabled':True},paid_qa={'free_questions':5,'payment_url':''})
                previous=bid
                ch=self.channel(bid);bodies=[]
                def transport(url,method,body,headers,**kw):
                    bodies.append(body)
                    return {'choices':[{'message':{'content':'中文相关回复'}}],'usage':{'total_tokens':10}}
                ch.runtime.engine().model_call=lambda p,m,t,n:invoke(p,m,t,n,transport=transport)
                update=self.update(n=9001,text=text)
                update['message'].update(message_id=21,date=1791594300)
                update['message']['from'].update(is_bot=False,language_code='zh-hans')
                await ch.handle(update)
                self.assertEqual(bodies[0]['messages'][-1],{'role':'user','content':text})
                self.assertIn('Respond in the language of the visitor',bodies[0]['messages'][0]['content'])
                # A second turn must preserve both original user text and prior assistant content.
                await ch.handle(self.update(n=9002,text='请继续。'))
                self.assertEqual(bodies[1]['messages'][1:],[{'role':'user','content':text},
                    {'role':'assistant','content':'中文相关回复'},{'role':'user','content':'请继续。'}])
                c=self.s.get(bid);c['enabled']=False;self.s.save(bid,c)

    async def test_telegram_failure_paths_reply_and_polling_continues(self):
        from agentj.bots.engine import UNAVAILABLE
        bid=self.bot(template='paid_qa',enabled=True,terms_accepted=True,telegram={'enabled':True})
        ch=self.channel(bid)
        for failure in ('admission','engine','timeout'):
            with self.subTest(failure=failure):
                self.sent.clear()
                async def reply(*args):
                    if failure=='timeout':await asyncio.Event().wait()
                    raise RuntimeError('private exception must not escape')
                ch.runtime.engine=lambda:SimpleNamespace(reply=reply)
                with patch('agentj.bots.telegram.TURN_TIMEOUT',.01,create=True):
                    if failure=='admission':
                        with patch.object(self.s,'admit_visitor',side_effect=BotError('visitor_limit')):
                            await ch.handle(self.update(text='Agent J 测试：Python list 可以修改吗？'))
                    else:await ch.handle(self.update(text='Agent J 测试：Python list 可以修改吗？'))
                self.assertEqual(len(self.sent),1)
                self.assertEqual(self.sent[0][2]['text'],UNAVAILABLE)
        async def good(*args):return {'text':'list 可以修改，tuple 不可以。'}
        ch.runtime.engine=lambda:SimpleNamespace(reply=good)
        await ch.handle(self.update(n=2));self.assertIn('list',self.sent[-1][2]['text'])

    async def test_telegram_paid_first_answer_then_link_and_jev_refusal_reply(self):
        from agentj.bots.engine import UNAVAILABLE
        bid=self.bot(template='paid_qa',paid_qa={'free_questions':1,'payment_url':'https://example.com/pay'},enabled=True,terms_accepted=True,telegram={'enabled':True})
        ch=self.channel(bid)
        await ch.handle(self.update(text='Agent J 测试：list 和 tuple 哪个可以修改？'))
        await ch.handle(self.update(n=2,text='Agent J 测试：第二问。'))
        self.assertIn('https://example.com/pay',self.sent[-1][2]['text'])
        self.assertEqual(len(self.models),1)
        ch.runtime.engine().decision_call=lambda *args:Result('{"allow":false}',1)
        await ch.handle(self.update(n=3,text='Agent J 测试：第三问。'))
        self.assertEqual(self.sent[-1][2]['text'],UNAVAILABLE)

    async def test_telegram_filters_groups_bots_oversize(self):
        bid=self.bot(enabled=True,terms_accepted=True,telegram={'enabled':True});ch=self.channel(bid)
        await ch.handle(self.update(kind='group'));await ch.handle(self.update(text='x'*2001))
        u=self.update();u['message']['from']['is_bot']=True;await ch.handle(u)
        self.assertFalse(self.sent);self.assertFalse(self.models)
    async def test_telegram_pause_and_epoch_block_delivery(self):
        bid=self.bot(enabled=True,terms_accepted=True,telegram={'enabled':True});ch=self.channel(bid)
        c=self.s.get(bid);c['telegram']['enabled']=False;self.s.save(bid,c)
        await ch.handle(self.update());self.assertFalse(self.sent)
        c['telegram']['enabled']=True;self.s.save(bid,c);await ch.handle(self.update());self.assertFalse(self.sent)
    async def test_telegram_estop_during_model_prevents_send(self):
        bid=self.bot(enabled=True,terms_accepted=True,telegram={'enabled':True});ch=self.channel(bid)
        async def reply(*args):ch.runtime.host.stopped=lambda:True;return {'text':'hello'}
        ch.runtime.engine=lambda:SimpleNamespace(reply=reply)
        await ch.handle(self.update());self.assertFalse(self.sent)
    def test_token_rotation_cursor_and_private_file(self):
        bid=self.bot();ch=self.channel(bid);ch.advance(4);self.assertEqual(ch.offset(),4)
        atomic(self.s.secrets(bid)/'TELEGRAM_BOT_TOKEN',b'123456:zyxwvutsrqponmlkjihgfedcba');new=Channel(ch.runtime,bid);self.assertEqual(new.offset(),0)
        path=self.s.secrets(bid)/'TELEGRAM_BOT_TOKEN';path.chmod(0o644)
        with self.assertRaises(BotError):token(self.s,bid)
    def test_telegram_setup_verifies_bot_identity(self):
        from agentj.bots.telegram import verify
        bid=self.bot();self.channel(bid)
        self.assertTrue(verify(self.s,bid,lambda *args:{'id':123456,'is_bot':True}))
        for value in (None,{}, {'id':123456,'is_bot':False},{'id':'123','is_bot':True}):
            with self.assertRaisesRegex(BotError,'telegram_unavailable'):verify(self.s,bid,lambda *args:value)
    def test_existing_native_gemini_bot_keeps_hooks_skills_and_tools_disabled(self):
        import json
        from agentj.bots.native import command
        env={}
        command(Provider('','test-model','native','',name='gemini'),self.root,env,selected='/fixture/gemini')
        settings=json.loads((self.root/'gemini.json').read_text())
        self.assertEqual(settings['hooksConfig'],{'enabled':False})
        self.assertEqual(settings['skills'],{'enabled':False})
        self.assertEqual(settings['tools']['exclude'],['*'])
        self.assertEqual(settings['mcpServers'],{})
    def test_telegram_errors_never_echo_token(self):
        secret='dummy-secret'
        def transport(*args,**kwargs):raise ValueError(args[0])
        with self.assertRaisesRegex(BotError,'^telegram_unavailable$'):api(secret,'getMe',{},transport)
        with self.assertRaisesRegex(BotError,'^telegram_unavailable$'):api(secret,'getMe',{},lambda *a,**kw:{'ok':False,'description':secret})

class OwnerKeys(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict('os.environ',{'XDG_CONFIG_HOME':self.tmp.name+'/config'});self.env.start()
        from agentj.bots.owner import Manager
        from unittest.mock import AsyncMock
        root=Path(self.tmp.name)/'state';root.mkdir()
        self.elevate=SimpleNamespace(request=AsyncMock(return_value={'result':'saved'}))
        host=SimpleNamespace(st=SimpleNamespace(root=root,perm_dir=root/'perm'),channel='p109',elevate=self.elevate,agent=None,agent_cfg={'kind':'codex'})
        self.m=Manager(host);self.s=self.m.state();self.bid=self.s.create({'slug':'p109-owner','enabled':True,'terms_accepted':True,'telegram':{'enabled':True}})['id']
    def tearDown(self):self.s.close();self.env.stop();self.tmp.cleanup()
    async def test_telegram_rotation_pauses_and_uses_secret_card(self):
        result=await self.m.write({'op':'telegram_key','id':self.bid})
        self.assertTrue(result['stored']);self.assertFalse(self.s.get(self.bid)['telegram']['enabled'])
        card=self.elevate.request.call_args.args[0];self.assertEqual(card['name'],'TELEGRAM_BOT_TOKEN');self.assertNotIn('value',card)
    async def test_model_key_card_does_not_mutate_main_profile(self):
        await self.m.write({'op':'model_key','id':self.bid});card=self.elevate.request.call_args.args[0]
        self.assertEqual(card['name'],'BOT_MODEL_KEY');self.assertTrue(card['dest'].endswith('/BOT_MODEL_KEY'));self.assertEqual(self.s.get(self.bid)['provider'],{'source':'main'})
    async def test_secret_values_refused_in_request(self):
        with self.assertRaisesRegex(BotError,'invalid_request'):await self.m.write({'op':'telegram_key','id':self.bid,'token':'dummy'})
        self.elevate.request.assert_not_awaited()
    async def test_declined_token_remains_paused(self):
        self.elevate.request.return_value={'result':'denied'}
        with self.assertRaisesRegex(BotError,'key_required'):await self.m.write({'op':'telegram_key','id':self.bid})
        self.assertFalse(self.s.get(self.bid)['telegram']['enabled'])

if __name__=='__main__':unittest.main()
