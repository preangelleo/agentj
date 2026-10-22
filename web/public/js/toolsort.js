// P340 (#45): Settings → 语言和外观 → 「输入框按钮顺序」. The four buttons above the message box (文件 / 图片 / 拍照 / 麦克风),
// left to right, in a row the owner can rearrange:
//   mouse / pen  drag a button sideways (it follows the pointer, the others make room);
//   touch        hold one for a moment, then drag it (a plain swipe still scrolls the page);
//   tap          tap one to pick it up, then tap where it should go (no hold needed, works with VoiceOver / TalkBack);
//   keyboard     focus a button, ← / → (or ↑ / ↓) move it one place, Home / End to either end.
// Every change shows in the message box at once (js/toolorder.js) and is saved: through `pref_set ui.composer_buttons` when
// the computer knows the key (every paired remote then follows the `preferences` broadcast), otherwise on this device only.
// 「恢复默认」 puts back 文件 · 图片 · 拍照 · 麦克风.
import { t, onLang } from './t.js';
import { el } from './ui.js';
import * as order from './toolorder.js';
import { registerLayer } from './layers.js';

const SAVE_MS = 450;                 // a burst of ← → presses is one save
const HOLD_MS = 320, SLOP_PX = 10, DRAG_PX = 5;
let H = { setPref: async () => false, settable: () => false, explain: () => {} };
let picked = null;                   // the tool a tap picked up (then a tap on another place puts it there)
let drag = null;
let saveTimer = 0, saving = null;

const nameOf = (id) => t('p340e.' + id);
const list = () => el('set-tools');
const chips = () => [...list().querySelectorAll('button[data-tool]')];
const domOrder = () => chips().map((b) => b.dataset.tool);
const buzz = (ms) => { try { if (navigator.vibrate) navigator.vibrate(ms); } catch { /* no vibration */ } };
function say(text) { const live = el('set-tools-live'); live.textContent = ''; if (text) requestAnimationFrame(() => { live.textContent = text; }); }

function icon(id) {
  const src = el(order.TOOL_DOM[id])?.querySelector('svg');
  const svg = src ? src.cloneNode(true) : document.createElement('i');   // the composer's own glyph (always there)
  svg.setAttribute('aria-hidden', 'true');
  svg.removeAttribute('id');
  return svg;
}
function chipLabel(id, i, n) { return t('p340e.chip', { name: nameOf(id), n: i + 1, of: n }); }

/** Draw the row from the current order (never while a finger / pointer is dragging it). */
export function render() {
  if (!list() || drag?.started) return;
  const o = order.order(), had = document.activeElement?.dataset?.tool;
  if (picked && !o.includes(picked)) picked = null;
  const items = o.map((id, i) => {
    const li = document.createElement('li');
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'tschip'; b.dataset.tool = id;
    b.setAttribute('aria-label', chipLabel(id, i, o.length));
    b.setAttribute('aria-pressed', String(picked === id));
    b.setAttribute('aria-keyshortcuts', 'ArrowLeft ArrowRight Home End');
    const num = document.createElement('i'); num.className = 'tsnum'; num.setAttribute('aria-hidden', 'true'); num.textContent = String(i + 1);
    const lab = document.createElement('span'); lab.textContent = nameOf(id); lab.setAttribute('aria-hidden', 'true');
    b.append(num, icon(id), lab);
    li.append(b);
    return li;
  });
  list().replaceChildren(...items);
  list().classList.toggle('picking', !!picked);
  el('set-tools-reset').disabled = order.isDefault(o);
  el('set-tools-local').hidden = H.settable();
  if (had) list().querySelector(`button[data-tool="${had}"]`)?.focus({ preventScroll: true });
}

/** A new order from this device: shown at once, then saved (debounced) to the computer when it takes the key. */
function commit(next, words) {
  const before = order.order();
  if (order.same(order.normalize(next), before)) { render(); return; }
  order.set(next);
  render();
  if (words) say(words);
  order.hold(true);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { saveTimer = 0; save(); }, SAVE_MS);
}
async function save() {
  if (saving) { await saving; }
  if (!H.settable()) { order.hold(false); render(); return; }        // an older computer: this device only
  const want = order.order();
  saving = H.setPref(order.TOOL_KEY, want).then((ok) => { if (!ok) H.explain(); return ok; }).catch(() => false);
  await saving;
  saving = null;
  if (!saveTimer) order.hold(false);   // a later change is still waiting: it saves (and releases) next
}
const place = (id, at) => {
  const o = order.order(), from = o.indexOf(id), n = o.length;
  const to = Math.max(0, Math.min(n - 1, at));
  if (from < 0 || to === from) return;
  commit(order.moved(o, from, to), t('p340e.moved', { name: nameOf(id), n: to + 1, of: n }));
};
export function reset() {
  picked = null;
  if (order.isDefault(order.order())) { render(); return; }
  commit([...order.TOOL_IDS], t('p340e.resetDone'));
}

// ---------------------------------------------------------------- keyboard
function onKey(e) {
  const b = e.target.closest?.('button[data-tool]');
  if (!b || e.altKey || e.ctrlKey || e.metaKey) return;
  const o = order.order(), i = o.indexOf(b.dataset.tool);
  const to = { ArrowLeft: i - 1, ArrowUp: i - 1, ArrowRight: i + 1, ArrowDown: i + 1, Home: 0, End: o.length - 1 }[e.key];
  if (to === undefined) return;      // Esc with a picked-up / dragged button: the layer below (layers.js) puts it down
  e.preventDefault(); e.stopPropagation();
  place(b.dataset.tool, to);
}

// ---------------------------------------------------------------- tap: pick up, tap a place
function onTap(b) {
  const id = b.dataset.tool;
  if (!picked) { picked = id; render(); say(t('p340e.picked', { name: nameOf(id) })); return; }
  if (picked === id) { picked = null; render(); say(t('p340e.dropped')); return; }
  const was = picked; picked = null;
  place(was, order.order().indexOf(id));
  render();
}

// ---------------------------------------------------------------- drag (mouse / pen at once, touch after a hold)
function slots() { return chips().map((c) => { const r = c.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, l: r.left, t: r.top }; }); }
function start() {
  const d = drag;
  d.started = true;
  clearTimeout(d.timer);
  d.before = order.order();
  d.slots = slots();
  const r = d.chip.getBoundingClientRect();
  d.gx = d.x0 - r.left; d.gy = d.y0 - r.top;
  try { d.chip.setPointerCapture(d.pid); } catch { /* the pointer is already gone */ }
  d.chip.classList.add('dragging'); list().classList.add('dragging');
  picked = null;
  for (const c of chips()) c.setAttribute('aria-pressed', 'false');
  if (d.type === 'touch') buzz(20);
}
function follow(x, y) {
  const d = drag, o = domOrder(), from = o.indexOf(d.id);
  let best = from, bd = Infinity;
  d.slots.forEach((s, i) => { const k = Math.hypot(x - s.x, (y - s.y) * 0.5); if (k < bd) { bd = k; best = i; } });
  if (best !== from) {
    // move the OTHER items around the dragged one: moving the dragged node itself would drop its pointer capture
    const li = d.chip.parentElement, items = [...list().children];
    if (best < from) li.after(...items.slice(best, from)); else li.before(...items.slice(from + 1, best + 1));
    chips().forEach((c, i) => { c.querySelector('.tsnum').textContent = String(i + 1); });
  }
  const s = d.slots[domOrder().indexOf(d.id)];
  d.chip.style.setProperty('--dx', `${Math.round(x - d.gx - s.l)}px`);
  d.chip.style.setProperty('--dy', `${Math.round(y - d.gy - s.t)}px`);
}
function finish(ok) {
  const d = drag;
  drag = null;
  if (!d) return;
  clearTimeout(d.timer);
  if (!d.started) return;
  d.chip.classList.remove('dragging'); list().classList.remove('dragging');
  d.chip.style.removeProperty('--dx'); d.chip.style.removeProperty('--dy');
  d.eat = performance.now() + 400;
  eatUntil = d.eat;
  const now = domOrder(), at = now.indexOf(d.id);
  if (ok && !order.same(now, d.before)) commit(now, t('p340e.moved', { name: nameOf(d.id), n: at + 1, of: now.length }));
  else { render(); }
  list().querySelector(`button[data-tool="${d.id}"]`)?.focus({ preventScroll: true });
}
let eatUntil = 0;
function onDown(e) {
  const b = e.target.closest?.('button[data-tool]');
  if (!b || e.button > 0 || !e.isPrimary || drag) return;
  drag = { id: b.dataset.tool, chip: b, pid: e.pointerId, type: e.pointerType || 'mouse', x0: e.clientX, y0: e.clientY, started: false };
  if (drag.type === 'touch') { const me = drag; me.timer = setTimeout(() => { if (drag === me && !me.started) start(); }, HOLD_MS); }
}
function onMove(e) {
  const d = drag;
  if (!d || e.pointerId !== d.pid) return;
  const far = Math.hypot(e.clientX - d.x0, e.clientY - d.y0);
  if (!d.started) {
    if (d.type === 'touch') { if (far > SLOP_PX) { clearTimeout(d.timer); drag = null; } return; }   // a swipe: the page scrolls
    if (far < DRAG_PX) return;
    start();
  }
  e.preventDefault();
  follow(e.clientX, e.clientY);
}

export function configure(hooks) {
  H = { ...H, ...hooks };
  const ul = list();
  if (!ul) return;
  order.paint();
  ul.addEventListener('keydown', onKey);
  ul.addEventListener('pointerdown', onDown);
  ul.addEventListener('pointermove', onMove);
  ul.addEventListener('pointerup', (e) => { if (drag && e.pointerId === drag.pid) finish(true); });
  ul.addEventListener('pointercancel', (e) => { if (drag && e.pointerId === drag.pid) finish(false); });
  ul.addEventListener('lostpointercapture', (e) => { if (drag?.started && e.pointerId === drag.pid) finish(true); });
  // once a held finger is dragging, the page must not scroll under it (non-passive: this is the one place it can be stopped)
  ul.addEventListener('touchmove', (e) => { if (drag?.started) e.preventDefault(); }, { passive: false });
  ul.addEventListener('contextmenu', (e) => { if (e.target.closest?.('button[data-tool]')) e.preventDefault(); });
  ul.addEventListener('click', (e) => {
    const b = e.target.closest?.('button[data-tool]');
    if (!b) return;
    if (performance.now() < eatUntil) { e.preventDefault(); return; }   // the click that ends a drag is not a tap
    onTap(b);
  });
  el('set-tools-reset').addEventListener('click', reset);
  // Esc puts a picked-up button down / cancels a drag before it would close Settings (rank 40)
  let last = null;
  registerLayer({ element: list, open: () => !el('settings').hidden && (!!picked || !!drag?.started), rank: 41, trap: false,
    close: () => { last = picked || drag?.id || null; if (drag) finish(false); picked = null; render(); say(t('p340e.dropped')); },
    fallback: () => (last && list().querySelector(`button[data-tool="${last}"]`)) || el('set-tools-reset') });
  order.onChange(() => { if (!el('settings').hidden) render(); });
  onLang(() => render());
  render();
}
/** settings.js: closing the page drops a picked-up button. */
export function close() { picked = null; if (drag) finish(false); }
export const _debug = () => ({ order: order.order(), picked, dragging: !!drag?.started, saving: !!saving });
