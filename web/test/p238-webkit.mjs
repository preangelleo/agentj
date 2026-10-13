#!/usr/bin/env node
// P238 on Safari's engine (Playwright WebKit, iPhone 13 profile; not a physical iPhone): the exact answer a 0.20.1a1 host
// sent Leo's phone (shared Claude Code, one own command /good-morning, no /switch-harness) and a fixed 0.20.3 host's answer.
// Both: 「/」 → /switch-harness right after /customize in 一键执行; one tap sends it. The list is a card on the 5-hour bar
// (≤ 60 % high, no sideways scroll); a tap above it closes it. Shots → reports/qa/p238/webkit-*.png
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const require = createRequire(import.meta.url);
const { webkit, devices } = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p238/';
mkdirSync(OUT, { recursive: true });
const GM = { cmd: '/good-morning', desc: '早安例行：夜间体检 + 微信协同 / Morning routine' };
const CASES = {
  'host-0.20.1': { source: 'config', items: [GM], skills: [{ cmd: '/review', desc: '' }], cmds: [], upgrade: { current: '0.20.1a1', latest: '0.20.1a1' } },
  'host-0.20.3': { source: 'config', items: [GM, { cmd: '/goal', desc: '目标模式', group: '命令' },
    { cmd: '/switch-harness', desc: '换底层 harness', group: '主 Agent' }], skills: [], cmds: [], upgrade: { current: '0.20.3a1', latest: '0.20.3a1' } },
};
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const browser = await webkit.launch();
let failed = 0;
for (const [tag, menu] of Object.entries(CASES)) {
  try {
    fake.reset(); fake.st.menu = menu;
    const ctx = await browser.newContext({ ...devices['iPhone 13'] });
    await ctx.addInitScript(() => { window.Audio = undefined; for (const k of ['play', 'pause', 'load']) HTMLMediaElement.prototype[k] = function () { return k === 'play' ? Promise.resolve() : undefined; }; });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(fake.newPairing(web.url.replace('127.0.0.1', 'localhost') + '?lang=zh'));
    await page.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 15000 });
    await fake.approve();
    await page.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 15000 });
    await page.evaluate(() => { for (const id of ['phone-env-dismiss', 'a2hs-ok', 'confirm-no']) { const b = document.getElementById(id); if (b && b.offsetParent) b.click(); } });
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await sleep(300);
    await page.tap('#slashBtn');
    await page.waitForFunction(() => !document.getElementById('menu').hidden && document.getElementById('switchEntry'), null, { timeout: 10000 });
    const rows = await page.evaluate(() => [...document.getElementById('menuList').children].map((n) => n.querySelector('b')?.textContent || n.textContent));
    const i = rows.indexOf('/customize');
    assert.equal(rows[i + 1], '/switch-harness', JSON.stringify(rows));
    assert.equal(rows.filter((r) => r === '/switch-harness').length, 1);
    // RESUME (Leo 23:29): a card rising from the 5-hour bar — not full screen, no sideways scroll; a tap above it closes
    await sleep(300);
    const g = await page.evaluate(() => { const s = document.getElementById('menuSheet').getBoundingClientRect(), b = document.getElementById('m5h').getBoundingClientRect(), body = document.getElementById('menuBody');
      return { top: s.top, bottom: s.bottom, h: s.height, w: s.width, vw: innerWidth, vh: innerHeight, bar: b.top, sideways: body.scrollWidth > body.clientWidth }; });
    assert.ok(g.h <= g.vh * 0.6 + 1 && g.top > 40 && g.w < g.vw && g.bottom <= g.bar && g.bar - g.bottom <= 24 && !g.sideways, JSON.stringify(g));
    if (tag === 'host-0.20.1') {
      writeFileSync(`${OUT}webkit-iphone-sheet.png`, await page.screenshot());
      await page.touchscreen.tap(g.vw / 2, Math.max(10, g.top - 20));
      await page.waitForFunction(() => document.getElementById('menu').hidden, null, { timeout: 5000 });
      await page.tap('#slashBtn');
      await page.waitForFunction(() => !document.getElementById('menu').hidden && document.getElementById('switchEntry'), null, { timeout: 10000 });
    }
    await page.evaluate(() => document.getElementById('switchEntry').scrollIntoView({ block: 'center' }));
    await sleep(200);
    writeFileSync(`${OUT}webkit-iphone-${tag}-menu.png`, await page.screenshot());
    const n0 = fake.st.says.length;
    await page.tap('#switchEntry');
    await sleep(600);
    assert.deepEqual(fake.st.says.slice(n0).map((m) => (m.text || '').trim()), ['/switch-harness']);
    if (tag === 'host-0.20.1') writeFileSync(`${OUT}webkit-iphone-after-tap.png`, await page.screenshot());
    assert.deepEqual(errors, []);
    await ctx.close();
    console.log(`  ✓ webkit ${tag}`);
  } catch (e) { failed++; console.log(`  ✗ webkit ${tag}: ${e && e.stack || e}`); }
}
await browser.close(); await fake.stop(); await web.stop();
console.log(failed ? `P238 WebKit: ${failed} case(s) failed` : 'P238 WebKit: all cases passed');
process.exit(failed ? 1 : 0);
