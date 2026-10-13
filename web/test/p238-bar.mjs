#!/usr/bin/env node
// P238 (RESUME, owner 2026-10-13 23:32 + 23:44) · the reply card's bottom row: one inset, one baseline, never out of the card.
// Real page + local fake host (contacts + Telegram caps, so all seven icons show), own headless Chromium (never :9222).
//   a) row-fits-every-width   280 / 320 / 360 / 393 / 412 px phones (touch) and a 1440 desktop: every visible button of the row
//                             lies inside the card; targets ≥ 40 px; at 360 px and up nothing folds (shrunk toward 40 px there),
//                             at 393 px and up the targets are the full 44 px
//   b) one-inset-one-baseline the 分享成网页报告 disc's left edge on the text inset (= main's padding), its bottom edge on the
//                             same line as every glyph's bottom edge (= --rmbar-base above the card's edge), 回复's glyph ending
//                             the same inset from the right — light and dark, zh and en
//   c) narrow-folds-into-more at 280 px the secondary icons (邮件, then 阅读, then 转发) fold into 「⋯」; it opens a popover inside
//                             the card listing them with their own labels; a row does what its button does (阅读 → the reader);
//                             a tap outside / Esc closes it; widen the window → everything is back, 「⋯」 gone
// Shots → reports/qa/p238/bar/{after|before}-<lang>-<theme>-<w>.png. P238_BEFORE=<public dir> shoots the old page instead
// (`git archive p240-integration-0203 web/public`) and checks nothing.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, tap, key } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p238/bar/';
mkdirSync(OUT, { recursive: true });
const BEFORE = process.env.P238_BEFORE || '';
const rec = BEFORE ? { record() {}, write() {} } : parityRecorder('web/test/p238-bar.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(BEFORE ? { publicDir: BEFORE } : {}) });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const $ = (id) => `document.getElementById(${JSON.stringify(id)})`;
const REPLY = { zh: '三件事：定价、招聘、发布日期。下周一前把定价表发给财务。', en: 'Three things: pricing, hiring, the launch date. Pricing goes to finance by Monday.' };

async function paired({ w = 390, h = 844, lang = 'zh', scheme = 'light' } = {}) {
  fake.reset();
  fake.st.caps = ['contacts', 'tgfwd'];
  fake.st.tgfwd = true;
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'ct_list') return false;
    await send({ t: 'ct_res', r: m.r, ok: true, contacts: [], count: 0, me: ['owner@example.com'], mail: { ready: true } });
    return true;
  };
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 600 });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `(()=>{${$('phone-env-dismiss')}?.click(); const n=${$('confirm-no')}; if(n&&!${$('confirm')}.hidden)n.click(); const a=${$('a2hs-ok')}; if(a&&!${$('a2hs')}.hidden)a.click();})()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '早上的会说了什么' : 'What did the morning meeting decide?' }, REPLY[lang]);
  await waitFor(p, `!${$('mailBtn')}.hidden && !${$('webReportBtn')}.disabled`);
  await sleep(250);
  return p;
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };
// every visible row button, the card, the text inset, the disc and every visible glyph
const GEOM = `(() => {
  const R = (e) => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height }; };
  const rm = R(${$('rm')}), bar = ${$('rmbar')};
  const btns = [...bar.children].filter((b) => !b.hidden && b.getClientRects().length).map((b) => ({ id: b.id, ...R(b) }));
  const glyphs = [...bar.querySelectorAll('.act')].filter((b) => !b.hidden && b.getClientRects().length).map((b) => {
    const g = [...b.querySelectorAll('svg.g')].find((x) => getComputedStyle(x).display !== 'none'); return { id: b.id, ...R(g) }; });
  const disc = R(${$('webReportBtn')}.querySelector('svg'));
  const inset = parseFloat(getComputedStyle(document.querySelector('.rm main')).paddingLeft);
  const folded = ['mailBtn', 'readBtn', 'fwdBtn'].filter((id) => document.getElementById(id).classList.contains('folded'));   // fold order
  return { rm, btns, glyphs, disc, inset, folded, more: !${$('rmMoreBtn')}.hidden, vw: innerWidth, docX: document.documentElement.scrollWidth > innerWidth };
})()`;
const WIDTHS = [[280, 653], [320, 690], [360, 780], [393, 852], [412, 915], [1440, 900]];

if (BEFORE) {
  for (const [w, h] of WIDTHS) for (const scheme of ['light', 'dark']) {
    const p = await paired({ w, h, scheme });
    writeFileSync(`${OUT}before-zh-${scheme}-${w}.png`, await shoot(p, ''));
    await p.dispose();
  }
  await B.close(); await fake.stop(); await web.stop();
  console.log('P238 bar: before shots written');
  process.exit(0);
}

await C('row-fits-every-width', async () => {
  for (const [w, h] of WIDTHS) for (const scheme of ['light', 'dark']) {
    const p = await paired({ w, h, scheme });
    const g = await ev(p, GEOM);
    const tag = `${w} ${scheme}: ${JSON.stringify(g)}`;
    for (const b of g.btns) {
      assert.ok(b.l >= g.rm.l - 0.5 && b.r <= g.rm.r + 0.5 && b.b <= g.rm.b + 0.5, `${b.id} inside the card — ${tag}`);
      assert.ok(b.w >= 40 - 0.5 && b.h >= 40 - 0.5, `${b.id} ≥ 40 px — ${tag}`);
    }
    for (let i = 1; i < g.btns.length; i++) assert.ok(g.btns[i].l >= g.btns[i - 1].r - 0.5, `no overlap ${g.btns[i].id} — ${tag}`);
    assert.equal(g.docX, false, `no sideways page — ${tag}`);
    if (w >= 360) {
      assert.deepEqual(g.folded, [], `nothing folds — ${tag}`);
      assert.equal(g.more, false);
      assert.equal(g.btns.length, 7, `all seven — ${tag}`);
      if (w >= 393) for (const b of g.btns) assert.ok(Math.abs(b.w - 44) < 0.5, `44 px at ${w} — ${tag}`);
    }
    if (w <= 320) assert.ok(g.folded.length >= 1 && g.more, `narrow: something folds — ${tag}`);
    writeFileSync(`${OUT}after-zh-${scheme}-${w}.png`, await shoot(p, ''));
    noProblems(p);
    await p.dispose();
  }
});

await C('one-inset-one-baseline', async () => {
  for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const p = await paired({ w, h, lang, scheme });
    const g = await ev(p, GEOM);
    const tag = `${lang} ${scheme} ${w}: ${JSON.stringify({ disc: g.disc, rm: g.rm, inset: g.inset, glyphs: g.glyphs })}`;
    const left = g.disc.l - g.rm.l, bottom = g.rm.b - g.disc.b;
    assert.ok(Math.abs(left - g.inset) <= 1, `disc on the text inset (${left} vs ${g.inset}) — ${tag}`);
    for (const x of g.glyphs) assert.ok(Math.abs((g.rm.b - x.b) - bottom) <= 1, `${x.id} on the disc's baseline (${g.rm.b - x.b} vs ${bottom}) — ${tag}`);
    assert.ok(bottom >= 14 && bottom <= 18, `baseline 16 px above the edge (${bottom}) — ${tag}`);
    const reply = g.glyphs.find((x) => x.id === 'replyBtn');
    assert.ok(Math.abs((g.rm.r - reply.r) - g.inset) <= 1.5, `回复 ends one inset from the right (${g.rm.r - reply.r}) — ${tag}`);
    if (lang === 'zh') writeFileSync(`${OUT}after-baseline-${scheme}-${w}.png`, await shoot(p, ''));
    noProblems(p);
    await p.dispose();
  }
});

await C('narrow-folds-into-more', async () => {
  for (const lang of ['zh', 'en']) {
    const p = await paired({ w: 280, h: 653, lang });
    let g = await ev(p, GEOM);
    assert.deepEqual(g.folded.slice(0, 1), ['mailBtn'], 'mail folds first: ' + g.folded);
    const order = ['mailBtn', 'readBtn', 'fwdBtn'];
    assert.deepEqual(g.folded, order.slice(0, g.folded.length), 'fold order');
    const m = await ev(p, `(() => { const r = ${$('rmMoreBtn')}.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
    await tap(p, m.x, m.y);
    await waitFor(p, `!${$('rmMore')}.hidden`);
    const pop = await ev(p, `(() => { const r = ${$('rmMore')}.getBoundingClientRect(), c = ${$('rm')}.getBoundingClientRect();
      return { rows: [...${$('rmMore')}.querySelectorAll('button')].map((b) => [b.dataset.for, b.textContent, b.getBoundingClientRect().height]),
        inside: r.left >= c.left - 0.5 && r.right <= c.right + 0.5 && r.top >= c.top, exp: ${$('rmMoreBtn')}.getAttribute('aria-expanded') }; })()`);
    assert.deepEqual(pop.rows.map((r) => r[0]), g.folded, JSON.stringify(pop));
    for (const [id, text, hh] of pop.rows) {
      assert.equal(text, await ev(p, `${$(id)}.getAttribute('aria-label')`), 'its own label');
      assert.ok(hh >= 44, 'row ≥ 44 px');
    }
    assert.ok(pop.inside, 'the popover stays inside the card');
    assert.equal(pop.exp, 'true');
    writeFileSync(`${OUT}after-more-open-${lang}-280.png`, await shoot(p, ''));
    // a tap outside closes; Esc closes
    await tap(p, 140, 120);
    await waitFor(p, `${$('rmMore')}.hidden`);
    await tap(p, m.x, m.y);
    await waitFor(p, `!${$('rmMore')}.hidden`);
    await key(p, 'Escape');
    await waitFor(p, `${$('rmMore')}.hidden`);
    // 阅读 through 「⋯」 opens the reader, like the button
    if (g.folded.includes('readBtn')) {
      await tap(p, m.x, m.y);
      await waitFor(p, `!${$('rmMore')}.hidden`);
      const r = await ev(p, `(() => { const b = ${$('rmMore')}.querySelector('[data-for="readBtn"]').getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()`);
      await tap(p, r.x, r.y);
      await waitFor(p, `${$('rmMore')}.hidden && !${$('rd')}.hidden`, 3000);
      await key(p, 'Escape');
      await waitFor(p, `${$('rd')}.hidden`, 3000);
    }
    // wider → all back
    await p.send('Emulation.setDeviceMetricsOverride', { width: 412, height: 915, deviceScaleFactor: 1, mobile: true });
    await sleep(400);
    g = await ev(p, GEOM);
    assert.deepEqual(g.folded, [], 'unfolded at 412');
    assert.equal(g.more, false, '「⋯」 gone at 412');
    noProblems(p);
    await p.dispose();
  }
});

rec.write();
await B.close(); await fake.stop(); await web.stop();
console.log(failed ? `P238 bar: ${failed} case(s) failed` : 'P238 bar: all cases passed');
process.exit(failed ? 1 : 0);
