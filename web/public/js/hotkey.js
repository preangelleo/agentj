// P285 (Leo 10-14 22:1x): the reply card's bottom-left disc is a hot key. P216 made it 「分享成网页报告」; it stays that by
// default, and a hold opens this sheet: what it is bound to, what a tap does, and 「换成别的」 — every command and skill of
// the phone's 「/」 list (relay.js hands them over, same source), searchable, pick → confirm → saved as the user-tier setting
// `ui.card_hot_action` through `pref_set` (the host's schema check; the Agent can do the same with `agentj config set
// ui.card_hot_action <name>`). The icon never changes (Leo); the button's label and this sheet name the binding.
// What a tap does is relay.js's hotTap: a command runs exactly like its row in the ≡ list (/clear still asks first).
import { t, onLang, fillText } from './t.js';
import { el, toast } from './ui.js';
import { registerLayer } from './layers.js';
import { setPref, prefSettable } from './settings.js';

export const HOT_DEFAULT = 'web-page-report';
export const HOT_KEY = 'ui.card_hot_action';
const NAME_RE = /^\/?[A-Za-z0-9][A-Za-z0-9_:.\-]{0,62}$/;   // = the schema's pattern
let name = HOT_DEFAULT, ctx = null, picked = null, query = '';

/** The setting's value → a bare name; anything the schema would refuse falls back to the default. */
export const normal = (v) => (typeof v === 'string' && NAME_RE.test(v) ? v.replace(/^\//, '') : HOT_DEFAULT);
export const hotName = () => name;
/** report (P216's draft) · clear (runs, asks first) · run (one-tap command) · say (sent as typed) · skill (drafted + quoted) */
export function kindOf(n = name) {
  if (n === HOT_DEFAULT) return 'report';
  if (ctx && ctx.run.includes(n)) return n === 'clear' || n === 'new' ? 'clear' : 'run';
  if (ctx && ctx.say.includes(n)) return 'say';
  return 'skill';
}
const what = (n) => t('p285.do.' + kindOf(n), { cmd: '/' + n });

/** The `preferences` broadcast (or a missing key: an older host) → the binding. */
export function setHot(v) {
  const n = normal(v);
  if (n === name) return;
  name = n;
  paintBtn();
  if (ctx) ctx.changed();
  if (isOpen()) paint();
}
function paintBtn() {
  const b = el('webReportBtn');
  if (!b) return;
  const lab = name === HOT_DEFAULT ? t('r.wr.btn') : t('p285.btn', { cmd: '/' + name });
  b.setAttribute('aria-label', lab); b.title = lab; b.dataset.hot = name;
}

const isOpen = () => !el('hotMenu').hidden;
export function openHot() {
  if (isOpen()) return;
  picked = null; query = '';
  el('hotQ').value = '';
  el('hotMenu').hidden = false;
  paint();
  el('hotBody').scrollTop = 0;
  if (ctx.refresh) ctx.refresh().then(() => { if (isOpen()) paint(); }, () => {});   // the host's menu / skills, re-read
}
export function closeHot() { el('hotMenu').hidden = true; picked = null; }

function row(cmd, desc, data) {
  const b = document.createElement('button');
  b.type = 'button'; b.setAttribute('role', 'option');
  Object.assign(b.dataset, data);
  const n = document.createElement('b'); n.textContent = cmd;
  const d = document.createElement('span'); d.textContent = desc;
  b.append(n, d);
  return b;
}
function paint() {
  fillText(el('hotNow'), t('p285.now', { cmd: '/' + name }));
  fillText(el('hotWhat'), what(name));
  const q = query.trim().toLowerCase().replace(/^[/／]/, '');
  const nodes = [];
  if (name !== HOT_DEFAULT && !q) {
    const r = row(t('p285.reset'), t('p285.resetDesc', { cmd: '/' + HOT_DEFAULT }), { hot: HOT_DEFAULT });
    r.classList.add('hotreset'); nodes.push(r);
  }
  const seen = new Set();
  for (const g of ctx.groups()) {
    const rows = g.rows.filter(([cmd, desc]) => {
      const n = cmd.replace(/^\//, '');
      if (seen.has(n) || !NAME_RE.test(cmd)) return false;
      return !q || n.toLowerCase().includes(q) || (desc || '').toLowerCase().includes(q);
    });
    if (!rows.length) continue;
    const h = document.createElement('div'); h.className = 'menugroup'; h.setAttribute('role', 'presentation'); h.textContent = g.label;
    nodes.push(h);
    for (const [cmd, desc] of rows) {
      const n = cmd.replace(/^\//, '');
      seen.add(n);
      const r = row(cmd, desc, { hot: n });
      if (n === name) { r.setAttribute('aria-selected', 'true'); r.classList.add('cur'); const c = document.createElement('i'); c.textContent = t('p285.cur'); r.append(c); }
      nodes.push(r);
    }
  }
  if (!seen.size) {
    const e = document.createElement('p'); e.className = 'hotnone'; e.textContent = t('p285.none'); nodes.push(e);
  }
  el('hotList').replaceChildren(...nodes);
  paintAsk();
}
function paintAsk() {
  const box = el('hotAsk');
  box.hidden = !picked;
  if (!picked) return;
  fillText(el('hotAskT'), t('p285.ask', { cmd: '/' + picked }));
  fillText(el('hotAskW'), what(picked));
  el('hotYes').disabled = false;
}
async function confirmPick() {
  const n = picked;
  if (!n) return;
  if (!prefSettable(HOT_KEY)) {        // an older host: say how to do it on the computer (the sheet stays open)
    fillText(el('hotAskW'), t('p285.oldHost', { cmd: '`agentj config set ' + HOT_KEY + ' ' + n + '`' }));
    el('hotYes').disabled = true;
    return;
  }
  el('hotYes').disabled = true;
  if (await setPref(HOT_KEY, n)) {
    setHot(n);
    closeHot();
    toast(t('p285.saved', { cmd: '/' + n }), 2400, 'success');
  } else el('hotYes').disabled = false;   // setPref already said why (offline / refused)
}

/** relay.js: run (one-tap commands), say (sent as typed), groups() = the 「/」 list in its order, changed() = repaint the row,
 *  refresh() = re-read the host's menu (a promise). */
export function initHot(c) {
  ctx = c;
  paintBtn();
  onLang(() => { paintBtn(); if (isOpen()) paint(); });
  registerLayer({ element: () => el('hotMenu'), open: isOpen, close: closeHot, fallback: () => el('webReportBtn'), rank: 31, trap: true });
  el('hotClose').addEventListener('click', closeHot);
  el('hotNo').addEventListener('click', () => { picked = null; paintAsk(); });
  el('hotYes').addEventListener('click', confirmPick);
  el('hotQ').addEventListener('input', () => { query = el('hotQ').value; paint(); });
  el('hotList').addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('button[data-hot]');
    if (!b) return;
    e.stopPropagation();
    picked = b.dataset.hot === name ? null : b.dataset.hot;
    paintAsk();
    if (picked) el('hotYes').focus({ preventScroll: true });
  });
  const menu = el('hotMenu');
  menu.addEventListener('click', (e) => { if (e.target === menu) closeHot(); });
  // the scrim swallows drags (the reply cards underneath never page)
  menu.addEventListener('touchmove', (e) => { if (!el('hotSheet').contains(e.target) && e.cancelable) e.preventDefault(); }, { passive: false });
  el('hotSheet').addEventListener('touchmove', (e) => e.stopPropagation(), { passive: true });
}
