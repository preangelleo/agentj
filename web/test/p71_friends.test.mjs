// P71 (0.16, PROTOCOL §17): the web side of agent friends without a browser — Agent ID parsing / derivation against the
// host's vectors (protocol/vectors/peer-id.json), friendAnswerMessage, the six new control objects, the QR encoder (decoded
// back with the vendored jsQR), the Worker's /friends route, and the "no field sends to a friend" rule in friends.js.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { createHash, randomBytes } from 'node:crypto';
import * as wire from '../../protocol/wire.js';
import { qrMatrix } from '../public/js/qr.js';
import { handle, SPA_PATHS } from '../worker.ts';

const V = JSON.parse(readFileSync(new URL('../../protocol/vectors/peer-id.json', import.meta.url), 'utf8'));
const unhex = (h) => Uint8Array.from(Buffer.from(h, 'hex'));
const hex = (b) => Buffer.from(b).toString('hex');
const sha = (s) => createHash('sha256').update(s).digest('hex');
const dec = new TextDecoder();

test('§17.1 Agent ID, id_raw and mbox match every host vector', async () => {
  assert.ok(V.vectors.length >= 3);
  for (const v of V.vectors) {
    const id = await wire.agentIdOf(unhex(v.x25519_pub), unhex(v.ed25519_pub));
    assert.equal(id, v.id);
    assert.equal(hex(wire.agentIdRaw(v.id)), v.id_raw);
    assert.equal(await wire.mboxOfId(v.id), v.mbox);
    assert.equal(wire.friendLink(v.id), `https://m.agentj.app/friends#add=${v.id}`);
  }
});

test('§17.1 parseAgentId agrees with the host on every parse case (case, separators, I/L/O, check character, length)', () => {
  assert.ok(V.parse.length >= 5);
  for (const c of V.parse) assert.equal(wire.parseAgentId(c.in), c.out, JSON.stringify(c.in));
  const id = V.vectors[0].id;
  // check = Σ (i+1)·v_i mod 31: every single substitution is caught except 0 ↔ Z (delta ±31) on a data character
  const body = id.replace(/^AJ-|-/g, '');
  for (let i = 0; i < 16; i++) {
    for (const c of wire.CROCKFORD) {
      if (c === body[i]) continue;
      const d = wire.CROCKFORD.indexOf(c) - wire.CROCKFORD.indexOf(body[i]);
      const got = wire.parseAgentId(body.slice(0, i) + c + body.slice(i + 1));
      if (i < 15 && Math.abs(d) === 31) assert.notEqual(got, null); else assert.equal(got, null, `sub ${i} ${c}`);
    }
  }
  for (const bad of ['', 'AJ-', 'AJ-FRGG-6PG6-V1WK-AAQ', 'AJ-FRGG-6PG6-V1WK-AAQAX', 'AJ-FRGG-6PG6-V1WK-AAQU', null, 42, 'x'.repeat(100)]) assert.equal(wire.parseAgentId(bad), null, String(bad));
});

test('§17.1 derivation round trip on random keys (JS == the spec computed with node:crypto)', async () => {
  for (let k = 0; k < 50; k++) {
    const x = randomBytes(32), pk = randomBytes(32);
    const id = await wire.agentIdOf(x, pk);
    const raw = createHash('sha256').update(Buffer.concat([Buffer.from('agentj/peer-id/v1\n'), x, pk])).digest().subarray(0, 10);
    raw[9] &= 0xe0;
    assert.equal(hex(wire.agentIdRaw(id)), raw.toString('hex'));
    assert.equal(wire.parseAgentId(id.toLowerCase().replace(/-/g, ' ')), id);
  }
});

test('§17.7 friendAnswerMessage: allow + group adds hex(SHA-256("group:"+g)) on one more line; deny / no group / other tools = approveMessage', async () => {
  const args = ['CH', 'DEV', 'a'.repeat(32)];
  const base = dec.decode(await wire.approveMessage(...args, 'allow', 'friend_request', 'card + note'));
  const withG = dec.decode(await wire.friendAnswerMessage(...args, 'allow', 'friend_request', 'card + note', 'colleague'));
  assert.equal(withG, base + '\n' + sha('group:colleague'));
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'allow', 'friend_request', 'card + note', null)), base);
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'deny', 'friend_request', 'card + note', 'colleague')),
    dec.decode(await wire.approveMessage(...args, 'deny', 'friend_request', 'card + note')));
  assert.equal(dec.decode(await wire.friendAnswerMessage(...args, 'allow', 'peer_question', 's', 'colleague')),
    dec.decode(await wire.approveMessage(...args, 'allow', 'peer_question', 's')));
  await assert.rejects(wire.friendAnswerMessage(...args, 'allow', 'friend_request', 's', 'Bad Group'), /bad group/);
});

test('§17.7 control actions and their object texts', async () => {
  for (const a of ['fr_set', 'pg_set', 'pg_del', 'fr_add', 'fr_discoverable', 'fr_card']) assert.ok(wire.CONTROL_ACTIONS.includes(a), a);
  assert.equal(wire.controlObject('fr_set', { friend: 'AJ-1', op: 'group', value: 'friend' }), 'AJ-1\ngroup\nfriend');
  assert.equal(wire.controlObject('fr_set', { friend: 'AJ-1', op: 'block', value: '' }), 'AJ-1\nblock\n');
  const g = { name: '同事', id: 'colleague', builtin: true, limits: { tok: { min: 1, day: null }, msg: { min: 3 }, max_len: 2000, min_interval_s: 1 },
    auto: { mode: 'all', allow: ['寒暄'], ask: [], max_auto_rounds: 30 } };
  const canon = wire.controlObject('pg_set', { group: g });
  assert.equal(canon, '{"auto":{"allow":["寒暄"],"ask":[],"max_auto_rounds":30,"mode":"all"},"builtin":true,"id":"colleague",' +
    '"limits":{"max_len":2000,"min_interval_s":1,"msg":{"min":3},"tok":{"day":null,"min":1}},"name":"同事"}');
  assert.equal(canon, JSON.stringify(JSON.parse(canon)), 'no spaces');
  assert.equal(wire.controlObject('pg_del', { id: 'g-1a2b' }), 'g-1a2b');
  assert.equal(wire.controlObject('fr_add', { id: 'AJ-X', note: 'hi\nthere' }), 'AJ-X\nhi\nthere');
  assert.equal(wire.controlObject('fr_discoverable', { on: true }), 'on');
  assert.equal(wire.controlObject('fr_discoverable', { on: false }), 'off');
  assert.equal(wire.controlObject('fr_card', { owner: 'Leo', intro: '' }), 'Leo\n');
  const m = dec.decode(await wire.controlMessage('CH', 'DEV', 'fr_add', 'f'.repeat(32), 1, { id: 'AJ-X', note: '' }));
  assert.equal(m, `agentjarvis-control-v1\nCH\nDEV\nfr_add\n${'f'.repeat(32)}\n1\n${sha('AJ-X\n')}`);
  await assert.rejects(wire.controlMessage('CH', 'DEV', 'fr_send', 'f'.repeat(32), 1, {}), /bad action/, 'there is no fr_send');
});

test('QR: the share link (and longer texts up to version 10-M) decode back with the vendored jsQR', () => {
  const jsQR = createRequire(import.meta.url)('../public/vendor/jsQR.js');
  const texts = [wire.friendLink(V.vectors[0].id), 'a', 'x'.repeat(100), '中文 ' + 'y'.repeat(150), 'z'.repeat(213)];
  for (const s of texts) {
    const q = qrMatrix(s), k = 4, size = (q.n + 8) * k;
    const px = new Uint8ClampedArray(size * size * 4).fill(255);
    for (let y = 0; y < q.n; y++) for (let x = 0; x < q.n; x++) if (q.m[y][x]) for (let a = 0; a < k; a++) for (let b = 0; b < k; b++) {
      const i = (((y + 4) * k + a) * size + (x + 4) * k + b) * 4; px[i] = px[i + 1] = px[i + 2] = 0;
    }
    assert.equal(jsQR(px, size, size)?.data, s, `decodes ${s.slice(0, 20)}… (version ${(q.n - 17) / 4})`);
  }
  assert.equal(qrMatrix('z'.repeat(214)), null, 'too long → null, never a broken code');
});

test('Worker: /friends serves the page (exact path only); other paths unchanged', async () => {
  assert.deepEqual(SPA_PATHS, ['/friends', '/bots']);
  const seen = [];
  const env = { WEB_HOST: 'm.agentj.app', RELAY_URL: 'wss://relay.agentj.app', ASSETS: { fetch: async (r) => { seen.push(new URL(r.url).pathname); return new Response(new URL(r.url).pathname === '/' ? 'page' : 'nf', { status: new URL(r.url).pathname === '/' ? 200 : 404 }); } } };
  const r = await handle(new Request('https://m.agentj.app/friends'), env);
  assert.equal(r.status, 200);
  assert.equal(await r.text(), 'page');
  assert.match(r.headers.get('content-security-policy'), /connect-src wss:\/\/relay\.agentj\.app/);
  assert.equal(r.headers.get('x-robots-tag'), 'noindex, nofollow, noarchive');
  await handle(new Request('https://m.agentj.app/friends/'), env);
  await handle(new Request('https://m.agentj.app/friends?lang=en'), env);
  assert.deepEqual(seen, ['/', '/friends/', '/']);
  assert.equal((await handle(new Request('https://m.agentj.app/friends', { method: 'POST' }), env)).status, 404);
});

test('friends.js: no field that sends text to a friend, no fr_send, every write is one of the six signed actions', () => {
  const src = readFileSync(new URL('../public/js/friends.js', import.meta.url), 'utf8').replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.doesNotMatch(src, /fr_send|fr_msg|'pmsg'|t: 'tell'/);
  const sent = [...src.matchAll(/\bt: '(\w+)'/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(sent)].sort(), ['answer', 'fr_add', 'fr_card', 'fr_discoverable', 'fr_hist', 'fr_list', 'fr_set', 'fr_usage', 'pg_del', 'pg_set',
    'fr_ctx', 'fr_ctx_get'].sort());   // + P73: a friend's 「补充设定」 (the owner's own setting — not a message to the friend)
  // the only text inputs: the friend ID + note (add), the card's owner name + intro, a group's name / numbers / topics
  const ids = [...src.matchAll(/\.id = '(fr-[\w-]+)'/g)].map((m) => m[1]).filter((id) => /add-id|add-note|owner|intro|fr-g-/.test(id));
  assert.ok(ids.includes('fr-add-id') && ids.includes('fr-add-note') && ids.includes('fr-owner') && ids.includes('fr-intro'));
  assert.equal((src.match(/mk\('textarea'/g) || []).length, 5, 'textareas: the note, the intro, a group\'s topics, the 「补充设定」 (details + accept card, P73)');
});
