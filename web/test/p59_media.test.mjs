// P59 (ADR-A164) — F21 finishing, unit checks in Node: md.js turns a `[label](local file)` link into a link slot (the label
// kept as an attribute value, never markup), remote links / pictures stay links and are never slots, and mediawire.placeSlots
// decides where every item and skip note of a page goes (inline at its reference, or the strip — never both).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window ??= {};
const { RelayMD } = await import('../public/js/md.js');
const W = await import('../public/js/mediawire.js');
const MD_SRC = readFileSync(new URL('../public/js/md.js', import.meta.url), 'utf8');
const MEDIA_SRC = readFileSync(new URL('../public/js/media.js', import.meta.url), 'utf8');

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
const slots = (r) => r.all().filter((n) => n.className === 'mslot');
const mid = (c) => c.repeat(22);

test('md.js: [label](local file) → a link slot with the label as plain text, the source text inside until filled', () => {
  const r = render('报告在这：[**季度**报告](out/report.pdf)，请看。');
  const [s] = slots(r);
  assert.ok(s, 'a slot');
  assert.equal(s.tag, 'span');
  assert.deepEqual(s.attrs, { 'data-ref': 'out/report.pdf', 'data-link': '1', 'data-label': '季度报告' });
  assert.equal(s.textContent, '[**季度**报告](out/report.pdf)', 'until the computer sends it, the source text shows');
  assert.equal(r.all().filter((n) => n.tag === 'a').length, 0, 'never an <a href> to a local path');
  for (const [src, ref] of [['[a](~/x.csv)', '~/x.csv'], ['[a](./a b.png)', null], ['[a](<a b.zip>)', 'a b.zip'], ['[a](file:///tmp/x.pdf)', 'file:///tmp/x.pdf']]) {
    const got = slots(render(src))[0];
    assert.equal(got ? got.getAttribute('data-ref') : null, ref, src);
  }
});

test('md.js: remote links and pictures are still links (never fetched, never a slot); other schemes stay text', () => {
  for (const src of ['[报告](https://example.com/r.pdf)', '![图](https://example.com/a.png)', '[信](mailto:a@example.com)']) {
    const r = render(src);
    assert.equal(slots(r).length, 0, src);
    assert.equal(r.all().filter((n) => n.tag === 'a').length, 1, src);
    assert.equal(r.all().filter((n) => n.tag === 'img').length, 0, src);
  }
  for (const src of ['[x](javascript:alert(1))', '[x](data:text/html,<b>)', '[x](//evil.example/a)']) {
    const r = render(src);
    assert.equal(slots(r).length + r.all().filter((n) => n.tag === 'a').length, 0, src);
  }
  assert.equal(slots(render('`[a](out/x.pdf)`')).length, 0, 'inside code: text');
  assert.equal(slots(render('```\n[a](out/x.pdf)\n```')).length, 0, 'inside a fence: text');
  // a picture inside a remote link's words: the link stays a link; a local picture there is still the picture's slot
  const r = render('[![图](out/a.png)](https://example.com/)');
  assert.equal(r.all().filter((n) => n.tag === 'a').length, 1);
  assert.equal(slots(r).filter((s) => s.getAttribute('data-link') === '1').length, 0);
});

test('md.js: the slot is only ever a span with data-ref / data-link / data-label, whatever parse() produced', () => {
  const root = new FNode('div');
  RelayMD.toDOM([{ tag: 'mslot', ref: 'x', link: true, label: '<img src=x onerror=alert(1)>' + 'y'.repeat(400), attrs: { onclick: 'x' }, children: [{ text: 't' }] }], fdoc, root);
  const s = root.kids[0];
  assert.deepEqual(Object.keys(s.attrs).sort(), ['data-label', 'data-link', 'data-ref']);
  assert.equal(s.attrs['data-label'].length, 200, 'label capped');
  assert.equal(s.kids.length, 1, 'the label is an attribute value, not a node');
  assert.doesNotMatch(MD_SRC.replace(/\/\/.*$/gm, ''), /innerHTML|insertAdjacentHTML|DOMParser|outerHTML/, 'md.js never parses HTML');
});

test('placeSlots: the same reference first, then the unique name; each item inline OR in the strip, never both', () => {
  const items = [{ mid: mid('A'), name: 'chart.png', ref: './out/chart.png' }, { mid: mid('B'), name: 'report.pdf', ref: 'out/report.pdf' },
    { mid: mid('C'), name: 'voice.wav', ref: 'voice.wav' }, { mid: mid('D'), name: 'dup.png', ref: 'x/dup.png' }, { mid: mid('E'), name: 'dup.png', ref: 'y/dup.png' }];
  const { at, rest, restSkips } = W.placeSlots(['out/chart.png', '/abs/report.pdf', 'z/dup.png', 'y/dup.png'], items, []);
  assert.deepEqual(at, [{ mid: mid('A') }, { mid: mid('B') }, null, { mid: mid('E') }]);
  assert.deepEqual(rest.map((m) => m.mid), [mid('C'), mid('D')], 'the strip: only what no slot shows');
  assert.deepEqual(restSkips, []);
  // an exact reference later in the text wins over a by-name guess earlier
  const two = [{ mid: mid('P'), name: 'a.png', ref: 'out/a.png' }];
  assert.deepEqual(W.placeSlots(['elsewhere/a.png', 'out/a.png'], two).at, [{ mid: mid('P') }, { mid: mid('P') }],
    'exact first; the other reference to the same file shows it again where it was written');
  assert.deepEqual(W.placeSlots(['elsewhere/a.png', 'out/a.png'], two).rest, []);
  assert.deepEqual(W.placeSlots(['<out/a.png>', 'file:///w/out/a.png'], [{ mid: mid('Q'), name: 'a.png', ref: 'out/a.png' }]).at[0], { mid: mid('Q') });
});

test('placeSlots: a refused file shows its note where it was written (and not again under the words)', () => {
  const skips = [{ name: 'huge.mp4', why: 'too_big', bytes: 9 }, { name: '.env', why: 'secret', bytes: 0 }, { name: 'gone.png', why: 'gone', bytes: 0 }];
  const { at, rest, restSkips } = W.placeSlots(['out/huge.mp4', 'never/sent.png', 'gone.png'], [], skips);
  assert.deepEqual(at, [{ skip: 0 }, null, { skip: 2 }]);
  assert.deepEqual(rest, []);
  assert.deepEqual(restSkips, [skips[1]], 'the strip keeps only the notes no slot shows');
  assert.deepEqual(W.placeSlots([], [], skips).restSkips, skips, 'no slots: every note under the words, as before');
  assert.deepEqual(W.placeSlots(['', '   '], [], skips).at, [null, null]);
});

test('media.js: the reader shares the LRU and owns its own object URLs; no markup APIs', () => {
  assert.match(MEDIA_SRC, /export function renderReader\(words, p\)/);
  assert.match(MEDIA_SRC, /export function clearReader\(\)/);
  assert.match(MEDIA_SRC, /const blob = await fetchMedia\(m\)/, 'every card goes through fetchMedia (cache + one flight per mid)');
  assert.match(MEDIA_SRC, /c\._sc\.urls\.push\(c\._url\)/, 'object URLs belong to the scope that made them');
  const code = MEDIA_SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\\'"])\/\/.*$/gm, '$1');
  assert.doesNotMatch(code, /innerHTML|outerHTML|insertAdjacentHTML|DOMParser|\bfetch\s*\(/);
  const relay = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
  assert.match(relay, /fill\(el\("rdWords"\), tx\);\n\s+renderReaderMedia\(el\("rdWords"\), p\);/, 'the reader fills its slots when it opens');
  assert.match(relay, /clearReaderMedia\(\);/, 'and lets go of its object URLs when it closes');
});
