// P156: the heartbeat on the phone — a settings row (switch + idle interval + cost note) shown only to a host that can set
// it, and a folded 「心跳检查」 label on the pages a heartbeat produced (a quiet beat has no page at all: 〔不回群〕).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

test('P156 settings: heartbeat keys are phone-settable, interval steps wrap, the cost is stated in both languages', () => {
  const src = read('../public/js/settings.js');
  assert.match(src, /PREF_KEYS\.push\('updates\.mode', 'heartbeat\.enabled', 'heartbeat\.interval_minutes'[,)]/);
  // P207 §3: the ⇄ stepper became a dropdown (30 min … 24 h) + a custom number 5–1440 + a daily wake-up time
  const steps = JSON.parse(src.match(/HB_STEPS = (\[[^\]]+\])/)[1]);
  assert.deepEqual(steps, [30, 60, 120, 180, 240, 360, 480, 720, 1440]);
  assert.ok(steps.every((n) => n >= 5 && n <= 1440));
  assert.match(src, /PREF_KEYS\.push\('heartbeat\.daily_at'\)/);
  const html = read('../public/index.html');
  for (const id of ['set-hb-row', 'set-hb', 'set-hb-every']) assert.equal(html.split(`id="${id}"`).length, 2, id);
  const i18n = read('../public/i18n.js');
  for (const k of ['r.src.heartbeat', 'set.heartbeat', 'set.heartbeatNote', 'set.heartbeatMin', 'set.heartbeatHour'])
    assert.equal(i18n.split(`"${k}":`).length, 3, k + ' in zh and en');
  // P589: a host-only preflight replaces the per-interval model call.
  assert.match(html, /data-i18n="p589.note"/);
  assert.match(i18n, /无事不调用模型/);
  assert.match(i18n, /checks first without calling a model/);
});

test('P156 pages: via heartbeat gets its own folded label; older hosts (no settable key) never see the row', () => {
  const relay = read('../public/js/relay.js');
  assert.match(relay, /src\.via === "heartbeat" \? t\('r\.src\.heartbeat'\)/);
  const settings = read('../public/js/settings.js');
  assert.match(settings, /hostInfo\.settable\.includes\('heartbeat\.enabled'\)/);
  assert.match(settings, /el\('set-hb-row'\)\.hidden = !hbKnown/);
});
