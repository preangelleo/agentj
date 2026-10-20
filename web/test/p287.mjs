// P287: 「重启后请登录」 only as the phone's own offline line. Owned headless browser + encrypted fake host (never production).
// 1. service restart (computer gone ~10 s) → only 「Agent J 主机离线」, no hint;  2. upgrade (~40 s) → the same;
// 3. OS restart (gone past the threshold, page reloaded cold meanwhile) → the Mac line, from what the last ready said;
// back online → the line is gone. Shots → reports/qa/p287/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitState, evaluate, waitFor, sleep, shoot } from './browser.mjs';
import { HINT_AFTER_MS } from '../public/js/offhint.js';
const ok = (v, n) => { assert.ok(v, n); console.log('PASS ' + n); };
const OUT = join(dirname(fileURLToPath(import.meta.url)), '../../../reports/qa/p287');
mkdirSync(OUT, { recursive: true });
const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const hint = `(() => { const b = document.getElementById('offHint'); return b && !b.hidden && getComputedStyle(b).display !== 'none' ? b.textContent : ''; })()`;
const sampler = `window.p287 = []; clearInterval(window.p287t); window.p287t = setInterval(() => window.p287.push(${hint}), 200)`;
let p;
try {
  fake.st.kind = { plat: 'macos', boot: 'login' };
  p = await newPage(B, 390, 844, 'light', { allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await sleep(500);
  ok(await evaluate(p, `(async () => { const { dbGet } = await import('./js/store.js'); const h = await dbGet('host'); return h.plat === 'macos' && h.boot === 'login'; })()`),
    'the ready\'s plat / boot are kept with the pairing (a cold page offline still knows)');
  ok(!(await evaluate(p, hint)), 'online (green): no hint');

  for (const [name, secs] of [['service restart', 10], ['upgrade', 40]]) {
    await evaluate(p, sampler);
    fake.setUp(false);
    await waitFor(p, `document.body.dataset.conn === 'off'`, 8000);   // P393: after the five-second window
    await sleep(secs * 1000);
    fake.setUp(true); await waitState(p, 'ready'); await sleep(300);
    ok((await evaluate(p, `window.p287`)).every((x) => !x), `${name} (${secs} s offline): only 「Agent J 主机离线」, never the reboot line`);
  }

  // OS restart: the Agent J host goes away and stays away; the phone page is even closed and reopened in between
  fake.setUp(false);
  await waitFor(p, `document.body.dataset.conn === 'off'`, 8000);   // P393: shown after the five-second window
  writeFileSync(join(OUT, 'zh-offline-early.png'), await shoot(p));
  await navigate(p, web.url);
  await waitState(p, 'waiting-host', 15000);
  ok(!(await evaluate(p, hint)), 'right after the drop: no hint yet');
  await waitFor(p, hint, HINT_AFTER_MS + 15000);
  const text = await evaluate(p, hint);
  ok(/Mac.*重启.*登录/.test(text), 'OS restart (offline past the threshold, cold page): the Mac line — ' + text);
  ok(await evaluate(p, `document.getElementById('status').dataset.state === 'waiting-host' && document.body.dataset.conn === 'off'`), 'shown only in the grey state');
  writeFileSync(join(OUT, 'zh-offline-mac-hint.png'), await shoot(p));
  fake.setUp(true); await waitState(p, 'ready'); await sleep(300);
  ok(!(await evaluate(p, hint)), 'back online (green): the line is gone');
  writeFileSync(join(OUT, 'zh-back-online.png'), await shoot(p));
  ok(p.problems.length === 0, 'no browser exceptions: ' + JSON.stringify(p.problems.slice(0, 3)));
} finally { await B.close(); await fake.stop(); await web.stop(); }
