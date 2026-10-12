// P154 追加 12 · hold the grey (empty) Send button → a vertical volume slider grows up out of it. The same setting as
// Settings' 「提示音音量」 (js/sfx.js: phone-local, live, persisted). Leo, 2026-10-12: 「长按……原地往上长出来一个音量控制按钮」.
// - Only while the message box is empty and Send is grey; with text, a press of Send is unchanged.
// - Hold ~HOLD_MS (finger or mouse) → open; keep the finger down and slide up/down to set it, let go = done. The slide is
//   relative (the finger starts on Send, below the track): one track height of travel = 0 → 100 %.
//   A hold released without sliding leaves it open: tap / drag the slider, then a tap outside or Esc closes it.
// - The slider is focused when it opens: ↑ / ↓ set it from the keyboard (native range input), Esc closes.
// - One preview whoosh once the level settles (not one per pixel); none at 0 %.
// Send stays disabled (a click never sends): the press is read on the document (capture), against Send's box, because
// browsers do not all deliver pointer events to a disabled button.
import * as sfx from './sfx.js';
import { t } from './t.js';

export const HOLD_MS = 450;
const SLOP_PX = 10, PREVIEW_MS = 220;

let send = null, input = null, pop = null, range = null, val = null;
let press = null, drag = null, previewT = 0;

const isOpen = () => !!pop && !pop.hidden;
const inside = (r, x, y) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
const armed = () => !!send && send.disabled && !(input && input.value.trim()) && !isOpen();

function paint() {
  const v = sfx.volume();
  range.value = String(v);
  range.style.setProperty('--p', v + '%');
  val.textContent = v ? v + '%' : t('set.sfxOff');
  range.setAttribute('aria-valuetext', val.textContent);
}
function set(v, hear = true) {
  sfx.setVolume(v);
  paint();
  clearTimeout(previewT);
  if (hear) previewT = setTimeout(() => { if (sfx.volume() > 0) sfx.preview(); }, PREVIEW_MS);
}
function build() {
  pop = document.createElement('div');
  pop.className = 'volpop';
  pop.id = 'volPop';
  pop.hidden = true;
  val = document.createElement('output');
  val.className = 'vv';
  range = document.createElement('input');
  Object.assign(range, { type: 'range', min: '0', max: '100', step: '5' });
  const vi = document.createElement('span');
  vi.className = 'vi';
  vi.setAttribute('aria-hidden', 'true');
  const NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round' })) svg.setAttribute(k, v);
  for (const d of ['M11 5 6 9H3v6h3l5 4z', 'M15.5 8.5a5 5 0 0 1 0 7']) {
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
  }
  vi.appendChild(svg);
  pop.append(val, range, vi);
  range.addEventListener('input', () => set(range.value));
  range.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); } });
  document.body.appendChild(pop);
}
function place() {
  const r = send.getBoundingClientRect();
  pop.style.left = Math.round(r.left + r.width / 2) + 'px';
  pop.style.bottom = Math.round(innerHeight - r.top + 8) + 'px';
}
export function open() {
  if (!pop) build();
  range.setAttribute('aria-label', t('set.sfx'));
  paint();
  place();
  pop.hidden = false;
  sfx.unlock();                                   // inside the gesture: iOS lets the preview play
  try { if (navigator.vibrate) navigator.vibrate(20); } catch (_) {}
  try { range.focus({ preventScroll: true }); } catch (_) {}
  document.body.dataset.volpop = '1';
}
export function close(refocus = false) {
  if (!isOpen()) return;
  pop.hidden = true;
  drag = null;
  delete document.body.dataset.volpop;
  if (refocus && input) try { input.focus({ preventScroll: true }); } catch (_) {}
}
/** The finger at clientY, relative to where the slide began → a level (up = louder; one track height = 100 %). */
function levelAt(y) {
  const h = range.getBoundingClientRect().height;
  if (!h) return sfx.volume();
  return Math.round((drag.v0 + ((drag.y - y) / h) * 100) / 5) * 5;
}

function down(e) {
  if (isOpen()) {
    if (!pop.contains(e.target)) { close(); if (send && inside(send.getBoundingClientRect(), e.clientX, e.clientY)) e.preventDefault(); }
    return;
  }
  if (e.button > 0 || !armed() || !inside(send.getBoundingClientRect(), e.clientX, e.clientY)) return;
  e.preventDefault();                             // no text selection / iOS callout / focus loss on a hold
  press = { id: e.pointerId, x: e.clientX, y: e.clientY, t: setTimeout(() => {
    if (!press) return;
    open();
    drag = { id: press.id, moved: false, y: press.y, v0: sfx.volume() };
    press = null;
  }, HOLD_MS) };
}
function move(e) {
  if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > SLOP_PX) { clearTimeout(press.t); press = null; }
  if (drag && e.pointerId === drag.id) {
    if (!drag.moved && Math.abs(e.clientY - drag.y) < 4) return;
    if (!drag.moved) { drag.moved = true; drag.y = e.clientY; drag.v0 = sfx.volume(); }
    e.preventDefault();
    set(levelAt(e.clientY));
  }
}
function up(e) {
  if (press && e.pointerId === press.id) { clearTimeout(press.t); press = null; }
  if (drag && e.pointerId === drag.id) {
    const slid = drag.moved;
    drag = null;
    if (slid) close();                            // hold-and-slide: letting go is done
  }
}

/** Wire once: the Send button and the message box. */
export function attach(sendBtn, field) {
  send = sendBtn; input = field;
  document.addEventListener('pointerdown', down, true);
  document.addEventListener('pointermove', move, { capture: true, passive: false });
  document.addEventListener('pointerup', up, true);
  document.addEventListener('pointercancel', up, true);
  // a finger hold must not turn into a page pan (the browser would cancel the pointer mid-slide)
  document.addEventListener('touchmove', e => { if (press || drag) e.preventDefault(); }, { capture: true, passive: false });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && isOpen()) { e.preventDefault(); e.stopPropagation(); close(true); } }, true);
  // no long-press menu on the grey button (Android / desktop right-click after a hold)
  send.addEventListener('contextmenu', e => { if (send.disabled) e.preventDefault(); });
  addEventListener('resize', () => { if (isOpen()) place(); });
}

export const _debug = () => ({ open: isOpen(), value: range ? +range.value : null, volume: sfx.volume() });
