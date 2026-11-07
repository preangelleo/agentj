// 0.26.9 lane w4-web2 — unit half of the leftover web items (real page: web2_0269.mjs).
//   #432 a code block's copy button is the shared copy glyph (no word); its label stays in aria-label / title.
//   #434 「留在这里」 on a seat move is kept in the stored pairing (asked once, not again after a reload).
//   #430 a background task row's @ puts a mention of that task (who, what, task id) in the composer, shown as one chip.
//   #438 the big-screen remote's focus mark is short-lived and in the card's own text colour.
//   #431 a host notice keeps its reply card (one grey line), never an empty screen under the input card.
//   #442 the recording card says the rule: up to T s → text in the box, longer → a voice attachment.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
globalThis.window = {};
const read = (p) => readFileSync(new URL('../' + p, import.meta.url), 'utf8');
const ZH = JSON.parse(read('i18n/web.zh.json')), EN = JSON.parse(read('i18n/web.en.json'));

// ---- a tiny DOM with SVG, enough for md.js toDOM
class N {
  constructor(tag, ns = null) { this.tag = tag; this.ns = ns; this.kids = []; this.attrs = {}; this.className = ''; this.data = null; this.type = ''; this.title = ''; }
  appendChild(n) { if (n.tag === '#frag') this.kids.push(...n.kids); else this.kids.push(n); return n; }
  append(...ns) { for (const n of ns) this.appendChild(n); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  get textContent() { return this.data !== null ? this.data : this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; if (this.tag === '#text') this.data = String(v); else this.kids.push(doc.createTextNode(v)); }
  all() { const out = []; const st = [...this.kids]; while (st.length) { const n = st.pop(); if (n.tag !== '#text') { out.push(n); st.push(...n.kids); } } return out; }
}
const doc = {
  createElement: (t) => new N(t), createElementNS: (ns, t) => new N(t, ns),
  createTextNode: (s) => { const n = new N('#text'); n.data = String(s); return n; },
  createDocumentFragment: () => new N('#frag'),
};
const { RelayMD } = await import('../public/js/md.js');

test('#432 code block: the copy button is the shared copy glyph, no word; aria-label / title carry the label', () => {
  const was = { ...RelayMD.LABELS };
  try {
    for (const [copy, aria] of [['拷贝', '复制代码'], ['Copy', 'Copy code']]) {
      RelayMD.LABELS.copy = copy; RelayMD.LABELS.copyAria = aria;
      const root = new N('div'); RelayMD.toDOM(RelayMD.parse('```bash\nnpm test\n```'), doc, root);
      const btn = root.all().find((n) => n.tag === 'button' && n.className.split(/\s+/).includes('copy'));
      assert.ok(btn, 'a copy button');
      assert.equal(btn.textContent, '', 'no word on the button (icons have no language)');
      assert.equal(btn.getAttribute('aria-label'), aria);
      assert.equal(btn.title, aria);
      const svg = btn.kids.find((k) => k.tag === 'svg');
      assert.ok(svg && svg.ns === 'http://www.w3.org/2000/svg', 'an SVG glyph');
      assert.deepEqual(svg.kids.map((k) => k.getAttribute('class')), ['cc-g0', 'cc-g0', 'cc-ok'], 'the copycode.js strokes (sheets + tick)');
      assert.equal(svg.getAttribute('aria-hidden'), 'true');
    }
  } finally { Object.assign(RelayMD.LABELS, was); }
  const css = read('public/app.css');
  assert.match(css, /\.codeblock \.copy:has\(>svg\)\{[^}]*width:44px;height:44px/, '44 px target');
});

test('#434 seat move: 「留在这里」 is kept in the stored pairing and dropped when the remote does move', async () => {
  const { moveKey, stayedHere, withStay, movedRecord, STAY_MAX } = await import('../public/js/hostmove.js');
  const k = moveKey('old', 'new');
  const h = { channel: 'old', approved: true };
  assert.equal(stayedHere(h, k), false);
  const h2 = withStay(h, k);
  assert.equal(stayedHere(h2, k), true, 'a reload reads the same record');
  assert.equal(h2.channel, 'old'); assert.equal(h2.approved, true);
  assert.deepEqual(withStay(h2, k).moveStayed, [k], 'once');
  let many = h; for (let i = 0; i < 20; i++) many = withStay(many, moveKey('old', 'n' + i));
  assert.equal(many.moveStayed.length, STAY_MAX);
  const moved = movedRecord(h2, { relay: 'wss://r.example/x', channel: 'new', hostPub: 'p', name: 'n' });
  assert.equal('moveStayed' in moved, false, 'a new computer starts clean');
  const app = read('public/app.js');
  assert.match(app, /stayedHere\(h0, key\)/, 'the move sheet checks the stored answer');
  assert.match(app, /withStay\(h, key\)/, '「留在这里」 writes it');
});

test('#430 busy: a task mention carries who, what and the task id; the chip is @who; the row has an @ button', async () => {
  const busy = await import('../public/js/busy.js');
  const m = busy.mentionOf({ id: 'agentj-busy-7f3a', who: '掌枢', label: 'P160 harness 对等' });
  assert.match(m.text, /^@/);
  assert.ok(m.text.includes('掌枢') && m.text.includes('P160 harness 对等') && m.text.includes('agentj-busy-7f3a'), m.text);
  assert.equal(m.label, '@掌枢');
  const anon = busy.mentionOf({ id: '', who: null, label: 'x'.repeat(200) });
  assert.ok(anon.text.length < 140, 'bounded');
  assert.ok(!/ID/.test(anon.text), 'no id → no id clause');
  assert.deepEqual(busy.mentions(), [], 'nothing mentioned yet');
  const src = read('public/js/busy.js');
  assert.match(src, /li\.append\(atButton\(x\)\)/);
  assert.match(read('public/js/five-pages.js'), /\.\.\.busyMentions\(\)/, 'the composer turns it into a chip');
  assert.match(read('public/app.js'), /mention: \(text\) => relay\.insertText\(text\)/);
  for (const D of [ZH, EN]) for (const k of ['p0269w4.busy.at', 'p0269w4.busy.mention', 'p0269w4.busy.mentionId']) assert.ok(D[k], k);
});

test('#438 remote focus mark: cleared on a timer / pointer, drawn in the card text colour, no white ring or dark band', () => {
  const js = read('public/js/tvremote.js'), css = read('public/app.css');
  assert.match(js, /export const RING_MS=\d+/);
  assert.match(js, /ringTimer=setTimeout\(unmark,RING_MS\)/);
  assert.match(js, /addEventListener\('pointerdown',unmark,true\)/);
  const rule = css.match(/\[data-tv-focus="1"\]\{[^}]*\}/)[0];
  assert.ok(!/#faf9f5|#141414|box-shadow/i.test(rule), rule);
  assert.match(rule, /currentColor/);
});

test('#431 host notice keeps its reply card with one grey line; #442 the recording rule strings exist in both languages', () => {
  const js = read('public/js/relay.js'), css = read('public/app.css');
  assert.ok(!/\.rm\.host-notice>main[^{]*\{display:none/.test(css), 'no rule hides the card');
  assert.match(js, /noticeOnly \? t\('p0269w4\.notice'\)/);
  assert.ok(!/if \(noticeOnly\) el\("words"\)\.replaceChildren\(\)/.test(js));
  for (const D of [ZH, EN]) {
    assert.ok(D['p0269w4.notice']);
    for (const k of ['p0269w4.ptt.ruleText', 'p0269w4.ptt.ruleVoice']) assert.ok(D[k] && D[k].includes('{s}'), k);
  }
  assert.match(js, /p0269w4\.ptt\.ruleVoice' : 'p0269w4\.ptt\.ruleText'/);
});
