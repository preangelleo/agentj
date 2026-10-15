// The reply reader and file cards share these bounds (interactions: double-tap).
export const DOUBLE_MS = 300, TAP_MS = 350, TAP_PX = 10, PAIR_PX = 32;
/** Only the file surface owns the gesture; actions and long presses keep their owners. */
export function fileDoubleTap(card, open, now = () => performance.now()) {
  let down = null, last = null, touchAt = -Infinity;
  const eligible = e => !e.target.closest('button,a,input,[role=link]');
  const clear = () => { down = last = null; };
  card.addEventListener('pointerdown', e => {
    if (e.pointerType === 'mouse') return;
    if (!e.isPrimary || !eligible(e)) { clear(); return; }
    down = { id:e.pointerId, x:e.clientX, y:e.clientY, t:now() };
  });
  card.addEventListener('pointercancel', clear);
  card.addEventListener('pointerup', e => {
    if (!down || e.pointerId !== down.id) return;
    const d = down, t = now(); down = null;
    if (t-d.t > TAP_MS || Math.hypot(e.clientX-d.x,e.clientY-d.y) > TAP_PX) { last=null; return; }
    if (last && t-last.t <= DOUBLE_MS && Math.hypot(e.clientX-last.x,e.clientY-last.y) <= PAIR_PX) {
      last=null; touchAt=t; e.preventDefault(); e.stopPropagation(); open();
    } else last={t,x:e.clientX,y:e.clientY};
  });
  card.addEventListener('mousedown', e => { if (e.detail >= 2 && eligible(e)) e.preventDefault(); });
  card.addEventListener('dblclick', e => {
    if (!eligible(e)) return;
    e.preventDefault(); e.stopPropagation();
    if (now()-touchAt > 700) open();
  });
  // Compatibility click after the second touch must not toggle the inline video.
  card.addEventListener('click', e => {
    if (now()-touchAt < 700 && eligible(e)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
}
