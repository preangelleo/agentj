// P340 (#45, Leo 10-16: 「麦克风放最左边，左手用的时候方便」): the order, left to right, of the four buttons above the message
// box. The default is today's order. The owner changes it in Settings → 语言和外观 (js/toolsort.js). A host that knows
// `ui.composer_buttons` stores it and sends it to every paired remote in each `preferences` broadcast. With an older host,
// the order stays on this device only (ORDER_KEY = "aj.toolOrder", the four ids as JSON, nothing else).
// Pure bookkeeping + one DOM move (the same four nodes re-appended, so their listeners and states stay as they are).
export const TOOL_KEY = 'ui.composer_buttons';
export const TOOL_IDS = Object.freeze(['file', 'image', 'camera', 'mic']);
export const TOOL_DOM = Object.freeze({ file: 'tDoc', image: 'tPhoto', camera: 'tCamera', mic: 'mic' });
const ORDER_KEY = 'aj.toolOrder';

/** true when `v` is the four ids, each exactly once (the host's `preferences.composer_order_ok`). */
export function isOrder(v) {
  return Array.isArray(v) && v.length === TOOL_IDS.length && v.every((x) => typeof x === 'string' && TOOL_IDS.includes(x))
    && new Set(v).size === TOOL_IDS.length;
}
/** a valid order as a fresh array; anything else (missing, a duplicate, an unknown id, not a list) → the default. */
export const normalize = (v) => (isOrder(v) ? [...v] : [...TOOL_IDS]);
export const isDefault = (v) => normalize(v).every((x, i) => x === TOOL_IDS[i]);
export const same = (a, b) => isOrder(a) && isOrder(b) && a.every((x, i) => x === b[i]);
/** the order with the button at index `from` taken out and put back at index `to` (both clamped). */
export function moved(order, from, to) {
  const o = normalize(order), n = o.length;
  const f = Math.max(0, Math.min(n - 1, from | 0)), t = Math.max(0, Math.min(n - 1, to | 0));
  const [x] = o.splice(f, 1);
  o.splice(t, 0, x);
  return o;
}

function readLocal() {
  try { const raw = localStorage.getItem(ORDER_KEY); return raw ? normalize(JSON.parse(raw)) : [...TOOL_IDS]; } catch { return [...TOOL_IDS]; }
}
function writeLocal(o) {
  try { localStorage.setItem(ORDER_KEY, JSON.stringify(normalize(o))); } catch { /* blocked storage: this visit only */ }
}

let cur = readLocal();
const subs = [];
export const order = () => [...cur];
export function onChange(fn) { subs.push(fn); }

/** Re-append the four buttons in `cur` order inside their row (nothing else in the row moves). */
export function paint(doc = globalThis.document) {
  if (!doc) return;
  const nodes = cur.map((id) => doc.getElementById(TOOL_DOM[id]));
  if (nodes.some((n) => !n)) return;
  const row = nodes[0].parentElement;
  if (nodes.some((n) => n.parentElement !== row)) return;
  const now = [...row.children].filter((n) => nodes.includes(n));
  if (now.every((n, i) => n === nodes[i])) return;
  const had = doc.activeElement;
  for (const n of nodes) row.append(n);
  if (had && nodes.includes(had) && doc.activeElement !== had) had.focus({ preventScroll: true });
  row.dataset.order = cur.join(' ');
}
/** This device's choice (Settings): shown at once and kept locally; the caller sends it to the host when it can. */
export function set(o) {
  const next = normalize(o);
  const changed = !same(next, cur);
  cur = next;
  writeLocal(cur);
  paint();
  if (changed) for (const f of subs) { try { f(order()); } catch { /* one bad listener must not stop the rest */ } }
  return order();
}
/** Every `preferences` broadcast: a host that has the key decides (an invalid value → the default); a host without it
 *  (absent = older than 0.23.0) leaves this device's own order alone. */
export function fromHost(prefs) {
  const ui = prefs && typeof prefs === 'object' ? prefs.ui : null;
  if (!ui || typeof ui !== 'object' || !Object.prototype.hasOwnProperty.call(ui, 'composer_buttons')) { paint(); return false; }
  if (held) { late = normalize(ui.composer_buttons); return true; }
  set(ui.composer_buttons);
  return true;
}
// While this device is still saving its own change, a broadcast from an earlier save must not flick the row back.
let held = false, late = null;
export function hold(on) {
  held = !!on;
  if (!held && late) { const o = late; late = null; set(o); }
}
/** tests: drop the in-memory copy and re-read storage. */
export function _reload() { cur = readLocal(); return order(); }
