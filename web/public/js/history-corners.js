// History corner shortcuts: a click is inert; keyboard/assistive activation is explicit.
// Touch/pen use Pointer Events because native dblclick is not consistent on phones.
export function historyDoubleTap(node, jump, now = () => performance.now()) {
  let down = null, last = null, touchAt = -Infinity;
  const clear = () => { down = null; last = null; };
  node.addEventListener('pointerdown', e => {
    if (e.pointerType === 'mouse') return;
    if (!e.isPrimary) { clear(); return; }
    down = {id: e.pointerId, x: e.clientX, y: e.clientY, t: now()};
  });
  node.addEventListener('pointercancel', clear);
  // Touch emits pointerleave after every completed tap: preserve the pair then.
  node.addEventListener('pointerleave', () => { if (down) clear(); });
  node.addEventListener('pointerup', e => {
    if (e.pointerType === 'mouse' || !down || e.pointerId !== down.id) return;
    const d = down, t = now(); down = null; touchAt = t;
    if (t - d.t > 300 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 12) { last = null; return; }
    if (last && t - last.t <= 350 && Math.hypot(e.clientX - last.x, e.clientY - last.y) <= 24) {
      last = null; e.preventDefault(); jump();
    } else last = {t, x: e.clientX, y: e.clientY};
  });
  node.addEventListener('mousedown', e => { if (e.detail >= 2) e.preventDefault(); });
  node.addEventListener('dblclick', e => {
    e.preventDefault();
    // Ignore compatibility mouse events after a touch pair (one jump only).
    if (now() - touchAt > 700) jump();
  });
  node.addEventListener('keydown', e => {
    if (!e.repeat && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault(); clear(); jump();
    }
  });
  node.addEventListener('blur', clear);
}

// P179: a long press on the source label (touch hold / mouse button held) renames this remote (dev-rename.js).
export const HOLD_MS = 500;
/** A hold of `ms` without moving away calls fn(event). The click that ends a hold does nothing else; double taps are
 *  untouched (history-corners.js ignores taps longer than 300 ms). */
export function longPress(node, fn, ms = HOLD_MS, now = () => performance.now()) {
  let down = null, timer = null, fired = 0;
  const clear = () => { clearTimeout(timer); timer = null; down = null; };
  node.addEventListener('pointerdown', (e) => {
    if (!e.isPrimary || (e.pointerType === 'mouse' && e.button !== 0)) { clear(); return; }
    clear();
    down = { id: e.pointerId, x: e.clientX, y: e.clientY };
    timer = setTimeout(() => { timer = null; down = null; fired = now(); fn(e); }, ms);
  });
  node.addEventListener('pointermove', (e) => {
    if (down && e.pointerId === down.id && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 12) clear();
  });
  for (const ev of ['pointerup', 'pointercancel', 'pointerleave', 'blur']) node.addEventListener(ev, clear);
  // the phone's own long-press menu (copy / callout) must not cover the dialog
  node.addEventListener('contextmenu', (e) => { if (down || now() - fired < 1000) e.preventDefault(); });
  node.addEventListener('click', (e) => { if (now() - fired < 700) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
  // after a touch hold, no compatibility mouse events / click: they would move focus off the dialog's field
  node.addEventListener('touchend', (e) => { if (now() - fired < 700 && e.cancelable) e.preventDefault(); }, { passive: false });
}

