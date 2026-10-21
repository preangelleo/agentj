// P433 (#196): the phone's quota bar names a per-model cap (Opus / Sonnet / Fable / fast mode) instead of a plain
// 「额度用完」 — the 5H / WEEKLY allowance still has room — and keeps the P314 wording for a used-up account limit.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window ??= {};
globalThis.addEventListener ??= () => {};
if (typeof globalThis.document === 'undefined') globalThis.document = { getElementById: () => null };
const { text } = await import('../public/js/qresume.js');

const read = (p) => JSON.parse(readFileSync(new URL(p, import.meta.url), 'utf8'));
const zh = read('../i18n/web.zh.json'), en = read('../i18n/web.en.json');
const zhSrc = read('../i18n/web.zh.src.json'), zhOv = read('../i18n/web.zh.override.json');

test('dictionaries: p433 keys in zh and en (and the zh source / override), same placeholders', () => {
  for (const k of ['p433.banner', 'p433.bannerTask', 'p433.fast']) {
    for (const [name, d] of [['zh', zh], ['en', en], ['zh.src', zhSrc], ['zh.override', zhOv]]) assert.ok(typeof d[k] === 'string' && d[k].length > 1, `${name} ${k}`);
    assert.equal(zh[k], zhOv[k], 'zh = the override ' + k);
    assert.deepEqual((zh[k].match(/\{\w+\}/g) || []).sort(), (en[k].match(/\{\w+\}/g) || []).sort(), k);
  }
});

test('banner: a per-model cap is named; the account limit keeps the P314 words', () => {
  const at = Math.floor(new Date().setHours(12, 0, 0, 0) / 1000);
  assert.match(text({ id: 'a', scope: 'main', harness: 'claude', at, limit: 'opus' }), /^Opus 专属限额用完，预计 \d\d:\d\d 自动继续$/);
  assert.match(text({ id: 'a', scope: 'task', harness: 'claude', at, limit: 'sonnet', title: '周报' }, 2),
    /^「周报」Sonnet 专属限额用完，预计 \d\d:\d\d 自动重跑（另有 2 项）$/);
  assert.match(text({ id: 'a', scope: 'main', harness: 'claude', at, limit: 'fast' }), /^快速模式 专属限额用完/);
  assert.match(text({ id: 'a', scope: 'main', harness: 'claude', at }), /^额度用完，预计 \d\d:\d\d 自动继续$/);
  assert.match(text({ id: 'a', scope: 'main', harness: 'claude', at, limit: 'session' }), /^额度用完，/, 'not a model: P314 words');
});

// #196 correction (12:17): the owner's Claude page said This week 100 % used — the limit was real, the phone's WEEKLY 39 %
// was an old reading. Each reading carries when it was measured; one a confirmed limit contradicted reads 「读数可能过期」.
test('readings: measured time and the stale mark travel to the bar and the usage dialog', async () => {
  const { quotaWindows } = await import('../public/js/dashboard.js');
  const at = Math.floor(Date.now() / 1000) - 5 * 3600;
  const w = quotaWindows({ week_pct: 39, five_hour_pct: 14, week_at: at, h5_at: at, week_stale: true, h5_stale: false });
  assert.deepEqual(w.map((x) => [x.window, x.at, !!x.stale]), [['weekly', at, true], ['5h', at, false]]);
  assert.deepEqual(quotaWindows({ week_pct: 5 }).map((x) => Object.keys(x).sort()), [['pct', 'window']], 'unknown time: nothing added');
  const { quotaRows } = await import('../public/js/usagedlg.js');
  const node = (tag) => ({ tag, style: { setProperty() {} }, dataset: {}, kids: [], classList: { add() {} },
    append(...k) { this.kids.push(...k); } });
  const had = document.createElement;
  document.createElement = node;
  try {
    const small = (r) => r.kids.find((k) => k.tag === 'small')?.textContent || '';
    const soon = Math.floor(Date.now() / 1000) + 3600;
    assert.match(small(quotaRows({ window: '5h', pct: 14, label: '5H', reset: soon, at, stale: true })[0]), /^读数可能过期/);
    assert.match(small(quotaRows({ window: '5h', pct: 14, label: '5H', reset: soon, at })[0]), /更新于 \d\d:\d\d$/);
    assert.doesNotMatch(small(quotaRows({ window: '5h', pct: 14, label: '5H', reset: soon })[0]), /更新于|过期/);
  } finally { document.createElement = had; }
  for (const k of ['p433.stale', 'p433.updated', 'p433.staleWhy']) {
    for (const [name, d] of [['zh', zh], ['en', en], ['zh.src', zhSrc], ['zh.override', zhOv]]) assert.ok(typeof d[k] === 'string' && d[k].length > 1, `${name} ${k}`);
    assert.deepEqual((zh[k].match(/\{\w+\}/g) || []).sort(), (en[k].match(/\{\w+\}/g) || []).sort(), k);
  }
  const src = (f) => readFileSync(new URL(f, import.meta.url), 'utf8');
  assert.match(src('../public/js/snap.js'), /week_at: measured\(m\.week, m\.source_at\)/, 'the host\'s measured time, not the phone\'s receipt');
  assert.match(src('../public/js/relay.js'), /function readAge\(x[^\n]*\n  if \(x\.stale\) return ' · ' \+ t\('p433\.stale'\);/);
  assert.match(src('../public/js/usagedlg.js'), /if \(w\.stale\) sub\.unshift\(t\('p433\.staleWhy'\)\);/);
});
