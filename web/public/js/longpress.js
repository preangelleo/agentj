// P181 (owner, 2026-10-12): long-press shortcuts on the chat page's buttons — the robots / dots (send the 「定制主题」
// message), the ≡ menu button (send the 「定制命令」 message), the file button (paste the clipboard's text) and the photo
// button (paste the clipboard's image). A tap keeps doing what it always did; a hold of HOLD_MS with the finger / pointer
// still (≤ SLOP_PX) is the shortcut, and the click that follows it is eaten.
//   `at: 'hold'`    — the action runs the moment the hold is recognised (nothing needs the browser's user gesture);
//   `at: 'release'` — the hold is only armed then (vibrate + `.held`), the action runs on pointerup: Clipboard API reads need
//                     a user activation, and for a finger that is pointerup / touchend, never a timer (iOS Safari then shows
//                     its own 「粘贴」 bubble; Chromium asks once for clipboard permission).
export const HOLD_MS = 550, SLOP_PX = 12, EAT_MS = 800;
const buzz = (ms) => { try { if (navigator.vibrate) navigator.vibrate(ms); } catch (_) { /* no vibration */ } };

export function longPress(node, action, { at = 'hold', ms = HOLD_MS } = {}) {
  let p = null, eatUntil = 0;
  const end = () => { if (p) { clearTimeout(p.timer); node.classList.remove('held'); p = null; } };
  node.addEventListener('pointerdown', (e) => {
    if (e.button > 0 || !e.isPrimary) return;
    end();
    const me = p = { x: e.clientX, y: e.clientY, armed: false };
    me.timer = setTimeout(() => {
      if (p !== me) return;
      me.armed = true;
      buzz(25);
      eatUntil = performance.now() + 60000;     // the click after this hold, whenever the finger lifts
      if (at === 'hold') { end(); action(e); }
      else node.classList.add('held');
    }, ms);
  });
  node.addEventListener('pointermove', (e) => {
    if (p && !p.armed && Math.hypot(e.clientX - p.x, e.clientY - p.y) > SLOP_PX) end();
  });
  node.addEventListener('pointerup', (e) => {
    const was = p;
    end();
    if (eatUntil) eatUntil = performance.now() + EAT_MS;
    if (was && was.armed && at === 'release') action(e);    // inside the user gesture
  });
  node.addEventListener('pointercancel', () => { end(); if (eatUntil) eatUntil = performance.now() + EAT_MS; });
  // after a hold, the finger's lift must not become a tap: no compatibility mousedown (it would take the focus away from a
  // field the action just focused) and no click
  node.addEventListener('touchend', (e) => {
    if (eatUntil && e.cancelable) { e.preventDefault(); eatUntil = performance.now() + 250; }   // no click follows: a new tap soon after is real
  });
  node.addEventListener('contextmenu', (e) => { if (p || eatUntil) e.preventDefault(); });
  // capture on the document: runs before the button's own click listeners, so a hold never also counts as a tap. Any
  // target: when the hold opened a dialog, the release's click lands on its backdrop and must not close it.
  document.addEventListener('click', (e) => {
    if (eatUntil && performance.now() < eatUntil) {
      e.preventDefault(); e.stopImmediatePropagation(); eatUntil = 0;
    }
  }, true);
  return { cancel: end };
}
