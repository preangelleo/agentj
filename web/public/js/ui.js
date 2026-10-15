// Small page helpers shared by the modules: element lookup, relay's toast (one line, optional action button), the
// two-step confirm dialog of Agent J's signed writes.
import { fillText } from './t.js';
export const el = (id) => document.getElementById(id);
export function mk(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

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

// ms defaults to a glance; an upload holds its progress toast until it finishes.
export function toast(t, ms) {
  const n = el('toast'); delete n.dataset.sharedFollow; n.classList.remove('act', 'mail-saved');
  // P292: a toast that names a `command` shows it as code with ⧉ (copycode.js) and stays long enough to tap it
  const cmd = /`[^`\n]+`/.test(String(t));
  if (cmd) { fillText(n, t); ms = Math.max(ms || 0, CMD_TOAST_MS); } else n.textContent = t;
  n.classList.toggle('cmd', cmd); n.classList.add('on');
  clearTimeout(n._t); n._t = setTimeout(() => n.classList.remove('on'), ms || 1800);
}
export const CMD_TOAST_MS = 6000;
// P302 #29 (Leo 10-15 12:08, Mac PWA: 「已更新到 0.21.1a1」 never went away): one auto-hide for transient lines.
// flashLine shows `text` in `node` for FLASH_MS, then hides it; `onceKey` records it on this device (localStorage) so a
// reload or reconnect never replays it. Returns false when it was already shown. toast() above is the glance version.
export const FLASH_MS = 5000;
export function flashLine(node, text, onceKey, ms = FLASH_MS) {
  if (!node) return false;
  if (onceKey) { try { if (localStorage.getItem(onceKey)) return false; localStorage.setItem(onceKey, '1'); } catch { /* no storage: show once this page */ } }
  node.textContent = text; node.hidden = false;
  clearTimeout(node._flash); node._flash = setTimeout(() => { node.hidden = true; node._flash = null; }, ms);
  return true;
}
export function toastOff() { const n = el('toast'); clearTimeout(n._t); n.classList.remove('on', 'act', 'mail-saved'); }
// A toast with one action button (undo, restore). Any later toast() replaces it.
export function toastAction(t, label, fn, ms) {
  const n = el('toast'); delete n.dataset.sharedFollow; n.textContent = t; n.classList.remove('mail-saved');
  const b = document.createElement('button'); b.type = 'button'; b.className = 'undo'; b.textContent = label;
  b.addEventListener('pointerdown', (e) => e.preventDefault());          // keep the field's focus
  b.addEventListener('click', () => { toastOff(); fn(); });
  n.appendChild(b); n.classList.remove('cmd'); n.classList.add('on', 'act');
  clearTimeout(n._t); n._t = setTimeout(() => n.classList.remove('on', 'act'), ms || 5000);
}

/** Two-step confirmation for every write (and /clear): → true / false. `danger` = a destructive yes (unbind, unpair);
 *  `no` = an optional label for the other button (P182). */
export function confirmSheet(title, text, yes, danger = false, no) {
  return new Promise((resolve) => {
    el('confirm-title').textContent = title; fillText(el('confirm-text'), text);   /* P292: `agentj pair` in the text → code + ⧉ */ el('confirm-yes').textContent = yes;
    el('confirm-yes').classList.toggle('aj-btn--danger', !!danger);              // P171: unbind / unpair read as destructive
    el('confirm-yes').classList.toggle('aj-btn--primary', !danger);
    const noBtn = el('confirm-no'), noWas = noBtn.textContent;              // P182: an optional label for the other button
    if (no) noBtn.textContent = no;
    const box = el('confirm'), opener = document.activeElement;
    box.hidden = false; box.dataset.modalOpen = '1';
    el('confirm-yes').focus();
    const done = (v) => { if (no) noBtn.textContent = noWas; box.hidden = true; delete box.dataset.modalOpen; el('confirm-yes').onclick = null; el('confirm-no').onclick = null; if(opener?.isConnected&&!opener.closest('[hidden]')) opener.focus({preventScroll:true}); resolve(v); };
    el('confirm-yes').onclick = () => done(true);
    el('confirm-no').onclick = () => done(false);
  });
}
/** P302: a centred one-button notice on the confirm card (why a switch did not change) → resolves when dismissed. */
export function noticeSheet(title, text, ok) {
  const noBtn = el('confirm-no');
  noBtn.hidden = true;
  return confirmSheet(title, text, ok).then((v) => { noBtn.hidden = false; return v; });
}
export const confirmOpen = () => !el('confirm').hidden;
export function closeConfirm() { if (!el('confirm').hidden) el('confirm-no').click(); }

export function human(n) {
  return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(0) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
}
export function stamp() { const d = new Date(); return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(''); }
export function mmss(s) { s = Math.round(s); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
