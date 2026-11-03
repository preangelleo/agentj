#!/usr/bin/env node
// #424 (bugbatch 0268): the picture preview fits the window (small pictures scaled up, aspect kept), the wheel and a
// trackpad pinch (ctrl+wheel) zoom at the cursor, the zoom row (缩小 · 放大 · 适应窗口 · 原始尺寸 · 全屏) drives the same
// scale, a zoomed picture still pans with a drag, double-click still toggles 2.5×, and Esc leaves full screen first,
// then the preview. Real client + fakehost + headless Chromium; synthetic gradient pictures only.
//   node web/test/p424_zoom.mjs            the checks + the after shots
//   P424_BEFORE=<public dir of the base commit> node …  only the before shots (no checks), served from that dir
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { deflateSync, crc32 } from 'node:zlib';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, sleep } from './browser.mjs';

const OUT = process.env.AJ_P424_SHOTS || join(tmpdir(), 'aj-p424-shots');
mkdirSync(OUT, { recursive: true });
const BEFORE = process.env.P424_BEFORE || '';
const sha = (b) => createHash('sha256').update(b).digest('hex');
const CHUNK = 45056, WINDOW = 8;

function png(w, h, f) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const [r, g, b] = f(x, y); raw.set([r, g, b], y * (w * 3 + 1) + 1 + x * 3); }
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from('\x89PNG\r\n\x1a\n', 'latin1'), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
// a small "screenshot": 320×200, a gradient with a grid so zoom / pan is visible in the shots
const NW = 320, NH = 200;
const shot = png(NW, NH, (x, y) => (x % 40 === 0 || y % 40 === 0) ? [250, 249, 245] : [40 + (x * 180 / NW) | 0, 90 + (y * 120 / NH) | 0, 200]);
const id = (c) => c.repeat(22);
const BYTES = new Map();
const item = (mid, name, mime, kind, bytes) => { BYTES.set(mid, bytes); return { mid, name, mime, kind, bytes: bytes.length, sha256: sha(bytes), ref: 'out/' + name }; };
const FILES = [item(id('A'), 'screenshot.png', 'image/png', 'image', shot)];
const REPLY = ['截图在这里：', '', '![截图](out/screenshot.png)'].join('\n');

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(BEFORE ? { publicDir: BEFORE } : {}) });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);

function serve(st) {
  st.onApp = async (c, m, send) => {
    if (m.t !== 'media_get') return false;
    const b = BYTES.get(m.mid);
    if (!b) { await send({ t: 'media_err', mid: m.mid, why: 'gone' }); return true; }
    for (let i = 0; i < WINDOW; i++) {
      const o = m.o + i * CHUNK;
      if (o >= b.length) break;
      const d = b.subarray(o, Math.min(b.length, o + CHUNK));
      await send({ t: 'media_chunk', mid: m.mid, o, d: Buffer.from(d).toString('base64url'), last: o + d.length >= b.length });
    }
    return true;
  };
}
async function opened(w, h, scheme = 'light') {
  fake.reset();
  serve(fake.st);
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  const t1 = await fake.addTurn({ k: 'phone', dev: null, text: '发一张截图' }, REPLY, 'done');
  await fake.updateTurn(t1.id, { media: FILES, media_skip: [] });
  const card = `document.querySelector('#words .mcard[data-mid="${id('A')}"]')`;
  await waitFor(p, `!!${card}`, 15000);
  await ev(p, `${card}.querySelector('.mtap')?.click(), true`);
  await waitFor(p, `${card}.dataset.state === 'ready'`, 15000);
  await ev(p, `${card}.scrollIntoView({ block: 'center' })`);
  await sleep(450);                                       // past the viewer's own ghost-click guard of the page
  await ev(p, `${card}.querySelector('img').click()`);
  await waitFor(p, `!document.querySelector('.mview').hidden && document.querySelector('.mview').dataset.mode === 'image'`);
  await waitFor(p, `document.querySelector('.mview img.mzoom').complete && document.querySelector('.mview img.mzoom').naturalWidth === ${NW}`);
  await sleep(250);
  return p;
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
}
const geo = (p) => ev(p, `(() => { const i = document.querySelector('.mview img.mzoom'), s = document.querySelector('.mstage').getBoundingClientRect(), r = i.getBoundingClientRect();
  const m = /scale\\(([\\d.e-]+)\\)/.exec(i.style.transform); return { iw: r.width, ih: r.height, il: r.left, it: r.top, sw: s.width, sh: s.height, sl: s.left, st: s.top, s: m ? Number(m[1]) : 1, tf: i.style.transform }; })()`);
const fitOf = (g) => Math.min(g.sw / NW, g.sh / NH);
const mouse = (p, type, x, y, extra = {}) => p.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1, pointerType: 'mouse', ...extra });
const click = async (p, sel) => {
  const r = await ev(p, `(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
  await mouse(p, 'mousePressed', r.x, r.y); await mouse(p, 'mouseReleased', r.x, r.y);
};
const wheel = (p, x, y, deltaY, ctrl = false) => p.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX: 0, deltaY, modifiers: ctrl ? 2 : 0 });

if (BEFORE) {
  for (const scheme of ['light', 'dark']) {
    for (const [w, h] of [[1440, 900], [390, 844]]) {
      const p = await opened(w, h, scheme);
      try {
        const g = await geo(p);
        console.log(`  before ${w} ${scheme}: picture ${g.iw.toFixed(0)}×${g.ih.toFixed(0)} in a ${g.sw.toFixed(0)}×${g.sh.toFixed(0)} stage`);
        writeFileSync(join(OUT, `before-${w}-${scheme}.png`), await shoot(p));
      } finally { await p.dispose(); }
    }
  }
} else {
  for (const scheme of ['light', 'dark']) {
    await C(`p424-desktop-${scheme}: 1440×900 — the small picture fills the stage (aspect kept), the zoom row is there`, async () => {
      const p = await opened(1440, 900, scheme);
      try {
        const g = await geo(p), f = fitOf(g);
        assert.ok(f > 2, 'a 320×200 picture in a 1440-wide stage is scaled up: ' + f);
        assert.ok(Math.abs(g.iw - NW * f) < 1.5 && Math.abs(g.ih - NH * f) < 1.5, `fitted: ${g.iw}×${g.ih} vs ${NW * f}×${NH * f}`);
        assert.ok(Math.abs(g.iw / g.ih - NW / NH) < 0.01, 'aspect kept');
        assert.ok(g.il >= g.sl - 0.5 && g.it >= g.st - 0.5 && g.il + g.iw <= g.sl + g.sw + 0.5 && g.it + g.ih <= g.st + g.sh + 0.5, 'inside the stage');
        const z = await ev(p, `[...document.querySelectorAll('.mvzoom button')].map(b => ({ k: b.dataset.z, hidden: b.hidden, dis: b.disabled, label: b.getAttribute('aria-label'), w: b.getBoundingClientRect().width, h: b.getBoundingClientRect().height }))`);
        assert.deepEqual(z.map((b) => b.k), ['zoomOut', 'zoomIn', 'fit', 'actual', 'fullscreen']);
        assert.deepEqual(z.map((b) => b.label), ['缩小', '放大', '适应窗口', '原始尺寸', '全屏']);
        assert.ok(z.every((b) => b.hidden || (b.w >= 44 && b.h >= 44)), 'touch targets ≥ 44 px');
        assert.deepEqual(z.filter((b) => b.dis).map((b) => b.k), ['fit'], 'fitted: only 适应窗口 has nothing to do (原始尺寸 is smaller, so 缩小 works)');
        writeFileSync(join(OUT, `after-1440-${scheme}-fit.png`), await shoot(p));
        if (scheme !== 'light') return;

        // the wheel zooms at the cursor: the picture point under the cursor stays under it
        const cx = g.il + g.iw * 0.75, cy = g.it + g.ih * 0.3;
        for (let i = 0; i < 4; i++) { await wheel(p, cx, cy, -120); await sleep(30); }
        let a = await geo(p);
        assert.ok(a.s > 2, 'wheel up zooms in: ' + a.s);
        assert.ok(Math.abs((cx - a.il) / a.iw - 0.75) < 0.01 && Math.abs((cy - a.it) / a.ih - 0.3) < 0.01, 'the point under the cursor stayed: ' + a.tf);
        writeFileSync(join(OUT, 'after-1440-light-wheel-zoom.png'), await shoot(p));
        // zoomed: a mouse drag pans, never steps / closes
        const before = a;
        await mouse(p, 'mousePressed', 700, 450);
        for (let i = 1; i <= 6; i++) { await mouse(p, 'mouseMoved', 700 - i * 20, 450 + i * 10); await sleep(16); }
        await mouse(p, 'mouseReleased', 580, 510);
        a = await geo(p);
        assert.ok(Math.abs(a.il - before.il + 120) < 2 && Math.abs(a.it - before.it - 60) < 2, 'drag panned the picture: ' + a.tf);
        assert.equal(await ev(p, `document.querySelector('.mview').hidden`), false, 'still open after the drag');
        // wheel down far enough: back to the fit, centred
        for (let i = 0; i < 12; i++) { await wheel(p, cx, cy, 240); await sleep(10); }
        a = await geo(p);
        assert.ok(Math.abs(a.s - Math.min(1, 1 / fitOf(a))) < 1e-3, 'wheel down stops at the smaller of fit / 1:1: ' + a.s);
        // ctrl+wheel (a trackpad pinch) zooms too
        await click(p, '.mvz-fit'); await sleep(250);
        await wheel(p, 720, 450, -40, true); await sleep(50);
        a = await geo(p);
        assert.ok(a.s > 1.3, 'ctrl+wheel zooms in: ' + a.s);
        // 原始尺寸: on screen at its own pixels; 适应窗口: back
        await click(p, '.mvz-actual'); await sleep(250);
        a = await geo(p);
        assert.ok(Math.abs(a.iw - NW) < 1 && Math.abs(a.ih - NH) < 1, '1:1 = 320×200 on screen: ' + a.iw + '×' + a.ih);
        writeFileSync(join(OUT, 'after-1440-light-actual.png'), await shoot(p));
        await click(p, '.mvz-fit'); await sleep(250);
        a = await geo(p);
        assert.ok(Math.abs(a.s - 1) < 1e-3 && /translate\(0px, 0px\)/.test(a.tf), 'fit again: ' + a.tf);
        // 放大 / 缩小 by 1.5× around the centre
        await click(p, '.mvz-zoomIn'); await sleep(250);
        assert.ok(Math.abs((await geo(p)).s - 1.5) < 1e-3, 'zoom in = 1.5×');
        await click(p, '.mvz-zoomOut'); await sleep(250);
        assert.ok(Math.abs((await geo(p)).s - 1) < 1e-3, 'zoom out = back to 1×');
        // double-click still toggles 2.5× ↔ 1×
        const dbl = async () => { for (let i = 0; i < 2; i++) { await mouse(p, 'mousePressed', 720, 450); await mouse(p, 'mouseReleased', 720, 450); await sleep(60); } };
        await dbl(); await sleep(200);
        assert.ok(/scale\(2\.5\)/.test((await geo(p)).tf), 'double-click → 2.5×');
        await sleep(350); await dbl(); await sleep(200);
        assert.ok(/scale\(1\)/.test((await geo(p)).tf), 'double-click → 1×');
        // 全屏: the whole preview; Esc leaves full screen first, the next Esc closes the preview
        const fs = await ev(p, `!document.querySelector('.mvz-fullscreen').hidden`);
        if (fs) {
          await click(p, '.mvz-fullscreen');
          await waitFor(p, `document.fullscreenElement === document.querySelector('.mview')`, 4000);
          await sleep(300);
          assert.equal(await ev(p, `document.querySelector('.mvz-fullscreen').getAttribute('aria-label')`), '退出全屏');
          a = await geo(p);
          assert.ok(Math.abs(a.iw - NW * fitOf(a)) < 1.5, 're-fitted to the full-screen stage');
          writeFileSync(join(OUT, 'after-1440-light-fullscreen.png'), await shoot(p));
          await key(p, 'Escape');
          await waitFor(p, `!document.fullscreenElement`, 4000);
          assert.equal(await ev(p, `document.querySelector('.mview').hidden`), false, 'the first Esc only left full screen');
          await sleep(200);
        } else console.log('    (full screen not offered by this browser: skipped)');
        await key(p, 'Escape');
        await waitFor(p, `document.querySelector('.mview').hidden`);
      } finally { await p.dispose(); }
    });
    await C(`p424-phone-${scheme}: 390×844 — fitted, the zoom row under the picture, clear of ×`, async () => {
      const p = await opened(390, 844, scheme);
      try {
        const g = await geo(p), f = fitOf(g);
        assert.ok(Math.abs(g.iw - NW * f) < 1.5 && g.iw > NW, 'fitted to the phone width: ' + g.iw);
        const r = await ev(p, `(() => { const q = (s) => document.querySelector(s).getBoundingClientRect(); const x = q('.mview-x'), z = q('.mvzoom'), st = q('.mstage');
          return { x: [x.left, x.top, x.right, x.bottom], z: [z.left, z.top, z.right, z.bottom], st: st.bottom, vh: innerHeight,
            btns: [...document.querySelectorAll('.mvzoom button')].filter(b => !b.hidden).map(b => { const r = b.getBoundingClientRect(); return [r.width, r.height, r.right <= innerWidth]; }) }; })()`);
        assert.ok(r.z[1] >= r.x[3], 'the zoom row is below the bar with ×');
        assert.ok(r.z[1] >= r.st - 0.5 && r.z[3] <= r.vh + 0.5, 'under the stage, on screen');
        assert.ok(r.btns.length >= 4 && r.btns.every(([w, h, inside]) => w >= 44 && h >= 44 && inside), 'buttons ≥ 44 px, all on screen');
        writeFileSync(join(OUT, `after-390-${scheme}-fit.png`), await shoot(p));
      } finally { await p.dispose(); }
    });
  }
}
await B.close(); await fake.stop(); await web.stop();
console.log(failed ? `p424_zoom: ${failed} FAILED` : 'p424_zoom: all passed');
process.exit(failed ? 1 : 0);
