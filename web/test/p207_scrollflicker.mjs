#!/usr/bin/env node
// P207 追加 5 (2026-10-13, Mac M1, the installed Web App in macOS Chrome 0.20.1): while the reply scrolls under the pinned
// header of the reply card (.rmtop: name · load line · dancing robots), the header's bottom edge — its 14 px fade
// (.rmtop::after) — flickers for a while; a screenshot never catches it.
//
// Measured cause: the fade's `transition: opacity .2s` (the same rule P205 fixed, commit 0a87a7f3). Every crossing of
// scrollTop 1 started a 0.2 s fade on its own animation layer (intermediate frames; flapping near the top), and on a real
// device scale factor of 2 (zoom-for-DSF, like a Retina Mac) the finished 1 → 0 animation left a stale layer behind: a SOLID
// 14 px band of the card colour under the header at the top (over the reply's first line) that vanished on the next scroll
// tick and came back after the next fade-out — the edge "flickers". Emulated dsf (Emulation.setDeviceMetricsOverride) never
// shows the stale band, hence the chromium-retina engine. Fix = P205's: no transition (app.css), nothing else needed.
//
// What this measures (the real page + the local fake host, never :9222), robots busy (3 tasks) and the load line gliding:
//   chromium-desktop 1440×900 emulated dsf 2 · chromium-retina 1440×900 window, real dsf 2 (--force-device-scale-factor=2,
//   scroll offsets in half CSS px like a Mac trackpad on Retina) · chromium-android 412×915 dsf 2.625 (isMobile, touch)
// Scroll scenarios, each with every compositor frame captured (CDP Page.startScreencast, lossless PNG; CSS-px frames on the
// emulated engines, device-px frames on chromium-retina):
//   momentum  trackpad-like wheel deltas decaying 40 → 0.2 px (fractional), from the top down
//   totop     decaying negative deltas up to the top, then 0.6 s of pushing past it (overscroll at the top)
//   threshold slow ±0.5 / ±1 px steps across scrollTop 0 ↔ 1 ↔ 3, back and forth for ~3 s
//   midpage   small fractional deltas (0.3 … 3 px, both ways) half-way down for ~2.5 s
//   touch     (mobile only) synthetic touch drags down and back up to the top
// The reading area is painted solid black for the measurement (#read and the words: black on black), so the band under the
// header holds nothing but the fade: per frame the band's profile (rows, median over columns) is fitted as
// "header colour down to edge E, then opacity o × the 14 px gradient over black" → (E, o, residual).
//   (dom)    every animation frame: .rm.under, the fade's computed opacity, main.scrollTop → under toggles, flaps (a toggle
//            undone within 300 ms — informative: the threshold scenario reverses that fast on purpose), frames with an
//            intermediate computed opacity (0.02 < o < 0.98: the transition running)
//   (pixels) frames whose fitted o is intermediate (0.12 < o < 0.88), on/off flips on screen (|Δo| > 0.5) beyond the
//            .under toggles (extra flips), frames that fit neither state (misfit: e.g. the solid stale band), and the
//            header edge moving once stuck (edge jitter, scrollTop ≥ 12, fade on).
// PASS = no intermediate frames (dom + pixels), no extra flips, no misfit frames, no edge jitter.
// Out: agentjarvis/reports/qa/p207/flicker/scrollflicker-<tag>.json + the worst frame's header crop per engine × scenario,
// scrollflicker-<tag>-<engine>-<scenario>.png.
//   node web/test/p207_scrollflicker.mjs   AJ_P207F_TAG=before|after  AJ_P207F_ONLY=chromium-retina  AJ_P207F_SCEN=threshold
// Probe knobs (not used by the gate): AJ_P207F_CSS (extra CSS), AJ_P207F_ARGS (Chromium switches). With
// '--enable-features=FractionalScrollOffsets' (off by default) the stuck header's bottom edge jumps 1 px mid-scroll on the
// emulated engines whatever the CSS (sticky inside a fractionally scrolled scroller); real dsf 2 without it: no jitter.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { inflateSync, deflateSync, crc32 } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

process.env.PW_TEST_SCREENSHOT_NO_FONTS_READY ||= '1';
const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'agentjarvis/reports/qa/p207/flicker/';
mkdirSync(OUT, { recursive: true });
const TAG = process.env.AJ_P207F_TAG || 'run';
const ONLY = (process.env.AJ_P207F_ONLY || '').split(',').filter(Boolean);
const SCEN = (process.env.AJ_P207F_SCEN || '').split(',').filter(Boolean);   // a subset of the scenarios
const LONG = '这是一条很长的回复，要往下滚才能读完，滚动的时候看页眉下沿。'.repeat(220);
// extra Chromium switches, e.g. AJ_P207F_ARGS='--enable-features=FractionalScrollOffsets' (macOS-like fractional scroll offsets)
const ARGS = (process.env.AJ_P207F_ARGS || '').split(' ').filter(Boolean);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ENGINES = [
  { name: 'chromium-desktop', vp: { width: 1440, height: 900 }, opts: { deviceScaleFactor: 2 } },
  // a real device scale factor (zoom-for-DSF, like a Retina Mac): scroll offsets in device pixels → half CSS px
  { name: 'chromium-retina', vp: null, args: ['--force-device-scale-factor=2', '--window-size=1440,900'], opts: {} },
  { name: 'chromium-android', vp: { width: 412, height: 915 }, opts: { isMobile: true, hasTouch: true, deviceScaleFactor: 2.625 } },
];

// ---- a minimal PNG codec (8-bit RGB / RGBA, non-interlaced) — the same as p205_flicker.mjs -------------------------------
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

/** The band's profile and its fit. geo (CSS px): headB = the stuck header's bottom, x0..x1 = columns under the header with
 *  nothing but the black reading area (right of the load line). alpha(row) = how much of the card colour shows over
 *  black (1 = header / full fade, 0 = black). Fit: rows above E are the header (alpha 1); from E down the fade
 *  o·(1 − (y + .5) / 14 px). E is searched from 8 CSS px above to 10 below the stuck edge (the header still moves while
 *  sticky engages, scrollTop 0…6). */
function fitBand(img, geo) {
  const s = img.w / geo.vw, F = 14 * s;
  const yA = Math.round((geo.headB - 12) * s), yB = Math.round((geo.headB + 30) * s);
  const cols = []; for (let k = 0; k < 9; k++) cols.push(Math.round((geo.x0 + (geo.x1 - geo.x0) * (k + 0.5) / 9) * s));
  // the card colour as drawn: the header 10 CSS px above its stuck bottom (nothing else there right of the load line)
  const yBg = Math.round((geo.headB - 10) * s), bgv = cols.map((x) => { const i = (yBg * img.w + x) * 3; return img.px[i] + img.px[i + 1] + img.px[i + 2]; }).sort((a, b) => a - b);
  const bgSum = Math.max(1, bgv[4]);
  const prof = [];
  for (let y = yA; y < yB; y++) {
    const v = cols.map((x) => { const i = (y * img.w + x) * 3; return (img.px[i] + img.px[i + 1] + img.px[i + 2]) / bgSum; }).sort((a, b) => a - b);
    prof.push(v[4]);
  }
  const stuck = Math.round(geo.headB * s) - yA;
  let best = null;
  for (let E = stuck - Math.round(8 * s); E <= stuck + Math.round(10 * s); E++) {
    if (E < 4) continue;
    let sgg = 0, sag = 0, err0 = 0;
    for (let r = Math.max(0, E - Math.round(4 * s)); r < E; r++) err0 += (prof[r] - 1) ** 2;
    const rows = [];
    for (let r = E; r < Math.min(prof.length, E + Math.round(18 * s)); r++) { const g = Math.max(0, 1 - (r - E + 0.5) / F); rows.push([prof[r], g]); sgg += g * g; sag += prof[r] * g; }
    const o = Math.min(1.05, Math.max(0, sag / sgg));
    let err = err0; for (const [a, g] of rows) err += (a - o * g) ** 2;
    err /= (rows.length + Math.round(4 * s));
    if (!best || err < best.err) best = { E: E - stuck, o, err };
  }
  // the load line's track row (the header's own content): where it is tells a moving header from a moving fade
  let L = 0, Lmax = -1; const lx = Math.round(geo.lx * s);
  for (let y = Math.round((geo.headB - 18) * s); y < Math.round(geo.headB * s); y++) {
    const i = (y * img.w + lx) * 3, d = Math.abs(img.px[i] + img.px[i + 1] + img.px[i + 2] - bgSum);
    if (d > Lmax) { Lmax = d; L = y; }
  }
  return { E: best.E, Ecss: +(best.E / s).toFixed(2), o: +best.o.toFixed(3), rms: +Math.sqrt(best.err).toFixed(4), L: +((L / s) - geo.headB).toFixed(2) };
}

const SAMPLER = () => {
  const rm = document.getElementById('rm'), top = document.getElementById('rmTop'), main = document.getElementById('main');
  const S = window.__p207f = { frames: [], on: true };
  const tick = () => {
    if (!S.on) return;
    S.frames.push({ t: performance.timeOrigin + performance.now(), sc: main.scrollTop, under: rm.classList.contains('under'), fade: +getComputedStyle(top, '::after').opacity });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};

function domStats(S) {
  let toggles = 0, flaps = 0, last = null; const tog = [];
  for (const f of S) { if (last !== null && f.under !== last) { toggles++; tog.push(f.t); } last = f.under; }
  for (let i = 1; i < tog.length; i++) if (tog[i] - tog[i - 1] < 300) flaps++;
  const mid = S.filter((f) => f.fade > 0.02 && f.fade < 0.98);
  const sc = S.map((f) => f.sc);
  return { frames: S.length, toggles, flaps, intermediate: mid.length, intermediateSample: mid.slice(0, 6).map((f) => +f.fade.toFixed(3)),
    fractionalScroll: S.filter((f) => f.sc % 1).length, scroll: [Math.min(...sc), Math.max(...sc)].map((v) => +v.toFixed(2)) };
}

async function wheel(cdp, x, y, deltas, gap = 16) {
  for (const d of deltas) { await cdp.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX: 0, deltaY: d }); await sleep(gap); }
}
async function drag(cdp, x, y, dy, steps) {
  const pt = (yy) => [{ x, y: yy, id: 1 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: pt(y) });
  for (let i = 1; i <= steps; i++) { await sleep(16); await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: pt(y + dy * i / steps) }); }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
const decay = (v0, k, min) => { const a = []; for (let v = v0; Math.abs(v) > min; v *= k) a.push(+v.toFixed(2)); return a; };

// start = main.scrollTop set (and settled) before the capture begins
const START = { momentum: 0, totop: 260, threshold: 0, midpage: 600, touch: 0 };
const SCENARIOS = {
  async momentum({ cdp, cx, cy }) { await wheel(cdp, cx, cy, decay(40, 0.9, 0.2)); },
  async totop({ cdp, cx, cy }) { await wheel(cdp, cx, cy, [...decay(-30, 0.88, 0.2), ...Array(36).fill(-6), ...decay(-6, 0.8, 0.1)]); },
  async threshold({ cdp, cx, cy }) {
    const seq = []; for (let k = 0; k < 6; k++) seq.push(0.5, 0.5, 1, 1, 0.5, -0.5, -1, -1, -0.5, -0.5, -0.5, 0.5);
    await wheel(cdp, cx, cy, seq, 45);
  },
  async midpage({ cdp, cx, cy }) {
    const seq = []; for (let k = 0; k < 150; k++) seq.push(+((k % 2 ? -1 : 1) * (0.3 + ((k * 37) % 27) / 10)).toFixed(2));
    await wheel(cdp, cx, cy, seq);
  },
  async touch({ cdp, cx, cy }) {   // a finger flick up (reads down, released moving → fling), then a slow drag back to the top
    await drag(cdp, cx, cy + 150, -300, 12); await sleep(700);
    await drag(cdp, cx, cy - 100, 420, 40);
  },
};
async function setScroll(p, v) {
  await p.evaluate((v) => { const m = document.getElementById('main'); m.scrollTop = v; m.dispatchEvent(new Event('scroll')); }, v);
  await sleep(450);
}

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const report = { tag: TAG, args: ARGS, engines: {} };
let failed = 0;
try {
  for (const E of ENGINES) {
    if (ONLY.length && !ONLY.includes(E.name)) continue;
    const R = report.engines[E.name] = { scenarios: {}, ok: true };
    const B = await pw.chromium.launch({ executablePath: process.env.CHROMIUM || '/usr/bin/chromium', args: [...ARGS, ...(E.args || [])] });
    const ctx = await B.newContext({ viewport: E.vp, ...E.opts });
    const p = await ctx.newPage();
    const problems = []; p.on('pageerror', (e) => problems.push(String(e)));
    fake.reset();
    await p.goto(fake.newPairing(web.url + '?lang=zh'));
    await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 20000 });
    await fake.approve();
    await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 20000 });
    await p.evaluate(() => { for (const id of ['phone-env-dismiss', 'confirm-no', 'a2hs-ok']) { const e = document.getElementById(id); if (e && e.offsetParent) e.click(); } });
    fake.st.autoReply = false;
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await fake.send({ t: 'busy', by: 'Agent J', tasks: [0, 1, 2].map((i) => ({ id: 'r' + i, who: null, kind: 'temp', label: 'task ' + i, since: 1760000000 + i, c: i })), load: 0.5 });
    await fake.addTurn({ k: 'phone', dev: 'other', text: '一个问题' }, LONG);
    await p.waitForFunction(() => document.getElementById('words').textContent.length > 2000, null, { timeout: 10000 });
    // the measurement paint: the reading area solid black, so the band under the header shows only the fade
    await p.addStyleTag({ content: '#read,#read #words,#read #words *{background:#000 !important;color:#000 !important}' + (process.env.AJ_P207F_CSS || '') });   // AJ_P207F_CSS: probe-only extra CSS
    await p.waitForTimeout(600);
    const cdp = await ctx.newCDPSession(p);
    // the stuck geometry
    await setScroll(p, 200);
    const geo = await p.evaluate(() => {
      const hb = document.getElementById('rmTop').getBoundingClientRect(), wb = document.getElementById('words').getBoundingClientRect(),
        mb = document.getElementById('main').getBoundingClientRect(), lb = document.getElementById('loadLine').getBoundingClientRect();
      return { vw: innerWidth, headB: hb.bottom, x0: Math.max(wb.left, mb.left, lb.right) + 12, x1: Math.min(wb.right, mb.right) - 6, lx: lb.left + 4,
        cx: (mb.left + mb.right) / 2, cy: (mb.top + mb.bottom) / 2, loadLine: lb.width > 0, robots: document.querySelectorAll('#busyDots .rb').length };
    });
    R.geo = geo;
    const scen = Object.keys(SCENARIOS).filter((n) => (n !== 'touch' || E.opts.hasTouch) && (!SCEN.length || SCEN.includes(n)));
    for (const name of scen) {
      const C = R.scenarios[name] = {};
      const all = [];
      cdp.removeAllListeners('Page.screencastFrame');
      cdp.on('Page.screencastFrame', (f) => { all.push({ t: typeof f.metadata?.timestamp === 'number' ? f.metadata.timestamp * 1000 : Date.now(), data: f.data }); cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {}); });
      // Establish and paint the static baseline before the compositor stream starts.
      // Starting the stream at the old scroll position could deliver its first old frame late.
      await setScroll(p, START[name]);
      await p.screenshot();
      await cdp.send('Page.startScreencast', { format: 'png', everyNthFrame: 1 });
      await sleep(300);
      const tCast = Date.now();
      await p.evaluate(SAMPLER);
      await sleep(120);
      await SCENARIOS[name]({ cdp, cx: geo.cx, cy: geo.cy });
      await sleep(500);
      await cdp.send('Page.stopScreencast');
      const frames = all.filter((f) => f.t >= tCast);
      const S = await p.evaluate(() => { window.__p207f.on = false; return window.__p207f.frames; });
      C.dom = domStats(S);
      let prevO = null, worst = null; const flipT = [];
      const fits = [];
      for (const f of frames) {
        const img = decodePng(Buffer.from(f.data, 'base64')), r = fitBand(img, geo);
        const d = S.filter((x) => x.t <= f.t).at(-1); r.sc = d ? +d.sc.toFixed(2) : null; r.t = f.t - tCast;   // the main thread's scrollTop then
        fits.push(r);
        if (prevO !== null && Math.abs(r.o - prevO) > 0.5) flipT.push(f.t);
        prevO = r.o;
        const bad = (r.o > 0.12 && r.o < 0.88 ? 1 : 0) + (r.rms > 0.06 ? 1 : 0);
        const score = bad * 10 + Math.min(r.o, 1 - r.o) + r.rms;
        if (!worst || score > worst.score) worst = { score, img, r };
      }
      let opFlaps = 0; for (let i = 1; i < flipT.length; i++) if (flipT[i] - flipT[i - 1] < 300) opFlaps++;
      const Es = {}; for (const r of fits) if (r.o > 0.5) Es[r.Ecss] = (Es[r.Ecss] || 0) + 1;
      // once stuck (scrollTop ≥ 12 a frame before and after) the header's bottom edge must not move. Fade-on frames only: the
      // main thread's scrollTop runs ahead of the compositor frame, so a frame tagged ≥ 12 may still be an at-the-top one
      const stuckE = fits.filter((r, i) => r.sc >= 12 && fits[i - 1]?.sc >= 12 && r.o > 0.5 && fits[i - 1].o > 0.5).map((r) => r.E);
      const modeE = stuckE.length ? +Object.entries(stuckE.reduce((m, e) => (m[e] = (m[e] || 0) + 1, m), {})).sort((a, b) => b[1] - a[1])[0][0] : 0;
      const edgeJitter = stuckE.filter((e) => e !== modeE).length;
      C.px = { frameW: frames.length ? decodePng(Buffer.from(frames[0].data, 'base64')).w : 0, frames: frames.length, intermediate: fits.filter((r) => r.o > 0.12 && r.o < 0.88).length,
        intermediateSample: fits.filter((r) => r.o > 0.12 && r.o < 0.88).slice(0, 8).map((r) => r.o),
        opacityFlips: flipT.length, extraFlips: Math.max(0, flipT.length - C.dom.toggles), opacityFlaps: opFlaps, edgeJitterStuck: edgeJitter, stuckFrames: stuckE.length,
        series: fits.map((r) => [r.t, r.sc, r.Ecss, r.o, r.L]), misfit: fits.filter((r) => r.rms > 0.06).length,
        maxRms: Math.max(0, ...fits.map((r) => r.rms)), edgeRowsWhenOn: Es, worst: worst && worst.r };
      if (worst) {
        const s = worst.img.w / geo.vw;
        writeFileSync(`${OUT}scrollflicker-${TAG}-${E.name}-${name}.png`,
          encodePng(crop(worst.img, (geo.x0 - 30) * s, (geo.headB - 70) * s, Math.min(geo.x1 - geo.x0 + 60, 700) * s, 110 * s)));
      }
      // a flap that follows the finger (scrolled back over the 1 px line within 300 ms) is the fade doing its job; what fails is
      // anything the scroll did not ask for: in-between opacity, more on/off flips on screen than .under toggles, a stale band
      C.ok = !C.dom.intermediate && !C.px.intermediate && !C.px.extraFlips && !C.px.misfit && !edgeJitter && frames.length > 5;
      if (!C.ok) R.ok = false;
      console.log(`${C.ok ? '✓' : '✗'} ${E.name} ${name}: dom ${C.dom.frames}f scroll ${C.dom.scroll} toggles=${C.dom.toggles} flaps=${C.dom.flaps} ` +
        `intermediate=${C.dom.intermediate}${C.dom.intermediate ? ' (' + C.dom.intermediateSample + '…)' : ''} · pixels ${C.px.frames}f intermediate=${C.px.intermediate} ` +
        `flips=${C.px.opacityFlips} (extra ${C.px.extraFlips}) flaps=${C.px.opacityFlaps} edge-jitter=${edgeJitter}/${stuckE.length} misfit=${C.px.misfit} (max rms ${C.px.maxRms}) edges-on=${JSON.stringify(Es)}`);
    }
    R.problems = problems;
    R.robotsBusy = geo.robots; R.loadLine = geo.loadLine;
    await B.close();
    if (!R.ok) failed++;
    console.log(`${R.ok ? 'PASS' : 'FAIL'} ${E.name} (robots ${geo.robots}, load line ${geo.loadLine})`);
  }
} finally { await fake.stop(); await web.stop(); }
writeFileSync(`${OUT}scrollflicker-${TAG}.json`, JSON.stringify(report, null, 1) + '\n');
console.log(failed ? `p207-scrollflicker: ${failed} engine(s) failed` : 'p207-scrollflicker: all passed');
process.exit(failed ? 1 : 0);
