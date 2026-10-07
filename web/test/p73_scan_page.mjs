// F29 / ADR-A177 in the real page (own headless Chromium, never :9222): BarcodeDetector removed (= iOS Safari, so jsQR and
// public/js/scan.js do the work) and the camera fed a simulated 1920×1080 photo of a terminal showing a compact pairing QR
// (Chromium's --use-file-for-fake-video-capture), made from a live fake host's link with the host's own QR code
// (wire.pairing_qr). Checks: the page asks for 1080p, gets 1920×1080, scans, pairs, is approved. Then, on Chromium's
// default fake camera (no QR in it), the 10 s hint appears — Safari-tab wording, and the home-screen wording when standalone.
//   node web/test/p73_scan_page.mjs        → reports/qa/p73/f29/page-*.png, page.json
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startFakeHost } from './fakehost.mjs';
import { startWebServer } from './serve.mjs';
import { launch, newPage, navigate, evaluate, waitFor, waitState, shoot, sleep } from './browser.mjs';
import * as L from './p73_scan_lib.mjs';

const out = new URL('../../../reports/qa/p73/f29/', import.meta.url);
mkdirSync(out, { recursive: true });
const PY = new URL('../../host/.venv/bin/python', import.meta.url).pathname;
const HOST = new URL('../../host/', import.meta.url).pathname;
const tmp = mkdtempSync(join(tmpdir(), 'aj-p73-'));
const NO_DETECTOR = `Object.defineProperty(window,'BarcodeDetector',{value:undefined});`;
const result = {};
const server = await startWebServer();
const fake = await startFakeHost();
let browser = null;
try {
  // 1. scan a simulated 1080p screen photo → pair
  const link = fake.newPairing(server.url, 300, { compact: true });
  assert.match(link, /#p=[0-9]+$/);
  const rows = JSON.parse(execFileSync(PY, ['-c', 'import json,sys; from agentj import wire; q=wire.pairing_qr(sys.stdin.read()); '
    + 'print(json.dumps(["".join("1" if m else "0" for m in r) for r in q.matrix_iter(scale=1,border=0)]))'], { input: link, cwd: HOST, encoding: 'utf8' }));
  result.link = { chars: link.length, modules: rows.length };
  const scene = L.screenScene(rows, { w: 1920, h: 1080, ppm: 7.8, blur: 2 });
  for (let i = 0; i < 3; i++) writeFileSync(join(tmp, `f${i}.png`), L.writePngGray(L.shoot(scene, 11 + i).gray, 1920, 1080));
  const y4m = join(tmp, 'scene.y4m');
  execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-framerate', '3', '-i', join(tmp, 'f%d.png'), '-pix_fmt', 'yuv420p', y4m]);
  browser = await launch({ args: [`--use-file-for-fake-video-capture=${y4m}`] });
  let page = await newPage(browser, 390, 844, 'light', { allow: [server.url, fake.relay] });
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: NO_DETECTOR + `Object.defineProperty(navigator,'standalone',{value:true});` });
  await navigate(page, server.url);
  await waitFor(page, `!document.getElementById('pair-view').hidden`);
  const t0 = Date.now();
  await evaluate(page, `document.getElementById('scan').click()`);
  await waitFor(page, `document.getElementById('scan-video').videoWidth > 0`);
  result.stream = await evaluate(page, `(()=>{const v=document.getElementById('scan-video'),s=v.srcObject.getVideoTracks()[0].getSettings();return {videoWidth:v.videoWidth,videoHeight:v.videoHeight,settings:{width:s.width,height:s.height}};})()`);
  assert.equal(result.stream.videoWidth, 1920); assert.equal(result.stream.videoHeight, 1080);
  await evaluate(page, `document.getElementById('scan-trigger').click()`);
  await waitState(page, 'awaiting-approval', 15000);
  result.scanToPairMs = Date.now() - t0;
  await fake.approve();
  await waitState(page, 'ready');
  writeFileSync(new URL('page-paired.png', out), await shoot(page, 'unused'));
  result.paired = true;
  await page.dispose();
  await browser.close(); browser = null;

  // 2. nothing readable in view for 10 s → the hint (Chromium's own fake camera pattern has no QR)
  browser = await launch();
  for (const standalone of [false, true]) {
    page = await newPage(browser, 390, 844, 'light', { allow: [server.url, fake.relay] });
    await page.send('Page.addScriptToEvaluateOnNewDocument', { source: NO_DETECTOR + (standalone ? `Object.defineProperty(navigator,'standalone',{value:true});` : '') });
    await navigate(page, server.url);
    await waitFor(page, `!document.getElementById('pair-view').hidden`);
    await evaluate(page, `document.getElementById('scan').click()`);
    await waitFor(page, `document.getElementById('scan-video').videoWidth > 0`);
    await evaluate(page, `document.getElementById('scan-trigger').click()`);
    const aim = await evaluate(page, `document.getElementById('camera-hint').textContent`);
    await sleep(8000);
    assert.equal(await evaluate(page, `document.getElementById('camera-hint').textContent`), aim, 'no hint before 10 s');
    await waitFor(page, `document.getElementById('camera-hint').textContent.includes('agentj pair --link')`, 6000);
    const hint = await evaluate(page, `document.getElementById('camera-hint').textContent`);
    assert.match(hint, standalone ? /返回/ : /相机/);
    result[standalone ? 'hintApp' : 'hintTab'] = hint;
    writeFileSync(new URL(`page-hint-${standalone ? 'app' : 'tab'}.png`, out), await shoot(page, 'unused'));
    await page.dispose();
  }
  writeFileSync(new URL('page.json', out), JSON.stringify(result, null, 1) + '\n');
  console.log('p73 scan page OK', JSON.stringify(result));
} finally {
  if (browser) await browser.close();
  await fake.stop?.(); await server.stop();
  rmSync(tmp, { recursive: true, force: true });
}
