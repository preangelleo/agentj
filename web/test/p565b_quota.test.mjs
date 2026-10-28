// P565 追加 B (#345): after an upgrade the Claude status line stopped reporting and the phone kept showing the old numbers.
// The host now marks such a reading `expired` (shared.py / quota.py); the phone shows 「额度读数已过期」 instead of n %, an
// empty bar, and the dialog says why (never P433's 「AI 已确认额度用完」).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window ??= {};
globalThis.addEventListener ??= () => {};
if (typeof globalThis.document === 'undefined') globalThis.document = { getElementById: () => null };
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const zh = JSON.parse(read('../i18n/web.zh.json')), en = JSON.parse(read('../i18n/web.en.json'));

test('the expired mark travels from the host message to the bar and the dialog', async () => {
  const { quotaWindows } = await import('../public/js/dashboard.js');
  const at = Math.floor(Date.now() / 1000) - 5 * 3600;
  const w = quotaWindows({ week_pct: 39, five_hour_pct: 14, week_at: at, h5_at: at, week_expired: true, h5_expired: false });
  assert.deepEqual(w.map((x) => [x.window, !!x.expired]), [['weekly', true], ['5h', false]]);
  assert.match(read('../public/js/snap.js'), /h5_expired: !!\(m\.h5 && m\.h5\.expired\), week_expired: !!\(m\.week && m\.week\.expired\)/);
  const { quotaRows } = await import('../public/js/usagedlg.js');
  const node = (tag) => ({ tag, style: { setProperty() {} }, dataset: {}, kids: [], classList: { add() {} }, append(...k) { this.kids.push(...k); } });
  const had = document.createElement;
  document.createElement = node;
  try {
    const small = (r) => r.kids.find((k) => k.tag === 'small')?.textContent || '';
    const soon = Math.floor(Date.now() / 1000) + 3600;
    const s = small(quotaRows({ window: '5h', pct: 14, label: '5H', reset: soon, at, stale: true, expired: true })[0]);
    assert.match(s, /^额度读数已过期/);
    assert.doesNotMatch(s, /读数可能过期/);
  } finally { document.createElement = had; }
});

test('the bar: 「额度读数已过期」 replaces n %, the fill is empty, aria says it too', () => {
  const r = read('../public/js/relay.js');
  assert.match(r, /bar\.firstElementChild\.style\.width = x && !x\.expired \? x\.pct \+ '%' : '0';/);
  assert.match(r, /x\.expired \? `\$\{x\.window\} \$\{t\('p565b\.expired'\)\}`/);
  assert.match(r, /x && x\.expired \? label \+ ' · ' \+ t\('p565b\.expired'\)/);
  assert.match(r, /\.\.\.\(x\.expired \? \{expired: true\} : \{\}\)/);
});

test('words: zh / en / src / override', () => {
  const src = JSON.parse(read('../i18n/web.zh.src.json')), ov = JSON.parse(read('../i18n/web.zh.override.json'));
  assert.equal(zh['p565b.expired'], '额度读数已过期');
  for (const k of ['p565b.expired', 'p565b.expiredWhy']) for (const d of [zh, en, src, ov]) assert.ok(typeof d[k] === 'string' && d[k].length > 1, k);
  assert.match(zh['p565b.expiredWhy'], /agentj doctor/);
  assert.match(en['p565b.expiredWhy'], /agentj doctor/);
});
