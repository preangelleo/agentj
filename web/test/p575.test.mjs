// P575 (#390): the state-change sounds + 全部完成 — pure logic (js/cues.js: colour → sound, settle / merge / no repeat,
// all-done, switches) and the player (js/sfx.js: one volume through the one gain, one sound at a time, unlock, hidden / 0 %).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';

const mem = new Map();
globalThis.localStorage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };

// ---- a fake browser for sfx.js: Audio elements and one AudioContext whose gain we can read
const made = { ctx: [], gains: [], sources: [], audios: [] };
class FakeAudio {
  constructor(src) { this.src = src || ''; this.volume = 1; this.muted = false; this.paused = true; this.ended = false; this.currentTime = 0;
    this.plays = []; this.listeners = {}; made.audios.push(this); }
  play() { this.paused = false; this.ended = false; this.plays.push({ src: this.src, muted: this.muted }); return Promise.resolve(); }
  pause() { this.paused = true; }
  addEventListener(e, f) { (this.listeners[e] ||= []).push(f); }
  removeEventListener(e, f) { this.listeners[e] = (this.listeners[e] || []).filter((x) => x !== f); }
  end() { this.paused = true; this.ended = true; const l = this.listeners.ended || []; this.listeners.ended = []; for (const f of l) f(); }
}
class FakeAC {
  constructor() { this.state = 'running'; this.destination = { dest: true }; made.ctx.push(this); }
  createGain() { const g = { gain: { value: 1 }, connect: (x) => { g.to = x; return x; } }; made.gains.push(g); return g; }
  createMediaElementSource(el) { const s = { el, connect: (x) => { s.to = x; return x; } }; made.sources.push(s); return s; }
  resume() { this.state = 'running'; return Promise.resolve(); }
}
globalThis.window = { AudioContext: FakeAC };
globalThis.Audio = FakeAudio;
globalThis.document = { visibilityState: 'visible', body: { dataset: {} } };

const cues = await import('../public/js/cues.js');
const sfx = await import('../public/js/sfx.js');

// ---- a fake clock for createCues
function rig(opts = {}) {
  let now = 0, seq = 0;
  const timers = new Map(), played = [];
  const box = cues.createCues({
    now: () => now,
    setTimer: (f, ms) => { const h = ++seq; timers.set(h, { f, at: now + ms }); return h; },
    clearTimer: (h) => timers.delete(h),
    play: (id, started) => { if (opts.play && !opts.play(id)) return false; played.push(id); started(); return true; },
    visible: () => (opts.visible ? opts.visible() : true),
    on: opts.on || (() => true),
  });
  const advance = (ms) => {
    const end = now + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      timers.delete(due[0]); now = due[1].at; due[1].f();
    }
    now = end;
  };
  return { box, played, advance, at: () => now };
}
const S = cues.SETTLE_MS;
/** connected, idle, nothing in 在办; settled → armed baseline, silent */
function armed(opts) {
  const r = rig(opts);
  r.box.set({ conn: true, status: 'idle', tasks: 0 });
  r.advance(S + 10);
  assert.deepEqual(r.played, [], 'the first settled state is the baseline: silent');
  return r;
}

test('assets: six English-named mp3 files, one per cue id, exactly the delivered A set (different bytes each)', () => {
  assert.deepEqual(cues.CUE_IDS, ['idle', 'working', 'waiting', 'question', 'offline', 'all-done']);
  const seen = new Set();
  for (const id of cues.CUE_IDS) {
    const rel = cues.SRC[id];
    assert.match(rel, /^assets\/cue-[a-z-]+\.mp3$/);
    const f = new URL('../public/' + rel, import.meta.url);
    assert.ok(existsSync(f), rel);
    const b = readFileSync(f);
    assert.ok(b.length > 4000 && b.length < 64000, `${rel} ${b.length} bytes`);
    assert.ok(b[0] === 0x49 || b[0] === 0xff, `${rel} is an mp3 (ID3 / frame sync)`);
    seen.add(b.toString('base64'));
  }
  assert.equal(seen.size, 6, 'six different sounds');
  assert.ok(readFileSync(new URL('../public/assets/cue-all-done.mp3', import.meta.url)).length > 2 * readFileSync(new URL('../public/assets/cue-idle.mp3', import.meta.url)).length,
    'all-done is the longer chord');
});

test('colorOf: the page colour rules of app.css', () => {
  const c = cues.colorOf;
  assert.equal(c({ conn: true, status: 'idle' }), 'idle');
  assert.equal(c({ conn: true, status: 'working' }), 'working');
  assert.equal(c({ conn: true, status: 'waiting', kind: 'permission' }), 'waiting');
  assert.equal(c({ conn: true, status: 'waiting' }), 'waiting');
  assert.equal(c({ conn: true, status: 'waiting', kind: 'question' }), 'question');
  assert.equal(c({ conn: true, status: 'unknown' }), 'offline');
  for (const st of ['idle', 'working', 'waiting']) assert.equal(c({ conn: false, status: st }), 'offline', 'not connected = grey');
  assert.equal(c(null), 'offline');
});

test('switch → sound: each colour change plays its own sound after it settles', () => {
  const r = armed();
  r.box.set({ status: 'working' }); assert.deepEqual(r.played, [], 'not before it settles');
  r.advance(S); assert.deepEqual(r.played, ['working']);
  r.box.set({ status: 'waiting', kind: 'permission' }); r.advance(S); assert.deepEqual(r.played.at(-1), 'waiting');
  r.advance(3000);
  r.box.set({ status: 'waiting', kind: 'question' }); r.advance(S); assert.deepEqual(r.played.at(-1), 'question');
  r.advance(3000);
  r.box.set({ status: 'working', kind: null }); r.advance(S); assert.deepEqual(r.played.at(-1), 'working');
  r.box.set({ tasks: 2 }); r.advance(S);
  r.box.set({ status: 'idle' }); r.advance(S); assert.deepEqual(r.played.at(-1), 'idle', 'idle with work still in 在办 = the green tick');
  r.advance(3000);
  r.box.set({ conn: false }); r.advance(S); assert.deepEqual(r.played.at(-1), 'offline');
  assert.deepEqual(r.played, ['working', 'waiting', 'question', 'working', 'idle', 'offline']);
});

test('ordinary reply: main Agent idle without background tasks → green only, never the chord', () => {
  const r = armed();
  r.box.set({ status: 'working' }); r.advance(S);
  r.box.set({ status: 'idle' }); r.advance(S);
  assert.equal(cues.ALL_DONE_NEEDS_TASKS, true);
  assert.deepEqual(r.played, ['working', 'idle'], 'ordinary replies use the green cue');
  r.advance(5000);
  r.box.set({ tasks: 0 }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'idle'], 'no second cue without new work');
});

test('all-done: the last background task ending while the main Agent is already idle', () => {
  const r = armed();
  r.box.set({ tasks: 3 }); r.advance(S);
  assert.deepEqual(r.played, [], 'a task starting while idle changes no colour');
  r.box.set({ tasks: 1 }); r.advance(S); assert.deepEqual(r.played, []);
  r.box.set({ tasks: 0 }); r.advance(S);
  assert.deepEqual(r.played, ['all-done']);
});

test('all-done switched off → the green tick plays instead; both off → silence', () => {
  let off = new Set(['all-done']);
  const r = armed({ on: (id) => !off.has(id) });
  r.box.set({ status: 'working', tasks: 1 }); r.advance(S);
  r.box.set({ status: 'idle', tasks: 0 }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'idle']);
  off = new Set(['all-done', 'idle', 'working']);
  r.advance(3000);
  r.box.set({ status: 'working' }); r.advance(S);
  r.box.set({ status: 'idle' }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'idle'], 'nothing new');
});

test('debounce: a flicker inside the settle window is no change at all', () => {
  const r = armed();
  r.box.set({ status: 'working' }); r.advance(100);
  r.box.set({ status: 'idle' }); r.advance(S + 50);
  assert.deepEqual(r.played, [], 'idle → working → idle within 100 ms: nothing');
  r.box.set({ status: 'working' }); r.advance(S);
  assert.deepEqual(r.played, ['working']);
  r.box.set({ status: 'idle' }); r.advance(120);
  r.box.set({ status: 'working' }); r.advance(S + 50);
  assert.deepEqual(r.played, ['working'], 'a blue → green → blue blip between two tool calls is not 「做完了」');
});

test('debounce: a run of changes is merged into the last one, and never waits past MAX_WAIT_MS', () => {
  const r = armed();
  r.box.set({ status: 'working' }); r.advance(150);
  r.box.set({ status: 'waiting', kind: 'permission' }); r.advance(150);
  r.box.set({ status: 'waiting', kind: 'question' }); r.advance(S);
  assert.deepEqual(r.played, ['question'], 'only the last colour of the burst');
  // a change every 300 ms (each inside the settle window) still sounds by MAX_WAIT_MS
  const r2 = armed();
  const start = r2.at();
  const seq = ['working', 'idle'];
  for (let i = 0; i < 12 && !r2.played.length; i++) { r2.box.set({ status: seq[i % 2] }); r2.advance(300); }
  assert.equal(r2.played.length, 1);
  assert.ok(r2.at() - start <= cues.MAX_WAIT_MS + 300, `sounded after ${r2.at() - start} ms`);
});

test('debounce: the same sound again within REPEAT_MS is dropped (2 s same colour no repeat)', () => {
  const r = armed();
  r.box.set({ status: 'working', tasks: 1 }); r.advance(S);
  r.box.set({ status: 'idle' }); r.advance(S);            // green tick (a task is still running)
  r.box.set({ status: 'working' }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'idle'], 'blue again 0.8 s after blue: dropped');
  r.box.set({ status: 'idle' }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'idle'], 'green again 0.8 s after green: dropped');
  r.advance(cues.REPEAT_MS);
  r.box.set({ status: 'working' }); r.advance(S);
  r.box.set({ status: 'idle' }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'idle', 'working', 'idle'], 'after REPEAT_MS each sounds again');
});

test('popup: a new approval / question card says its colour once; the orange it brings does not repeat it', () => {
  const r = armed();
  r.box.set({ status: 'working' }); r.advance(S);
  r.advance(3000);
  assert.equal(r.box.popup('waiting'), 'waiting', 'at once, no settle');
  r.box.set({ status: 'waiting', kind: 'permission' }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'waiting'], 'card + colour = one sound');
  r.advance(3000);
  assert.equal(r.box.popup('waiting'), 'waiting', 'a second card while already orange still sounds');
  assert.deepEqual(r.played, ['working', 'waiting', 'waiting']);
  const fresh = rig();
  assert.equal(fresh.box.popup('waiting'), null, 'the cards already waiting when the page (re)loads are silent');
});

test('reload / first connect is silent; a drop and back to the same colour is silent', () => {
  const r = rig();
  r.box.set({ conn: false }); r.advance(S);
  r.box.set({ conn: true, status: 'unknown' }); r.advance(S);
  r.box.set({ status: 'working' }); r.advance(S);
  assert.deepEqual(r.played, [], 'boot grey → first known colour: baseline, no sound');
  r.box.set({ status: 'idle' }); r.advance(S);
  assert.deepEqual(r.played, ['idle']);
  r.advance(3000);
  r.box.set({ conn: false }); r.advance(S);
  r.box.set({ conn: true }); r.advance(S);
  assert.deepEqual(r.played, ['idle', 'offline'], 'back to green after grey: no 「做完了」, no second all-done');
  r.advance(3000);
  r.box.set({ status: 'working', tasks: 1 }); r.advance(S);
  r.box.set({ conn: false }); r.advance(S);
  r.box.set({ conn: true, status: 'idle', tasks: 0 }); r.advance(S);
  assert.deepEqual(r.played.slice(-3), ['working', 'offline', 'all-done'], 'the work finished while we were grey: all-done');
});

test('hidden page: nothing plays, and coming back does not play what was missed', () => {
  let vis = true;
  const r = armed({ visible: () => vis });
  vis = false;
  r.box.set({ status: 'working' }); r.advance(S);
  r.box.set({ status: 'idle' }); r.advance(S);
  assert.deepEqual(r.played, []);
  vis = true;
  r.box.set({ tasks: 0 }); r.advance(S);
  assert.deepEqual(r.played, []);
});

test('switches: one per sound, all on by default, phone-local (localStorage aj.cueOff only)', () => {
  for (const id of cues.CUE_IDS) assert.equal(cues.enabled(id), true, id);
  assert.equal(cues.enabled('nope'), false);
  assert.equal(cues.setEnabled('offline', false), false);
  assert.equal(cues.setEnabled('all-done', false), false);
  assert.equal(mem.get(cues.OFF_KEY), 'offline,all-done');
  assert.equal(cues.enabled('working'), true);
  assert.equal(cues.setEnabled('offline', true), true);
  assert.equal(cues.setEnabled('all-done', true), true);
  assert.equal(mem.has(cues.OFF_KEY), false, 'all on again = the key is gone');
  assert.equal(cues.setEnabled('bogus', false), false);
  assert.equal(mem.has(cues.OFF_KEY), false);
  const src = readFileSync(new URL('../public/js/cues.js', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /fetch\(|sendApp|XMLHttpRequest|WebSocket/, 'never sent anywhere');
});

test('unified volume: every cue plays through the Send whoosh\'s gain; one slider sets both', () => {
  assert.equal(sfx.playCue('idle'), false, 'before the first gesture nothing can play');
  sfx.setVolume(40);
  sfx.unlock();                                           // the first tap (app.js) / Send / Settings
  assert.equal(made.ctx.length, 1, 'one AudioContext');
  assert.equal(made.gains.length, 1, 'one gain node for every sound');
  const [g] = made.gains;
  assert.equal(made.sources.length, 2, 'whoosh + the cue element');
  for (const s of made.sources) assert.equal(s.to, g, 'both elements go through the same gain');
  assert.equal(g.to, made.ctx[0].destination);
  const cue = made.sources[1].el;
  assert.equal(cue.plays[0].muted, true, 'unlock plays the cue element once, muted (iOS)');
  assert.equal(cue.volume, 1, 'iPhone: the gain sets the level, not <audio>.volume');
  return Promise.resolve().then(() => {
    assert.equal(g.gain.value, sfx.gainOf(40));
    assert.equal(sfx.playCue('working'), true);
    assert.match(cue.src, /assets\/cue-working\.mp3$/);
    assert.equal(cue.plays.at(-1).muted, false);
    sfx.setVolume(10);
    assert.equal(g.gain.value, sfx.gainOf(10), 'the slider moves the one gain');
    sfx.unlock(); sfx.unlock();
    assert.equal(made.gains.length, 1, 'unlock builds once');
  });
});

test('player: 0 % / hidden → silent; never stacks; waits for the whoosh; Settings preview plays anyway', () => {
  const cue = made.sources[1].el, whoosh = made.sources[0].el;
  sfx.setVolume(0);
  assert.equal(sfx.playCue('idle'), false, '0 % = every sound muted');
  sfx.setVolume(30);
  document.visibilityState = 'hidden';
  assert.equal(sfx.playCue('idle'), false, 'hidden page: silent');
  document.visibilityState = 'visible';
  const n = cue.plays.length;
  sfx.playCue('waiting'); sfx.playCue('question');
  assert.equal(cue.plays.length, n + 2);
  assert.match(cue.src, /cue-question/, 'one element: the newer cue replaces the older (never two at once)');
  assert.equal(made.audios.filter((a) => a !== whoosh).length, 1, 'only one cue element ever');
  // the Send whoosh is sounding: the cue waits for its end
  assert.equal(sfx.play(), true);
  whoosh.currentTime = 0.1;
  const m = cue.plays.length;
  assert.equal(sfx.playCue('working'), true);
  assert.equal(cue.plays.length, m, 'not on top of the whoosh');
  sfx.playCue('all-done');                                // the newest one wins
  whoosh.end();
  assert.equal(cue.plays.length, m + 1);
  assert.match(cue.src, /cue-all-done/);
  assert.equal(sfx.previewCue('offline'), true);
  assert.match(cue.src, /cue-offline/);
  document.visibilityState = 'hidden';
  assert.equal(sfx.previewCue('idle'), true, 'the Settings 试听 is a tap: plays');
  document.visibilityState = 'visible';
  sfx.setVolume(0);
  assert.equal(sfx.previewCue('idle'), false, 'but not at 0 %');
});

test('wiring: page colour, connection, 在办 and the card popup feed the cues; Settings has one switch per sound', () => {
  const relay = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
  assert.match(relay, /cueBox\.set\(\{status: st, kind:/);
  assert.match(relay, /document\.body\.dataset\.conn = on \? "on" : "off";\n  cueBox\.set\(\{conn: !!on\}\);/);
  assert.match(relay, /cueBox\.popup\(card\.kind === "question" \? "question" : "waiting"\)/);
  assert.match(relay, /play: \(id, started\) => sfx\.playCue\(id, false, started\)/);
  const app = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
  assert.match(app, /busy\.setBusy\(m\); relay\.cueTasks\(busy\.currentCounts\(\)\.task_count\)/);
  assert.match(app, /const UNLOCK_EVENTS = \['pointerup', 'touchend', 'keydown'\];/);
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  for (const id of cues.CUE_IDS) {
    assert.match(html, new RegExp(`id="set-cue-${id}" class="tgl" role="switch" aria-checked="true"`), id);
    assert.match(html, new RegExp(`data-cue-play="${id}"`), id + ' preview');
  }
  assert.ok(html.indexOf('id="set-sfx-row"') < html.indexOf('id="set-cues"'), 'right under the one volume');
  assert.doesNotMatch(html.slice(html.indexOf('id="set-cues"'), html.indexOf('id="set-cue-all-done"') + 400), /type="range"/, 'no volume of their own');
  const en = JSON.parse(readFileSync(new URL('../i18n/web.en.json', import.meta.url), 'utf8'));
  const zh = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8'));
  for (const k of ['cue.gState', 'cue.idle', 'cue.working', 'cue.waiting', 'cue.question', 'cue.offline', 'cue.allDone', 'cue.allDoneNote', 'cue.gStateNote'])
    assert.ok(en[k] && zh[k], k);
  assert.equal(zh['cue.idle'], '变绿：做完了');
  assert.equal(zh['cue.waiting'], '变橙：等你点头');
  assert.equal(en['cue.question'], 'Turns purple: has a question');
});

test('first Settings cue preview survives the asynchronous unlock completion', async () => {
  const first = made.audios.length;
  const fresh = await import('../public/js/sfx.js?first-preview');
  fresh.setVolume(30);
  assert.equal(fresh.previewCue('working'), true);
  const cue = made.audios[first + 1];
  await Promise.resolve();
  assert.equal(cue.paused, false, 'unlock completion must not pause the preview from the same gesture');
  assert.equal(cue.muted, false);
  assert.match(cue.src, /cue-working/);
});

test('Send replaces an active cue; immediate cues and previews wait for Send', async () => {
  const first = made.audios.length;
  const fresh = await import('../public/js/sfx.js?send-order');
  fresh.setVolume(30);
  fresh.unlock(); await Promise.resolve();
  const [whoosh, cue] = made.audios.slice(first);
  fresh.playCue('all-done'); cue.currentTime = 0.2;
  assert.equal(fresh.play(), true);
  assert.equal(cue.paused, true, 'Send stops the active cue before starting');
  assert.equal(whoosh.currentTime, 0);
  const plays = cue.plays.length;
  fresh.playCue('waiting');
  assert.equal(cue.plays.length, plays, 'a whoosh is active even before currentTime advances');
  fresh.previewCue('question');
  assert.equal(cue.plays.length, plays, 'Settings preview also waits instead of stacking');
  whoosh.end();
  assert.equal(cue.plays.length, plays + 1, 'only the latest queued cue plays');
  assert.match(cue.src, /cue-question/);
  assert.equal([whoosh, cue].filter((a) => !a.paused && !a.muted).length, 1);
});


test('hidden page does not consume a cue repeat stamp, including player refusal at visibility change', () => {
  let visible = false, accepted = true;
  const r = armed({ visible: () => visible, play: () => accepted });
  assert.equal(r.box.popup('question'), null);
  assert.equal(r.box._debug().last.question, undefined);
  visible = true;
  accepted = false; // page became hidden between the scheduler check and the player's guard
  assert.equal(r.box.popup('question'), null);
  assert.equal(r.box._debug().last.question, undefined, 'refused playback is not a played cue');
  accepted = true;
  assert.equal(r.box.popup('question'), 'question', 'foreground can play the same cue immediately');
  assert.deepEqual(r.played, ['question']);
});

test('cue DOM counters are opt-in test hooks, absent during production playback', async () => {
  document.body.dataset = {};
  delete window.__AJ_TEST_CUES__;
  const fresh = await import('../public/js/sfx.js?test-hook');
  fresh.setVolume(30); fresh.unlock(); await Promise.resolve();
  assert.equal(fresh.playCue('working'), true);
  assert.equal(document.body.dataset.cues, undefined);
  assert.equal(document.body.dataset.cue, undefined);
  window.__AJ_TEST_CUES__ = true;
  assert.equal(fresh.playCue('question'), true);
  assert.equal(document.body.dataset.cues, '1');
  assert.equal(document.body.dataset.cue, 'question');
  delete window.__AJ_TEST_CUES__;
});


test('background tasks that finish while the main Agent works qualify only that turn', () => {
  const r = armed();
  r.box.set({ status: 'working' }); r.advance(S);
  r.box.set({ tasks: 1 }); r.advance(S);
  r.box.set({ tasks: 0 }); r.advance(S);
  r.box.set({ status: 'idle' }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'all-done']);
  r.advance(3000);
  r.box.set({ status: 'working' }); r.advance(S);
  r.box.set({ status: 'idle' }); r.advance(S);
  assert.deepEqual(r.played, ['working', 'all-done', 'working', 'idle'], 'the next ordinary reply has no old background-task flag');
});


test('queued cue rejected while hidden keeps no repeat stamp; only the actual owned playback starts dedupe', async () => {
  const first = made.audios.length;
  document.visibilityState = 'visible';
  const fresh = await import('../public/js/sfx.js?queued-hidden-receipt');
  fresh.setVolume(30); fresh.unlock(); await Promise.resolve();
  const [whoosh, cue] = made.audios.slice(first);
  let now = 1000;
  const box = cues.createCues({ now: () => now, setTimer: () => 1, clearTimer() {}, on: () => true,
    visible: () => document.visibilityState === 'visible', play: (id, started) => fresh.playCue(id, false, started) });
  box.set({ conn: true, status: 'idle', tasks: 0 }); box.flush();
  fresh.play();
  const initial = cue.plays.length;
  assert.equal(box.popup('question'), 'question');
  assert.equal(box._debug().last.question, undefined, 'accepting a queued cue is not playback');
  document.visibilityState = 'hidden'; whoosh.end(); await Promise.resolve();
  assert.equal(cue.plays.length, initial, 'hidden page refuses the queued cue');
  assert.equal(box._debug().last.question, undefined);
  document.visibilityState = 'visible';
  assert.equal(box.popup('question'), 'question', 'foreground retry of the same cue is allowed');
  assert.equal(box._debug().last.question, undefined, 'wait for Audio.play success');
  await Promise.resolve();
  assert.equal(cue.plays.length, initial + 1);
  assert.equal(box._debug().last.question, now);
  assert.equal(box.popup('question'), null, 'successful playback really dedupes');
  now += cues.REPEAT_MS;
  fresh.play();
  box.popup('waiting'); box.popup('working'); whoosh.end(); await Promise.resolve();
  assert.equal(box._debug().last.waiting, undefined, 'a replaced queued cue never started');
  assert.equal(box._debug().last.working, now, 'only the newest queued cue gets a receipt');
  now += cues.REPEAT_MS;
  box.popup('waiting'); box.popup('offline'); await Promise.resolve();
  assert.equal(box._debug().last.waiting, undefined, 'a replaced unresolved play promise cannot claim the player');
  assert.equal(box._debug().last.offline, now);
  now += cues.REPEAT_MS;
  box.popup('waiting'); fresh.play(); await Promise.resolve();
  assert.equal(box._debug().last.waiting, undefined, 'Send takes ownership before the old play promise resolves');
  whoosh.end();
  const originalPlay = cue.play;
  cue.play = () => Promise.reject(new Error('NotAllowedError'));
  box.popup('question'); await Promise.resolve();
  assert.equal(box._debug().last.question, 1000, 'a rejected playback promise cannot replace the previous stamp');
  cue.play = originalPlay;
});
