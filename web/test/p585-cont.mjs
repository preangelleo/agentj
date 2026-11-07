// #585 page logic the real-host run cannot reach without restarting a real service: the local Noise fake host plays serve.
//   1 「重启服务」: reset_done {pending, cont:"probe"} → the link drops (service restarting) → reconnect → the page sends the
//     signed `rescue_do probe` by itself → green with the proof. No second tap.
//   2 「重启服务」 on a host with Stop everything on: the probe says estop → 「恢复」 right on the card → after the resume the
//     page sends the probe again by itself → green.
//   3 一键修复 that tried everything: ✗ per step, 「把诊断发给客服」 → confirm sheet → signed `rescue_do report` → ticket.
// Phone 390 and desktop 1440, zh + en. Desktop Chromium viewport simulation, not a physical phone.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot } from './browser.mjs';

const out = process.env.AJ_P585_SHOTS || new URL('../../../.jarvis/out/p585-emergency/', import.meta.url).pathname;
mkdirSync(out, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
const sent = [];
let estopOn = false, mode = 'restart';
fake.st.onApp = async (c, m, send) => {
  if (m.t === 'resume') {
    assert.ok(await fake.verifyControl(c, 'resume', m, {}));
    estopOn = false; await send({ t: 'ctl_res', r: m.r, action: 'resume', ok: true });
    await fake.send({ t: 'estop_state', on: false }); return true;
  }
  if (m.t !== 'rescue_do') return false;
  assert.ok(await fake.verifyControl(c, 'rescue_do', m, { action: m.action }), 'signed by this remote');
  sent.push(m.action);
  const S = (o) => send({ t: 'reset_step', r: m.r, flow: m.action, ...o });
  if (m.action === 'restart') {
    await S({ step: 1, state: 'run', text: mode === 'zh' ? '' : '' });
    await send({ t: 'reset_done', r: m.r, flow: 'restart', ok: true, step: 1, pending: true, cont: 'probe', text: 'RESTARTING', ...(estopOn ? { resume: true } : {}) });
    setTimeout(() => fake.kick(1012), 300);             // serve goes down for the restart
    return true;
  }
  if (m.action === 'probe') {
    await S({ step: 1, state: 'run', text: 'PROBE-RUN' });
    if (estopOn) { await S({ step: 1, state: 'fail', text: 'ESTOP' }); await send({ t: 'reset_done', r: m.r, flow: 'probe', ok: false, why: 'estop', resume: true, cont: 'probe', step: 1, text: 'RESTARTED\nESTOP' }); return true; }
    await S({ step: 1, state: 'ok', text: 'ANSWERED' });
    await send({ t: 'reset_done', r: m.r, flow: 'probe', ok: true, step: 1, text: 'RESTARTED\nANSWERED' }); return true;
  }
  if (m.action === 'repair') {
    for (const n of [1, 2, 3, 4]) { await S({ step: n, state: 'run', text: 'RUN' + n }); await S({ step: n, state: n === 3 ? 'skip' : 'fail', text: 'NO' + n }); }
    await send({ t: 'reset_done', r: m.r, flow: 'repair', ok: false, why: 'all_failed', report: true, step: 4, text: 'SUMMARY\nALLFAIL' }); return true;
  }
  if (m.action === 'report') {
    await S({ step: 1, state: 'run', text: 'SENDING' });
    await send({ t: 'reset_done', r: m.r, flow: 'report', ok: true, step: 1, ticket: 'st_ABCDEFGH12', text: 'TICKET st_ABCDEFGH12' }); return true;
  }
  return false;
};
const vis = (id) => `(() => { const e = document.getElementById(${JSON.stringify(id)}); return !!e && !e.closest('[hidden]') && e.getClientRects().length > 0; })()`;
const st = (p, id) => evaluate(p, `document.getElementById(${JSON.stringify(id)})?.dataset.state`);
async function tap(p, id, yes = true) {
  await evaluate(p, `document.getElementById(${JSON.stringify(id)}).scrollIntoView({block:'center'}); document.getElementById(${JSON.stringify(id)}).click(); true`);
  if (yes) { await waitFor(p, `!document.getElementById('confirm').hidden`, 5000); await evaluate(p, `document.getElementById('confirm-yes').click(); true`); }
}
let n = 0;
try {
  for (const lang of ['zh', 'en']) for (const w of [390, 1440]) {
    fake.reset(); fake.setUp(true); estopOn = false; sent.length = 0;
    const p = await newPage(B, w, w === 390 ? 844 : 900, 'dark', { touch: w === 390, allow: [web.url, fake.relay + '/'] });
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `document.getElementById('a2hs-ok')?.click();document.getElementById('phone-env-dismiss')?.click();document.getElementById('setBtn').click();true`);
      await waitFor(p, `!!document.getElementById('rs-restart-go')`);
      // 1 restart → drop → reconnect → probe by itself
      await tap(p, 'rs-restart-go');
      await waitFor(p, `document.getElementById('rs-restart').innerText.includes('RESTARTING')`, 8000);
      await waitFor(p, `document.getElementById('rs-restart').dataset.state === 'ok'`, 30000);
      assert.deepEqual(sent, ['restart', 'probe'], 'the page sent the probe by itself after the reconnect');
      assert.ok(await evaluate(p, `document.getElementById('rs-restart').innerText.includes('ANSWERED')`));
      writeFileSync(`${out}/cont-restart-${lang}-${w}.png`, await shoot(p, '')); n++;
      // 2 Stop everything on → Resume on the card → probe again by itself
      await evaluate(p, `document.getElementById('rs-restart-close')?.click(); true`);
      estopOn = true; await fake.send({ t: 'estop_state', on: true }); sent.length = 0;
      await sleep(300);
      await tap(p, 'rs-restart-go');
      await waitFor(p, `document.getElementById('rs-restart').dataset.state === 'fail'`, 30000);
      assert.ok(await evaluate(p, vis('rs-restart-resume')), 'a 「恢复」 button right on the card');
      writeFileSync(`${out}/cont-estop-${lang}-${w}.png`, await shoot(p, ''));
      await evaluate(p, `document.getElementById('rs-restart-resume').click(); true`);
      await waitFor(p, `!document.getElementById('confirm').hidden`, 5000); await evaluate(p, `document.getElementById('confirm-yes').click(); true`);
      await waitFor(p, `document.getElementById('rs-restart').dataset.state === 'ok'`, 30000);
      assert.deepEqual(sent, ['restart', 'probe', 'probe'], 'after 「恢复」 the probe ran again without another tap');
      writeFileSync(`${out}/cont-resumed-${lang}-${w}.png`, await shoot(p, '')); n++;
      // 3 one-tap repair: everything failed → per-step ✗ → send the diagnosis (asked first) → ticket
      sent.length = 0;
      await tap(p, 'rs-repair-go');
      await waitFor(p, `document.getElementById('rs-repair').dataset.state === 'fail'`, 15000);
      const marks = await evaluate(p, `[...document.querySelectorAll('#rs-repair-steps li')].map(li => li.dataset.state).join()`);
      assert.equal(marks, 'fail,fail,skip,fail', 'every step shows how it went (red ✗ / hollow skip marker), no leftover run row');
      assert.ok(await evaluate(p, vis('rs-repair-report')));
      await tap(p, 'rs-repair-report');
      await waitFor(p, `document.getElementById('rs-repair').innerText.includes('st_ABCDEFGH12')`, 15000);
      assert.deepEqual(sent, ['repair', 'report']);
      await evaluate(p, `document.getElementById('rs-repair').scrollIntoView({block:'start'}); true`);
      writeFileSync(`${out}/cont-allfail-report-${lang}-${w}.png`, await shoot(p, '')); n++;
      console.log('PASS', lang, w);
    } finally { await p.dispose(); }
  }
  assert.equal(n, 12);
  console.log('ALL PASS');
} finally { await B.close(); await fake.stop(); await web.stop(); }
