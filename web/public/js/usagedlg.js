// P207 §1 · tap the Weekly / 5H numbers → the standard centred dark dialog (the P150 gdlg / P207 --dlg-* card), not a toast.
// The owner (2026-10-13): 「点了会给个小弹窗……没有用我们标准的那种中间的黑色的那种弹窗……要改回到中间黑色那个弹窗，统一一下」.
// relay.js hands over what it already knows (the quota windows, context %, the age of the numbers); nothing is fetched.
// Esc / a tap outside / 「知道了」 close it (js/layers.js).
// P303 (owner 2026-10-15): as wide as the input-message card, as tall as its content; a 「重置周期」 bar (day N of 7, how long
// until the reset, when), a line under each quota (pace, projection), the used part of a bar Peek-a-Blue when it runs ahead
// of the even pace, Mellow Marrow at ≥ 80 %, Peachy Feeling at ≥ 90 % (js/quotapace.js decides).
import { registerLayer } from './layers.js';
import { t, lang, onLang } from './t.js';
import { el, mk } from './ui.js';
import { cycle, tier, allowedPct, projection, span } from './quotapace.js';

let last = null;          // the last data shown (repainted on a language change)
let opener = null;

function row(label, pct, text, glow, sub) {
  const li = mk('li');
  const k = mk('span'); k.textContent = label;
  li.append(k);
  if (typeof pct === 'number' && Number.isFinite(pct)) {
    const bar = mk('span', 'ub'), i = mk('i');
    i.style.width = Math.max(0, Math.min(100, pct)) + '%';
    bar.append(i);
    if (glow) li.dataset.glow = glow;
    const v = mk('b'); v.textContent = Math.round(pct) + '%';
    li.append(bar, v);
  } else {
    li.className = 'txt';
    const v = mk('b'); v.textContent = text || '—'; v.style.textAlign = 'left';
    li.append(v);
  }
  if (sub && sub.length) { const s = mk('small', 'usub'); s.textContent = sub.join(' · '); li.append(s); }
  return li;
}

/** 「还有 4 天 2 小时」 / 「2 小时 13 分」 */
export function spanText(sec) {
  const x = span(sec);
  return x.d ? t('p303.spanD', { d: x.d, h: x.h }) : x.h ? t('p303.spanH', { h: x.h, m: x.m }) : t('p303.spanM', { m: x.m });
}
/** 10月18日 周六 09:00 / Sat, Oct 18, 09:00 — the owner's own clock */
function when(reset) {
  try {
    return new Date(reset * 1000).toLocaleString(lang() === 'en' ? 'en-US' : 'zh-CN',
      { month: 'short', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
  } catch (_) { return ''; }
}

const hhmm = (at) => { const d = new Date(at * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };

/** One quota window → its row + (weekly with a known reset) the 「重置周期」 row right under it. */
export function quotaRows(w, now = Date.now()) {
  const c = cycle(w.window, w.reset, now);
  const glow = tier(w.pct, c, { pace: w.window === 'weekly' });
  const sub = [];
  if (glow === 'empty') sub.push(t('p303.empty'));
  else if (glow === 'tight') sub.push(t('p303.tight'));
  else if (glow === 'fast') sub.push(t('p303.fastD', { a: Math.round(allowedPct(c)) }));
  const pr = projection(w.pct, c);
  // a weekly quota still within the even pace never reads 「会用完」 (early in a day a straight line overshoots; the rule wins)
  if (pr && pr.emptyIn != null) { if (glow || w.window !== 'weekly') sub.push(t('p303.emptyIn', { when: spanText(pr.emptyIn) })); }
  else if (pr) sub.push(t('p303.atReset', { p: pr.atReset }));
  if (c && w.window === '5h') sub.push(t('p303.resetIn', { when: spanText(c.left) }));
  // P433 (#196): when this reading was measured, and 「读数可能过期」 when a confirmed limit contradicted it
  if (w.expired) sub.unshift(t('p565b.expiredWhy'));   // P565 追加 B: the status line stopped; never 「AI 已确认额度用完」
  else if (w.stale) sub.unshift(t('p433.staleWhy'));
  else if (Number.isFinite(w.at) && w.at > 0) sub.push(t('p433.updated', { t: hhmm(w.at) }));
  const out = [row(w.label, w.pct, null, glow, sub)];
  if (c && w.window === 'weekly') {
    const at = when(w.reset);
    out.push(row(t('p303.cycle'), c.frac * 100, null, '',
      [t('p303.cycleDay', { n: c.step, of: c.steps }), t('p303.resetIn', { when: spanText(c.left) }), ...(at ? [at] : [])]));
    out[1].classList.add('cyc');
  }
  return out;
}

function paint() {
  const d = last;
  if (!d) return;
  const list = el('usageList');
  const rows = d.windows.flatMap((w) => quotaRows(w));
  if (typeof d.ctx === 'number') rows.push(row(t('p207set.usage.ctx'), d.ctx, null, tier(d.ctx, null, { pace: false })));
  else if (d.ctxText) rows.push(row(t('p207set.usage.ctx'), null, d.ctxText));
  list.replaceChildren(...rows);
  el('usageNone').hidden = !!d.source;
  list.hidden = !d.source;
  el('usageAge').textContent = d.source && d.age != null ? t('r.usage.age', { s: Math.round(d.age) }) : '';
}

// P303: the card takes the input-message card's width (the source card when it is up, else the deck less its 2 × 12 px)
function fitWidth() {
  const sq = el('usageDlg').querySelector('.sq'), om = el('om'), deck = el('deck');
  let w = om && !om.hidden ? om.getBoundingClientRect().width : 0;
  if (!w && deck) {
    const cs = getComputedStyle(deck);
    w = deck.clientWidth - parseFloat(cs.paddingLeft || 0) - parseFloat(cs.paddingRight || 0) - 24;
  }
  if (w > 200) sq.style.setProperty('--usage-w', Math.round(w) + 'px');
  else sq.style.removeProperty('--usage-w');
}

/** relay.js: {source, windows: [{window, reset, label, pct, at?, stale?}], ctx: number|null, ctxText: string|null, age: number|null} */
export function openUsage(data, from) {
  last = data;
  opener = from || document.activeElement;
  paint();
  fitWidth();
  el('usageDlg').hidden = false;
  el('usageClose').focus({ preventScroll: true });
}
export function closeUsage() {
  const d = el('usageDlg');
  if (!d || d.hidden) return;
  d.hidden = true;
  if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus({ preventScroll: true });
}
export const usageOpen = () => !!el('usageDlg') && !el('usageDlg').hidden;

export function initUsage() {
  const d = el('usageDlg');
  if (!d || d.dataset.wired) return;
  d.dataset.wired = '1';
  d.addEventListener('click', (e) => { if (e.target === d) closeUsage(); });
  el('usageClose').addEventListener('click', closeUsage);
  registerLayer({ element: () => d, open: usageOpen, close: closeUsage, fallback: () => el('mWeek'), rank: 5 });
  onLang(() => { if (usageOpen()) paint(); });
}
