// P393 (#122 / #113 / #115): the five-second rule for 「连不上 Agent J 主机」 with a FAKE CLOCK, and the wiring that makes the
// disconnected card, Settings' red line and Emergency repair's 「现在连不上」 all read that one gate; repair results close (×),
// fold after success / reconnect and open folded next time. Page-level twin: p393_reconnect.mjs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createGate, DOWN_DEBOUNCE_MS, LATE_MS } from '../public/js/reconnect.js';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const APP = read('../public/app.js'), CARD = read('../public/js/offline-card.js'), SET = read('../public/js/settings.js');
const RESCUE = read('../public/js/rescue.js'), CMD = read('../public/js/cmdrun.js'), MENU = read('../public/menu.css'), CSS = read('../public/app.css');
const DICT = (await import('../public/i18n.js')).default;

/** A fake clock: timers run only when the test advances time; `late` simulates a frozen page (timers fire after wake). */
function fakeClock() {
  let t = 1_000_000, seq = 0;
  const timers = new Map();
  return {
    now: () => t,
    setT: (f, d) => { const id = ++seq; timers.set(id, { at: t + d, f }); return id; },
    clearT: (id) => { timers.delete(id); },
    advance(ms) {
      const end = t + ms;
      for (;;) {
        const next = [...timers.entries()].filter(([, x]) => x.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        timers.delete(next[0]); t = Math.max(t, next[1].at); next[1].f();
      }
      t = end;
    },
    /** frozen for `ms`: no timer runs in between; on wake every due timer fires at the wake-up time (late). */
    freeze(ms) {
      t += ms;
      for (const [id, x] of [...timers.entries()].sort((a, b) => a[1].at - b[1].at)) if (x.at <= t) { timers.delete(id); x.f(); }
    },
  };
}
function rig() {
  const c = fakeClock();
  let hidden = false, shown = 0;
  const log = [];
  const g = createGate({ now: c.now, setT: c.setT, clearT: c.clearT, hidden: () => hidden, onChange: (v) => { log.push(v); if (v === 'down') shown++; } });
  return { c, g, log, shownCount: () => shown, hide: (v) => { hidden = v; } };
}

test('P393 the debounce is one named constant: 5000 ms', () => {
  assert.equal(DOWN_DEBOUNCE_MS, 5000);
  assert.match(APP, /const QUIET_MS = DOWN_DEBOUNCE_MS;/);
  assert.match(CARD, /export const SHOW_AFTER_MS = DOWN_DEBOUNCE_MS;/);
  assert.doesNotMatch(APP, /QUIET_MS = 6000/);
});

test('P393 fake clock: disconnect + reconnect at 4.9 s → the popup is never shown', () => {
  const { c, g, log, shownCount } = rig();
  assert.equal(g.drop(), true);
  assert.equal(g.state(), 'quiet');
  c.advance(4900);
  assert.equal(g.state(), 'quiet');
  g.up();
  c.advance(60_000);
  assert.equal(shownCount(), 0);
  assert.deepEqual(log, ['quiet', 'up']);
});

test('P393 fake clock: a disconnect that persists 5.1 s → shown (once), and reset on reconnect', () => {
  const { c, g, log, shownCount } = rig();
  g.drop();
  c.advance(4999);
  assert.equal(g.state(), 'quiet');
  c.advance(101);                         // 5.1 s in total
  assert.equal(g.state(), 'down');
  assert.equal(shownCount(), 1);
  assert.equal(g.drop(), false, 'a second drop while down is not a new window');
  c.advance(30_000);
  assert.equal(shownCount(), 1);
  g.up();
  assert.equal(g.state(), 'up');
  // the next drop starts from zero again
  g.drop(); c.advance(4900); g.up();
  assert.equal(shownCount(), 1);
  assert.deepEqual(log, ['quiet', 'down', 'up', 'quiet', 'up']);
});

test('P393 fake clock: foreground / visibilitychange resume with a reconnect inside 5 s → no popup', () => {
  // the link died while the screen was locked; the hidden page's window ran out in the background
  const { c, g, shownCount, hide } = rig();
  hide(true);
  g.drop();
  c.advance(60_000);                      // locked for a minute
  assert.equal(g.state(), 'quiet', 'a hidden page never turns down');
  hide(false);
  assert.equal(g.restart(), true);        // visibilitychange → visible
  c.advance(800);                         // the socket is back 0.8 s after unlock
  g.up();
  c.advance(10_000);
  assert.equal(shownCount(), 0);
});

test('P393 fake clock: back in front while already shown → a fresh silent window, shown again only after 5 s more', () => {
  const { c, g, log, shownCount } = rig();
  g.drop(); c.advance(5100);
  assert.equal(g.state(), 'down');
  g.restart();                            // pageshow / resume / online / network change
  assert.equal(g.state(), 'quiet');
  c.advance(4900);
  assert.equal(g.state(), 'quiet');
  c.advance(200);
  assert.equal(g.state(), 'down');
  assert.equal(shownCount(), 2);
  assert.deepEqual(log, ['quiet', 'down', 'quiet', 'down']);
  assert.equal(g.restart() && g.state(), 'quiet');
  g.up(); assert.equal(g.restart(), false, 'restart is a no-op while connected');
});

test('P393 fake clock: a frozen page (timer fires late on thaw) does not flash — the window restarts at the wake-up', () => {
  const { c, g, shownCount } = rig();
  g.drop();
  c.freeze(30_000);                       // page frozen; its 5 s timer runs 25 s late, while visible again
  assert.equal(g.state(), 'quiet');
  assert.ok(30_000 - 5000 > LATE_MS);
  c.advance(700); g.up();                 // reconnected 0.7 s after the thaw
  assert.equal(shownCount(), 0);
  // …and if it does not come back, the full five seconds count from the thaw
  g.drop(); c.freeze(20_000); c.advance(4900);
  assert.equal(g.state(), 'quiet');
  c.advance(200);
  assert.equal(g.state(), 'down');
});

test('P393 wiring: every 「连不上」 surface reads the gate', () => {
  // app.js: a steady-session drop and the relay's 「主机离线」 both go quiet first; boot, foreground, resume, online, network change
  assert.match(APP, /createGate\(\{ ms: QUIET_MS/);
  assert.doesNotMatch(APP, /key !== 'st\.hostDown'/, 'host-down is no longer shown at once');
  for (const ev of [/addEventListener\('online', \(\) => \{ wakeLink\(\)/, /addEventListener\('pageshow', \(e\) => \{ wakeLink\(\)/,
    /addEventListener\('resume', \(\) => \{ wakeLink\(\)/, /navigator\.connection\?\.addEventListener\?\.\('change'/, /if \(fg\) wakeLink\(\);/]) assert.match(APP, ev);
  assert.match(APP, /link\.state\(\) === 'quiet' \|\| \(painted === 'idle'/, 'the card waits while quiet');
  assert.match(APP, /settings\.connectionChanged\(on, !on && link\.state\(\) === 'quiet'\)/, 'Settings red waits too');
  assert.match(APP, /rescue\.onLinkDown\(\)/);
  assert.match(APP, /linkState: \(\) => \(session\.isReady\(\) \? 'up' : link\.state\(\) === 'quiet' \? 'quiet' : 'down'\)/);
  assert.match(CARD, /export function hush\(\)/);
  // Settings: pending = amber 「正在重新连接…」, no notice
  assert.match(SET, /n\.dataset\.online = 'pending'; n\.textContent = t\('p393\.reconnecting'\)/);
  assert.match(MENU, /\.setconnection\[data-online="pending"\]\{color:var\(--c-peachy-feeling\)\}/);
  assert.match(CSS, /body\[data-relink\] \.dot\{background:transparent;box-shadow:inset 0 0 0 2px var\(--c-peachy-feeling\)/);
  // Emergency repair: a tap during the quiet window waits; past it, the reason + next step + check time
  assert.match(RESCUE, /H\.linkState\(\) === 'quiet'\) \{ F\.wait = true;/);
  assert.match(RESCUE, /export function onLinkDown\(\)/);
  assert.match(RESCUE, /rs-reset-next/);
  assert.match(RESCUE, /p393\.checkedAt/);
  // a drop that heals inside the window still resumes an upload it cut (parity upload-resume: a serve restart = st.hostDown)
  const RELAY = read('../public/js/relay.js');
  assert.match(RELAY, /export function connLost\(\)\{ quietLost = true; outbox\.lost\(\); \}/);
  assert.match(RELAY, /if \(on && \(!was \|\| quietLost\)\) uploadsResume\(\);/);
});

test('P393 repair results: ×, fold after 30 s / on reconnect / on reopening Settings, log under 详情; command card too', () => {
  assert.match(RESCUE, /export const FOLD_MS = 30000;/);
  assert.match(RESCUE, /b\.setAttribute\('aria-label', tx\('p380\.closeResult'\)\)/, 'the P380 close: same key');
  assert.match(RESCUE, /node\('button', 'menuicon rscmdclose', '×'\)/, 'the P380 close: same look');
  assert.match(RESCUE, /if \(ok && isReady\(\)\) foldLater\(flow\);/);
  assert.match(RESCUE, /!F\.fold && !F\.foldTimer\) foldLater\(flow\);/, 'reconnect folds ended results');
  assert.match(RESCUE, /export function foldEnded\(\)/);
  assert.match(SET, /rescue\.foldEnded\(\);/);
  assert.match(SET, /cmdrun\.foldEnded\(\);/);
  assert.match(CMD, /export function foldEnded\(\)/);
  assert.match(CMD, /R\.why = 'restored'/, '#113: the command card\'s 「连不上」 turns into 已恢复连接');
  assert.match(APP, /cmdrun\.onReady\(\);/);
});

test('P393 strings: both languages, same placeholders, no ticket codes, no palette names', () => {
  const keys = ['p393.reconnecting', 'p393.waitLink', 'p393.offlineNext', 'p393.checkedAt', 'p393.sumOk', 'p393.sumFail', 'p393.ended'];
  const ph = (s) => (String(s).match(/\{\w+\}/g) || []).sort().join();
  for (const k of keys) {
    assert.ok(DICT.zh[k] && DICT.en[k], k);
    assert.equal(ph(DICT.zh[k]), ph(DICT.en[k]), k);
    for (const l of ['zh', 'en']) assert.doesNotMatch(DICT[l][k], /P\d{3}|#\d{2,}|Noir|Lotion|Sooty|Emerald|Peachy|试用|trial/i, k + ' ' + l);
  }
  assert.match(DICT.zh['p393.offlineNext'], /`agentj doctor --reconnect`/);
  assert.match(DICT.en['p393.offlineNext'], /`agentj doctor --reconnect`/);
  assert.equal(DICT.zh['p380.closeResult'], '关闭执行结果');
});
