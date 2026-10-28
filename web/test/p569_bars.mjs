// P569 (#370, chairman 10-28 「把圆边也用那个颜色……整个卡片就显得比较丝滑」): every left accent bar on the phone page
// (and the account dashboard) is the card's own border-left, curving through its rounded top-left / bottom-left corners,
// in exactly the colour the old bar had. Real page + fake host (Noise), Chromium AND WebKit, zh + en, 390 light / 390 dark /
// 1440 light; the dashboard sheet on a static page, both engines, 390 light / dark + 1440.
// Per surface (getComputedStyle): border-left-width > 0, top-left and bottom-left radius > 0, no ::before / ::after strip,
// no inset stripe, and the border colour = the bar colour measured on the base tree (EXPECT below).
//   node web/test/p569_bars.mjs            → prints 「P569 bars: all PASS」, exit 1 on any failure
//   P569_PUBLIC=<public dir> P569_DASH=<dashboard public dir> P569_PROBE=1 node …  → prints the bars as JSON (used once on
//   the base tree to fill EXPECT)
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { boot, quote, unquote, notices, clearNotices, sentQuote, extras, openSettings, probe, sameColour, dashboardServer, DASH, HERE } from './p569_scene.mjs';

const require = createRequire(import.meta.url);
const pw = require(process.env.AJ_PLAYWRIGHT_MODULE || '/tmp/aj-webmcp-playwright/node_modules/playwright');
const PUB = process.env.P569_PUBLIC || resolve(HERE, '../public');
const DASHDIR = process.env.P569_DASH || resolve(HERE, '../../dashboard/public');
const PROBE = !!process.env.P569_PROBE;
const ENGINES = (process.env.P569_ENGINES || 'chromium,webkit').split(',');
const LANGS = ['zh', 'en'];
const VIEWS = [[390, 844, 'light'], [390, 844, 'dark'], [1440, 900, 'light']];

// the old bar colours, measured on the base tree (fdeac15eb) — same in both engines, both languages, every width
const EXPECT = {
  light: {
    'words-quote': 'rgb(217, 119, 87)',
    'qbar': 'rgb(193, 225, 240)',
    'qc': 'rgb(193, 225, 240)',
    'omq': 'rgba(20, 20, 20, 0.55)',
    'omqfold': 'rgba(20, 20, 20, 0.55)',
    'toast-info': 'rgb(98, 152, 135)',
    'toast-success': 'rgb(98, 152, 135)',
    'toast-error': 'rgb(217, 119, 87)',
    'share-notice': 'rgb(98, 152, 135)',
    'elev-grant': 'rgb(98, 152, 135)',
    'elev-run': 'rgb(98, 152, 135)',
    'elev-run-ask': 'rgb(217, 119, 87)',
    'elev-run-ok': 'rgb(98, 152, 135)',
    'elev-run-fail': 'rgb(162, 59, 42)',
    'danger-cmd': 'rgb(250, 249, 245)',
    'setup-act': 'rgb(98, 152, 135)',
    'fp-mention': 'rgb(113, 187, 222)',
    'fp-quote': 'rgb(113, 187, 222)',
    'jc-mark': 'rgb(217, 119, 87)',
    'ct-fresh': 'rgb(98, 152, 135)',
    'term-origin': 'rgb(113, 187, 222)',
    'term-warn': 'rgb(247, 130, 126)',
    'sug-selected': 'rgb(113, 187, 222)',
    'rsmode': 'rgb(247, 130, 126)',
    'dashd-msg': 'rgb(98, 152, 135)',
    'dashd-msgerr': 'rgb(247, 130, 126)',
    'dashd-pinned': 'rgb(98, 152, 135)',
    'dashd-approve': 'rgb(98, 152, 135)',
    'fr-mention': 'rgb(20, 20, 20)',
    'setside': 'rgb(113, 187, 222)',
    'dashd-warn': 'rgb(106, 155, 204)',
    'dashd-info': 'rgb(106, 155, 204)',
    'dashd-pending': 'rgb(106, 155, 204)',
    'dashd-sentence': 'rgb(106, 155, 204)',
    'dashd-admin': 'rgb(106, 155, 204)',
    'dashd-side': 'rgb(98, 152, 135)',
  },
  dark: {
    'words-quote': 'rgb(217, 119, 87)',
    'qbar': 'rgb(193, 225, 240)',
    'qc': 'rgb(193, 225, 240)',
    'omq': 'rgba(20, 20, 20, 0.55)',
    'omqfold': 'rgba(20, 20, 20, 0.55)',
    'toast-info': 'rgb(98, 152, 135)',
    'toast-success': 'rgb(98, 152, 135)',
    'toast-error': 'rgb(217, 119, 87)',
    'share-notice': 'rgb(98, 152, 135)',
    'elev-grant': 'rgb(98, 152, 135)',
    'elev-run': 'rgb(98, 152, 135)',
    'elev-run-ask': 'rgb(217, 119, 87)',
    'elev-run-ok': 'rgb(98, 152, 135)',
    'elev-run-fail': 'rgb(162, 59, 42)',
    'danger-cmd': 'rgb(250, 249, 245)',
    'setup-act': 'rgb(98, 152, 135)',
    'fp-mention': 'rgb(113, 187, 222)',
    'fp-quote': 'rgb(113, 187, 222)',
    'jc-mark': 'rgb(217, 119, 87)',
    'ct-fresh': 'rgb(98, 152, 135)',
    'term-origin': 'rgb(113, 187, 222)',
    'term-warn': 'rgb(247, 130, 126)',
    'sug-selected': 'rgb(113, 187, 222)',
    'rsmode': 'rgb(247, 130, 126)',
    'dashd-msg': 'rgb(98, 152, 135)',
    'dashd-msgerr': 'rgb(247, 130, 126)',
    'dashd-pinned': 'rgb(98, 152, 135)',
    'dashd-approve': 'rgb(98, 152, 135)',
    'fr-mention': 'rgb(250, 249, 245)',
    'dashd-warn': 'rgb(113, 187, 222)',
    'dashd-info': 'rgb(113, 187, 222)',
    'dashd-pending': 'rgb(113, 187, 222)',
    'dashd-sentence': 'rgb(113, 187, 222)',
    'dashd-admin': 'rgb(113, 187, 222)',
  },
};

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', publicDir: PUB });
const base = web.url.replace('127.0.0.1', 'localhost').replace(/\/$/, '');
const fake = await startFakeHost();
const dash = await dashboardServer(DASHDIR);
const seen = [], fails = [];
let checks = 0;

function judge(engine, lang, w, scheme, name, b) {
  const key = `${engine} ${lang} ${w} ${scheme} ${name}`;
  seen.push({ engine, lang, w, scheme, name, ...b });
  if (PROBE) return;
  const exp = EXPECT[scheme][name];
  const bad = [];
  if (b.missing) bad.push('missing');
  else {
    if (exp === undefined) bad.push('no expected colour');
    if (b.technique !== 'border') bad.push('technique ' + b.technique);
    if (!(b.blw > 0)) bad.push('border-left-width ' + b.blw);
    if (!(b.tl > 0) || !(b.bl > 0)) bad.push(`left radii ${b.tl}/${b.bl}`);
    if (b.pseudo) bad.push('pseudo-element strip');
    if (/inset/.test(b.shadow)) bad.push('inset shadow ' + b.shadow);
    if (exp && !sameColour(b.blc, exp)) bad.push(`colour ${b.blc} ≠ before ${exp}`);
  }
  checks++;
  if (bad.length) { fails.push(key + ': ' + bad.join('; ')); console.log('FAIL', key, bad.join('; ')); }
}

try {
  for (const engine of ENGINES) {
    const browser = await pw[engine].launch(engine === 'chromium' ? { executablePath: process.env.CHROMIUM || '/usr/bin/chromium' } : {});
    try {
      for (const lang of LANGS) for (const [w, h, scheme] of VIEWS) {
        const mobile = w < 900;
        const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, hasTouch: mobile, deviceScaleFactor: 1 });
        const page = await ctx.newPage();
        const errors = []; page.on('pageerror', (e) => errors.push(String(e)));
        const m = async (name, sel) => judge(engine, lang, w, scheme, name, await probe(page, sel));
        try {
          await boot(page, fake, base, lang, scheme);
          await m('words-quote', '#words blockquote');
          await quote(page, 1); await m('qbar', '#qbar');
          await unquote(page); await quote(page, 2); await m('qc', '#qbar .qc'); await unquote(page);
          await sentQuote(page, lang); await m('omq', '#omQuote'); await m('omqfold', '#omQuoteFold');
          for (const kind of ['info', 'success', 'error']) { await notices(page, lang, kind); await m('toast-' + kind, '#toast'); }
          await m('share-notice', '#share-notice'); await m('elev-grant', '#p569-grant');
          for (const [st, ok, name] of [['run', null, 'elev-run'], ['ask', null, 'elev-run-ask'], ['done', '1', 'elev-run-ok'], ['done', '0', 'elev-run-fail']]) {
            await page.evaluate(([st, ok]) => { const r = document.getElementById('p569-run'); r.dataset.state = st; if (ok) r.dataset.ok = ok; else delete r.dataset.ok; }, [st, ok]);
            await m(name, '#p569-run');
          }
          await clearNotices(page);
          await extras(page, lang); await page.waitForTimeout(300);   // terminal.css loads with the sheet
          for (const [name, sel] of [['danger-cmd', '#p569-cmd'], ['setup-act', '#p569-setup-act'], ['fr-mention', '#p569-frm'], ['fp-mention', '#p569-fpm'],
            ['fp-quote', '#p569-fpq'], ['jc-mark', '#p569-jci'], ['ct-fresh', '#p569-ctf'], ['term-origin', '#p569-tor'], ['term-warn', '#p569-twa'],
            ['sug-selected', '#p569-sug1'], ['rsmode', '#p569-rsmode']]) await m(name, sel);
          if (!mobile) { await openSettings(page); await m('setside', '#settings .setside button[aria-current="true"]'); }
          if (errors.length) { fails.push(`${engine} ${lang} ${w} ${scheme}: page errors ${errors.join(' | ')}`); console.log('FAIL page errors', errors); }
        } catch (e) {
          fails.push(`${engine} ${lang} ${w} ${scheme}: ${e.message}`); console.log('FAIL', engine, lang, w, scheme, e.message);
        } finally { await ctx.close(); }
      }
      // the account dashboard's own sheet (no language-dependent styling: run once per view)
      for (const [w, h, scheme] of VIEWS) {
        const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, deviceScaleFactor: 1 });
        const page = await ctx.newPage();
        try {
          await page.goto(dash.url + '/p569.html'); await page.waitForTimeout(200);
          for (const sel of DASH) {
            if (sel === '#d-side' && w < 1024) continue;     // the side nav's current mark exists from 1024 px
            judge(engine, '-', w, scheme, 'dash' + sel.slice(1), await probe(page, sel));
          }
        } finally { await ctx.close(); }
      }
    } finally { await browser.close(); }
  }
} finally {
  await web.stop?.(); await fake.stop(); await dash.stop();
}

if (PROBE) {
  const table = {};
  for (const s of seen) {
    const k = s.scheme + '|' + s.name;
    (table[k] ||= new Set()).add(s.missing ? 'MISSING' : `${s.technique}:${s.colour}`);
  }
  for (const [k, v] of Object.entries(table)) console.log(k, [...v].join('  ||  '));
  process.exit(fails.length ? 1 : 0);
}
console.log(`P569 bars: ${checks} surfaces×views checked, ${fails.length} failed`);
if (fails.length) { for (const f of fails) console.log(' -', f); process.exit(1); }
console.log('P569 bars: all PASS');
process.exit(0);
