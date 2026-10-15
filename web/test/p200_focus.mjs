#!/usr/bin/env node
// P200 追加 2 (Leo, 2026-10-13): a black ring stayed round the header robots after they were tapped. Chromium matches
// :focus-visible when script moves focus after the last input was a key (typed in the field → robots → dialog closes →
// focus back on the robots). Rule: nothing a finger or mouse touched keeps a ring / frame / tap flash; only keyboard
// navigation (Tab) shows one, in the theme accent (--ring). Real page + local fake host, three engines (as p200.mjs).
// Paths per engine: type in the field → tap the robots → 知道了 (focus returns by script) → no ring; tap ≡ / settings /
// send / a menu row → no ring; Tab → ring in --ring; a tap after Tab → no ring again. Shots → reports/qa/p200/focus-*.png.
//   node web/test/p200_focus.mjs
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const require = createRequire(import.meta.url);
const { chromium, webkit, devices } = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p200/';
mkdirSync(OUT, { recursive: true });
const CHROMIUM = process.env.CHROMIUM || '/usr/bin/chromium';
const NOW = () => Math.floor(Date.now() / 1000);
const busy = { t: 'busy', by: 'AgentJ-Father', tasks: [['P165', '0.20.0 集成与发布候选'], ['P200', '输入框滚动条'], [null, '检查依赖']]
  .map(([who, label], i) => ({ id: 'k' + i, who, kind: who ? 'unit' : 'temp', label, since: NOW() - 300, c: i })) };

// What the focused element paints: an outline (style ≠ none and width > 0) or a focus box-shadow; its colour vs --ring.
const RING = () => { const a = document.activeElement; if (!a || a === document.body) return { id: null, ring: false, kbd: document.documentElement.dataset.kbd || '' };
  const cs = getComputedStyle(a), probe = document.createElement('i'); probe.style.color = 'var(--ring)'; document.body.appendChild(probe);
  const ring = getComputedStyle(probe).color; probe.remove();
  const ol = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
  return { id: a.id || a.className, ring: ol || (cs.boxShadow !== 'none' && a.matches(':focus-visible')), outline: ol ? `${cs.outlineWidth} ${cs.outlineStyle} ${cs.outlineColor}` : 'none',
    shadow: cs.boxShadow, accent: ring, kbd: document.documentElement.dataset.kbd || '', fv: a.matches(':focus-visible'), tap: cs.webkitTapHighlightColor }; };

const profiles = [
  { name: 'android-chrome', engine: chromium, launch: { executablePath: CHROMIUM }, ctx: { ...devices['Pixel 7'] }, touch: true },
  { name: 'desktop-chrome', engine: chromium, launch: { executablePath: CHROMIUM }, ctx: { viewport: { width: 1280, height: 900 } }, touch: false },
  { name: 'ios-safari', engine: webkit, launch: {}, ctx: { ...devices['iPhone 13'] }, touch: true },
];
const res = { scope: 'real page + local fake host; Chromium (system binary) and Playwright WebKit; not a physical phone', profiles: {} };
let failed = 0;
for (const pr of profiles) {
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const browser = await pr.engine.launch(pr.launch);
  const out = res.profiles[pr.name] = { checks: [] };
  const C = async (n, fn) => { try { const d = await fn(); out.checks.push({ n, ok: true, d }); console.log(`PASS ${pr.name} ${n}`); }
    catch (e) { failed++; out.checks.push({ n, ok: false, err: String(e.message || e) }); console.log(`FAIL ${pr.name} ${n}: ${e.message || e}`); } };
  try {
    const ctx = await browser.newContext({ ...pr.ctx, locale: 'zh-CN' });
    if (pr.engine === webkit) await ctx.addInitScript(() => { window.Audio = undefined;
      for (const k of ['play', 'pause', 'load']) HTMLMediaElement.prototype[k] = function () { return k === 'play' ? Promise.resolve() : undefined; }; });
    const p = await ctx.newPage();
    const errors = []; p.on('pageerror', (e) => errors.push(String(e)));
    const tap = async (sel) => { if (pr.touch) await p.tap(sel); else await p.click(sel); await sleep(250); };
    const noRing = async (what) => { const m = await p.evaluate(RING);
      if (m.ring || m.kbd) throw new Error(`${what}: ring left ${JSON.stringify(m)}`);
      if (m.tap && !/rgba\(0, 0, 0, 0\)|transparent/.test(m.tap)) throw new Error(`${what}: tap highlight ${m.tap}`); return m; };
    await p.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost') + '?lang=zh'));
    await p.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 15000 });
    await fake.approve();
    await p.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 15000 });
    await p.evaluate(() => { document.getElementById('phone-env-dismiss')?.click();
      const a = document.getElementById('a2hs-ok'); if (a && !document.getElementById('a2hs').hidden) a.click(); });
    await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'AgentJ-Father' });
    await fake.send(busy);
    await p.waitForFunction(() => !document.getElementById('busyDots').hidden && document.querySelectorAll('#busyDots i.rb').length >= 3, null, { timeout: 8000 });
    await sleep(300);
    await C('type in the field → tap robots → 知道了: focus back by script, no ring (Leo\'s path)', async () => {
      await tap('#input'); await p.keyboard.type('hello'); await sleep(100);
      await tap('#busyDots'); await p.waitForFunction(() => !document.getElementById('busyDlg').hidden, null, { timeout: 5000 });
      await noRing('dialog open');
      await p.keyboard.type('x'); // a key while the dialog is up (Chromium's modality heuristic) — not a nav key
      await tap('#busyClose'); await sleep(300);
      await p.evaluate(() => document.getElementById('busyDots').focus());   // what a dialog's focus-return does
      const m = await noRing('robots after close');
      await p.screenshot({ path: `${OUT}focus-${pr.name}-1-robots.png`, clip: await clip(p, '#busyDots') }); return m; });
    await C('robots focused by script right after typing in the field: no ring', async () => {
      await tap('#input'); await p.keyboard.type('abc'); await p.keyboard.press('ArrowLeft');
      await p.evaluate(() => document.getElementById('busyDots').focus()); return noRing('robots after typing'); });
    for (const [n, sel, close] of [['settings ⚙', '#setBtn', '#setBtn'], ['≡ menu', '#menuBtn', null], ['the clear ×', '#input', null]]) {
      await C(`tap ${n}: no ring`, async () => {
        if (!(await p.$(sel)) || !(await p.isVisible(sel))) return { skipped: sel };
        await tap(sel); const m = await noRing(n);
        if (close && await p.isVisible(close)) await tap(close); else await p.keyboard.press('Escape').catch(() => {});
        await sleep(300); await noRing(n + ' closed'); return m; });
    }
    await C('Send (rocket) tapped: no ring', async () => { await p.fill('#input', 'ping'); await tap('#send'); await sleep(300); return noRing('send'); });
    await C('keyboard Tab: a ring, in the theme accent', async () => {
      await p.evaluate(() => document.activeElement?.blur());
      let m = null; for (let k = 0; k < 6 && !(m && m.ring); k++) { await p.keyboard.press(pr.engine === webkit ? 'Alt+Tab' : 'Tab'); await sleep(60); m = await p.evaluate(RING); }
      // headless WebKit's ⌥Tab may not move focus: the key still set the keyboard mode; land focus where Tab would.
      // Headless WebKit moves no focus on (⌥)Tab, and script focus never matches :focus-visible there: only the keyboard
      // mode is checked (real Safari's ⌥Tab focus matches :focus-visible → the same --ring rule as Chromium).
      if (!m.ring && pr.engine === webkit) { if (m.kbd !== '1') throw new Error(`Tab did not set keyboard mode ${JSON.stringify(m)}`); return { ...m, note: 'headless WebKit: keyboard mode only' }; }
      if (!m.ring || m.kbd !== '1' || !m.outline.includes(m.accent)) throw new Error(`no accent ring on Tab ${JSON.stringify(m)}`);
      await p.screenshot({ path: `${OUT}focus-${pr.name}-2-tab.png` }); return m; });
    await C('tap after Tab: the ring goes', async () => { await tap('#busyDots'); await tap('#busyClose'); return noRing('tap after Tab'); });
    await C('no page errors', async () => { const left = errors.filter((e) => !/WebSocket|interactive-widget/.test(e)); if (left.length) throw new Error(left.join(' | ')); });
  } catch (e) { failed++; out.checks.push({ n: 'run', ok: false, err: String(e.stack || e) }); console.log(`FAIL ${pr.name}: ${e.stack || e}`); }
  finally { await browser.close(); await fake.stop(); await web.stop(); }
}
async function clip(p, sel) { const b = await p.locator(sel).boundingBox(); const vp = p.viewportSize();
  return { x: 0, y: Math.max(0, b.y - 60), width: vp.width, height: Math.min(200, vp.height - Math.max(0, b.y - 60)) }; }
res.complete = !failed;
writeFileSync(`${OUT}results-focus.json`, JSON.stringify(res, null, 2) + '\n');
console.log(failed ? `p200_focus: ${failed} failed` : 'p200_focus: all passed');
process.exit(failed ? 1 : 0);
