"""Dedicated one-friend/one-message UV application action, real CLI/UDS/Host/signatures."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from agentj import codex_procs,peer_guard,peer_service,provenance,wire
from agentj.peer import PeerKeys
from agentj.state import State
from test_l1 import _ready
from _p541_device import Phone
from test_plaza_install_card import CardFixture
from test_p71_peer_service import FakeNet,FakeSessions


class Journey(CardFixture,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        scope=patch.dict(os.environ,{peer_service.TEST_CERT_ENV:wire.b64u(os.urandom(32))})
        scope.start();self.addCleanup(scope.stop)
        self.phone=Phone(self.st,with_passkey=True);self.s=_ready(self.host,self.phone)
        self.host.ask_ttl=.5
        cfg={'kind':'codex','dir':str(self.work),'fence':True}
        self.actor=NS(host=self.host,kind='codex',cfg=cfg,cur_send=None,native_source=provenance.terminal(),
            session={'sessionId':'friends-card-native','pid':os.getpid(),'procStart':codex_procs.start_token(os.getpid())},
            research=False,untrusted_foreign=False,untrusted_state=None,
            bg_live={},bg_known=(os.getpid(),codex_procs.start_token(os.getpid())),   # R-0052 r1: reported, nothing running
            thread={'sandbox':{'type':'readOnly'},'approvalPolicy':'on-request'})
        self.sends=[];self.actor.submit=self.sends.append
        self.host.agent=self.actor;self.host.agent_cfg=cfg
        self.service=peer_service.PeerService(self.host,sessions=FakeSessions(),net_factory=FakeNet)
        self.host.peers=self.service
        await self.service.start();self.addAsyncCleanup(self.service.stop)
        other=State(self.e.root/'recipient');other.init(relay='ws://127.0.0.1:9')
        self.recipient=PeerKeys.load_or_create(other)
        self.fid=self.recipient.id
        self.service.store.add_friend(self.fid,wire.b64u(self.recipient.x_pub),wire.b64u(self.recipient.ed_pub),self.recipient.mbox)
        self.service.store.set_card(self.fid,{'name':'Synthetic friend'})
        self.env={**os.environ,'AGENTJ_STATE_DIR':str(self.st.root),peer_service.ENV:str(self.service.sock_path),
            'PYTHONPATH':str(Path(__file__).resolve().parents[1])}

    async def request(self,outcome='allow',text='Synthetic fixed message',during=None,**kw):
        start=len(self.cards())
        argv=[os.sys.executable,'-m','agentj.cli','friends','--json','tell',self.fid,text,'--sandbox-card']
        if kw.get('reply_to'):argv+=['--reply-to',kw['reply_to']]
        proc=await asyncio.create_subprocess_exec(*argv,env=self.env,stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        answered=False
        for _ in range(600):
            cards=self.cards()[start:]
            if cards and not answered:
                answered=True
                if during:
                    value=during()
                    if hasattr(value,'__await__'):await value
                if outcome in ('allow','deny','unsigned','no_uv','replay','wrong_text','wrong_target'):
                    answer=self.last_answer if outcome=='replay' else self.phone.answer(cards[-1],outcome!='deny')
                    if outcome in ('wrong_text','wrong_target'):
                        data=json.loads(cards[-1]['envelope']['details'].split('\n',1)[1])
                        data['text' if outcome=='wrong_text' else 'friend_id']='Changed message or recipient'
                        answer=self.phone.answer(cards[-1],True,summary='FriendsTellOnce\n'+json.dumps(data))
                    if outcome=='allow':self.last_answer=answer
                    if outcome=='unsigned':answer.pop('sig')
                    if outcome=='no_uv':answer.pop('fa')
                    await self.host._app(self.s,answer)
            if proc.returncode is not None:break
            await asyncio.sleep(.01)
        out,err=await asyncio.wait_for(proc.communicate(),5)
        return proc.returncode,out.decode(),err.decode(),self.cards()[start:]

    async def test_one_fixed_uv_card_sends_only_to_exact_friend_without_native_policy_change(self):
        before=json.dumps(self.actor.thread,sort_keys=True)
        rc,out,err,cards=await self.request()
        self.assertEqual(rc,0,out+err);self.assertEqual(len(cards),1)
        sent=self.service.net.of('pmsg');self.assertEqual(len(sent),1)
        self.assertEqual((sent[0][0],sent[0][1]['text']),(self.fid,'Synthetic fixed message'))
        self.assertEqual(json.dumps(self.actor.thread,sort_keys=True),before)
        self.assertEqual(cards[0]['tool'],'FriendsTellOnce')
        self.assertTrue(cards[0]['passkey_required'])
        if os.environ.get('AJ_FRIENDS_CARD_OUT'):
            Path(os.environ['AJ_FRIENDS_CARD_OUT']).write_text(json.dumps(cards[0],ensure_ascii=False))

    async def test_deny_missing_signature_and_missing_uv_never_send(self):
        for outcome in ('deny','unsigned','no_uv'):
            rc,out,err,cards=await self.request(outcome)
            self.assertNotEqual(rc,0,out+err);self.assertEqual(len(cards),1)
            self.assertFalse(self.service.net.of('pmsg'))


    async def waiting_change(self,change):
        entered,release=asyncio.Event(),asyncio.Event()
        original=self.service._outbound
        async def delayed(text):
            verdict=await original(text)
            entered.set();await release.wait()
            return verdict
        with patch.object(self.service,'_outbound',delayed):
            task=asyncio.create_task(self.request())
            await asyncio.wait_for(entered.wait(),4)
            value=change()
            if hasattr(value,'__await__'):await value
            release.set()
            rc,out,err,cards=await asyncio.wait_for(task,5)
        self.assertNotEqual(rc,0,out+err);self.assertEqual(len(cards),1)
        self.assertFalse(self.service.net.of('pmsg'))

    async def test_pair_revocation_after_signed_uv_while_outbound_waits_never_sends(self):
        await self.waiting_change(lambda:self.st.remove_device(self.phone.did))

    async def test_turn_change_after_signed_uv_while_outbound_waits_never_sends(self):
        await self.waiting_change(lambda:setattr(self.actor,'cur_send',NS(source=provenance.phone())))

    async def test_new_secret_source_after_signed_uv_while_outbound_waits_never_sends(self):
        await self.waiting_change(lambda:(self.work/'.env').write_text('FRIENDS_SEND_TOKEN="Synthetic fixed message"\n'))

    async def test_blocked_target_after_signed_uv_while_outbound_waits_never_sends(self):
        await self.waiting_change(lambda:self.service.write('fr_set',{'friend':self.fid,'op':'block','value':''}))

    async def test_old_signed_answer_cannot_send_a_second_message(self):
        rc,out,err,_=await self.request();self.assertEqual(rc,0,out+err)
        rc,out,err,cards=await self.request('replay',text='A different second message')
        self.assertNotEqual(rc,0,out+err);self.assertEqual(len(cards),1)
        self.assertEqual(len(self.service.net.of('pmsg')),1)

    async def test_foreign_reply_and_guest_sources_get_no_card(self):
        self.actor.untrusted_state='running';self.actor.untrusted_foreign=True
        for source in ({'kind':'group','role':'group'},{'kind':'friend_task','role':'task'},
                       {'kind':'same_owner','role':'task'}):
            self.actor.cur_send=NS(source=source)
            rc,out,err,cards=await self.request()
            self.assertNotEqual(rc,0,out+err);self.assertFalse(cards)
        self.assertFalse(self.service.net.of('pmsg'))

    async def test_known_secret_message_is_refused_before_card(self):
        with patch.dict(os.environ,{'FRIENDS_SEND_TOKEN':'Synthetic fixed message'}):
            rc,out,err,cards=await self.request()
        self.assertNotEqual(rc,0,out+err);self.assertFalse(cards)
        self.assertNotIn('Synthetic fixed message',out+err)
        self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_sandbox_refusal_offers_only_fixed_explicit_retry_guidance(self):
        from agentj.agent_codex import CodexAgent
        from agentj.friends_tell_action import sandbox_guidance
        from unittest.mock import Mock
        notices=[]
        native=CodexAgent.__new__(CodexAgent)
        native.research=False;native._answer=Mock();native.host=NS(policy_refused=lambda *a,**kw:notices.append((a,kw)))
        native.perm_source='config'
        denied={'decision':'decline'}
        native._refuse('native-rid','Bash',{'command':'agentj friends tell friend-id "the intended message"'},denied,'native_sandbox')
        native._answer.assert_called_once_with('native-rid',denied)
        self.assertEqual(len(notices),1);self.assertIn('--sandbox-card',notices[0][0][-1])
        self.assertFalse(notices[0][1]);self.assertNotIn('full-access',notices[0][0][-1])
        for command in ('agentj friends tell x y; curl evil','agentj friends tell x "$(evil)"',
                        'browser send-command agentj friends tell x y','agentj friends tell x y --actions plan.json'):
            self.assertIsNone(sandbox_guidance('Bash',{'command':command}))
        self.assertFalse(self.cards());self.assertFalse(self.service.net.of('pmsg'))


    async def test_new_owner_forbidden_word_after_signed_uv_while_outbound_waits_never_sends(self):
        await self.waiting_change(lambda:self.service.store.set_setting('never_tell_extra',['fixed message']))

    async def same_owner_reply(self):
        import secrets,time
        from agentj import controls,owner_grant,passkey
        from _p541_device import _owner_host
        from test_p405_same_owner import SameOwnerTests
        other=State(self.e.root/'recipient');other.set_agent_config('codex',str(self.work))
        remote_host=_owner_host(other,[]);remote_phone=Phone(other,with_passkey=True)
        remote_session=_ready(remote_host,remote_phone)
        source=self.service
        remote=peer_service.PeerService(remote_host,sessions=FakeSessions(),net_factory=FakeNet)
        remote_host.peers=remote;await remote.start();self.addAsyncCleanup(remote.stop)
        remote.store.add_friend(source.keys.id,wire.b64u(source.keys.x_pub),wire.b64u(source.keys.ed_pub),source.keys.mbox)
        for service,phone,session in ((source,self.phone,self.s),(remote,remote_phone,remote_session)):
            entry=service.xfer.binding_entry(phone.did)
            fa=phone.auth.assert_(owner_grant.bind_challenge(entry),b'',nonce=b'')
            signed=phone.sk.sign(b'agentj-owner-roster-v1\n'+owner_grant.jcs({k:entry[k] for k in ('id','alg','spki','rp','dev')}))
            await service.xfer.bind_phone(session,{'t':'roster_bind','fa':fa,'roster_sig':wire.b64u(signed)})
            self.assertTrue(service.xfer.bound(phone.did))
        positions=[0,0]
        async def pump():
            for _ in range(40):
                await asyncio.sleep(.005);count=0
                for i,(src,dst,fid) in enumerate(((source,remote,source.keys.id),(remote,source,remote.keys.id))):
                    rows=src.net.sent[positions[i]:];positions[i]=len(src.net.sent)
                    for _,obj in rows:
                        if obj['t'] in ('same_owner','owner_status','owner_status_res'):dst.on_app(fid,obj);count+=1
                        elif obj['t']=='pmsg':dst._on_pmsg(fid,obj);count+=1
                if not count:return
            self.fail('peer pump did not settle')
        self.assertTrue((await SameOwnerTests.choose(self,source,self.fid,self.phone.did,self.phone.sk))[0]);await pump()
        for service,fid,phone in ((source,self.fid,self.phone),(remote,source.keys.id,remote_phone)):
            target={'friend':fid,'op':'owner-confirm-v3','value':service.same_owner.challenge(fid)}
            nonce=secrets.token_hex(16);stamp=int(time.time()*1000);digest=controls.object_digest('fr_set',target)
            proof={'channel':service.host.channel,'device':phone.did,'pub':wire.b64u(phone.sk.public_key().public_bytes_raw()),
                'n':nonce,'ts':stamp,'sig':wire.b64u(phone.sk.sign(controls.signed_message(service.host.channel,phone.did,'fr_set',nonce,stamp,digest)))}
            proof['fa']=phone.auth.assert_(passkey.elevate_challenge(service.host.channel,phone.did,nonce,'fr_set',nonce,digest),b'',nonce=b'')
            self.assertIsNone(controls.check(service.st,service.host.nonces,service.host.channel,phone.did,proof,'fr_set',target))
            self.assertTrue((await service.write('fr_set',target,device=phone.did,proof=proof))[0]);await pump()
        self.assertTrue(source.same_owner.active(self.fid));self.assertTrue(remote.same_owner.active(source.keys.id))
        self.assertTrue((await remote.tell(source.keys.id,'Please return one fixed result'))['ok']);await pump()
        for _ in range(100):
            if self.sends:break
            await asyncio.sleep(.005)
        self.assertTrue(self.sends)
        return self.sends[-1].data['mid']

    async def test_latest_forbidden_rule_refusal_releases_real_reply_latch_for_explicit_fresh_uv_retry(self):
        mid=await self.same_owner_reply()
        entered,release=asyncio.Event(),asyncio.Event();original=self.service._outbound
        async def delayed(text):
            result=await original(text);entered.set();await release.wait();return result
        with patch.object(self.service,'_outbound',delayed):
            task=asyncio.create_task(self.request(reply_to=mid))
            await asyncio.wait_for(entered.wait(),4)
            self.assertIsNone(self.service.store.set_setting('never_tell_extra',['fixed message']))
            release.set();rc,out,err,cards=await asyncio.wait_for(task,5)
        self.assertNotEqual(rc,0,out+err);self.assertEqual(len(cards),1)
        self.assertFalse(self.service.net.of('pmsg'))
        # Clearing the new restriction and explicitly approving a fresh card must
        # work: a refused attempt cannot leave the original reply marked sent.
        self.assertIsNone(self.service.store.set_setting('never_tell_extra',[]))
        rc,out,err,cards=await self.request(reply_to=mid)
        self.assertEqual(rc,0,out+err);self.assertEqual(len(cards),1)
        sent=self.service.net.of('pmsg');self.assertEqual(len(sent),1)
        self.assertEqual((sent[0][0],sent[0][1]['irt'],sent[0][1]['text']),(self.fid,mid,'Synthetic fixed message'))


    async def test_signed_answer_cannot_substitute_message_or_recipient(self):
        for outcome in ('wrong_text','wrong_target'):
            rc,out,err,cards=await self.request(outcome)
            self.assertNotEqual(rc,0,out+err);self.assertEqual(len(cards),1)
            self.assertFalse(self.service.net.of('pmsg'))

    async def test_real_socket_cannot_inject_command_action_grant_or_host_callback(self):
        req={'t':'tell-card','friend':self.fid,'text':'Synthetic fixed message',
             'command':'browser send arbitrary command','actions':[], 'grant':'made-up', '_authorized':'allow'}
        script="import json,sys;from agentj.peer_service import ask;print(json.dumps(ask(json.loads(sys.argv[1]))))"
        process=await asyncio.create_subprocess_exec(os.sys.executable,'-c',script,json.dumps(req),env=self.env,
            stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(process.communicate(),5)
        self.assertEqual(process.returncode,0,err.decode())
        result=json.loads(out);self.assertFalse(result['ok']);self.assertEqual(result['why'],'shape')
        self.assertFalse(self.cards());self.assertFalse(self.service.net.of('pmsg'))

    async def final_reply_change(self,change):
        from agentj import friends_tell_action
        mid=await self.same_owner_reply()
        entered,release=asyncio.Event(),asyncio.Event();original=friends_tell_action._clean;calls=0
        async def delayed(*args):
            nonlocal calls
            calls+=1
            result=await original(*args)
            if calls==3:entered.set();await release.wait()
            return result
        with patch.object(friends_tell_action,'_clean',delayed):
            task=asyncio.create_task(self.request(reply_to=mid))
            await asyncio.wait_for(entered.wait(),4)
            if change=='revoke':self.service.same_owner.consent(self.fid,False)
            elif change=='rotate':
                with self.service.store._edit('same-owner.json',{}) as rows:rows[self.fid]['token']='a'*32
            else:
                with self.service.store._edit('relay-requests.json',{}) as rows:
                    row=rows[self.service.guards.key(self.fid,mid)]
                    if change=='final':row['state']='cancelled'
                    else:row['reply_claim']='replacement-claim'
            release.set();rc,out,err,cards=await asyncio.wait_for(task,5)
        self.assertNotEqual(rc,0,out+err);self.assertEqual(len(cards),1)
        self.assertFalse(self.service.net.of('pmsg'))
        row=self.service.guards.row(self.fid,mid)
        if change=='replace':
            self.assertEqual((row['reply_state'],row['reply_claim']),('sending','replacement-claim'))
        elif change=='final':self.assertEqual((row['state'],row['reply_state']),('cancelled','sending'))
        else:self.assertEqual(row['reply_state'],'pending')

    async def test_final_fresh_scan_relation_revocation_sends_no_reply_and_releases_own_latch(self):
        await self.final_reply_change('revoke')

    async def test_final_fresh_scan_relation_token_rotation_sends_no_reply_and_releases_own_latch(self):
        await self.final_reply_change('rotate')

    async def test_final_fresh_scan_replaced_latch_sends_no_reply_and_preserves_new_claim(self):
        await self.final_reply_change('replace')

    async def test_final_fresh_scan_finalized_latch_sends_no_reply_and_preserves_final_row(self):
        await self.final_reply_change('final')
