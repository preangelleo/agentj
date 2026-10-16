// P342 (#46) — 「说完直接发送」 on Safari's engine (Playwright WebKit, iPhone 13 profile; 390 and a 280px narrow screen).
// WebKit on Linux has no audio stack, so the microphone and MediaRecorder are stand-ins that hand the page a real WAV take
// (audio/wav: the page sends it to the computer for transcription as is); everything after that is the page's own code:
// double tap locks, the card shows its icons in a row (P346: three), the send icon ends the take and the transcribed words reach the
// computer as a message with no second tap; the end icon still only fills the field. Shots → reports/qa/p342/rec-send-webkit-*.png.
// Usage: node web/test/p342_rec_send_webkit.mjs
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {webkit, devices} = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const out = new URL('../../../reports/qa/p342/', import.meta.url); mkdirSync(out, {recursive: true});
const WORDS = '这是转写出来的文字';
const LABEL = {zh: '说完直接发送', en: 'Stop and send'};
const STUBS = () => {
  window.AudioContext = undefined; window.webkitAudioContext = undefined;
  window.Audio = undefined; for (const k of ['play', 'pause', 'load']) HTMLMediaElement.prototype[k] = function(){ return k === 'play' ? Promise.resolve() : undefined; };
  const wav = () => {                                     // 1 s, 16 kHz mono 16-bit, a 440 Hz tone
    const n = 16000, b = new ArrayBuffer(44 + n * 2), v = new DataView(b), s = (o, t) => [...t].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    s(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); s(8, 'WAVEfmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); s(36, 'data'); v.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.round(6000 * Math.sin(2 * Math.PI * 440 * i / 16000)), true);
    return new Blob([b], {type: 'audio/wav'});
  };
  // P346: a fresh track per take, as a real microphone gives (the shared, already stopped one read as an interruption and
  // only passed because the interrupted card used to stay on screen)
  const mk = () => Object.assign(new EventTarget(), {kind: 'audio', readyState: 'live', stop(){ this.readyState = 'ended'; }, getSettings: () => ({noiseSuppression: false})});
  Object.defineProperty(navigator, 'mediaDevices', {configurable: true, value: {getUserMedia: async () => { const track = mk(); return {getAudioTracks: () => [track], getTracks: () => [track]}; }}});
  Object.defineProperty(navigator, 'wakeLock', {configurable: true, value: {request: async () => Object.assign(new EventTarget(), {released: false, release: async () => {}})}});
  window.MediaRecorder = class {
    static isTypeSupported(t){ return t === 'audio/wav'; }
    constructor(stream){ this.stream = stream; this.mimeType = 'audio/wav'; this.state = 'inactive'; }
    start(){ this.state = 'recording'; this.tick = setInterval(() => this.ondataavailable?.({data: new Blob([new Uint8Array(1)], {type: 'audio/wav'}).slice(0, 0)}), 1000); }
    stop(){ if (this.state === 'inactive') return; this.state = 'inactive'; clearInterval(this.tick);
      setTimeout(() => { this.ondataavailable?.({data: wav()}); this.onstop?.(); }, 30); }
  };
};
let web, fake, browser;
const results = {};
try {
  web = await startWebServer({port: 0, relayCsp: 'ws://127.0.0.1:*'}); fake = await startFakeHost(); browser = await webkit.launch();
  const sent = () => fake.st.says.filter((x) => (x.text || '').includes(WORDS)).length;
  for (const [lang, w] of [['zh', 390], ['en', 390], ['zh', 280]]){
    fake.reset();
    const ctx = await browser.newContext({...devices['iPhone 13'], viewport: {width: w, height: 844}});
    await ctx.addInitScript(STUBS);
    const page = await ctx.newPage(); const errors = []; page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost') + '?lang=' + lang));
    await page.waitForFunction(() => window.__ajState === 'awaiting-approval'); await fake.approve(); await page.waitForFunction(() => window.__ajState === 'ready');
    await page.evaluate(() => document.getElementById('phone-env-dismiss')?.click());
    const lock = async () => { const b = await page.locator('#mic').boundingBox(); const [x, y] = [b.x + b.width / 2, b.y + b.height / 2];
      await page.touchscreen.tap(x, y); await page.waitForTimeout(80); await page.touchscreen.tap(x, y);
      await page.waitForFunction(() => document.getElementById('mic').classList.contains('lock') && /^\d+:\d\d$/.test(document.getElementById('pttTime').textContent), null, {timeout: 5000}); };
    await lock(); await page.waitForTimeout(1200);
    const g = await page.evaluate(() => ({r: ['pttCancel', 'pttEnd', 'pttSend'].map((i) => { const x = document.getElementById(i).getBoundingClientRect(); return {l: x.left, r: x.right, t: x.top, w: x.width}; }),
      vw: innerWidth, label: document.getElementById('pttSend').getAttribute('aria-label')}));
    assert.equal(g.label, LABEL[lang]);
    assert.ok(g.r.every((b, i) => Math.abs(b.t - g.r[0].t) < 1 && b.l >= 0 && b.r <= g.vw && b.w >= 44 && (!i || b.l > g.r[i - 1].r)), `${lang}-${w}: three icons in one row (P346): ${JSON.stringify(g.r)}`);
    await page.screenshot({path: new URL(`rec-send-webkit-${lang}-${w}.png`, out).pathname});
    const before = sent();
    await page.locator('#pttSend').tap();
    await page.waitForFunction(() => document.getElementById('ptt').hidden, null, {timeout: 4000});
    for (let i = 0; i < 100 && sent() === before; i++) await page.waitForTimeout(100);
    assert.equal(sent(), before + 1, `${lang}-${w}: one tap sent the take`);
    assert.equal(await page.inputValue('#input'), '');
    results[`webkit-send-${lang}-${w}`] = 'PASS';
    if (w === 390){
      await page.waitForTimeout(600); await lock(); await page.waitForTimeout(1200);
      const b3 = sent();
      await page.locator('#pttEnd').tap();
      await page.waitForFunction((t) => document.getElementById('input').value.includes(t), WORDS, {timeout: 10000});
      await page.waitForTimeout(800);
      assert.equal(sent(), b3, `${lang}: the end icon does not send`);
      results[`webkit-end-unchanged-${lang}`] = 'PASS';
    }
    assert.deepEqual(errors, []);
    await ctx.close();
  }
} finally { await Promise.allSettled([browser?.close(), fake?.stop(), web?.stop()]); }
console.log(JSON.stringify(results));
if (Object.keys(results).length !== 5) { console.log('FAILED'); process.exit(1); }
console.log('P342 rec send WebKit ALL PASS');
