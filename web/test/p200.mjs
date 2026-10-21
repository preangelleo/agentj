#!/usr/bin/env node
// P200 (Leo, 2026-10-13, Android Chrome 0.20.0a1): an empty composer showed a vertical scrollbar. P150 paints every
// scrollbar (track included), so a field overflowing by 1–3 px — the placeholder's line box, or one line in a CJK fallback
// font whose ascent + descent beat line-height (Android's Noto CJK) — drew a fixed bar with nothing typed.
// P251/Leo19:10: no visible scrollbar, including overflowing input; scrollability and the original 30 vh cap remain. Real page + local fake host, three engines:
//   Android Chrome  — Chromium, Pixel 7 profile (mobile, touch, DPR 2.625), the system Chromium binary, scrollbars drawn
//   desktop Chrome  — Chromium 1280 × 900, scrollbars drawn
//   iOS Safari      — Playwright WebKit, iPhone 13 profile (overlay bars: checked on overflow-y + scrollability)
// The "tall line" is reproduced by giving the placeholder and the text 3 px of extra line box (what Android's fallback
// font does); without the fix the empty field draws the 8 px bar (bar = offsetWidth − clientWidth).
// Paths: empty → 3 lines → cleared (×) → long text (bar) → Send (cleared, new page) → Settings open / closed →
// keyboard up (viewport half as tall, the same text now overflows) / down → draft restored → 追加 3: type size × system
// font × zoom matrix and a landscape keyboard-up viewport (one line never scrolls). Shots → reports/qa/p200/.
//   node web/test/p200.mjs            [--tree <dir> --tag <name>: run against another checkout]
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { userInfo } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { parityRecorder } from '../../parity/lib.mjs';

const require = createRequire(import.meta.url);
// P417: e2e_parity runs every suite under a throw-away HOME, where Playwright's default browser cache (~/.cache/ms-playwright)
// is empty → WebKit "Executable doesn't exist". Use the owner's installed cache, as the gate PHASE_ENV does for *-webkit.
if (!process.env.PLAYWRIGHT_BROWSERS_PATH) {
  const own = join(userInfo().homedir, '.cache/ms-playwright');
  if (existsSync(own)) { process.env.PLAYWRIGHT_BROWSERS_PATH = own; process.env.PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS ||= '1'; }
}
const { chromium, webkit, devices } = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const ti = process.argv.indexOf('--tree');
const TREE = ti > 0 ? process.argv[ti + 1].replace(/\/?$/, '/') : ROOT;
const TAG = ti > 0 ? (process.argv[process.argv.indexOf('--tag') + 1] || 'other') : 'candidate';
const { startWebServer } = await import(pathToFileURL(TREE + 'web/test/serve.mjs').href);
const { startFakeHost } = await import(pathToFileURL(TREE + 'web/test/fakehost.mjs').href);
const OUT = ROOT + 'reports/qa/p200/';
mkdirSync(OUT, { recursive: true });
const CHROMIUM = process.env.CHROMIUM || '/usr/bin/chromium';

const TALL = `#input,#input::placeholder{padding-bottom:3px}`;   // the Android line box: 3 px taller than line-height
const M = () => { const i = document.getElementById('input'); return { v: i.value.length, bar: i.offsetWidth - i.clientWidth,
  ov: getComputedStyle(i).overflowY, sh: i.scrollHeight, ch: i.clientHeight, top: i.scrollTop, h: i.offsetHeight, ovf: i.classList.contains('ovf') }; };
const setText = (p, v) => p.evaluate((v) => { const i = document.getElementById('input'); i.value = v; i.dispatchEvent(new Event('input')); }, v);
const lines = (n) => Array.from({ length: n }, (_, k) => `第 ${k + 1} 行：把索引重建一下再导出`).join('\n');

const profiles = [
  { name: 'android-chrome', engine: chromium, launch: { executablePath: CHROMIUM, ignoreDefaultArgs: ['--hide-scrollbars'] }, ctx: { ...devices['Pixel 7'] }, drawn: true },
  { name: 'desktop-chrome', engine: chromium, launch: { executablePath: CHROMIUM, ignoreDefaultArgs: ['--hide-scrollbars'] }, ctx: { viewport: { width: 1280, height: 900 } }, drawn: true },
  { name: 'ios-safari', engine: webkit, launch: {}, ctx: { ...devices['iPhone 13'] }, drawn: false },
];

const res = { scope: 'real page + local fake host; Chromium (system binary) and Playwright WebKit; not a physical phone', tree: TAG, profiles: {} };
let failed = 0;
// P417: features.json names p200.mjs#3 lines typed — a case passes only when it passed in every profile (fail sticks)
const rec = parityRecorder('web/test/p200.mjs');
for (const pr of profiles) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const browser = await pr.engine.launch(pr.launch);
  const out = res.profiles[pr.name] = { checks: [] };
  const C = async (n, fn) => { try { const d = await fn(); out.checks.push({ n, ok: true, d }); rec.record(n, true); console.log(`PASS ${pr.name} ${n}`); }
    catch (e) { failed++; out.checks.push({ n, ok: false, err: String(e.message || e) }); rec.record(n, false); console.log(`FAIL ${pr.name} ${n}: ${e.message || e}`); } };
  // No bar unless the words outgrow the cap: hidden overflow, nothing drawn, back at the top when empty.
  const noBar = async (p, what) => { const m = await p.evaluate(M);
    if (m.ov !== 'hidden' || m.bar !== 0 || m.ovf) throw new Error(`${what}: bar shown ${JSON.stringify(m)}`);
    if (!m.v && m.top !== 0) throw new Error(`${what}: empty field not at the top ${JSON.stringify(m)}`); return m; };
  const bar = async (p, what) => { const m = await p.evaluate(M);
    if (m.ov !== 'auto' || !m.ovf || m.sh <= m.ch || (m.bar !== 0 || await p.evaluate(() => getComputedStyle(document.getElementById("input")).scrollbarWidth !== "none"))) throw new Error(`${what}: no bar for overflowing text ${JSON.stringify(m)}`); return m; };
  // the matrix's CDP metrics override can turn Chromium's bars into overlay ones (0 px wide): the scroll state is checked
  const ovfOn = async (p, what) => { const m = await p.evaluate(M);
    if (m.ov !== 'auto' || !m.ovf || m.sh <= m.ch || m.bar !== 0) throw new Error(`${what}: not scrollable for overflowing text ${JSON.stringify(m)}`); return m; };
  const shot = async (p, tag) => { const box = await p.locator('.field').boundingBox(); const vp = p.viewportSize();
    const y = Math.max(0, box.y - 120); await p.screenshot({ path: `${OUT}${pr.name}-${tag}.png`, clip: { x: 0, y, width: vp.width, height: Math.min(vp.height - y, box.height + 160) } }); };
  try {
    const ctx = await browser.newContext({ ...pr.ctx, locale: 'zh-CN' });
    if (pr.engine === webkit) await ctx.addInitScript(() => { window.Audio = undefined;
      for (const k of ['play', 'pause', 'load']) HTMLMediaElement.prototype[k] = function () { return k === 'play' ? Promise.resolve() : undefined; }; });
    const p = await ctx.newPage();
    const errors = []; p.on('pageerror', (e) => errors.push(String(e)));
    await p.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost') + '?lang=zh'));
    await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 15000 });
    await fake.approve();
    await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 15000 });
    await p.evaluate(() => document.getElementById('phone-env-dismiss')?.click());
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ-Father' });
    await sleep(400);
    await C('empty field (plain fonts)', () => noBar(p, 'empty'));
    await p.addStyleTag({ content: TALL }); await p.evaluate(() => dispatchEvent(new Event('resize'))); await sleep(200);
    await C('empty field, line box 3 px taller (Android CJK)', async () => { const m = await noBar(p, 'empty/tall'); await shot(p, '1-empty'); return m; });
    // P412 (#174): the box is one line for good — two lines or more scroll inside it (no bar drawn, 「N 行」 on the full-screen button)
    await C('3 lines typed', async () => { await setText(p, lines(3)); await sleep(80); return ovfOn(p, '3 lines'); });
    await C('cleared with ×', async () => { await p.evaluate(() => document.getElementById('clr').click()); await sleep(120); return noBar(p, 'cleared'); });
    await C('long text → bar (only now)', async () => { await setText(p, lines(40)); await sleep(80);
      await p.evaluate(() => { const i = document.getElementById('input'); i.scrollTop = i.scrollHeight; }); const m = await bar(p, 'long'); await shot(p, '2-long'); return m; });
    await C('Send → cleared, new page, no bar', async () => {
      await p.waitForFunction(() => !document.getElementById('send').disabled, null, { timeout: 5000 });
      await p.evaluate(() => document.getElementById('send').click());
      await p.waitForFunction(() => document.getElementById('input').value === '', null, { timeout: 8000 }); await sleep(300);
      const m = await noBar(p, 'after send'); await shot(p, '3-after-send'); return m; });
    await C('Settings open → closed', async () => {
      await setText(p, lines(40)); await sleep(80); await setText(p, ''); await sleep(50);
      await p.evaluate(() => document.getElementById('setBtn').click()); await sleep(400);
      await p.evaluate(() => document.getElementById('setBtn').click()); await sleep(400);
      return noBar(p, 'back from settings'); });
    await C('keyboard up: the same 6 short lines overflow a shorter viewport', async () => {
      const vp = p.viewportSize(); out.vp = vp;
      await setText(p, Array.from({ length: 6 }, (_, k) => `第 ${k + 1} 行`).join('\n')); await sleep(80); const before = await ovfOn(p, 'tall viewport');
      await p.setViewportSize({ width: vp.width, height: Math.round(vp.height * 0.5) }); await sleep(400);
      const up = await bar(p, 'keyboard up');
      await p.setViewportSize(vp); await sleep(400);
      const down = await ovfOn(p, 'keyboard down');
      await setText(p, ''); await sleep(80); await noBar(p, 'emptied');
      return { before, up, down }; });
    await C('draft restored', async () => {
      await setText(p, lines(2)); await sleep(900);                                            // draftSoon() saves after 600 ms
      await p.evaluate(() => { document.getElementById('input').value = ''; });               // no event: the draft stays
      await p.evaluate(async () => (await import('./js/relay.js')).restoreDraft());
      await p.waitForFunction(() => document.getElementById('input').value.length > 0, null, { timeout: 5000 });
      await sleep(100); const m = await ovfOn(p, 'restored'); await setText(p, ''); await sleep(80); await noBar(p, 'restored→cleared'); return m; });
    // 追加 3 (Leo, 09:1x): smallest type made it show. Rule: empty or one line → never a bar, whatever the page's type size
    // (data-fz s/m/l), Android's system font size (min 0.85 / default / max 1.3: the text's own size scaled, its px padding
    // not — what Chrome's text scaling does) or the browser zoom (50 / 100 / 200 %: CSS viewport ÷ zoom, DPR × zoom on
    // Chromium); only text taller than the cap gets one. Plus a landscape phone with the keyboard up (30 vh < one line).
    await C('追加 3: type size × system font × zoom matrix', async () => {
      const vp = p.viewportSize(), dpr = pr.ctx.deviceScaleFactor || 1, cdp = pr.engine === chromium ? await ctx.newCDPSession(p) : null;
      const rows = [];
      for (const fz of ['s', 'm', 'l']) for (const k of [0.85, 1, 1.3]) for (const z of [0.5, 1, 2]) {
        const w = Math.round(vp.width / z), h = Math.round(vp.height / z);
        if (cdp) await cdp.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: dpr * z, mobile: !!pr.ctx.isMobile });
        else await p.setViewportSize({ width: w, height: h });
        await p.evaluate(([fz, k]) => { document.documentElement.dataset.fz = fz; let st = document.getElementById('p200k');
          if (!st) { st = document.createElement('style'); st.id = 'p200k'; document.head.appendChild(st); }
          st.textContent = `#input{font-size:calc(var(--ta-fs) * ${k})!important}#input::placeholder{font-size:calc(13.6px * var(--fz) * ${k})!important}`;
          dispatchEvent(new Event('resize')); }, [fz, k]);
        await sleep(120);
        const tag = `fz=${fz} font×${k} zoom ${z * 100}%`;
        await setText(p, ''); await sleep(40); const e = await noBar(p, `${tag} empty`);
        await setText(p, '好'); await sleep(40); await noBar(p, `${tag} one line`);   // one glyph: never folds, even at 195 px wide
        // two lines: no bar while they fit the cap; past it (zoom 200 % on a phone: 30 vh ≈ 126 px) the bar is right
        await setText(p, '第一行\n第二行'); await sleep(40);
        const two = await p.evaluate(() => { const i = document.getElementById('input'); i.style.height = 'auto'; const sh = i.scrollHeight;
          i.dispatchEvent(new Event('input')); return sh > innerHeight * 0.3 + 1; });
        void two; await ovfOn(p, `${tag} two lines`);   // P412: two lines always scroll inside the one-line box
        await setText(p, lines(60)); await sleep(40); await ovfOn(p, `${tag} long`);
        await setText(p, ''); await sleep(40); await noBar(p, `${tag} emptied`);
        rows.push({ tag, emptyH: e.h });
      }
      if (cdp) await cdp.send('Emulation.clearDeviceMetricsOverride');
      await p.setViewportSize(vp); await p.evaluate(() => { delete document.documentElement.dataset.fz; document.getElementById('p200k')?.remove(); dispatchEvent(new Event('resize')); });
      await sleep(200); await shot(p, '4-matrix-reset'); return { combos: rows.length }; });
    await C('追加 3: landscape + keyboard up (30 vh under one line): one line has no bar', async () => {
      const vp = p.viewportSize(); await p.setViewportSize({ width: Math.max(vp.width, vp.height), height: 120 }); await sleep(300);
      await setText(p, '一行字'); await sleep(80); const m = await noBar(p, 'landscape one line');
      await setText(p, lines(8)); await sleep(80); await bar(p, 'landscape long');
      await setText(p, ''); await sleep(80); await noBar(p, 'landscape emptied');
      await p.setViewportSize(vp); await sleep(300); return m; });
    await C('no page errors', async () => { const left = errors.filter((e) => !/WebSocket|interactive-widget/.test(e)); if (left.length) throw new Error(left.join(' | ')); });
  } catch (e) { failed++; out.checks.push({ n: 'run', ok: false, err: String(e.stack || e) }); console.log(`FAIL ${pr.name}: ${e.stack || e}`); }
  finally { await browser.close(); await fake.stop(); await web.stop(); }
}
res.complete = !failed;
rec.write();
writeFileSync(`${OUT}results-${TAG}.json`, JSON.stringify(res, null, 2) + '\n');
console.log(failed ? `p200: ${failed} failed` : 'p200: all passed');
process.exit(failed ? 1 : 0);
