"""P424 (#152): every friend-card button → the shipped wire.js `friendAnswer` (real WebCrypto Ed25519; it builds BOTH the signed
bytes and the sent `answer`) → PeerService.answer. Root cause proven on the released 0.24.0a1 sources: the peer_question button
「以后这位都让分身自己聊」 sent group:colleague but signed without that line (reports/qa/p424/d/repro-0.24.0a1.log).
Here: the answer object is never rebuilt in Python, refusals log which line differs (no text), and nothing is loosened."""
import _hermetic  # noqa: F401
import json
import subprocess
from test_p71_peer_service import Base, FakeNet, FakeSessions, NODE, ROOT, freq, peer_keys, pmsg
from agentj import approvals, peer_service, wire

JS = """
const w = await import(process.argv[2]);
const [ch, dev, card, ok, opts, mode] = JSON.parse(process.argv[1]);
const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
const sign = async (m) => new Uint8Array(await crypto.subtle.sign('Ed25519', k.privateKey, m));
let ans, err = null;
try {
  if (mode === 'legacy') {          // the 0.24.0a1 signer: approve text only, group sent anyway
    const msg = await w.approveMessage(ch, dev, card.id, ok ? 'allow' : 'deny', card.tool, card.summary);
    ans = { t: 'answer', id: card.id, ok, sig: w.b64u(await sign(msg)), group: opts.group };
  } else if (mode === 'hidden_group') {   // signs a group line but does not send the field
    const msg = await w.friendAnswerMessage(ch, dev, card.id, 'allow', card.tool, card.summary, opts.group);
    ans = { t: 'answer', id: card.id, ok: true, sig: w.b64u(await sign(msg)) };
  } else ans = await w.friendAnswer(ch, dev, card, ok, opts, sign);
} catch (e) { err = String(e.message || e); }
console.log(JSON.stringify({ pk: w.b64u(new Uint8Array(await crypto.subtle.exportKey('raw', k.publicKey))), ans, err }));
"""
WIRE = (ROOT / 'web/public/proto/wire.js').as_uri()
UNICODE = 'Café ＡＢＣ１２３ 👩‍💻 中文\n第二行  \r\n尾随空格   '


class FriendAnswer(Base):
    def phone(self, card, ok, opts=None, mode='real'):
        r = subprocess.run([NODE, '--input-type=module', '-e', JS,
                            json.dumps([self.host.channel, self.did, card, ok, opts or {}, mode]), WIRE],
                           cwd=ROOT, capture_output=True, text=True, check=True)
        out = json.loads(r.stdout)
        self.host.st.sign_key = lambda did: wire.unb64u(out['pk']) if did == self.did else None
        return out

    def press(self, card, ok, opts=None, mode='real'):
        """→ (decided?, the answer_refused fields logged, receipts)."""
        out = self.phone(card, ok, opts, mode)
        self.assertIsNone(out['err'])
        ev, refused, orig = [], [], self.svc.log
        self.svc.log = lambda e, **k: (ev.append((e, k)), orig(e, **k))
        try:
            self.svc.answer(self.did, out['ans'], refuse=refused.append)
        finally:
            self.svc.log = orig
        bad = [k for e, k in ev if e == 'answer_refused']
        return card['id'] not in self.svc.asks, (bad[0] if bad else None), refused

    def question(self, text='报价多少？😀\n第二行', draft='先问主人。', **kw):
        k = self.friend()
        self.svc._question(k.id, pmsg(text), draft, '超出自动回复范围，由你决定', **kw)
        return self.svc.ask_msgs()[-1]

    def request(self):
        k = peer_keys()
        self.svc.on_request(k.id, k.x_pub, k.ed_pub, freq(k), k.mbox)
        return k, self.svc.ask_msgs()[-1]

    # ---------------------------------------------------------------- every button, through the one builder
    async def test_peer_question_every_button(self):
        for label, ok, opts in [('frQSend', True, {}), ('frQSkip', False, {}), ('frQTell', False, {}),
                                ('frQAuto', True, {'group': 'colleague'})]:
            with self.subTest(label):
                a = self.question()
                decided, bad, _ = self.press(a, ok, opts)
                self.assertTrue(decided, bad)
        self.assertEqual(self.store.group_of(a['pq']['friend'])['id'], 'colleague')

    async def test_same_owner_main_button(self):
        a = self.question()
        self.svc.same_owner.active = lambda fid: True
        decided, bad, _ = self.press(a, True, {'group': 'main'})
        self.assertTrue(decided, bad)

    async def test_handover_card_allow_and_deny(self):
        for ok in (True, False):
            with self.subTest(ok=ok):
                a = self.question(handover=True)
                self.assertTrue(a['pq'].get('handover'))
                self.assertTrue(self.press(a, ok)[0])

    async def test_unicode_and_truncated_summaries(self):
        for text, draft in [(UNICODE, UNICODE), ('长' * (peer_service.SHOW_MAX + 50), '稿' * (peer_service.DRAFT_MAX + 9))]:
            with self.subTest(n=len(text)):
                a = self.question(text, draft)
                self.assertNotIn('\r', a['summary'])
                decided, bad, _ = self.press(a, True, {'group': 'colleague'})
                self.assertTrue(decided, bad)

    async def test_friend_request_buttons(self):
        for label, ok, opts in [('default group', True, {'group': 'friend'}), ('ctx', True, {'group': 'friend', 'ctx': '老客户😀\n先确认报价'}),
                                ('no group', True, {}), ('deny', False, {})]:
            with self.subTest(label):
                k, a = self.request()
                decided, bad, _ = self.press(a, ok, opts)
                self.assertTrue(decided, bad)
                self.assertEqual(self.store.get(k.id) is not None, ok)

    async def test_restart_restored_question_colleague(self):
        a = self.question(UNICODE)
        await self.svc.stop()
        self.host.lang = 'en'
        self.svc = peer_service.PeerService(self.host, sessions=FakeSessions(), net_factory=FakeNet, clock=self.clock)
        await self.svc.start()
        self.addAsyncCleanup(self.svc.stop)
        restored = next(x for x in self.svc.ask_msgs() if x['id'] == a['id'])
        decided, bad, _ = self.press(restored, True, {'group': 'colleague'})
        self.assertTrue(decided, bad)

    # ---------------------------------------------------------------- the builder cannot send an unsigned field
    async def test_builder_refuses_unsigned_fields(self):
        a = self.question()
        for ok, opts in [(False, {'group': 'colleague'}), (True, {'ctx': '设定'}), (True, {'group': 'friend'})]:
            with self.subTest(ok=ok, opts=opts):
                out = self.phone(a, ok, opts)
                self.assertIsNotNone(out['err'])
                self.assertIsNone(out.get('ans'))
        _, fr = self.request()
        self.assertIsNotNone(self.phone(fr, False, {'ctx': '设定'})['err'])
        ans = self.phone(a, True, {'group': 'colleague'})['ans']
        self.assertEqual(ans['group'], 'colleague')
        self.assertEqual(ans['dg'], approvals.shown_digest(a['tool'], a['summary'])[:16])

    # ---------------------------------------------------------------- refusals: still refused, now with a diagnosis
    async def test_legacy_0240_signer_refused_with_hint(self):
        a = self.question()
        decided, bad, refused = self.press(a, True, {'group': 'colleague'}, mode='legacy')
        self.assertFalse(decided)
        self.assertEqual((bad['reason'], bad['why'], bad['status']), ('bad_signature', 'unsigned_group', 'dg_none'))
        self.assertEqual(refused[0]['ask']['id'], a['id'])
        self.assertEqual(self.store.group_of(a['pq']['friend'])['id'], 'default')
        decided, bad, _ = self.press(refused[0]['ask'], True, {'group': 'colleague'})
        self.assertTrue(decided, bad)

    async def test_stale_summary_logs_digest_differs(self):
        a = self.question()
        decided, bad, _ = self.press({**a, 'summary': a['summary'] + ' '}, False)
        self.assertFalse(decided)
        self.assertEqual((bad['why'], bad['status']), ('none', 'dg_differs'))

    async def test_tampered_decision_and_hidden_group(self):
        a = self.question()
        out = self.phone(a, False)
        out['ans']['ok'] = True
        ev, orig = [], self.svc.log
        self.svc.log = lambda e, **k: (ev.append((e, k)), orig(e, **k))
        self.svc.answer(self.did, out['ans'])
        self.svc.log = orig
        self.assertIn(a['id'], self.svc.asks)
        self.assertEqual([(k['why'], k['status']) for e, k in ev if e == 'answer_refused'], [('decision', 'dg_same')])
        decided, bad, _ = self.press(a, True, {'group': 'colleague'}, mode='hidden_group')
        self.assertFalse(decided)
        self.assertEqual((bad['why'], bad['status']), ('group_not_sent', 'dg_none'))
        self.assertEqual(self.store.group_of(a['pq']['friend'])['id'], 'default')

    async def test_refusal_log_line_has_no_text(self):
        a = self.question('机密报价 98765', '草稿 43210')
        self.press({**a, 'summary': 'stale'}, True, {'group': 'colleague'})
        self.host.st.log('answer_refused', id=a['id'], device=self.did, reason='bad_signature', why='none', status='dg_differs', text='leak')
        from agentj import diskq
        await diskq.aflush(10)
        log = self.host.st.log_path.read_text() if self.host.st.log_path.exists() else ''
        rows = [json.loads(x) for x in log.splitlines() if '"answer_refused"' in x]
        self.assertEqual([(r['why'], r['status']) for r in rows], [('none', 'dg_differs')] * 2)
        for row in rows:
            self.assertLessEqual(set(row), {'ts', 'ev', 'id', 'device', 'reason', 'why', 'status'})
        self.assertNotIn('98765', log)
        self.assertNotIn('43210', log)
        self.assertNotIn('leak', log)
