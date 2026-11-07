"""Ordinary approval source v2, actual Ed25519/WebAuthn, with released-v1 compatibility."""
import _hermetic  # noqa: F401
import asyncio
import json
import pathlib
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import approval_context, approvals, passkey, wire
import test_l1 as l1

class ContextProtocol(unittest.TestCase):
    setUp, tearDown, asks = l1.Flows.setUp, l1.Flows.tearDown, l1.Flows.asks

    def rig(self):
        ph=l1.Phone(self.st,with_passkey=True)
        host=l1._host(self.st,self.sent);s=l1._ready(host,ph)
        host.agent=SimpleNamespace(kind='claude',status='idle',cur_send=SimpleNamespace(source={'kind':'phone','role':'owner'}))
        turn=host.hist.add({'k':'phone','text':'verified human root'})
        host.agent.cur_send.approval_origin=approval_context.remember(host,turn,{'kind':'phone','role':'owner'})
        host.ask_ttl=3
        return ph,host,s

    def flow(self,version):
        ph,host,s=self.rig()
        async def go():
            task=asyncio.create_task(host.ask('Bash',{'command':'echo protocol-572-no-execution'},require_passkey=True))
            while not self.asks() and not task.done():await asyncio.sleep(.01)
            self.assertFalse(task.done())
            card=self.asks()[-1];a=host.asks[card['id']]
            self.assertEqual(card['approval_v'],2)
            self.assertTrue(card['envelope']['source']['verified'])
            self.assertEqual(card['envelope']['source']['root']['text'],'verified human root')
            obj=ph.answer(card,True)
            if version==2:
                obj.update(approval_v=2,revision=card['revision'],envelope_sha256=card['envelope_sha256'])
                # Actual webpage encoder from the web owner's frozen source.
                js=pathlib.Path(os.environ.get('AGENTJ_572_WEB_WIRE') or pathlib.Path(__file__).resolve().parents[2]/'protocol/wire.js')
                script='import {approvalV2Message} from '+json.dumps(js.as_uri())+';const p=JSON.parse(process.argv[1]);console.log(Buffer.from(await approvalV2Message(...p)).toString("base64url"));'
                preimage=wire.unb64u(subprocess.check_output(['node','--input-type=module','-e',script,json.dumps([host.channel,ph.did,{**card,'envelope_verified':True},'allow'])],text=True).strip())
                obj['sig']=wire.b64u(ph.sk.sign(preimage))
                obj['fa']=ph.auth.assert_(passkey.elevate_challenge(host.channel,ph.did,a.rid,a.pk_kind,a.rid,a.envelope_digest),b'0',nonce=b'0')
                await host._app(s,{**obj,'envelope_sha256':'0'*64})
                self.assertFalse(task.done())
                self.assertEqual(self.sent[-1][1]['why'],'approval_context')
            else:
                js=pathlib.Path(__file__).resolve().parent/'fixtures/p561-legacy-0270/proto/wire.js'
                script='import {approveMessage} from '+json.dumps(js.as_uri())+';const p=JSON.parse(process.argv[1]);console.log(Buffer.from(await approveMessage(...p)).toString("base64url"));'
                preimage=wire.unb64u(subprocess.check_output(['node','--input-type=module','-e',script,json.dumps([host.channel,ph.did,a.rid,'allow',card['tool'],card['summary']])],text=True).strip())
                obj['sig']=wire.b64u(ph.sk.sign(preimage))
                await host._app(s,{**obj,'revision':1})
                self.assertFalse(task.done());self.assertEqual(self.sent[-1][1]['why'],'approval_fields')
            await host._app(s,{**obj,'sig':wire.b64u(bytes(64))})
            self.assertFalse(task.done());self.assertEqual(self.sent[-1][1]['why'],'bad_signature')
            await host._app(s,{k:v for k,v in obj.items() if k!='fa'})
            self.assertFalse(task.done());self.assertEqual(self.sent[-1][1]['why'],'passkey_required')
            await host._app(s,obj)
            result=await task
            self.assertEqual(result['behavior'],'allow')
            log=approvals.read_log(self.st)[-1]
            self.assertEqual(approvals.check_record(self.st,log),'ok')
            self.assertEqual(log.get('approval_v'),2 if version==2 else None)
        asyncio.run(go())

    def test_current_legacy_js_uv_still_approves(self):self.flow(1)
    def test_new_js_v2_uv_approves_exact_envelope(self):self.flow(2)

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
