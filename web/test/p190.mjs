#!/usr/bin/env node
// P190 · the scan entry points on a paired page (PROTOCOL §27 is end to end in tests/e2e_p190_reverse_pair.mjs).
// Real page + local fake host, own headless Chromium (never :9222) whose camera is a simulated 1080p photo of a QR code that
// is NOT a pairing code (BarcodeDetector removed = iOS: jsQR reads it). Cases:
//   scan-icon     the header's top-right: scan icon (≥ 44 px, the reply-card icon set) + gear; the 网页版 tag only in Settings
//   scan-to-field tap the scan icon → scanning starts at once → the text lands in the message field, nothing is sent
//   camera-hold   camera button held ≥ 0.5 s = scan (released = the user gesture); a short tap is still the photo; 4 tools
//   rm-scan       Settings → 遥控器管理 → 「扫码添加遥控器」: a code that is not a new device's → said so, back on the page
//   screens       zh / en × light / dark × 390 / 1440 → reports/qa/p190/screens/
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';
import * as L from './p73_scan_lib.mjs';

const OUT = new URL('../../../reports/qa/p190/screens/', import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });
const PY = new URL('../../host/.venv/bin/python', import.meta.url).pathname;
const SHARED_PY = '/home/' + process.env.USER + '/coding/vibe-remote/host/.venv/bin/python';
const TEXT = 'https://example.com/menu?table=12 · 扫到的文字';
const rec = parityRecorder('web/test/p190.mjs');
const tmp = mkdtempSync(join(tmpdir(), 'aj-p190-'));
let failed = 0;
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}

// the camera: a 1080p photo of a QR code holding TEXT (segno, the host's own QR library)
let rows;
for (const exe of [PY, SHARED_PY]) {
  try { rows = JSON.parse(execFileSync(exe, ['-c', 'import json,sys,segno; q=segno.make(sys.stdin.read(), error="m", micro=False); '
    + 'print(json.dumps(["".join("1" if m else "0" for m in r) for r in q.matrix_iter(scale=1,border=0)]))'], { input: TEXT, encoding: 'utf8' })); break; } catch { /* next */ }
}
assert.ok(rows, 'segno');
const scene = L.screenScene(rows, { w: 1920, h: 1080, ppm: 12, blur: 1 });
for (let i = 0; i < 3; i++) writeFileSync(join(tmp, `f${i}.png`), L.writePngGray(L.shoot(scene, 21 + i).gray, 1920, 1080));
const y4m = join(tmp, 'scene.y4m');
execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-framerate', '3', '-i', join(tmp, 'f%d.png'), '-pix_fmt', 'yuv420p', y4m]);

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch({ args: [`--use-file-for-fake-video-capture=${y4m}`] });
const NO_DETECTOR = `Object.defineProperty(window,'BarcodeDetector',{value:undefined});`;
const ev = evaluate;
const NOW = Math.floor(Date.now() / 1000);

async function paired({ w = 390, h = 844, scheme = 'light', lang = 'zh' } = {}) {
  fake.reset();
  fake.st.prefs = { appearance: { language: lang, theme: 'system' }, voice: { speak_replies: false, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'shared' } };
  fake.remotes({ limit: 10, items: [{ id: 'ME', name: 'iPhone Air', paired_at: NOW - 86400, seen: NOW, online: true, source: 'local', me: true }], link: '', qr: '', code: '000000' });
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await p.send('Page.addScriptToEvaluateOnNewDocument', { source: NO_DETECTOR });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await sleep(200);
  return p;
}
const sent = () => fake.log.filter((m) => m.t === 'say').length;

try {
  const p = await paired();

  await C('scan-icon', async () => {
    const r = await ev(p, `(() => { const s = document.getElementById('scanBtn').getBoundingClientRect(), g = document.getElementById('setBtn').getBoundingClientRect();
      return { w: s.width, h: s.height, left: s.right <= g.left, sameRow: Math.abs(s.top - g.top) < 2, badgeInTop: !!document.querySelector('.top #badge'),
        chip: document.getElementById('badge').closest('#menu-about') !== null, stroke: document.querySelector('#scanBtn svg').getAttribute('stroke-width') }; })()`);
    assert.ok(r.w >= 44 && r.h >= 44, JSON.stringify(r));
    assert.ok(r.left && r.sameRow, 'scan icon sits just left of the gear');
    assert.equal(r.badgeInTop, false, 'no 网页版 tag in the header');
    assert.equal(r.chip, true, '网页版 tag inside Settings → 这个网页版有多安全');
    assert.equal(r.stroke, '1.9', 'same icon set as the reply card tools');
  });

  await C('scan-to-field', async () => {
    const before = sent();
    await ev(p, `document.getElementById('input').value = ''; document.getElementById('scanBtn').click()`);
    await waitFor(p, `document.body.dataset.view === 'scan' && document.body.dataset.scan === 'any'`, 6000);
    await waitFor(p, `document.getElementById('input').value.includes('example.com/menu')`, 20000);
    assert.equal(await ev(p, `document.body.dataset.view`), 'chat', 'back on the chat');
    assert.equal(await ev(p, `document.getElementById('input').value`), TEXT, 'the text verbatim');
    assert.match(await ev(p, `document.getElementById('toast').textContent`), /输入框/);
    await sleep(600);
    assert.equal(sent(), before, 'nothing was sent');
    writeFileSync(OUT + 'scan-to-field-390.png', await shoot(p, 'unused'));
  });

  await C('camera-hold', async () => {
    assert.equal(await ev(p, `document.querySelectorAll('#tools > .tool').length`), 4, 'still four tools (no fifth button)');
    // a short tap: no scanner
    await ev(p, `(() => { const b = document.getElementById('tCamera'); b.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, button:0})); b.dispatchEvent(new PointerEvent('pointerup', {bubbles:true})); return true; })()`);
    await sleep(700);
    assert.notEqual(await ev(p, `document.body.dataset.view`), 'scan', 'a tap is not a scan');
    await ev(p, `document.getElementById('input').value = ''`);
    await ev(p, `document.getElementById('tCamera').dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, button:0}))`);
    await sleep(650);
    assert.equal(await ev(p, `document.getElementById('tCamera').dataset.held`), '1', 'held state shows');
    await ev(p, `(() => { const b = document.getElementById('tCamera'); b.dispatchEvent(new PointerEvent('pointerup', {bubbles:true})); b.click(); return true; })()`);
    await waitFor(p, `document.body.dataset.scan === 'any' || document.getElementById('input').value.includes('example.com')`, 6000);
    await waitFor(p, `document.getElementById('input').value.includes('example.com/menu')`, 20000);
    // dragging off the button cancels a hold
    await ev(p, `document.getElementById('input').value = ''`);
    await ev(p, `document.getElementById('tCamera').dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, button:0}))`);
    await sleep(650);
    await ev(p, `(() => { const b = document.getElementById('tCamera'); b.dispatchEvent(new PointerEvent('pointerleave', {bubbles:true})); b.dispatchEvent(new PointerEvent('pointerup', {bubbles:true})); return true; })()`);
    await sleep(500);
    assert.notEqual(await ev(p, `document.body.dataset.view`), 'scan', 'moved off = cancelled');
  });

  await C('rm-scan', async () => {
    await ev(p, `document.getElementById('setBtn').click()`);
    await waitFor(p, `!document.getElementById('settings').hidden`);
    await ev(p, `document.getElementById('set-remotes-open').click()`);
    await waitFor(p, `!document.getElementById('set-remotes').hidden && !document.getElementById('rm-scan-icon').hidden`);
    writeFileSync(OUT + 'remotes-scan-button-390.png', await shoot(p, 'unused'));
    await ev(p, `document.getElementById('input').value = ''; document.getElementById('rm-scan-icon').click()`);
    await waitFor(p, `document.body.dataset.scan === 'rv'`, 6000);
    await waitFor(p, `document.getElementById('toast').classList.contains('on') && /不是新设备的配对码/.test(document.getElementById('toast').textContent)`, 20000);
    await waitFor(p, `!document.getElementById('settings').hidden && !document.getElementById('set-remotes').hidden`, 4000);
    assert.equal(await ev(p, `document.getElementById('input').value`), '', 'a 「扫码添加」 scan never fills the field');
    await ev(p, `document.getElementById('settings') && document.getElementById('set-close')?.click()`);
  });

  await p.dispose();

  await C('screens', async () => {
    for (const [lang, scheme, w, h] of [['zh', 'light', 390, 844], ['zh', 'dark', 390, 844], ['en', 'light', 1440, 900], ['en', 'dark', 390, 844]]) {
      const q = await paired({ w, h, scheme, lang });
      const tag = `${lang}-${scheme}-${w}`;
      writeFileSync(OUT + `chat-header-${tag}.png`, await shoot(q, 'unused'));
      await ev(q, `document.getElementById('setBtn').click()`);
      await waitFor(q, `!document.getElementById('settings').hidden`);
      await ev(q, `document.getElementById('menu-about').scrollIntoView({block:'center'})`);
      await sleep(150);
      writeFileSync(OUT + `settings-webtag-${tag}.png`, await shoot(q, 'unused'));
      const over = await ev(q, `document.documentElement.scrollWidth > innerWidth + 1`);
      assert.equal(over, false, `${tag}: no horizontal overflow`);
      assert.equal(q.problems.length, 0, q.problems.join('\n'));
      await q.dispose();
    }
  });
} catch (e) {
  failed++; console.log('  ✗ ' + (e && e.stack || e));
} finally {
  await B.close?.();
  await fake.stop();
  await web.stop();
  rmSync(tmp, { recursive: true, force: true });
}
console.log(failed ? `${failed} FAILED` : 'ALL PASS', '· parity', rec.write());
process.exit(failed ? 1 : 0);
