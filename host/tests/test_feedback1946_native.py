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
        self.a.kind = "codex"  # RealHost copies attributes from a Claude fixture.
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
        def observe_scope(host,peer,**kw):
            result=original_scope(host,peer,**kw)
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

    async def admit(self, command="agentj support feedback 'Synthetic feedback' --wait 0", call='feedback-1', turn='turn1', reason=None):
        self.a._on_request({'id':101,'method':'item/commandExecution/requestApproval','params':{
            'threadId':self.a.tid,'turnId':turn,'itemId':call,'command':command,'cwd':str(self.root),'reason':reason}})
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

    async def cli(self, text='Synthetic feedback', old=False, explicit_wait=True):
        env = {'PATH':os.environ['PATH'],'HOME':str(self.root),'PYTHONPATH':str(Path(__file__).resolve().parents[1]),
               'AGENTJ_STATE_DIR':str(self.h.st.root),'AGENTJ_ELEVATE_SOCK':str(self.h.elevate.sock_path)}
        argv=[sys.executable,'-m','agentj.cli','support','feedback',text]
        if explicit_wait:
            argv.extend(['--wait', '0'])
        self.worker.stdin.write((json.dumps({'argv':argv,'env':env,'op':'old' if old else 'new'})+'\n').encode());await self.worker.stdin.drain()
        return json.loads(await asyncio.wait_for(self.worker.stdout.readline(),5))

    async def complete(self, output, call='feedback-1', turn='turn1', child=False):
        self.a._on_note('item/completed',{'threadId':'other-child' if child else self.a.tid,'turnId':turn,'item':{
            'type':'commandExecution','id':call,'status':'completed','exitCode':0,'aggregatedOutput':output}})
        await self.broker.drain()

    def receipts(self):
        return [row['reply']['text'] for row in self.h.hist.turns.values()
                if row['src'].get('k') == 'agent' and row['src'].get('quote', {}).get('id') == self.send.turn]

    async def test_native_feedback_ticket_visible_once_on_original_page(self):
        await self.admit()
        result = await self.cli()
        await self.complete(result['out'])
        await self.complete(result['out'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.receipts()), 1)
        self.assertIn('st_' + 'a' * 22, self.receipts()[0])
        self.assertNotIn('Synthetic feedback', self.receipts()[0])

    async def test_native_feedback_failure_is_visible_without_raw_detail_or_ticket(self):
        await self.admit()
        result = await self.cli()
        with patch.object(support, 'channel_for', side_effect=support.SupportError('QA_UNTRUSTED_DETAIL')):
            await self.complete(result['out'])
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(len(self.receipts()), 1)
        self.assertNotIn('QA_UNTRUSTED_DETAIL', self.receipts()[0])
        self.assertNotIn('st_', self.receipts()[0])

    async def change_after_http(self, change):
        import threading
        started, proceed = threading.Event(), threading.Event()
        channel = support.channel_for()
        send = channel.send
        def pause_after_response(fields):
            receipt = send(fields)
            started.set()
            if not proceed.wait(5):
                raise TimeoutError
            return receipt
        await self.admit()
        result = await self.cli()
        with patch.object(type(channel), 'send', lambda _,fields:pause_after_response(fields)):
            running=asyncio.create_task(self.complete(result['out']))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                change()
            finally:
                proceed.set()
            await running
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.receipts())
        self.assertFalse(any('st_'+'a'*22 in row['reply']['text'] for row in self.h.hist.turns.values()))

    async def test_changed_owner_after_http_never_gets_previous_ticket(self):
        from agentj import provenance
        def another_owner_message():
            later = self.h.hist_add({'k':'phone', 'dev':'another-device', 'text':'Different request'}, '', 'open')
            approval_review.remember(self.h, later, provenance.phone())
        await self.change_after_http(another_owner_message)

    async def test_changed_native_thread_after_http_never_gets_previous_ticket(self):
        await self.change_after_http(lambda:setattr(self.a, 'tid', 'different-thread'))

    async def test_revoked_remote_after_http_never_gets_previous_ticket(self):
        await self.change_after_http(lambda:self.h.st.remove_device(self.f.ph.did))

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

    async def test_phone_codex_native_execution_without_approval_rpc(self):
        command = "agentj support feedback 'Synthetic feedback'"
        from agentj import shared_risk
        await shared_risk.NativePathGuard(self.a).hook_event({
            'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': command},
            '_agentj_path_authenticated': True, 'session_id': self.a.tid, 'turn_id': 'turn1',
            'tool_use_id': 'native-start', 'cwd': str(self.root)})
        self.assertEqual((await self.cli())['exit'], 3)
        self.a._on_note('item/started', {'threadId': self.a.tid, 'turnId': 'turn1', 'item': {
            'type': 'commandExecution', 'id': 'native-start', 'status': 'inProgress',
            'command': '/bin/bash -lc ' + shlex.quote(command), 'cwd': str(self.root)}})
        async def answer():
            while True:
                for ask in list(self.h.asks.values()):
                    if not ask.fut.done():
                        await self.h._answer(self.f.s, self.f.ph.answer(self.h._ask_msg(ask, self.f.s), True))
                await asyncio.sleep(.005)
        responder = asyncio.create_task(answer())
        try:
            result = await self.cli(explicit_wait=False)
        finally:
            responder.cancel()
            await asyncio.gather(responder, return_exceptions=True)
        self.assertTrue(result['out'].startswith('SUPPORT_TASK_PENDING:'), result)
        self.assertEqual(self.calls, [])
        await self.complete(result['out'], call='native-start')
        await self.complete(result['out'], call='native-start')
        self.assertEqual(len(self.calls), 1)

    async def test_mismatched_native_start_and_repeated_prehook_cannot_admit(self):
        from agentj import feedback_native
        cmd = "agentj support feedback 'Synthetic feedback'"
        for cid in ('different-command', 'duplicate-hook'):
            evt = {'_agentj_path_authenticated': True, 'session_id': self.a.tid, 'turn_id': self.a.turn_id,
                   'tool_use_id': cid, 'cwd': str(self.root)}
            feedback_native.codex_prehook(self.a, evt, 'Bash', {'command': cmd})
            if cid == 'duplicate-hook':
                feedback_native.codex_prehook(self.a, evt, 'Bash', {'command': cmd})
            item = {'type': 'commandExecution', 'id': cid, 'status': 'inProgress', 'cwd': str(self.root),
                    'command': cmd if cid == 'duplicate-hook' else "agentj support feedback 'Another draft'"}
            self.a._on_note('item/started', {'threadId':self.a.tid, 'turnId':self.a.turn_id, 'item':item})
            self.assertEqual((await self.cli())['exit'], 3)
        self.assertEqual(self.calls, [])

    async def test_observed_start_then_request_approval_matches_exact_draft(self):
        from agentj import feedback_native
        cmd = "agentj support feedback 'Synthetic feedback' --wait 0"
        call = 'observed-then-rpc'
        event = {'_agentj_path_authenticated': True, 'session_id': self.a.tid, 'turn_id': self.a.turn_id,
                 'tool_use_id': call, 'cwd': str(self.root)}
        feedback_native.codex_prehook(self.a, event, 'Bash', {'command': cmd})
        self.a._on_note('item/started', {'threadId':self.a.tid, 'turnId':self.a.turn_id, 'item':{
            'type':'commandExecution', 'id':call, 'status':'inProgress', 'command':cmd, 'cwd':str(self.root)}})
        await self.admit(command=cmd, call=call, reason='Native request reason')
        bad = await self.cli('Changed payload')
        self.assertEqual(bad['exit'], 3)
        pending = await self.cli()
        self.assertTrue(pending['out'].startswith('SUPPORT_TASK_PENDING:'))
        self.assertEqual(self.calls, [])
        await self.complete(pending['out'], call=call)
        await self.complete(pending['out'], call=call)
        self.assertEqual(len(self.calls), 1)

    async def test_late_approval_rpc_shares_the_current_observed_decision(self):
        from agentj import feedback_native
        cmd = "agentj support feedback 'Synthetic feedback' --wait 0"
        call = 'observed-during-rpc'
        event = {'_agentj_path_authenticated': True, 'session_id': self.a.tid, 'turn_id': self.a.turn_id,
                 'tool_use_id': call, 'cwd': str(self.root)}
        feedback_native.codex_prehook(self.a, event, 'Bash', {'command': cmd})
        self.a._on_note('item/started', {'threadId':self.a.tid, 'turnId':self.a.turn_id, 'item':{
            'type':'commandExecution', 'id':call, 'status':'inProgress', 'command':cmd, 'cwd':str(self.root)}})

        async def waiting_for_decision():
            while not self.broker.calls.get(call, {}).get('feedback_decision'):
                await asyncio.sleep(.01)

        with patch.object(self.h, 'ask', wraps=self.h.ask) as ask:
            cli = asyncio.create_task(self.cli())
            try:
                await asyncio.wait_for(waiting_for_decision(), 3)
                await self.admit(command=cmd, call=call, reason='Late native request reason')
                pending = await cli
            finally:
                if not cli.done():
                    cli.cancel()
                    await asyncio.gather(cli, return_exceptions=True)
            ask.assert_awaited_once()
        self.assertTrue(pending['out'].startswith('SUPPORT_TASK_PENDING:'))
        await self.complete(pending['out'], call=call)
        self.assertEqual(len(self.calls), 1)

    async def test_fenced_owner_update_opens_one_version_bound_phone_card(self):
        from agentj import update
        approval_material.turn_start(self.h, self.send)
        await asyncio.sleep(.035)
        with patch.object(update, 'check', return_value={'status':'newer', 'latest':'1.2.3'}):
            for _ in range(2):
                result = await self.update_offer()
                self.assertTrue(result['ok'], result)
                self.assertEqual(self.h.upg['target'], '1.2.3')
                self.assertEqual(result['id'], self.h.upg['id'])
            self.assertEqual(result['card'], 'reused')
            self.a.cur_send = None
            self.assertFalse((await self.update_offer())['ok'])

    async def update_offer(self):
        env = {'PATH':os.environ['PATH'], 'HOME':str(self.root), 'AGENTJ_ELEVATE_SOCK':str(self.h.elevate.sock_path),
               'PYTHONPATH':str(Path(__file__).resolve().parents[1]), 'AGENTJ_STATE_DIR':str(self.h.st.root)}
        code = ("from agentj import elevate; import json,os; "
                "print(json.dumps(elevate.client_request(None,{'t':'upgrade','op':'phone_offer','version':'1.2.3'},"
                "5,path=os.environ['AGENTJ_ELEVATE_SOCK'])))")
        self.worker.stdin.write((json.dumps({'argv':[sys.executable,'-c',code],'env':env})+'\n').encode())
        await self.worker.stdin.drain()
        return json.loads(json.loads(await asyncio.wait_for(self.worker.stdout.readline(),7))['out'])

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

    async def test_kernel_root_stays_live_across_scheduler_states(self):
        from agentj import peer_origin
        read_stat = peer_origin._stat
        state = 'S'

        def changed_state(proc, pid):
            row = read_stat(proc, pid)
            return (state, *row[1:]) if row else None

        with patch.object(peer_origin, '_stat', side_effect=changed_state):
            await self.admit()
            pending = await self.cli()
            self.assertTrue(pending['out'].startswith('SUPPORT_TASK_PENDING:'))
            state = 'R'
            await self.complete(pending['out'])
        self.assertEqual(len(self.calls), 1)

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
        for c in ('agentj support feedback safe', "agentj support feedback 'safe' --wait 0",'agentj support report --file draft.txt --wait 0'):
            self.assertIsNotNone(parse('Bash',{'command':c},'/tmp'))
        for c in ('agentj support ask safe','agentj support feedback safe --wait 10',
                  'agentj support feedback $BODY --wait 0','agentj support feedback safe --wait 0; echo later'):
            self.assertIsNone(parse('Bash',{'command':c},'/tmp'))
