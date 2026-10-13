#!/usr/bin/env node
// P239 · the header model · effort pill: a tap only changes the label (at once); each half commits 5 s after its last tap;
// a tap back to what runs sends nothing; a refusal rolls the label back with one toast; leaving the page sends a waiting
// pick at once; the waiting look is a faded half with a thin line running out under it (no fill). Real page against the
// fake host, own headless Chromium (never :9222), phone size with touch.
//   node web/test/p239.mjs        (screenshots + a screencast → reports/qa/p239/)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, tap, rect, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P239_SHOTS || join(ROOT, 'reports/qa/p239');
mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (v, n) => { assert.ok(v, n); console.log('PASS ' + n); results.push({ ok: true, name: n }); };

const MODELS = { t: 'models', models: [{ id: 'opus', name: 'Opus 5.5', efforts: ['low', 'medium', 'high'], cur: true },
  { id: 'sonnet', name: 'Sonnet 5.5', efforts: ['low', 'medium', 'high'] }, { id: 'haiku', name: 'Haiku 5.5', efforts: null },
  { id: 'fable', name: 'Fable 5.1', efforts: ['low', 'medium', 'high'] }], effort: 'medium', default: { model: 'opus', effort: 'medium' } };
const METER = { t: 'meter', model: 'opus', model_name: 'Opus 5.5', effort: 'medium', ctx: { used: 40, max: 100 }, h5: { pct: 30, reset: 0 }, week: { pct: 55, reset: 0 }, at: 0 };

const web = await startWebServer(), fake = await startFakeHost(), B = await launch();
const ev = (p, e) => evaluate(p, e);
const label = (p) => ev(p, `document.getElementById('meta').textContent`);
const sw = (p) => ev(p, `JSON.stringify({sw: document.getElementById('meta').dataset.sw || null, m: document.getElementById('meta').dataset.swModel || null, e: document.getElementById('meta').dataset.swEffort || null})`);
async function paired(scheme){
  const p = await newPage(B, 390, 844, scheme, { allow: [web.url, fake.relay + '/'], touch: true });
  await navigate(p, fake.newPairing(web.url)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await ev(p, "document.getElementById('phone-env-dismiss')?.click()");
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Wren' }); await fake.send(MODELS); await fake.send(METER);
  await waitFor(p, `document.getElementById('meta').textContent === 'Opus 5.5 · medium'`);
  return p;
}
async function headShot(p, file){
  const r = await ev(p, `(() => { const b = document.getElementById('meta').getBoundingClientRect(); return {x: b.x, y: b.y, w: b.width, h: b.height}; })()`);
  const clip = { x: Math.max(0, r.x - 90), y: Math.max(0, r.y - 30), width: Math.min(390, r.w + 180), height: r.h + 60, scale: 3 };
  const { data } = await p.send('Page.captureScreenshot', { format: 'png', clip });
  writeFileSync(join(OUT, file), Buffer.from(data, 'base64'));
}
const tapHalf = async (p, half) => { const r = await rect(p, half === 'model' ? '#metaModel' : '#metaEffort'); await tap(p, r.x, r.y); };

async function screencast(p, file, fn){
  const dir = join(OUT, '.frames-' + Date.now()); mkdirSync(dir, { recursive: true });
  const frames = [];
  p.on((m) => { if (m.method !== 'Page.screencastFrame') return; frames.push({ data: m.params.data, ts: m.params.metadata.timestamp });
    p.send('Page.screencastFrameAck', { sessionId: m.params.sessionId }).catch(() => {}); });
  await p.send('Page.startScreencast', { format: 'jpeg', quality: 75, everyNthFrame: 1 });
  try { await fn(); await sleep(600); } finally { await p.send('Page.stopScreencast').catch(() => {}); }
  if (frames.length < 2) { rmSync(dir, { recursive: true, force: true }); return 0; }
  let list = '';
  frames.forEach((f, i) => { const n = join(dir, `f${String(i).padStart(4, '0')}.jpg`); writeFileSync(n, Buffer.from(f.data, 'base64'));
    const dur = i + 1 < frames.length ? Math.max(0.02, frames[i + 1].ts - f.ts) : 0.5; list += `file '${n}'\nduration ${dur.toFixed(3)}\n`; });
  list += `file '${join(dir, `f${String(frames.length - 1).padStart(4, '0')}.jpg`)}'\n`;
  writeFileSync(join(dir, 'list.txt'), list);
  const r = spawnSync('ffmpeg', ['-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', join(dir, 'list.txt'), '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2,fps=30', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', join(OUT, file)]);
  rmSync(dir, { recursive: true, force: true });
  if (r.status !== 0) throw new Error('ffmpeg: ' + r.stderr);
  return frames.length;
}

try {
  const p = await paired('light');
  // 1. six fast taps on the model → the label follows every tap, nothing sent inside 5 s, exactly one model_set after
  let n0 = fake.st.modelSets.length;
  const frames = await screencast(p, 'p239-six-taps.mp4', async () => {
    let tLast = 0;
    for (let i = 0; i < 6; i++){ await tapHalf(p, 'model'); tLast = Date.now(); await sleep(350); if (i === 2) await headShot(p, 'p239-1-tapping-light.png'); }
    const until = (ms) => sleep(Math.max(0, tLast + ms - Date.now()));
    ok((await label(p)) === 'Haiku 5.5', 'six taps: the label shows the 6th step at once (Haiku 5.5): ' + await label(p));
    ok(JSON.parse(await sw(p)).m === 'wait' && JSON.parse(await sw(p)).sw === 'wait', 'six taps: the model half wears the waiting look ' + await sw(p));
    await until(1800); await headShot(p, 'p239-2-waiting-light.png');
    await until(4500);
    ok(fake.st.modelSets.length === n0, `six taps: nothing sent ${Date.now() - tLast} ms after the last tap `);
    await until(5800);
    ok(fake.st.modelSets.length === n0 + 1 && fake.st.modelSets[n0].model === 'haiku', 'six taps → exactly one model_set (haiku), ~5 s after the last tap: ' + JSON.stringify(fake.st.modelSets.slice(n0)));
    await waitFor(p, `document.getElementById('meta').textContent === 'Haiku 5.5' && !document.getElementById('meta').dataset.sw`, 5000);   // the meter's effort is hidden: Haiku has none
    await headShot(p, 'p239-3-applied-light.png');
  });
  ok(frames > 10, `screencast p239-six-taps.mp4: ${frames} frames`);

  // 2. tapped back to what runs within 5 s → nothing sent
  n0 = fake.st.modelSets.length;
  const before = await label(p);
  for (let i = 0; i < 4; i++){ await tapHalf(p, 'model'); await sleep(200); }       // haiku → fable opus sonnet haiku
  await sleep(5600);
  ok(fake.st.modelSets.length === n0 && (await label(p)) === before && JSON.parse(await sw(p)).sw === null, 'tapped back to the running model within 5 s → nothing sent, no pending look');

  // 3. effort half: its own clock; a refusal rolls the label back with one toast
  await tapHalf(p, 'model'); await sleep(5600);                                          // → fable · medium (sent, applied)
  await waitFor(p, `document.getElementById('meta').textContent === 'Fable 5.1 · medium' && !document.getElementById('meta').dataset.sw`, 5000);
  n0 = fake.st.modelSets.length;
  fake.st.modelWhy = 'busy';
  await tapHalf(p, 'effort');
  ok((await label(p)) === 'Fable 5.1 · high' && JSON.parse(await sw(p)).e === 'wait' && JSON.parse(await sw(p)).m === null, 'effort tap: label at once, only the effort half waits');
  await headShot(p, 'p239-4-effort-waiting-light.png');
  await sleep(5400);
  await waitFor(p, `document.getElementById('toast').classList.contains('on')`, 3000);
  ok(fake.st.modelSets.length === n0 + 1 && (await label(p)) === 'Fable 5.1 · medium', 'refusal (busy) → label back to Fable 5.1 · medium: ' + await label(p));
  const toastText = await ev(p, `document.getElementById('toast').textContent`);
  ok(/没切成|Not switched/.test(toastText), 'refusal → one toast: ' + toastText);
  writeFileSync(join(OUT, 'p239-5-refused-light.png'), await shoot(p));

  // 4. leaving the page sends a waiting pick at once
  await sleep(2600);
  n0 = fake.st.modelSets.length;
  await tapHalf(p, 'effort'); await sleep(300);
  await ev(p, `Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true}); document.dispatchEvent(new Event('visibilitychange'))`);
  await sleep(600);
  ok(fake.st.modelSets.length === n0 + 1 && fake.st.modelSets[n0].effort === 'high', 'page hidden → the waiting pick is sent at once: ' + JSON.stringify(fake.st.modelSets.slice(n0)));
  await ev(p, `delete document.visibilityState; document.dispatchEvent(new Event('visibilitychange'))`);
  await sleep(5600);
  ok(fake.st.modelSets.length === n0 + 1, 'the flushed clock never fires again');

  // 5. hold = the default at once
  n0 = fake.st.modelSets.length;
  await tapHalf(p, 'model');
  const r = await rect(p, '#meta');
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: r.x, y: r.y }] }); await sleep(800);
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await sleep(400);
  ok(fake.st.modelSets.length === n0 + 1 && fake.st.modelSets[n0].default === true, 'hold → default sent at once, the waiting tap dropped');
  await sleep(5600);
  ok(fake.st.modelSets.length === n0 + 1, 'the dropped tap never fires');

  // dark theme: the waiting look on a dark page
  const d = await paired('dark');
  await tapHalf(d, 'effort'); await sleep(1200);
  await headShot(d, 'p239-6-effort-waiting-dark.png');
  writeFileSync(join(OUT, 'p239-6-full-dark.png'), await shoot(d));
  const look = await ev(d, `(() => { const s = getComputedStyle(document.getElementById('metaEffort')); return JSON.stringify({o: s.opacity, bg: s.backgroundColor, img: s.backgroundImage.slice(0, 40), anim: s.animationName}); })()`);
  ok(/"o":"0\.6/.test(look) && /rgba\(0, 0, 0, 0\)|transparent/.test(look) && /pillwait/.test(look), 'the waiting look = faded + a thin running-out line, no fill: ' + look);
} finally {
  writeFileSync(join(OUT, 'p239-results.json'), JSON.stringify({ at: new Date().toISOString(), results }, null, 1));
  await B.close(); await fake.stop(); await web.stop();
}
console.log(`p239: ${results.length} checks passed → ${OUT}`);
process.exit(0);
