#!/usr/bin/env node
// P188 · 搜索聊天记录 (PROTOCOL §28, ADR-P188). Real page + local fake host (its hist_find = protocol/search.js), own headless
// Chromium (never :9222).
//   header-order       🔍 sits left of the gear, the gear is rightmost; same icon geometry (44 button, 22 icon)
//   local-ranking      pages on the phone are found at once: AND of the words, case / width / Traditional insensitive, input
//                      beats reply, ties newest first, highlights on every hit; 按时间 = newest first
//   host-older         a page only the computer still has comes in after the local ones (status says so), opens, flashes
//   jump-flash         a hit closes the card, the deck turns to that page, both cards flash, the words get a mark
//   reopen-restores    🔍 again = the last results page, same words, same scroll position
//   history-switch     every search is a page: 「第 N/共 M 次搜索：…」, ‹ › / ← → / a sideways swipe step through them
//   cache-limits       ≤ 20 pages (least recently used goes), dedupe of the same words; the sealed copy survives a reload
//   clear-empties      /clear (a new epoch) empties the cache; unpair wipes the sealed copy
//   old-host           a computer without `hfind`: local results + 「只搜了手机上已有的记录」
//   keyboard           ⌘K anywhere / Ctrl+K outside the field opens, Esc closes (focus back on 🔍); Ctrl+K in the field = readline
//   audit              dark card (Sooty, Lotion), AA contrast, taps ≥ 44, no console errors / off-origin requests
//   shots              390 / 1440 × zh / en × light / dark → reports/qa/p188/screens/
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, key, shoot, sleep, swipe, rect } from './browser.mjs';
import * as S from '../../protocol/search.js';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + (process.env.AJ_P188_OUT || 'reports/qa/p188/screens/');
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P188_ONLY || '').split(',').filter(Boolean);
const rec = parityRecorder('web/test/p188.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const click = (p, sel) => ev(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
const shown = (id) => `(() => { const e = document.getElementById(${JSON.stringify(id)}); return !!e && !e.hidden && e.getClientRects().length > 0; })()`;
const dbg = (p) => ev(p, `import('./js/search.js').then((m) => m.debug())`);
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };

// A small conversation: (owner text, reply). Ids follow the order.
const CHAT = [
  ['帮我把网站部署到线上', '好的，已经部署上线，地址不变。'],
  ['昨天的報表做完了嗎', '做完了，报表在共享文件夹里。'],
  ['Deploy the docs site too', 'Deployed. The docs site is live.'],
  ['部署', '已部署。'],
  ['今天天气怎么样', '东京晴，22 度。'],
  ['再检查一下线上的部署日志', '日志里没有错误；部署 09:12 完成。'],
  ['ＡＢＣ 全角测试', 'abc half width'],
  ['回滚上一个版本', '已经回滚到昨天的版本，线上恢复正常。'],
];
async function seed(chat = CHAT, filler = 0) {
  for (let i = 0; i < filler; i++) await fake.addTurn({ k: 'phone', dev: 'X', text: '闲聊 ' + i }, '收到 ' + i, 'done');
  for (const [a, b] of chat) await fake.addTurn({ k: 'phone', dev: 'X', name: 'iPad', text: a }, b, 'done');
}
async function paired({ w = 1440, h = 900, scheme = 'dark', lang = 'zh', chat = CHAT, filler = 0, hfind = true, olderDelay = 0 } = {}) {
  fake.reset();
  fake.st.hfind = hfind; fake.st.olderDelay = olderDelay;
  const base = fake.st.nextId - 1 + filler;       // the fake host's ids keep growing across resets
  await seed(chat, filler);
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await waitFor(p, `document.getElementById('pg').textContent.trim().endsWith('/ ${chat.length + filler}')`, 12000);
  await sleep(150);
  p.base = base;
  p.id = (i) => base + i;                          // 1-based position in `chat` → page id
  return p;
}
async function C(name, fn) {
  if (ONLY.length && !ONLY.includes(name)) return;
  let ok = true;
  try { await fn(); } catch (e) { ok = false; failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
  if (ok) console.log(`  ✓ ${name}`);
  rec.record(name, ok);
}
async function openSearch(p) { if (!(await ev(p, shown('srch')))) await click(p, '#srchBtn'); await waitFor(p, shown('srch')); await sleep(60); }
async function type(p, q) {
  await ev(p, `(() => { const i = document.getElementById('srchInput'); i.focus(); i.value = ${JSON.stringify(q)}; i.dispatchEvent(new Event('input', {bubbles: true})); })()`);
  await sleep(220);
}
const rowIds = (p) => ev(p, `[...document.querySelectorAll('#srchList .srhit')].map((b) => +b.dataset.id)`);
const label = (p) => ev(p, `document.getElementById('srchPage').textContent`);
/** What the ranking must be (ids are 1-based in CHAT order after `base`): the same scorer, newest first on a tie. */
function expect(q, chat = CHAT, base = 0) {
  const ts = S.terms(q);
  return chat.map(([a, b], i) => ({ id: base + i + 1, r: S.scoreFields({ in: a, quote: '', att: '', reply: b }, ts) }))
    .filter((x) => x.r).sort((x, y) => y.r.score - x.r.score || y.id - x.id).map((x) => x.id);
}

try {
  await C('header-order', async () => {
    for (const w of [1440, 390]) {
      const p = await paired({ w, h: w > 500 ? 900 : 844 });
      try {
        const g = await ev(p, `(() => { const top = document.querySelector('header.top'); const vis = [...top.children].filter((e) => e.getClientRects().length && getComputedStyle(e).display !== 'none');
          const r = (id) => document.getElementById(id).getBoundingClientRect();
          return { last: vis.at(-1).id, beforeLast: vis.at(-2).id, s: r('srchBtn').toJSON(), g: r('setBtn').toJSON(), si: document.querySelector('#srchBtn svg').getBoundingClientRect().width, gi: document.querySelector('#setBtn svg').getBoundingClientRect().width,
            tfit: document.getElementById('srchBtn').classList.contains('tfit') ? Math.round(parseFloat(getComputedStyle(document.getElementById('srchBtn'), '::after').width)) : null, sw: getComputedStyle(document.querySelector('#srchBtn svg path')).strokeWidth, gw: getComputedStyle(document.querySelector('#setBtn svg path')).strokeWidth }; })()`);
        assert.equal(g.last, 'setBtn', 'the gear is rightmost'); assert.equal(g.beforeLast, 'srchBtn', '🔍 is just left of it');
        assert.ok(g.s.right <= g.g.left + 1, 'search left of the gear ' + JSON.stringify([w, g.s, g.g]));
        // P340 #41 (topfit.js): on a narrow header 搜索's box narrows (class tfit) and a centred 44 px ::after keeps the target
        assert.equal(Math.round(g.s.height), Math.round(g.g.height));
        assert.ok((g.s.width >= 44 || g.tfit === 44) && g.s.height >= 44, 'search target ' + JSON.stringify([w, g.s.width, g.tfit]));
        assert.equal(Math.round(g.si), Math.round(g.gi)); assert.equal(g.sw, g.gw);
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });

  await C('local-ranking', async () => {
    const p = await paired();
    try {
      await openSearch(p);
      for (const q of ['部署', '部署 线上', 'DEPLOY', '報表', '报表', 'ａｂｃ', '回滚 版本 线上']) {
        await type(p, q);
        assert.deepEqual(await rowIds(p), expect(q, CHAT, p.base), q);
      }
      // AND: both words must appear
      await type(p, '部署 天气'); assert.deepEqual(await rowIds(p), []);
      assert.match(await ev(p, `document.getElementById('srchList').textContent`), /没有找到/);
      // the exact input ranks first, input beats reply
      await type(p, '部署'); assert.equal((await rowIds(p))[0], p.id(4));
      // highlights: every <mark> folds to one of the words
      await type(p, '报表 完');
      const ms = await ev(p, `[...document.querySelectorAll('#srchList mark')].map((m) => m.textContent)`);
      assert.ok(ms.length >= 2 && ms.every((m) => ['报表', '完'].includes(S.fold(m))), ms.join(','));
      assert.ok(ms.includes('報表'), 'Traditional text highlighted');
      // where + who + time on every row
      const meta = await ev(p, `[...document.querySelectorAll('#srchList .srhit')].map((b) => [b.querySelector('.srmeta').textContent, [...b.querySelectorAll('.srsnip > b')].map((x) => x.textContent)])`);
      for (const [m, tags] of meta) { assert.match(m, /\d\d:\d\d/); assert.ok(tags.length >= 1 && tags.every((x) => ['输入', '回复', '引用', '附件'].includes(x)), tags.join()); }
      // 按时间: newest first
      await type(p, '部署');
      await click(p, '#srchSort [data-sort="time"]'); await sleep(80);
      assert.deepEqual(await rowIds(p), [...expect('部署', CHAT, p.base)].sort((a, b) => b - a));
      await click(p, '#srchSort [data-sort="score"]'); await sleep(80);
      assert.deepEqual(await rowIds(p), expect('部署', CHAT, p.base));
      assert.equal(fake.st.finds.length, 0, 'everything is on the phone: the computer is not asked');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('host-older', async () => {
    // 60 filler pages, then the conversation: the phone gets the newest 50, the oldest of CHAT are only on the computer
    const old = [['很久以前的部署记录 alpha', '那次部署失败了']];
    const chat = old.concat(Array.from({ length: 55 }, (_, i) => ['闲聊 ' + i, '收到 ' + i]), [['最近一次部署 beta', '部署成功']]);
    const p = await paired({ chat, olderDelay: 5000 });   // the phone has the newest 50; paging back to the rest takes 5 s
    try {
      fake.st.findDelay = 600;
      await openSearch(p);
      await type(p, '部署');
      assert.deepEqual(await rowIds(p), [p.id(chat.length)], 'at once: the page on the phone');
      assert.match(await ev(p, `document.getElementById('srchStatus').textContent`), /正在搜电脑上更早的记录/);
      if (process.env.AJ_P188_DEBUG) { await sleep(2000); console.log(JSON.stringify(await dbg(p)), JSON.stringify(fake.st.finds), await ev(p, `document.getElementById('srchStatus').textContent`), p.problems); }
      await waitFor(p, `document.querySelectorAll('#srchList .srhit').length === 2`, 4000);
      assert.deepEqual(await rowIds(p), [p.id(1), p.id(chat.length)].sort((a, b) => expect('部署', chat, p.base).indexOf(a) - expect('部署', chat, p.base).indexOf(b)));
      assert.match(await ev(p, `document.getElementById('srchStatus').textContent`), /其中 1 条来自电脑上更早的记录/);
      const f = fake.st.finds.at(-1);
      assert.deepEqual([f.q, f.sort, f.limit], ['部署', 'score', 50]);
      // opening the computer-only page pages back until it is on the phone
      await click(p, `#srchList .srhit[data-id="${p.id(1)}"]`);
      await waitFor(p, `document.getElementById('omText').textContent.includes('很久以前的部署记录')`, 8000);
      assert.equal(await ev(p, shown('srch')), false);
      assert.match(await ev(p, `document.getElementById('pg').textContent`), /^1 \//);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('jump-flash', async () => {
    const p = await paired();
    try {
      await openSearch(p);
      await type(p, '共享文件夹');
      assert.deepEqual(await rowIds(p), [p.id(2)]);
      await click(p, '#srchList .srhit');
      await waitFor(p, `document.getElementById('omText').textContent.includes('報表')`);
      assert.equal(await ev(p, shown('srch')), false);
      assert.equal(await ev(p, `document.getElementById('rm').classList.contains('srflash') && document.getElementById('om').classList.contains('srflash')`), true);
      assert.equal(await ev(p, `document.querySelector('#words mark.srmark')?.textContent`), '共享文件夹');
      await sleep(3400);
      assert.equal(await ev(p, `!!document.querySelector('#words mark.srmark') || document.getElementById('rm').classList.contains('srflash')`), false, 'the flash and the mark go');
      // a hit in the owner's input opens the source card
      await openSearch(p);
      await type(p, '天气');
      await click(p, '#srchList .srhit');
      await waitFor(p, `document.getElementById('omText').textContent.includes('天气')`);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('reopen-restores', async () => {
    const chat = Array.from({ length: 40 }, (_, i) => ['部署第 ' + i + ' 步', '部署 ' + i + ' 完成']);
    const p = await paired({ w: 390, h: 844, chat });
    try {
      await openSearch(p);
      await type(p, '部署');
      assert.equal((await rowIds(p)).length, 40);
      await ev(p, `document.getElementById('srchList').scrollTop = 600`);
      await sleep(80);
      const top = await ev(p, `document.getElementById('srchList').scrollTop`);
      assert.ok(top > 300);
      await click(p, '#srchList .srhit:nth-child(9)');
      await waitFor(p, `!document.body.dataset.srch`);
      await click(p, '#srchBtn'); await waitFor(p, shown('srch'));
      assert.equal(await ev(p, `document.getElementById('srchInput').value`), '部署');
      assert.equal(await label(p), '第 1/共 1 次搜索：部署');
      assert.equal(await ev(p, `document.getElementById('srchList').scrollTop`), top, 'the same scroll position');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('history-switch', async () => {
    const p = await paired({ w: 390, h: 844 });
    try {
      for (const q of ['部署', '报表', '回滚']) { await openSearch(p); await type(p, q); await key(p, 'Escape'); await waitFor(p, `!document.body.dataset.srch`); }
      await openSearch(p);
      assert.equal(await label(p), '第 3/共 3 次搜索：回滚');
      await click(p, '#srchPrev'); await sleep(60);
      assert.equal(await label(p), '第 2/共 3 次搜索：报表');
      assert.deepEqual(await rowIds(p), expect('报表', CHAT, p.base));
      // a sideways swipe on the card: right = the earlier search, left = the later one
      const r = await rect(p, '#srchList');
      await swipe(p, r.x - 120, r.y, r.x + 120, r.y + 10);
      await sleep(120);
      assert.equal(await label(p), '第 1/共 3 次搜索：部署');
      await swipe(p, r.x + 120, r.y, r.x - 120, r.y - 8);
      await sleep(120);
      assert.equal(await label(p), '第 2/共 3 次搜索：报表');
      // a vertical swipe is a scroll, not a switch
      await swipe(p, r.x, r.y + 80, r.x + 20, r.y - 80);
      await sleep(120);
      assert.equal(await label(p), '第 2/共 3 次搜索：报表');
      // keyboard: ← → when the focus is in the results
      await ev(p, `document.getElementById('srchList').focus()`);
      await key(p, 'ArrowRight'); await sleep(60);
      assert.equal(await label(p), '第 3/共 3 次搜索：回滚');
      await key(p, 'ArrowLeft'); await key(p, 'ArrowLeft'); await sleep(60);
      assert.equal(await label(p), '第 1/共 3 次搜索：部署');
      assert.equal(await ev(p, `document.getElementById('srchPrev').disabled`), true);
      // typing new words on an old page makes a new page at the end; the same words again do not add a page
      await type(p, '天气');
      assert.equal(await label(p), '第 4/共 4 次搜索：天气');
      await key(p, 'Escape'); await openSearch(p); await type(p, '部署'); await key(p, 'Escape'); await openSearch(p);
      assert.equal(await label(p), '第 4/共 4 次搜索：部署', 'the earlier 部署 page made way for the newer one');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('cache-limits', async () => {
    const p = await paired();
    try {
      const lim = await ev(p, `import('./js/search.js').then((m) => [m.PAGES_MAX, m.BYTES_MAX])`);
      assert.deepEqual(lim, [20, 2 * 1024 * 1024]);
      for (let i = 0; i < 23; i++) { await openSearch(p); await type(p, '部署 ' + 'abcdefghijklmnopqrstuvw'[i]); await key(p, 'Escape'); }
      const d = await dbg(p);
      assert.equal(d.pages.length, 20, 'at most 20 pages');
      assert.equal(d.pages[0].q, '部署 d', 'the three least recently used went');
      assert.ok(d.pages.reduce((n, x) => n + x.bytes, 0) <= 2 * 1024 * 1024);
      // the sealed copy survives a reload of the page (same computer, same session)
      await sleep(600);
      await navigate(p, web.url);
      await waitState(p, 'ready');
      await sleep(400);
      await openSearch(p);
      assert.equal(await label(p), '第 20/共 20 次搜索：部署 w');
      assert.deepEqual(await rowIds(p), []);
      assert.equal(await ev(p, `document.getElementById('srchInput').value`), '部署 w');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('clear-empties', async () => {
    const p = await paired();
    try {
      await openSearch(p); await type(p, '部署'); await key(p, 'Escape');
      assert.equal((await dbg(p)).pages.length, 1);
      await fake.clearHistory();
      await waitFor(p, `import('./js/search.js').then((m) => m.debug().pages.length === 0)`);
      await openSearch(p);
      assert.equal(await label(p), '搜索聊天记录');
      assert.match(await ev(p, `document.getElementById('srchList').textContent`), /空格隔开多个词/);
      await key(p, 'Escape');
      // a reload after the clear: the sealed copy is gone too
      await sleep(600);
      await navigate(p, web.url); await waitState(p, 'ready'); await sleep(300);
      await openSearch(p);
      assert.equal(await label(p), '搜索聊天记录');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('old-host', async () => {
    const chat = [['很久以前的部署', '好']].concat(Array.from({ length: 55 }, (_, i) => ['闲聊 ' + i, '收到 ' + i]), [['最近的部署', '好']]);
    const p = await paired({ chat, hfind: false, olderDelay: 5000 });
    try {
      await openSearch(p); await type(p, '部署'); await sleep(600);
      assert.deepEqual(await rowIds(p), [p.id(chat.length)]);
      assert.match(await ev(p, `document.getElementById('srchStatus').textContent`), /版本较旧，只搜了手机上已有的记录/);
      assert.equal(fake.st.finds.length, 0, 'an older computer is never asked');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('keyboard', async () => {
    const p = await paired();
    try {
      await ev(p, `document.activeElement && document.activeElement.blur()`);
      await key(p, 'k', { ctrl: true });
      await waitFor(p, shown('srch'));
      assert.equal(await ev(p, `document.activeElement.id`), 'srchInput');
      await type(p, '部署');
      await key(p, 'ArrowDown');
      assert.equal(await ev(p, `document.activeElement.classList.contains('srhit') && document.activeElement.getAttribute('aria-selected')`), 'true');
      await key(p, 'ArrowDown');
      const second = await ev(p, `+document.activeElement.dataset.id`);
      assert.equal(second, expect('部署', CHAT, p.base)[1]);
      await key(p, 'Escape');
      await waitFor(p, `!document.body.dataset.srch`);
      assert.equal(await ev(p, `document.activeElement.id`), 'srchBtn', 'focus back on 🔍');
      // Ctrl+K in the chat field = readline 删到行尾, never the search; ⌘K opens it from there
      await ev(p, `(() => { const i = document.getElementById('input'); i.focus(); i.value = 'abc def'; i.setSelectionRange(3, 3); i.dispatchEvent(new Event('input')); })()`);
      await key(p, 'k', { ctrl: true }); await sleep(120);
      assert.equal(await ev(p, shown('srch')), false);
      assert.equal(await ev(p, `document.getElementById('input').value`), 'abc');
      await key(p, 'k', { meta: true });
      await waitFor(p, shown('srch'));
      await key(p, 'k', { meta: true }); await sleep(80);
      assert.equal(await ev(p, shown('srch')), false, '⌘K again closes');
      // Enter in the box opens the first hit
      await key(p, 'k', { meta: true }); await waitFor(p, shown('srch'));
      await type(p, '回滚'); await key(p, 'Enter');
      await waitFor(p, `document.getElementById('omText').textContent.includes('回滚')`);
      // the shortcut sheet lists it
      assert.match(await ev(p, `document.getElementById('keys').textContent`), /搜索聊天记录/);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('audit', async () => {
    for (const [w, h, scheme] of [[1440, 900, 'light'], [390, 844, 'dark'], [390, 844, 'light']]) {
      const p = await paired({ w, h, scheme });
      try {
        await openSearch(p); await type(p, '部署 线上'); await sleep(120);
        const a = await ev(p, `(() => {
          const card = document.getElementById('srchCard'), r = card.getBoundingClientRect(), vis = (e) => e.getClientRects().length && !e.closest('[hidden]');
          const small = [...card.querySelectorAll('button, input')].filter(vis).filter((b) => { const q = b.getBoundingClientRect(); return q.height < 43.5 || q.width < 43.5; }).map((b) => b.id || b.className);
          return { bg: getComputedStyle(card).backgroundColor, fg: getComputedStyle(card).color, l: r.left, rgt: innerWidth - r.right, top: r.top, w: r.width, vw: innerWidth, small,
            overflowX: document.documentElement.scrollWidth > innerWidth || card.scrollWidth > card.clientWidth + 1 }; })()`);
        assert.equal(a.bg, 'rgb(44, 43, 38)'); assert.equal(a.fg, 'rgb(250, 249, 245)');
        assert.ok(Math.abs(a.l - a.rgt) <= 1, 'centred'); assert.deepEqual(a.small, []); assert.equal(a.overflowX, false);
        if (w > 500) assert.ok(a.w <= 641 && a.top > 60, 'a card in the middle, not a full page');
        const k = await ev(p, `(() => {
          const parse = (c) => { let m = c.match(/rgba?\\(([^)]+)\\)/), k = 1; if (!m) { m = c.match(/color\\(srgb ([^)]+)\\)/); k = 255; } if (!m) return null; const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [v[0] * k, v[1] * k, v[2] * k, v.length > 3 ? v[3] : 1]; };
          const over = (t, u) => [0, 1, 2].map((i) => t[i] * t[3] + u[i] * (1 - t[3])).concat(1);
          const bgOf = (e) => { const st = []; for (let n = e; n; n = n.parentElement) { const c = parse(getComputedStyle(n).backgroundColor); if (c && c[3] > 0) { st.push(c); if (c[3] >= 1) break; } } let col = [20, 20, 20, 1]; for (let i = st.length - 1; i >= 0; i--) col = over(st[i], col); return col; };
          const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
          const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
          const bad = []; let n = 0;
          const wk = document.createTreeWalker(document.getElementById('srchCard'), NodeFilter.SHOW_TEXT);
          for (let t = wk.nextNode(); t; t = wk.nextNode()) { const e = t.parentElement; if (!t.textContent.trim() || !e.getClientRects().length || e.closest(':disabled')) continue;
            const cs = getComputedStyle(e), bg = bgOf(e), fg = over(parse(cs.color), bg), q = ratio(fg, bg); n++; if (q < 4.5) bad.push(t.textContent.slice(0, 16) + ' ' + q.toFixed(2)); }
          return { n, bad }; })()`);
        assert.ok(k.n > 10); assert.deepEqual(k.bad, [], 'WCAG AA');
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });

  await C('shots', async () => {
    for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [1440, 900]]) {
      const p = await paired({ w, h, scheme, lang });
      try {
        const tag = `${lang}-${scheme}-${w}`;
        writeFileSync(OUT + `header-${tag}.png`, await shoot(p, ''));
        for (const q of lang === 'zh' ? ['报表', '部署'] : ['deploy', 'docs']) { await openSearch(p); await type(p, q); await key(p, 'Escape'); }
        await openSearch(p);
        await type(p, lang === 'zh' ? '部署 线上' : 'deploy');
        await sleep(200);
        writeFileSync(OUT + `results-${tag}.png`, await shoot(p, ''));
        await click(p, '#srchPrev'); await sleep(120);
        writeFileSync(OUT + `history-${tag}.png`, await shoot(p, ''));
        await click(p, '#srchList .srhit');
        await sleep(350);
        writeFileSync(OUT + `jump-${tag}.png`, await shoot(p, ''));
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });
} finally {
  rec.write();
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p188: ${failed} failed` : 'p188: all passed');
process.exit(failed ? 1 : 0);
