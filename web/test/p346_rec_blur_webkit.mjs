// P346 (#54) — the recording card's page blur + three icons on Safari's engine (Playwright WebKit, iPhone 13 profile; 390 and
// a 344px folded outer screen, zh / en). WebKit on Linux has no audio stack, so the microphone and MediaRecorder are stand-ins
// that hand the page a real WAV take (as p342_rec_send_webkit.mjs); the rest is the page's own code:
//   - hold to talk (pointer held on the mic): the page behind blurred (backdrop-filter) under its own colour, no icons;
//   - locked: the same blur; the veil follows the state colours and a theme's own colour (measured on a canvas);
//   - three icons in one row: cancel erases (nothing sent), Esc too, end fills the field, send sends.
// Shots → reports/qa/p346/rec-blur-webkit-*.png. Not in the parity run (needs the owner's Playwright + browsers).
// Usage: node web/test/p346_rec_blur_webkit.mjs
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const {webkit, devices} = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const out = new URL('../../../reports/qa/p346/', import.meta.url); mkdirSync(out, {recursive: true});
const WORDS = '这是转写出来的文字';
const LABELS = {zh: ['取消录音', '结束录音', '说完直接发送'], en: ['Cancel recording', 'End recording', 'Stop and send']};
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
  // a fresh track per take, as a real microphone gives (a shared, already stopped one reads as an interruption)
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
const VEIL = () => { const px = (c) => { const cv = document.createElement('canvas'); cv.width = cv.height = 1; const x = cv.getContext('2d');
    x.clearRect(0, 0, 1, 1); x.fillStyle = 'rgba(0,0,0,0)'; x.fillStyle = c; x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data]; };
  const box = document.getElementById('ptt'), cs = getComputedStyle(box), r = box.getBoundingClientRect();
  return {veil: px(cs.backgroundColor), page: px(getComputedStyle(document.body).backgroundColor), blur: cs.backdropFilter || cs.webkitBackdropFilter || '',
    cover: r.left <= 0 && r.top <= 0 && r.right >= innerWidth && r.bottom >= innerHeight, act: document.getElementById('pttAct').hidden,
    body: document.body.classList.contains('recblur')}; };
function veilOk(v, what){
  assert.match(v.blur, /blur\(\d+px\)/, `${what}: backdrop blur (${v.blur})`);
  assert.ok(v.cover && v.body, `${what}: covers the page`);
  for (let i = 0; i < 3; i++) assert.ok(Math.abs(v.veil[i] - v.page[i]) <= 3, `${what}: the page's own colour ${v.veil} vs ${v.page}`);
  assert.ok(Math.abs(v.veil[3] - 148) <= 4, `${what}: half see-through (${v.veil[3]})`);
}
const STATE = ([st, custom]) => { const b = document.body; b.dataset.conn = 'on'; b.dataset.agent = 'none';
  b.dataset.status = st === 'offline' ? 'unknown' : st === 'question' ? 'waiting' : st;
  if (st === 'question') b.dataset.kind = 'question'; else delete b.dataset.kind;
  if (custom) b.style.setProperty('--c-working', custom); else b.style.removeProperty('--c-working'); };
const gone = () => document.getElementById('ptt').hidden && !document.body.classList.contains('recblur');
let web, fake, browser;
const results = {};
try {
  web = await startWebServer({port: 0, relayCsp: 'ws://127.0.0.1:*'}); fake = await startFakeHost(); browser = await webkit.launch();
  const sent = () => fake.st.says.filter((x) => (x.text || '').includes(WORDS)).length;
  for (const [lang, w] of [['zh', 390], ['en', 390], ['zh', 344]]){
    fake.reset();
    const tag = `${lang}-${w}`;
    const ctx = await browser.newContext({...devices['iPhone 13'], viewport: {width: w, height: 844}});
    await ctx.addInitScript(STUBS);
    const page = await ctx.newPage(); const errors = []; page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost') + '?lang=' + lang));
    await page.waitForFunction(() => window.__ajState === 'awaiting-approval'); await fake.approve(); await page.waitForFunction(() => window.__ajState === 'ready');
    await page.evaluate(() => document.getElementById('phone-env-dismiss')?.click());
    const st = async (s, custom) => { await page.evaluate(STATE, [s, custom || '']); await page.waitForTimeout(350); };
    const clear = () => page.evaluate(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); });
    const b = await page.locator('#mic').boundingBox(); const [mx, my] = [b.x + b.width / 2, b.y + b.height / 2];
    const lock = async () => { await page.touchscreen.tap(mx, my); await page.waitForTimeout(80); await page.touchscreen.tap(mx, my);
      await page.waitForFunction(() => document.getElementById('mic').classList.contains('lock') && /^\d+:\d\d$/.test(document.getElementById('pttTime').textContent), null, {timeout: 5000}); };
    await st('working');
    let v;
    // hold to talk: a held pointer on the mic
    await page.mouse.move(mx, my); await page.mouse.down();
    await page.waitForFunction(() => !document.getElementById('ptt').hidden && !document.getElementById('ptt').classList.contains('lock'), null, {timeout: 5000});
    await page.waitForTimeout(700);
    v = await page.evaluate(VEIL); veilOk(v, `${tag} hold`); assert.ok(v.act, `${tag} hold: no icons`);
    await page.screenshot({path: new URL(`rec-blur-webkit-hold-${tag}.png`, out).pathname});
    await page.mouse.up();
    await page.waitForFunction(gone, null, {timeout: 4000});
    results[`webkit-hold-blur-${tag}`] = 'PASS';
    await page.waitForTimeout(1500); await clear();
    // locked: state colours + a theme's own colour
    await lock(); await page.waitForTimeout(800);
    const seen = new Set();
    for (const s of ['idle', 'working', 'waiting', 'question', 'offline']){
      await st(s); v = await page.evaluate(VEIL); veilOk(v, `${tag} lock ${s}`); assert.ok(!v.act); seen.add(v.page.slice(0, 3).join());
      if (s === 'working' || (w === 390 && s === 'waiting')) await page.screenshot({path: new URL(`rec-blur-webkit-lock-${s}-${tag}.png`, out).pathname});
    }
    assert.ok(seen.size >= 4, `${tag}: state colours differ`);
    await st('working', '#1f5fae'); v = await page.evaluate(VEIL); veilOk(v, `${tag} theme`); assert.deepEqual(v.page.slice(0, 3), [0x1f, 0x5f, 0xae]);
    await st('working');
    const g = await page.evaluate(() => ({r: ['pttCancel', 'pttEnd', 'pttSend'].map((i) => { const x = document.getElementById(i).getBoundingClientRect(); return {l: x.left, r: x.right, t: x.top, w: x.width}; }),
      labels: ['pttCancel', 'pttEnd', 'pttSend'].map((i) => document.getElementById(i).getAttribute('aria-label')), n: document.querySelectorAll('#pttAct button').length, vw: innerWidth}));
    assert.equal(g.n, 3); assert.deepEqual(g.labels, LABELS[lang]);
    assert.ok(g.r.every((x, i) => Math.abs(x.t - g.r[0].t) < 1 && x.l >= 0 && x.r <= g.vw && x.w >= 44 && (!i || x.l - g.r[i - 1].r >= 16)), `${tag}: one row: ${JSON.stringify(g.r)}`);
    results[`webkit-lock-colours-${tag}`] = 'PASS';
    const b0 = sent();
    await page.locator('#pttCancel').tap();
    await page.waitForFunction(gone, null, {timeout: 4000}); await page.waitForTimeout(1500);
    assert.equal(await page.inputValue('#input'), ''); assert.equal(sent(), b0, `${tag}: cancel sends nothing`);
    await lock(); await page.waitForTimeout(800);
    await page.keyboard.press('Escape');
    await page.waitForFunction(gone, null, {timeout: 4000}); await page.waitForTimeout(1500);
    assert.equal(await page.inputValue('#input'), ''); assert.equal(sent(), b0, `${tag}: Esc sends nothing`);
    await lock(); await page.waitForTimeout(1200);
    await page.locator('#pttEnd').tap();
    await page.waitForFunction((t) => document.getElementById('input').value.includes(t), WORDS, {timeout: 10000}); await page.waitForTimeout(800);
    assert.equal(sent(), b0, `${tag}: end does not send`);
    await clear(); await page.waitForTimeout(600);
    await lock(); await page.waitForTimeout(1200);
    await page.locator('#pttSend').tap();
    await page.waitForFunction(gone, null, {timeout: 4000});
    for (let i = 0; i < 100 && sent() === b0; i++) await page.waitForTimeout(100);
    assert.equal(sent(), b0 + 1, `${tag}: send sends`);
    results[`webkit-buttons-${tag}`] = 'PASS';
    assert.deepEqual(errors, []);
    await ctx.close();
  }
} finally { await Promise.allSettled([browser?.close(), fake?.stop(), web?.stop()]); }
console.log(JSON.stringify(results));
if (Object.keys(results).length !== 9) { console.log('FAILED'); process.exit(1); }
console.log('P346 rec blur WebKit ALL PASS');
