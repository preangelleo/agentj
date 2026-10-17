#!/usr/bin/env node
// P364 (#74) · browser checks for the Android splash icon, own headless Chromium (never :9222):
//   icons-alpha     the manifest's icons decoded by Chromium itself (canvas getImageData): "any" = transparent corners and
//                   only alpha 0 / 255; maskable = opaque, corner = background_color
//   boot-android    boot.js under (display-mode: standalone) (stubbed matchMedia) + light scheme → every theme-color = background_color
//   boot-browser    in a normal tab boot.js leaves the light / dark theme-color pair alone
//   page-clean      the real page loads with the new manifest, no console error / exception
//   screens         simulated splash (background_color + the maskable cropped to its 80 % safe zone, as Chrome draws a
//                   maskable splash image) before / after, and the home-screen icon (circle mask) before / after
//                   → reports/qa/p364/
// Run: node web/test/p364.mjs
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startWebServer, PUBLIC_DIR } from './serve.mjs';
import { launch, newPage, evaluate, navigate, shoot, sleep } from './browser.mjs';

const OUT = new URL('../../../reports/qa/p364/', import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });
const M = JSON.parse(readFileSync(join(PUBLIC_DIR, 'manifest.webmanifest'), 'utf8'));
const BG = M.background_color;
const MASK = M.icons.find((i) => i.purpose === 'maskable');
const b64 = (f) => 'data:image/png;base64,' + readFileSync(f).toString('base64');
let failed = 0;
async function C(name, fn) {
  try { await fn(); console.log(`  ✓ ${name}`); } catch (e) { failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
}

const web = await startWebServer({ port: 0 });
const B = await launch();
try {
  await C('icons-alpha', async () => {
    const p = await newPage(B, 412, 915, 'light', { allow: [web.url] });
    await navigate(p, web.url + 'manifest.webmanifest');
    const r = await evaluate(p, `Promise.all(${JSON.stringify(M.icons)}.map(async (i) => {
      const img = new Image(); img.src = new URL(i.src, location.href).href; await img.decode();
      const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
      const g = c.getContext('2d'); g.drawImage(img, 0, 0); const d = g.getImageData(0, 0, c.width, c.height).data;
      const alphas = new Set(); for (let k = 3; k < d.length; k += 4) alphas.add(d[k]);
      return { purpose: i.purpose, size: c.width + 'x' + c.height, corner: [...d.slice(0, 4)], alphas: [...alphas].sort((a, b) => a - b) };
    }))`);
    for (const i of r) {
      if (i.purpose === 'any') { assert.equal(i.corner[3], 0, i.size + ' corner'); assert.deepEqual(i.alphas, [0, 255], i.size + ' hard edges'); }
      else { assert.deepEqual(i.alphas, [255], 'maskable opaque'); assert.equal('#' + i.corner.slice(0, 3).map((x) => x.toString(16).padStart(2, '0')).join(''), BG); }
    }
    writeFileSync(join(OUT, 'icons-alpha.json'), JSON.stringify(r, null, 1) + '\n');
  });

  const BOOT = readFileSync(join(PUBLIC_DIR, 'js/boot.js'), 'utf8');
  const bootIn = async (standalone) => {
    const p = await newPage(B, 412, 915, 'light');
    await p.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
    // headless Chromium cannot emulate display-mode: an installed app's answer to that one query is stubbed
    return evaluate(p, `(() => {
      if (${standalone}) { const mm = window.matchMedia.bind(window); window.matchMedia = (q) => /display-mode: standalone/.test(q) ? { matches: true, media: q } : mm(q); }
      document.head.innerHTML = '<meta name="theme-color" content="#faf9f5" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#141414" media="(prefers-color-scheme: dark)">';
      const standalone = matchMedia('(display-mode: standalone)').matches;
      (0, eval)(${JSON.stringify(BOOT)});
      return { standalone, metas: [...document.querySelectorAll('meta[name="theme-color"]')].map((m) => m.content) };
    })()`);
  };
  await C('boot-android', async () => {
    const r = await bootIn(true);
    assert.equal(r.standalone, true, 'display-mode emulated');
    assert.deepEqual(r.metas, [BG, BG]);
  });
  await C('boot-browser', async () => assert.deepEqual((await bootIn(false)).metas, ['#faf9f5', '#141414']));

  await C('page-clean', async () => {
    const p = await newPage(B, 412, 915, 'light', { allow: [web.url] });
    await navigate(p, web.url); await sleep(800);
    assert.equal(await evaluate(p, `document.querySelector('link[rel=manifest]').getAttribute('href')`), 'manifest.webmanifest');
    assert.deepEqual(p.problems.filter((x) => !/WebSocket|ERR_CONNECTION_REFUSED|relay/i.test(x)), []);
  });

  await C('screens', async () => {
    const before = b64(join(OUT, 'before-maskable.png')), after = b64(join(PUBLIC_DIR, MASK.src.split('?')[0]));
    const p = await newPage(B, 800, 640, 'dark');
    const splash = (src, label, icons) => `<div class=ph><div class=sb style="color:${icons}">10:36<span>◉ ◐ ▮100</span></div>
      <div class=crop><img src="${src}"></div><p>${label}</p></div>`;
    await evaluate(p, `document.body.innerHTML = ${JSON.stringify(`<style>
      body{margin:0;background:#2c2b26;font:14px system-ui;color:#faf9f5;display:flex;gap:24px;padding:24px}
      .ph{width:360px;height:576px;background:${BG};position:relative}
      .sb{display:flex;justify-content:space-between;padding:6px 12px;font-size:13px}
      .crop{position:absolute;left:115px;top:213px;width:130px;height:130px;overflow:hidden}
      .crop img{width:162.5px;height:162.5px;margin:-16.25px;image-rendering:pixelated}
      p{position:absolute;bottom:4px;width:100%;text-align:center;margin:0;color:#888}</style>`
      + splash(before, '修前（Lotion 方块 + 深色状态栏图标）', '#2c2b26') + splash(after, '修后（同色底 + 浅色状态栏图标）', '#faf9f5'))}`);
    await sleep(300);
    writeFileSync(join(OUT, 'splash-before-after.png'), await shoot(p));
    const q = await newPage(B, 560, 260, 'dark');
    const home = (bg) => `<div class=w style="background:${bg}"><img src="${before}"><img src="${after}"></div>`;
    await evaluate(q, `document.body.innerHTML = ${JSON.stringify(`<style>body{margin:0;display:flex;flex-direction:column}
      .w{display:flex;gap:48px;padding:24px 48px;align-items:center}
      img{width:96px;height:96px;border-radius:50%;image-rendering:pixelated}</style>` + home('#629887') + home('#faf9f5'))}`);
    await sleep(300);
    writeFileSync(join(OUT, 'home-icon-before-after.png'), await shoot(q));
  });
} finally {
  await B.close(); await web.stop();
}
console.log(failed ? `P364: ${failed} failed` : 'P364: all passed');
process.exit(failed ? 1 : 0);
