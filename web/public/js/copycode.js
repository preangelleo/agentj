// P292: ONE copy behaviour for every <code> / <pre> the page shows (Leo 10-15: 「但凡是这个地方都应该有 copy 按钮，或者点击就能
// copy……在弹窗里边的这些结构，我们迷失掉了」). Every place that makes a code or pre element hands it to copyable(); the static
// rule web/test/p292_copy.test.mjs fails on any that does not, and audit() lists the ones on screen that are not wired.
//
//   copyable(node, { variant, label, click, btn, text }) → the node to insert (the wrapper for inline / pre block)
//     variant 'inline'  <span class="cc"><code data-cc=inline>…</code><button class="cc-btn">⧉</button></span> (compact icon)
//             'block'   a <pre>: <div class="cc cc--block"><pre data-cc=block>…</pre><button></div>, icon top right;
//                       any other element (a dd): the button goes inside it, top right
//             'bare'    no icon: one click on the code copies it (the reply's inline code, P154 — the shade flashes and
//                       `cc:copied` bubbles so the page can say 「已拷贝」)
//             'ext'     the place already has its own copy button (Settings ⧉, 方法二 ⧉, the code block's 拷贝, the approval's
//                       ⧉, the secret card's 复制): a click on the code presses that button — one feedback, never two
//     label   i18n key of the button's aria-label / title (default 'ar.copy' 复制 / Copy)
//     click   false = icon only (long reading text: a memory, a file preview — a tap there is reading, not copying)
//     btn     'ext' only: the button (else data-cc-btn="<id>", the next sibling button, or the code block's .copy)
//     text    a function → the text to copy (default: the node's textContent — the icon adds no text)
//   copyNow(text) → Promise<boolean>   the shared copy (RelayMD.copyText: secure context, else execCommand)
//   audit(root)   → the visible code / pre elements that are not wired (tests; empty = every one can be copied)
//
// A click that ends a text selection never copies (drag / long-press / double-click select stays the system's: ⌘C / Ctrl+C
// work as before, nothing is user-select:none); a code inside a link stays a link. Feedback in place, ~1.4 s: the icon turns
// into ✓ with a small 「已复制」 bubble (r.md.copied), failure says 复制失败 (r.copy.fail); one polite live region announces it.
import { RelayMD } from './md.js';
import { t, plain, onLang } from './t.js';

export const ATTR = 'data-cc';
const SVG = 'http://www.w3.org/2000/svg';
export const DONE_MS = 1500;
const BARE_MS = 900;
const states = new WeakMap();
let installed = false, live = null;
let notifyCopy = () => {};
export function configureCopyNotice(fn) { notifyCopy = fn; }

const label = (k) => plain(t(k));
function glyph(doc) {
  const s = doc.createElementNS(SVG, 'svg');
  for (const [k, v] of [['viewBox', '0 0 24 24'], ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '1.9'], ['stroke-linecap', 'round'], ['stroke-linejoin', 'round'], ['aria-hidden', 'true']]) s.setAttribute(k, v);
  const add = (tag, attrs) => { const e = doc.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); s.appendChild(e); };
  add('rect', { class: 'cc-g0', x: '8.5', y: '8.5', width: '11.5', height: '11.5', rx: '2.5' });
  add('path', { class: 'cc-g0', d: 'M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5' });
  add('path', { class: 'cc-ok', d: 'M5 12.5l4.5 4.5L19 7.5' });
  return s;
}
function relabel(b) { if (b.dataset.done) return; const l = label(b.dataset.ccLabel || 'ar.copy'); b.setAttribute('aria-label', l); b.title = l; }
function makeButton(doc, key) {
  const b = doc.createElement('button');
  b.type = 'button'; b.className = 'cc-btn'; b.dataset.ccLabel = key || 'ar.copy';
  b.appendChild(glyph(doc)); relabel(b);
  return b;
}

/** Wire `node` (a code / pre, or a dd showing a value) — see the header. Idempotent. */
export function copyable(node, opts = {}) {
  if (!node || node.nodeType !== 1) return node;
  const doc = node.ownerDocument || document;
  install(doc);
  const pre = node.tagName === 'PRE';
  const variant = opts.variant || node.getAttribute(ATTR) || (pre ? 'block' : 'inline');
  node.setAttribute(ATTR, variant);
  if (opts.click === false || node.getAttribute('data-cc-click') === '0') node.setAttribute('data-cc-click', '0');
  else if (variant !== 'ext' && !node.title) node.title = label('r.md.tapCopy');
  if (typeof opts.text === 'function') node._ccText = opts.text;
  if (opts.btn) node._ccBtn = opts.btn;
  if (variant === 'bare' || variant === 'ext') return node;
  if (node._ccWrap) return node._ccWrap;
  const b = makeButton(doc, opts.label || node.getAttribute('data-cc-label'));
  b._ccFor = node;
  if (variant === 'block' && !pre) { node.classList.add('cc-in'); node.appendChild(b); node._ccWrap = node; return node; }
  const w = doc.createElement(variant === 'block' ? 'div' : 'span');
  w.className = variant === 'block' ? 'cc cc--block' : 'cc';
  if (node.parentNode) node.parentNode.replaceChild(w, node);
  w.append(node, b);
  node._ccWrap = w;
  return w;
}

/** Static markup ([data-cc] in index.html) and anything inserted before install(): wire what is not wired yet. */
export function upgrade(root = document) {
  for (const n of root.querySelectorAll('[' + ATTR + ']')) if (!n._ccWrap && n.getAttribute(ATTR) !== 'bare' && n.getAttribute(ATTR) !== 'ext') copyable(n);
}

export const copyNow = (text) => (text ? RelayMD.copyText(String(text)) : Promise.resolve(false));

function textOf(node) {
  if (node._ccText) return node._ccText();
  if (node.classList.contains('cc-in')) {                // the button is inside: its (empty) text must not count anyway
    let s = ''; for (const c of node.childNodes) if (!(c.classList && c.classList.contains('cc-btn'))) s += c.textContent; return s;
  }
  return node.textContent;
}
function extButton(node) {
  if (node._ccBtn) return node._ccBtn;
  const id = node.getAttribute('data-cc-btn');
  if (id) return node.ownerDocument.getElementById(id);
  const nx = node.nextElementSibling;
  if (nx && nx.tagName === 'BUTTON') return nx;
  const cb = node.closest('.codeblock');
  return cb ? cb.querySelector('.bar .copy') : null;
}
function say(doc, msg) {
  if (!live || !live.isConnected) {
    live = doc.createElement('span'); live.className = 'cc-live'; live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite');
    doc.body.appendChild(live);
  }
  live.textContent = ''; setTimeout(() => { live.textContent = msg; }, 30);
}
function flash(node, ms) {
  node.classList.remove('copied'); void node.offsetWidth; node.classList.add('copied');
  clearTimeout(node._ccT); node._ccT = setTimeout(() => node.classList.remove('copied'), ms);
}
/** Shared visible feedback. Holds only the original button UI, never the copied value. */
export function copyFeedback(btn, ok) {
  const msg = label(ok ? 'r.md.copied' : 'r.copy.fail');
  if (btn) {
    let state = states.get(btn);
    if (!state) {
      state = { kids: [...btn.childNodes], title: btn.getAttribute('title'), aria: btn.getAttribute('aria-label') };
      states.set(btn, state);
    }
    clearTimeout(state.timer);
    btn.classList.add('cc-feedback');
    btn.dataset.done = ok ? '1' : 'fail'; btn.dataset.tip = msg;
    btn.setAttribute('aria-label', msg); btn.title = msg;
    const caption = btn.ownerDocument.createElement('span'); caption.className = 'cc-result'; caption.textContent = msg; caption.setAttribute('aria-hidden', 'true');
    btn.replaceChildren(glyph(btn.ownerDocument), caption);
    state.timer = setTimeout(() => {
      btn.replaceChildren(...state.kids);
      btn.classList.remove('cc-feedback'); delete btn.dataset.done; delete btn.dataset.tip;
      for (const [key, value] of [['title', state.title], ['aria-label', state.aria]]) {
        if (value === null) btn.removeAttribute(key); else btn.setAttribute(key, value);
      }
      states.delete(btn);
      if (btn.classList.contains('cc-btn')) relabel(btn);
    }, DONE_MS);
  }
  notifyCopy({kind: ok ? 'success' : 'error', shape: 'circle', text: msg, ms: 1800});
  return ok;
}
/** Starts the clipboard write in the tap; optional writer preserves approval / image-copy contracts. */
export async function copyButton(text, btn, writer = copyNow) {
  let ok = false;
  try { ok = !!(await writer(text)); } catch {}
  return copyFeedback(btn, ok);
}

/** Copy `node`'s text and show it; `btn` = its icon (absent for bare). */
export async function copyFrom(node, btn) {
  const variant = node.getAttribute(ATTR);
  if (variant === 'ext') {
    const b = extButton(node);
    if (b && !b.hidden && !b.disabled) { b.click(); return true; }
  }
  const ok = await copyNow(textOf(node));
  if (variant === 'bare') { if (ok) flash(node, BARE_MS); copyFeedback(null, ok); }
  else {
    if (ok) flash(node, DONE_MS);
    const b = btn || (node._ccWrap && node._ccWrap.querySelector('.cc-btn'));
    if (b) copyFeedback(b, ok);
    else copyFeedback(null, ok);
    say(node.ownerDocument, label(ok ? 'r.md.copied' : 'r.copy.fail'));
  }
  node.dispatchEvent(new CustomEvent('cc:copied', { bubbles: true, detail: { ok, variant } }));
  return ok;
}

function selecting(doc) {
  const sel = doc.getSelection && doc.getSelection();
  return !!(sel && !sel.isCollapsed && String(sel).trim());
}
function onClick(e) {
  const tg = e.target && e.target.closest ? e.target : e.target && e.target.parentElement;
  if (!tg || e.defaultPrevented) return;
  const b = tg.closest('.cc-btn');
  if (b) { const n = b._ccFor || b.parentElement.querySelector('[' + ATTR + ']'); if (n) copyFrom(n, b).catch(() => {}); return; }
  if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const w = tg.closest('.cc');                                   // the wrapper between two lines of a wrapped code
  const n = tg.closest('[' + ATTR + ']') || (w && !w.classList.contains('cc--block') && w.querySelector(':scope > [' + ATTR + ']'));
  if (!n || n.getAttribute('data-cc-click') === '0') return;
  const inner = tg.closest('a, button, input, textarea, select, [contenteditable="true"]');
  if (inner && (n.contains(inner) || inner.contains(n))) return;   // a code inside a link stays a link
  if (selecting(n.ownerDocument)) return;                        // the press selected text: that is the owner's copy
  copyFrom(n).catch(() => {});
}

/** Once per document: the delegated click (every wired element, now and later), labels on a language switch, the static ones. */
export function install(doc = typeof document !== 'undefined' ? document : null) {
  if (installed || !doc || !doc.addEventListener) return;
  installed = true;
  doc.addEventListener('click', onClick);
  onLang(() => {
    for (const b of doc.querySelectorAll('.cc-btn')) relabel(b);
    for (const n of doc.querySelectorAll('[' + ATTR + ']:not([' + ATTR + '="ext"]):not([data-cc-click="0"])')) if (n.getAttribute(ATTR) !== 'bare') n.title = label('r.md.tapCopy');
  });
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', () => upgrade(doc), { once: true });
  else upgrade(doc);
}
export const init = () => install();

const shown = (n) => { const r = n.getBoundingClientRect(); if (!r.width || !r.height) return false; const s = getComputedStyle(n); return s.visibility !== 'hidden' && s.display !== 'none'; };
/** Visible code / pre elements on screen that are not wired (a pre whose code is wired, and the code in a wired pre, count as wired). */
export function audit(root = document) {
  const bad = [];
  for (const n of root.querySelectorAll('code, pre')) {
    if (!shown(n)) continue;
    if (n.hasAttribute(ATTR)) continue;
    if (n.closest('[' + ATTR + ']')) continue;                       // inside a wired element (pre > code, highlighted spans)
    if (n.tagName === 'PRE' && n.querySelector('[' + ATTR + ']')) continue;
    bad.push(n);
  }
  return bad;
}
