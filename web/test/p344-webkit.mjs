// P344: the shortcut sheet's search on Safari's engine (Playwright WebKit, iPhone 13 profile), local fake host; not physical
// iPhone acceptance. Settings → 键盘快捷键 → tap the magnifier → type → rows filter; no match line; tap again restores.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
const { webkit, devices } = createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const out = new URL('../../reports/qa/p344/', import.meta.url); mkdirSync(out, { recursive: true });
const rec = parityRecorder('web/test/p344-webkit.mjs');
let web, fake, browser, ok = false;
try {
  web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }); fake = await startFakeHost(); browser = await webkit.launch();
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  // as P264: headless Linux WebKit's media stack crashes the page on the UI sounds; stand-ins, the page code is unchanged
  await ctx.addInitScript(() => { window.Audio = undefined; for (const k of ['play', 'pause', 'load']) HTMLMediaElement.prototype[k] = function () { return k === 'play' ? Promise.resolve() : undefined; }; });
  const page = await ctx.newPage();
  const errors = []; page.on('pageerror', (e) => errors.push(String(e))); page.on('crash', () => console.error('webkit page crashed'));
  await page.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost')));
  await page.waitForFunction(() => window.__ajState === 'awaiting-approval'); await fake.approve(); await page.waitForFunction(() => window.__ajState === 'ready');
  await page.evaluate(() => { const a = document.getElementById('a2hs'); if (a && !a.hidden) document.getElementById('a2hs-ok').click(); });
  await page.locator('#setBtn').click();
  await page.waitForFunction(() => !document.getElementById('settings').hidden);
  await page.evaluate(() => document.getElementById('keysBtn').click());   // the row sits in a long list; the tap under test is the magnifier
  await page.waitForFunction(() => !document.getElementById('keys').hidden);
  const rows = () => page.locator('#keysCols dt').count();
  const all = await rows();
  assert.ok(all > 40, 'full sheet');
  const btn = await page.locator('#keysFindBtn').boundingBox();
  assert.ok(btn.width >= 44 && btn.height >= 44, '44 px magnifier');
  await page.locator('#keysFindBtn').tap();
  assert.equal(await page.locator('#keysFind').isVisible(), true, 'field opens');
  await page.locator('#keysFindInput').fill('截取');
  const hit = await rows();
  assert.ok(hit > 0 && hit < all, `filters (${hit}/${all})`);
  assert.ok(await page.locator('#keysCols dt[data-ix="app.screenshot"]').count() > 0, 'finds the screenshot key');
  await page.locator('#keysFindInput').fill('Capture THIS');
  assert.ok(await page.locator('#keysCols dt[data-ix="app.screenshot"]').count() > 0, 'English, any case');
  await page.screenshot({ path: new URL('webkit-iphone13-search.png', out).pathname });
  await page.locator('#keysFindInput').fill('zzqx');
  assert.equal(await rows(), 0); assert.equal(await page.locator('#keysNone').isVisible(), true, 'no-match line');
  await page.locator('#keysFindBtn').tap();
  assert.equal(await page.locator('#keysFind').isVisible(), false, 'tap again closes');
  assert.equal(await rows(), all, 'every row back');
  assert.equal(await page.locator('#keys').isVisible(), true, 'sheet stays');
  assert.deepEqual(errors, [], 'no page errors');
  ok = true;
  await ctx.close(); console.log('P344 WebKit PASS');
} finally {
  rec.record('keys-search-webkit', ok); rec.write();
  await Promise.allSettled([browser?.close(), fake?.stop(), web?.stop()]);
}
