"""P396: actual WebCrypto Ed25519 signatures from shipped wire.js → PeerService.answer.
No Python reimplementation of the phone's signed text; default unittest discovery runs this matrix.
"""
import _hermetic  # noqa: F401
import json
import subprocess
from test_p71_peer_service import Base, NODE, ROOT, freq, peer_keys, pmsg
from agentj import peer_service, wire


class Roundtrip(Base):
    def signed(self, card, ok, group=None, ctx=None):
        script = """
const w = await import(process.argv[2]);
const [ch, dev, a, ok, group, ctx] = JSON.parse(process.argv[1]);
const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
const msg = await w.friendAnswerMessage(ch, dev, a.id, ok ? 'allow' : 'deny', a.tool, a.summary, group, ctx);
console.log(JSON.stringify({pk:w.b64u(new Uint8Array(await crypto.subtle.exportKey('raw',k.publicKey))),
 sig:w.b64u(new Uint8Array(await crypto.subtle.sign('Ed25519',k.privateKey,msg)))}));
"""
        # Use an existing paired id, replace only the fixture's signing key.
        args = [self.host.channel, self.did, card, ok, group, ctx]
        r = subprocess.run([NODE, '--input-type=module', '-e', script, json.dumps(args), (ROOT / 'web/public/proto/wire.js').as_uri()], cwd=ROOT,
                           capture_output=True, text=True, check=True)
        out = json.loads(r.stdout)
        self.host.st.sign_key = lambda did: wire.unb64u(out['pk']) if did == self.did else None
        ans = {'t': 'answer', 'id': card['id'], 'ok': ok, 'sig': out['sig']}
        if group is not None: ans['group'] = group
        if ctx is not None: ans['ctx'] = ctx
        return ans

    def question(self):
        k = self.friend()
        self.svc._question(k.id, pmsg('报价多少？😀\n第二行'), '先问主人。', '需确认')
        return self.svc.ask_msgs()[-1]

    async def test_peer_question_allow(self):
        a = self.question()
        self.svc.answer(self.did, self.signed(a, True))
        self.assertNotIn(a['id'], self.svc.asks)

    async def test_peer_question_deny(self):
        a = self.question()
        self.svc.answer(self.did, self.signed(a, False))
        self.assertNotIn(a['id'], self.svc.asks)

    async def test_peer_question_group(self):
        a = self.question()
        self.svc.answer(self.did, self.signed(a, True, 'colleague'))
        self.assertNotIn(a['id'], self.svc.asks)
        self.assertEqual(self.store.group_of(a['pq']['friend'])['id'], 'colleague')

    async def test_friend_request_context_and_group(self):
        k = peer_keys()
        self.svc.on_request(k.id, k.x_pub, k.ed_pub, freq(k), k.mbox)
        a = self.svc.ask_msgs()[-1]
        self.svc.answer(self.did, self.signed(a, True, 'friend', '老客户😀\n先确认报价'))
        self.assertNotIn(a['id'], self.svc.asks)
        self.assertIn('老客户', self.store.context_text(k.id))

    async def test_restore_allow_and_deny_exact_summary(self):
        for ok in (True, False):
            with self.subTest(ok=ok):
                a = self.question()
                self.svc.asks.clear()
                self.host.lang = 'en'   # restore must keep the original signed text, even after language changes
                self.svc.restore_asks()
                restored = next(x for x in self.svc.ask_msgs() if x['id'] == a['id'])
                self.assertEqual(restored['summary'], a['summary'])
                self.svc.answer(self.did, self.signed(restored, ok))
                self.assertNotIn(a['id'], self.svc.asks)

    async def test_stale_summary_deny_refused_with_fresh_card(self):
        a = self.question()
        refused = []
        ans = self.signed({**a, 'summary': a['summary'] + '旧卡'}, False)
        self.svc.answer(self.did, ans, refuse=refused.append)
        self.assertIn(a['id'], self.svc.asks)
        self.assertEqual(refused[0]['t'], 'answer_refused')
        self.assertEqual(refused[0]['ask']['summary'], a['summary'])
        self.svc.answer(self.did, self.signed(refused[0]['ask'], False))
        self.assertNotIn(a['id'], self.svc.asks)

    async def test_timeout_no_reply_notice(self):
        a = self.question()
        self.assertLessEqual(a['ttl'], 180)
        self.clock.t += peer_service.PQ_TTL + 1
        self.svc.expire_asks()
        self.assertNotIn(a['id'], self.svc.asks)
        self.assertEqual(self.host.of('ask_done')[-1]['result'], 'timeout')
        self.assertTrue(any('不回复' in s for s in self.host.notices))

    async def test_legacy_24h_restore_expires_once_without_reply(self):
        a = self.question()
        with self.store._edit('questions.json', {}) as rows:
            rows[a['id']].pop('created')
            rows[a['id']]['exp'] = self.clock() - 8 * 3600 + 86400
        self.svc.asks.clear()
        self.svc.restore_asks()
        self.assertNotIn(a['id'], self.svc.asks)
        self.assertEqual(len(self.host.notices), 1)
        self.svc.restore_asks()
        self.assertEqual(len(self.host.notices), 1)
        self.assertEqual(self.net.of('pmsg'), [])

    async def test_host_routes_refusal_only_to_originating_session(self):
        from agentj.serve import Host
        from types import SimpleNamespace
        a = self.question()
        ans = self.signed({**a, 'summary': 'stale'}, False)
        sent = []
        async def send(session, receipt): sent.append((session, receipt))
        origin = SimpleNamespace(device=self.did)
        host = SimpleNamespace(peers=self.svc, send_app=send)
        await Host._answer(host, origin, ans)
        self.assertEqual(len(sent), 1)
        self.assertIs(sent[0][0], origin)
        self.assertEqual(sent[0][1]['why'], 'bad_signature')
        self.assertIn(a['id'], self.svc.asks)

    async def test_wrong_group_and_tamper_cannot_grant(self):
        a = self.question()
        ans = self.signed(a, True, 'colleague')
        ans['ok'] = False
        self.svc.answer(self.did, ans)
        self.assertIn(a['id'], self.svc.asks)
        ans['ok'], ans['group'] = True, 'friend'
        self.svc.answer(self.did, ans)
        self.assertIn(a['id'], self.svc.asks)
        self.assertEqual(self.store.group_of(a['pq']['friend'])['id'], 'default')

    async def test_real_service_restart_restored_card_webcrypto_deny(self):
        from test_p71_peer_service import FakeSessions, FakeNet
        a = self.question()
        await self.svc.stop()
        self.host.lang = 'en'
        self.svc = peer_service.PeerService(self.host, sessions=FakeSessions(), net_factory=FakeNet, clock=self.clock)
        await self.svc.start()
        self.addAsyncCleanup(self.svc.stop)
        restored = next(x for x in self.svc.ask_msgs() if x['id'] == a['id'])
        self.assertEqual(a['summary'], restored['summary'])
        self.svc.answer(self.did, self.signed(restored, False))
        self.assertNotIn(a['id'], self.svc.asks)

    async def test_friend_request_plain_allow_and_deny(self):
        for ok in (True, False):
            with self.subTest(ok=ok):
                k = peer_keys()
                self.svc.on_request(k.id, k.x_pub, k.ed_pub, freq(k), k.mbox)
                a = self.svc.ask_msgs()[-1]
                self.svc.answer(self.did, self.signed(a, ok))
                self.assertNotIn(a['id'], self.svc.asks)
                self.assertEqual(self.store.get(k.id) is not None, ok)
