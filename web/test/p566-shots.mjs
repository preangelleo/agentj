// P566 · before / after shots of every visible change, Chromium + WebKit (Playwright), 390 wide, light + dark, zh:
//   1 keycard-refused   the Agent J host refused a key card's answer — the reason on the card + a notice above the card's scrim
//   2 keycard-expired   a key card at 0 — gone, with 「这张卡已过期，请让 Agent 重发」 (before: stays, 「时间到」, 保存 still enabled)
//   3 settings-pk       「本机 Touch ID：已设好」 — 「测试一下」 · 「重新设置」 under it (before: no button at all)
//   4 pkp-save-hover    the 「已批准 · 点「保存」」 dialog after a tap on 保存 (touch hover stays) — light theme was Noir on Noir
// Paired with the fake host (fakehost.mjs); host messages and module calls drive the states, so the same script shoots the
// 0.25.18a1 tree (before).
//   node web/test/p566-shots.mjs <public dir> <before|after> <out dir>
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';

const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const [pub, tag, out] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: pub });
const base = web.url.replace('127.0.0.1', 'localhost').replace(/\/$/, '');
const fake = await startFakeHost();
const hex = (n) => Array.from({ length: n }, (_, i) => ((i * 7 + 3) % 16).toString(16)).join('');
const results = [];

for (const engine of ['chromium', 'webkit']) {
  const browser = await pw[engine].launch(engine === 'chromium' ? { executablePath: process.env.CHROMIUM || '/usr/bin/chromium' } : {});
  for (const scheme of ['light', 'dark']) {
    let ctx = null, page = null;
    const shot = async (name) => { const f = `${out}/${tag}-${engine}-${scheme}-${name}.png`; await page.screenshot({ path: f }); results.push(f); };
    const fresh = async () => {
      fake.reset();
      if (ctx) await ctx.close();                       // a fresh browser profile: every state starts from a new pairing
      ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: scheme, hasTouch: true, deviceScaleFactor: 1 });
      page = await ctx.newPage();
      await page.goto(fake.newPairing(base + '/?lang=zh'));
      await page.waitForFunction(() => window.__ajState === 'awaiting-approval', null, { timeout: 15000 });
      await fake.approve();
      await page.waitForFunction(() => window.__ajState === 'ready', null, { timeout: 15000 });
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await page.waitForTimeout(500);
    };
    const card = (ttl) => ({ t: 'elev', id: hex(32), kind: 'secret', n: hex(32).split('').reverse().join(''), epk: 'A'.repeat(43), ttl,
      name: 'OPENAI_API_KEY', purpose: '给剪辑工作流调用模型', dest: '~/.claude/settings.json', verify: '', fa: 'Y3JlZC1pZC0wMDAwMDAwMQ', fa_s: 'local' });

    // 1 a refused answer
    await fresh();
    await fake.send(card(120));
    await page.waitForTimeout(400);
    await page.fill('#elev-input', 'sk-' + 'example-not-a-key');
    await page.evaluate(async (id) => { const e = await import('./js/elevate.js'); e.refused({ t: 'elev_refused', id, why: 'stale' }); }, card(120).id);
    await page.waitForTimeout(400);
    await shot('keycard-refused');

    // 2 a card at 0
    await fresh();
    await fake.send(card(2));
    await page.waitForTimeout(3300);
    await shot('keycard-expired');

    // 3 Settings: 「本机 Touch ID：已设好」
    await fresh();
    await fake.send({ t: 'pk_state', s: 'local', fa: 'Y3JlZC1pZC0wMDAwMDAwMQ', fa_ch: 'A'.repeat(43), n: 'B'.repeat(22) });
    await page.waitForTimeout(300);
    await page.evaluate(() => document.getElementById('setBtn').click());
    await page.waitForTimeout(500);
    await page.evaluate(() => document.getElementById('set-pk-row').scrollIntoView({ block: 'center' }));
    await page.waitForTimeout(500);
    await shot('settings-pk');

    // 4 the approved dialog after a tap on 保存 (the hover stays on a touch screen)
    await fresh();
    await page.evaluate(async () => {
      const m = await import('./js/pkpeer.js');
      m.configure({ regOpts: async () => ({}), ua: () => navigator.userAgent, touch: () => true });
      await m.onOffer({ n: 'AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM', peer: 'AAAAAAAAAAAAAAAAAAAAAA' });
    });
    await page.waitForTimeout(400);
    await page.hover('#pkp-save');
    await page.waitForTimeout(500);
    const c = await page.evaluate(() => { const s = getComputedStyle(document.getElementById('pkp-save')); return s.backgroundColor + ' / ' + s.color; });
    console.log(tag, engine, scheme, 'pkp-save hover', c);
    await shot('pkp-save-hover');
    await ctx.close();
  }
  await browser.close();
}
await web.stop?.(); await fake.stop();
console.log(results.join('\n'));
process.exit(0);
