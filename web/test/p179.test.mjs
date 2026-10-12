// P179: the long press on the message card's source label (history-corners.js longPress) — unit rules.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { longPress, historyDoubleTap, HOLD_MS } from '../public/js/history-corners.js';

function fixture() {
  const handlers = {}, node = { addEventListener(k, f) { (handlers[k] ??= []).push(f); } };
  let t = 1000, holds = 0, jumps = 0;
  const timers = [];
  const realSet = globalThis.setTimeout, realClear = globalThis.clearTimeout;
  globalThis.setTimeout = (f, ms) => { const x = { f, at: t + ms, live: true }; timers.push(x); return x; };
  globalThis.clearTimeout = (x) => { if (x) x.live = false; };
  longPress(node, () => holds++, HOLD_MS, () => t);
  historyDoubleTap(node, () => jumps++, () => t);
  const restore = () => { globalThis.setTimeout = realSet; globalThis.clearTimeout = realClear; };
  const advance = (n) => { t += n; for (const x of timers) if (x.live && x.at <= t) { x.live = false; x.f(); } };
  const fire = (type, patch = {}) => {
    let prevented = false, stopped = false;
    const e = { isPrimary: true, pointerType: 'touch', pointerId: 1, button: 0, clientX: 10, clientY: 10, cancelable: true,
      preventDefault() { prevented = true; }, stopImmediatePropagation() { stopped = true; }, ...patch };
    for (const f of handlers[type] ?? []) f(e);
    return { prevented, stopped };
  };
  return { fire, advance, holds: () => holds, jumps: () => jumps, restore };
}

test('P179 hold ≥ 500 ms opens once; shorter, moved, cancelled or extra-finger presses do not', () => {
  const f = fixture();
  try {
    f.fire('pointerdown'); f.advance(HOLD_MS - 1); f.fire('pointerup'); assert.equal(f.holds(), 0, 'short');
    f.fire('pointerdown'); f.advance(100); f.fire('pointermove', { clientX: 40 }); f.advance(600); assert.equal(f.holds(), 0, 'moved away');
    f.fire('pointerdown'); f.advance(100); f.fire('pointermove', { clientX: 15 }); f.advance(400); assert.equal(f.holds(), 1, 'a small wobble still holds');
    f.fire('pointerup');
    for (const end of ['pointercancel', 'pointerleave', 'blur']) { f.fire('pointerdown'); f.advance(100); f.fire(end); f.advance(600); }
    assert.equal(f.holds(), 1, 'cancel / leave / blur');
    f.fire('pointerdown', { isPrimary: false, pointerId: 2 }); f.advance(600); assert.equal(f.holds(), 1, 'second finger');
    f.fire('pointerdown', { pointerType: 'mouse', button: 2 }); f.advance(600); assert.equal(f.holds(), 1, 'right mouse button');
    f.fire('pointerdown', { pointerType: 'mouse' }); f.advance(600); assert.equal(f.holds(), 2, 'left mouse button held');
  } finally { f.restore(); }
});

test('P179 the hold swallows its own click / touchend / context menu; the double tap still jumps', () => {
  const f = fixture();
  try {
    f.fire('pointerdown'); f.advance(600); f.fire('pointerup');
    assert.equal(f.holds(), 1);
    assert.equal(f.fire('touchend').prevented, true, 'no compatibility mouse events after the hold (focus stays in the dialog)');
    assert.equal(f.fire('contextmenu').prevented, true, 'no phone context menu');
    const c = f.fire('click'); assert.ok(c.prevented && c.stopped, 'the hold\'s click does nothing else');
    assert.equal(f.jumps(), 0, 'a hold is never half of a double tap');
    f.advance(1000);
    assert.equal(f.fire('touchend').prevented, false, 'later touches are untouched');
    f.fire('pointerdown'); f.advance(50); f.fire('pointerup'); f.advance(80); f.fire('pointerdown'); f.advance(50); f.fire('pointerup');
    assert.equal(f.jumps(), 1, 'double tap → first message');
    assert.equal(f.holds(), 1, 'and no rename dialog');
  } finally { f.restore(); }
});
