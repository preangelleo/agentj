#!/usr/bin/env node
// P187 · one menu design system: every menu-type surface is a full-screen dark page (P299: Noir canvas, Sooty panels, Lotion
// text) built from two layers of tokens — `--ui-*` (global, inherits brand/palette.css) and `--menu-*` (inherits global).
// Real page + local fake host, own headless Chromium (never :9222).
//   a) every menu surface (P238: except the command list, a card on the 5-hour bar) covers the whole viewport, wears the menu tokens (canvas Noir, text Lotion) in light AND dark
//      themes, and carries the one menu header (title + back / close icon buttons)
//   b) WCAG AA: every visible text node inside a menu ≥ 4.5:1 against the colour it actually sits on (≥ 3:1 for ≥ 24 px /
//      ≥ 18.66 px bold); icon buttons ≥ 3:1 (non-text)
//   c) one row / button / icon geometry: level-1/2/3 rows at the menu heights, icon buttons 44 px, icons 22 px, no tap < 44
//   d) Esc = the header's back / close: shortcuts, silent records and the security page return to Settings when they were
//      opened from there; a level-3 page (memory, records, tasks, models, bots) returns to Settings
//   e) a theme's `colors.menu` repaints the menus (theme.js), the factory look comes back on reset
// Shots (zh/en × light/dark × 1440/390) → reports/qa/p187/screens/*.png; AJ_P187_SHOTS_ONLY=1 = shots, no assertions.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + (process.env.AJ_P187_OUT || 'reports/qa/p187/screens/');
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P187_ONLY || '').split(',').filter(Boolean);
const SHOTS_ONLY = process.env.AJ_P187_SHOTS_ONLY === '1';
const rec = parityRecorder('web/test/p187.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
const NOW = Math.floor(Date.now() / 1000);
const DEVICES = () => [
  { id: 'ME', name: 'iPhone Air', paired_at: NOW - 86400 * 12, seen: NOW, online: true, source: 'local', me: true },
  { id: 'Kx3pQ9wLm2Vb7Tz1', name: 'MacBook · Chrome', paired_at: NOW - 86400 * 30, seen: NOW - 3600, online: true, source: 'account' },
  { id: 'Rf8sN2cHq5Yd4Jk6', name: 'Pixel 9', paired_at: NOW - 86400 * 5, seen: NOW - 86400 * 2, online: false, source: 'local' },
];
const PREFS = { appearance: { language: 'zh', theme: 'system' }, voice: { speak_replies: true, wake_enabled: false }, agent: { high_risk_warnings: false, session_mode: 'shared' } };

async function paired({ w = 1440, h = 900, scheme = 'light', lang = 'zh' } = {}) {
  fake.reset();
  fake.st.prefs = structuredClone(PREFS);
  fake.st.prefs.appearance.language = lang;
  fake.remotes({ limit: 10, items: DEVICES(), link: 'https://m.agentj.app/#p=' + '2'.repeat(240), qr: '', code: '482913', silent: false });
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await sleep(200);
  return p;
}
async function C(name, fn) {
  if (ONLY.length && !ONLY.includes(name)) return;
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };
const shown = (id) => `(() => { const e = document.getElementById(${JSON.stringify(id)}); return !!e && !e.hidden && e.getClientRects().length > 0; })()`;
async function openSettings(p) { if (!(await ev(p, shown('settings')))) await click(p, '#setBtn'); await waitFor(p, shown('settings')); await sleep(120); }
async function toChat(p) {
  for (let i = 0; i < 6 && await ev(p, `document.body.dataset.view !== 'chat' || !document.getElementById('settings').hidden || [...document.querySelectorAll('.menupage, .cmdmenu')].some((e) => !e.hidden)`); i++) { await key(p, 'Escape'); await sleep(80); }
}

// Every menu surface: how to open it from the chat page, and the element that is the page.
const SURFACES = [
  { id: 'settings', level: 1, el: '#settings', open: async (p) => openSettings(p) },
  { id: 'commands', level: 1, el: '#menuSheet', sheet: true, open: async (p) => { await click(p, '#slashBtn'); await waitFor(p, shown('menu')); } },
  { id: 'remotes', level: 2, el: '#settings', open: async (p) => { await openSettings(p); await click(p, '#set-remotes-open'); await waitFor(p, shown('set-remotes')); await sleep(200); } },
  { id: 'keys', level: 2, el: '#keys', open: async (p) => { await openSettings(p); await click(p, '#keysBtn'); await waitFor(p, shown('keys')); } },
  { id: 'silent', level: 2, el: '#silent', open: async (p) => { await click(p, '#slashBtn'); await waitFor(p, shown('menu')); await click(p, '#silentEntry'); await waitFor(p, shown('silent')); } },
  { id: 'about', level: 2, el: '#badge-panel', open: async (p) => { await openSettings(p); await click(p, '#menu-about'); await waitFor(p, shown('badge-panel')); } },
  { id: 'memory', level: 2, el: 'body', view: 'mem', open: async (p) => { await openSettings(p); await click(p, '#open-mem'); await waitFor(p, `document.body.dataset.view === 'mem'`); await sleep(250); } },
  { id: 'records', level: 2, el: 'body', view: 'act', open: async (p) => { await openSettings(p); await click(p, '#open-act'); await waitFor(p, `document.body.dataset.view === 'act'`); await sleep(250); } },
  { id: 'tasks', level: 2, el: 'body', view: 'tasks', open: async (p) => { await openSettings(p); await click(p, '#open-tasks'); await waitFor(p, `document.body.dataset.view === 'tasks'`); await sleep(250); } },
  { id: 'models', level: 2, el: 'body', view: 'models', open: async (p) => { await openSettings(p); await click(p, '#open-models'); await waitFor(p, `document.body.dataset.view === 'models'`); await sleep(250); } },
];

// WCAG contrast of every visible text inside `root` against the colour under it (walks up to the first opaque
// background, compositing translucent ones), plus icon buttons (non-text, 3:1).
const CONTRAST = (root) => `(() => {
  const root = document.querySelector(${JSON.stringify(root)});
  const parse = (c) => { let m = c.match(/rgba?\\(([^)]+)\\)/), k = 1;
    if (!m) { m = c.match(/color\\(srgb ([^)]+)\\)/); k = 255; } if (!m) return null;
    const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [v[0] * k, v[1] * k, v[2] * k, v.length > 3 ? v[3] : 1]; };
  const over = (top, under) => { const a = top[3]; return [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a)).concat(1); };
  const bgOf = (e) => { const stack = []; for (let n = e; n; n = n.parentElement) { const pill = n === e && n.closest('.segrow') && getComputedStyle(n, '::after').content !== 'none' ? parse(getComputedStyle(n, '::after').backgroundColor) : null; if (pill && pill[3] >= 1) { stack.push(pill); break; } /* P336 × segrow: the pressed segment's pill is its ::after (z-index -1 under the text) */ const c = parse(getComputedStyle(n).backgroundColor); if (c && c[3] > 0) { stack.push(c); if (c[3] >= 1) break; } }
    let col = [255, 255, 255, 1]; for (let i = stack.length - 1; i >= 0; i--) col = over(stack[i], col); return col; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const vis = (e) => e.getClientRects().length && !e.closest('[hidden]') && getComputedStyle(e).visibility !== 'hidden';
  const bad = []; let n = 0, min = 99;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const e = t.parentElement; if (!t.textContent.trim() || !e || !vis(e) || e.closest('svg,[aria-hidden="true"]')) continue;
    const r = e.getBoundingClientRect(); if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
    const cs = getComputedStyle(e); if (parseFloat(cs.opacity) === 0) continue;
    let fg = parse(cs.color); let op = 1; for (let x = e; x && x !== document.body; x = x.parentElement) op *= parseFloat(getComputedStyle(x).opacity);
    const bg = bgOf(e); fg = over([fg[0], fg[1], fg[2], fg[3] * op], bg);
    const k = ratio(fg, bg), size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    if (e.closest(':disabled,[aria-disabled="true"]')) continue;     // WCAG 1.4.3: inactive controls are exempt
    n++; min = Math.min(min, k);
    if (k < need) bad.push(t.textContent.trim().slice(0, 24) + ' ' + k.toFixed(2) + '<' + need);
  }
  for (const b of root.querySelectorAll('.menuicon, .menuhead button')) {
    if (!vis(b) || b.disabled) continue;
    const fg = parse(getComputedStyle(b).color), bg = bgOf(b), k = ratio(over(fg, bg), bg); n++;
    if (k < 3) bad.push('icon ' + (b.id || b.className) + ' ' + k.toFixed(2));
  }
  return { n, min: +min.toFixed(2), bad: bad.slice(0, 12) };
})()`;

// Geometry: what the surface covers, its canvas colour, the header, taps.
const GEOM = (sel) => `(() => {
  const e = document.querySelector(${JSON.stringify(sel)});
  const page = e === document.body ? document.body : e;
  const r = page.getBoundingClientRect();
  const vis = (x) => x.getClientRects().length && !x.closest('[hidden]') && getComputedStyle(x).visibility !== 'hidden';
  const head = [...page.querySelectorAll('.menuhead, .sethead, .sub-head')].find(vis);
  const small = [];
  for (const b of page.querySelectorAll('button, a[href], [role=switch], input:not([type=range])')) {
    if (!vis(b) || b.closest('.setlinks') || b.closest('header.top') && page === document.body) continue;
    const q = b.getBoundingClientRect(); if (q.bottom < 0 || q.top > innerHeight) continue;
    if (q.height < 43.5 || q.width < 43.5) small.push((b.id || b.className || b.tagName) + ' ' + Math.round(q.width) + 'x' + Math.round(q.height));
  }
  const canvas = getComputedStyle(page === document.body ? document.querySelector('.ajpages') : page).backgroundColor;
  return { w: Math.round(r.width), h: Math.round(r.height), vw: innerWidth, vh: innerHeight, canvas, body: getComputedStyle(document.body).backgroundColor,
    head: head ? { h: Math.round(head.getBoundingClientRect().height), icons: [...head.querySelectorAll('button')].filter(vis).map((b) => Math.round(b.getBoundingClientRect().width)) } : null,
    text: getComputedStyle(page).color, small: small.slice(0, 10), overflowX: document.documentElement.scrollWidth > innerWidth };
})()`;

const SOOTY = 'rgb(20, 20, 20)', NOIR = 'rgb(44, 43, 38)', LOTION = 'rgb(250, 249, 245)';

try {
  await C('shots', async () => {
    for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[1440, 900], [390, 844]]) {
      const p = await paired({ w, h, scheme, lang });
      try {
        for (const s of SURFACES) {
          await toChat(p);
          await s.open(p);
          await sleep(180);
          writeFileSync(`${OUT}${s.id}-${w}-${scheme}-${lang}.png`, await shoot(p));
        }
        await toChat(p);
        // the confirm card (global, centred) over Settings
        await openSettings(p);
        await click(p, '#unpair');
        await waitFor(p, shown('confirm'));
        await sleep(200);
        writeFileSync(`${OUT}confirm-${w}-${scheme}-${lang}.png`, await shoot(p));
        await key(p, 'Escape');
      } finally { await p.dispose(); }
    }
  });
  if (!SHOTS_ONLY) {
    await C('fullscreen-dark-all-surfaces', async () => {
      for (const scheme of ['light', 'dark']) for (const [w, h] of [[1440, 900], [390, 844]]) {
        const p = await paired({ w, h, scheme });
        try {
          for (const s of SURFACES) {
            await toChat(p);
            await s.open(p);
            await sleep(260);                               // the 160 ms fade-in of a menu page
            const g = await ev(p, GEOM(s.el));
            const tag = `${s.id} ${w} ${scheme}`;
            if (s.sheet) {                                  // P238: the command list is a card (≤ 60 % high), the rest are pages
              assert.ok(g.w < w && g.h <= h * 0.6 + 1, `${tag} a card ${JSON.stringify(g)}`);
            } else {
              assert.equal(g.w, w, `${tag} full width ${JSON.stringify(g)}`);
              assert.ok(Math.abs(g.h - h) <= 1, `${tag} full height ${JSON.stringify(g)}`);
            }
            assert.equal(g.canvas, NOIR, `${tag} Noir canvas (P299)`);
            assert.equal(g.text, LOTION, `${tag} Lotion text`);
            assert.ok(g.head, `${tag} has the menu header`);
            assert.ok(g.head.h >= (s.sheet ? 52 : 56), `${tag} header height ${g.head.h}`);
            assert.ok(g.head.icons.length >= 1 && g.head.icons.every((x) => x === 44), `${tag} header icon buttons 44 px: ${g.head.icons}`);
            assert.equal(g.overflowX, false, `${tag} no sideways scroll`);
            assert.deepEqual(g.small, [], `${tag} taps < 44 px`);
            const k = await ev(p, CONTRAST(s.el === 'body' ? '.ajpages' : s.el));
            assert.ok(k.n > 3, `${tag}: measured ${k.n}`);
            assert.deepEqual(k.bad, [], `${tag} contrast`);
          }
          noProblems(p);
        } finally { await p.dispose(); }
      }
    });

    await C('one-geometry', async () => {
      const p = await paired({ w: 390, h: 844 });
      try {
        await openSettings(p);
        const m = await ev(p, `(() => {
          const cs = getComputedStyle(document.documentElement);
          const v = (n) => cs.getPropertyValue(n).trim();
          const rows = [...document.querySelectorAll('#set-main .setnav')].filter((e) => !e.hidden).map((e) => Math.round(e.getBoundingClientRect().height));
          const icons = [...document.querySelectorAll('#settings .menuicon svg, #settings svg.mi')].filter((e) => e.getClientRects().length).map((e) => Math.round(e.getBoundingClientRect().width));
          const btns = [...document.querySelectorAll('#settings .aj-btn')].filter((e) => e.getClientRects().length).map((e) => Math.round(e.getBoundingClientRect().height));
          return { row1: v('--menu-row-1'), row2: v('--menu-row-2'), icon: v('--ui-icon'), rows, icons, btns };
        })()`);
        assert.equal(m.row2, '56px');
        assert.equal(m.icon, '22px');
        assert.ok(m.rows.length >= 8 && m.rows.every((x) => x >= 56), `Settings rows ≥ the level-2 row height: ${m.rows}`);
        assert.ok(m.icons.length >= 8 && m.icons.every((x) => x === 22), `one icon size: ${m.icons}`);
        assert.ok(m.btns.every((x) => x === 44), `one button height: ${m.btns}`);
        // the slash menu rows: level-3 rows, every group a panel
        await toChat(p);
        await click(p, '#slashBtn');
        await waitFor(p, shown('menu'));
        const s = await ev(p, `(() => { const b = [...document.querySelectorAll('#menu button[role=menuitem]')].filter((e) => e.getClientRects().length).map((e) => Math.round(e.getBoundingClientRect().height)); return { min: Math.min(...b), n: b.length }; })()`);
        assert.ok(s.n >= 4 && s.min >= 52, `command rows: ${JSON.stringify(s)}`);
        noProblems(p);
      } finally { await p.dispose(); }
    });

    await C('esc-is-back', async () => {
      const p = await paired({ w: 1440, h: 900 });
      try {
        // shortcuts / security page open ABOVE Settings: back (icon or Esc) returns to Settings
        for (const [btn, page, back] of [['#keysBtn', 'keys', '#keysClose'], ['#menu-about', 'badge-panel', '#badge-close']]) {
          for (const how of ['esc', 'icon']) {
            await toChat(p);
            await openSettings(p);
            await click(p, btn);
            await waitFor(p, shown(page));
            if (how === 'esc') await key(p, 'Escape'); else await click(p, back);
            await waitFor(p, `document.getElementById(${JSON.stringify(page)}).hidden`);
            assert.equal(await ev(p, shown('settings')), true, `${page} ${how} → Settings`);
          }
        }
        // a level-3 page returns to Settings (icon or Esc) when opened from it; from the chat banner it goes to the chat
        for (const how of ['esc', 'icon']) {
          await toChat(p);
          await openSettings(p);
          await click(p, '#open-mem');
          await waitFor(p, `document.body.dataset.view === 'mem'`);
          if (how === 'esc') await key(p, 'Escape'); else await click(p, '#mem-view [data-back]');
          await waitFor(p, `document.body.dataset.view === 'chat'`);
          assert.equal(await ev(p, shown('settings')), true, `memory ${how} → Settings`);
        }
        // the command list: close icon = Esc = closed, focus back on ≡
        await toChat(p);
        await click(p, '#slashBtn'); await waitFor(p, shown('menu'));
        await click(p, '#menuClose');
        assert.equal(await ev(p, `document.getElementById('menu').hidden`), true);
        await click(p, '#slashBtn'); await waitFor(p, shown('menu'));
        await key(p, 'Escape');
        assert.equal(await ev(p, `document.getElementById('menu').hidden`), true);
        noProblems(p);
      } finally { await p.dispose(); }
    });

    await C('theme-menu-tokens', async () => {
      const p = await paired({ w: 390, h: 844 });
      try {
        await fake.send({ t: 'theme', v: 1, id: 'night', factory: null, rev: 'r1', name: { zh: '夜', en: 'Night' },
          colors: { menu: { background: '#141414', panel: '#2c2b26', text: '#faf9f5', accent: '#d97757', line: '#858585' } } });
        await sleep(200);
        await openSettings(p);
        const c = await ev(p, `[getComputedStyle(document.getElementById('settings')).backgroundColor, getComputedStyle(document.querySelector('#set-main .setcard')).backgroundColor, getComputedStyle(document.documentElement).getPropertyValue('--menu-accent').trim()]`);
        assert.deepEqual(c, [SOOTY, NOIR, '#d97757']);
        // the security page is locked: factory menu colours under any theme
        await click(p, '#menu-about'); await waitFor(p, shown('badge-panel')); await sleep(260);
        assert.equal(await ev(p, `getComputedStyle(document.getElementById('badge-panel')).backgroundColor`), NOIR);
        await key(p, 'Escape');
        // anything that is not #rrggbb is dropped
        await fake.send({ t: 'theme', v: 1, id: 'bad', factory: null, rev: 'r2', colors: { menu: { background: 'red', text: 'url(x)' } } });
        await sleep(200);
        assert.equal(await ev(p, `getComputedStyle(document.getElementById('settings')).backgroundColor`), NOIR);
        await fake.send({ t: 'theme', v: 1, id: 'robots', factory: 'robots', rev: 'robots' });
        await sleep(200);
        assert.equal(await ev(p, `getComputedStyle(document.documentElement).getPropertyValue('--menu-accent').trim()`), '#71bbde');
        noProblems(p);
      } finally { await p.dispose(); }
    });
  }
} finally {
  if (!SHOTS_ONLY) rec.write();
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p187: ${failed} failed` : 'p187: all passed');
process.exit(failed ? 1 : 0);
