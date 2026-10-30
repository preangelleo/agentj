// P575 (#390) · the state-change sounds and the 「全部完成」 sound. The page IS the state colour (body[data-conn] /
// [data-status] / [data-kind], app.css): a change of colour plays that colour's short cue; the main Agent back to
// idle after background work with nothing left in 在办 (busy.js) plays the longer chord instead — one sound per change, never stacked.
// Volume: the one 提示音音量 of js/sfx.js (the Send whoosh's slider, long-press on Send); no volume of its own here.
// Switches: one per sound, phone-local (localStorage, never sent anywhere); all on by default. A switched-off sound is
// skipped, so the next one that applies plays instead (all-done off → the green tick).
// This module is pure (no DOM, no audio): createCues() takes a clock, timers, the player and "is the page visible", so the
// node tests drive it with a fake clock. relay.js / busy.js feed it; sfx.js plays.
export const CUE_IDS = ['idle', 'working', 'waiting', 'question', 'offline', 'all-done'];
export const STATE_CUES = CUE_IDS.slice(0, 5);
export const SRC = Object.fromEntries(CUE_IDS.map((id) => [id, `assets/cue-${id}.mp3`]));
export const OFF_KEY = 'aj.cueOff';          // comma list of the switched-off ids; absent = all on
// A colour has to stay SETTLE_MS before it sounds (a flicker blue → green → blue between two tool calls is not 「做完了」);
// a run of changes is merged into the last one, but never waits longer than MAX_WAIT_MS. The same sound again within
// REPEAT_MS is dropped (an approval card's popup and the orange it brings are one sound).
export const SETTLE_MS = 400, MAX_WAIT_MS = 2000, REPEAT_MS = 2000;
// 「全部完成」 requires background tasks observed this turn, all ended, and main Agent idle.
// A plain reply ends on the green cue.
export const ALL_DONE_NEEDS_TASKS = true;

let off = new Set();
try {
  const raw = localStorage.getItem(OFF_KEY);
  if (raw) off = new Set(raw.split(',').filter((id) => CUE_IDS.includes(id)));
} catch { /* blocked storage: all on */ }

export const enabled = (id) => CUE_IDS.includes(id) && !off.has(id);
export function setEnabled(id, on) {
  if (!CUE_IDS.includes(id)) return false;
  if (on) off.delete(id); else off.add(id);
  try { if (off.size) localStorage.setItem(OFF_KEY, [...off].join(',')); else localStorage.removeItem(OFF_KEY); } catch { /* this visit only */ }
  return enabled(id);
}

/** The page colour as a cue id: grey while not connected or the state is unknown. Same rules as app.css. */
export function colorOf(s) {
  if (!s || !s.conn) return 'offline';
  if (s.status === 'idle') return 'idle';
  if (s.status === 'working') return 'working';
  if (s.status === 'waiting') return s.kind === 'question' ? 'question' : 'waiting';
  return 'offline';
}
const isDone = (s) => !!s.conn && s.status === 'idle' && !(s.tasks > 0);
const isWork = (s) => !!s.conn && (s.status === 'working' || s.status === 'waiting' || s.tasks > 0);

/**
 * deps: now() ms · setTimer(fn, ms) → handle · clearTimer(handle) · play(id, onStarted) → accepted bool; onStarted() only after actual playback begins · visible() → bool · on(id) → bool.
 * Returns {set(partial), popup(id), flush()}: set merges {conn, status, kind, tasks} into what it knows.
 */
export function createCues(deps) {
  const d = { now: () => Date.now(), setTimer: (f, ms) => setTimeout(f, ms), clearTimer: (h) => clearTimeout(h),
    play: () => false, visible: () => true, on: enabled, ...deps };
  const s = { conn: false, status: 'unknown', kind: null, tasks: 0 };
  let armed = false;          // the first settled, connected, known state is the baseline: a (re)load is silent
  let shown = 'offline';      // the colour the last settle saw
  let beforeOff = null;       // the colour before grey: back to it after a drop is silent
  let worked = false, hadTasks = false;   // work seen since the last all-done
  let timer = null, firstAt = 0;
  const lastAt = new Map();   // id → when it last played

  function emit(ids) {
    const id = ids.find((x) => x && d.on(x));
    if (!id || !d.visible()) return null;
    const t = d.now();
    if (t - (lastAt.has(id) ? lastAt.get(id) : -Infinity) < REPEAT_MS) return null;
    // Acceptance may only queue behind Send. Only the actual owned playback starts repeat suppression.
    if (!d.play(id, () => lastAt.set(id, d.now()))) return null;
    return id;
  }
  function fire() {
    timer = null; firstAt = 0;
    const c = colorOf(s), done = isDone(s);
    if (!armed) {
      shown = c;
      if (s.conn && s.status !== 'unknown') { armed = true; worked = isWork(s); hadTasks = s.tasks > 0; }
      return null;
    }
    // work counts only once it settled: a sub-SETTLE_MS blip of blue between two tool calls is no work
    if (isWork(s)) { worked = true; if (s.tasks > 0) hadTasks = true; }
    let colour = null;
    if (c !== shown) {
      if (c === 'offline') { beforeOff = shown; colour = c; }
      else if (shown === 'offline' && c === beforeOff) colour = null;   // a drop and back: nothing happened to the Agent
      else colour = c;
      if (c !== 'offline') beforeOff = null;
      shown = c;
    }
    let all = null;
    if (done && worked && (!ALL_DONE_NEEDS_TASKS || hadTasks)) { all = 'all-done'; worked = false; hadTasks = false; }
    else if (done) { worked = false; hadTasks = false; }
    return all || colour ? emit([all, colour]) : null;
  }
  function schedule() {
    const t = d.now();
    if (timer !== null) d.clearTimer(timer); else firstAt = t;
    const wait = Math.max(0, Math.min(SETTLE_MS, firstAt + MAX_WAIT_MS - t));
    timer = d.setTimer(fire, wait);
  }
  return {
    set(p) {
      const before = colorOf(s) + '|' + isDone(s);
      for (const k of ['conn', 'status', 'kind', 'tasks']) if (p && k in p) s[k] = p[k];
      // Task messages can start and finish while the main colour stays blue: retain that turn's work.
      if (armed && s.conn && s.tasks > 0) { worked = true; hadTasks = true; }
      if (colorOf(s) + '|' + isDone(s) !== before || !armed) schedule();
    },
    /** A confirmation card popped up (the approval / question sheet): its colour's sound now, unless that just played. */
    popup(id) { return armed ? emit([id]) : null; },
    flush() { if (timer !== null) { d.clearTimer(timer); return fire(); } return null; },
    _debug: () => ({ ...s, armed, shown, beforeOff, worked, hadTasks, last: Object.fromEntries(lastAt), pending: timer !== null }),
  };
}
