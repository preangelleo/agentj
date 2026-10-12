// P156: the heartbeat on the phone — a settings row (switch + idle interval + cost note) shown only to a host that can set
// it, and a folded 「心跳检查」 label on the pages a heartbeat produced (a quiet beat has no page at all: 〔不回群〕).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

test('P156 settings: heartbeat keys are phone-settable, interval steps wrap, the cost is stated in both languages', () => {
  const src = read('../public/js/settings.js');
  assert.match(src, /PREF_KEYS\.push\('updates\.mode', 'heartbeat\.enabled', 'heartbeat\.interval_minutes'[,)]/);
  // the stepping rule, evaluated on its own (the module needs a DOM)
  const steps = JSON.parse(src.match(/HB_STEPS = (\[[^\]]+\])/)[1]);
  const body = src.match(/export function hbNext\(n\) \{ (return [^}]+) \}/)[1];
  const hbNext = new Function('HB_STEPS', 'n', body).bind(null, steps);
  assert.equal(hbNext(60), 120);
  assert.equal(hbNext(45), 60, 'a value set from the computer steps to the next preset');
  assert.equal(hbNext(1440), 15, 'wraps');
  assert.ok(steps.every((n) => n >= 5 && n <= 1440));
  const html = read('../public/index.html');
  for (const id of ['set-hb-row', 'set-hb', 'set-hb-every']) assert.equal(html.split(`id="${id}"`).length, 2, id);
  const i18n = read('../public/i18n.js');
  for (const k of ['r.src.heartbeat', 'set.heartbeat', 'set.heartbeatNote', 'set.heartbeatMin', 'set.heartbeatHour'])
    assert.equal(i18n.split(`"${k}":`).length, 3, k + ' in zh and en');
  assert.match(i18n, /每次心跳都会用掉一次模型调用/);
  assert.match(i18n, /Every heartbeat costs one model call/);
});

test('P156 pages: via heartbeat gets its own folded label; older hosts (no settable key) never see the row', () => {
  const relay = read('../public/js/relay.js');
  assert.match(relay, /src\.via === "heartbeat" \? t\('r\.src\.heartbeat'\)/);
  const settings = read('../public/js/settings.js');
  assert.match(settings, /hostInfo\.settable\.includes\('heartbeat\.enabled'\)/);
  assert.match(settings, /el\('set-hb-row'\)\.hidden = !hbKnown/);
});
