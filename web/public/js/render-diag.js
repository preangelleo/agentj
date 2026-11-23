// Render-health counters for the layout diagnostic (blank reply card on one Android phone, 2026-11-22).
// Numbers and fixed words only: no message text, file name, URL, host name, identifier or raw UA ever enters a snapshot,
// and nothing here is stored. No imports, no rendering loop, no timers except one idle-time canary creation.
export const REASONS = ['manual', 'context_lost', 'long_hidden'];
export const AGENTS = ['android-chrome', 'android-webview', 'android-firefox', 'android-other', 'ios-safari', 'ios-other',
  'desktop-chrome', 'desktop-safari', 'desktop-firefox', 'desktop-other', 'other'];
export const ALIVE = ['lt1m', 'lt10m', 'lt1h', 'lt6h', 'lt1d', 'lt3d', 'ge3d'];
export const HIDDEN_MAX = ['none', 'lt1m', 'lt10m', 'lt1h', 'lt6h', 'ge6h'];
export const GL = ['pending', 'ok', 'lost', 'unavailable'];
export const MEMORY = [0.25, 0.5, 1, 2, 4, 8, 16, 32];
export const CACHE = ['none', 'lt1mb', 'lt8mb', 'lt32mb', 'lt64mb', 'ge64mb', 'unknown'];
export const CARDS = ['none', 'lt10', 'lt50', 'lt200', 'lt500', 'lt1000', 'ge1000', 'unknown'];
export const NODES = ['lt500', 'lt1500', 'lt5000', 'lt15000', 'ge15000', 'unknown'];
export const LONG_HIDDEN_MS = 10 * 60_000;   // back in front after this long hidden = one sample
export const AUTO_MAX = 3, AUTO_WINDOW_MS = 3_600_000;   // unattended samples per page per hour
const MIB = 1 << 20, COUNT_MAX = 9999, GL_MAX = 99;

/** v below edges[i] → names[i]; at or past the last edge → the last name. Not a finite, non-negative number → null. */
export function bucket(v, edges, names) {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) return null;
  const i = edges.findIndex((e) => v < e);
  return names[i < 0 ? edges.length : i];
}
/** A coarse browser family from the UA string; the string itself is never returned or kept. */
export function agentClass(ua, touchPoints = 0) {
  if (typeof ua !== 'string') return 'other';
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && touchPoints > 1)) return /CriOS|FxiOS|EdgiOS/.test(ua) || !/Version\/.+Safari\//.test(ua) ? 'ios-other' : 'ios-safari';
  if (/Android/.test(ua)) {
    if (/Firefox\//.test(ua)) return 'android-firefox';
    if (/; wv\)/.test(ua)) return 'android-webview';
    if (/HeyTapBrowser|OppoBrowser|OPR\/|MiuiBrowser|SamsungBrowser|HuaweiBrowser|VivoBrowser|UCBrowser|Quark|MQQBrowser|EdgA\//.test(ua)) return 'android-other';
    return /Chrome\//.test(ua) ? 'android-chrome' : 'android-other';
  }
  if (!/Mozilla\//.test(ua)) return 'other';
  if (/Firefox\//.test(ua)) return 'desktop-firefox';
  if (/Chrome\//.test(ua)) return 'desktop-chrome';
  return /Safari\//.test(ua) ? 'desktop-safari' : 'desktop-other';
}

const fresh = () => ({on: false, startAt: 0, hidden: 0, hiddenSince: null, hiddenMax: 0, gl: 'pending', lost: 0, restored: 0, canvas: null, sent: [], emit: null, now: Date.now});
let S = fresh();

function canary() {
  if (S.canvas || S.gl !== 'pending') return;
  try {
    const c = document.createElement('canvas'); c.width = 1; c.height = 1;   // never attached, never drawn to
    const o = {alpha: false, antialias: false, depth: false, stencil: false, preserveDrawingBuffer: false, powerPreference: 'low-power', failIfMajorPerformanceCaveat: false};
    const g = c.getContext('webgl', o) || c.getContext('experimental-webgl', o);
    if (!g) { S.gl = 'unavailable'; return; }
    c.addEventListener('webglcontextlost', (e) => {
      try { e.preventDefault(); } catch { /* the browser may then never restore it; still counted */ }
      S.gl = 'lost'; S.lost = Math.min(S.lost + 1, GL_MAX); notify('context_lost');
    });
    c.addEventListener('webglcontextrestored', () => { S.gl = 'ok'; S.restored = Math.min(S.restored + 1, GL_MAX); });
    S.canvas = c; S.gl = g.isContextLost() ? 'lost' : 'ok';
  } catch { S.gl = 'unavailable'; }
}
function notify(reason) { try { S.emit?.(reason); } catch { /* a diagnostic never breaks the page */ } }
function onVisibility() {
  const now = S.now();
  if (document.hidden) { if (S.hiddenSince === null) { S.hiddenSince = now; S.hidden = Math.min(S.hidden + 1, COUNT_MAX); } return; }
  if (S.hiddenSince === null) return;
  const span = Math.max(0, now - S.hiddenSince); S.hiddenSince = null; S.hiddenMax = Math.max(S.hiddenMax, span);
  if (span >= LONG_HIDDEN_MS) notify('long_hidden');
}

/** Start counting (once per page). `emit(reason)` hears 'context_lost' and 'long_hidden'; `now` is for tests. */
export function start({emit = null, now = Date.now, defer = true} = {}) {
  if (S.on) return;
  S.on = true; S.emit = emit; S.now = now; S.startAt = now();
  try {
    if (document.hidden) { S.hiddenSince = S.startAt; S.hidden = 1; }
    document.addEventListener('visibilitychange', onVisibility);
    if (!defer) canary();
    else if (typeof requestIdleCallback === 'function') requestIdleCallback(canary, {timeout: 8000});
    else setTimeout(canary, 3000);
  } catch { S.gl = 'unavailable'; }
}
/** One unattended sample may go out now? Records it when it may (≤ AUTO_MAX per AUTO_WINDOW_MS per page). */
export function allow() {
  const now = S.now();
  S.sent = S.sent.filter((at) => now - at < AUTO_WINDOW_MS && at <= now);
  if (S.sent.length >= AUTO_MAX) return false;
  S.sent.push(now); return true;
}
/** The fixed-shape `render` object of a layout sample. `cacheBytes`: the media cache's compressed size, a number. */
export function snapshot(reason, {cacheBytes = null} = {}) {
  const now = S.now(), nav = typeof navigator === 'object' && navigator ? navigator : {};
  const span = S.hiddenSince === null ? S.hiddenMax : Math.max(S.hiddenMax, now - S.hiddenSince);
  let cards = null, nodes = null;
  try { const m = /(\d{1,7})\s*$/.exec(document.getElementById('pg')?.textContent || ''); cards = m ? Number(m[1]) : null; } catch { /* unknown */ }
  try { nodes = document.getElementsByTagName('*').length; } catch { /* unknown */ }
  return {
    reason: REASONS.includes(reason) ? reason : 'manual',
    agent: agentClass(nav.userAgent, nav.maxTouchPoints),
    alive: bucket(S.on ? (now - S.startAt) / 1000 : 0, [60, 600, 3600, 21600, 86400, 259200], ALIVE) || ALIVE[0],
    hidden: S.hidden,
    hiddenMax: span > 0 ? bucket(span / 1000, [60, 600, 3600, 21600], HIDDEN_MAX.slice(1)) || 'none' : 'none',
    gl: S.gl, glLost: S.lost, glRestored: S.restored,
    mem: MEMORY.includes(nav.deviceMemory) ? nav.deviceMemory : null,
    cache: cacheBytes === 0 ? 'none' : bucket(cacheBytes, [MIB, 8 * MIB, 32 * MIB, 64 * MIB], CACHE.slice(1, 6)) || 'unknown',
    cards: cards === 0 ? 'none' : bucket(cards, [10, 50, 200, 500, 1000], CARDS.slice(1, 7)) || 'unknown',
    nodes: bucket(nodes, [500, 1500, 5000, 15000], NODES.slice(0, 5)) || 'unknown',
  };
}
export const AUTO_RETRY_MS = 5000, AUTO_TRIES = 12;
/** The unattended sender's queue. One reason is kept (a lost context outranks a long absence); nothing goes out while the
 *  page is hidden (kept, no timer — `wake()` on the return to the front), while it is not connected or while the owner's
 *  own send is running (tried again every AUTO_RETRY_MS, dropped after AUTO_TRIES), or past the hourly cap (dropped).
 *  `send(reason)` never throws into the page. All of `ready/busy/hidden/send` come from the caller; timers can be faked. */
export function autoQueue({ready, busy, hidden, send, may = allow, setTimer = setTimeout, clearTimer = clearTimeout}) {
  let pending = null, tries = 0, timer = null, sending = false;
  async function flush() {
    clearTimer(timer); timer = null;
    if (!pending || sending) return;
    if (hidden()) return;
    if (!ready() || busy()) { if (++tries <= AUTO_TRIES) timer = setTimer(flush, AUTO_RETRY_MS); else pending = null; return; }
    const reason = pending; pending = null;
    if (!may()) return;
    sending = true;
    try { await send(reason); } catch { /* a diagnostic never surfaces an error */ } finally { sending = false; if (pending) flush(); }
  }
  return {
    push(reason) { if (reason !== 'context_lost' && reason !== 'long_hidden') return; if (pending !== 'context_lost') pending = reason; tries = 0; flush(); },
    wake() { if (pending) { tries = 0; flush(); } },
    pending: () => pending,
  };
}
/** Tests only: forget everything (the listener of a started tracker is removed). */
export function reset() { try { document.removeEventListener('visibilitychange', onVisibility); } catch { /* no document */ } S = fresh(); }
