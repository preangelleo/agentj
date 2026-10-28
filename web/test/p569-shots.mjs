// P569 (#370) · before / after shots of every surface family whose left accent bar changed: Chromium + WebKit, zh + en,
// 390 light / 390 dark / 1440 light, cropped around the surface at 2× so the corner curve is visible:
//   notices        the reply's notice stack — rect notification, share notice, 10-minute approval bar, running command
//   quote-bar      the composer's quote row (one quote)
//   quote-chips    two quotes side by side (chips)
//   sent-quote     the sent message's quote block over its words
//   reply-quote    a markdown quote inside a reply
//   settings       Settings' side nav, current section (1440 only — the side nav exists from 900 px)
//   dashboard      the account dashboard's callouts / status lines / pinned reply / side nav (no language, run once)
// Paired with the fake host (fakehost.mjs); the same calls drive the base tree (before).
//   node web/test/p569-shots.mjs <public dir> <before|after> <out dir> [dashboard public dir]
//   node web/test/p569-shots.mjs --compare <out dir>      → compare-<surface>.png (before | after, one row per view)
import { createRequire } from 'node:module';
import { mkdirSync, readdirSync, readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { boot, quote, unquote, notices, clearNotices, sentQuote, openSettings, dashboardServer, HERE } from './p569_scene.mjs';

const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const chromiumOpts = { executablePath: process.env.CHROMIUM || '/usr/bin/chromium' };
const ENGINES = ['chromium', 'webkit'], LANGS = ['zh', 'en'];
const VIEWS = [[390, 844, 'light'], [390, 844, 'dark'], [1440, 900, 'light']];
const SURFACES = ['notices', 'quote-bar', 'quote-chips', 'sent-quote', 'reply-quote', 'settings', 'dashboard'];

if (process.argv[2] === '--compare') await compare(process.argv[3]);
else await shoot(...process.argv.slice(2));
process.exit(0);

async function shoot(pub, tag, out, dashDir = resolve(pub, '../../dashboard/public')) {
  mkdirSync(out, { recursive: true });
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: pub });
  const base = web.url.replace('127.0.0.1', 'localhost').replace(/\/$/, '');
  const fake = await startFakeHost();
  const dash = await dashboardServer(dashDir);
  const files = [];
  // the union of the selectors' boxes, 16 px around, inside the viewport
  const crop = async (page, sels, name, view) => {
    const box = await page.evaluate((sels) => {
      const rs = sels.flatMap((s) => [...document.querySelectorAll(s)]).map((e) => e.getBoundingClientRect()).filter((r) => r.width && r.height);
      if (!rs.length) return null;
      const x = Math.max(0, Math.min(...rs.map((r) => r.left)) - 16), y = Math.max(0, Math.min(...rs.map((r) => r.top)) - 16);
      const w = Math.min(innerWidth, Math.max(...rs.map((r) => r.right)) + 16) - x, h = Math.min(innerHeight, Math.max(...rs.map((r) => r.bottom)) + 16) - y;
      return { x, y, width: Math.min(w, 760), height: h };
    }, sels);
    if (!box) { console.log('no box', name, view); return; }
    const f = `${out}/${name}--${view}--${tag}.png`;
    await page.screenshot({ path: f, clip: box }); files.push(f);
  };
  try {
    for (const engine of ENGINES) {
      const browser = await pw[engine].launch(engine === 'chromium' ? chromiumOpts : {});
      try {
        for (const lang of LANGS) for (const [w, h, scheme] of VIEWS) {
          const view = `${engine}-${lang}-${w}-${scheme}`;
          const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, hasTouch: w < 900, deviceScaleFactor: 2 });
          const page = await ctx.newPage();
          try {
            await boot(page, fake, base, lang, scheme);
            await crop(page, ['#words blockquote'], 'reply-quote', view);
            await quote(page, 1); await page.waitForTimeout(250); await crop(page, ['#qbar'], 'quote-bar', view);
            await unquote(page); await quote(page, 2); await page.waitForTimeout(250); await crop(page, ['#qbar'], 'quote-chips', view); await unquote(page);
            await sentQuote(page, lang); await crop(page, ['#omQuote', '#omQuoteFold'], 'sent-quote', view);
            await notices(page, lang, 'info'); await page.waitForTimeout(300);
            await crop(page, ['#toast', '#share-notice', '#p569-grant', '#p569-run'], 'notices', view);
            await clearNotices(page);
            if (w >= 900) { await openSettings(page); await crop(page, ['#settings .setside'], 'settings', view); }
          } catch (e) { console.log('FAIL', view, e.message); }
          finally { await ctx.close(); }
        }
        for (const [w, h, scheme] of VIEWS) {
          const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, deviceScaleFactor: 2 });
          const page = await ctx.newPage();
          try {
            await page.goto(dash.url + '/p569.html'); await page.waitForTimeout(300);
            await crop(page, ['#d-msg', '#d-msgerr', '#d-warn', '#d-sentence', '#d-admin', '#d-pinned', '#d-approve', '#d-side'], 'dashboard', `${engine}-xx-${w}-${scheme}`);
          } finally { await ctx.close(); }
        }
      } finally { await browser.close(); }
    }
  } finally { await web.stop?.(); await fake.stop(); await dash.stop(); }
  console.log(files.length + ' shots →', out);
}

async function compare(dir) {
  const browser = await pw.chromium.launch(chromiumOpts);
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  const names = readdirSync(dir);
  for (const s of SURFACES) {
    const views = [...new Set(names.filter((n) => n.startsWith(s + '--') && n.endsWith('--before.png')).map((n) => n.split('--')[1]))].sort();
    if (!views.length) continue;
    const img = (f) => existsSync(f) ? `<img src="data:image/png;base64,${readFileSync(f).toString('base64')}">` : '<i>missing</i>';
    const rows = views.map((v) => `<tr><th>${v}</th><td>${img(`${dir}/${s}--${v}--before.png`)}</td><td>${img(`${dir}/${s}--${v}--after.png`)}</td></tr>`).join('');
    await page.setContent(`<!doctype html><style>body{margin:0;background:#faf9f5;color:#141414;font:14px/1.3 sans-serif}table{border-collapse:collapse}
      th,td{padding:8px;border:1px solid #c8c6bd;vertical-align:top;text-align:left}th{font-weight:600;white-space:nowrap}img{display:block}</style>
      <table><tr><th>${s}</th><th>before (fdeac15eb)</th><th>after (P569)</th></tr>${rows}</table>`);
    await page.screenshot({ path: `${dir}/compare-${s}.png`, fullPage: true });
    console.log(`${dir}/compare-${s}.png`);
  }
  await browser.close();
}
