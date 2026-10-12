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
