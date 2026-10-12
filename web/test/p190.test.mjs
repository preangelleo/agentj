// P190 (PROTOCOL §27): the reverse pairing code and its sealed hand-off — protocol/wire.js, the bytes the page ships.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as w from '../../protocol/wire.js';

const now = () => Math.floor(Date.now() / 1000);
async function code(relay = w.PAIR_DEFAULT_RELAY, exp = now() + 300) {
  const kp = await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
  const pub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
  const ch = w.b64u(crypto.getRandomValues(new Uint8Array(16))), n = crypto.getRandomValues(new Uint8Array(16));
  return { kp, pub, ch, n, link: w.reverseLink(ch, pub, n, exp, relay) };
}

test('a code round-trips: our relay is empty in the code, a test relay kept whole, ≤ 120 chars', async () => {
  const c = await code();
  assert.match(c.link, /^https:\/\/m\.agentj\.app\/#j=[A-Za-z0-9_-]{92}$/);
  assert.ok(c.link.length <= 120);
  const p = w.parseReverse(c.link);
  assert.equal(p.channel, c.ch); assert.deepEqual(p.pub, c.pub); assert.deepEqual(p.nonce, c.n); assert.equal(p.relay, w.PAIR_DEFAULT_RELAY);
  const t = await code('ws://127.0.0.1:8787');
  assert.equal(w.parseReverse(t.link).relay, 'ws://127.0.0.1:8787');
});

test('expired, far-future, wrong version, truncated, foreign relay → refused', async () => {
  assert.throws(() => w.parseReverse(''), /bad reverse code/);
  const old = await code(undefined, now() - 1);
  assert.throws(() => w.parseReverse(old.link), /expired/);
  const far = await code(undefined, now() + 3600);
  assert.throws(() => w.parseReverse(far.link), /bad expiry/);
  const c = await code();
  const raw = w.unb64u(c.link.split('#j=')[1]); raw[0] = 2;
  assert.throws(() => w.parseReverse('#j=' + w.b64u(raw)), /version/);
  assert.throws(() => w.parseReverse(c.link.slice(0, -10)), /bad reverse code/);
  const evil = await code('wss://evil.example');
  assert.throws(() => w.parseReverse(evil.link, now(), (u) => u === w.PAIR_DEFAULT_RELAY), /relay not allowed/);
});

test('classifyScan: reverse / pair / friend / data — and nothing else is trusted', () => {
  assert.equal(w.classifyScan('https://m.agentj.app/#j=AAAA'), 'reverse');
  assert.equal(w.classifyScan('https://m.agentj.app/#p=123'), 'pair');
  assert.equal(w.classifyScan(w.FRIEND_LINK_BASE + 'AJ-XXXX'), 'friend');
  for (const s of ['https://m.agentj.app.evil.com/#j=AAAA', 'http://m.agentj.app/#j=A', 'hello', 'https://example.com/#j=A', ''])
    assert.equal(w.classifyScan(s), 'data', s);
});

test('only the code\'s own key opens an offer; the answer opens only with the offer\'s key; nonce and channel are bound', async () => {
  const c = await code();
  const p = w.parseReverse(c.link);
  const { frame, key } = await w.rvOffer(p, 'https://m.agentj.app/#p=12345');
  assert.equal(frame[0], w.RV_OFFER);
  const o = await w.rvOpenOffer(frame, c.kp.privateKey, c.ch, c.n);
  assert.equal(o.link, 'https://m.agentj.app/#p=12345');
  const other = await code();
  await assert.rejects(w.rvOpenOffer(frame, other.kp.privateKey, c.ch, c.n));                 // another page's key
  await assert.rejects(w.rvOpenOffer(frame, c.kp.privateKey, other.ch, c.n));                 // another channel
  await assert.rejects(w.rvOpenOffer(frame, c.kp.privateKey, c.ch, other.n));                 // another nonce
  const bad = frame.slice(); bad[bad.length - 1] ^= 1;
  await assert.rejects(w.rvOpenOffer(bad, c.kp.privateKey, c.ch, c.n));                       // tampered
  const ans = await w.rvAnswer(o.key, c.ch, { ok: false, why: 'taken' });
  assert.deepEqual(await w.rvOpenAnswer(ans, key, c.ch), { ok: false, why: 'taken' });
  await assert.rejects(w.rvOpenAnswer(ans, key, other.ch));
  await assert.rejects(w.rvOpenAnswer(frame, key, c.ch));                                      // an offer is not an answer
  assert.ok(new TextDecoder('latin1').decode(frame).indexOf('#p=') < 0, 'the link is not in the frame');
});

test('dev_add signs "new" or "rv\\n<channel>"', () => {
  assert.equal(w.controlObject('dev_add', {}), 'new');
  assert.equal(w.controlObject('dev_add', { rv: 'AbCdEfGhIjKlMnOpQrStUv' }), 'rv\nAbCdEfGhIjKlMnOpQrStUv');
});
