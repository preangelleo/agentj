"""Native-call holder, real bwrap/UDS, actual Host privacy and signed fixture answers."""
import _hermetic  # noqa: F401
import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import unittest
from unittest.mock import patch
from agentj import approval_material, approval_review, privacy, support
from agentj.compose import Send
import test_stella_codex_children as stella_fixtures


WORKER = r'''
import subprocess,sys,json
oldcode='import sys,json,subprocess; print("ready",flush=True)\nfor line in sys.stdin:\n r=json.loads(line); p=subprocess.run(r["argv"],env=r["env"],capture_output=True,text=True); print(json.dumps({"exit":p.returncode,"out":p.stdout,"stderr":bool(p.stderr)}),flush=True)'
old=subprocess.Popen([sys.executable,'-c',oldcode],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
old.stdout.readline()
print('ready',flush=True)
while line:=sys.stdin.readline():
 r=json.loads(line)
 if r.get('op')=='stop':break
 if r.get('op')=='old':
  old.stdin.write(json.dumps(r)+'\n');old.stdin.flush();print(old.stdout.readline().strip(),flush=True)
 else:
  p=subprocess.run(r['argv'],env=r['env'],capture_output=True,text=True)
  print(json.dumps({'exit':p.returncode,'out':p.stdout,'stderr':bool(p.stderr)}),flush=True)
old.terminate();old.wait()
'''


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('bwrap'), 'requires Linux bwrap')
class NativeFeedback(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = stella_fixtures.RealHost('test_child_danger_real_signed_card_after_parent_done')
        await self.f.asyncSetUp()
        self.addCleanup(self.f.doCleanups)
        self.h, self.a = self.f.h, self.f.agent
        self.root = Path(self.h.agent_cfg['dir'])
        from agentj import peer_service
        self.h.peers = peer_service.PeerService(self.h)
        self.broker = self.h.peers.xfer.broker
        self.worker = await asyncio.create_subprocess_exec(shutil.which('bwrap'), '--dev-bind','/','/',
            '--unshare-pid','--as-pid-1','--proc','/proc',sys.executable,'-c',WORKER,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        self.assertEqual(await self.worker.stdout.readline(),b'ready\n')
        self.a.proc = self.worker
        self.a.cfg['fence'] = True
        self.a.cfg['dir'] = str(self.root)
        self.h.agent_cfg = self.a.cfg
        self.h.busy.set_context('feedback-native',harness='codex',cwd=str(self.root),pid=self.worker.pid)
        self.proof_rows=[]
        original_scope=approval_material._direct_scope
        def observe_scope(host,peer):
            result=original_scope(host,peer)
            if host is self.h:self.proof_rows.append({'kind':result.get('kind') if result else None})
            return result
        observer=patch.object(approval_material,'_direct_scope',side_effect=observe_scope)
        observer.start();self.addCleanup(observer.stop)
        receipt=getattr(self.broker,'feedback_receipt',None)
        if receipt is not None:
            def observed_receipt(req,peer):
                observe_scope(self.h,peer)
                return receipt(req,peer)
            receipt_observer=patch.object(self.broker,'feedback_receipt',side_effect=observed_receipt)
            receipt_observer.start();self.addCleanup(receipt_observer.stop)
        # A later real held owner task: the native PID1 now predates this Send.
        await asyncio.sleep(.03)
        page = self.h.hist_add({'k':'phone','dev':self.f.ph.did,'name':'Synthetic owner','text':'Submit a synthetic feedback probe'},'','done')
        self.send = Send(self.f.ph.did,'feedback-probe',page['id'],text='Synthetic feedback task',source={'kind':'phone','role':'owner'})
        self.send.approval_origin = approval_review.remember(self.h,page,self.send.source)
        self.a.cur_send = self.send
        self.h.cur_turn = page['id']
        self.h.peers.xfer.begin_execution(self.send)
        self.h.ask_ttl = 10
        self.calls = []
        outer = self
        class Channel:
            signed = True
            st = outer.h.st
            def send(self,fields):
                outer.calls.append(fields)
                return 201,{'thread':'st_'+'a'*22,'seq':1}
        self.channel_patch = patch.object(support,'channel_for',return_value=Channel())
        self.channel_patch.start();self.addCleanup(self.channel_patch.stop)
        self.review_patch = patch.object(privacy,'layer2',return_value=privacy.Verdict('ok'))
        self.backend = self.review_patch.start();self.addCleanup(self.review_patch.stop)
        await self.h.elevate.start()

    async def asyncTearDown(self):
        await self.h.elevate.stop()
        if self.worker.returncode is None:
            self.worker.stdin.write(b'{"op":"stop"}\n');await self.worker.stdin.drain()
            await asyncio.wait_for(self.worker.wait(),3)
        await self.f.asyncTearDown()

    async def admit(self, command="agentj support feedback 'Synthetic feedback' --wait 0", call='feedback-1', turn='turn1'):
        self.a._on_request({'id':101,'method':'item/commandExecution/requestApproval','params':{
            'threadId':self.a.tid,'turnId':turn,'itemId':call,'command':command,'cwd':str(self.root)}})
        async def answer_while_native_task_is_live():
            while self.a.tasks:
                for ask in list(self.h.asks.values()):
                    if not ask.fut.done():
                        await self.h._answer(self.f.s,self.f.ph.answer(self.h._ask_msg(ask,self.f.s),True))
                await asyncio.sleep(.005)
        # Source preparation can finish after one second; keep the signed fixture
        # responder alive until native completion. The fixture card budget also
        # covers real durable reserve on disk; production ASK_TTL is unchanged.
        await asyncio.wait_for(answer_while_native_task_is_live(), timeout=15)
        self.assertEqual(self.a._answer.call_args.args[1],{'decision':'accept'})

    async def cli(self, text='Synthetic feedback', old=False):
        env = {'PATH':os.environ['PATH'],'HOME':str(self.root),'PYTHONPATH':str(Path(__file__).resolve().parents[1]),
               'AGENTJ_STATE_DIR':str(self.h.st.root),'AGENTJ_ELEVATE_SOCK':str(self.h.elevate.sock_path)}
        argv=[sys.executable,'-m','agentj.cli','support','feedback',text,'--wait','0']
        self.worker.stdin.write((json.dumps({'argv':argv,'env':env,'op':'old' if old else 'new'})+'\n').encode());await self.worker.stdin.drain()
        return json.loads(await asyncio.wait_for(self.worker.stdout.readline(),5))

    async def complete(self, output, call='feedback-1', turn='turn1', child=False):
        self.a._on_note('item/completed',{'threadId':'other-child' if child else self.a.tid,'turnId':turn,'item':{
            'type':'commandExecution','id':call,'status':'completed','exitCode':0,'aggregatedOutput':output}})
        await self.broker.drain()

    async def test_exact_native_result_is_required_before_http(self):
        await self.admit()
        result=await self.cli()
        print(json.dumps({'phase':'native-cli-result','exit':result['exit'],
            'source_failure': 'authenticated privacy broker/source' in result['out'],
            'pending':result['out'].startswith('SUPPORT_TASK_PENDING:'),
            'http_stub_calls':len(self.calls),'privacy_backend_calls':self.backend.call_count,
            'kernel_scope_kinds':sorted({str(x['kind']) for x in self.proof_rows})}))
        self.assertEqual(result['exit'],0)
        self.assertTrue(result['out'].startswith('SUPPORT_TASK_PENDING:'))
        self.assertEqual(self.calls,[])
        self.assertEqual(self.backend.call_count,0)
        for kwargs in ({'call':'wrong-call'},{'turn':'old-turn'},{'child':True}):
            await self.complete(result['out'],**kwargs)
            self.assertEqual(self.calls,[])
        await self.complete(result['out'])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0]['body'],'Synthetic feedback')
        await self.complete(result['out'])
        self.assertEqual(len(self.calls),1)

    async def test_modified_payload_never_gets_ticket_or_sends(self):
        await self.admit()
        result=await self.cli('Changed payload')
        self.assertEqual(result['exit'],3)
        await self.complete(result['out'])
        self.assertEqual(self.calls,[])
        self.backend.assert_not_called()

    async def test_task_switch_after_pending_never_sends(self):
        await self.admit();result=await self.cli()
        self.a.cur_send=Send(self.f.ph.did,'other-task',999,source={'kind':'phone','role':'owner'})
        await self.complete(result['out'])
        self.assertEqual(self.calls,[])
        self.backend.assert_not_called()

    async def test_late_old_task_fresh_peer_cannot_complete_new_call_ticket(self):
        await self.admit();await self.cli()
        page=self.h.hist_add({'k':'phone','dev':self.f.ph.did,'name':'Synthetic owner','text':'New exact feedback task'},'','done')
        self.send=Send(self.f.ph.did,'new-task',page['id'],source={'kind':'phone','role':'owner'})
        self.send.approval_origin=approval_review.remember(self.h,page,self.send.source)
        self.a.cur_send=self.send;self.h.cur_turn=page['id'];self.a.turn_id='turn2'
        self.h.peers.xfer.begin_execution(self.send)
        await self.admit(call='feedback-2',turn='turn2')
        pending=await self.cli()
        self.assertEqual(pending['exit'],0)
        await self.complete(pending['out'],call='feedback-1',turn='turn1')
        await self.complete(pending['out'],call='feedback-2',turn='turn1')
        self.broker.complete('feedback-2',True,pending['out'],native=(self.a.tid,'turn2'),child=True)
        await self.broker.drain()
        self.assertEqual(self.calls,[])
        self.backend.assert_not_called()
        await self.complete(pending['out'],call='feedback-2',turn='turn2')
        self.assertEqual(len(self.calls),1)

    async def test_old_background_branch_cannot_take_the_new_call_ticket(self):
        await self.admit()
        old=await self.cli(old=True)
        self.assertEqual(old['exit'],3)
        await self.complete(old['out'])
        self.assertEqual(self.calls,[])

    async def test_expired_pending_and_changed_identity_never_send(self):
        import time,contextlib
        for reason in ('timeout','identity','epoch','cancel','stop'):
            # Each iteration binds a fresh native call, never reuses its old ticket.
            call='feedback-'+reason
            text='Synthetic '+reason
            await self.admit(command='agentj support feedback '+shlex.quote(text)+' --wait 0',call=call)
            result=await self.cli(text)
            binding=self.broker.calls[call]['feedback']
            epoch,state,estop=self.h.hist.epoch,self.send.state,self.h.estop
            if reason=='timeout':binding.expires=time.monotonic()-1
            if reason=='epoch':self.h.hist.epoch='changed'
            if reason=='cancel':self.send.state='cancelled'
            if reason=='stop':self.h.estop={'on':True}
            with patch.object(self.h.st,'sign_key',return_value=b'changed-key') if reason=='identity' else contextlib.nullcontext():
                await self.complete(result['out'],call=call)
            self.h.hist.epoch,self.send.state,self.h.estop=epoch,state,estop
            self.assertEqual(self.calls,[])

    async def test_source_change_during_model_review_stops_transport(self):
        import threading
        entered,release=threading.Event(),threading.Event()
        def slow(*args,**kwargs):
            entered.set();release.wait(2)
            return privacy.Verdict('ok')
        self.backend.side_effect=slow
        await self.admit();result=await self.cli()
        task=asyncio.create_task(self.complete(result['out']))
        for _ in range(300):
            if entered.is_set():break
            await asyncio.sleep(.002)
        self.assertTrue(entered.is_set())
        self.a.cur_send=Send(self.f.ph.did,'changed-task',999,source={'kind':'phone','role':'owner'})
        release.set();await task
        self.assertEqual(self.calls,[])

    async def test_file_payload_is_sealed_and_unrecognized_replays_cannot_resend(self):
        draft=self.root/'feedback.txt';draft.write_text('Synthetic file feedback')
        cmd='agentj support feedback --file '+shlex.quote(str(draft))+' --wait 0'
        await self.admit(command=cmd)
        result=await self.cli('Synthetic file feedback')
        await self.complete(result['out'])
        self.assertEqual(len(self.calls),1)
        # Same native item cannot refresh a consumed operation with a new ticket.
        self.broker.register('feedback-1','Bash',{'command':cmd},str(self.root),native=(self.a.tid,'turn1'))
        denied=await self.cli('Synthetic file feedback')
        self.assertEqual(denied['exit'],3)
        self.assertEqual(len(self.calls),1)

    async def test_model_credential_denial_is_not_skipped(self):
        self.backend.return_value=privacy.Verdict('blocked',reason='synthetic credential finding',kind='credential')
        await self.admit();result=await self.cli();await self.complete(result['out'])
        self.assertEqual(self.calls,[])

    async def test_optional_backend_outage_keeps_local_redaction_and_truthful_note(self):
        self.backend.return_value=privacy.Verdict('unavailable',reason='privacy-check/backend_off')
        await self.admit();result=await self.cli();await self.complete(result['out'])
        self.assertEqual(len(self.calls),1)
        self.assertIn(privacy.LOCAL_REVIEW_NOTE,self.calls[0]['body'])

    async def test_kernel_root_replacement_and_namespace_mismatch_stop_pending(self):
        await self.admit();result=await self.cli()
        binding=self.broker.calls['feedback-1']['feedback']
        # Real OS native root was captured earlier; a different held kernel identity
        # cannot become it while the same native call remains pending.
        original=binding.native_identity
        binding.native_identity=(original[0],original[1],'different-pid-namespace')
        await self.complete(result['out'])
        self.assertEqual(self.calls,[])
        self.backend.assert_not_called()

    async def test_private_binding_and_client_source_fields_cannot_select_authority(self):
        from agentj import host_diagnostics
        from agentj.feedback_native import canonical
        req={'t':'host_diagnostics','op':'support','kind':'report','text':'Synthetic feedback','wait':0}
        self.assertIsNone(canonical({**req,'origin':{'verified':True}}))
        self.assertIsNone(canonical({**req,'_support_binding':True}))
        denied=host_diagnostics.execute(self.h.st,req,_support_binding=True)
        self.assertEqual(denied['exit'],3)
        self.assertEqual(self.calls,[])


class Grammar(unittest.TestCase):
    def test_only_closed_literal_wait_zero_profile(self):
        from agentj.feedback_native import command as parse
        for c in ("agentj support feedback 'safe' --wait 0",'agentj support report --file draft.txt --wait 0'):
            self.assertIsNotNone(parse('Bash',{'command':c},'/tmp'))
        for c in ('agentj support feedback safe','agentj support feedback safe --wait 10',
                  'agentj support feedback $BODY --wait 0','agentj support feedback safe --wait 0; echo later'):
            self.assertIsNone(parse('Bash',{'command':c},'/tmp'))
