// P551 (#316 #318): every confirmation card is drawn above whatever is open — a dialog, a page, the reader, the camera — and
// takes the keyboard focus; when it goes, the focus returns to where it was (an e-mail being typed keeps its words).
// One tier for all of them: CSS `--z-confirm` (app.css); among themselves the latest card is on top (`--ontop-n`), and
// layers.js gives them TOP_RANK so Esc / the remote's Back reach the card first.
//  · the approval / question sheet lives inside the chat stage (its own stacking context). When anything covers the stage
//    it floats: scrim + sheet move to <body> (`.ontop-float`: whole-screen scrim, the card at the bottom) and come home
//    when the card closes or folds.
//  · a modal <dialog> (J Cards editors, the calendar) sits in the browser's top layer, above every z-index, and makes the
//    rest of the page inert — so the card moves INTO that dialog while it is open (fixed position, the viewport is still
//    its frame) and comes home when the card or the dialog closes.
import { focusDraft } from './composer-focus.js';

const SURFACES = '#confirm, .ajmodal.elev, .ajmodal.lqr, .ajmodal.sout, .ajmodal.pkp';   // P561: + 「另一台批准」 (an open ③ card hid it, both sides)
const FOCUSABLE = 'button:not([disabled]),a[href],summary,input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
const state = new Map();            // node → {n, prev, card, homes: [[node, marker]]}
let serial = 0, busy = false, probe = 0;

const $ = (id) => document.getElementById(id);
const shown = (n) => !!n && n.isConnected && !n.hidden && !n.closest('[hidden]') && n.getClientRects().length > 0;
const sheetOpen = () => document.body.dataset.sheet === '1' && !!$('sheet') && !$('sheet').hidden;
const modal = (d) => { try { return d.matches(':modal'); } catch { return d.open; } };   // :modal — Safari 15.6+, Chrome 105+
const topDialog = () => [...document.querySelectorAll('dialog')].filter((d) => d.open && modal(d)).at(-1) || null;

/** The confirmation cards open now, the top one last. */
export function openCards() {
  return [...state.entries()].sort((a, b) => a[1].n - b[1].n).map(([node]) => node);
}
/** The card the owner is looking at (null = none open). relay.js: ⌘↩ approves only when the sheet is this one. */
export const topCard = () => openCards().at(-1) || null;

function primary(node) {
  if (node.id === 'sheet') {
    for (const id of ['apprAllow', 'askSend']) { const b = $(id); if (shown(b) && !b.disabled) return b; }
    for (const box of ['qs', 'frAsk', 'apprBtns']) { const b = $(box)?.querySelector(FOCUSABLE); if (b && shown(b)) return b; }
    return $('sheetClose');
  }
  const pick = node.querySelector('[autofocus],.aj-btn--primary:not([hidden]):not([disabled]),.aj-btn--danger:not([hidden]):not([disabled])');
  return shown(pick) ? pick : [...node.querySelectorAll(FOCUSABLE)].find(shown) || null;
}
function parts(node) {
  if (node.id !== 'sheet') return [node];
  const scrim = node.previousElementSibling?.classList.contains('scrim') ? node.previousElementSibling : document.querySelector('.scrim');
  return scrim ? [scrim, node] : [node];
}
function move(s, node, into) {
  for (const p of parts(node)) {
    if (!s.homes.some(([q]) => q === p)) { const mark = document.createComment('p551 card home'); p.before(mark); s.homes.push([p, mark]); }
    if (p.parentNode !== into) into.append(p);
  }
}
function goHome(s) {
  for (const [p, mark] of s.homes) { if (p.classList.contains('ontop-float')) p.classList.remove('ontop-float'); if (mark.isConnected) mark.replaceWith(p); else mark.remove(); }
  s.homes = [];
}
/** Is the chat stage under something? (probe with the sheet at home; anything that is not the stage itself = covered) */
function stageCovered() {
  const stage = $('stage');
  if (!stage) return true;
  const r = stage.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return true;
  for (const [fx, fy] of [[0.5, 0.5], [0.5, 0.85], [0.15, 0.3]]) {
    const hit = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
    if (!hit || !stage.contains(hit)) return true;
  }
  return false;
}
function place(node, s) {
  const dlg = topDialog();
  if (node.id === 'sheet') {
    if (node.classList.contains('auth-approval')) return;          // auth-overlay.js already lifted it above Settings
    const floated = node.classList.contains('ontop-float');
    if (!floated && !dlg && !stageCovered()) return;                // nothing over the stage: the card stays where it lives
    move(s, node, dlg || document.body);
    for (const p of parts(node)) if (!p.classList.contains('ontop-float')) p.classList.add('ontop-float');   // no-op adds still queue a mutation
    return;
  }
  if (dlg && node.parentNode !== dlg) move(s, node, dlg);
  else if (!dlg && s.homes.length) goHome(s);
}
function focusIn(node, again = false) {
  if (!again && node.contains(document.activeElement)) return;
  const b = primary(node);
  if (b) { try { b.focus({ preventScroll: true }); } catch { /* old browsers */ } }
}
function restore(s) {
  const prev = s.prev;
  if (!prev || !prev.isConnected || !shown(prev) || !prev.matches?.(FOCUSABLE + ',[contenteditable="true"]')) return;
  if (openCards().some((n) => n.contains(document.activeElement))) return;
  if (/^(input|workflow-compose)$/.test(prev.id)) focusDraft(prev, { preventScroll: true });
  else try { prev.focus({ preventScroll: true }); } catch { /* old browsers */ }
}
function stamp(node, n) { for (const p of parts(node)) p.style.setProperty('--ontop-n', String(n)); }

export function sync() {
  if (busy) return;
  busy = true;
  try {
    const now = new Set([...document.querySelectorAll(SURFACES)].filter(shown));
    if (sheetOpen()) now.add($('sheet'));
    for (const [node, s] of [...state]) {
      if (now.has(node)) continue;
      state.delete(node);
      goHome(s);
      for (const p of parts(node)) p.style.removeProperty('--ontop-n');
      restore(s);
    }
    for (const node of now) {
      let s = state.get(node);
      const card = node.dataset.card || '';
      if (!s) {
        s = { n: (serial += 2), prev: document.activeElement, card, homes: [] };
        state.set(node, s);
        stamp(node, s.n);
        place(node, s);
        focusIn(node);
      } else if (s.card !== card) {      // the sheet now shows another card: it is a new confirmation — on top again, focused
        s.card = card;
        if (!node.contains(document.activeElement) && document.activeElement !== document.body) s.prev = document.activeElement;
        s.n = (serial += 2);
        stamp(node, s.n);
        place(node, s);
        if (card) focusIn(node, true);
      } else if (node.id !== 'sheet' || node.classList.contains('ontop-float')) place(node, s);
      // the sheet at home: is something covering the stage now? (layout probe — at most once a frame)
      else if (!probe) probe = requestAnimationFrame(() => { probe = 0; const t = state.get(node); if (t && sheetOpen()) { busy = true; try { place(node, t); } finally { busy = false; } } });
    }
    if (!state.size) serial = 0;
    if (state.size) document.body.dataset.ontop = '1'; else delete document.body.dataset.ontop;
  } finally { busy = false; }
}
// A dialog removed with a card inside it (`dialog.close(); dialog.remove()`) would take the card along. This observer is made when
// the module loads — before layers.js makes its own — so the card is home again before anyone looks it up by id.
const rescue = () => { for (const s of state.values()) if (s.homes.some(([p]) => !p.isConnected)) goHome(s); };
if (typeof MutationObserver !== 'undefined' && typeof document !== 'undefined' && document.body) new MutationObserver(rescue).observe(document.body, { subtree: true, childList: true });
export function initOntop() {
  new MutationObserver(sync).observe(document.body, { subtree: true, childList: true, attributes: true,
    attributeFilter: ['hidden', 'open', 'class', 'data-view', 'data-sheet', 'data-card'] });
  // a modal dialog that closes takes its children with it (some remove themselves): bring a card inside it home first
  document.addEventListener('close', (e) => {
    if (!(e.target instanceof HTMLDialogElement)) return;
    for (const s of state.values()) if (s.homes.length && s.homes.some(([p]) => e.target.contains(p))) goHome(s);
    sync();
  }, true);
  sync();
}
