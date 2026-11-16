// P593 r3 — R-0001: the iPhone status bar above Agent J is Sooty #141414 on every path, forever.
//
// Root cause this gate locks (IMG_2821, 2026-11-16 06:47, iPhone Air Home Screen app):
//   * With apple-mobile-web-app-status-bar-style=default (iOS 26), the WebView starts BELOW an opaque
//     status bar that iOS paints itself: env(safe-area-inset-top)=0, the bar is 204 px = 68 pt, the page
//     begins at row 204. So html::before (height = safe-top) was 0 px tall and never painted.
//   * iOS colours that bar from the page itself. WebKit Page::computeSampledPageTopColorIfNecessary()
//     samples 5 points of the page's top row (y=0) at the first contentful rendering update and keeps
//     the value until a new document loads (clearSampledPageTopColor on load); fixed-container edges
//     sample a 2 px strip. theme-color (#141414) is not what the bar shows.
//   * The body is the state colour (--pg). A reload while idle froze Emerald (#629887 + 28 % Sooty =
//     #4c7367, the exact pixel in IMG_2821) and the later Windstorm page never re-sampled it.
// Playwright WebKit has no iOS status bar and no sampled-top-colour API, so the gate emulates the
// sampler on real rendered pixels: every step the top row must be all Sooty (whatever moment iOS
// samples), and the per-document frozen sample is reported as "the bar iOS would show".
//
// Modes: home  = real iOS 26 Home Screen geometry (safe-top 0, page under an iOS-painted bar);
//        notch = simulated 59/34 px safe area (Safari / page drawn under the bar): rows 0..58 Sooty.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { inflateSync } from 'node:zlib';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const pw = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const out = process.env.P593_OUT || 'reports/qa/p593-r3';
const publicDir = process.env.P593_PUBLIC || undefined;           // main reproduction: serve another public/
const shots = process.env.P593_SHOTS !== '0';
mkdirSync(out, { recursive: true });
const web = await startWebServer(publicDir ? { publicDir } : {});
const fake = await startFakeHost();
const browser = await pw.webkit.launch();
const W = 390, H = 844, SOOTY = [20, 20, 20];
const ua = pw.devices['iPhone 13'].userAgent;

function png(buf) {                                                  // minimal 8-bit RGB/RGBA PNG decoder
  let o = 8, w, h, ct; const idat = [];
  while (o < buf.length) {
    const len = buf.readUInt32BE(o), type = buf.toString('latin1', o + 4, o + 8), d = buf.subarray(o + 8, o + 8 + len);
    if (type === 'IHDR') { w = d.readUInt32BE(0); h = d.readUInt32BE(4); ct = d[9]; if (d[8] !== 8 || d[12]) throw new Error('png'); }
    if (type === 'IDAT') idat.push(d);
    o += 12 + len;
  }
  const bpp = ct === 6 ? 4 : 3, raw = inflateSync(Buffer.concat(idat)), stride = w * bpp, px = Buffer.alloc(h * stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)], src = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? px[y * stride + x - bpp] : 0, b = y ? px[(y - 1) * stride + x] : 0, c = x >= bpp && y ? px[(y - 1) * stride + x - bpp] : 0;
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
      const pred = [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? b : c][f];
      px[y * stride + x] = (src[x] + pred) & 255;
    }
  }
  return { w, h, at: (x, y) => [...px.subarray(y * stride + x * bpp, y * stride + x * bpp + 3)] };
}
const hex = c => '#' + c.map(v => v.toString(16).padStart(2, '0')).join('');

async function fakeViewport(context) {
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { get: () => true });
    // A controllable visualViewport: the keyboard is height < innerHeight with offsetTop > 0.
    const vv = new EventTarget(); let h = null, top = 0;
    Object.defineProperties(vv, {
      height: { get: () => h ?? innerHeight }, width: { get: () => innerWidth },
      offsetTop: { get: () => top }, offsetLeft: { get: () => 0 }, scale: { get: () => 1 },
      pageTop: { get: () => top }, pageLeft: { get: () => 0 },
    });
    Object.defineProperty(window, 'visualViewport', { get: () => vv });
    window.__vvSet = (height, offsetTop) => { h = height; top = offsetTop; vv.dispatchEvent(new Event('resize')); };
  });
}

async function check(page, ctx, label) {
  const g = await page.evaluate(() => {
    const html = document.documentElement, b = getComputedStyle(html, '::before');
    return { vvtop: html.style.getPropertyValue('--vvtop') || '', top: parseFloat(b.top) || 0, height: parseFloat(b.height) || 0,
      before: b.backgroundColor, safeTop: parseFloat(getComputedStyle(html).getPropertyValue('--ios-safe-top')) || 0,
      underPage: getComputedStyle(html).backgroundColor, status: document.body.dataset.status, pg: getComputedStyle(document.body).backgroundColor,
      theme: [...document.querySelectorAll('meta[name="theme-color"]')].map(m => m.content.toLowerCase()) };
  });
  // The visible top edge: layout row --vvtop while the keyboard pans the visual viewport, else 0.
  const y0 = Math.round(parseFloat(g.vvtop) || 0);
  const band = ctx.mode === 'notch' ? Math.max(1, Math.round(g.safeTop)) : 2;
  const img = png(await page.screenshot({ clip: { x: 0, y: y0, width: W, height: band } }));
  const bad = [];
  for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) {
    const c = img.at(x, y); if (c.some((v, i) => Math.abs(v - SOOTY[i]) > 1)) bad.push({ x, y: y + y0, c: hex(c) });
  }
  // WebKit PageColorSampler::sampleTop: 5 points on the top row.
  const five = [0, 1, 2, 3, 4].map(i => img.at(Math.floor((W - 1) * i / 4), 0));
  const sample = hex([0, 1, 2].map(k => Math.round(five.reduce((s, c) => s + c[k], 0) / 5)));
  if (!ctx.frozen) ctx.frozen = sample;                              // first sample of this document sticks
  const row = { mode: ctx.mode, scheme: ctx.scheme, step: label, status: g.status, page: g.pg, vvtop: g.vvtop || '0',
    layer: `${g.before} top=${g.top} h=${g.height}`, topRow: sample, iosBar: ctx.frozen, underPage: g.underPage,
    covered: `${y0}..${y0 + band - 1}`, nonSooty: bad.length, firstBad: bad[0] || null };
  const errs = [];
  if (bad.length) errs.push(`top rows ${row.covered} have ${bad.length} non-Sooty px, e.g. ${JSON.stringify(bad[0])}`);
  if (ctx.frozen !== '#141414') errs.push(`iOS-sampled bar would be ${ctx.frozen} (page ${g.pg})`);
  if (!g.theme.length || g.theme.some(t => t !== '#141414')) errs.push(`theme-color ${g.theme}`);
  if (g.underPage !== 'rgb(20, 20, 20)') errs.push(`under-page ${g.underPage}`);
  if (g.top !== y0) errs.push(`layer top ${g.top} != visible top ${y0}`);
  row.ok = !errs.length; row.errors = errs;
  ctx.rows.push(row);
  console.log(`${row.ok ? 'ok  ' : 'FAIL'} ${ctx.mode}/${ctx.scheme}/${label}: status=${g.status} page=${g.pg} topRow=${sample} iosBar=${ctx.frozen}${errs.length ? ' — ' + errs.join('; ') : ''}`);
  if (shots) await page.screenshot({ path: `${out}/${ctx.scheme}-${ctx.mode}-${label}.png` });
}

const setState = (page, s) => page.evaluate(s => { document.body.dataset.status = s; document.body.dataset.conn = 'on'; }, s);
const deepen = page => page.evaluate(() => document.body.style.setProperty('--deepen', '28%'));  // the owner's theme in IMG_2821
const safe = (page, mode) => mode === 'notch' && page.evaluate(() => {
  const h = document.documentElement; h.style.setProperty('--ios-safe-top', '59px'); h.style.setProperty('--ios-safe-bottom', '34px');
});
const settle = page => page.waitForTimeout(400);                     // viewport.js re-checks now, next frame and +300 ms

const all = [];
for (const mode of ['home', 'notch']) for (const scheme of ['light', 'dark']) {
  const context = await browser.newContext({ userAgent: ua, viewport: { width: W, height: H }, colorScheme: scheme,
    serviceWorkers: 'block', isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  await fakeViewport(context);
  const page = await context.newPage();
  page.on('crash', () => console.log(`CRASH ${mode}/${scheme}`));
  page.on('pageerror', e => console.log(`PAGEERROR ${mode}/${scheme}: ${e.message}`));
  const ctx = { mode, scheme, rows: [], frozen: null };
  fake.reset(); fake.setUp(true);
  await page.goto(fake.newPairing(web.url + '?lang=en'), { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__ajState === 'awaiting-approval');
  await fake.approve();
  await page.waitForFunction(() => window.__ajState === 'ready');
  await deepen(page); await safe(page, mode); await setState(page, 'idle');
  await check(page, ctx, '1-first-open-idle');
  // Keyboard: focus the composer, the visual viewport shrinks and pans (offsetTop > 0).
  await page.focus('#input');
  await page.evaluate(() => window.__vvSet(508, 120)); await settle(page);
  await check(page, ctx, '2-keyboard-up');
  // Keyboard down, but iOS leaves a stale offsetTop (hypothesis A).
  await page.evaluate(() => { document.activeElement?.blur(); window.__vvSet(null, 120); }); await settle(page);
  await check(page, ctx, '3a-keyboard-down-stale-offset');
  await page.evaluate(() => window.__vvSet(null, 0)); await settle(page);
  await check(page, ctx, '3b-keyboard-down');
  for (const [s, label] of [['idle', '4a-idle'], ['working', '4b-working'], ['idle', '4c-done-green'], ['working', '4d-blue']]) {
    await setState(page, s); await check(page, ctx, label);
  }
  // Reload while the page is green (done/idle): a new document, so iOS samples again.
  await setState(page, 'idle');
  await page.reload({ waitUntil: 'domcontentloaded' });
  ctx.frozen = null;
  await page.waitForFunction(() => window.__ajState === 'ready');
  await deepen(page); await safe(page, mode); await setState(page, 'idle');
  await check(page, ctx, '5a-reload-green');
  await setState(page, 'working');
  await check(page, ctx, '5b-after-reload-blue');
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('pageshow')); window.dispatchEvent(new Event('focus'));
  });
  await settle(page);
  await check(page, ctx, '6-foreground');
  const btn = await page.evaluate(() => [...document.querySelectorAll('.btn')].map(b => getComputedStyle(b).borderTopWidth).filter(w => w !== '0px'));
  if (btn.length) ctx.rows.push({ mode, scheme, step: 'btn-border', ok: false, errors: [`.btn borders ${btn}`] });
  all.push(...ctx.rows);
  await context.close();
}
await browser.close(); await fake.stop(); await web.stop();
writeFileSync(`${out}/p593-rows.json`, JSON.stringify(all, null, 1));
const failed = all.filter(r => !r.ok);
console.log(failed.length ? `P593 FAIL ${failed.length}/${all.length} ${out}` : `P593 PASS ${all.length} steps ${out}`);
process.exit(failed.length ? 1 : 0);
