#!/usr/bin/env node
// P238 (RESUME, owner 2026-10-13 23:29) · the 「/」 command list is a card that rises from the 5-hour bar, not a full-screen page.
// Real page + local fake host (own headless Chromium with touch, never :9222).
//   a) geometry, zh/en × light/dark × 390/1440: a card (not full width on a wide screen, ≤ 60 % of the viewport high), its
//      bottom edge just above the 5-hour bar and its label, menu tokens (Sooty card, Lotion text), every description wraps
//      inside the card, nothing scrolls sideways
//   b) a sideways swipe on the card moves nothing: the list does not scroll sideways, the reply cards underneath do not page,
//      the card stays open
//   c) a pull-down at the top of the list closes it; scrolled down, the same pull first scrolls back (stays open), the next
//      pull closes; a short pull snaps back
//   d) tap a command → it lands in the field and the card closes; tap outside (the scrim) / Esc / × → closed
//   e) the ⬆ row follows the version on disk: installed ≠ running (serve not restarted yet) → 「已装好 X…」 and not tappable;
//      running = latest → 「已是最新」; an older host → 「点击即授权升级」 and tappable; a dev build ahead of the release → 已是最新
// Shots → reports/qa/p238/sheet-*.png
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep, swipe, tap, key } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p238/';
mkdirSync(OUT, { recursive: true });
const rec = parityRecorder('web/test/p238-sheet.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const $ = (id) => `document.getElementById(${JSON.stringify(id)})`;
const LONG = '会议总结：附上录音（或用最近一组录音）→ 转写 → 纪要；后面再补一句很长很长的说明，看它会不会在卡片里换行而不是被切掉';
const MENU = (upgrade = { current: '0.20.3a1', latest: '0.20.3a1' }) => ({ source: 'config', cmds: [],
  items: [{ cmd: '/good-morning', desc: '早安例行 / Morning routine', group: '日常' }, { cmd: '/long', desc: LONG, group: '日常' },
    ...Array.from({ length: 14 }, (_, i) => ({ cmd: `/cmd${i}`, desc: `第 ${i} 条命令`, group: '日常' })),
    { cmd: '/goal', desc: '目标模式', group: '命令' }, { cmd: '/switch-harness', desc: '换底层 harness', group: '主 Agent' }],
  skills: [{ cmd: '/review', desc: '' }], upgrade });
const SOOTY = 'rgb(20, 20, 20)', LOTION = 'rgb(250, 249, 245)';

async function paired({ w = 390, h = 844, lang = 'zh', scheme = 'light', menu = MENU() } = {}) {
  fake.reset();
  fake.st.menu = menu;
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await ev(p, `(()=>{${$('phone-env-dismiss')}?.click(); const n=${$('confirm-no')}; if(n&&!${$('confirm')}.hidden)n.click(); const a=${$('a2hs-ok')}; if(a&&!${$('a2hs')}.hidden)a.click();})()`);
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await sleep(200);
  return p;
}
async function C(name, fn) {
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };
async function openMenu(p) {
  await ev(p, `${$('slashBtn')}.click()`);
  await waitFor(p, `!${$('menu')}.hidden`);
  await sleep(450);                                    // the host's answer re-renders the list; the 160 ms rise
}
const isOpen = (p) => ev(p, `!${$('menu')}.hidden`);
const GEOM = `(() => {
  const s = ${$('menuSheet')}.getBoundingClientRect(), bar = ${$('m5h')}.getBoundingClientRect(), lab = ${$('m5h')}.querySelector('.lab').getBoundingClientRect();
  const body = ${$('menuBody')}, cs = getComputedStyle(${$('menuSheet')});
  const clipped = [...document.querySelectorAll('#menuList button span, #menuList button b')].filter((e) => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent.slice(0, 20));
  return { top: s.top, bottom: s.bottom, left: s.left, w: s.width, h: s.height, vw: innerWidth, vh: innerHeight, bar: bar.top, lab: lab.top,
    bg: cs.backgroundColor, color: cs.color, sideways: body.scrollWidth > body.clientWidth, docX: document.documentElement.scrollWidth > innerWidth, clipped,
    scrim: ${$('menu')}.getBoundingClientRect().height };
})()`;

await C('a-card-on-the-5h-bar', async () => {
  for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) {
    const p = await paired({ w, h, lang, scheme });
    await openMenu(p);
    const g = await ev(p, GEOM);
    const tag = `${lang} ${scheme} ${w}: ${JSON.stringify(g)}`;
    assert.ok(g.h <= g.vh * 0.6 + 1, `≤ 60 % high — ${tag}`);
    assert.ok(g.top > 40, `not full screen — ${tag}`);
    assert.ok(g.bottom <= Math.min(g.bar, g.lab) && Math.min(g.bar, g.lab) - g.bottom <= 12, `sits on the 5-hour bar — ${tag}`);
    if (w > 600) assert.ok(g.w <= 481, `a card, not a page — ${tag}`);
    else assert.ok(g.w <= g.vw - 16 && g.left >= 8, `phone gutters — ${tag}`);
    assert.ok(Math.abs(g.scrim - g.vh) <= 1, `the scrim covers the viewport — ${tag}`);
    assert.equal(g.bg, SOOTY); assert.equal(g.color, LOTION);
    assert.equal(g.sideways, false, `no sideways scroll in the list — ${tag}`);
    assert.equal(g.docX, false);
    assert.deepEqual(g.clipped, [], `every line wraps inside the card — ${tag}`);
    writeFileSync(`${OUT}sheet-${lang}-${w}-${scheme}.png`, await shoot(p, ''));
    noProblems(p);
    await p.dispose();
  }
});

await C('sideways-swipe-moves-nothing', async () => {
  const p = await paired();
  await openMenu(p);
  const before = await ev(p, `[${$('deck')}.style.transform, ${$('menuBody')}.scrollLeft]`);
  const g = await ev(p, GEOM);
  const y = g.top + g.h / 2;
  await swipe(p, g.left + g.w - 30, y, g.left + 30, y + 4, 10, 200);
  await sleep(300);
  await swipe(p, g.left + 30, y, g.left + g.w - 30, y - 4, 10, 200);
  await sleep(300);
  assert.equal(await isOpen(p), true, 'still open');
  assert.deepEqual(await ev(p, `[${$('deck')}.style.transform, ${$('menuBody')}.scrollLeft]`), before, 'no paging, no sideways scroll');
  assert.equal(await ev(p, `document.getElementById('deck').dataset.swipe || ''`), '', 'the deck never saw a swipe');
  noProblems(p);
  await p.dispose();
});

await C('pull-down-at-the-top-closes', async () => {
  const p = await paired();
  await openMenu(p);
  let g = await ev(p, GEOM);
  // a short pull snaps back
  await swipe(p, g.left + g.w / 2, g.top + 120, g.left + g.w / 2, g.top + 150, 6, 120);
  await sleep(350);
  assert.equal(await isOpen(p), true, 'short pull: still open');
  assert.equal(await ev(p, `${$('menuSheet')}.style.transform`), '', 'snapped back');
  // scrolled down: the first pull scrolls back to the top, the card stays
  await ev(p, `${$('menuBody')}.scrollTop = 400`);
  await sleep(100);
  await swipe(p, g.left + g.w / 2, g.top + 100, g.left + g.w / 2, g.top + 340, 10, 220);
  await sleep(500);
  assert.equal(await isOpen(p), true, 'scrolled: the pull scrolls, does not close');
  await ev(p, `${$('menuBody')}.scrollTop = 0`);       // (emulated touch scrolling stops short of 0 in headless runs)
  await sleep(100);
  // at the top: the pull drags the card away
  await swipe(p, g.left + g.w / 2, g.top + 100, g.left + g.w / 2, g.top + 300, 10, 220);
  await waitFor(p, `${$('menu')}.hidden`, 2000);
  // and from the grab bar
  await openMenu(p);
  g = await ev(p, GEOM);
  await swipe(p, g.left + g.w / 2, g.top + 8, g.left + g.w / 2, g.top + 200, 10, 200);
  await waitFor(p, `${$('menu')}.hidden`, 2000);
  writeFileSync(`${OUT}sheet-after-pull.png`, await shoot(p, ''));
  noProblems(p);
  await p.dispose();
});

await C('tap-fills-the-field-outside-esc-x-close', async () => {
  const p = await paired();
  await openMenu(p);
  await ev(p, `[...document.querySelectorAll('#menuList [data-slash]')].find((b) => b.dataset.slash === '/good-morning').scrollIntoView({block: 'center'})`);
  await sleep(100);
  const r = await ev(p, `(() => { const q = [...document.querySelectorAll('#menuList [data-slash]')].find((b) => b.dataset.slash === '/good-morning').getBoundingClientRect(); return {x: q.x + q.width / 2, y: q.y + q.height / 2}; })()`);
  await tap(p, r.x, r.y);
  await waitFor(p, `${$('menu')}.hidden`, 2000);
  assert.equal(await ev(p, `${$('input')}.value`), '/good-morning ');
  // tap outside (the scrim above the card)
  await ev(p, `${$('input')}.value = ''`);
  await openMenu(p);
  const g = await ev(p, GEOM);
  await tap(p, g.left + 20, Math.max(20, g.top - 20));
  await waitFor(p, `${$('menu')}.hidden`, 2000);
  assert.equal(await ev(p, `${$('input')}.value`), '', 'outside tap only closes');
  await openMenu(p);
  await key(p, 'Escape');
  await waitFor(p, `${$('menu')}.hidden`, 2000);
  await openMenu(p);
  await ev(p, `${$('menuClose')}.click()`);
  await waitFor(p, `${$('menu')}.hidden`, 2000);
  noProblems(p);
  await p.dispose();
});

await C('upgrade-row-follows-the-version-on-disk', async () => {
  const cases = [
    [{ current: '0.20.1a1', latest: '0.20.2a1', installed: '0.20.2a1' }, { zh: /^已装好 0\.20\.2a1，电脑空闲时自动重启生效$/, en: /^0\.20\.2a1 is installed/ }, true],
    [{ current: '0.20.2a1', latest: '0.20.2a1' }, { zh: /已是最新$/, en: /Already up to date$/ }, true],
    [{ current: '0.20.1a1', latest: '0.20.2a1' }, { zh: /^0\.20\.1a1 → 0\.20\.2a1 · 点击即授权升级$/, en: /Tap to authorize upgrade$/ }, false],
    [{ current: '0.20.3a1', latest: '0.20.2a1' }, { zh: /已是最新$/, en: /Already up to date$/ }, true],
  ];
  for (const lang of ['zh', 'en']) for (const [i, [upgrade, want, disabled]] of cases.entries()) {
    const p = await paired({ lang, menu: MENU(upgrade) });
    await openMenu(p);
    const row = await ev(p, `(() => { const b = document.querySelector('#menuList [data-run="update"]'); return {desc: b.querySelector('span').textContent, disabled: b.disabled}; })()`);
    assert.match(row.desc, want[lang], `${lang} ${JSON.stringify(upgrade)}`);
    assert.equal(row.disabled, disabled, `${lang} ${JSON.stringify(upgrade)} disabled`);
    if (lang === 'zh' && i === 0) {
      await ev(p, `document.querySelector('#menuList [data-run="update"]').scrollIntoView({block: 'center'})`);
      writeFileSync(`${OUT}sheet-upgrade-installed-waiting-restart.png`, await shoot(p, ''));
    }
    noProblems(p);
    await p.dispose();
  }
});

await C('upgrade-row-trusts-the-live-host-over-a-cached-answer', async () => {
  // Leo 23:31: the page kept the menu answer of the 0.20.1a1 serve; the new serve (0.20.2a1) had been connected for two minutes
  const p = await paired({ menu: MENU({ current: '0.20.1a1', latest: '0.20.2a1' }) });
  await openMenu(p);
  const row = () => ev(p, `(() => { const b = document.querySelector('#menuList [data-run="update"]'); return {desc: b.querySelector('span').textContent, disabled: b.disabled}; })()`);
  assert.match((await row()).desc, /^0\.20\.1a1 → 0\.20\.2a1 · 点击即授权升级$/);
  fake.st.prefSet = true; fake.st.version = '0.20.2a1'; fake.st.prefs = fake.st.prefs || {};
  await fake.send({ t: 'preferences', value: fake.st.prefs, problem: null, host: { version: '0.20.2a1', language_at: 0, settable: [], devices: [] } });
  await waitFor(p, `/已是最新$/.test(document.querySelector('#menuList [data-run="update"] span').textContent)`, 3000);
  assert.equal((await row()).disabled, true, 'not tappable');
  noProblems(p);
  await p.dispose();
});

rec.write();
await B.close(); await fake.stop(); await web.stop();
console.log(failed ? `P238 sheet: ${failed} case(s) failed` : 'P238 sheet: all cases passed');
process.exit(failed ? 1 : 0);
