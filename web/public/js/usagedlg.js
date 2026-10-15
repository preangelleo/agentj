// P207 §1 · tap the Weekly / 5H numbers → the standard centred dark dialog (the P150 gdlg / P207 --dlg-* card), not a toast.
// The owner (2026-10-13): 「点了会给个小弹窗……没有用我们标准的那种中间的黑色的那种弹窗……要改回到中间黑色那个弹窗，统一一下」.
// relay.js hands over what it already knows (the quota windows, context %, the age of the numbers); nothing is fetched.
// Esc / a tap outside / 「知道了」 close it (js/layers.js).
import { registerLayer } from './layers.js';
import { t, onLang } from './t.js';
import { el, mk } from './ui.js';

let last = null;          // the last data shown (repainted on a language change)
let opener = null;

function row(label, pct, text) {
  const li = mk('li');
  const k = mk('span'); k.textContent = label;
  li.append(k);
  if (typeof pct === 'number' && Number.isFinite(pct)) {
    const bar = mk('span', 'ub'), i = mk('i');
    i.style.width = Math.max(0, Math.min(100, pct)) + '%';
    bar.append(i);
    const v = mk('b'); v.textContent = Math.round(pct) + '%';
    li.append(bar, v);
  } else {
    li.className = 'txt';
    const v = mk('b'); v.textContent = text || '—'; v.style.textAlign = 'left';
    li.append(v);
  }
  return li;
}

function paint() {
  const d = last;
  if (!d) return;
  const list = el('usageList');
  const rows = d.windows.map((w) => row(w.label, w.pct));
  if (typeof d.ctx === 'number') rows.push(row(t('p207set.usage.ctx'), d.ctx));
  else if (d.ctxText) rows.push(row(t('p207set.usage.ctx'), null, d.ctxText));
  list.replaceChildren(...rows);
  el('usageNone').hidden = !!d.source;
  list.hidden = !d.source;
  el('usageAge').textContent = d.source && d.age != null ? t('r.usage.age', { s: Math.round(d.age) }) : '';
}

/** relay.js: {source, windows: [{label, pct}], ctx: number|null, ctxText: string|null, age: number|null} */
export function openUsage(data, from) {
  last = data;
  opener = from || document.activeElement;
  paint();
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
