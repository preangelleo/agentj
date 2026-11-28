"""Owner chat key card: real UDS early receipt, signed phone decision, disk and main history."""
import asyncio
import json
import os
import stat
import time
import unittest
from unittest.mock import patch

import _hermetic
from agentj import approval_delivery, approval_material, approval_review, diskq, provenance, serve, wire
from agentj.noise import IK, Handshake, Keypair
from test_f17 import answer
import test_0335_secret_validation as validation


class ChatSecretReceipt(unittest.IsolatedAsyncioTestCase):
    setUp = validation.SecretValidation.setUp
    of = validation.SecretValidation.of
    wait_for = validation.SecretValidation.wait_for
    _turn = validation.SecretValidation._turn
    req = validation.SecretValidation.req

    async def asyncSetUp(self):
        await validation.SecretValidation.asyncSetUp(self)
        self.host.lang = 'en'
        self.st.set_agent_session(self.host.agent.kind, 'qa-native-session')
        self.followups = []
        self.host.agent.submit = self.followups.append

    async def asyncTearDown(self):
        await validation.SecretValidation.asyncTearDown(self)
        # Test cleanup runs before the event loop's own disk queue shutdown.
        self.assertTrue(await diskq.aflush())

    async def open_card(self, **kw):
        self.proof = patch.object(approval_material, 'verified_phone_chat', return_value=(self.host.agent, self.origin))
        with self.proof:
            r, w = await asyncio.open_unix_connection(str(self.el.sock_path))
            w.write((json.dumps(self.req(wait=30, **kw)) + '\n').encode())
            await w.drain()
            early = json.loads(await asyncio.wait_for(r.readline(), 3))
        self.addCleanup(w.close)
        self.assertEqual(early['t'], 'card')
        self.delivery_status = early.get('delivery_status')
        return r, w, next(x for x in self.of('elev') if x['id'] == early['id'])

    def replies(self):
        return [row['reply']['text'] for row in self.host.hist.turns.values()
                if row['src'].get('quote', {}).get('id') == self.origin['message_id']]

    async def await_reply(self):
        for _ in range(250):
            if self.replies():
                return
            await asyncio.sleep(.01)
        self.fail('no persisted receipt linked to original owner turn')

    async def test_live_cli_gets_signed_disk_receipt_and_main_reply(self):
        r, w, frame = await self.open_card()
        self.assertEqual(self.delivery_status, 'sent')
        self.assertEqual(self.replies(), [])
        await self.host._app(self.s, answer(self.ph, frame, True, 'synthetic-card-input'))
        self.assertEqual(json.loads(await r.readline())['result'], 'saved')
        await self.await_reply()
        self.assertEqual(self.replies(), ['Received. Your key has been saved.'])
        self.assertEqual((self.d/'value').read_text(), 'synthetic-card-input')
        self.assertFalse([s for s in self.followups if s.device == 'agentj-secret'])

    async def test_detached_cli_resumes_once_then_rechecks_new_owner(self):
        r, w, frame = await self.open_card()
        w.close()
        await w.wait_closed()
        self.el.secret_waiters[frame['id']] = 1
        await self.host._app(self.s, answer(self.ph, frame, True, 'synthetic-card-input'))
        await self.await_reply()
        self.assertEqual(len(self.followups), 1)
        continuation, = self.followups
        self.assertTrue(continuation.delivery_allowed())
        self.assertEqual(continuation.source, provenance.system())
        self.assertTrue(await continuation.wait_ready())
        later = self.host.hist_add({'k':'phone','dev':self.s.device,'text':'another task'}, '', 'open')
        approval_review.remember(self.host, later, provenance.phone())
        self.assertFalse(continuation.delivery_allowed())
        await self.host._app(self.s, answer(self.ph, frame, True, 'synthetic-card-input'))
        self.assertEqual(len(self.followups), 1)

    async def test_decline_and_expiry_have_ordinary_main_receipts(self):
        for expire, text in [(False,'You declined the key card. No key was saved.'),
                             (True,'The key card expired without saving a key.')]:
            r, w, frame = await self.open_card()
            if expire:
                self.el.cards[frame['id']]['deadline'] = time.monotonic() - 1
            else:
                await self.host._app(self.s, answer(self.ph, frame, False))
            await asyncio.wait_for(r.readline(), 3)
            self.assertIn(text, self.replies())
            self.assertFalse((self.d/'value').exists())
            self.assertFalse([s for s in self.followups if s.device == 'agentj-secret'])

    async def test_unconfirmed_directory_sync_never_claims_saved(self):
        r, w, frame = await self.open_card()
        sync = os.fsync
        def fault(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('directory sync unavailable')
            sync(fd)
        with patch.object(os, 'fsync', side_effect=fault):
            await self.host._app(self.s, answer(self.ph, frame, True, 'synthetic-card-input'))
            self.assertEqual(json.loads(await r.readline())['result'], 'unknown')
        self.assertEqual(self.replies(), ['Saving the key could not be confirmed.'])
        self.assertFalse([s for s in self.followups if s.device == 'agentj-secret'])


    async def test_offline_card_replays_only_with_resumed_registered_signing_key(self):
        from unittest.mock import AsyncMock
        from test_l1 import _raw
        # A fresh Noise public key associated with a synthetic registered signing key.
        kp = Keypair.generate()
        sk = _raw(self.ph.sk.public_key())
        device = self.st.add_device(kp.pub, 'Other paired phone', sk)
        r, w = await asyncio.open_unix_connection(str(self.el.sock_path))
        self.addCleanup(w.close)
        with patch.object(approval_material, 'verified_phone_chat', return_value=(self.host.agent, self.origin)):
            w.write((json.dumps(self.req(wait=30)) + '\n').encode())
            await w.drain()
            early = json.loads(await r.readline())
        self.assertEqual(early['delivery_status'], 'partial')
        rid = early['id']
        self.host.send_app = AsyncMock(return_value=True)
        self.host._op = AsyncMock()
        for candidate, deliveries in [(b'x' * 32, 0), (sk, 1)]:
            phone = Handshake(IK, True, kp, prologue=wire.resume_prologue(self.host.channel), rs=self.host.kp.pub)
            session = serve.Session(cid=22)
            self.host.sessions[22] = session
            await self.host._resume_init(session, phone.write_message(json.dumps({'sk':wire.b64u(candidate)}).encode()))
            self.assertEqual(session.device, device)
            session.state = 'ready'
            self.assertEqual(await approval_delivery.replay(self.host, session), deliveries)
        self.assertFalse((self.d / 'value').exists())
        self.assertEqual(self.replies(), [])
        self.assertEqual(self.host.send_app.await_args.args[1]['id'], rid)
