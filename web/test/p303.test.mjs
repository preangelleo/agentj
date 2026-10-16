// P303 (owner 2026-10-15): WEEKLY 「（第 N 天）」 of the 7-day reset cycle; a faint glow only when it says something — weekly
// ahead of the even pace (≈ 14 %/day), ≥ 80 % Mellow Marrow, ≥ 90 % Peachy Feeling (the amount wins over the pace), 5H the
// 80 / 90 % tiers only; the usage dialog as wide as the input card, with a 「重置周期」 bar and a line per quota. No reset time
// = no day / pace / projection, the amount tiers still apply.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {cycle, allowedPct, fast, tier, projection, span, DAY_S, TIGHT_PCT, EMPTY_PCT} from '../public/js/quotapace.js';

const read = (f) => readFileSync(new URL(f, import.meta.url), 'utf8');
const NOW = Date.UTC(2026, 9, 15, 9, 41) ;                       // ms
const now = NOW / 1000;
const weekAt = (elapsedDays) => now + 7 * DAY_S - elapsedDays * DAY_S;   // the reset time that puts us `elapsedDays` in

test('P303 cycle: day N of 7 from the reset time; 5 h → hour N of 5', () => {
  assert.equal(cycle('weekly', weekAt(0.01), NOW).step, 1);
  assert.equal(cycle('weekly', weekAt(0.99), NOW).step, 1);
  assert.equal(cycle('weekly', weekAt(1.0), NOW).step, 2);
  assert.equal(cycle('weekly', weekAt(2.5), NOW).step, 3);
  assert.equal(cycle('weekly', weekAt(6.99), NOW).step, 7);
  const c = cycle('weekly', weekAt(3.5), NOW);
  assert.equal(c.steps, 7); assert.ok(Math.abs(c.frac - 0.5) < 1e-9); assert.ok(Math.abs(c.left - 3.5 * DAY_S) < 1e-6);
  const h = cycle('5h', now + 3.5 * 3600, NOW);                  // 1.5 h into the 5 h window → hour 2 of 5
  assert.equal(h.step, 2); assert.equal(h.steps, 5);
});

test('P303 cycle: no / bad / passed / too-far reset time → null (graceful degradation)', () => {
  for (const r of [undefined, null, NaN, 0, -5, 'x', now - 1, now + 7 * DAY_S + 3600]) assert.equal(cycle('weekly', r, NOW), null, String(r));
  assert.ok(cycle('weekly', now + 7 * DAY_S + 300, NOW), '5 min of clock skew is tolerated');
  assert.equal(cycle('daily', now + 3600, NOW), null, 'only weekly / 5h have a known period');
  assert.equal(allowedPct(null), null); assert.equal(fast(50, null), null);
});

test('P303 expected use = shown day N / 7 (≈ 14 %/day); over it = fast', () => {
  const d1 = cycle('weekly', weekAt(0.3), NOW), d3 = cycle('weekly', weekAt(2.2), NOW);
  assert.ok(Math.abs(allowedPct(d1) - 100 / 7) < 1e-9);
  assert.ok(Math.abs(allowedPct(d3) - 300 / 7) < 1e-9);
  assert.equal(fast(14, d1), false); assert.equal(fast(15, d1), true);
  assert.equal(fast(42, d3), false); assert.equal(fast(43, d3), true);
  assert.equal(fast(8, d1), false, 'the owner\'s screenshot: 8 % on day 1 is fine');
});

test('P303 tiers: none / fast / tight ≥ 80 / empty ≥ 90; the amount wins over the pace; 5h has no pace tier', () => {
  const d2 = cycle('weekly', weekAt(1.5), NOW), d7 = cycle('weekly', weekAt(6.5), NOW);
  assert.equal(TIGHT_PCT, 80); assert.equal(EMPTY_PCT, 90);
  assert.equal(tier(20, d2), '');
  assert.equal(tier(40, d2), 'fast');
  assert.equal(tier(79.9, d2), 'fast');
  assert.equal(tier(80, d2), 'tight');
  assert.equal(tier(85, d7), 'tight', 'last day, on pace — still tight (independent of the pace)');
  assert.equal(tier(90, d7), 'empty');
  assert.equal(tier(100, null), 'empty', 'no reset time: the amount tiers still apply');
  assert.equal(tier(40, null), '', 'no reset time: no pace glow');
  const h = cycle('5h', now + 4.5 * 3600, NOW);                  // hour 1 of 5, 60 % used = way ahead
  assert.equal(tier(60, h, {pace: false}), '');
  assert.equal(tier(81, h, {pace: false}), 'tight');
  assert.equal(tier(95, h, {pace: false}), 'empty');
  assert.equal(tier(NaN, d2), '');
});

test('P303 projection: % at the reset, or how long until 100 % when that comes first; too early = none', () => {
  const d = cycle('weekly', weekAt(2), NOW);                     // 2 of 7 days gone
  assert.deepEqual(projection(20, d), {atReset: 70});
  const p = projection(40, d);                                   // 20 %/day → 100 % in 3 more days, before the 5 left
  assert.ok(p.emptyIn && Math.abs(p.emptyIn - 3 * DAY_S) < 1);
  assert.equal(projection(0, d), null);
  assert.equal(projection(20, null), null);
  assert.equal(projection(5, cycle('weekly', now + 7 * DAY_S - 600, NOW)), null, 'under an hour in: no extrapolation');
  assert.deepEqual(span(4 * DAY_S + 2 * 3600 + 59), {d: 4, h: 2, m: 0});
  assert.deepEqual(span(2 * 3600 + 13 * 60), {d: 0, h: 2, m: 13});
});

test('P303 wiring: header label + glow, dialog rows, width, palette-only colours', () => {
  const relay = read('../public/js/relay.js'), dlg = read('../public/js/usagedlg.js'), css = read('../public/app.css'), html = read('../public/index.html');
  assert.match(relay, /import \{ cycle, tier \} from '\.\/quotapace\.js'/);
  assert.match(relay, /bar\.dataset\.glow = x \? tier\(x\.pct, c, \{pace: x\.window === 'weekly'\}\)/);
  assert.match(relay, /t\('p303\.day', \{n: c\.step\}\)/);
  assert.match(relay, /reset: (x\.reset \|\| )?quotaReset\(u, x\.window\)/);   // P340 D: a provider window's own reset first
  assert.match(dlg, /t\('p303\.cycle'\)/);
  assert.match(dlg, /fitWidth\(\)/);
  assert.match(html, /class="sq namesq usagesq"/);
  const block = css.slice(css.indexOf('/* P303 (owner'), css.indexOf('/* P207 §2'));
  for (const v of ['--c-peek-a-blue', '--c-mellow-marrow', '--c-peachy-feeling']) assert.ok(block.includes(v), v);
  const pal = read('../public/brand/palette.css');
  assert.match(pal, /--c-peachy-feeling: #d97757/); assert.match(pal, /--c-mellow-marrow: #ebc9b7/); assert.match(pal, /--c-peek-a-blue: #c7dedc/);
  const p303css = css.split('\n').filter((l) => /P303|data-glow|usagesq|usub|\.cyc/.test(l)).join('\n');
  assert.doesNotMatch(p303css, /#[0-9a-f]{3,8}\b/i, 'no raw hex — palette variables only');
});

test('P303 i18n: every p303 key in zh (src, override, built) and en, same placeholders', () => {
  const used = new Set();
  for (const f of ['../public/js/usagedlg.js', '../public/js/relay.js']) for (const m of read(f).matchAll(/p303\.[A-Za-z]+/g)) used.add(m[0]);
  assert.ok(used.size >= 11, String(used.size));
  const D = Object.fromEntries(['web.zh.src', 'web.zh.override', 'web.zh', 'web.en'].map((n) => [n, JSON.parse(read(`../i18n/${n}.json`))]));
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
  for (const k of used) {
    for (const n of Object.keys(D)) assert.equal(typeof D[n][k], 'string', `${k} in ${n}`);
    assert.equal(ph(D['web.zh'][k]), ph(D['web.en'][k]), k);
  }
});
