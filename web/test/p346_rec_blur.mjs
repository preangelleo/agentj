// P346 (#54) — the recording card's page blur in its own state colour + the three icons, in real Chromium (Android profile:
// touch, fake microphone) + the Noise fake host. Per language (zh, en) and width (390 phone, 344 folded outer screen):
//   - hold to talk (finger down): the whole page behind the card is blurred under a veil of the page's own colour, no icons;
//     release lifts it;
//   - locked (double tap): the same blur; the veil follows every state colour (idle / working / waiting / question / offline)
//     and a theme's own colour; three icons in one row — cancel · end · end and send, each with its label;
//   - cancel erases (nothing transcribed, nothing sent), Esc does the same, end puts the words in the field, send sends.
// The veil is measured, not trusted: the card's computed background is painted on a 1×1 canvas and must carry the page's own
// RGB at the CSS alpha (58 %). iOS Safari's engine: p346_rec_blur_webkit.mjs. Shots → reports/qa/p346/rec-blur-*.png.
// Usage: node web/test/p346_rec_blur.mjs
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync, mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, shoot, rect, tap, key, sleep} from './browser.mjs';
import {wavBytes} from '../public/js/wav.js';

const out = process.env.AJ_P375_VOICE_OUT || fileURLToPath(new URL('../../../reports/qa/p346/', import.meta.url)); mkdirSync(out, {recursive: true});
const WORDS = '这是转写出来的文字';
const LABELS = {zh: ['取消录音', '结束录音', '说完直接发送'], en: ['Cancel recording', 'End recording', 'Stop and send']};
const STATES = [['idle', ''], ['working', ''], ['waiting', ''], ['question', 'question'], ['offline', '']];
const results = {};
// the veil: card background vs the page colour, both painted on a canvas → [r, g, b, a]
const VEIL = `(() => { const px = (c) => { const cv = document.createElement('canvas'); cv.width = cv.height = 1; const x = cv.getContext('2d');
    x.clearRect(0, 0, 1, 1); x.fillStyle = 'rgba(0,0,0,0)'; x.fillStyle = c; x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data]; };
  const box = document.getElementById('ptt'), cs = getComputedStyle(box), r = box.getBoundingClientRect();
  return {veil: px(cs.backgroundColor), page: px(getComputedStyle(document.body).backgroundColor), blur: cs.backdropFilter || cs.webkitBackdropFilter || '',
    cover: r.left <= 0 && r.top <= 0 && r.right >= innerWidth && r.bottom >= innerHeight, z: +cs.zIndex,
    topZ: +getComputedStyle(document.querySelector('.top')).zIndex, act: document.getElementById('pttAct').hidden,
    body: document.body.classList.contains('recblur')}; })()`;
function veilOk(v, what){
  assert.match(v.blur, /blur\(\d+px\)/, `${what}: backdrop blur (${v.blur})`);
  assert.ok(v.cover && v.z > v.topZ, `${what}: covers the whole page, top line included`);
  assert.ok(v.body, `${what}: body.recblur`);
  for (let i = 0; i < 3; i++) assert.ok(Math.abs(v.veil[i] - v.page[i]) <= 3, `${what}: the veil is the page's own colour ${v.veil} vs ${v.page}`);
  assert.ok(Math.abs(v.veil[3] - 148) <= 4, `${what}: half see-through (${v.veil[3]})`);
}
async function setState(p, st, kind, custom){
  await evaluate(p, `(() => { const b = document.body; b.dataset.conn = 'on'; b.dataset.agent = 'none';
    b.dataset.status = ${JSON.stringify(st === 'offline' ? 'unknown' : st === 'question' ? 'waiting' : st)};
    if (${JSON.stringify(kind)}) b.dataset.kind = ${JSON.stringify(kind)}; else delete b.dataset.kind;
    if (${JSON.stringify(custom || '')}) b.style.setProperty('--c-working', ${JSON.stringify(custom || '')}); else b.style.removeProperty('--c-working'); })()`);
  await sleep(350);   // the .25 s background transition
}
async function lock(p){ const r = await rect(p, '#mic'); await tap(p, r.x, r.y); await sleep(80); await tap(p, r.x, r.y);
  await waitFor(p, `document.getElementById('mic').classList.contains('lock') && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 5000); }
async function clickEl(p, sel){ const r = await rect(p, sel); await tap(p, r.x, r.y); }
const gone = `document.getElementById('ptt').hidden && !document.body.classList.contains('recblur')`;
const sent = (S) => S.fake.st.says.filter((x) => (x.text || '').includes(WORDS)).length;
const clear = (p) => evaluate(p, `(() => { const i = document.getElementById('input'); i.value = ''; i.dispatchEvent(new Event('input')); })()`);

const dir = mkdtempSync(join(tmpdir(), 'aj-p346-tone-'));
const rate = 16000, x = new Float32Array(rate);
for (let i = 0; i < x.length; i++) x[i] = 0.2 * Math.sin(2 * Math.PI * 440 * i / rate);
const wav = join(dir, 'steady.wav'); writeFileSync(wav, wavBytes(x, rate));
let B;
try {
  B = await launch({args: [`--use-file-for-fake-audio-capture=${wav}`]});
  for (const [lang, w] of [['zh', 390], ['en', 390], ['zh', 344], ['en', 344]]){
    const web = await startWebServer(), fake = await startFakeHost(), S = {fake};
    const p = await newPage(B, w, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
    const tag = `${lang}-${w}`;
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
      await setState(p, 'working', '');
      // A. hold to talk: the same blur, no icons; release lifts it
      const m = await rect(p, '#mic');
      await p.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x: m.x, y: m.y}]});
      await waitFor(p, `!document.getElementById('ptt').hidden && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 5000);
      await sleep(700);
      let v = await evaluate(p, VEIL);
      veilOk(v, `${tag} hold`); assert.ok(v.act, `${tag} hold: no icons (release ends a held take)`);
      assert.equal(await evaluate(p, `document.getElementById('ptt').classList.contains('lock')`), false);
      writeFileSync(out + `rec-blur-hold-${tag}.png`, await shoot(p, ''));
      await p.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
      await waitFor(p, gone, 4000);
      results[`hold-blur-${tag}`] = 'PASS';
      await sleep(1500); await clear(p);
      // B. locked: the veil follows every state colour and a theme's own colour
      await lock(p); await sleep(600);
      const seen = new Set();
      for (const [st, kind] of STATES){
        await setState(p, st, kind);
        v = await evaluate(p, VEIL); veilOk(v, `${tag} lock ${st}`); assert.ok(!v.act, `${tag}: icons shown`);
        seen.add(v.page.slice(0, 3).join());
        if (w === 390 || st === 'working') writeFileSync(out + `rec-blur-lock-${st}-${tag}.png`, await shoot(p, ''));
      }
      assert.ok(seen.size >= 4, `${tag}: the state colours differ (${[...seen].join(' | ')})`);
      await setState(p, 'working', '', '#1f5fae');   // a theme that paints "working" blue
      v = await evaluate(p, VEIL); veilOk(v, `${tag} theme colour`);
      assert.deepEqual(v.page.slice(0, 3), [0x1f, 0x5f, 0xae], `${tag}: the theme's own colour`);
      if (w === 390) writeFileSync(out + `rec-blur-lock-theme-${tag}.png`, await shoot(p, ''));
      await setState(p, 'working', '');
      results[`lock-state-colours-${tag}`] = 'PASS';
      // C. three icons: one row, labelled, cancel · end · send
      const g = await evaluate(p, `(() => { const ids = ['pttCancel', 'pttEnd', 'pttSend'];
        return {r: ids.map((i) => { const x = document.getElementById(i).getBoundingClientRect(); return {l: x.left, r: x.right, t: x.top, w: x.width}; }),
          labels: ids.map((i) => document.getElementById(i).getAttribute('aria-label')), group: document.getElementById('pttAct').getAttribute('aria-label'),
          n: document.querySelectorAll('#pttAct button').length, vw: innerWidth, circle: document.querySelector('#ptt .pttc').getBoundingClientRect().bottom}; })()`);
      assert.equal(g.n, 3, `${tag}: exactly three icons`);
      assert.deepEqual(g.labels, LABELS[lang]);
      assert.equal(g.group, lang === 'zh' ? '录音操作' : 'Recording controls');
      assert.ok(g.r.every((b, i) => Math.abs(b.t - g.r[0].t) < 1 && b.l >= 0 && b.r <= g.vw && b.w >= 44 && (!i || b.l - g.r[i - 1].r >= 16)), `${tag}: one row, spaced: ${JSON.stringify(g.r)}`);
      assert.ok(g.r[0].t > g.circle, `${tag}: under the circle`);
      const b0 = sent(S);
      await clickEl(p, '#pttCancel');
      await waitFor(p, gone, 4000); await sleep(1500);
      assert.equal(await evaluate(p, `document.getElementById('input').value`), '', `${tag}: cancel erases`);
      assert.equal(sent(S), b0, `${tag}: cancel sends nothing`);
      results[`cancel-${tag}`] = 'PASS';
      await lock(p); await sleep(600);
      await key(p, 'Escape', {code: 'Escape', keyCode: 27});
      await waitFor(p, gone, 4000); await sleep(1500);
      assert.equal(await evaluate(p, `document.getElementById('input').value`), '', `${tag}: Esc erases`);
      assert.equal(sent(S), b0, `${tag}: Esc sends nothing`);
      results[`esc-${tag}`] = 'PASS';
      await lock(p); await sleep(900);
      await clickEl(p, '#pttEnd');
      await waitFor(p, gone, 4000);
      await waitFor(p, `document.getElementById('input').value.includes(${JSON.stringify(WORDS)})`, 10000); await sleep(800);
      assert.equal(sent(S), b0, `${tag}: end does not send`);
      results[`end-${tag}`] = 'PASS';
      await clear(p); await sleep(600);
      await lock(p); await sleep(900);
      await clickEl(p, '#pttSend');
      await waitFor(p, gone, 4000);
      for (let i = 0; i < 100 && sent(S) === b0; i++) await sleep(100);
      assert.equal(sent(S), b0 + 1, `${tag}: send sends`);
      assert.equal(await evaluate(p, `['input','workflow-compose'].includes(document.activeElement.id)`), false, `${tag}: P375 voice send releases composer focus`);
      results[`send-${tag}`] = 'PASS';
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
    } finally { await fake.stop(); await web.stop(); }
  }
} finally { if (B) await B.close(); rmSync(dir, {recursive: true, force: true}); }
writeFileSync(out + 'rec-blur-results.json', JSON.stringify(results, null, 1) + '\n');
console.log(JSON.stringify(results));
if (Object.keys(results).length !== 24) { console.log('FAILED'); process.exit(1); }
console.log('P346 rec blur ALL PASS');
