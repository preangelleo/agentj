// F21 (0.15.2, PROTOCOL §13) — media out, unit checks in Node: md.js's local-vs-remote decision for `![](…)` (a local
// file becomes a slot, a remote picture stays a link and is never fetched, other schemes stay text), the slot → item
// match, the wire sanitiser, the HTML preview frame's attributes (an EMPTY sandbox, no network), the skip notes, and the
// same "no egress / no markup" rules static.test.mjs applies to the other modules, applied to js/media.js.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
globalThis.window ??= {};
const { RelayMD } = await import('../public/js/md.js');
const M = await import('../public/js/media.js');
const { toPage } = await import('../public/js/snap.js');
const SRC = ['media.js', 'mediawire.js'].map((f) => readFileSync(new URL('../public/js/' + f, import.meta.url), 'utf8')).join('\n');

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
const render = (src) => { const root = new FNode('div'); RelayMD.toDOM(RelayMD.parse(src), fdoc, root); return root; };
const sha = 'a'.repeat(64), mid = (c) => c.repeat(22);

test('md.js localRef: absolute, ~/, ./, relative and file: are local; http(s), data:, javascript:, mailto:, //host are not', () => {
  for (const s of ['/home/u/out/a.png', '~/a.png', './a.png', '../a.png', 'out/图表.png', 'a.png', 'file:///tmp/a.png', ' out/a.png '])
    assert.ok(RelayMD.localRef(s), s);
  for (const s of ['https://example.com/a.png', 'http://x/a.png', 'data:image/png;base64,AAAA', 'javascript:alert(1)', 'JavaScript:x',
    'mailto:a@example.com', 'vbscript:x', '//evil.example/a.png', '', '   ', 'a\u0000.png', 'a\n.png', 'x'.repeat(1025), null, 5])
    assert.equal(RelayMD.localRef(s), null, String(s));
});

test('md.js: a local image is a slot (source text inside), a remote image a link (never an <img>), other schemes stay text', () => {
  let r = render('看 ![图表](out/chart.png) 这里');
  const slot = r.all().find((n) => n.className === 'mslot');
  assert.ok(slot, 'a slot for the local picture');
  assert.equal(slot.tag, 'span');
  assert.equal(slot.getAttribute('data-ref'), 'out/chart.png');
  assert.equal(slot.textContent, '![图表](out/chart.png)', 'until the computer sends it, the source text shows');
  assert.equal(r.all().filter((n) => n.tag === 'img').length, 0, 'md.js never creates an <img>');
  r = render('![远程](https://example.com/a.png)');
  const a = r.all().find((n) => n.tag === 'a');
  assert.equal(a.getAttribute('href'), 'https://example.com/a.png');
  assert.equal(r.all().filter((n) => n.className === 'mslot' || n.tag === 'img').length, 0, 'remote: a link, nothing fetched');
  for (const s of ['![x](data:image/png;base64,AAAA)', '![x](javascript:alert(1))'])
    assert.equal(render(s).all().length, 1, `${s}: one paragraph of text, no slot / link`);
  assert.equal(render('[文件](out/a.csv)').all().filter((n) => n.className === 'mslot').length, 0, 'a plain link is not a slot');
  assert.equal(render('`![x](out/a.png)`').all().filter((n) => n.className === 'mslot').length, 0, 'inside code: text');
  assert.equal(render('![e](out/a\\_b.png)').all().find((n) => n.className === 'mslot').getAttribute('data-ref'), 'out/a_b.png');
  assert.equal(render('![e](<my chart.png>)').all().find((n) => n.className === 'mslot').getAttribute('data-ref'), 'my chart.png');
  // the second wall: the slot can only ever be a <span> with a class and data-ref, whatever parse() produced
  const root = new FNode('div');
  RelayMD.toDOM([{ tag: 'mslot', ref: 'x', attrs: { onclick: 'x', src: 'y' }, children: [{ text: 't' }] }], fdoc, root);
  assert.deepEqual(root.kids[0].attrs, { 'data-ref': 'x' });
});

test('slot ↔ item: the same reference, else the only item with that file name; each item once', () => {
  const items = [{ mid: mid('A'), name: 'chart.png', ref: './out/chart.png' }, { mid: mid('B'), name: 'b.png', ref: '<out/b.png>' },
    { mid: mid('C'), name: 'dup.png', ref: 'x/dup.png' }, { mid: mid('D'), name: 'dup.png', ref: 'y/dup.png' }];
  assert.equal(M.matchSlot('out/chart.png', items).mid, mid('A'));
  assert.equal(M.matchSlot('out/b.png', items).mid, mid('B'));
  assert.equal(M.matchSlot('/abs/elsewhere/b.png', items).mid, mid('B'), 'by name when unique');
  assert.equal(M.matchSlot('z/dup.png', items), null, 'two items share the name: no guess');
  assert.equal(M.matchSlot('out/chart.png', items, new Set([mid('A')])), null, 'used once');
  assert.equal(M.normRef('file:///tmp/a%20b.png'), '/tmp/a b.png');
});

test('wire sanitiser: kinds, ids, caps, base names; snap.toPage carries media / mediaSkip', () => {
  const good = { mid: mid('a'), name: 'dir/../chart.png', mime: 'image/png', kind: 'image', bytes: 10, sha256: sha, ref: 'out/chart.png' };
  const { media, skip } = M.sanitize({ media: [good, { ...good, mid: 'short' }, { ...good, kind: 'exe' }, { ...good, bytes: M.CAPS.image + 1 },
    { ...good, sha256: 'x' }, { ...good, mime: 'text/html; x=<y>' }, ...Array(20).fill(good)],
  media_skip: [{ name: '/a/b/.env', why: 'secret' }, { name: 'x', why: 'nope' }, { name: 'big.mp4', why: 'too_big', bytes: 5 * 1048576 }] });
  assert.equal(media.length, M.MAX_ITEMS);
  assert.equal(media[0].name, 'chart.png', 'only the base name is shown');
  assert.deepEqual(skip, [{ name: '.env', why: 'secret', bytes: 0 }, { name: 'big.mp4', why: 'too_big', bytes: 5 * 1048576 }]);
  const p = toPage({ id: 3, ts: 1, src: { k: 'phone', text: 'x' }, reply: { text: 'r' }, end: 'done', media: [good], media_skip: [{ name: 'a', why: 'gone' }] });
  assert.equal(p.media.length, 1);
  assert.deepEqual(p.mediaSkip, [{ name: 'a', why: 'gone', bytes: 0 }]);
  assert.deepEqual(toPage({ id: 4, src: {}, reply: { text: '' } }).media, [], 'an older computer: no media');
  assert.equal(M.blobType({ mime: 'video/quicktime' }), 'video/mp4');
});

test('HTML preview frame: an EMPTY sandbox, no referrer, a CSP that allows no network at all', () => {
  const a = M.sandboxAttrs();
  assert.equal(a.sandbox, '', 'sandbox="" — no allow-scripts / same-origin / forms / popups');
  assert.equal(a.referrerpolicy, 'no-referrer');
  assert.match(a.csp, /^default-src 'none'/);
  assert.doesNotMatch(a.csp, /script-src|https?:|\*/);
  assert.equal(a.allow, '');
  assert.doesNotMatch(SRC, /allow-scripts|allow-same-origin|allow-popups|allow-forms|allow-top-navigation/);
  assert.match(SRC, /f\.srcdoc = html/);
  assert.doesNotMatch(SRC, /window\.open|location\.href|\.src = .*html/i, 'HTML is never opened as a page of its own');
});

test('skip notes say what happened, never the content', () => {
  assert.equal(M.skipText({ name: 'v.mp4', why: 'too_big', bytes: 60 * 1048576 }), 'v.mp4 太大（60.0 MB），没有发到手机。');
  assert.equal(M.skipText({ name: 'a.png', why: 'outside' }), 'a.png 不在工作目录里，没有发。');
  assert.equal(M.skipText({ name: '.env', why: 'secret' }), '.env 可能含密钥，没有发。');
  for (const w of M.WHYS) assert.ok(!M.skipText({ name: 'n', why: w }).startsWith('media.'), w);
});

test('js/media.js + mediawire.js: only the session as egress, no markup APIs, no storage, no logging', () => {
  const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\\'"])\/\/.*$/gm, '$1');
  for (const bad of [/\bfetch\s*\(/, /XMLHttpRequest/, /sendBeacon/, /\bEventSource\b/, /sessionStorage/, /localStorage/, /indexedDB/, /\bconsole\./,
    /document\.cookie/, /postMessage/, /\bnew Worker\b/, /importScripts/, /\bimport\s*\(/, /\binnerHTML\b/, /\bouterHTML\b/, /insertAdjacentHTML/,
    /\beval\s*\(/, /new\s+Function\b/, /document\.write/, /setTimeout\(\s*['"`]/, /\bWebSocket\b/, /https?:\/\//]) {
    assert.doesNotMatch(code, bad, `media.js uses ${bad}`);
  }
  assert.match(SRC, /sendApp\(\{ t: 'media_get', mid: this\.item\.mid, o: this\.got \}, this\.g\)/, 'pinned to the session generation');
  assert.match(SRC, /crypto\.subtle\.digest\('SHA-256', all\)/);
  assert.match(SRC, /if \(sha !== this\.item\.sha256\) return this\.fail\('sha'\)/, 'verified before anything is shown');
});

test('fetch without a session fails as net; media_chunk / media_err are routed, other messages are not', async () => {
  // drive Flight through handle() with a stubbed session: media.js only sends while a generation is live, so here
  // the requests fail with 'net' — the shape of the routing is what is checked
  const bad = M.fetchMedia({ mid: mid('Z'), bytes: 3, sha256: sha, mime: 'image/png', kind: 'image' });
  await assert.rejects(bad, (e) => e.why === 'net');
  assert.equal(M.handle({ t: 'media_chunk', mid: mid('Z'), o: 0, d: 'AAAA', last: true }), true, 'a chunk for no flight is swallowed');
  assert.equal(M.handle({ t: 'media_err', mid: mid('Z'), why: 'gone' }), true);
  assert.equal(M.handle({ t: 'blob_ack', bid: 'x' }), false);
  assert.equal(M.cacheSize(), 0);
});
