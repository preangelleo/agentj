// P204 (0.21, PROTOCOL §29) spend & speed — the host sends a finished page's `use` (snap.js useOf) and, on the load
// card, today's totals. Process folded by default, only the result shows (0.21 principle): one small line in the reply
// card's header, a tap opens the details. A subscription login shows its quota share, never a dollar figure; a number the
// harness did not report is「—」; an estimate from the price table carries「≈」. Nothing here leaves the phone.
import { t } from './t.js';

export { useOf } from './useof.js';

export function ktok(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(Math.round(n));
}
export function secs(s) {
  if (typeof s !== 'number' || !Number.isFinite(s)) return '—';
  if (s < 60) return (s < 10 ? s.toFixed(1) : String(Math.round(s))) + ' s';
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return m < 60 ? `${m} min ${r} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}
export function money(x, est) {
  if (typeof x !== 'number' || !Number.isFinite(x)) return '—';
  return (est ? '≈' : '') + (x < 0.01 ? '$' + x.toFixed(4) : '$' + x.toFixed(2));
}
/** The cost part: subscription → quota share (or 「订阅」), API → dollars (or —). */
export function costText(u) {
  if (u.b === 'sub') return typeof u.q === 'number' ? t('use.quota', { q: u.q.toFixed(1) }) : t('use.sub');
  return money(u.usd, u.est);
}
// A turn the harness reported no numbers for (e.g. a shared session) shows its time only — never a bare 「订阅」 / 「—」.
const measured = (u) => u.i !== null || u.o !== null || u.usd !== null || u.q !== null;
/** The folded line: 「12 s · ≈$0.04」. */
export function useShort(u) {
  if (!u) return '';
  return [secs(u.s), measured(u) ? costText(u) : ''].filter((x) => x && x !== '—').join(' · ');
}
/** The unfolded details: label/value rows (only what was reported). */
export function useRows(u) {
  if (!u) return [];
  const rows = [[t('use.time'), u.tool_s ? t('use.timeTool', { s: secs(u.s), tool: secs(u.tool_s) }) : secs(u.s)]];
  if (u.i !== null || u.o !== null)
    rows.push([t('use.tokens'), t('use.tokensVal', { i: ktok(u.i), o: ktok(u.o), c: ktok((u.cr || 0) + (u.cw || 0)) })]);
  if (u.tps !== null) rows.push([t('use.speed'), t('use.speedVal', { n: u.tps >= 100 ? Math.round(u.tps) : u.tps.toFixed(1) })]);
  if (u.m) rows.push([t('use.model'), u.m]);
  if (measured(u)) rows.push([t(u.b === 'sub' ? 'use.quotaLabel' : 'use.cost'), costText(u) + (u.b !== 'sub' && u.est ? t('use.est') : '')]);
  return rows;
}
/** The load card's line: today's totals (`res.use`). */
export function sanitizeToday(x) {
  if (!x || typeof x !== 'object') return null;
  const n = (v) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0);
  return { n: n(x.n), s: n(x.s), i: n(x.i), o: n(x.o), c: n(x.cr) + n(x.cw), usd: n(x.usd), est: x.est === true, q: n(x.q),
    sub: n(x.sub), noUsd: n(x.no_usd) };
}
export function todayText(d) {
  if (!d || !d.n) return t('use.todayNone');
  const parts = [t('use.todayRuns', { n: d.n }), t('use.tokensVal', { i: ktok(d.i), o: ktok(d.o), c: ktok(d.c) })];
  if (d.n - d.sub > 0) parts.push(d.n - d.sub > d.noUsd ? money(d.usd, d.est) : '—');
  if (d.sub) parts.push(t('use.quota', { q: d.q.toFixed(1) }));
  return parts.join(' · ');
}
/** The status line while working: 「执行工具中 · Bash」 / 「生成中」 (null = say nothing more). */
export function phaseText(p) {
  if (!p) return null;
  if (p.ph === 'tool') return p.tool ? t('use.ph.toolNamed', { tool: p.tool }) : t('use.ph.tool');
  return p.ph === 'gen' ? t('use.ph.gen') : null;
}
