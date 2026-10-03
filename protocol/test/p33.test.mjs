// PROTOCOL §10 (relay parity, PROMPT-33): limits, text rules, frag, the question signature, message builders.
// The vectors in ../vectors/p33.json come from the host side (agentj.approvals / agentj.wire); this file checks the JS side
// against them, and host/tests/test_p33.py runs the JS side against Python the other way round.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  padJson, unpadJson, MAX_JSON, MAX_JSON_P33, MAX_TEXT_P33, FRAG_MAX_N, FRAG_MAX_TOTAL, textProblem, normalizeNewlines,
  Defrag, fragSplit, questionDigest, questionMessage, picksText, checkPicks, sayMessage, blobOpen, blobChunk, wav16k,
  histGet, modelSet, menuGet, isId22, newId22, unb64u, b64u, BLOB_CHUNK,
} from '../wire.js';

const V = JSON.parse(readFileSync(new URL('../vectors/p33.json', import.meta.url), 'utf8'));
const enc = new TextEncoder();

test('p33 limits are the vector file\'s and a 60 KiB message pads to ≤ 61 696 bytes (relay payload < 65 536)', () => {
  assert.deepEqual({ MAX_JSON_P33, MAX_TEXT_P33, FRAG_MAX_N, FRAG_MAX_TOTAL }, V.limits);
  const filler = (n) => ({ t: 'x', d: 'a'.repeat(n - 16) });
  const pt = padJson(filler(MAX_JSON_P33), MAX_JSON_P33);
  assert.equal(enc.encode(JSON.stringify(filler(MAX_JSON_P33))).length, MAX_JSON_P33);
  assert.ok(pt.length <= 61696 && pt.length % 256 === 0);
  assert.ok(1 + pt.length + 16 <= 65536);
  assert.throws(() => padJson(filler(MAX_JSON_P33 + 1), MAX_JSON_P33), /too large/);
  assert.throws(() => padJson(filler(MAX_JSON + 1)), /too large/, 'without p33 the §3 limit stays');
  assert.throws(() => unpadJson(padJson(filler(MAX_JSON + 1), MAX_JSON_P33)), /bad length/, 'receiver enforces 16 KiB for an old peer');
  assert.equal(unpadJson(padJson(filler(MAX_JSON + 1), MAX_JSON_P33), MAX_JSON_P33).t, 'x');
});

test('text rules: the same answer as the host for every vector (lone surrogates, C0, CR, the 20 000 limit)', () => {
  for (const c of V.text) assert.equal(textProblem(c.s, c.limit), c.want, JSON.stringify(c.s.slice(0, 30)));
  assert.equal(normalizeNewlines('a\r\nb\rc'), 'a\nb\nc');
  assert.equal(textProblem(42), 'shape');
});

test('a maximal say (20 000 units, ≤ 3 bytes each) always fits one frame', () => {
  const m = sayMessage({ text: '中'.repeat(MAX_TEXT_P33 - 2000), reply_to: 7, excerpt: '文'.repeat(2000) });
  assert.ok(enc.encode(JSON.stringify(m)).length <= MAX_JSON_P33, 'no device → host chunking of text exists');
  assert.throws(() => sayMessage({ text: 'x'.repeat(MAX_TEXT_P33 - 10), reply_to: 1, excerpt: 'y'.repeat(11) }), /too_long/);
  assert.throws(() => sayMessage({ text: 'x', excerpt: 'y' }), /excerpt only with reply_to/);
  assert.throws(() => sayMessage({ text: '' }), /empty/);
  assert.throws(() => sayMessage({ text: 'a\u0007' }), /shape/);
  assert.equal(sayMessage({ text: 'a\r\nb' }).text, 'a\nb', 'the sender normalises newlines');
  assert.throws(() => sayMessage({ text: 'x', att: ['short'] }), /bad att/);
  const id = newId22();
  assert.ok(isId22(id));
  assert.deepEqual(sayMessage({ sid: id, text: '', att: [id], ts: 1 }), { t: 'say', sid: id, text: '', ts: 1, att: [id] });
});

test('frag: split ↔ reassemble on code-point boundaries, every frame ≤ 60 KiB; broken sequences are dropped', () => {
  const r = V.frag_recipe;
  const inner = { t: 'hist_turn', epoch: 1, turn: { id: 1, ts: 0, src: { k: 'agent', text: '' }, reply: { text: r.unit.repeat(r.repeat) }, end: 'done' } };
  const fr = fragSplit(inner, r.fid);
  assert.ok(fr.length > 1 && fr.length <= FRAG_MAX_N);
  for (const f of fr) assert.ok(enc.encode(JSON.stringify(f)).length <= MAX_JSON_P33);
  const d = new Defrag();
  let out = null;
  for (const f of fr) out = d.feed(f) ?? out;
  assert.deepEqual(out, inner);
  // another message in between → the partial is dropped, the other message is handled
  const d2 = new Defrag();
  assert.equal(d2.feed(fr[0]), null);
  assert.deepEqual(d2.feed({ t: 'status', s: 'idle' }), { t: 'status', s: 'idle' });
  for (const f of fr.slice(1)) assert.equal(d2.feed(f), null, 'no reassembly from a broken sequence');
  // a wrong i, a changed n, a frag inside a frag
  const d3 = new Defrag();
  d3.feed(fr[0]);
  assert.equal(d3.feed({ ...fr[2] }), null);
  const nested = fragSplit({ t: 'frag', f: 'x', i: 0, n: 1, d: 'y'.repeat(70000) }, 'z'.repeat(16));
  const d4 = new Defrag();
  let o4 = null;
  for (const f of nested) o4 = d4.feed(f) ?? o4;
  assert.equal(o4, null, 'the inner message is never another frag');
  assert.deepEqual(fragSplit({ t: 'small' }, 'f'), [{ t: 'small' }]);
});

test('question signature: digest, message bytes and Ed25519 signatures equal the host\'s vectors', async () => {
  const Q = V.question;
  assert.equal(await questionDigest(Q.qs), Q.digest);
  const pkcs8 = new Uint8Array([0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
    ...Buffer.from(V.seed_hex, 'hex')]);
  const priv = await crypto.subtle.importKey('pkcs8', pkcs8, { name: 'Ed25519' }, false, ['sign']);
  const pub = await crypto.subtle.importKey('raw', unb64u(V.pub), { name: 'Ed25519' }, false, ['verify']);
  for (const c of Q.cases) {
    const msg = await questionMessage(c.channel, c.device, c.id, c.action, Q.qs, c.picks);
    assert.equal(new TextDecoder().decode(msg), c.message);
    assert.equal(picksText(c.picks), c.picks_text);
    const sig = new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, priv, msg));
    assert.equal(b64u(sig), c.sig, 'Ed25519 is deterministic: the same bytes sign the same');
    assert.ok(await crypto.subtle.verify({ name: 'Ed25519' }, pub, unb64u(c.sig), msg));
  }
  // what the phone showed is bound: another label / description / header / multi flag / pick changes the bytes
  const base = await questionMessage('C', 'D', 'i', 'answer', Q.qs, [[1], [2]]);
  const alt = structuredClone(Q.qs); alt[0].o[0].d = 'changed';
  assert.notDeepEqual(await questionMessage('C', 'D', 'i', 'answer', alt, [[1], [2]]), base);
  const alt2 = structuredClone(Q.qs); alt2[1].m = false;
  await assert.rejects(questionMessage('C', 'D', 'i', 'answer', alt2, [[1], [1, 3]]), /bad picks/);
  assert.notDeepEqual(await questionMessage('C', 'D', 'i', 'answer', alt2, [[1], [2]]), base);
  await assert.rejects(questionMessage('C', 'D', 'i', 'cancel', Q.qs, [[1], [2]]), /picks only with answer/);
  assert.equal(checkPicks(Q.qs, [[1], [3, 1]]), false, 'ascending');
  assert.equal(checkPicks(Q.qs, [[1, 2], [1]]), false, 'one pick for a single-choice question');
  assert.equal(checkPicks(Q.qs, [[4], [1]]), false, 'in range');
  assert.equal(checkPicks(Q.qs, [[1], [1, 2, 3]]), true);
});

test('blob helpers: a 45 056-byte chunk frame is the same size every time and < 60 KiB; WAV header is 16 kHz mono PCM16', () => {
  const bid = newId22();
  const sizes = new Set();
  for (let k = 0; k < 3; k++) sizes.add(padJson(blobChunk(bid, k * BLOB_CHUNK, crypto.getRandomValues(new Uint8Array(BLOB_CHUNK))), MAX_JSON_P33).length);
  assert.deepEqual([...sizes], [60160]);
  const w = wav16k(new Float32Array(16000));
  assert.equal(w.length, 44 + 32000);
  const v = new DataView(w.buffer);
  assert.deepEqual([String.fromCharCode(...w.slice(0, 4)), String.fromCharCode(...w.slice(8, 12)), v.getUint16(20, true), v.getUint16(22, true), v.getUint32(24, true), v.getUint16(34, true), v.getUint32(40, true)],
    ['RIFF', 'WAVE', 1, 1, 16000, 16, 32000]);
  const o = blobOpen({ bid, name: 'a.wav', mime: 'audio/wav', size: w.length, sha256: '0'.repeat(64), purpose: 'asr', origin: 'recording', secs: 1 });
  assert.equal(o.t, 'blob_open');
  assert.deepEqual(histGet('r1', { before: 9, limit: 20 }), { t: 'hist_get', r: 'r1', before: 9, limit: 20 });
  assert.deepEqual(modelSet('r2', { def: true }), { t: 'model_set', r: 'r2', default: true });
  assert.deepEqual(modelSet('r3', { effort: 'high' }), { t: 'model_set', r: 'r3', model: null, effort: 'high' });
  assert.deepEqual(menuGet('r4'), { t: 'menu_get', r: 'r4' });
});
