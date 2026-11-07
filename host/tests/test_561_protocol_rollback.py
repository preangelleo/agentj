"""#561 legacy web approval protocol on real Host; isolated synthetic keys only.

A harmless echo card is explicitly UV-required. Nothing executes a command.
No verifier mock: both Ed25519 and WebAuthn P256 are actually checked.
"""
import _hermetic
import asyncio
import subprocess,json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from agentj import approvals, wire, approval_review, provenance
from test_l1 import Flows, Phone, _host, _ready


class LegacyWebProtocol(Flows):
    def rig(self):
        ph = Phone(self.st, with_passkey=True)
        host = _host(self.st, self.sent)
        s = _ready(host, ph)
        host.agent = SimpleNamespace(kind='claude', status='idle', cur_send=SimpleNamespace(source={'kind':'phone','role':'owner'}))
        with mock.patch.object(host,'hist_emit'):
            turn=host.hist_add({'k':'phone','dev':ph.did,'name':'Owner','text':'Synthetic paired owner protocol request.'},'', 'done')
        host.agent.cur_send.approval_origin=approval_review.remember(host,turn,provenance.phone())
        host.ask_ttl = 3
        return ph, host, s

    def test_legacy_signed_uv_allows_and_forgery_missing_uv_reject(self):
        ph, host, s = self.rig()
        async def go():
            wait = asyncio.create_task(host.ask('Bash', {'command':'echo protocol-561-no-execution'}, require_passkey=True))
            while not self.asks() and not wait.done(): await asyncio.sleep(.01)
            self.assertFalse(wait.done())
            card = self.asks()[-1]
            self.assertTrue(card['passkey_required'])
            obj = ph.answer(card, True)
            frozen = Path(__file__).resolve().parent/'fixtures/p561-legacy-0270/proto/wire.js'
            script = "import {approveMessage} from " + json.dumps(frozen.as_uri()) + "; const p=JSON.parse(process.argv[1]); console.log(Buffer.from(await approveMessage(...p)).toString('base64url'));"
            preimage = wire.unb64u(subprocess.check_output(['node','--input-type=module','-e',script,json.dumps([ph.channel,ph.did,card['id'],'allow',card['tool'],card['summary']])],text=True).strip())
            # Signature is over the actual frozen released webpage's JS encoder.
            obj['sig'] = wire.b64u(ph.sk.sign(preimage))
            # Released 0.27 message: no #541 v2 envelope or review revision.
            self.assertEqual(set(obj), {'t','id','ok','sig','fa'})
            forged = {**obj, 'sig':wire.b64u(bytes(64))}
            await host._app(s, forged)
            self.assertFalse(wait.done())
            self.assertEqual(self.sent[-1][1]['why'], 'bad_signature')
            await host._app(s, {k:v for k,v in obj.items() if k!='fa'})
            self.assertFalse(wait.done())
            self.assertEqual(self.sent[-1][1]['why'], 'passkey_required')
            await host._app(s, obj)
            result = await wait
            self.assertEqual(result['behavior'], 'allow')
            log = approvals.read_log(self.st)[-1]
            self.assertEqual(log['decision'], 'allow')
            self.assertEqual(approvals.check_record(self.st, log), 'ok')
        asyncio.run(go())
