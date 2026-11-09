"""Ordinary approval source v2, actual Ed25519/WebAuthn, with released-v1 compatibility."""
import _hermetic  # noqa: F401
import asyncio
import json
import pathlib
import os
import subprocess
import shutil
import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import approval_context, approvals, passkey, wire, functions
import test_l1 as l1
import test_p513b_summary as actual_wire

class ContextProtocol(unittest.TestCase):
    setUp, tearDown, asks = l1.Flows.setUp, l1.Flows.tearDown, l1.Flows.asks

    def rig(self):
        ph=l1.Phone(self.st,with_passkey=True)
        host=l1._host(self.st,self.sent);s=l1._ready(host,ph)
        actual_wire.real_outbound(host,s,self.sent)
        host.agent=SimpleNamespace(kind='claude',status='idle',cur_send=SimpleNamespace(source={'kind':'phone','role':'owner'}))
        turn=host.hist.add({'k':'phone','text':'verified human root'})
        host.agent.cur_send.approval_origin=approval_context.remember(host,turn,{'kind':'phone','role':'owner'})
        host.ask_ttl=3
        return ph,host,s

    def flow(self,version):
        ph,host,s=self.rig()
        async def go():
            functions._save(self.st, {'protocol572': functions.record('protocol572', ['echo', 'protocol-572-no-execution'], str(self.st.root))})
            task=asyncio.create_task(functions.handle(host, {'t':'fn','op':'remove','name':'protocol572'}, peer=os.getpid()))
            try:
                for _ in range(200):
                    if self.asks() or task.done():break
                    await asyncio.sleep(.005)
                self.assertFalse(task.done())
                card=self.asks()[-1];a=host.asks[card['id']]
                self.assertEqual(card['approval_v'],2)
                self.assertTrue(card['envelope']['source']['verified'])
                self.assertEqual(card['envelope']['source']['root']['text'],'verified human root')
                async def refused(answer, why):
                    start=len(self.sent)
                    await host._app(s,answer)
                    self.assertFalse(task.done())
                    frames=[frame for _,frame in self.sent[start:] if frame.get('t')=='answer_refused' and frame.get('id')==card['id']]
                    self.assertEqual([frame['why'] for frame in frames],[why])
                obj=ph.answer(card,True)
                before=len(self.sent)
                await host._app(s,{**obj,'id':'unknown-protocol572'})
                self.assertFalse(task.done())
                self.assertEqual(len(self.sent),before)
                self.assertIn('protocol572',functions._load(self.st))
                if version==2:
                    obj.update(approval_v=2,revision=card['revision'],envelope_sha256=card['envelope_sha256'])
                    # Actual webpage encoder from the web owner's frozen source.
                    js=pathlib.Path(os.environ.get('AGENTJ_572_WEB_WIRE') or pathlib.Path(__file__).resolve().parents[2]/'protocol/wire.js')
                    script='import {approvalV2Message} from '+json.dumps(js.as_uri())+';const p=JSON.parse(process.argv[1]);console.log(Buffer.from(await approvalV2Message(...p)).toString("base64url"));'
                    # Match the public snap verification before enabling its encoder.
                    self.assertEqual(card['envelope']['request_id'], card['id'])
                    self.assertEqual(card['envelope']['channel'], host.channel)
                    self.assertEqual(card['envelope']['revision'], card['revision'])
                    self.assertEqual(card['envelope']['details'], card['summary'])
                    from agentj import approval_review
                    self.assertEqual(approval_review.digest(card['envelope']), card['envelope_sha256'])
                    preimage=wire.unb64u(subprocess.check_output([os.environ.get('AGENTJ_TEST_NODE') or shutil.which('node') or 'node','--input-type=module','-e',script,json.dumps([host.channel,ph.did,{**card,'envelope_verified':True},'allow'])],text=True,timeout=10).strip())
                    obj['sig']=wire.b64u(ph.sk.sign(preimage))
                    obj['fa']=ph.auth.assert_(passkey.elevate_challenge(host.channel,ph.did,a.rid,a.pk_kind,a.rid,a.envelope_digest),b'0',nonce=b'0')
                    await refused({**obj,'revision':card['revision']+1},'approval_context')
                    await refused({**obj,'envelope_sha256':'0'*64},'approval_context')
                else:
                    js=pathlib.Path(__file__).resolve().parent/'fixtures/p561-legacy-0270/proto/wire.js'
                    script='import {approveMessage} from '+json.dumps(js.as_uri())+';const p=JSON.parse(process.argv[1]);console.log(Buffer.from(await approveMessage(...p)).toString("base64url"));'
                    preimage=wire.unb64u(subprocess.check_output([os.environ.get('AGENTJ_TEST_NODE') or shutil.which('node') or 'node','--input-type=module','-e',script,json.dumps([host.channel,ph.did,a.rid,'allow',card['tool'],card['summary']])],text=True,timeout=10).strip())
                    obj['sig']=wire.b64u(ph.sk.sign(preimage))
                    await refused({**obj,'revision':1},'approval_fields')
                await refused({**obj,'sig':wire.b64u(bytes(64))},'bad_signature')
                await refused({k:v for k,v in obj.items() if k!='fa'},'passkey_required')
                await host._app(s,obj)
                result=await asyncio.wait_for(task,2)
                self.assertTrue(result['ok'], result)
                self.assertEqual(functions._load(self.st), {})
                await host._app(s,obj)
                self.assertFalse(host.asks)
                log=approvals.read_log(self.st)[-1]
                self.assertEqual(approvals.check_record(self.st,log),'ok')
                self.assertEqual(log.get('approval_v'),2 if version==2 else None)
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        asyncio.run(go())

    def test_current_legacy_js_uv_still_approves(self):self.flow(1)
    def test_new_js_v2_uv_approves_exact_envelope(self):self.flow(2)

    def test_original_bash_vector_is_ordinary_despite_uv_hint(self):
        ph,host,s=self.rig()
        async def go():
            task=asyncio.create_task(host.ask('Bash',{'command':'echo protocol-572-no-execution'},require_passkey=True))
            try:
                for _ in range(200):
                    if self.asks() or task.done():break
                    await asyncio.sleep(.005)
                self.assertFalse(task.done())
                card=self.asks()[-1]
                self.assertFalse(card.get('passkey_required'))
                js=pathlib.Path(__file__).resolve().parent/'fixtures/p561-legacy-0270/proto/wire.js'
                script='import {approveMessage} from '+json.dumps(js.as_uri())+';const p=JSON.parse(process.argv[1]);console.log(Buffer.from(await approveMessage(...p)).toString("base64url"));'
                preimage=wire.unb64u(subprocess.check_output([os.environ.get('AGENTJ_TEST_NODE') or shutil.which('node') or 'node','--input-type=module','-e',script,json.dumps([host.channel,ph.did,card['id'],'allow',card['tool'],card['summary']])],text=True,timeout=10).strip())
                answer=ph.answer(card,True)
                answer['sig']=wire.b64u(ph.sk.sign(preimage))
                await host._app(s,answer)
                self.assertEqual((await asyncio.wait_for(task,2))['behavior'],'allow')
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        asyncio.run(go())

    def test_final_snapshot_preserves_old_v2_display_and_true_v1_audit(self):
        from agentj import approval_review
        import unicodedata
        ph,host,s=self.rig()
        inp={'command':'echo 界\u200b', 'note':'quoted {"z":2,"a":1}', 'password':'synthetic-private-protocol572'}
        raw_input=json.loads(json.dumps(inp))
        async def go():
            task=asyncio.create_task(host.ask('Bash',inp,_harness_permission=True))
            try:
                for _ in range(200):
                    if self.asks() or task.done():break
                    await asyncio.sleep(.005)
                self.assertFalse(task.done())
                card=self.asks()[-1];a=host.asks[card['id']]
                # Reproduce the previous bind's display calculation, independent
                # of the new complete-snapshot helper.
                raw='Bash\n'+approval_review.canonical(a.display_normalizer(inp))
                old=a.display_normalizer(str(raw or ''))
                old=''.join(c for c in old if c in '\n\t' or unicodedata.category(c) not in ('Cc','Cf','Cs'))
                self.assertEqual(card['envelope']['details'],old)
                self.assertEqual(card['summary'],a.summary)
                self.assertEqual(a.summary,old)
                self.assertNotIn('synthetic-private-protocol572',json.dumps(card))
                self.assertNotIn('\u200b',card['summary'])
                self.assertEqual(a.tool_input,raw_input)
                self.assertEqual(a.input_sha,approvals.input_digest(raw_input))
                self.assertEqual(a.legacy_digest,approvals.shown_digest(card['tool'],card['summary']))
                self.assertEqual(card['envelope']['source']['root']['text'],'verified human root')
                import copy
                for changed in ('missing','changed','overflow'):
                    broken=copy.copy(a)
                    if changed=='missing':del broken._list_display_snapshot
                    elif changed=='changed':broken.summary += ' changed'
                    else:broken.summary=broken._list_display_snapshot='x'*24001
                    previous=broken.review_revision
                    with self.assertRaisesRegex(ValueError,'invalid frozen approval display'):
                        approval_review.bind(host,broken)
                    self.assertEqual(broken.review_revision,previous)
                # Released v1 signs only the actual delivered tool and summary.
                await host._app(s,ph.answer(card,True))
                self.assertEqual((await asyncio.wait_for(task,2))['behavior'],'allow')
                row=approvals.read_log(self.st)[-1]
                self.assertEqual(row['shown_sha256'],approvals.shown_digest(card['tool'],card['summary']))
                self.assertEqual(approvals.check_record(self.st,row),'ok')
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
        asyncio.run(go())

    def test_final_utf8_budget_rejects_projection_growth_without_card(self):
        from agentj import approval_review
        ph,host,s=self.rig()
        inp={'command':'echo synthetic-budget-no-execution','padding':{f'k{i:04}':0 for i in range(2100)}}
        raw='Bash\n'+approval_review.canonical(inp)
        self.assertLess(len(raw.encode('utf-8')),24000)
        async def go():
            result=await host.ask('Bash',inp,_harness_permission=True)
            self.assertEqual(result['reason'],'too_large')
            self.assertFalse(host.asks)
            self.assertFalse(self.asks())
            result=await host.ask('Bash',{'command':'echo '+'界'*8000},_harness_permission=True)
            self.assertEqual(result['reason'],'too_large')
            self.assertFalse(host.asks)
            self.assertFalse(self.asks())
        asyncio.run(go())

    def test_source_ledger_retains_root_and_does_not_invent_historical_owner(self):
        ph,host,s=self.rig()
        turn=host.hist.add({'k':'phone','text':'human original task'})
        ref=approval_context.remember(host,turn,{'kind':'phone','role':'owner'})
        host.agent.cur_send.approval_origin=ref
        self.assertEqual(approval_context.snapshot(host)['text'],'human original task')
        approval_context.launch(host,'agentj-572.service')
        held=host.__dict__['_approval_launch_refs']['agentj-572.service']
        self.assertEqual(held['text'],'human original task')
        self.assertEqual(held['chain'][-1],{'kind':'same_host','id':'agentj-572.service'})
        del host.agent.cur_send.approval_origin
        self.assertFalse(approval_context.snapshot(host))

    def test_desktop_origin_and_injected_tool_ids_do_not_replace_it(self):
        from agentj import serve, provenance
        ph,host,s=self.rig()
        del host.agent.cur_send.approval_origin
        host.agent.phone_turn=False
        async def go():
            host.desktop_input('actual terminal human request')
            self.assertEqual(approval_context.snapshot(host)['text'],'actual terminal human request')
            a=serve.Ask('request','Bash','summary','legacy',{'root_message_id':'invented','root':{'text':'fake'}},'input',1,asyncio.get_running_loop().create_future())
            approval_context.bind(host,a)
            self.assertEqual(a.envelope['source']['root']['text'],'actual terminal human request')
            with provenance.request_source({'kind':'unknown'}):
                b=serve.Ask('request','Bash','summary','legacy',{'root':{'text':'fake'}},'input',1,asyncio.get_running_loop().create_future())
                approval_context.bind(host,b)
                self.assertFalse(b.envelope['source']['verified'])
                self.assertEqual(b.envelope['source']['root']['text'],'')
        asyncio.run(go())
        self.assertIn('[truncated]',approval_context.safe_text('x'*1500,1200))

    def test_short_quoted_assignment_password_is_one_redaction(self):
        self.assertEqual(approval_context.safe_text('password="a bb"'),'password=<redacted>')

    def test_short_quoted_flag_password_is_one_redaction(self):
        self.assertEqual(approval_context.safe_text('--password "a bb"'),'--password <redacted>')

    def test_short_quoted_chinese_password_is_one_redaction(self):
        self.assertEqual(approval_context.safe_text('密码="短 词"'),'密码=<redacted>')

    def test_short_quoted_api_key_is_one_redaction(self):
        self.assertEqual(approval_context.safe_text('api_key="a bb"'),'api_key=<redacted>')
        self.assertEqual(approval_context.safe_text('api_key="<redacted> bb"'),'api_key=<redacted>')
