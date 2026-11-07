"""One diagnostic for the old terminal fixture's wire digest, never a real login."""
import _hermetic
import asyncio
import unittest
from unittest.mock import patch
import test_p408_terminal as fixture

class TerminalFixture(unittest.TestCase):
    def test_old_helper_refusal_is_signature_not_owner_source(self):
        rig=fixture.Rig();rig.setUp();self.addCleanup(rig.tearDown)
        rig.passkey();rig.enable()
        async def run():
            try:
                await rig.step2(await rig.step1())
                card=await rig.until(lambda:next((o for o in rig.msgs('elev') if o.get('kind')=='login'),None),2)
                self.assertTrue(card['envelope']['source']['verified'])
                self.assertFalse(card['envelope']['source']['suspicious'])
                reasons=[];check=rig.host.elevate._check
                def inspect(*args):
                    why=check(*args);reasons.append(why);return why
                with patch.object(rig.host.elevate,'_check',side_effect=inspect):
                    legacy={k:v for k,v in card.items() if k!='envelope_sha256'}
                    await rig.answer_login(legacy,fixture.GOOD)
                self.assertEqual(reasons,['bad_signature'])
            finally:
                await rig.host.elevate.stop()
                await rig.desk.close('owner')
        asyncio.run(run())
