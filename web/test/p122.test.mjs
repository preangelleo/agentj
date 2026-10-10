// P122 (0.17.4, PROTOCOL §12.1): the renewal-ticket cookie. Vectors shared with host/tests/test_p122_renewal.py (the page's
// challenge, the Worker's proof, the host's check agree), and worker.ts /.aj/rt: cookie attributes, same-origin only, the
// secret never returned, nothing else answered.
import test from 'node:test';
import assert from 'node:assert/strict';
import { restoreChallenge, ticketValue } from '../public/js/renew.js';
import { handle, rtEndpoint, rtProof, rtSetCookie, RT_PATH } from '../worker.ts';

const hex = (u) => Buffer.from(u).toString('hex');
const range = (a, b) => Uint8Array.from({ length: b - a }, (_, i) => a + i);
const VEC_CHALLENGE = '6ede7e3f42f0d64a07069969be654eaf19016e570df81a044a93d1090bb10e4d';
const VEC_PROOF = '07517785563f55b0902df406e79d098f15b341e9bb47b23b4cf36a485a76cda1';
const b64u = (u) => Buffer.from(u).toString('base64url');

test('vectors: page challenge + Worker proof = host/agentj/renewal.py', async () => {
  const c = await restoreChallenge(range(0, 32), range(32, 64), 1790000000000, range(64, 80));
  assert.equal(hex(c), VEC_CHALLENGE);
  assert.equal(hex(await rtProof(range(200, 232), c)), VEC_PROOF);
});

const ORIGIN = 'https://m.agentj.app';
const env = { WEB_HOST: 'm.agentj.app', LEGACY_WEB_HOST: 'alpha-web.agentjarvis.net', RELAY_URL: 'wss://relay.agentj.app',
  ASSETS: { fetch: async () => new Response('page') } };
const handleBytes = Uint8Array.of(1, 0, ...range(0, 16), ...range(100, 132));
const VALUE = ticketValue({ relayIndex: 0, channel: b64u(range(0, 16)), hostPub: range(100, 132) }, b64u(range(1, 17)), b64u(range(200, 232)));
const post = (body, { origin = ORIGIN, site = 'same-origin', cookie = '', type = 'application/json', url = ORIGIN + RT_PATH, method = 'POST' } = {}) =>
  new Request(url, { method, headers: { origin, 'sec-fetch-site': site, 'content-type': type, ...(cookie ? { cookie } : {}) },
    body: method === 'POST' ? JSON.stringify(body) : undefined });

test('ticket value: 1.<handle>.<id>.<secret>, nothing else accepted', () => {
  assert.equal(VALUE.split('.')[1], b64u(handleBytes));
  assert.throws(() => ticketValue({ relayIndex: 0, channel: b64u(range(0, 16)), hostPub: range(100, 132) }, 'short', b64u(range(0, 32))));
});

test('/.aj/rt set: HttpOnly + Secure + SameSite=Strict, scoped to /.aj/rt, ~400 days, __Secure- prefix', async () => {
  const r = await handle(post({ op: 'set', v: VALUE }), env);
  assert.equal(r.status, 204);
  assert.equal(r.headers.get('set-cookie'), `__Secure-aj_rt=${VALUE}; Path=/.aj/rt; Max-Age=34560000; Secure; HttpOnly; SameSite=Strict`);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  assert.equal(r.headers.get('access-control-allow-origin'), null, 'no CORS: another site never reads an answer');
  assert.equal(rtSetCookie('', false), 'aj_rt=; Path=/.aj/rt; Max-Age=0; HttpOnly; SameSite=Strict', 'the loopback test server (http) only');
});

test('/.aj/rt prove: the proof over the page challenge, never the secret; has; clear', async () => {
  const cookie = `other=1; __Secure-aj_rt=${VALUE}`;
  const c = await restoreChallenge(range(0, 32), range(32, 64), 1790000000000, range(64, 80));
  const r = await handle(post({ op: 'prove', c: b64u(c) }, { cookie }), env);
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.deepEqual(j, { h: b64u(handleBytes), i: b64u(range(1, 17)), p: Buffer.from(VEC_PROOF, 'hex').toString('base64url') });
  assert.doesNotMatch(JSON.stringify(j), new RegExp(b64u(range(200, 232))), 'the secret never comes back');
  assert.deepEqual(await (await handle(post({ op: 'prove', c: b64u(c) }), env)).json(), {}, 'no cookie → {}');
  assert.deepEqual(await (await handle(post({ op: 'has' }, { cookie }), env)).json(), { has: true });
  assert.deepEqual(await (await handle(post({ op: 'has' }), env)).json(), { has: false });
  assert.match((await handle(post({ op: 'clear' }, { cookie }), env)).headers.get('set-cookie'), /^__Secure-aj_rt=; Path=\/\.aj\/rt; Max-Age=0;/);
  // a malformed cookie value is ignored like a missing one
  assert.deepEqual(await (await handle(post({ op: 'prove', c: b64u(c) }, { cookie: '__Secure-aj_rt=1.x.y.z' }), env)).json(), {});
});

test('/.aj/rt refuses: another origin, a cross-site fetch, a form post, GET, bad ops, oversize, a planted bad value', async () => {
  const c = b64u(range(0, 32));
  for (const [what, req] of [
    ['other origin', post({ op: 'set', v: VALUE }, { origin: 'https://evil.example' })],
    ['no origin', post({ op: 'set', v: VALUE }, { origin: '' })],
    ['cross-site', post({ op: 'prove', c }, { site: 'cross-site' })],
    ['same-site subdomain', post({ op: 'prove', c }, { site: 'same-site' })],
    ['form post', post({ op: 'set', v: VALUE }, { type: 'application/x-www-form-urlencoded' })],
    ['GET', post(null, { method: 'GET' })],
    ['unknown op', post({ op: 'get' })],
    ['bad value', post({ op: 'set', v: VALUE + 'x' })],
    ['bad challenge', post({ op: 'prove', c: c + 'A' })],
    ['oversize', post({ op: 'set', v: VALUE, pad: 'x'.repeat(600) })],
  ]) {
    const r = await handle(req, env);
    assert.equal(r.status, 404, what);
    assert.equal(r.headers.get('set-cookie'), null, what);
  }
  // the legacy host answers on its own origin (its paired phones keep their own cookie there)
  const legacy = 'https://alpha-web.agentjarvis.net';
  assert.equal((await handle(post({ op: 'set', v: VALUE }, { origin: legacy, url: legacy + RT_PATH }), env)).status, 204);
  assert.equal((await handle(post({ op: 'set', v: VALUE }, { url: legacy + RT_PATH }), env)).status, 404, 'origin must be the host it reached');
  assert.equal((await rtEndpoint(post({ op: 'has' }), true)).status, 200);
});
