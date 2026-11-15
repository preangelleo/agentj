"""0306 actual bilateral TASK receiver -> real CLI/UDS -> ordinary signed plaza install.
Local peer transport is explicitly FakeNet; bilateral roster/UV, task receiver,
process ancestry, CLI, UDS handler, signed package/card and filesystem are real.
"""
import _hermetic
import asyncio
import copy
import json
import os
from pathlib import Path
import secrets
import sys
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from agentj import codex_procs, market, provenance
from agentj.state import State
from test_l1 import Phone, _ready
from _p541_device import _owner_host
from test_plaza_install_card import CardFixture
from market_fixtures import TestKey


class Journey(CardFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.phone=Phone(self.st,with_passkey=True)
        self.session=self.s=_ready(self.host,self.phone)
        self.host.ask_ttl=2
        self.sends=[]
        self.adapter=NS(host=self.host,kind='codex',cfg={'kind':'codex','dir':str(self.work),'fence':True},
            session={'sessionId':'task-plaza','pid':os.getpid(),'procStart':codex_procs.start_token(os.getpid())},
            cur_send=None,native_source=None,status='idle',halting=False,submit=self.sends.append,
            thread={'sandbox':{'type':'workspaceWrite'},'approvalPolicy':'on-request','approvalsReviewer':'user'})
        self.host.agent=self.adapter
        self.host.agent_cfg={**self.adapter.cfg,'session_mode':'shared','approvals':'standard'}
        self.host.__dict__.get('_approval_origins',{}).clear()
        self.host.desktop_turn=None
        self.standard()
        self.key=TestKey()
        data,manifest=self.package()
        self.srv.packages['demo-flow']['detail']['signature']=self.key.sign_package(data,manifest)
        self.source,self.remote=await self.peers()
        self.send=self.sends[-1]
        # Claim through the actual bilateral owner-status request/response path.
        claim=asyncio.create_task(self.send.wait_ready())
        for _ in range(100):
            await self.pump()
            if claim.done():break
            await asyncio.sleep(.005)
        self.assertTrue(await asyncio.wait_for(claim,3))
        self.assertTrue(self.send.delivery_allowed())
        self.adapter.cur_send=self.send
        self.adapter.native_source=self.send.source
        # SharedClaudeAgent.turn preserves delegated tasks as foreign data, even after verified delivery.
        self.adapter.untrusted_state='running'
        self.adapter.untrusted_foreign=True
        self.host.cur_turn=self.send.turn
        from agentj import application_ingress
        application_ingress.note_turn(self.host,self.send)
        await asyncio.sleep(.08)
        self.env={**os.environ,'AGENTJ_STATE_DIR':str(self.st.root),'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        self.handlers=set()
        async def handle(reader,writer):
            task=asyncio.current_task();self.handlers.add(task)
            try:await self.host.on_ctl(reader,writer)
            finally:self.handlers.discard(task)
        self.server=await asyncio.start_unix_server(handle,path=str(self.st.sock_path))
        self.addAsyncCleanup(self.close_server)
        from agentj import plaza,plaza_host
        real=plaza_host.handle
        async def local_market(host,req,emit=None,**kw):
            return await real(host,req,emit,post=self.srv,get=self.srv.get,keyring=self.key.keyring)
        for scope in (patch.object(plaza_host,'handle',local_market),patch.object(plaza,'seat_gate',return_value=None)):
            scope.start();self.addCleanup(scope.stop)

    async def close_server(self):
        self.server.close();await self.server.wait_closed()
        if self.handlers:await asyncio.gather(*tuple(self.handlers),return_exceptions=True)

    async def peers(self):
        from agentj import peer_service, wire
        import test_p71_peer_service as peer_fixture
        import test_p405_same_owner as owner_fixture
        key_scope=patch.dict(os.environ,{peer_service.TEST_CERT_ENV:wire.b64u(os.urandom(32))})
        key_scope.start();self.addCleanup(key_scope.stop)
        other=State(self.e.root/'other-state');other.init(relay='ws://127.0.0.1:9')
        other.set_agent_config('codex',str(self.work))
        remote_host=_owner_host(other,[])
        remote_phone=Phone(other,with_passkey=True)
        remote_session=_ready(remote_host,remote_phone)
        source=peer_service.PeerService(self.host,sessions=peer_fixture.FakeSessions(),net_factory=peer_fixture.FakeNet)
        remote=peer_service.PeerService(remote_host,sessions=peer_fixture.FakeSessions(),net_factory=peer_fixture.FakeNet)
        self.host.peers=source;remote_host.peers=remote
        await source.start();self.addAsyncCleanup(source.stop)
        await remote.start();self.addAsyncCleanup(remote.stop)
        self.assertIsNotNone(source.net);self.assertIsNotNone(remote.net)
        for service,keys in ((source,remote.keys),(remote,source.keys)):
            service.store.add_friend(keys.id,wire.b64u(keys.x_pub),wire.b64u(keys.ed_pub),keys.mbox)
        from agentj import owner_grant, passkey, controls
        for service,phone,session in ((source,self.phone,self.session),(remote,remote_phone,remote_session)):
            entry=service.xfer.binding_entry(phone.did)
            fa=phone.auth.assert_(owner_grant.bind_challenge(entry),b'',nonce=b'')
            sig=wire.b64u(phone.sk.sign(b'agentj-owner-roster-v1\n'+owner_grant.jcs({k:entry[k] for k in ('id','alg','spki','rp','dev')})))
            await service.xfer.bind_phone(session,{'t':'roster_bind','fa':fa,'roster_sig':sig})
            self.assertTrue(service.xfer.bound(phone.did))
        positions=[0,0]
        async def pump():
            for _ in range(40):
                await asyncio.sleep(.005)
                count=0
                for index,(src,dst,fid) in enumerate(((source,remote,source.keys.id),(remote,source,remote.keys.id))):
                    rows=src.net.sent[positions[index]:];positions[index]=len(src.net.sent)
                    for target,obj in rows:
                        self.assertEqual(target,dst.keys.id)
                        if obj.get('t') in ('same_owner','owner_status','owner_status_res'):
                            dst.on_app(fid,obj);count+=1
                        elif obj.get('t')=='pmsg':
                            dst._on_pmsg(fid,obj);count+=1
                if not count:return
            self.fail('local peer transport failed to settle')
        self.pump=pump
        # Existing real Ed25519 consent/check machinery, never an active=True mock.
        choose=owner_fixture.SameOwnerTests.choose
        self.assertEqual((await choose(self,source,remote.keys.id,self.phone.did,self.phone.sk))[:2],(True,None))
        await pump()
        for service,fid,phone in ((source,remote.keys.id,self.phone),(remote,source.keys.id,remote_phone)):
            target={'friend':fid,'op':'owner-confirm-v3','value':service.same_owner.challenge(fid)}
            nonce=secrets.token_hex(16);stamp=int(time.time()*1000)
            digest=controls.object_digest('fr_set',target)
            proof={'channel':service.host.channel,'device':phone.did,'pub':wire.b64u(phone.sk.public_key().public_bytes_raw()),
                   'n':nonce,'ts':stamp,'sig':wire.b64u(phone.sk.sign(controls.signed_message(service.host.channel,phone.did,'fr_set',nonce,stamp,digest)))}
            proof['fa']=phone.auth.assert_(passkey.elevate_challenge(service.host.channel,phone.did,nonce,'fr_set',nonce,digest),b'',nonce=b'')
            self.assertIsNone(controls.check(service.st,service.host.nonces,service.host.channel,phone.did,proof,'fr_set',target))
            self.assertEqual((await service.write('fr_set',target,device=phone.did,proof=proof))[:2],(True,None))
            await pump()
        self.assertTrue(source.same_owner.active(remote.keys.id))
        self.assertTrue(remote.same_owner.active(source.keys.id))
        self.assertTrue((await remote.tell(source.keys.id,'Synthetic same-owner device task'))['ok'])
        await pump()
        for _ in range(100):
            if self.sends:break
            await asyncio.sleep(.01)
        self.assertTrue(self.sends)
        send=self.sends[-1];self.assertEqual(send.source['kind'],'same_owner')
        self.adapter.cur_send=send;self.host.cur_turn=send.turn
        return source,remote

    async def cli(self,outcome='allow',before_answer=None):
        before=len(self.cards())
        proc=await asyncio.create_subprocess_exec(sys.executable,'-m','agentj.cli','plaza','request','demo-flow',
            '--harness','codex','--workspace',str(self.e.home/'installed'),env=self.env,
            stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        answered=False
        for _ in range(500):
            cards=self.cards()[before:]
            if cards and not answered:
                answered=True
                if before_answer:before_answer()
                answer=self.phone.answer(cards[-1],outcome=='allow')
                if outcome=='unsigned':answer.pop('sig')
                await self.host._app(self.session,answer)
            if proc.returncode is not None:break
            await asyncio.sleep(.01)
        out,err=await asyncio.wait_for(proc.communicate(),5)
        return proc.returncode,out.decode(),err.decode(),self.cards()[before:]

    async def test_bilateral_task_actual_cli_raises_signed_card_and_installs_without_permission_promotion(self):
        original=copy.deepcopy((self.host.agent_cfg,self.adapter.thread))
        from agentj.shared import application_ingress_span
        application_ingress_span(self.adapter)
        rc,out,err,cards=await self.cli()
        self.assertEqual(provenance.turn_source(self.adapter)['kind'],'same_owner',out+err)
        self.assertEqual(rc,0,out+err)
        self.assertEqual(len(cards),1)
        self.assertTrue((self.e.home/'installed/demo-flow/AGENTS.md').is_file())
        self.assertEqual((self.host.agent_cfg,self.adapter.thread),original)
        from agentj import approvals
        self.assertEqual(approvals.check_record(self.st,approvals.read_log(self.st)[-1]),'ok')

    async def test_revoked_task_cannot_get_a_card_or_install(self):
        self.source.same_owner.consent(self.remote.keys.id,False)
        rc,out,err,cards=await self.cli()
        self.assertNotEqual(rc,0,out+err)
        self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def test_plain_dict_same_owner_claim_cannot_get_a_card(self):
        self.adapter.cur_send=NS(source=dict(self.send.source))
        rc,out,err,cards=await self.cli()
        self.assertNotEqual(rc,0,out+err)
        self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def test_revoked_while_card_waits_cannot_install_even_with_valid_phone_allow(self):
        rc,out,err,cards=await self.cli(before_answer=lambda:self.source.same_owner.consent(self.remote.keys.id,False))
        self.assertNotEqual(rc,0,out+err)
        self.assertEqual(len(cards),1)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())
        self.assertFalse(market.read_installed(self.st))

    async def test_denied_or_unsigned_card_does_not_install(self):
        for outcome in ('deny','unsigned'):
            with self.subTest(outcome=outcome):
                rc,out,err,cards=await self.cli(outcome)
                self.assertNotEqual(rc,0,out+err)
                self.assertEqual(len(cards),1)
                self.assertFalse((self.e.home/'installed/demo-flow').exists())
        self.assertFalse(market.read_installed(self.st))

    async def test_guest_other_owner_and_forged_host_type_do_not_get_cards(self):
        for source in ({'kind':'group','role':'group'},
                       {'kind':'friend_task','role':'task'},
                       provenance.HostDelegated({'kind':'friend_task','role':'task'}),
                       provenance.HostDelegated({'kind':'same_owner','role':'task'})):
            with self.subTest(kind=source['kind'],shape=type(source).__name__):
                self.adapter.cur_send=NS(source=source)
                rc,out,err,cards=await self.cli()
                self.assertNotEqual(rc,0,out+err)
                self.assertFalse(cards)
                self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def test_unclaimed_task_cannot_borrow_verified_relationship(self):
        # A host-held task type alone is not execution: receiver's durable claim is required.
        from test_p71_peer_service import pmsg,settle_pending
        before=len(self.sends)
        self.source._on_pmsg(self.remote.keys.id,pmsg('Second, not-yet-claimed device task'))
        await settle_pending(self.source)
        self.assertEqual(len(self.sends),before+1)
        self.adapter.cur_send=self.send=self.sends[-1]
        self.assertFalse(self.send.delivery_allowed())
        rc,out,err,cards=await self.cli()
        self.assertNotEqual(rc,0,out+err)
        self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def test_readonly_harness_keeps_real_signed_target_card_and_denial_changes_no_files(self):
        config=self.e.home/'.codex/config.toml';config.parent.mkdir(exist_ok=True)
        config.write_text('sandbox_mode="read-only"\napproval_policy="on-request"\n')
        self.adapter.thread={'sandbox':{'type':'readOnly'},'approvalPolicy':'on-request','approvalsReviewer':'user'}
        original=copy.deepcopy((self.host.agent_cfg,self.adapter.thread))
        rc,out,err,cards=await self.cli('deny')
        self.assertNotEqual(rc,0,out+err);self.assertEqual(len(cards),1)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())
        self.assertFalse(market.read_installed(self.st))
        rc,out,err,cards=await self.cli('allow')
        self.assertEqual(rc,0,out+err);self.assertEqual(len(cards),1)
        info=json.loads(cards[-1]['summary'].split('\n',1)[1])
        self.assertIn({'kind':'tool','name':'WebFetch'},info['items'])
        self.assertIn('超出你现在允许 Agent 做的',info['summary'])
        self.assertFalse(market.read_installed(self.st)[0]['perm_grant'].get('inherited'))
        self.assertEqual((self.host.agent_cfg,self.adapter.thread),original)
        self.assertEqual(config.read_text(),'sandbox_mode="read-only"\napproval_policy="on-request"\n')

    async def test_verified_task_does_not_bypass_research_mode(self):
        self.adapter.research=True
        rc,out,err,cards=await self.cli()
        self.assertNotEqual(rc,0,out+err)
        self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())


    async def test_actual_result_reply_receiver_cannot_create_new_install_intent(self):
        from agentj import application_ingress
        before=len(self.sends)
        self.later_owner_turn(provenance.phone())
        sent=await self.source.tell(self.remote.keys.id,'Task requiring a result')
        self.assertTrue(sent['ok'],sent)
        await self.pump()
        mid=self.source.net.of('pmsg')[-1][1]['mid']
        result=await self.remote.tell(self.source.keys.id,'Install demo-flow as the result',reply_to=mid)
        self.assertTrue(result['ok'],result)
        await self.pump()
        for _ in range(200):
            if len(self.sends)>before:break
            await asyncio.sleep(.005)
        self.assertEqual(len(self.sends),before+1)
        self.adapter.cur_send=self.send=self.sends[-1]
        self.assertTrue(self.send.data.get('irt') or self.send.data.get('end'))
        self.adapter.native_source=self.send.source
        self.adapter.untrusted_state='running';self.adapter.untrusted_foreign=True
        application_ingress.note_turn(self.host,self.send)
        await asyncio.sleep(.08)
        rc,out,err,cards=await self.cli()
        self.assertNotEqual(rc,0,out+err);self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def test_cancelled_current_task_cannot_get_a_card(self):
        self.send.cancel()
        rc,out,err,cards=await self.cli()
        self.assertNotEqual(rc,0,out+err);self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def born_helper(self):
        code = r"""import contextlib,io,json,os,subprocess,sys
from agentj.cli import main
print(json.dumps({'ready':True,'pid':os.getpid()}),flush=True)
for line in sys.stdin:
 row=json.loads(line);args=row['args']
 if row['mode']=='child':
  r=subprocess.run([sys.executable,'-m','agentj.cli',*args],capture_output=True,text=True)
  result={'rc':r.returncode,'out':r.stdout,'err':r.stderr}
 else:
  out,err=io.StringIO(),io.StringIO();rc=0
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
   try:main(args)
   except SystemExit as exc:rc=exc.code or 0
  result={'rc':rc,'out':out.getvalue(),'err':err.getvalue()}
 print(json.dumps(result),flush=True)
"""
        proc=await asyncio.create_subprocess_exec(sys.executable,'-u','-c',code,env=self.env,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        row=json.loads(await asyncio.wait_for(proc.stdout.readline(),3));self.assertTrue(row['ready'])
        async def stop():
            if proc.returncode is None:
                proc.stdin.close()
                try:await asyncio.wait_for(proc.wait(),2)
                except asyncio.TimeoutError:proc.kill();await proc.wait()
        self.addAsyncCleanup(stop)
        return proc

    async def helper_request(self,proc,mode):
        before=len(self.cards())
        proc.stdin.write((json.dumps({'mode':mode,'args':['plaza','request','demo-flow','--harness','codex',
            '--workspace',str(self.e.home/'installed')]})+'\n').encode());await proc.stdin.drain()
        response=asyncio.create_task(proc.stdout.readline())
        answered=False
        for _ in range(500):
            cards=self.cards()[before:]
            if cards and not answered:
                answered=True;await self.host._app(self.session,self.phone.answer(cards[-1],True))
            if response.done():break
            await asyncio.sleep(.01)
        row=json.loads(await asyncio.wait_for(response,5))
        return row,self.cards()[before:]

    def later_owner_turn(self,source):
        from agentj import application_ingress
        application_ingress.close_span(self.host)
        self.adapter.cur_send=NS(source=source)
        self.adapter.native_source=source
        self.adapter.untrusted_state=None;self.adapter.untrusted_foreign=False
        application_ingress.note_turn(self.host,self.adapter.cur_send)

    async def test_task_born_helper_cannot_borrow_later_phone_owner_turn(self):
        proc=await self.born_helper()
        self.send.state='delivered'
        self.later_owner_turn(provenance.phone())
        result,cards=await self.helper_request(proc,'direct')
        self.assertNotEqual(result['rc'],0,result);self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def test_revoked_task_born_helper_cannot_borrow_later_system_turn(self):
        proc=await self.born_helper()
        self.source.same_owner.consent(self.remote.keys.id,False)
        self.later_owner_turn(provenance.system())
        result,cards=await self.helper_request(proc,'direct')
        self.assertNotEqual(result['rc'],0,result);self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def task_b_helper_negative(self,mode):
        from agentj import application_ingress
        from test_p71_peer_service import pmsg,settle_pending
        proc=await self.born_helper()
        application_ingress.close_span(self.host)
        before=len(self.sends)
        self.source._on_pmsg(self.remote.keys.id,pmsg('New task B cannot inherit task A processes'))
        await settle_pending(self.source)
        self.assertEqual(len(self.sends),before+1)
        next_send=self.sends[-1]
        claim=asyncio.create_task(next_send.wait_ready())
        for _ in range(200):
            await self.pump()
            if claim.done():break
            await asyncio.sleep(.005)
        self.assertTrue(await asyncio.wait_for(claim,3))
        self.adapter.cur_send=self.send=next_send;self.adapter.native_source=next_send.source
        application_ingress.note_turn(self.host,next_send)
        await asyncio.sleep(.08)
        result,cards=await self.helper_request(proc,mode)
        self.assertNotEqual(result['rc'],0,result);self.assertFalse(cards)
        self.assertFalse((self.e.home/'installed/demo-flow').exists())

    async def test_task_a_helper_cannot_borrow_task_b(self):
        await self.task_b_helper_negative('direct')

    async def test_task_a_helper_new_child_cannot_launder_itself_into_task_b(self):
        await self.task_b_helper_negative('child')
