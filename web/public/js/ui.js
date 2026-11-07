// Small page helpers shared by the modules: element lookup, the Noir Notification (notify / toast / toastAction), the
// two-step confirm dialog of Agent J's signed writes.
import { initSurfaceOutlines } from './surface-outline.js';
import { fillText } from './t.js';
import { SVG } from './icons.js';
import { configureCopyNotice } from './copycode.js';
import { slotShow } from './cardplace.js';   // 0.26.9 #472: a notice above a docked card scrolls only the reply's slot
export const el = (id) => document.getElementById(id);
export function mk(tag, cls, text, owner, actionContext) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; if (owner) e.setAttribute('data-aj-owner', owner); if (actionContext) e.setAttribute('data-aj-action-context', actionContext); return e; }
// Explicit page-copy slot: the caller supplies the node's own semantic owner.
export function pageCopy(node) { node.setAttribute('data-aj-page-copy', ''); return node; }

// P200 追加 2 (Leo, 2026-10-13): a tapped / clicked button or icon never keeps a ring. Chromium also matches
// :focus-visible when focus moves by script after the last input was a key — typing in the field and then closing a
// dialog put a black ring on the header robots, "random" to the owner. So the page decides itself: <html data-kbd> only
// after a navigation key from a real keyboard (Tab, arrows / Enter / Space / Esc outside a text field); any pointer
// (mouse, touch, pen) clears it. app.css draws rings only under html[data-kbd] — keyboard users still get one.
const NAV_KEYS = new Set(['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', ' ', 'Escape', 'Home', 'End', 'PageUp', 'PageDown']);
const typing = (t) => !!t && (t.isContentEditable || t.tagName === 'TEXTAREA' || (t.tagName === 'INPUT' && !/^(button|checkbox|radio|range|submit|reset|color|file)$/.test(t.type)));
export function kbdKey(e) { return e.key === 'Tab' || (NAV_KEYS.has(e.key) && !e.isComposing && !typing(e.target) && !e.ctrlKey && !e.metaKey && !e.altKey); }
if (typeof document !== 'undefined') {
  const root = document.documentElement;
  addEventListener('keydown', (e) => { if (e.isTrusted && kbdKey(e)) root.dataset.kbd = '1'; }, true);
  addEventListener('pointerdown', () => { delete root.dataset.kbd; }, true);
}

export const CMD_TOAST_MS = 6000;
// P425: once-per-device public-version marker; never stores message content.
export function notifyOnce(spec, onceKey) {
  if (onceKey) { try { if (localStorage.getItem(onceKey)) return false; localStorage.setItem(onceKey, '1'); } catch {} }
  notify(spec);
  return true;
}
// Two placements: centred circle/square for transient status/information; rect inside the reply for execution/actions.
// Colours and actions retain P318's Noir tokens. Held notices keep ms:0 and can be dismissed with Escape.
export const CIRCLE_MAX = 10; // half-width display cells: five Chinese glyphs
export function circleStatus(text) {
  const value = String(text ?? '').trim();
  if(!value||/[\r\n\u2028\u2029]/u.test(value))return false;
  const parts=typeof Intl.Segmenter==='function'?[...new Intl.Segmenter(undefined,{granularity:'grapheme'}).segment(value)].map(x=>x.segment):Array.from(value.normalize('NFC'));
  const wide=/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Extended_Pictographic}\p{Regional_Indicator}\u3000-\u303f\uff01-\uff60\uffe0-\uffe6]/u;
  return parts.reduce((n,part)=>n+(wide.test(part)?2:/^[\p{Mark}\u200d\ufe0f]+$/u.test(part)?0:1),0)<=CIRCLE_MAX;
}
export const NOTIFY_ACCENTS = ['--state', '--emerald-oasis', '--windstorm', '--peachy-feeling', '--magic-carpet'];
export const FORMATS = Object.freeze({
  success:  { accent: '--emerald-oasis',  icon: 'check',    ms: 1800, shape: 'circle' },
  info:     { accent: '--state',      icon: 'info',     ms: 1800, shape: 'circle' },
  warn:     { accent: '--peachy-feeling', icon: 'warn',     ms: 3000, shape: 'rect' },
  error:    { accent: '--peachy-feeling', icon: 'error',    ms: 3000, shape: 'rect' },
  progress: { accent: '--state',   icon: 'progress', ms: 0,    shape: 'rect' },
  confirm:  { accent: '--state',      icon: 'confirm',  ms: 0,    shape: 'rect' },
});
// line icons, 24-box; app.css strokes them with --nt-accent
const ICONS = {
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  info: ['M12 11v6', 'M12 7.5v.01', 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z'],
  warn: ['M12 9v4.5', 'M12 17v.01', 'M10.3 4.2L2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z'],
  error: ['M15 9l-6 6', 'M9 9l6 6', 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z'],
  progress: ['M12 3a9 9 0 1 0 9 9'],
  confirm: ['M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14', 'M12 17.5v.01', 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z'],
};
/** The resolved format of a spec (pure; unit-tested): kind falls back to info, ms 0 = held. */
export function notifyFormat(spec = {}) {
  const kind = Object.prototype.hasOwnProperty.call(FORMATS, spec.kind) ? spec.kind : 'info', f = FORMATS[kind];
  let shape = ['circle', 'square', 'rect'].includes(spec.shape) ? spec.shape : f.shape;
  // Structured/actionable notices retain their reply/page owner. A long A notice stays centred, without truncation.
  if (shape !== 'rect' && ((spec.actions || []).length || spec.title || spec.media)) shape = 'rect';
  else if (shape === 'circle' && !circleStatus(spec.text)) shape = 'square';
  const ms = spec.ms === undefined || spec.ms === null ? f.ms : Math.max(0, +spec.ms || 0);
  return { kind, accent: f.accent, icon: f.icon, shape, ms };
}
function icon(name) {
  const NS = SVG, s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); s.setAttribute('class', 'nt-ico');
  for (const d of ICONS[name] || ICONS.info) { const p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); s.appendChild(p); }
  return s;
}
// P440: execution / actionable notices belong to the current reply, never above the input card.
export function replyNotice(node) {
  // Interactive sheets own the stage overlay; execution notices alone use this slot.
  if (node?.classList.contains('sheet')) return node;
  const host = el('reply-notices');
  if (host && node) host.appendChild(node);
  return node;
}
export function executionToast(text, ms = 3600, kind = 'info') {
  return notify({ text, ms, kind, shape: 'rect' });
}
if (typeof document !== 'undefined') {
  for (const id of ['shared-status', 'version-later', 'setup-banner', 'ar-renew', 'qr-banner', 'rescue-banner', 'estop-banner', 'grant-bar', 'share-notice']) replyNotice(el(id));
}
export function notify(spec) {
  const f = notifyFormat(spec), box = el('toast');
  if (!box) return null;
  delete box.dataset.sharedFollow;
  box.classList.remove('act', 'mail-saved', 'circle', 'square', 'rect', 'glance', 'over-dlg');
  box.classList.toggle('glance', !!spec.glance);
  box.dataset.kind = f.kind; box.style.setProperty('--nt-accent', 'var(' + f.accent + ')');
  box.setAttribute('role', f.kind === 'error' || f.kind === 'warn' ? 'alert' : 'status');
  const t = spec.text == null ? '' : String(spec.text), n = mk('span', 'nt-body'), kids = [icon(f.icon)];
  // P340 #27: a rect may carry a small picture of what it is about (the screenshot it just took) — an <img> the caller built
  if (spec.media && f.shape === 'rect' && spec.media.nodeType === 1) { spec.media.classList.add('nt-media'); kids.push(spec.media); }
  const col = mk('span', 'nt-text');
  if (spec.title && f.shape === 'rect') col.appendChild(mk('b', 'nt-title', spec.title));
  // P292: a toast that names a `command` shows it as code with ⧉ (copycode.js) and stays long enough to tap it
  const cmd = /`[^`\n]+`/.test(t);
  let ms = f.ms;
  if (cmd) { fillText(n, t); ms = ms && Math.max(ms, CMD_TOAST_MS); } else n.textContent = t;
  col.appendChild(n); kids.push(col);
  const acts = (spec.actions || []).filter((a) => a && a.label && typeof a.fn === 'function');
  if (acts.length && f.shape === 'rect') {
    const row = mk('span', 'nt-acts');
    for (const a of acts) {
      const b = document.createElement('button'); b.setAttribute('data-aj-owner', 'aj-notice'); b.type = 'button'; b.className = 'undo'; b.textContent = a.label;
      b.addEventListener('pointerdown', (e) => e.preventDefault());          // keep the field's focus
      b.addEventListener('click', () => { toastOff(); a.fn(b); });
      row.appendChild(b);
    }
    kids.push(row);
  }
  box.replaceChildren(...kids);
  box.classList.add(f.shape);
  // #556: a visible password / confirmation card owns the stage; its results use a centred rounded card above it.
  const dlg = f.shape === 'rect' && document.querySelector('.ajmodal.dlgcard:not([hidden]):not(.cp-docked),#confirm:not([hidden]):not(.cp-docked)');
  if (dlg) { document.body.appendChild(box); box.classList.add('over-dlg'); }
  else if (f.shape === 'rect') {
    // A non-chat page owns its local result card; do not hide errors behind Settings / the terminal.
    const page = [el('term'), el('settings'), el('rd'), ...document.querySelectorAll('.view.sub, #five-page')].find((n) => n && !n.hidden && n.getClientRects().length);
    const local = page && (page.querySelector('.setbox') || page);
    if (local) {
      let area = local.querySelector('.page-notices');
      if (!area) { area = mk('div', 'reply-notices page-notices'); local.appendChild(area); }
      area.appendChild(box);
      box.classList.add('over-dlg'); // #556: page results are centred cards, never a sticky bottom bar.
    } else {
      replyNotice(box);
      // 0.26.9 #472: a card open under the reply card — the notice goes right above it (never below its fold) and into view
      const slot = el('reply-notices'), card = slot && [...slot.children].find((n) => n.classList.contains('cp-docked') && !n.hidden && n.getClientRects().length);
      if (card) { if (box.nextElementSibling !== card) slot.insertBefore(box, card); slotShow(box); }
    }
  }
  else if (box.parentElement && box.parentElement !== document.body) document.body.appendChild(box);
  box.classList.toggle('aj-notice-command', cmd); box.classList.toggle('act', !!acts.length && f.shape === 'rect');
  box.classList.toggle('media', kids.some((k) => k.classList && k.classList.contains('nt-media')));
  if (ms) delete box.dataset.hold; else box.dataset.hold = '1';
  box.classList.add('on');
  fitNotice(box);
  clearTimeout(box._t);
  if (ms) box._t = setTimeout(() => box.classList.remove('on', 'act'), ms);
  return box;
}
// A centred notice may use the viewport centre only while it stays clear of the composer.
// Refit for the software keyboard, a growing draft and rotation; never move focus or scroll the page.
export function noticeBounds(height, protectedTop, noticeHeight) {
  const bottom = Math.max(16, Math.min(height - 16, protectedTop - 12));
  const maxHeight = Math.max(1, bottom - 16), half = Math.min(noticeHeight, maxHeight) / 2;
  return { top: Math.max(16 + half, Math.min(height / 2, bottom - half)), maxHeight };
}
function fitNotice(box = el('toast')) {
  if (!box?.classList.contains('on') || !['circle', 'square', 'mail-saved', 'over-dlg'].some(c => box.classList.contains(c))) return;
  if (typeof window.innerHeight !== 'number') return;
  const view = window.visualViewport, height = view?.height || window.innerHeight, offset = view?.offsetTop || 0;
  const protectedRects = ['tools', 'input'].map(el).filter(n => n && !n.closest('[hidden]') && n.getClientRects().length)
    .map(n => n.getBoundingClientRect()).filter(r => r.height && r.top >= offset && r.top < offset + height);
  const protectedTop = protectedRects.length ? Math.min(...protectedRects.map(r => r.top)) - offset : height;
  const bounds = noticeBounds(height, protectedTop, box.scrollHeight);
  box.style.setProperty('--aj-notice-center-y', `${bounds.top + offset}px`);
  box.style.setProperty('--aj-notice-max-height', `${bounds.maxHeight}px`);
}
if (typeof document !== 'undefined') {
  addEventListener('resize', () => fitNotice());
  window.visualViewport?.addEventListener('resize', () => fitNotice());
  window.visualViewport?.addEventListener('scroll', () => fitNotice());
  if (typeof ResizeObserver !== 'undefined') {
    const watch = new ResizeObserver(() => fitNotice());
    for (const id of ['tools', 'input']) { const n = el(id); if (n) watch.observe(n); }
  }
}
// the glance: kind info (an upload holds its progress line by passing a long ms)
export function toast(t, ms, kind = 'info') { notify({ kind, text: t, ms: ms || FORMATS[kind]?.ms || 1800 }); }
export function toastOff() { const n = el('toast'); if (!n) return; clearTimeout(n._t); delete n.dataset.hold; n.classList.remove('on', 'act', 'mail-saved'); }
// A toast with one action button (undo, restore). Any later toast() replaces it.
export function toastAction(t, label, fn, ms) { notify({ kind: 'confirm', text: t, actions: [{ label, fn }], ms: ms || 5000 }); }
// a held notice is a layer like any other: Esc puts it away (transient ones go by themselves)
if (typeof document !== 'undefined') addEventListener('keydown', (e) => {
  // Modal editors and the focused PTY program own Escape while a recent notice is still visible.
  const program = !e.isComposing && !e.ctrlKey && !e.metaKey && e.target?.closest?.('#term-xt');
  if (document.querySelector('dialog[open]') || program) return;
  const n = e.key === 'Escape' && el('toast');
  if (n && n.classList.contains('on')) { toastOff(); e.preventDefault(); e.stopImmediatePropagation(); }
}, true);

/** Two-step confirmation for every write (and /clear): → true / false. `danger` = a destructive yes (unbind, unpair);
 *  `no` = an optional label for the other button (P182). */
const pkOff = () => { const b = el('confirm-pk'); if (b) { b.hidden = true; b.replaceChildren(); } };
export function confirmSheet(title, text, yes, danger = false, no, details = '') {
  return new Promise((resolve) => {
    el('confirm-title').textContent = title; fillText(el('confirm-text'), text);   /* P292: `agentj pair` in the text → code + ⧉ */ el('confirm-yes').textContent = yes;
    const raw = el('confirm-details'); raw.open = false; raw.hidden = !details;
    pkOff();   // P539: pkassist.pkSheet() fills #confirm-pk after this; every other sheet has none
    el('confirm-raw').textContent = details;
    el('confirm-yes').classList.toggle('aj-action--danger', !!danger);              // P171: unbind / unpair read as destructive
    el('confirm-yes').classList.toggle('aj-action--primary', !danger);
    const noBtn = el('confirm-no'), noWas = noBtn.textContent;              // P182: an optional label for the other button
    if (no) noBtn.textContent = no;
    const box = el('confirm'), opener = document.activeElement;
    box.hidden = false; box.dataset.modalOpen = '1';
    el('confirm-yes').focus();
    const done = (v) => { if (no) noBtn.textContent = noWas; box.hidden = true; raw.open = false; el('confirm-raw').textContent = ''; pkOff(); delete box.dataset.modalOpen; el('confirm-yes').onclick = null; el('confirm-no').onclick = null; if(opener?.isConnected&&!opener.closest('[hidden]')) opener.focus({preventScroll:true}); resolve(v); };
    el('confirm-yes').onclick = () => done(true);
    el('confirm-no').onclick = () => done(false);
  });
}
/** Pure information never requires acknowledgement. Long explanations wrap in the centred rounded square. */
export function noticeSheet(title, text) {
  notify({ kind: 'info', shape: 'circle', glance: true, text: String(text || title).replace(/`/g, ''), ms: 2000 });
  return Promise.resolve();
}
export const confirmOpen = () => !el('confirm').hidden;
export function closeConfirm() { if (!el('confirm').hidden) el('confirm-no').click(); }

export function human(n) {
  return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(0) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
}
export function stamp() { const d = new Date(); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(''); }
export function mmss(s) { s = Math.round(s); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }

// Copy primitives remain usable by pure renderers without importing page event handlers.
configureCopyNotice(spec => notify(spec));

if (typeof document !== "undefined") initSurfaceOutlines();
