"""#572 five released-v1 scenarios on real Host and synthetic P256/Ed25519 keys."""
import _hermetic
import asyncio
import unittest
from test_561_protocol_rollback import LegacyWebProtocol
from agentj import passkey, wire

class LegacyFive(LegacyWebProtocol):
    def test_five_legacy_scenarios(self):
        ph, host, session = self.rig()
        async def go():
            waiter = asyncio.create_task(host.ask('Bash', {'command':'echo synthetic-no-execution'}, require_passkey=True))
            for _ in range(300):
                if self.asks() or waiter.done(): break
                await asyncio.sleep(.01)
            self.assertFalse(waiter.done())
            card = self.asks()[-1]
            good = ph.answer(card, True)
            a = host.asks[card['id']]
            challenge = passkey.elevate_challenge(host.channel,ph.did,a.rid,a.pk_kind,a.rid,a.digest)
            no_uv = {**good,'fa':ph.auth.assert_(challenge,b'0',nonce=b'0',flags=0x01)}
            wrong_rid = {**good,'fa':ph.auth.assert_(passkey.elevate_challenge(host.channel,ph.did,'f'*32,a.pk_kind,'f'*32,a.digest),b'0',nonce=b'0')}
            for label, packet, reason in [
                ('forged',{**good,'sig':wire.b64u(bytes(64))},'bad_signature'),
                ('missingUV',{k:v for k,v in good.items() if k!='fa'},'passkey_required'),
                ('noUVflag',no_uv,'passkey_required'),
                ('wrongrid',wrong_rid,'passkey_required')]:
                with self.subTest(label=label):
                    await host._app(session, packet)
                    self.assertFalse(waiter.done())
                    self.assertEqual(self.sent[-1][1]['why'],reason)
            await host._app(session,good)
            self.assertEqual((await waiter)['behavior'],'allow')
        asyncio.run(go())
