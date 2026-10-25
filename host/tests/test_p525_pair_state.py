"""P525: device changes reach already-open remotes, and reconnect repairs missed state."""
import _hermetic  # noqa: F401
import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock

from agentj import first_run, serve, wire
import test_p72_onboarding as fixtures
PC, PHONE = fixtures.PC, fixtures.PHONE


class PairState(unittest.TestCase):
    PASS = fixtures.Serve.PASS
    def setUp(self):
        fixtures.Serve.setUp(self)
        self.host._ready_then_onboard = AsyncMock()
        self.host.theme_cached = {"t": "theme"}
        self.host._hk = {"plat": "linux", "boot": "login"}

    def tearDown(self):
        fixtures.Serve.tearDown(self)
    def last(self, cid, kind):
        return [m for c, m in self.sent if c == cid and m.get('t') == kind][-1]

    async def pair(self, cid, name):
        pub = os.urandom(32)
        s = serve.Session(cid=cid, state='pending', device=wire.device_id(pub), name=name,
                          pub=pub, h=os.urandom(32), p33=True)
        s.deadline = time.monotonic() + 60
        self.host.sessions[cid] = s
        await asyncio.wait_for(self.host._grant_pair(s), 3)
        return s

    def test_open_computers_pair_unbind_and_reconnect(self):
        async def go():
            self.host.agent = None
            a = await self.pair(71, PC)
            await self.host.onboarding_paired(a)  # legacy baseline's first hint is the positive control
            # Two connections of the same remote must both update.
            b = serve.Session(cid=72, state='ready', device=a.device, name=PC, pub=a.pub, p33=True)
            self.host.sessions[b.cid] = b
            self.assertEqual(self.last(71, 'pair_guide')['kind'], 'computer')
            # Only device-dependent probes; no browser/service/network or machine changes.
            with first_run._lock(self.st):
                rec = first_run._blank(self.st)
                rec['resumed_at'] = int(time.time())
                first_run._save(self.st, rec)
            ph = await self.pair(73, PHONE)
            for cid in (71, 72, 73):
                self.assertIsNone(self.last(cid, 'pair_guide')['kind'])
                self.assertEqual(len(self.last(cid, 'devs')['items']), 2)
            await asyncio.sleep(0)  # drain the existing push_setup queue
            snap = first_run.status_(self.st)
            self.assertEqual(next(i for i in snap['items'] if i['id'] == 'phone')['state'], 'verified')
            await self.host.revoke(ph.device)
            for cid in (71, 72):
                self.assertEqual(self.last(cid, 'pair_guide')['kind'], 'computer')
                self.assertEqual(len(self.last(cid, 'devs')['items']), 1)
            snap = first_run.status_(self.st)
            self.assertEqual(next(i for i in snap['items'] if i['id'] == 'phone')['state'], 'waiting_local')
            # Disconnect, miss a pairing, then resume without reloading the page.
            self.host.sessions.pop(72)
            await self.pair(74, PHONE)
            self.host.sessions[72] = b
            self.sent.clear()
            await self.host.on_ready(b, None)
            self.assertIsNone(self.last(72, 'pair_guide')['kind'])
            self.assertEqual(len(self.last(72, 'devs')['items']), 2)
            await self.host.revoke(a.device)
            self.assertFalse(any(c in (71, 72) and m['t'] == 'devs' for c, m in self.sent[self.sent.index((72, self.last(72, 'devs'))) + 1:]))
        asyncio.run(asyncio.wait_for(go(), 4))

    def test_multiple_computers_still_need_phone_and_last_phone_unbind_restores_hint(self):
        async def go():
            self.host.agent = None
            await self.pair(71, PC)
            await self.pair(72, "网页 · macOS Safari")
            for cid in (71, 72):
                self.assertEqual(self.last(cid, 'pair_guide')['kind'], 'computer')
            ph = await self.pair(73, PHONE)
            for cid in (71, 72, 73):
                self.assertIsNone(self.last(cid, 'pair_guide')['kind'])
            await self.host.revoke(ph.device)
            for cid in (71, 72):
                self.assertEqual(self.last(cid, 'pair_guide')['kind'], 'computer')
                self.assertEqual(len(self.last(cid, 'devs')['items']), 2)
        asyncio.run(asyncio.wait_for(go(), 4))

    def test_no_checklist_is_created_and_pending_remote_gets_no_state(self):
        async def go():
            self.host.agent = None
            await self.pair(71, PHONE)
            self.assertIsNone(first_run.read(self.st))
            self.host.sessions[79] = serve.Session(cid=79, state='pending', pub=os.urandom(32), p33=True)
            self.sent.clear()
            await self.host.remotes.push_list()
            self.assertFalse(any(c == 79 for c, _ in self.sent))
            self.assertEqual(self.last(71, 'pair_guide')['kind'], 'phone')
        asyncio.run(asyncio.wait_for(go(), 4))

    def test_closed_checklist_survives_changes_and_resume(self):
        async def go():
            self.host.agent = None
            a = await self.pair(71, PC)
            with first_run._lock(self.st):
                rec = first_run._blank(self.st)
                rec['closed'] = True
                first_run._save(self.st, rec)
            await self.pair(72, PHONE)
            await asyncio.sleep(0)
            self.assertTrue(self.last(71, 'setup_card')['closed'])
            self.assertFalse(self.last(71, 'setup_card')['show'])
            self.sent.clear()
            await self.host.on_ready(a, None)
            self.assertTrue(self.last(71, 'setup_card')['closed'])
            self.assertFalse(self.last(71, 'setup_card')['show'])
        asyncio.run(asyncio.wait_for(go(), 4))
