"""0.30.72 DECISION-0300 item 1 (chairman 2026-11-23, option A): a heartbeat turn is back on the Face ID list.

Chairman 11-11: 「指纹卡只管主线程（花钱 / 转账 / 不可逆删除核心数据文档）」 — the heartbeat was the main thread then
(0.30.10). f7aee0e3aa (0.30.11) dropped it from uv_list.FINGERPRINT_SOURCES together with the background automations the
19:14 voice note was about; the voice note never named the heartbeat. Restored here.

Everything goes through the real permission connection (Host._perm_ask → Host.ask → list_approval) with the real source
typing (a HeartbeatSend as the running turn; approval_offlist.source is NOT patched) and a paired phone that holds a real
passkey (test_l1.Phone(with_passkey=True)). Synthetic state, HOME and device only.
"""
import _hermetic  # noqa: F401,I001
import asyncio
import json
import unittest
from unittest import mock

from agentj import approval_offlist, approvals, provenance, trust_taint, uv_list

import test_heartbeat_readonly_cards_0306 as hb

CHARGE = {'command': 'stripe charges create --amount 500 --currency usd'}
TRANSFER = {'command': 'solana transfer synthetic-recipient 1'}
FORCE_PUSH = {'command': 'git push --force origin main'}
WIPE_HOME = {'command': 'rm -rf ~/'}
LISTED = (CHARGE, TRANSFER, FORCE_PUSH, WIPE_HOME)


class Rig(hb._Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.h.ask_ttl = 3
        self.addCleanup(hb_remove, str(self.st.root))

    async def asyncTearDown(self):
        for _ in range(8):                                     # let queued off-loop audit writes finish
            await asyncio.to_thread(lambda: None)
        await asyncio.sleep(.05)

    def cards(self):
        return [obj for _, obj in self.sent if isinstance(obj, dict) and obj.get('t') == 'ask']

    def rows(self, ev):
        path = self.st.root / 'host.log'
        out = []
        for line in (path.read_text().splitlines() if path.exists() else []):
            row = json.loads(line)
            if row.get('ev') == ev:
                out.append(row)
        return out

    def beat(self, read_external=False):
        """A heartbeat turn started exactly as serve starts it; optionally one that has read outside content."""
        send = self.heartbeat()
        self.h.agent.cur_send = send
        self.h.agent_turn_start('synthetic', send)
        self.assertEqual('host_heartbeat', approval_offlist.source(self.h)['type'])
        if read_external:
            trust_taint.observe_claude(self.h.agent, {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash',
                                                      'tool_input': {'command': 'agentj mail check'}, 'tool_response': {}})
            self.assertIsNotNone(trust_taint.tainted(self.h.agent))
        return send

    async def perm(self, inp, reply=None, tool='Bash'):
        """One request on the main permission connection. `reply(card)` builds the phone's answer frame (None: nobody
        answers). → (cards shown for it, the answer the harness got)."""
        w = object()
        self.h.perm_w = w
        answers, n = [], len(self.cards())

        async def fake_send(writer, obj):
            answers.append(obj)
        with mock.patch.object(self.h, '_perm_send', side_effect=fake_send):
            task = asyncio.create_task(self.h._perm_ask(w, {'id': 1, 'tool': tool, 'input': inp}, {}))
            for _ in range(600):
                if len(self.cards()) > n or task.done():
                    break
                await asyncio.sleep(.005)
            shown = self.cards()[n:]
            if shown and reply is not None:
                await self.h._app(self.s, reply(shown[-1]))
            await asyncio.wait_for(task, 20)
        return shown, answers[-1]

    def face_id(self, card):
        return self.ph.answer(card, True)                     # signature + a real passkey assertion

    def tap_only(self, card):
        frame = self.ph.answer(card, True)
        frame.pop('fa')                                        # the paired signature alone
        return frame


def hb_remove(path):
    import os
    import shutil
    import time
    for _ in range(40):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(.05)


class HeartbeatNeedsFaceId(Rig):
    def test_heartbeat_is_on_the_list_and_background_work_is_not(self):
        self.assertIn('host_heartbeat', uv_list.FINGERPRINT_SOURCES)
        # the 19:14 voice note stands: already-running automations are not re-approved at run time
        for kind in ('scheduled_task', 'host_system', 'settings', 'automation', 'avatar'):
            self.assertNotIn(kind, uv_list.FINGERPRINT_SOURCES)

    async def test_listed_action_shows_a_face_id_card_and_runs_only_after_face_id(self):
        for external in (False, True):
            for inp in LISTED:
                with self.subTest(read_external=external, inp=inp):
                    self.beat(external)
                    shown, answer = await self.perm(inp, self.face_id)
                    self.assertEqual(1, len(shown))
                    self.assertTrue(shown[0].get('passkey_required'))
                    self.assertEqual('allow', answer['behavior'], answer)
                    self.assertEqual(('allow', 'device'), tuple(approvals.read_log(self.st)[-1][k] for k in ('decision', 'reason')))
                    self.h.agent_turn_end()
        asked = self.rows('ask')
        self.assertEqual(2 * len(LISTED), len(asked))
        self.assertEqual({('host_heartbeat', 'host', 'uv')}, {(r['source_type'], r['trust'], r['tier']) for r in asked})
        self.assertFalse([r for r in self.rows('ask_done') if r.get('reason') == 'fingerprint_headless'])

    async def test_signed_tap_without_face_id_does_not_run_it(self):
        for external in (False, True):
            with self.subTest(read_external=external):
                self.beat(external)
                self.h.ask_ttl = .6
                shown, answer = await self.perm(CHARGE, self.tap_only)
                self.assertEqual(1, len(shown))
                self.assertNotEqual('allow', answer['behavior'], answer)
                self.h.agent_turn_end()

    async def test_another_passkey_does_not_run_it(self):
        from test_p57_passkey import Authenticator
        for same_id in (False, True):
            with self.subTest(same_credential_id=same_id):
                self.beat()
                self.h.ask_ttl = .6
                stranger = Authenticator()
                if same_id:
                    stranger.id = self.ph.auth.id                          # names this remote's credential, signs with another key

                def reply(card):
                    frame, real = self.ph.answer(card, True), self.ph.auth
                    self.ph.auth = stranger
                    try:
                        frame['fa'] = self.ph.answer(card, True)['fa']     # the right challenge, the wrong passkey
                    finally:
                        self.ph.auth = real
                    return frame
                shown, answer = await self.perm(CHARGE, reply)
                self.assertEqual(1, len(shown))
                self.assertNotEqual('allow', answer['behavior'], answer)
                self.h.agent_turn_end()

    async def test_face_id_given_for_another_card_does_not_run_it(self):
        self.beat()
        saved = {}

        def first(card):
            saved['fa'] = self.ph.answer(card, True)['fa']                 # a real Face ID, for THIS card …
            return self.ph.answer(card, False)                             # … which the owner then refuses
        shown, answer = await self.perm(TRANSFER, first)
        self.assertEqual((1, 'deny'), (len(shown), answer['behavior']))
        self.h.ask_ttl = .6

        def second(card):
            frame = self.ph.answer(card, True)
            frame['fa'] = saved['fa']                                      # replayed on the next card
            return frame
        shown, answer = await self.perm(CHARGE, second)
        self.assertEqual(1, len(shown))
        self.assertNotEqual('allow', answer['behavior'], answer)
        # control: the same card shape with its own Face ID does run
        self.h.ask_ttl = 3
        shown, answer = await self.perm(CHARGE, self.face_id)
        self.assertEqual((1, 'allow'), (len(shown), answer['behavior']))

    async def test_refusal_on_the_phone_refuses(self):
        self.beat()
        shown, answer = await self.perm(CHARGE, lambda card: self.ph.answer(card, False))
        self.assertEqual((1, 'deny'), (len(shown), answer['behavior']))

    async def test_paired_phone_not_connected_waits_then_refuses(self):
        for external in (False, True):
            with self.subTest(read_external=external):
                self.beat(external)
                held, self.h.sessions = dict(self.h.sessions), {}
                self.h.ask_ttl = .4
                try:
                    shown, answer = await self.perm(CHARGE)
                finally:
                    self.h.sessions = held
                self.assertEqual([], shown)                    # nobody connected: nothing was delivered
                self.assertEqual('deny', answer['behavior'], answer)
                self.assertEqual(('deny', 'timeout'), tuple(approvals.read_log(self.st)[-1][k] for k in ('decision', 'reason')))
                self.assertFalse([a for a in self.h.asks.values() if getattr(a, 'parked', False)])
                self.h.agent_turn_end()

    async def test_no_paired_phone_at_all_refuses_at_once(self):
        self.beat()
        self.h.sessions.clear()
        self.assertTrue(self.st.remove_device(self.ph.did))
        self.assertEqual([], self.h._approvers())
        for inp in LISTED:
            with self.subTest(inp=inp):
                shown, answer = await self.perm(inp)
                self.assertEqual([], shown)
                self.assertEqual('deny', answer['behavior'], answer)
                self.assertEqual(('deny', 'no_device'), tuple(approvals.read_log(self.st)[-1][k] for k in ('decision', 'reason')))

    async def test_ordinary_heartbeat_work_still_has_no_card(self):
        self.beat()
        for inp in ({'command': 'git status'}, {'command': 'agentj card todo due'}, {'command': 'git push origin main'},
                    {'command': 'stripe charges list'}):
            with self.subTest(inp=inp):
                shown, answer = await self.perm(inp)
                self.assertEqual(([], 'allow'), (shown, answer['behavior']))

    async def test_scheduled_task_keeps_running_without_a_card(self):
        self.h.agent.cur_send = None
        with provenance.task_run(owner=True):
            self.assertEqual('scheduled_task', approval_offlist.source(self.h, background='task')['type'])
            answer = await self.h.ask('Bash', dict(CHARGE), _harness_permission=True, background='task')
        self.assertEqual('allow', answer['behavior'], answer)
        self.assertEqual([], self.cards())
        self.assertTrue([r for r in self.rows('ask_done') if r.get('reason') == 'fingerprint_headless'])


if __name__ == '__main__':
    unittest.main()
