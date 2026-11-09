"""0.30 real mail completion and private secret-callback identity regressions."""
import _hermetic
import asyncio
import copy
import json
import pathlib
import secrets
import tempfile
import unittest
from unittest.mock import patch
from agentj import approval_jev, approval_review, application_ingress, mail, wire
from test_l1 import _ready
from _p541_device import Phone, _owner_host
from agentj.state import State
from mailfake import FakeSMTP


class Completion(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=State(pathlib.Path(self.tmp.name)/'state');self.st.init()
        self.sent=[];self.h=_owner_host(self.st,self.sent);self.h.ask_ttl=.15
        self.ph=Phone(self.st,with_passkey=True);self.ph.policy_host=self.h
        self.session=_ready(self.h,self.ph)
        self.m=self.h.mail=mail.Mail(self.h)
        self.smtp=FakeSMTP('agent@example.com','fixture-password');self.addCleanup(self.smtp.close)
        cfg=mail.blank_config();cfg.update(enabled=True,address='agent@example.com',username='agent@example.com',
            imap={'host':'127.0.0.1','port':1,'security':'plain'},
            smtp={'host':'127.0.0.1','port':self.smtp.port,'security':'plain'},
            allow=[{'addr':'owner@example.com','role':'owner'},{'addr':'guest@example.com','role':'guest'}])
        self.cfg=self.m.set_config(mail.validate(cfg));mail.store_secret(self.st,b'fixture-password',self.cfg['address'])
        off=patch.object(approval_jev,'available',return_value=False);off.start();self.addCleanup(off.stop)
        self.addAsyncCleanup(self.stop)

    async def stop(self):
        for task in list(self.m.sending):task.cancel()
        await asyncio.gather(*list(self.m.sending),return_exceptions=True)
        self.h.elevate.cancel_all('stopped')
        await asyncio.gather(*list(self.h.elevate.review_requests),return_exceptions=True)

    def turn(self, role='owner', body='body'):
        turn=self.h.hist.add({'k':'email','text':'original mail','name':role},reply=body,end='done')
        self.h.cur_turn=turn['id']
        state=mail.read_state(self.st)
        state['routes'][str(turn['id'])]={'to':role+'@example.com','gen':self.cfg['generation'],
            'subject':'fixture','msgid':'<fixture@example.com>','refs':'','role':role,'verified':True,
            'explicit_owner':role=='owner','state':'awaiting'}
        mail.save_state(self.st,state)
        return turn

    async def finish(self, turn):
        self.m.completed(turn)
        tasks=list(self.m.sending)
        result=await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        return result

    async def test_actual_completion_once_without_a_generic_frame(self):
        turn=self.turn()
        self.m.completed(turn);self.m.completed(turn)
        self.assertEqual(len(self.m.sending),1)
        self.assertEqual(await asyncio.gather(*list(self.m.sending)),['sent'])
        await asyncio.sleep(0)
        self.assertEqual(len(self.smtp.sent),1)
        self.m.completed(turn)
        self.assertFalse(self.m.sending)
        self.assertFalse(self.m._reply_jobs)
        self.assertFalse(application_ingress.allowed(self.h))
        self.assertEqual((await self.h.ask('agentj mail send',{'to':['owner@example.com']}))['reason'],'application_ingress_unverified')
        self.assertFalse(self.h.asks)

    async def test_completion_scope_cannot_change_tool_input_or_task(self):
        turn=self.turn();original=self.h.ask;seen=[]
        async def observe(tool,inp,**kwargs):
            if tool=='agentj mail reply':
                self.assertFalse(application_ingress.allowed(self.h))
                other=await original('agentj mail send',inp)
                changed=await original(tool,{**inp,'subject':'different target'})
                child=await asyncio.create_task(original(tool,inp))
                for result in (other,changed,child):
                    self.assertEqual((result['behavior'],result['reason']),('deny','application_ingress_unverified'))
                seen.append(True)
            return await original(tool,inp,**kwargs)
        self.h.ask=observe
        try:self.assertEqual(await self.finish(turn),['sent'])
        finally:self.h.ask=original
        self.assertEqual(seen,[True]);self.assertEqual(len(self.smtp.sent),1)
        self.assertFalse(self.h.asks)

    async def test_copied_noncurrent_and_nonterminal_turns_do_not_mint_jobs(self):
        for mode in ('clone','noncurrent','open','failed','stopped'):
            with self.subTest(mode=mode):
                turn=self.turn()
                supplied=copy.deepcopy(turn) if mode=='clone' else turn
                if mode=='noncurrent':self.h.cur_turn=None
                elif mode in ('open','failed','stopped'):turn['end']=mode
                self.assertEqual(await self.finish(supplied),[])
                self.assertFalse(self.m._reply_jobs)
                self.assertFalse(self.smtp.sent)

    async def test_revocation_after_smtp_login_is_zero_data(self):
        for mode in ('config','route','epoch','history','body','stop'):
            with self.subTest(mode=mode):
                turn=self.turn();opened=mail.smtp_open
                def boundary(cfg,pw):
                    client=opened(cfg,pw)
                    if mode=='config':self.m.set_config({**mail.read_config(self.st),'reply_per_hour':7})
                    elif mode=='route':
                        state=mail.read_state(self.st);state['routes'][str(turn['id'])]['to']='guest@example.com';mail.save_state(self.st,state)
                    elif mode=='epoch':self.h.hist.epoch+=1
                    elif mode=='history':self.h.hist.turns[turn['id']]=copy.deepcopy(turn)
                    elif mode=='body':turn['reply']['text']='changed'
                    else:self.h.stopping.set()
                    return client
                with patch.object(mail,'smtp_open',side_effect=boundary):
                    self.assertEqual(await self.finish(turn),['failed'])
                self.assertFalse(self.smtp.sent)
                self.h.stopping.clear()
                self.cfg=mail.read_config(self.st)

    async def test_guest_reply_is_redacted_and_cannot_authorize_secret_commands(self):
        material='sk-'+'x'*48
        turn=self.turn('guest','api key '+material)
        self.assertEqual(await self.finish(turn),['sent'])
        self.assertEqual(len(self.smtp.sent),1)
        self.assertNotIn(material,str(self.smtp.sent))
        task=asyncio.create_task(self.h.ask('agentj secret send',{'name':'SYNTHETIC_NOT_READ'}))
        for _ in range(100):
            if self.h.asks or task.done():break
            await asyncio.sleep(.005)
        ask=next(iter(self.h.asks.values()));self.assertTrue(ask.require_passkey)
        answer=self.ph.answer(self.h._ask_msg(ask,self.session),True);answer.pop('fa',None)
        await self.h._answer(self.session,answer)
        self.assertFalse(ask.fut.done())
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(len(self.smtp.sent),1)

    async def test_pickup_await_cannot_restore_a_revoked_or_replaced_route(self):
        from agentj import owner_route
        for mode in ('removed','replaced'):
            with self.subTest(mode=mode):
                turn=self.turn(body='password=fixture-unique-secret-material-123456789')
                self.assertTrue(owner_route.prepare(self.h,turn['reply']['text'],'mail','en')[1].found)
                async def boundary(host,values,*args):
                    await asyncio.sleep(0)
                    state=mail.read_state(self.st)
                    if mode=='removed':state['routes'].pop(str(turn['id']))
                    else:state['routes'][str(turn['id'])]['to']='guest@example.com'
                    mail.save_state(self.st,state)
                    return {'cards':0,'withheld':1,'failed':1}
                with patch.object(owner_route,'deliver',side_effect=boundary):
                    self.assertEqual(await self.finish(turn),['stale'])
                self.assertFalse(self.smtp.sent)
                current=mail.read_state(self.st)['routes'].get(str(turn['id']))
                if mode=='removed':self.assertIsNone(current)
                else:self.assertEqual(current['to'],'guest@example.com')

    async def test_inflight_callbacks_keep_question_operation_identity(self):
        elevator=self.h.elevate
        def first(value):raise AssertionError('unapproved first callback')
        def second(value):raise AssertionError('unapproved second callback')
        card={'kind':'secret','name':'SYNTHETIC','purpose':'fixture','dest':'fixture',
              'dest_spec':{'mode':'file','path':str(pathlib.Path(self.tmp.name)/'never-written'),'key':'SYNTHETIC'},
              'cwd':'/','verify':'','verify_url':'','verify_header':'','verify_cmd':'','sink':first}
        entered=asyncio.Event();release=asyncio.Event();original=elevator._request_impl
        async def delayed(candidate,*args):
            if candidate['sink'] is first:
                entered.set();await release.wait()
            return await original(candidate,*args)
        with patch.object(elevator,'_request_impl',side_effect=delayed):
            a=asyncio.create_task(elevator.request(card));await entered.wait()
            b=asyncio.create_task(elevator.request({**card,'sink':second}))
            for _ in range(100):
                if elevator.cards:break
                await asyncio.sleep(.005)
            release.set()
            for _ in range(100):
                if len(elevator.cards)==2:break
                await asyncio.sleep(.005)
            target=next(c for c in elevator.cards.values() if c['sink'] is first)
            adapter=target['review_adapter'];eid=secrets.token_hex(16)
            obj={'t':'approval_review','id':adapter.rid,'op':'question','revision':adapter.review_revision,
                 'envelope_sha256':adapter.digest,'event_id':eid}
            obj['sig']=wire.b64u(self.ph.sk.sign(approval_review.review_message(self.h.channel,self.ph.did,
                adapter.rid,'question',adapter.review_revision,eid,adapter.digest)))
            await self.h._app(self.session,obj)
            self.assertEqual((await a)['result'],'questioned')
            repeated=await elevator.request(card)
            self.assertEqual((repeated['result'],repeated['id']),('questioned',target['id']))
            self.assertEqual(len(elevator.cards),2)
            elevator.cancel_all('denied');await b

    async def test_secret_card_sink_is_private_and_question_identity_is_exact(self):
        self.m.set_config(mail.apply_preset(mail.blank_config(),'gmail','demo@example.com'))
        from agentj import provenance
        with provenance.paired_request(lambda:self.h._paired_live(self.session)):
            inherited=await asyncio.create_task(self.m.secret_card(recover=False))
            self.assertEqual(inherited,{'result':'refused','why':'authority_changed'})
            self.h.sessions.pop(self.session.cid)
            revoked=await self.m.secret_card(recover=False)
            self.assertEqual(revoked,{'result':'refused','why':'authority_changed'})
            self.h.sessions[self.session.cid]=self.session
        self.assertFalse(self.h.elevate.cards)
        self.assertFalse(self.h.elevate.review_requests)
        # Another approver remains online when the original creator is revoked
        # after its preflight but before the worker's first instruction.
        other=Phone(self.st,'second');_ready(self.h,other,cid=12)
        entered=asyncio.Event();release=asyncio.Event();impl=self.h.elevate._request_impl
        async def delayed(*args):
            entered.set();await release.wait()
            return await impl(*args)
        async def owner_request():
            with provenance.paired_request(lambda:self.h._paired_live(self.session)):
                return await self.m.secret_card(recover=False,only=self.ph.did)
        with patch.object(self.h.elevate,'_request_impl',side_effect=delayed):
            delayed_task=asyncio.create_task(owner_request());await entered.wait()
            self.h.sessions.pop(self.session.cid);release.set()
            self.assertEqual(await delayed_task,{'result':'refused','why':'authority_changed'})
            await asyncio.sleep(0)
            self.assertFalse(self.h.elevate.cards)
            self.assertFalse(self.h.elevate.review_requests)
            self.h.sessions[self.session.cid]=self.session
        task=asyncio.create_task(self.m.secret_card(recover=False))
        for _ in range(100):
            if self.h.elevate.cards or task.done():break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(),task.exception() if task.done() else '')
        card=next(iter(self.h.elevate.cards.values()));adapter=card['review_adapter']
        eid=secrets.token_hex(16)
        obj={'t':'approval_review','id':adapter.rid,'op':'question','revision':adapter.review_revision,
             'envelope_sha256':adapter.digest,'event_id':eid}
        obj['sig']=wire.b64u(self.ph.sk.sign(approval_review.review_message(self.h.channel,self.ph.did,adapter.rid,
             'question',adapter.review_revision,eid,adapter.digest)))
        await self.h._app(self.session,obj)
        self.assertEqual((await task)['result'],'questioned')
        # Reconstruct only original public fields plus the same private executor.
        original={k:card[k] for k in ('kind','name','purpose','dest','dest_spec','cwd','verify','verify_url','verify_header','verify_cmd','sink')}
        repeated=await self.h.elevate.request(original)
        self.assertEqual((repeated['result'],repeated['id']),('questioned',card['id']))
        def different(value):raise AssertionError('unapproved sink executed')
        other=asyncio.create_task(self.h.elevate.request({**original,'sink':different}))
        for _ in range(100):
            if len(self.h.elevate.cards)>1 or other.done():break
            await asyncio.sleep(.005)
        self.assertFalse(other.done())
        self.assertEqual(len(self.h.elevate.cards),2)
        self.h.elevate.cancel_all('denied');await other
        self.assertEqual(await self.h.elevate.request({**original,'sink':'not-callable'}),{'result':'failed','why':'invalid_sink'})
        self.assertNotIn('private_callback',json.dumps(self.sent,default=str))
        self.assertNotIn('function ',json.dumps(self.sent,default=str))
