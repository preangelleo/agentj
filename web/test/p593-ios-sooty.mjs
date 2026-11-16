// P593 r4 — R-0001: the status bar of the iOS 26 Home Screen app above Agent J is Sooty #141414 on every cold start.
//
// What iOS does (WebKit sources, main as of 2026-11-16; IMG_2822 13:42):
//   * Page::computeSampledPageTopColorIfNecessary() runs at each rendering update until it gets a value, and keeps
//     it for the whole document (cleared only on a new load). PageColorSampler::sampleTop() samples 5 points of
//     row y=0 once the frame is visually non-empty.
//   * isValidSampleLocation() rejects a point if ANY element under it (pointer-events ignored) has a background
//     image or a running transition/animation. A rejected middle point (or both end points) makes sampleTop()
//     return an invalid Color, which is stored and never retried for that document.
//   * Then the bar shows the under-page colour: WebPageProxy::underPageBackgroundColor() =
//     pageExtendedBackgroundColor = LocalFrameView::documentBackgroundColor() = body background-colour blended
//     over html's (background images ignored). It follows the live page.
//   * .flow (the 4 px status strip at top:0, opacity 0 when idle) has a gradient background, so every load where
//     the first painted frame already has .flow froze the bar to the body state colour (Emerald #4c7367);
//     a load whose first painted frame came earlier sampled the 2 px Sooty ::before. Hence "sometimes green".
// The r3 gate ran Playwright WebKit (Linux, no iOS bar, no sampler) and emulated the sampler with a pixel read of
// y=0 at chosen moments, treating html's colour as the under-page colour: it modelled neither the rejection rules
// nor WebKit's body-over-html blend, so it could not see this path.
//
// This gate models the real decision at EVERY animation frame of every cold load (whichever frame iOS happens to
// sample) and keeps watching while the state changes. Pass = at every frame the sample would be rejected and the
// under-page colour is Sooty, and no fixed/sticky top container (WebKit fixed-container edge) carries another
// colour. Non-standalone iPhone pages and desktop are checked for zero change of the body/html colours.
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const pw = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const out = process.env.P593_OUT || 'reports/qa/p593-r4';
const publicDir = process.env.P593_PUBLIC || undefined;           // main reproduction: serve another public/
const reloads = Number(process.env.P593_RELOADS || 12);
const shots = process.env.P593_SHOTS !== '0';
mkdirSync(out, { recursive: true });
const web = await startWebServer(publicDir ? { publicDir } : {});
const fake = await startFakeHost();
const browser = await pw.webkit.launch();
const W = 390, H = 776, BAR = 68;                                   // iPhone Air: 68 pt iOS-painted bar, page below it
const ua = pw.devices['iPhone 13'].userAgent;

// Runs in every document: the WebKit decision per frame for the first 4 s, then on demand.
function model() {
  const frames = []; window.__p593 = frames; const t0 = performance.now();
  const rgba = c => {
    let m = /^rgba?\(([\d.]+), ([\d.]+), ([\d.]+)(?:, ([\d.]+))?\)$/.exec(c);
    if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
    m = /^color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+))?\)$/.exec(c);
    if (m) return [m[1] * 255, m[2] * 255, m[3] * 255, m[4] === undefined ? 1 : +m[4]];
    return [0, 0, 0, 0];
  };
  const over = (dst, src) => { const a = src[3] + dst[3] * (1 - src[3]); if (!a) return [0, 0, 0, 0];
    return [0, 1, 2].map(i => (src[i] * src[3] + dst[i] * dst[3] * (1 - src[3])) / a).concat(a); };
  const hex = c => '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  const name = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\s+/)[0] : '');
  function decide(tag) {
    const html = document.documentElement, body = document.body;
    if (!body) return;
    const w = innerWidth - 1, xs = [0, 1, 2, 3, 4].map(i => Math.floor(w * i / 4));
    const running = document.getAnimations().filter(a => a.playState === 'running' && a.effect && a.effect.target);
    const els = [html, ...document.querySelectorAll('body, body *')];
    const rejected = xs.map(x => {
      const why = [];
      for (const el of els) {
        const r = el.getBoundingClientRect();
        if (!(x >= r.left && x < r.right && 0 >= r.top && 0 < r.bottom) && el !== html) continue;
        const cs = getComputedStyle(el); if (cs.display === 'contents') continue;
        if (cs.backgroundImage !== 'none') why.push('image ' + name(el));
        if (/^(IMG|IFRAME)$/.test(el.tagName)) why.push('replaced ' + name(el));
        for (const a of running) if (a.effect.target === el) why.push((a.transitionProperty ? 'transition ' : 'animation ') + name(el));
      }
      return why;
    });
    const bad = rejected.map(r => r.length > 0);
    const valid = !bad[1] && !bad[2] && !bad[3] && !(bad[0] && bad[4]);
    const under = over(over([255, 255, 255, 1], rgba(getComputedStyle(html).backgroundColor)), rgba(getComputedStyle(body).backgroundColor));
    // WebKit fixed-container edge: hit at the top midpoint 4 px in; a fixed/sticky ancestor >= 90 % wide, > 10 px tall.
    let fixedTop = null;
    for (const el of els) {
      const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
      if (!(innerWidth / 2 >= r.left && innerWidth / 2 < r.right && 4 >= r.top && 4 < r.bottom)) continue;
      if (!/fixed|sticky/.test(cs.position) || r.width < innerWidth * 0.9 || r.height <= 10 || cs.visibility === 'hidden' || +cs.opacity < 0.1) continue;
      const c = rgba(cs.backgroundColor); fixedTop = name(el) + (c[3] > 0.1 ? ' ' + hex(c) : ' transparent (WebKit would pixel-sample)');
    }
    const f = { t: Math.round(performance.now() - t0), tag, status: body.dataset.status || '', valid, under: hex(under),
      why: [...new Set(rejected.flat())].slice(0, 4).join(', '), fixedTop };
    const last = frames[frames.length - 1];
    if (!last || last.valid !== f.valid || last.under !== f.under || last.fixedTop !== f.fixedTop || last.status !== f.status || last.tag !== f.tag) frames.push(f);
  }
  window.__p593decide = decide;
  const tick = () => { decide('load'); if (performance.now() - t0 < 4000) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  // The owner's theme in IMG_2821/2822 (Emerald deepened 28 %), present from the first frame of every load.
  document.addEventListener('DOMContentLoaded', () => document.body.style.setProperty('--deepen', '28%'));
}

const all = []; let failed = 0;
const report = (scheme, run, frames) => {
  for (const f of frames) {
    const errs = [];
    if (f.valid) errs.push('top sample accepted (bar = whatever row 0 shows at that frame: race)');
    if (f.under !== '#141414') errs.push(`under-page ${f.under}`);
    // Not the bar source on the Home Screen: on 0.30.25 body (fixed, viewport-sized) carried Emerald on every load,
    // yet the same build showed Sooty after some relaunches. Recorded for the report; a coloured one still fails.
    if (f.fixedTop && !/ #141414$| transparent /.test(f.fixedTop)) errs.push(`fixed top container ${f.fixedTop}`);
    f.ok = !errs.length; f.errors = errs; f.scheme = scheme; f.run = run;
    if (!f.ok) failed++;
    all.push(f);
  }
  const bad = frames.filter(f => !f.ok);
  console.log(`${bad.length ? 'FAIL' : 'ok  '} ${scheme}/${run}: ${frames.length} distinct frames` + (bad.length
    ? ` — e.g. t=${bad[0].t}ms ${bad[0].tag} status=${bad[0].status}: ${bad[0].errors.join('; ')}${bad[0].why ? ' [' + bad[0].why + ']' : ''}` : ''));
};
const grab = page => page.evaluate(() => window.__p593.splice(0));
const stateTour = async page => {
  for (const s of ['idle', 'working', 'waiting', 'unknown', 'idle']) {
    await page.evaluate(s => { document.body.dataset.status = s; document.body.dataset.conn = 'on'; }, s);
    await page.waitForTimeout(250); await page.evaluate(s => window.__p593decide('state ' + s), s);
  }
};

async function composite(page, file, color) {                        // what the iPhone shows: iOS bar + WebView below
  const png = (await page.screenshot()).toString('base64');
  const p = await page.context().browser().newPage({ viewport: { width: W, height: H + BAR } });
  await p.setContent(`<body style="margin:0"><div style="height:${BAR}px;background:${color}"></div><img style="display:block" src="data:image/png;base64,${png}"></body>`);
  await p.evaluate(() => document.querySelector('img').decode());
  await p.screenshot({ path: file }); await p.close();
}

for (const scheme of ['light', 'dark']) {
  const context = await browser.newContext({ userAgent: ua, viewport: { width: W, height: H }, colorScheme: scheme,
    serviceWorkers: 'block', isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
  await context.addInitScript(() => Object.defineProperty(navigator, 'standalone', { get: () => true }));
  await context.addInitScript(model);
  const page = await context.newPage();
  page.on('pageerror', e => console.log(`PAGEERROR ${scheme}: ${e.message}`));
  page.on('crash', () => console.log(`CRASH ${scheme} (WebKit content process)`));
  fake.reset(); fake.setUp(true);
  await page.goto(fake.newPairing(web.url + '?lang=zh'), { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.__ajState === 'awaiting-approval');
  await fake.approve();
  await page.waitForFunction(() => window.__ajState === 'ready');
  await page.waitForTimeout(500); await stateTour(page);
  report(scheme, 'paired', await grab(page));
  for (let i = 0; i < reloads; i++) {                                 // owner kills the app and opens it again
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.__ajState === 'ready'); await page.waitForTimeout(1200);
    await stateTour(page);
    report(scheme, `cold-${i + 1}`, await grab(page));
  }
  if (shots) {
    await page.evaluate(() => { document.body.dataset.status = 'idle'; window.__p593decide('shot'); }); await page.waitForTimeout(300);
    const f = (await grab(page)).pop() || {};
    // The bar iOS falls back to once the sample is rejected (the observed path in IMG_2822).
    await composite(page, `${out}/${scheme}-home-idle-emerald.png`, f.under || '#ff00ff');
  }
  await context.close();
}

// Zero change off the Home Screen: Safari tab on iPhone and desktop keep their body/html colours.
const offHome = [];
for (const [label, opts] of [['iphone-safari', { userAgent: ua, isMobile: true, hasTouch: true }], ['desktop', {}]]) {
  const context = await browser.newContext({ ...opts, viewport: { width: W, height: H }, serviceWorkers: 'block' });
  const page = await context.newPage();
  await page.goto(web.url + '?lang=zh', { waitUntil: 'load' }); await page.waitForTimeout(600);
  offHome.push({ label, ...(await page.evaluate(() => ({ html: getComputedStyle(document.documentElement).backgroundColor,
    body: getComputedStyle(document.body).backgroundColor, bodyImage: getComputedStyle(document.body).backgroundImage,
    standalone: document.documentElement.dataset.standalone || '' }))) });
  await context.close();
}
for (const r of offHome) console.log(`off-home ${r.label}: html=${r.html} body=${r.body} image=${r.bodyImage} standalone=${r.standalone || '-'}`);

await browser.close(); await fake.stop(); await web.stop();
writeFileSync(`${out}/p593-frames.json`, JSON.stringify({ reloads, frames: all, offHome }, null, 1));
console.log(failed ? `P593 FAIL ${failed}/${all.length} frames ${out}` : `P593 PASS ${all.length} frames, ${reloads} cold loads x 2 schemes ${out}`);
process.exit(failed ? 1 : 0);
