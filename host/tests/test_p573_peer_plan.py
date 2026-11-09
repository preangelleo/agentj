"""An authenticated same-owner pmsg carries concrete approval, never generic authority."""
import _hermetic  # noqa: F401
import asyncio
import copy
import json
import time
import unittest
from agentj import approvals, approval_jev, approval_review, wire
from test_p405_same_owner import SameOwnerTests as _SameOwnerTests
from test_p71_peer_service import pmsg, settle_pending
from _p541_device import _owner_host, Phone
from unittest.mock import patch

class PeerPlan(_SameOwnerTests):
    async def asyncSetUp(self):
        offline=patch.object(approval_jev,'available',return_value=False)
        offline.start();self.addCleanup(offline.stop)
        await super().asyncSetUp()

    async def wait_card(self, host, task):
        for _ in range(100):
            if host.asks or task.done():break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(),task.result() if task.done() else '')
        return next(iter(host.asks.values()))

    def root_phone(self, host):
        from types import SimpleNamespace
        # Existing cross-host root key, signed with the current v2 public card format.
        return SimpleNamespace(pub=wire.unb64u(host.st.devices()[self.odid]['pub']),
                               sk=self.osk,did=self.odid,channel=host.channel)

    async def native_tap(self, host, phone, session, command):
        task=asyncio.create_task(host.ask('Bash',{'command':command},_harness_permission=True))
        card=await self.wait_card(host,task)
        self.assertFalse(card.require_passkey)
        await host._answer(session,Phone.answer(phone,host._ask_msg(card,session),True))
        self.assertEqual((await task)['behavior'],'allow')

    async def test_p573_signed_plan_roundtrip_and_negative_boundaries(self):
        self.clock.t = time.time()
        await self.confirm()
        h = _owner_host(self.host.st, [])
        h.peers = self.svc
        self.svc.host = h
        h.agent.submit = self.sends.append
        commands = ['rm -rf ~/Documents/old', 'stripe charges create --amount 500']
        actions = [{"tool":"Bash", "input": {"command":commands[0]}}]
        origin = _owner_host(self.other.st, [])
        origin.ask_ttl=2
        h.ask_ttl=2
        origin.cur_turn = 7
        self.remote.host = origin
        origin.peers = self.remote
        from types import SimpleNamespace
        from test_l1 import _ready
        from test_p57_passkey import Authenticator, pk_record
        from agentj import passkey
        ph = SimpleNamespace(pub=wire.unb64u(origin.st.devices()[self.odid]['pub']), sk=self.osk, did=self.odid,
                             channel=origin.channel)
        auth = Authenticator()
        origin.st.set_passkey(ph.pub, pk_record(auth))
        session = _ready(origin, ph)
        # Stripe remains an unsupported raw executor, including the changed amount.
        for command in (commands[1], 'stripe charges create --amount 501'):
            plan=[{'tool':'Bash','input':{'command':command}}]
            self.assertFalse((await self.remote.cli({'t':'approve-actions','actions':plan}))['ok'])
        text = '请按已列动作完成清理与付款。'
        job = asyncio.create_task(self.remote.cli({'t':'tell','friend':self.a,'text':text,'actions':actions}))
        for _ in range(100):
            if origin.asks:break
            await asyncio.sleep(.005)
        card = next(iter(origin.asks.values()))
        self.assertFalse(card.require_passkey)
        self.assertIn(commands[0],card.summary)
        self.assertIs(json.loads(card.task_scope)["uv"],False)
        answer=Phone.answer(ph,origin._ask_msg(card,session),True)
        with patch.object(approval_jev,'available',return_value=False):
            await origin._answer(session,answer)
            self.assertTrue((await job)['ok'])
        other_phone = Phone(origin.st, with_passkey=True)
        other_session = _ready(origin, other_phone, cid=12)
        before = sum(obj['t']=='pmsg' for _,obj in self.remote.net.sent)
        wrong = asyncio.create_task(self.remote.cli({'t':'tell','friend':self.a,'text':text+' different key','actions':actions}))
        for _ in range(100):
            if origin.asks:break
            await asyncio.sleep(.005)
        wrong_card = next(iter(origin.asks.values()))
        wrong_answer = other_phone.answer(origin._ask_msg(wrong_card, other_session), True)
        await origin._answer(other_session, wrong_answer)
        result = await wrong
        self.assertEqual(result['why'],'root_reconfirm')
        self.assertIn('Reconfirm',result['message']) if origin.lang != 'zh' else self.assertIn('重新确认',result['message'])
        self.assertEqual(sum(obj['t']=='pmsg' for _,obj in self.remote.net.sent),before)
        self.assertEqual(len(self.sends),0)
        sent = next(obj for _, obj in reversed(self.remote.net.sent) if obj['t']=='pmsg')
        msg = json.loads(json.dumps(sent))
        proof, scope = msg['task_proof'], msg['task_proof']['task_scope']
        # The outgoing schema and exact signed metadata survive the actual peer app parser.
        self.clock.t=time.time()
        self.svc.on_app(self.b, msg)
        await settle_pending(self.svc)
        self.assertEqual(len(self.sends),1)
        send = self.sends[-1]
        self.assertEqual(send.action_proof,proof)
        h.agent.cur_send = send
        h.cur_turn = send.turn
        from test_l1 import _ready
        receiver_phone=Phone(h.st,with_passkey=True);receiver_phone.policy_host=h
        receiver_session=_ready(h,receiver_phone)
        for command in commands:
            self.assertIsNone(self.svc.same_owner.authorized(send,'Bash',{'command':command},json.loads(scope)['actions'][0]['class']))
            await self.native_tap(h,receiver_phone,receiver_session,command)
        await self.native_tap(h,receiver_phone,receiver_session,'stripe charges create --amount 501')
        rows=approvals.read_log(h.st)
        self.assertEqual(approvals.check_record(h.st,rows[-1]),'ok')
        self.assertNotIn('task_grant',[r['reason'] for r in rows])
        # The historical task_id field is metadata for this native tap, never a reusable grant.
        self.assertEqual(approvals.check_record(h.st,{**rows[-1],'task_id':'different-turn'}),'ok')
        # A new message-id may not spend an already claimed signature again.
        self.svc.on_app(self.b, {**msg,'mid':'fa'*8})
        await settle_pending(self.svc)
        self.assertEqual(len(self.sends),1)
        for change in ({'text':text+' changed'}, {'task_proof':{**proof,'sk':wire.b64u(self.dsk.public_key().public_bytes_raw())}},
                       {'task_proof':{**proof,'task_scope':scope.replace(json.loads(scope)['task'],'different-origin-task')}}):
            self.assertIsNone(self.svc.same_owner.task_proof(self.b,{**msg,**change}))
        # Wrong recipient, relationship, expiration and a foreign peer cannot claim the same proof.
        for field, value in [('receiver','different-device'),('relationship','00'*16)]:
            bad=copy.deepcopy(proof);sc=json.loads(scope);sc['target'][field]=value
            bad['task_scope']=json.dumps(sc,sort_keys=True,separators=(',',':'))
            self.assertIsNone(self.svc.same_owner.task_proof(self.b,{**msg,'task_proof':bad}))
        # A full nonce table retains every live nonce: fresh proofs are refused instead of evicting old ones.
        fresh = copy.deepcopy(proof)
        fresh['id'] = 'ef'*16
        fresh['envelope']['request_id']=fresh['id']
        fresh['shown_sha256']=approval_review.digest(fresh['envelope'])
        fresh['sig']=wire.b64u(self.osk.sign(approval_review.decision_message(fresh['channel'],fresh['device'],fresh['id'],
                                         'allow',fresh['shown_sha256'],task_scope=scope)))
        self.assertIsNotNone(self.svc.same_owner.task_proof(self.b,{**msg,'task_proof':fresh}))
        with self.svc.store._edit('same-owner.json', {}) as rows:
            row=rows[self.b]
            for i in range(1023):row['action_nonces'][f'capacity-{i}']=int(self.clock())+1700
        self.svc.on_app(self.b, {**msg,'mid':'ca'*8,'task_proof':fresh})
        await settle_pending(self.svc)
        self.assertEqual(len(self.sends),1)
        self.assertIn(proof['id'],self.svc.same_owner.row(self.b)['action_nonces'])
        self.clock.t += 1801
        self.assertIsNone(self.svc.same_owner.task_proof(self.b,msg))
        self.assertIsNone(self.svc.same_owner.task_proof('11'*16,msg))
        before_cards=len(h.asks)
        await self.native_tap(h,receiver_phone,receiver_session,commands[0])
        self.assertEqual(len(h.asks),before_cards)
        h.cur_turn = send.turn + 1
        self.assertIsNone(self.svc.same_owner.authorized(send,'Bash',{'command':commands[0]},json.loads(scope)['actions'][0]['class']))
        # Revoking the receiver signer invalidates an otherwise current native card.
        task=asyncio.create_task(h.ask('Bash',{'command':commands[0]},_harness_permission=True))
        card=await self.wait_card(h,task)
        answer=receiver_phone.answer(h._ask_msg(card,receiver_session),True)
        self.assertTrue(h.st.remove_device(receiver_phone.did))
        await h._answer(receiver_session,answer)
        self.assertFalse(card.fut.done())
        task.cancel();await asyncio.gather(task,return_exceptions=True)


    async def test_p573_explicit_invalid_plan_never_becomes_plain_task(self):
        self.clock.t=time.time()
        await self.confirm()
        text='perform the explicitly approved deletion'
        target={'sender':self.b,'receiver':self.a,'relationship':self.svc.same_owner.challenge(self.b),
                'text_sha256':__import__('hashlib').sha256(text.encode()).hexdigest()}
        scope=json.dumps({'task':'explicit-task','uv':True,'actions':[{'tool':'Bash','input_sha256':'ab'*32,
                          'class':['admin',['delete'],['delete.rm']]}], 'target':target,'expires':int(self.clock())+1700},
                         sort_keys=True,separators=(',',':'))
        proof={'channel':self.other.channel,'device':self.odid,'id':'de'*16,'task_scope':scope,
               'shown_sha256':'ab'*32,'sk':wire.b64u(self.osk.public_key().public_bytes_raw())}
        proof['sig']=wire.b64u(self.osk.sign(approvals.signed_message(proof['channel'],self.odid,proof['id'],
                                            'allow',proof['shown_sha256'],task_scope=scope)))
        from test_p71_peer_service import device
        extra_did,extra_key=device(self.other.st)
        wrong_root={**proof,'device':extra_did,'sk':wire.b64u(extra_key.public_key().public_bytes_raw())}
        wrong_root['sig']=wire.b64u(extra_key.sign(approvals.signed_message(proof['channel'],extra_did,proof['id'],
                                                  'allow',proof['shown_sha256'],task_scope=scope)))
        variants=[{'task_plan':True}, {'task_plan':True,'task_proof':None},
                  {'task_plan':True,'task_proof':wrong_root},
                  {'task_plan':True,'task_proof':{**proof,'sig':'AA'}},
                  {'task_plan':True,'task_proof':{**proof,'task_scope':' '*12001}},
                  {'task_plan':True,'task_proof':{**proof,'task_scope':scope.replace(str(int(self.clock())+1700),'1')}},
                  {'task_proof':proof}]
        for tier in (None, "uv"):
            legacy=copy.deepcopy(proof); legacy_scope=json.loads(scope)
            if tier is None: legacy_scope.pop("uv")
            else: legacy_scope["uv"]=tier
            legacy["task_scope"]=json.dumps(legacy_scope,sort_keys=True,separators=(',',':'))
            legacy["sig"]=wire.b64u(self.osk.sign(approvals.signed_message(legacy["channel"],legacy["device"],legacy["id"],
                                   'allow',legacy["shown_sha256"],task_scope=legacy["task_scope"])))
            variants.append({"task_plan":True,"task_proof":legacy})
        for extra in variants:
            msg=pmsg(text,**extra)
            self.svc.on_app(self.b,msg)
            await settle_pending(self.svc)
            self.assertEqual(len(self.sends),0,extra)
            self.assertFalse(self.host.of('ask'))
            self.assertEqual(self.svc.guards.row(self.b,msg['mid'])['state'],'blocked')
            receipt=next(obj for _,obj in reversed(self.svc.net.sent) if obj['t']=='task_refused' and obj['mid']==msg['mid'])
            self.assertEqual(receipt['why'],'root_reconfirm')
            self.remote.guards.remember(self.a,msg,'out')
            self.remote.on_app(self.a,receipt)
            self.assertIn('重新确认',self.other.notices[-1])
        # Revalidation at the forwarding boundary also refuses invalid explicit plans.
        self.assertFalse(await self.svc.same_owner.forward(self.b,pmsg(text,task_plan=True,task_proof=wrong_root)))
        self.assertEqual(len(self.sends),0)
        # Old plain tasks retain their ordinary main-agent delivery path.
        self.svc.on_app(self.b,pmsg('ordinary legacy task'))
        await settle_pending(self.svc)
        self.assertEqual(len(self.sends),1)

    async def test_receiver_uv_floor_must_not_reuse_button_plan(self):
        from test_l1 import _ready
        self.clock.t=time.time()
        await self.confirm()
        receiver=_owner_host(self.host.st,[])
        receiver.peers=self.svc;self.svc.host=receiver
        receiver.agent.submit=self.sends.append
        sender=_owner_host(self.other.st,[])
        sender.ask_ttl=2;receiver.ask_ttl=2
        sender.cur_turn=7;sender.peers=self.remote;self.remote.host=sender
        phone=self.root_phone(sender);session=_ready(sender,phone)
        inp={'command':'rm -rf '+__import__('shlex').quote(str(__import__('pathlib').Path(__import__('tempfile').gettempdir())/'p573-controlled-uv-boundary'))}
        job=asyncio.create_task(self.remote.cli({'t':'tell','friend':self.a,'text':'Run the exact cleanup plan','actions':[{'tool':'Bash','input':inp}]}))
        card=await self.wait_card(sender,job)
        self.assertFalse(card.require_passkey)
        self.assertIs(json.loads(card.task_scope)['uv'],False)
        await sender._answer(session,Phone.answer(phone,sender._ask_msg(card,session),True))
        self.assertTrue((await job)['ok'])
        msg=next(obj for _,obj in reversed(self.remote.net.sent) if obj['t']=='pmsg')
        self.clock.t=time.time()
        self.svc.on_app(self.b,json.loads(json.dumps(msg)))
        await settle_pending(self.svc)
        self.assertEqual(len(self.sends),1)
        send=self.sends[-1];receiver.agent.cur_send=send;receiver.cur_turn=send.turn
        self.assertIsNone(self.svc.same_owner.authorized(send,'Bash',inp,json.loads(card.task_scope)['actions'][0]['class']))
        # A real explicit semantic controller name owns UV; no string classifier override.
        ph=Phone(receiver.st,with_passkey=True);ph.policy_host=receiver;sess=_ready(receiver,ph)
        for attempt in range(2):
            task=asyncio.create_task(receiver.ask('agentj secret send',{'name':'SYNTHETIC_NOT_READ'}))
            ask=await self.wait_card(receiver,task)
            self.assertTrue(ask.require_passkey)
            answer=ph.answer(receiver._ask_msg(ask,sess),True)
            answer.pop('fa',None)
            await receiver._answer(sess,answer)
            self.assertFalse(ask.fut.done())
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertFalse(receiver.asks)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([PeerPlan('test_p573_signed_plan_roundtrip_and_negative_boundaries'),
                              PeerPlan('test_p573_explicit_invalid_plan_never_becomes_plain_task'),
                              PeerPlan('test_receiver_uv_floor_must_not_reuse_button_plan')])
