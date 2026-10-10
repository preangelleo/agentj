// Unit checks of the page's protocol helpers (PROTOCOL §10), run in Node on the shipped modules.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { Pacer, P33_JSON } from '../public/js/session.js';
import { padJson as pad, unpadJson as unpad, Defrag, FRAG_MAX_TOTAL as FRAG_MAX_BYTES, questionMessage as wireQM, questionDigest, picksText } from '../public/proto/wire.js';
import { cleanText } from '../public/js/api.js';
import { wavBytes } from '../public/js/wav.js';
import { newId, CHUNK } from '../public/js/blobs.js';
import { chunks, speakText, langOf } from '../public/js/speak.js';
import * as wire from '../../protocol/wire.js';

if (!globalThis.crypto) globalThis.crypto = webcrypto;

// The page reassembles with wire.js's Defrag (session.js); these cases drive it the same way the page does.
class Frag { constructor() { this.d = new Defrag(); } push(m) { return this.d.feed(m); } get cur() { return this.d.f; } }
// The page signs with wire.js's questionMessage (api.js answerQ: picks de-duplicated and sorted first).
const questionMessage = (ch, dev, id, qs, picks) => {
  const clean = picks ? picks.map((p) => [...new Set(p)].sort((a, b) => a - b)) : null;
  return wireQM(ch, dev, id, clean ? 'answer' : 'cancel', qs, clean);
};

test('§10.0 padding: 60 KiB JSON fits, more is refused, a 61 440-byte message pads to ≤ 61 696', () => {
  const big = { t: 'x', d: 'a'.repeat(P33_JSON - 20) };
  const pt = pad(big, P33_JSON);
  assert.ok(pt.length % 256 === 0 && pt.length <= 61696);
  assert.deepEqual(unpad(pt, P33_JSON), big);
  assert.throws(() => pad({ t: 'x', d: 'a'.repeat(P33_JSON) }, P33_JSON));
  assert.throws(() => unpad(pt, 16384), /bad length/);
});

test('§10.1 frag: in-order slices reassemble; a gap, a changed N, an interleaved message or > 2 MiB drop the partial', () => {
  const inner = JSON.stringify({ t: 'hist_page', r: 'a', turns: [{ id: 1, reply: { text: '中文😀'.repeat(1000) } }] });
  const parts = [inner.slice(0, 1000), inner.slice(1000, 3000), inner.slice(3000)];
  const f = new Frag();
  assert.equal(f.push({ t: 'frag', f: '0123456789abcdef', i: 0, n: 3, d: parts[0] }), null);
  assert.equal(f.push({ t: 'frag', f: '0123456789abcdef', i: 1, n: 3, d: parts[1] }), null);
  assert.deepEqual(f.push({ t: 'frag', f: '0123456789abcdef', i: 2, n: 3, d: parts[2] }), JSON.parse(inner));
  const g = new Frag();
  g.push({ t: 'frag', f: '0123456789abcdef', i: 0, n: 3, d: parts[0] });
  assert.equal(g.push({ t: 'frag', f: '0123456789abcdef', i: 2, n: 3, d: parts[2] }), null, 'wrong i');
  assert.equal(g.cur, null);
  g.push({ t: 'frag', f: '0123456789abcdef', i: 0, n: 3, d: parts[0] });
  assert.deepEqual(g.push({ t: 'status', s: 'idle' }), { t: 'status', s: 'idle' }, 'another message is handled normally');
  assert.equal(g.cur, null, '…and drops the partial');
  g.push({ t: 'frag', f: '0123456789abcdef', i: 0, n: 3, d: parts[0] });
  assert.equal(g.push({ t: 'frag', f: '0123456789abcdef', i: 1, n: 4, d: parts[1] }), null, 'N changed');
  const h = new Frag();
  const slab = 'x'.repeat(60000);
  let out = null;
  for (let i = 0; i < 40 && h.cur !== null || i === 0; i++) out = h.push({ t: 'frag', f: 'ffffffffffffffff', i, n: 128, d: slab });
  assert.equal(out, null);
  assert.equal(h.cur, null, `over ${FRAG_MAX_BYTES} bytes → dropped`);
  assert.equal(new Frag().push({ t: 'frag', f: 'ffffffffffffffff', i: 0, n: 1, d: JSON.stringify({ t: 'frag', f: 'a', i: 0, n: 1, d: '' }) }), null, 'a frag inside a frag is refused');
});

test('§10.3 / §10.13 pacing: ≤ 50 frames / 10 s, boosted ≤ 220 / 10 s and ≤ 22 / s', () => {
  let now = 0;
  const p = new Pacer(() => now);
  for (let i = 0; i < 50; i++) { assert.equal(p.wait(), 0); p.mark(); }
  assert.ok(p.wait() > 9000, 'the 51st frame waits for the window');
  const q = new Pacer(() => now); q.boost = true;
  for (let i = 0; i < 22; i++) { assert.equal(q.wait(), 0); q.mark(); }
  assert.ok(q.wait() > 0 && q.wait() <= 1000, '≤ 22 per second');
  now = 1000;
  let sent = 22;
  for (let t = 1000; t < 10000; t += 50) { now = t; if (q.wait() === 0) { q.mark(); sent++; } }
  assert.ok(sent <= 220, `≤ 220 per 10 s (${sent})`);
});

test('§10.0 text rules: CR/LF → LF, C0 controls dropped (tab and newline kept), lone surrogates replaced', () => {
  assert.equal(cleanText('a\r\nb\rc\td\u0000e\u0007f\u001b'), 'a\nb\nc\tdef');
  assert.equal(cleanText('x\ud800y\udc00z😀'), 'x�y�z😀');
});

test('§10.9 WAV: 44-byte header, PCM 1 ch 16 kHz 16 bit, data length = samples', () => {
  const b = Buffer.from(wavBytes(new Float32Array([0, 1, -1, 0.5])));
  assert.equal(b.length, 44 + 8);
  assert.equal(b.toString('ascii', 0, 4), 'RIFF'); assert.equal(b.toString('ascii', 8, 12), 'WAVE');
  assert.equal(b.readUInt16LE(20), 1); assert.equal(b.readUInt16LE(22), 1); assert.equal(b.readUInt32LE(24), 16000); assert.equal(b.readUInt16LE(34), 16);
  assert.equal(b.readUInt32LE(40), 8);
  assert.deepEqual([b.readInt16LE(44), b.readInt16LE(46), b.readInt16LE(48)], [0, 32767, -32768]);
});

test('§10.7 question signature input: exact layout; the page signs with protocol/wire.js', async () => {
  const qs = [{ q: '用哪种方案？', h: '方案', m: true, o: [{ l: 'A', d: '快' }, { l: 'B' }] }, { q: 'Go?', h: '', m: false, o: [{ l: 'yes' }, { l: 'no' }] }];
  const msg = new TextDecoder().decode(await questionMessage('CH', 'DEV', 'f'.repeat(32), qs, [[2, 1], [1]]));
  const lines = msg.split('\n');
  assert.deepEqual(lines.slice(0, 5), ['agentjarvis-question-v1', 'CH', 'DEV', 'f'.repeat(32), 'answer']);
  assert.match(lines[5], /^[0-9a-f]{64}$/);
  assert.equal(lines[6], '1,2;1');
  assert.equal(picksText([[1, 3]]), '1,3');
  const cancel = new TextDecoder().decode(await questionMessage('CH', 'DEV', 'f'.repeat(32), qs, null));
  assert.ok(cancel.endsWith('\ncancel\n' + lines[5] + '\n-'));
  // Q digest changes with any shown text
  const d1 = await questionDigest(qs), d2 = await questionDigest([{ ...qs[0], o: [{ l: 'A', d: '慢' }, { l: 'B' }] }, qs[1]]);
  assert.notEqual(d1, d2);
  const ref = await wire.questionMessage('CH', 'DEV', 'f'.repeat(32), 'answer', qs, [[1, 2], [1]]);
  assert.equal(new TextDecoder().decode(ref), msg, 'the page signs exactly what protocol/wire.js defines');
});

test('§10.3 ids and chunks: 128-bit base64url ids, 45 056-byte chunks', () => {
  const id = newId();
  assert.match(id, /^[A-Za-z0-9_-]{22}$/);
  assert.equal(CHUNK, 45056);
  assert.ok(wire.b64u(new Uint8Array(CHUNK)).length === 60075);
});

test('§10.14 read aloud: Markdown reduced to words, code skipped, split ≤ 180, language guessed', () => {
  const s = speakText('# 标题\n\n**粗体**和[链接](https://example.com)\n\n```js\nsecret()\n```\n\n- 一\n- 二');
  assert.ok(s.includes('标题') && s.includes('粗体') && s.includes('链接') && !s.includes('secret') && !s.includes('example.com'));
  assert.ok(chunks('句子。'.repeat(200)).every((c) => c.length <= 180));
  assert.equal(langOf('今天有 132 单'), 'zh');
  assert.equal(langOf('Today there are 132 orders'), 'en');
});

// ---------------------------------------------------------------- md.js (the Agent's words): P33-X12 / P33-C08
// A minimal DOM: enough for toDOM (createElement / createTextNode / fragments / attributes / textContent).
class FNode {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.className = ''; this.data = null; }
  appendChild(n) { if (n.tag === '#frag') this.kids.push(...n.kids); else this.kids.push(n); return n; }
  append(...ns) { for (const n of ns) this.appendChild(n); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get textContent() { return this.data !== null ? this.data : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this.data = null; if (this.tag === '#text') this.data = String(v); else this.kids.push(fdoc.createTextNode(v)); }
  all() { const out = []; const st = [...this.kids]; while (st.length) { const n = st.pop(); if (n.tag !== '#text') { out.push(n); st.push(...n.kids); } } return out; }
}
const fdoc = {
  createElement: (t) => new FNode(t),
  createTextNode: (s) => { const n = new FNode('#text'); n.data = String(s); return n; },
  createDocumentFragment: () => new FNode('#frag'),
};
const { RelayMD } = await import('../public/js/md.js');
const render = (src) => { const root = new FNode('div'); RelayMD.toDOM(RelayMD.parse(src), fdoc, root); return root; };

test('P33-X12: the 2.4 KB table that expanded to 160 000 cells is plain text now; table size, cells and elements are bounded', () => {
  const evil = '|' + 'a|'.repeat(400) + '\n|' + '-|'.repeat(400) + '\n' + '|\n'.repeat(400);
  assert.ok(evil.length >= 2400 && evil.length < 2500);
  let t = performance.now();
  const root = render(evil);
  assert.ok(performance.now() - t < 500, 'rendered in well under a second');
  assert.equal(root.all().filter((n) => n.tag === 'td' || n.tag === 'th').length, 0, 'too wide: no table cells at all');
  assert.ok(root.textContent.includes('|a|a|'), 'the lines are shown as text');
  // a legal table: rows past MAX_ROWS stay text; the per-text cell budget is shared by every table
  const { MAX_ROWS, MAX_CELLS, MAX_NODES, MAX_COLS } = RelayMD.LIMITS;
  const row = '|' + 'x|'.repeat(MAX_COLS) + '\n';
  const tall = '|' + 'h|'.repeat(MAX_COLS) + '\n|' + '-|'.repeat(MAX_COLS) + '\n' + row.repeat(MAX_ROWS * 2);
  const cells = (r) => r.all().filter((n) => n.tag === 'td' || n.tag === 'th').length;
  assert.ok(cells(render(tall)) <= MAX_CELLS, 'one tall table ≤ MAX_CELLS cells');
  const many = ('|a|b|c|d|\n|-|-|-|-|\n' + '|1|2|3|4|\n'.repeat(50) + '\n').repeat(200);
  t = performance.now();
  const r2 = render(many);
  assert.ok(cells(r2) <= MAX_CELLS, `200 tables share the budget (${cells(r2)} cells)`);
  assert.ok(r2.textContent.includes('|1|2|3|4|'), 'the tables past the budget are text');
  // a flood of inline elements: ≤ MAX_NODES elements, the rest one text node, nothing lost
  const flood = '**b** '.repeat(60000);
  const r3 = render(flood);
  assert.ok(r3.all().length <= MAX_NODES + 1, `elements bounded (${r3.all().length})`);
  assert.equal((r3.textContent.match(/b/g) || []).length, 60000, 'every word still shown');
  assert.ok(performance.now() - t < 3000);
});

test('P33-X12: pathological 200 KB inputs parse in linear time (brackets, code runs, emphasis, nesting)', () => {
  const N = 200000;
  const growing = Array.from({ length: 600 }, (_, i) => '`'.repeat(i + 1)).join(' ').slice(0, N);
  for (const [name, src] of Object.entries({
    brackets: '['.repeat(N), nested: '[['.repeat(N / 2) + ']]', codeLinks: '[`'.repeat(N / 2), growing,
    stars: '*a '.repeat(N / 3), quotes: '>'.repeat(N), lists: '- '.repeat(N / 2), autolinks: '<http://a'.repeat(N / 9),
  })) {
    const t = performance.now();
    RelayMD.parse(src);
    const ms = performance.now() - t;
    assert.ok(ms < 400, `${name}: ${Math.round(ms)} ms`);
  }
});

test('P33-C08: links — https / http / mailto only, rel=noopener noreferrer, the real host shown when the words differ', () => {
  for (const bad of ['javascript:alert(1)', 'JAVASCRIPT:alert(1)', 'data:text/html,<b>x', 'vbscript:x', 'file:///etc/passwd', '//evil.example/x', 'mailto:a@b.io?bcc=c@d.io', 'mailto:a@b.io,c@d.io'])
    assert.equal(RelayMD.safeHref(bad), null, bad);
  assert.equal(RelayMD.safeHref('mailto:ops@shop.example'), 'mailto:ops@shop.example');
  assert.equal(RelayMD.safeHref('https://x.example/a'), 'https://x.example/a');
  const links = (r) => r.all().filter((n) => n.tag === 'a');
  const hosts = (r) => r.all().filter((n) => n.className === 'lhost').map((n) => n.textContent);
  let r = render('[https://bank.example](https://evil.example/login)');
  assert.equal(links(r).length, 1);
  assert.equal(links(r)[0].getAttribute('href'), 'https://evil.example/login');
  assert.equal(links(r)[0].getAttribute('rel'), 'noopener noreferrer');
  assert.deepEqual(hosts(r), [' (evil.example)'], 'a URL-looking label that lies shows where it goes');
  r = render('[点这里](https://evil.example/x)');
  assert.deepEqual(hosts(r), [' (evil.example)']);
  for (const same of ['https://x.example/a', '<https://x.example/a>', '[https://x.example/a](https://x.example/a)', '[x.example/a](https://x.example/a)'])
    assert.deepEqual(hosts(render(same)), [], 'no host when the words are the address: ' + same);
  r = render('[写信](mailto:ops@shop.example)');
  assert.equal(links(r)[0].getAttribute('href'), 'mailto:ops@shop.example');
  assert.deepEqual(hosts(r), [' (ops@shop.example)']);
  assert.deepEqual(hosts(render('[ops@shop.example](mailto:ops@shop.example)')), []);
  r = render('[x](javascript:alert(1)) [y](data:text/html,hi)');
  assert.equal(links(r).length, 0, 'never a javascript: / data: link');
  assert.ok(r.textContent.includes('javascript:alert(1)'), 'shown as its source text');
});

test('P34: speech selects configured local voice and refuses network or unknown-locality voices',async()=>{
 const {configureSpeech,localVoice}=await import('../public/js/speak.js');
 globalThis.window={speechSynthesis:{getVoices:()=>[{name:'network',localService:false,lang:'en-US'},{name:'unknown',lang:'en-US'},{name:'local',localService:true,lang:'en-US'}]}};
 configureSpeech({tts:{phone_voice:'network'}});assert.equal(localVoice('en').name,'local');
 configureSpeech({tts:{phone_voice:'unknown'}});assert.equal(localVoice('en').name,'local');
 configureSpeech({tts:{phone_voice:'local'}});assert.equal(localVoice('en').name,'local');
 configureSpeech({});delete globalThis.window;
});

// 0.15.1 (P55): which closes mean "removed". Before, every 4010 did — a refused pairing and a handshake the host timed out
// while iOS kept the page asleep both showed 「这台手机已被电脑移除」 and wiped the drafts.
test('P55: a close is "removed" only when the host said so; unexplained closes keep the pairing; a pairing that ends is "not approved"', async () => {
  const session = await import('../public/js/session.js');
  class FakeWS { constructor() { FakeWS.last = this; this.readyState = 1; } send() {} close() {} }
  FakeWS.OPEN = 1;
  const saved = globalThis.WebSocket;
  globalThis.WebSocket = FakeWS;
  const calls = [];
  session.configure({ setStatus() {}, resume() {}, onHostDown: () => calls.push('down'), onRevoked: (why) => calls.push('revoked:' + why),
    onPairFailed: () => calls.push('pairFailed') });
  const ctx = { relay: 'ws://127.0.0.1:1', channel: 'c'.repeat(22), hostPub: new Uint8Array(32) };
  const close = async (s, code) => { FakeWS.last.onclose({ code }); await s.chain; };
  try {
    let s = session.openSession('pair', ctx); s.phase = 'approval';
    await close(s, 4010);
    assert.deepEqual(calls.splice(0), ['pairFailed'], 'a refused / timed-out pairing (4010) is a failed pairing');
    s = session.openSession('resume', ctx); s.phase = 'hs';
    await close(s, 4010);
    assert.deepEqual(calls.splice(0), ['down'], 'one unexplained refusal: reconnect, nothing wiped');
    s = session.openSession('resume', ctx); s.phase = 'hs';
    await close(s, 1000);
    assert.deepEqual(calls.splice(0), ['down'], 'refused again without authenticated removed: reconnect');
    s = session.openSession('resume', ctx); s.phase = 'ready-wait'; s.removed = 'replaced';   // the host's `removed` message
    await close(s, 4010);
    assert.deepEqual(calls.splice(0), ['revoked:replaced'], 'the host said why: shown at once');
    for (let i = 0; i < 3; i++) { s = session.openSession('resume', ctx); s.phase = 'hs'; await close(s, 1006); }
    assert.deepEqual(calls.splice(0), ['down', 'down', 'down'], 'network drops (1006) never count as refusals');
  } finally {
    session.closeSession();
    globalThis.WebSocket = saved;
  }
});
