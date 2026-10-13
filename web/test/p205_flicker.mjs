#!/usr/bin/env node
// P205 item 3 (2026-10-13, macOS Chrome): right after sending, as the page turns blue (idle → working) and before the
// reply comes, a grey blurry horizontal line flickers under the robots, right of the load line; gone once the reply renders.
//
// Measured cause: the reply card's pinned-header fade (.rmtop::after, 追加 2 of P181) had `transition: opacity .2s`. When the
// owner had scrolled the previous reply (.rm.under → fade at opacity 1) and sent, the new page resets the scroll, the fade
// animates 1 → 0 on its own compositor layer while the state colour changes, and Chromium keeps drawing that layer's stale
// tiles — the OLD card colour's 14 px gradient (the grey band right of the load line) — on and off until the reply repaints
// the card. Without the transition the fade never gets an animation layer.
//
// Three engines, the real page + the local fake host (never :9222), each running CYCLES sends with the status change landing
// at different points of the page switch:
//   chromium-desktop 1440×900 · chromium-android 412×915 (isMobile, touch) · webkit-iphone 390×844 (isMobile, touch)
// Per cycle, every animation frame from just before the send until the reply has rendered:
//   (dom)    exactly one load line (#loadLine) is visible in the reply card; the header fade is never visible while nothing
//            is under it (main.scrollTop ≤ 1); no other blurred / glowing / gradient line-like element sits in the band
//            under the header (the fade band, right of the load line).
//   (pixels) the band right of the load line, 0–16 px under the pinned header, is the card's own colour in every working
//            frame. Chromium: lossless compositor frames (CDP Page.startScreencast, PNG). WebKit: Playwright's video →
//            ffmpeg PNGs (lossy, so a looser threshold).
// Frames: reports/qa/p205/flicker-<tag>-<browser>-NN.png (AJ_P205_TAG, default "run"; the worst working frame of each cycle,
// header crop) + reports/qa/p205/flicker-<tag>.json.
//   node web/test/p205_flicker.mjs          AJ_P205_ONLY=chromium-desktop,webkit-iphone  AJ_P205_CYCLES=6
// WebKit needs the owner's Playwright package (AJ_PLAYWRIGHT_MODULE) and PLAYWRIGHT_BROWSERS_PATH (gate PHASE_ENV), with the
// ICU libs in ~/.cache/ms-playwright/webkit-<rev>/minibrowser-*/sys/lib (documentation/NEXT_SESSION.md).
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync, mkdtempSync } from 'node:fs';
import { inflateSync, deflateSync, crc32 } from 'node:zlib';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

process.env.PW_TEST_SCREENSHOT_NO_FONTS_READY ||= '1';
const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p205/';
mkdirSync(OUT, { recursive: true });
const TAG = process.env.AJ_P205_TAG || 'run';
const CYCLES = +(process.env.AJ_P205_CYCLES || 4);
const ONLY = (process.env.AJ_P205_ONLY || '').split(',').filter(Boolean);
const DELAYS = [0, 60, 120, 180, 240, 30, 90, 150];      // ms from the send tap to the host's `working`
const LONG = '这是上一条回复，它很长，要往下滚才能读完。'.repeat(160);

const ENGINES = [
  { name: 'chromium-desktop', type: 'chromium', vp: { width: 1440, height: 900 }, opts: {} },
  { name: 'chromium-android', type: 'chromium', vp: { width: 412, height: 915 }, opts: { isMobile: true, hasTouch: true, deviceScaleFactor: 2.625 } },
  { name: 'webkit-iphone', type: 'webkit', vp: { width: 390, height: 844 }, opts: { isMobile: true, hasTouch: true, deviceScaleFactor: 3 } },
];

// ---- a minimal PNG codec (8-bit RGB / RGBA, non-interlaced: what Chromium's screencast and ffmpeg write) ----------------
function decodePng(buf) {
  let o = 8, w = 0, h = 0, ct = 0; const idat = [];
  while (o < buf.length) {
    const n = buf.readUInt32BE(o), type = buf.toString('latin1', o + 4, o + 8), d = buf.subarray(o + 8, o + 8 + n);
    if (type === 'IHDR') { w = d.readUInt32BE(0); h = d.readUInt32BE(4); ct = d[9]; if (d[8] !== 8 || d[12] !== 0) throw new Error('png: 8-bit non-interlaced only'); }
    else if (type === 'IDAT') idat.push(d);
    o += 12 + n;
  }
  const bpp = ct === 6 ? 4 : ct === 2 ? 3 : 0; if (!bpp) throw new Error('png: colour type ' + ct);
  const raw = inflateSync(Buffer.concat(idat)), stride = w * bpp, px = Buffer.alloc(w * h * 3);
  let prev = Buffer.alloc(stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)], line = Buffer.from(raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1)));
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? line[i - bpp] : 0, b = prev[i], c = i >= bpp ? prev[i - bpp] : 0;
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
      line[i] = (line[i] + (f === 1 ? a : f === 2 ? b : f === 3 ? (a + b) >> 1 : f === 4 ? (pa <= pb && pa <= pc ? a : pb <= pc ? b : c) : 0)) & 255;
    }
    for (let x = 0; x < w; x++) for (let k = 0; k < 3; k++) px[(y * w + x) * 3 + k] = line[x * bpp + k];
    prev = line;
  }
  return { w, h, px };
}
function encodePng({ w, h, px }) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) px.copy(raw, y * (w * 3 + 1) + 1, y * w * 3, (y + 1) * w * 3);
  const chunk = (type, d) => { const b = Buffer.alloc(12 + d.length); b.writeUInt32BE(d.length, 0); b.write(type, 4, 'latin1'); d.copy(b, 8);
    b.writeUInt32BE(crc32(Buffer.concat([Buffer.from(type, 'latin1'), d])) >>> 0, 8 + d.length); return b; };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
const crop = (img, x, y, w, h) => {
  x = Math.max(0, Math.round(x)); y = Math.max(0, Math.round(y)); w = Math.min(img.w - x, Math.round(w)); h = Math.min(img.h - y, Math.round(h));
  const px = Buffer.alloc(w * h * 3);
  for (let r = 0; r < h; r++) img.px.copy(px, r * w * 3, ((y + r) * img.w + x) * 3, ((y + r) * img.w + x + w) * 3);
  return { w, h, px };
};

/** The band right of the load line, 0–16 px under the pinned header (CSS px → image px by the frame's own scale).
 *  Returns the rows where ≥ 30 % of the band differs from the card colour (sampled 40 px further down, where the
 *  working page has nothing) by more than `th` (sum of |ΔR|+|ΔG|+|ΔB|), and whether the frame is a working (blue) frame. */
function scanBand(img, geo, th) {
  const s = img.w / geo.vw, P = (x, y) => { const i = (Math.round(y) * img.w + Math.round(x)) * 3; return [img.px[i], img.px[i + 1], img.px[i + 2]]; };
  const x0 = geo.bandL * s, x1 = geo.bandR * s, y0 = (geo.headB - 1) * s, y1 = (geo.headB + 17) * s;
  const ref = P((x0 + x1) / 2, (geo.headB + 40) * s);
  const working = ref[2] > ref[1] + 25;                     // the working card is the blue one
  let rows = 0, worst = 0;
  const step = Math.max(1, Math.round(s));
  for (let y = Math.round(y0); y < y1; y += step) {
    let n = 0, m = 0;
    for (let x = Math.round(x0); x < x1; x += step) { const p = P(x, y), d = Math.abs(p[0] - ref[0]) + Math.abs(p[1] - ref[1]) + Math.abs(p[2] - ref[2]); m++; if (d > th) n++; worst = Math.max(worst, d); }
    if (n >= m * 0.3) rows++;
  }
  return { working, rows, worst, ref };
}

/** In-page sampler: every animation frame until stopped. */
const SAMPLER = () => {
  const $ = (id) => document.getElementById(id);
  const vis = (e) => { for (let n = e; n && n.nodeType === 1; n = n.parentElement) { const c = getComputedStyle(n); if (c.display === 'none' || c.visibility === 'hidden' || +c.opacity === 0) return false; } const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const S = window.__p205 = { frames: [], on: true };
  const tick = () => {
    if (!S.on) return;
    const rm = $('rm'), top = $('rmTop'), main = $('main'), line = $('loadLine');
    const hb = top.getBoundingClientRect(), lb = line.getBoundingClientRect(), cb = rm.getBoundingClientRect();
    const lines = [...rm.querySelectorAll('.loadline')].filter(vis).length;
    const fade = +getComputedStyle(top, '::after').opacity;
    // anything else line-like (≤ 20 px tall, ≥ 40 px wide) in the band under the header that blurs, glows or is a gradient
    const ghosts = [];
    for (const e of rm.querySelectorAll('*')) {
      if (e === line || e.closest('.loadhit') || !vis(e)) continue;
      const r = e.getBoundingClientRect();
      if (r.height > 20 || r.width < 40 || r.bottom < hb.bottom - 4 || r.top > hb.bottom + 20) continue;
      const c = getComputedStyle(e);
      if (/blur/.test(c.filter) || (c.boxShadow !== 'none' && c.boxShadow) || /gradient/.test(c.backgroundImage)) ghosts.push(e.id || e.className || e.tagName);
    }
    S.frames.push({ t: performance.now(), st: document.body.dataset.status, lines, fade, sc: main.scrollTop, under: rm.classList.contains('under'), ghosts,
      geo: { vw: innerWidth, headB: hb.bottom, bandL: lb.right + 12, bandR: cb.right - 16, cardL: cb.left, cardT: cb.top } });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const report = { tag: TAG, cycles: CYCLES, engines: {} };
let failed = 0;
try {
  for (const E of ENGINES) {
    if (ONLY.length && !ONLY.includes(E.name)) continue;
    const R = report.engines[E.name] = { cycles: [], ok: true };
    let B;
    try { B = await pw[E.type].launch(E.type === 'chromium' ? { executablePath: process.env.CHROMIUM || '/usr/bin/chromium' } : {}); }
    catch (e) { R.ok = false; R.error = 'launch: ' + String(e.message || e).split('\n')[0]; failed++; console.log(`✗ ${E.name}: ${R.error}`); continue; }
    const vdir = mkdtempSync(join(tmpdir(), 'p205-video-'));
    const ctx = await B.newContext({ viewport: E.vp, ...E.opts, ...(E.type === 'webkit' ? { recordVideo: { dir: vdir, size: E.vp } } : {}) });
    const p = await ctx.newPage();
    const problems = [];
    p.on('pageerror', (e) => problems.push(String(e)));
    // WebKit on Linux has no audio stack; the Send gesture's whoosh player (sfx.unlock) would stall the tap there.
    if (E.type === 'webkit') await p.addInitScript(() => { window.AudioContext = undefined; window.webkitAudioContext = undefined;
      window.Audio = function () { return { preload: '', volume: 1, currentTime: 0, play: () => Promise.resolve(), pause() {} }; }; });
    fake.reset();
    await p.goto(fake.newPairing(web.url + '?lang=zh'));
    await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 20000 });
    await fake.approve();
    await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 20000 });
    await p.evaluate(() => { for (const id of ['phone-env-dismiss', 'confirm-no', 'a2hs-ok']) { const e = document.getElementById(id); if (e && e.offsetParent) e.click(); } });
    fake.st.autoReply = false;
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await fake.send({ t: 'busy', by: 'Agent J', tasks: [0, 1, 2].map((i) => ({ id: 'r' + i, who: null, kind: 'temp', label: 'task ' + i, since: 1760000000 + i, c: i })), load: 0.5 });
    const cdp = E.type === 'chromium' ? await ctx.newCDPSession(p) : null;
    const t0 = Date.now();
    for (let k = 0; k < CYCLES; k++) {
      const C = { k, delay: DELAYS[k % DELAYS.length], dom: {}, px: {} };
      R.cycles.push(C);
      // the previous reply: long, read part-way down (the pinned header's fade is on), the page idle (green)
      await fake.addTurn({ k: 'phone', dev: 'other', text: '上一条问题 ' + k }, LONG);
      await p.waitForFunction((k) => document.getElementById('omText').textContent.includes('上一条问题 ' + k), k, { timeout: 10000 });
      await p.waitForTimeout(500);
      await p.evaluate(() => { const m = document.getElementById('main'); m.scrollTop = 60; m.dispatchEvent(new Event('scroll')); });
      await p.waitForTimeout(500);
      C.before = await p.evaluate(() => ({ under: document.getElementById('rm').classList.contains('under'), st: document.body.dataset.status }));
      await p.fill('#input', '你好，帮我看一下 ' + k);
      const frames = [];
      if (cdp) {
        cdp.removeAllListeners('Page.screencastFrame');
        cdp.on('Page.screencastFrame', (f) => { frames.push({ t: Date.now(), data: f.data }); cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {}); });
        await cdp.send('Page.startScreencast', { format: 'png', everyNthFrame: 1 });
      }
      await p.evaluate(SAMPLER);
      const tSend = Date.now();
      if (E.name === 'chromium-android') await p.tap('#send', { timeout: 5000 }); else await p.click('#send', { timeout: 5000 });
      await p.waitForTimeout(C.delay);
      await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J' });
      const tWork = Date.now();
      await p.waitForTimeout(1100);
      const turn = fake.st.turns.at(-1);
      const tReply = Date.now();
      await fake.updateTurn(turn.id, { reply: { text: '好的，新回复来了 ' + k }, end: 'done' });
      await p.waitForFunction((k) => document.getElementById('words').textContent.includes('新回复来了 ' + k), k, { timeout: 10000 });
      await p.waitForTimeout(250);
      if (cdp) await cdp.send('Page.stopScreencast');
      const S = await p.evaluate(() => { window.__p205.on = false; return window.__p205.frames; });
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      Object.assign(C, { tSend: tSend - t0, tWork: tWork - t0, tReply: tReply - t0 });
      // (dom)
      const wf = S.filter((f) => f.st === 'working');
      C.dom = {
        frames: S.length, working: wf.length,
        lineCounts: [...new Set(S.map((f) => f.lines))],
        fadeWithNothingUnder: S.filter((f) => f.fade > 0.01 && f.sc <= 1).map((f) => +f.fade.toFixed(3)),
        ghosts: [...new Set(S.flatMap((f) => f.ghosts))],
      };
      C.geo = (wf.at(-1) || S.at(-1)).geo;
      // (pixels)
      if (cdp) {
        const work = frames.filter((f) => f.t >= tWork && f.t < tReply);
        let worst = null;
        C.px = { frames: frames.length, inWindow: work.length, workingFrames: 0, ghostFrames: 0 };
        for (const f of work) {
          const img = decodePng(Buffer.from(f.data, 'base64')), r = scanBand(img, C.geo, 18);
          if (!r.working) continue;
          C.px.workingFrames++;
          if (r.rows) C.px.ghostFrames++;
          if (!worst || r.rows > worst.r.rows || (r.rows === worst.r.rows && r.worst > worst.r.worst)) worst = { img, r, t: f.t - tWork };
        }
        if (worst) { C.px.worst = { rows: worst.r.rows, delta: worst.r.worst, msAfterWorking: worst.t }; C.shot = worst; }
      }
      const okDom = C.dom.lineCounts.length === 1 && C.dom.lineCounts[0] === 1 && !C.dom.fadeWithNothingUnder.length && !C.dom.ghosts.length;
      const okPx = !cdp || (C.px.workingFrames > 0 && C.px.ghostFrames === 0);
      C.ok = okDom && okPx && C.before.under && C.dom.working > 0;
      console.log(`${C.ok ? '✓' : '✗'} ${E.name} cycle ${k} (working +${C.delay} ms): under-before=${C.before.under} dom ${C.dom.frames}f/${C.dom.working}w lines=${C.dom.lineCounts} ` +
        `fade-with-nothing-under=${C.dom.fadeWithNothingUnder.length ? C.dom.fadeWithNothingUnder.slice(0, 4).join(',') + '…(' + C.dom.fadeWithNothingUnder.length + ')' : 0} ghosts=${C.dom.ghosts.join('|') || 0}` +
        (cdp ? ` · pixels ${C.px.workingFrames} working frames, ${C.px.ghostFrames} with a band${C.px.worst ? ` (worst ${C.px.worst.rows} rows, Δ${C.px.worst.delta})` : ''}` : ''));
      if (!C.ok) R.ok = false;
    }
    await ctx.close();
    // WebKit: the recorded video → PNG frames → the same band scan (lossy codec: looser threshold), for each cycle's window
    if (E.type === 'webkit') {
      const vid = readdirSync(vdir).find((f) => f.endsWith('.webm'));
      const fdir = join(vdir, 'f'); mkdirSync(fdir);
      let fps = 25;
      try { execFileSync('ffmpeg', ['-loglevel', 'error', '-i', join(vdir, vid), '-vf', `fps=${fps}`, join(fdir, '%05d.png')]); }
      catch (e) { R.videoError = String(e.message || e).split('\n')[0]; }
      const files = readdirSync(fdir).filter((f) => f.endsWith('.png')).sort();
      // the video starts with the context; frame i ≈ i / fps s after it. Its own clock: align on the first cycle's send by
      // treating the whole recording as covering [0, last tReply + 400 ms] (Playwright pads nothing at the start).
      const span = files.length / fps * 1000, last = R.cycles.at(-1).tReply + 400, off = span - last;
      for (const C of R.cycles) {
        let worst = null; C.px = { video: files.length, workingFrames: 0, ghostFrames: 0 };
        for (let i = 0; i < files.length; i++) {
          const t = i * 1000 / fps - off;
          if (t < C.tWork + 80 || t >= C.tReply) continue;
          const img = decodePng(readFileSync(join(fdir, files[i]))), r = scanBand(img, C.geo, 40);
          if (!r.working) continue;
          C.px.workingFrames++; if (r.rows >= 3) C.px.ghostFrames++;
          if (!worst || r.rows > worst.r.rows || (r.rows === worst.r.rows && r.worst > worst.r.worst)) worst = { img, r, t: t - C.tWork };
        }
        if (worst) { C.px.worst = { rows: worst.r.rows, delta: worst.r.worst, msAfterWorking: Math.round(worst.t) }; C.shot = worst; }
        const okPx = C.px.workingFrames > 0 && C.px.ghostFrames === 0;
        console.log(`  ${okPx ? '✓' : '✗'} ${E.name} cycle ${C.k} video: ${C.px.workingFrames} working frames, ${C.px.ghostFrames} with a band` + (C.px.worst ? ` (worst ${C.px.worst.rows} rows, Δ${C.px.worst.delta})` : ''));
        if (!okPx) { C.ok = false; R.ok = false; }
      }
    }
    for (const C of R.cycles) if (C.shot) {
      const g = C.geo, s = C.shot.img.w / g.vw;
      const img = crop(C.shot.img, (g.cardL - 4) * s, (g.headB - 70) * s, (g.bandR - g.cardL + 24) * s, 110 * s);
      writeFileSync(`${OUT}flicker-${TAG}-${E.name}-${String(C.k).padStart(2, '0')}.png`, encodePng(img));
      delete C.shot;
    }
    rmSync(vdir, { recursive: true, force: true });
    R.problems = problems;
    await B.close();
    if (!R.ok) failed++;
    console.log(`${R.ok ? 'PASS' : 'FAIL'} ${E.name}`);
  }
} finally { await fake.stop(); await web.stop(); }
writeFileSync(`${OUT}flicker-${TAG}.json`, JSON.stringify(report, null, 1) + '\n');
console.log(failed ? `p205-flicker: ${failed} engine(s) failed` : 'p205-flicker: all passed');
process.exit(failed ? 1 : 0);
