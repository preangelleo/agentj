// P342 (#46) — the locked recording card's 4th icon 「说完直接发送」, in real Chromium (Android profile: touch, fake microphone)
// + the Noise fake host. Per language (zh, en) and width (390 phone, 344 / 280 folded outer screens):
//   - locked → the icons in one row under the circle (P346: cancel · end · send), none cut off, send rightmost;
//   - the send icon ends the take AND sends it: the transcribed words reach the Agent J host as a message, the field is left
//     empty, no second tap — also with the page blurred behind the card (P346 replaced the dimmed card);
//   - control: the end icon still only puts the words into the field (nothing sent).
// iOS Safari's engine: p342_rec_send_webkit.mjs. Shots → reports/qa/p342/rec-send-*.png.
// Usage: node web/test/p342_rec_send.mjs
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync, mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitFor, waitState, evaluate, shoot, rect, tap, sleep} from './browser.mjs';
import {wavBytes} from '../public/js/wav.js';

const out = fileURLToPath(new URL('../../../reports/qa/p342/', import.meta.url)); mkdirSync(out, {recursive: true});
const WORDS = '这是转写出来的文字';
const LABEL = {zh: '说完直接发送', en: 'Stop and send'};
const results = {};
const row = `(() => { const ids = ['pttCancel', 'pttEnd', 'pttSend'];
  const r = ids.map(i => document.getElementById(i).getBoundingClientRect());
  return {r: r.map(x => ({l: x.left, r: x.right, t: x.top, w: x.width})), vw: innerWidth, circle: document.querySelector('#ptt .pttc').getBoundingClientRect().bottom,
    label: document.getElementById('pttSend').getAttribute('aria-label'), title: document.getElementById('pttSend').title}; })()`;
async function lock(p){ const r = await rect(p, '#mic'); await tap(p, r.x, r.y); await sleep(80); await tap(p, r.x, r.y);
  await waitFor(p, `document.getElementById('mic').classList.contains('lock') && /^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`, 5000); }
async function clickEl(p, sel){ const r = await rect(p, sel); await tap(p, r.x, r.y); }
const sent = (S) => S.fake.st.says.filter((x) => (x.text || '').includes(WORDS)).length;

const dir = mkdtempSync(join(tmpdir(), 'aj-p342-tone-'));
const rate = 16000, x = new Float32Array(rate);
for (let i = 0; i < x.length; i++) x[i] = 0.2 * Math.sin(2 * Math.PI * 440 * i / rate);
const wav = join(dir, 'steady.wav'); writeFileSync(wav, wavBytes(x, rate));
let B;
try {
  B = await launch({args: [`--use-file-for-fake-audio-capture=${wav}`]});
  for (const [lang, w] of [['zh', 390], ['en', 390], ['zh', 344], ['zh', 280], ['en', 280]]){
    const web = await startWebServer(), fake = await startFakeHost(), S = {fake};
    const p = await newPage(B, w, 844, 'light', {touch: true, allow: [web.url, fake.relay + '/']});
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
      // the card: three icons (P346), one row, all inside the screen, send last
      await lock(p); await sleep(900);
      const g = await evaluate(p, row);
      assert.equal(g.label, LABEL[lang]); assert.equal(g.title, LABEL[lang]);
      assert.ok(g.r.every((b) => Math.abs(b.t - g.r[0].t) < 1), `${lang}-${w}: one row`);
      assert.ok(g.r.every((b) => b.l >= 0 && b.r <= g.vw && b.w >= 44), `${lang}-${w}: every icon on screen and ≥ 44px: ${JSON.stringify(g.r)}`);
      assert.ok(g.r.every((b, i) => !i || b.l > g.r[i - 1].r), `${lang}-${w}: cancel · end · send, left to right`);
      assert.ok(g.r[0].t > g.circle, `${lang}-${w}: icons under the circle`);
      writeFileSync(out + `rec-send-${lang}-${w}.png`, await shoot(p, ''));
      // send: one tap ends and sends
      const before = sent(S);
      await clickEl(p, '#pttSend');
      await waitFor(p, `document.getElementById('ptt').hidden`, 4000);
      for (let i = 0; i < 100 && sent(S) === before; i++) await sleep(100);
      assert.equal(sent(S), before + 1, `${lang}-${w}: the take was sent without a second tap`);
      await waitFor(p, `document.getElementById('input').value === ''`, 3000);
      results[`send-${lang}-${w}`] = 'PASS';
      if (w === 390){
        // P346: with the page blurred behind the card (the blur lifts with the card)
        await sleep(600); await lock(p); await sleep(700);
        assert.equal(await evaluate(p, `document.body.classList.contains('recblur')`), true, `${lang}: blurred`);
        const b2 = sent(S);
        await clickEl(p, '#pttSend');
        await waitFor(p, `document.getElementById('ptt').hidden && !document.body.classList.contains('recblur')`, 4000);
        for (let i = 0; i < 100 && sent(S) === b2; i++) await sleep(100);
        assert.equal(sent(S), b2 + 1, `${lang}: sent from the blurred card`);
        results[`send-blurred-${lang}`] = 'PASS';
        // control: end keeps the old behaviour (words into the field, nothing sent)
        await sleep(600); await lock(p); await sleep(900);
        const b3 = sent(S);
        await clickEl(p, '#pttEnd');
        await waitFor(p, `document.getElementById('input').value.includes(${JSON.stringify(WORDS)})`, 10000);
        await sleep(800);
        assert.equal(sent(S), b3, `${lang}: the end icon does not send`);
        results[`end-unchanged-${lang}`] = 'PASS';
      }
      assert.deepEqual(p.problems, []); assert.deepEqual(p.offsite, []);
    } finally { await fake.stop(); await web.stop(); }
  }
} finally { if (B) await B.close(); rmSync(dir, {recursive: true, force: true}); }
writeFileSync(out + 'rec-send-results.json', JSON.stringify(results, null, 1) + '\n');
console.log(JSON.stringify(results));
if (Object.keys(results).length !== 9) { console.log('FAILED'); process.exit(1); }
console.log('P342 rec send ALL PASS');
