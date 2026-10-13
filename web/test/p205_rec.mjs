// P205 item 5 — the recording card and the silent-gap notice, in real Chromium (fake microphone) + the Noise fake host.
//   A. 390×844 touch (zh, en): hold to talk → the centred dark circle (square box, round, centred, ring live from the meter),
//      no icons; double tap → locked: end / dim / cancel icons + the "don't press the power button" note; dim → black, a tap
//      elsewhere does nothing; the end icon ends (words in the field); the cancel icon cancels; mute while hidden → unmute →
//      back on the page: "0:0x–0:0y came in silent", and again when the take ends.
//   B. a fake microphone file with 5 s of digital zeros in the middle: the opened track has noiseSuppression false, and the
//      finished take is told 「录音里 0:0x–0:0y 没有声音」 (live meter + decoded take).
//   C. 1440×900 mouse: the same circle in the middle of a desktop; Esc cancels a locked take.
// Shots → reports/qa/p205/rec-*.png. Not covered here: a real Android / iPhone (the report says what Leo should try).
// Usage: node web/test/p205_rec.mjs
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync, mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, shoot, rect, tap, key, sleep} from './browser.mjs';
import {wavBytes} from '../public/js/wav.js';
import { parityRecorder } from '../../parity/lib.mjs';

const out = fileURLToPath(new URL('../../../reports/qa/p205/', import.meta.url)); mkdirSync(out, {recursive: true});
const results = {};
const T = {
  zh: {hint: '松开转成文字', awake: '请别按电源键锁屏', dimNote: '暗屏中', live: '没有声音，可能没录上', gap: '录音里 ', words: '这是转写出来的文字', cancelled: '已取消'},
  en: {hint: 'Let go to turn it into text', awake: "Please don't press the power button", dimNote: 'Dimmed', live: 'came in silent and may be missing', gap: 'of the recording came in silent', words: '这是转写出来的文字', cancelled: 'Cancelled'},
};
const HOOKS = `(() => {
  const g = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia = async (c) => { window.__gum = c; return (window.__stream = await g(c)); };
  window.__wl = 0; Object.defineProperty(navigator, 'wakeLock', {configurable: true, value: {request: async () => { window.__wl++; const w = new EventTarget(); w.released = false; w.release = async () => { w.released = true; }; return w; }}});
  window.__vis = 'visible';
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => window.__vis});
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => window.__vis !== 'visible'});
  window.__toasts = []; new MutationObserver(() => { const t = document.getElementById('toast').textContent; if (t && window.__toasts.at(-1) !== t) window.__toasts.push(t); })
    .observe(document.getElementById('toast'), {childList: true, characterData: true, subtree: true});
})()`;
const setVis = (v) => `(() => { window.__vis = ${JSON.stringify(v)}; document.dispatchEvent(new Event('visibilitychange')); })()`;
const geo = `(() => { const c = document.querySelector('#ptt .pttc'), r = c.getBoundingClientRect(), s = getComputedStyle(c);
  return {w: r.width, h: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2, vw: innerWidth, vh: innerHeight, radius: s.borderRadius, bg: s.backgroundColor,
    live: c.dataset.live || '', lv: +(c.style.getPropertyValue('--lv') || 0), act: document.getElementById('pttAct').hidden, note: document.getElementById('pttNote').hidden ? '' : document.getElementById('pttNote').textContent,
    hint: document.getElementById('pttHint').textContent, dim: document.getElementById('ptt').classList.contains('dim'), pos: getComputedStyle(document.getElementById('ptt')).position,
    hl: (() => { const h = document.getElementById('pttHint'); return Math.round(h.getBoundingClientRect().height / parseFloat(getComputedStyle(h).lineHeight)); })()}; })()`;
function centred(g, what){
  assert.ok(Math.abs(g.w - g.h) < 1, `${what}: square box ${g.w}×${g.h}`);
  assert.ok(/50%|9\d\dpx|1\d\dpx/.test(g.radius) || parseFloat(g.radius) >= g.w / 2 - 1, `${what}: round (${g.radius})`);
  assert.ok(Math.abs(g.cx - g.vw / 2) < 2, `${what}: centred x ${g.cx} / ${g.vw}`);
  assert.ok(Math.abs(g.cy - g.vh / 2) < g.vh * 0.2, `${what}: in the middle y ${g.cy} / ${g.vh}`);
  assert.equal(g.pos, 'fixed');
  assert.ok(g.hl >= 1 && g.hl <= 2, `${what}: the hint reads across (${g.hl} lines)`);
}
async function hold(p, x, y){ await p.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x, y}]}); }
async function release(p){ await p.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []}); }
async function lock(p){ const r = await rect(p, '#mic'); await tap(p, r.x, r.y); await sleep(80); await tap(p, r.x, r.y);
  await waitFor(p, `document.getElementById('mic').classList.contains('lock') && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 5000); }
async function clickEl(p, sel){ const r = await rect(p, sel); await tap(p, r.x, r.y); }
async function reset(p){
  await evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); const n = document.getElementById('toast'); clearTimeout(n._t); n.classList.remove('on', 'act'); n.textContent = ''; window.__toasts = []; })()`);
  await sleep(700);
}
const toasts = (p) => evaluate(p, `JSON.stringify(window.__toasts)`).then(JSON.parse);
async function pair(B, w, h, language, opts){
  const web = await startWebServer(), fake = await startFakeHost();
  const p = await newPage(B, w, h, 'light', {...opts, allow: [web.url, fake.relay + '/']});
  await navigate(p, fake.newPairing(web.url + '?lang=' + language)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, HOOKS);
  return {p, web, fake, async stop(){ await fake.stop(); await web.stop(); }};
}

// ---- A. phone, both languages ----------------------------------------------------------------------------------------
let B = await launch();
try {
  for (const language of ['zh', 'en']){
    const L = T[language];
    const S = await pair(B, 390, 844, language, {touch: true}), p = S.p;
    try {
      // hold to talk
      const m = await rect(p, '#mic');
      await hold(p, m.x, m.y);
      await waitFor(p, `/^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 5000);
      let lvMax = 0, g;
      for (let i = 0; i < 12; i++){ await sleep(120); g = await evaluate(p, geo); lvMax = Math.max(lvMax, g.lv); }
      centred(g, 'hold');
      assert.ok(g.act, 'no icons while a finger holds the take');
      assert.ok(g.hint.includes(L.hint), g.hint);
      assert.equal(g.live, '1', 'the analyser runs'); assert.ok(lvMax > 0.05, `the ring follows the fake microphone (lv max ${lvMax})`);
      const gum = await evaluate(p, `JSON.stringify(window.__gum)`);
      assert.equal(gum, JSON.stringify({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: true}}));
      assert.equal(await evaluate(p, `window.__stream.getAudioTracks()[0].getSettings().noiseSuppression`), false);
      writeFileSync(out + `rec-hold-${language}-390.png`, await shoot(p, ''));
      await release(p);
      await waitFor(p, `document.getElementById('ptt').hidden`, 4000);
      await waitFor(p, `document.getElementById('input').value.includes(${JSON.stringify(L.words)})`, 10000);
      results['hold-circle-' + language] = 'PASS';
      await reset(p);

      // locked: icons + note; dim; a stray tap does nothing; end icon ends
      await lock(p);
      await sleep(600);
      g = await evaluate(p, geo);
      centred(g, 'locked');
      assert.ok(!g.act, 'icons under the circle'); assert.ok(g.note.includes(L.awake), g.note);
      writeFileSync(out + `rec-lock-${language}-390.png`, await shoot(p, ''));
      await clickEl(p, '#pttDim');
      await waitFor(p, `document.getElementById('ptt').classList.contains('dim') && document.body.classList.contains('recdim')`, 2000);
      await sleep(450);                                                            // the .25 s fade to black
      g = await evaluate(p, geo);
      assert.ok(g.note.includes(L.dimNote), g.note);
      assert.equal(await evaluate(p, `getComputedStyle(document.getElementById('ptt')).backgroundColor`), 'rgb(0, 0, 0)');
      writeFileSync(out + `rec-dim-${language}-390.png`, await shoot(p, ''));
      await tap(p, 195, 120); await tap(p, 60, 800); await sleep(300);           // pocket taps: swallowed by the black layer
      assert.equal(await evaluate(p, `!document.getElementById('ptt').hidden && document.getElementById('mic').classList.contains('lock')`), true, 'still recording');
      assert.equal(await evaluate(p, `localStorage.getItem('aj.recDim')`), '1', 'the brightness choice is remembered');
      await clickEl(p, '#pttDim');
      await waitFor(p, `!document.getElementById('ptt').classList.contains('dim')`, 2000);
      await clickEl(p, '#pttEnd');
      await waitFor(p, `document.getElementById('ptt').hidden`, 4000);
      await waitFor(p, `document.getElementById('input').value.includes(${JSON.stringify(L.words)})`, 10000);
      assert.equal(await evaluate(p, `document.body.classList.contains('recdim')`), false);
      results['lock-icons-dim-end-' + language] = 'PASS';
      await reset(p);

      // cancel icon
      await lock(p); await sleep(900);
      await clickEl(p, '#pttCancel');
      await waitFor(p, `document.getElementById('ptt').hidden`, 4000);
      await sleep(1500);
      assert.equal(await evaluate(p, `document.getElementById('input').value`), '');
      assert.ok((await toasts(p)).some((x) => x.includes(L.cancelled)), JSON.stringify(await toasts(p)));
      results['lock-cancel-icon-' + language] = 'PASS';
      await reset(p);

      // the system mutes the microphone while the screen is dark, gives it back before the owner returns
      await lock(p); await sleep(2000);
      await evaluate(p, setVis('hidden'));
      await evaluate(p, `window.__stream.getAudioTracks()[0].dispatchEvent(new Event('mute'))`);
      await sleep(4000);
      await evaluate(p, `window.__stream.getAudioTracks()[0].dispatchEvent(new Event('unmute'))`);
      await sleep(300);
      await evaluate(p, setVis('visible'));
      await waitFor(p, `document.getElementById('toast').textContent.includes(${JSON.stringify(L.live)})`, 3000);
      const liveTxt = await evaluate(p, `document.getElementById('toast').textContent`);
      assert.match(liveTxt, /0:0[12]–0:0[56]/, liveTxt);
      writeFileSync(out + `rec-gaplive-${language}-390.png`, await shoot(p, ''));
      await sleep(1200);
      await clickEl(p, '#pttEnd');
      await waitFor(p, `document.getElementById('toast').textContent.includes(${JSON.stringify(L.gap)})`, 12000);
      const endTxt = await evaluate(p, `document.getElementById('toast').textContent`);
      assert.match(endTxt, /0:0[12]–0:0[56]/, endTxt);
      writeFileSync(out + `rec-gapend-${language}-390.png`, await shoot(p, ''));
      results['mute-gap-told-' + language] = 'PASS';
      await reset(p);
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
    } finally { await S.stop(); }
  }
} finally { await B.close(); }

// ---- B. digital zeros in the microphone itself ---------------------------------------------------------------------
{
  const dir = mkdtempSync(join(tmpdir(), 'aj-p205-'));
  const rate = 16000, x = new Float32Array(rate * 12);
  let seed = 3; const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff) * 2 - 1;
  for (let i = 0; i < x.length; i++){ const s = i / rate; x[i] = s >= 3 && s < 8 ? 0 : 0.15 * Math.sin(i / 7) * (Math.floor(s * 2) % 2) + 0.003 * rnd(); }
  const wav = join(dir, 'gap.wav'); writeFileSync(wav, wavBytes(x, rate));
  B = await launch({args: [`--use-file-for-fake-audio-capture=${wav}%noloop`]});
  const S = await pair(B, 390, 844, 'zh', {touch: true}), p = S.p;
  try {
    await lock(p);
    await sleep(11000);
    await clickEl(p, '#pttEnd');
    await waitFor(p, `document.getElementById('toast').textContent.includes('录音里 ')`, 15000);
    const txt = await evaluate(p, `document.getElementById('toast').textContent`);
    assert.match(txt, /0:0[2-4]–0:0[7-9]/, txt);
    writeFileSync(out + `rec-zeros-zh-390.png`, await shoot(p, ''));
    results['digital-zeros-told'] = 'PASS';
  } finally { await S.stop(); await B.close(); }
}

// ---- C. desktop ----------------------------------------------------------------------------------------------------
B = await launch();
{
  const S = await pair(B, 1440, 900, 'zh', {}), p = S.p;
  try {
    const m = await rect(p, '#mic');
    for (let i = 0; i < 2; i++){
      await p.send('Input.dispatchMouseEvent', {type: 'mousePressed', x: m.x, y: m.y, button: 'left', clickCount: 1});
      await p.send('Input.dispatchMouseEvent', {type: 'mouseReleased', x: m.x, y: m.y, button: 'left', clickCount: 1});
      await sleep(90);
    }
    await waitFor(p, `document.getElementById('mic').classList.contains('lock') && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 5000);
    await sleep(700);
    const g = await evaluate(p, geo);
    centred(g, 'desktop');
    assert.ok(!g.act); assert.equal(g.note, '', 'no phone-only advice on a desktop');
    const lines = await evaluate(p, `Math.round(document.getElementById('pttHint').getBoundingClientRect().height / parseFloat(getComputedStyle(document.getElementById('pttHint')).lineHeight))`);
    assert.ok(lines <= 2, `the hint reads across, ${lines} lines`);
    writeFileSync(out + `rec-lock-zh-1440.png`, await shoot(p, ''));
    await key(p, 'Escape', {code: 'Escape', keyCode: 27});
    await waitFor(p, `document.getElementById('ptt').hidden`, 4000);
    results['desktop-circle-esc'] = 'PASS';
  } finally { await S.stop(); await B.close(); }
}
writeFileSync(out + 'rec-results.json', JSON.stringify(results, null, 1) + '\n');
{ const rec = parityRecorder('web/test/p205_rec.mjs'); for (const [k, v] of Object.entries(results)) rec.record(k, v === 'PASS'); rec.write(); }
console.log(JSON.stringify(results));
if (Object.keys(results).length !== 10) { console.log('FAILED'); process.exit(1); }
console.log('ALL PASS');
