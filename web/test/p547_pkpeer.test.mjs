// P547 (#315 / #317, PASSKEY-SEAT-MODEL §2): js/pkpeer.js — this remote's own Face ID key approved by another remote. The
// state machine runs here with a fake session and a fake view (no DOM): requester wait → done → peer offer → 「保存」 calls
// register(…, peer:true) in the same tick; approver card → approve() synchronously in the tap → pk_peer_answer; refusals,
// expiry, cancel, close. The browser leg is p547.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document ??= { getElementById: () => null, addEventListener() {}, body: null };
const P = await import('../public/js/pkpeer.js');
const { keyPlatform } = await import('../public/js/phone-env.js');
const b64u = (u) => Buffer.from(u).toString('base64url');
const ID = b64u(Buffer.alloc(16, 7));                      // 22 chars, like the host's request id
const NONCE = b64u(Buffer.alloc(32, 1));
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

function harness({ res, now = 1_800_000_000_000 } = {}) {
  P._reset();
  const h = { sent: [], shown: [], hidden: 0, asked: [], legacy: 0, registered: [], approved: [], order: [] };
  P.configure({
    view: { show: (s) => h.shown.push(s), hide: () => { h.hidden++; } },
    ask: async (o, want, ms) => { h.asked.push({ o, want, ms }); return typeof res === 'function' ? res(o) : res; },
    send: async (o) => { h.sent.push(o); },
    supported: async () => true,
    canAssert: () => true,
    regOpts: async () => ({ relayIndex: 0, channel: 'C'.repeat(22), hostPub: new Uint8Array(32), name: 'Agent J', displaySuffix: ' · x' }),
    register: (nonce, opts) => { h.order.push('register'); h.registered.push({ nonce, opts }); return Promise.resolve({ t: 'pk_reg', id: 'new' }); },
    approve: (ch, cred) => { h.order.push('approve'); h.approved.push({ ch, cred }); return Promise.resolve({ id: cred, cd: 'c', ad: 'a', sig: 's' }); },
    legacy: () => { h.legacy++; },
    now: () => now,
    ua: () => 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/605', touch: () => 5,
  });
  return h;
}
const last = (h) => h.shown[h.shown.length - 1];

test('requester: pk_peer_req → waiting sheet with the 6-digit code and the online count; 「取消」 = pk_peer_cancel', async () => {
  const h = harness({ res: { t: 'pk_peer_res', ok: true, mode: 'peer', id: ID, code: '042917', exp: 1_800_000_300, online: 0 } });
  assert.equal(await P.startPeerSetup(), 'peer');
  assert.deepEqual(h.asked[0].o, { t: 'pk_peer_req' });
  assert.equal(h.asked[0].want, 'pk_peer_res');
  assert.deepEqual(last(h), { mode: 'wait', code: '042917', online: 0, host: false, where: 'ios' });
  P.cancelWait();
  assert.deepEqual(h.sent, [{ t: 'pk_peer_cancel', id: ID }]);
  assert.equal(P._state().req, null);
});

test('requester: the owner\'s first device goes direct (no sheet; the host\'s pk_offer follows) and gets 「再加一把」 after saving', async () => {
  const h = harness({ res: { t: 'pk_peer_res', ok: true, mode: 'direct' } });
  assert.equal(await P.startPeerSetup(), 'direct');
  assert.equal(h.shown.length, 0);
  assert.equal(P.saved(true), true, 'first key on the account → the hint');
  assert.equal(P.saved(true), false, 'once');
});

test('requester: approval → peer offer → 「保存」 calls register(peer:true) synchronously, never approve() on the old key', async () => {
  const h = harness({ res: { t: 'pk_peer_res', ok: true, mode: 'peer', id: ID, code: '123456', exp: 1_800_000_300, online: 2 } });
  await P.startPeerSetup();
  assert.equal(last(h).online, 2);
  P.handle({ t: 'pk_peer_done', id: ID, ok: true, by: 'iPhone Safari' });
  assert.equal(last(h).mode, 'wait', 'the 「保存」 sheet waits for the offer itself');
  assert.equal(await P.onOffer({ t: 'pk_offer', n: NONCE, peer: ID }), true);
  assert.deepEqual(last(h), { mode: 'approved', by: 'iPhone Safari' });
  const p = P.saveTap();
  assert.deepEqual(h.order, ['register'], 'create() starts in the tap — no await, no approve()');
  assert.equal(h.registered[0].opts.peer, true);
  assert.equal(h.registered[0].opts.replace, null);
  assert.equal(h.registered[0].nonce.length, 32);
  await p;
  assert.deepEqual(h.sent, [{ t: 'pk_reg', id: 'new' }]);
  assert.equal(P.saved(true), false, 'not the first key: no hint');
});

test('requester: an offer for another request, a short nonce, or a reconnect within the TTL', async () => {
  const h = harness({ res: { t: 'pk_peer_res', ok: true, mode: 'peer', id: ID, code: '123456', exp: 1_800_000_300, online: 1 } });
  await P.startPeerSetup();
  assert.equal(await P.onOffer({ t: 'pk_offer', n: NONCE, peer: b64u(Buffer.alloc(16, 9)) }), false);
  assert.equal(await P.onOffer({ t: 'pk_offer', n: b64u(Buffer.alloc(16)), peer: ID }), false);
  P._reset();
  harness();
  P.handle({ t: 'pk_peer_done', id: ID, ok: true, by: 'Mac Chrome' });          // the page reloaded: no request in memory
  assert.equal(await P.onOffer({ t: 'pk_offer', n: NONCE, peer: ID }), true);
  assert.equal(P._state().shown, 'approved');
});

test('requester: refusals and the host\'s own reasons are said in plain words; an older host gets the old offer', async () => {
  for (const [res, want] of [[{ ok: false, why: 'busy' }, 'busy'], [{ ok: false, why: 'rate' }, 'rate'], [{ ok: false, why: 'no_approver' }, 'no_approver'], [{ ok: false, why: '???' }, 'fail']]) {
    const h = harness({ res: { t: 'pk_peer_res', ...res } });
    assert.equal(await P.startPeerSetup(), 'fail');
    assert.deepEqual(last(h), { mode: 'result', why: want });
  }
  for (const why of ['denied', 'expired', 'cancelled']) {
    const h = harness({ res: { t: 'pk_peer_res', ok: true, mode: 'peer', id: ID, code: '000001', exp: 1_800_000_300, online: 1 } });
    await P.startPeerSetup();
    P.handle({ t: 'pk_peer_done', id: ID, ok: false, why });
    assert.deepEqual(last(h), { mode: 'result', why });
  }
  const h = harness({ res: { t: 'timeout' } });
  assert.equal(await P.startPeerSetup(), 'legacy');
  assert.equal(h.legacy, 1);
});

test('approver: card with name, time, code; 「批准（Face ID）」 runs approve() in the tap and sends pk_peer_answer with fa', async () => {
  const h = harness();
  const ask = { t: 'pk_peer_ask', id: ID, device: 'D', name: 'Mac Chrome‮', at: 1_800_000_000, exp: 1_800_000_300, code: '654321', fa: 'Y3JlZA', fa_ch: b64u(Buffer.alloc(32, 3)) };
  assert.equal(P.handle(ask), true);
  assert.deepEqual(last(h), { mode: 'ask', id: ID, name: 'Mac Chrome', at: 1_800_000_000, code: '654321', can: true });
  const p = P.approveTap();
  assert.deepEqual(h.order, ['approve'], 'get() starts inside the tap');
  assert.equal(h.approved[0].cred, 'Y3JlZA');
  assert.equal(h.approved[0].ch.length, 32);
  await p;
  assert.deepEqual(h.sent, [{ t: 'pk_peer_answer', id: ID, ok: true, fa: { id: 'Y3JlZA', cd: 'c', ad: 'a', sig: 's' } }]);
  P.handle({ t: 'pk_peer_answer_res', id: ID, ok: true });
  assert.deepEqual(P._state().asks, []);
});

test('approver: 「拒绝」 = ok:false without Face ID; pk_peer_close puts the card away; a refused assertion brings it back', async () => {
  let h = harness();
  const ask = { t: 'pk_peer_ask', id: ID, device: 'D', name: 'OPPO', at: 1, exp: 2, code: '111111', fa: 'Y3JlZA', fa_ch: 'AAAA' };
  P.handle(ask);
  await P.denyTap();
  assert.deepEqual(h.sent, [{ t: 'pk_peer_answer', id: ID, ok: false }]);
  assert.equal(h.approved.length, 0);
  h = harness();
  P.handle(ask);
  P.handle({ t: 'pk_peer_close', id: ID });
  assert.equal(P._state().shown, null);
  assert.deepEqual(P._state().asks, []);
  h = harness();
  P.handle(ask);
  await P.approveTap();
  P.handle({ t: 'pk_peer_answer_res', id: ID, ok: false, why: 'passkey_failed' });
  assert.equal(P._state().shown, 'ask', 'the card is back for another try');
  assert.equal(P.handle({ t: 'pk_peer_ask', id: 'x', code: '12', fa: 'a', fa_ch: 'b' }), true);
  assert.deepEqual(P._state().asks, [ID], 'a malformed ask is ignored');
});

test('platform wording: iPhone iCloud Keychain, Android Google Password Manager, Mac Touch ID, Windows Hello', () => {
  assert.equal(keyPlatform('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)', 5), 'ios');
  assert.equal(keyPlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari', 5), 'ios', 'an iPad asks for the desktop site');
  assert.equal(keyPlatform('Mozilla/5.0 (Linux; Android 15; PJZ110)', 5), 'android');
  assert.equal(keyPlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/130', 0), 'mac');
  assert.equal(keyPlatform('Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 0), 'windows');
  assert.equal(keyPlatform('Mozilla/5.0 (X11; Linux x86_64)', 0), 'other');
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  assert.match(zh['pkp.where.ios'], /iCloud 钥匙串/); assert.match(zh['pkp.where.android'], /Google 密码管理器/);
  assert.match(zh['pkp.where.mac'], /Touch ID/); assert.match(zh['pkp.where.windows'], /Windows Hello/);
  assert.match(en['pkp.where.ios'], /iCloud Keychain/); assert.match(en['pkp.where.android'], /Google Password Manager/);
});

test('wiring: Settings / cards ask pk_peer_req (not pk_offer_req) first; a peer offer never re-asks or replaces', () => {
  const app = read('../public/app.js');
  assert.match(app, /async function setupFaceId\(\) \{[\s\S]{0,400}pkpeer\.startPeerSetup\(\)/);
  assert.match(app, /if \(m\.peer\) \{ pkForce = false; await pkpeer\.onOffer\(m\); return; \}/);
  assert.match(app, /case 'pk_peer_done': case 'pk_peer_ask': case 'pk_peer_close': case 'pk_peer_answer_res': pkpeer\.handle\(m\)/);
  const js = read('../public/js/pkpeer.js');
  assert.match(js, /export function saveTap\(\) \{[\s\S]{0,200}const p = H\.register\(o\.nonce, \{ \.\.\.o\.opts, replace: null, peer: true \}\);/);
  assert.doesNotMatch(js.slice(js.indexOf('export function saveTap'), js.indexOf('const p = H.register')), /await/);
  assert.doesNotMatch(js, /innerHTML|insertAdjacentHTML|localStorage|sessionStorage/);
  for (const k of ['pkp.setupHere', 'pkp.waitTitle', 'pkp.approvedTitle', 'pkp.askTitle', 'pkp.approve', 'pkp.deny', 'pkp.addAnother', 'pkp.eachOwn']) {
    assert.ok(JSON.parse(read('../i18n/web.zh.json'))[k] && JSON.parse(read('../i18n/web.en.json'))[k], k);
  }
  assert.equal(JSON.parse(read('../i18n/web.zh.json'))['pkp.setupHere'], '在本机设置 Face ID（需另一台批准）');
  assert.equal(JSON.parse(read('../i18n/web.zh.json'))['pkp.approvedTitle'], '已批准 · 点「保存」在本机设置');
});

test('review: pk_reg_res {ok:false, why:"peer_required"} starts the peer flow by itself — no failure toast; other answers are app.js\'s', async () => {
  const h = harness({ res: { t: 'pk_peer_res', ok: true, mode: 'peer', id: ID, code: '123456', exp: 1_800_000_300, online: 1 } });
  assert.equal(P.onRegRes({ t: 'pk_reg_res', ok: true }), false);
  assert.equal(P.onRegRes({ t: 'pk_reg_res', ok: false, why: 'bad_sig' }), false);
  assert.equal(P.onRegRes(null), false);
  assert.equal(h.asked.length, 0);
  assert.equal(P.onRegRes({ t: 'pk_reg_res', ok: false, why: 'peer_required' }), true);
  for (let i = 0; i < 20 && !h.shown.length; i++) await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(h.asked.map((a) => a.o), [{ t: 'pk_peer_req' }]);
  assert.equal(last(h).mode, 'wait');
  // the app hands its own entry (setupFaceId) in as `restart`
  let restarted = 0;
  P._reset(); P.configure({ restart: async () => { restarted++; } });
  assert.equal(P.onRegRes({ t: 'pk_reg_res', ok: false, why: 'peer_required' }), true);
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(restarted, 1);
  P.configure({ restart: undefined });
  const app = read('../public/app.js');
  assert.match(app, /async function pkSaved\(m\) \{\n(?:  [^\n]*P565 追加 A[^\n]*\n)*  if \(pkpeer\.onRegRes\(m\)\) return;/);   // P565: the hand-in ends first
  assert.match(app, /restart: \(\) => setupFaceId\(\)/);
  assert.match(read('../public/js/pkpeer.js'), /const ASK_MS = 10000;/, 'the 10 s legacy fallback stays');
});
