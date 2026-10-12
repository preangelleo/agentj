// P160 (PROTOCOL §22 busy_get / busy_info): one 「在办」 row's read-only card — the computer's detail is display text
// (bounded, plain, known sources only), durations read naturally, the card's strings exist in both languages, and the
// page asks only a computer that announced `busyget`.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
globalThis.window = {};
const { sanitize, sanitizeDetail, dur, DETAIL_POLL_MS } = await import('../public/js/busy.js');
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

test('P160 busy: the detail is bounded plain text from known sources', () => {
  assert.equal(sanitizeDetail(null), null);
  assert.equal(sanitizeDetail('x'), null);
  const d = sanitizeDetail({ src: 'codex', since: 1760000000, last: { text: 'Bash\u0007 · ' + 'x'.repeat(400), at: 5 },
    report: { path: '~/r/REPORT-1.md', head: 'H'.repeat(500), at: -1 } });
  assert.equal(d.src, 'codex'); assert.equal(d.since, 1760000000);
  assert.equal(d.last.text.length, 200); assert.ok(!/\u0007/.test(d.last.text));
  assert.equal(d.report.head.length, 240); assert.equal(d.report.at, null);
  assert.equal(sanitizeDetail({ src: 'evil', last: { text: '' }, report: { path: '' } }).src, null);
  assert.deepEqual(Object.keys(sanitizeDetail({ src: 'unit', last: { text: '' }, report: { path: '' } })).sort(), ['since', 'src']);
  for (const s of ['claude', 'codex', 'opencode', 'unit', 'file', 'workflow']) assert.equal(sanitizeDetail({ src: s }).src, s);
  assert.equal(sanitize({ tasks: [{ id: 'a', since: 1760000000 }] })[0].since, 1760000000, 'the list keeps since for the card');
  assert.equal(sanitize({ tasks: [{ id: 'a', since: 'x' }] })[0].since, null);
  assert.ok(DETAIL_POLL_MS >= 2000 && DETAIL_POLL_MS <= 10000, 'polls gently while open');
});

test('P160 busy: durations', () => {
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  for (const k of ['busy.dur.s', 'busy.dur.m', 'busy.dur.h']) { assert.ok(zh[k] && en[k], k); }
  // t() without a dictionary loaded returns the key; the shape (which key) is what matters here
  assert.match(dur(30), /busy\.dur\.s|不到|under/);
  assert.match(dur(12 * 60 + 5), /busy\.dur\.m|12/);
  assert.match(dur(2 * 3600 + 5 * 60), /busy\.dur\.h|2/);
  assert.match(dur(-5), /busy\.dur\.s|不到|under/);
});

test('P160 busy: the card is in the page once, its strings in both languages, host text through textContent only', () => {
  const html = read('../public/index.html');
  for (const id of ['busyOne', 'busyBack']) assert.equal(html.split(`id="${id}"`).length, 2, id);
  const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));
  const keys = ['busy.detail.open', 'busy.detail.back', 'busy.detail.since', 'busy.detail.last', 'busy.detail.lastNone', 'busy.detail.report',
    'busy.detail.reportNone', 'busy.detail.loading', 'busy.detail.old', 'busy.detail.goneTitle', 'busy.detail.gone',
    ...['claude', 'codex', 'opencode', 'unit', 'file', 'workflow'].map((s) => 'busy.src.' + s)];
  for (const k of keys) { assert.ok(zh[k], 'zh ' + k); assert.ok(en[k], 'en ' + k); }
  const src = read('../public/js/busy.js');
  assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML/.test(src));
  const api = read('../public/js/api.js'), sess = read('../public/js/session.js');
  assert.match(api, /peer\.busyget/, 'asks only a computer with the cap');
  assert.match(sess, /m\.caps\.includes\('busyget'\)/);
});
