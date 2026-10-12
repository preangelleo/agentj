#!/usr/bin/env node
// P181 item 9 · double-tap to lock the microphone, on Safari's engine (Playwright WebKit, iPhone 13 profile: touch, mobile
// viewport, iOS user agent) against the real page + the local fake host. Not a physical iPhone: WebKit's own touch → pointer
// event conversion, pointer capture and getUserMedia path are real; iOS UIKit gesture recognisers and the home-screen
// app's audio session are not.
//   1. records the exact event order WebKit delivers for one tap on the mic (the input for p181.mjs's synthetic orders)
//   2. 20 double taps, the gap between the taps 80 … 420 ms → how many locked (the button turned .lock)
//   --tree <dir>: run against another checkout (the negative control: the P154 baseline) → reports/qa/p181/webkit-<tag>.json
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';

const require = createRequire(import.meta.url);
const { webkit, devices } = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const ti = process.argv.indexOf('--tree');
const TREE = ti > 0 ? process.argv[ti + 1].replace(/\/?$/, '/') : ROOT;
const TAG = ti > 0 ? (process.argv[process.argv.indexOf('--tag') + 1] || 'other') : 'candidate';
const { startWebServer } = await import(pathToFileURL(TREE + 'web/test/serve.mjs').href);
const { startFakeHost } = await import(pathToFileURL(TREE + 'web/test/fakehost.mjs').href);
const OUT = ROOT + 'reports/qa/p181/';
mkdirSync(OUT, { recursive: true });

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const browser = await webkit.launch();
const res = { engine: 'WebKit (Playwright)', device: 'iPhone 13 profile', physical: false, tree: TAG };
let failed = 0;
try {
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  // This headless WebKit (WPE) build has no GStreamer media backend: any HTMLAudioElement (the read-aloud speaker unlocks
  // one on every pointerdown) or a real getUserMedia crashes the page process (GLib NULL-instance). So audio output is
  // off and the microphone is a stand-in with iOS-like timing: the stream arrives 150 ms after the request, MediaRecorder
  // collects nothing. The gesture path under test (touch → pointer events, pointer capture, the lock) is WebKit's own.
  await ctx.addInitScript(() => {
    const track = Object.assign(new EventTarget(), { stop() {}, kind: 'audio' });   // P182 listens for 'ended' (a real MediaStreamTrack is an EventTarget)
    const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
    navigator.mediaDevices.getUserMedia = () => new Promise((r) => setTimeout(() => r(stream), 150));
    window.MediaRecorder = class { constructor() { this.state = 'inactive'; this.mimeType = 'audio/mp4'; }
      start() { this.state = 'recording'; } stop() { this.state = 'inactive'; setTimeout(() => this.onstop && this.onstop(), 0); } };
    window.Audio = undefined;
    for (const k of ['play', 'pause', 'load']) HTMLMediaElement.prototype[k] = function () { return k === 'play' ? Promise.resolve() : undefined; };
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('crash', () => console.log('PAGE CRASH after', JSON.stringify(res.sofar || []), 'one tap:', JSON.stringify(res.oneTap || null)));
  await page.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost') + '?lang=zh'));
  await page.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 15000 });
  await fake.approve();
  await page.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 15000 });
  await page.evaluate(() => { for (const id of ['phone-env-dismiss', 'a2hs-ok', 'confirm-no']) { const b = document.getElementById(id); if (b && b.offsetParent) b.click(); } });
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await sleep(300);
  await page.evaluate(() => {
    const m = document.getElementById('mic');
    window.__ev = []; window.__locks = 0;
    for (const t of ['pointerdown', 'pointerup', 'pointercancel', 'gotpointercapture', 'lostpointercapture', 'touchstart', 'touchend', 'touchcancel', 'mousedown', 'mouseup', 'click', 'dblclick'])
      m.addEventListener(t, () => window.__ev.push(t), true);
    window.addEventListener('blur', () => window.__ev.push('window.blur'));
    new MutationObserver(() => { const on = m.classList.contains('lock'); if (on && !window.__was) window.__locks++; window.__was = on; }).observe(m, { attributes: true, attributeFilter: ['class'] });
  });
  const box = await page.locator('#mic').boundingBox();
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  // 1. one tap: what WebKit delivers, in order
  await page.touchscreen.tap(x, y);
  await sleep(900);
  res.oneTap = await page.evaluate(() => window.__ev.splice(0));
  res.gum = await page.evaluate(() => (window.__ajMediaDiagnostics || []).map((d) => d.kind));
  // 2. twenty double taps
  const gaps = Array.from({ length: 20 }, (_, i) => 80 + Math.round((i * 340) / 19));
  const tries = [];
  for (const gap of gaps) {
    const before = await page.evaluate(() => window.__locks);
    await page.touchscreen.tap(x, y);
    await sleep(gap);
    await page.touchscreen.tap(x + 2, y - 2);
    await sleep(150);
    const locked = (await page.evaluate(() => window.__locks)) > before;
    tries.push({ gap, locked, events: await page.evaluate(() => window.__ev.splice(0)) });
    res.sofar = tries.map((t) => [t.gap, t.locked, t.events.join(',')]);
    // end a still-locked take, then wait past the re-arm guard
    if (await page.evaluate(() => document.getElementById('mic').classList.contains('lock'))) await page.touchscreen.tap(x, y);
    await sleep(900);
    await page.evaluate(() => { window.__ev.length = 0; const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); });
  }
  res.locked = tries.filter((t) => t.locked).length;
  res.of = tries.length;
  res.tries = tries;
  res.errors = errors;
  console.log(`WebKit (${TAG}): one tap = ${res.oneTap.join(' → ')}`);
  console.log(`WebKit (${TAG}): ${res.locked}/${res.of} double taps locked; missed gaps: ${tries.filter((t) => !t.locked).map((t) => t.gap).join(', ') || 'none'}`);
  if (TAG === 'candidate' && res.locked < 19) { failed++; console.log('FAIL fewer than 19/20'); }
  if (errors.length) { failed++; console.log('FAIL page errors: ' + errors.join(' | ')); }
} catch (e) {
  failed++;
  console.log('FAIL ' + (e && e.stack || e));
} finally {
  writeFileSync(OUT + `webkit-${TAG}.json`, JSON.stringify(res, null, 1));
  await browser.close(); await fake.stop(); await web.stop();
}
process.exit(failed ? 1 : 0);
