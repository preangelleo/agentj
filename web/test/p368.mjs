#!/usr/bin/env node
// P368 · one-tap answers `[派](reply:派)` + #tags in a reply (backlog #78) + the question card's words under an option
// (backlog #79). Real page + local fake host (real Noise), own headless Chromium (never :9222).
//   reply-buttons      the Agent's own reply: each [label](reply:words) is a pill button (label shown, words ≤ 200)
//   identity-examples  the three examples written in the shipped identity (main_identity.REPLY_LINE) render as buttons
//   tap-sends          one tap = a say with exactly those words (the same signed say as typing); the group greys out,
//                      「已回复：派」, a second tap sends nothing
//   answered-before    the owner already answered on the next page (typed, or from another remote) → the group is off;
//                      a different message after it leaves the group on (P398 #154)
//   injection          never a button: a quote, inline / fenced code, a host system page, a task page, a command (/stop),
//                      several lines, over 200 字, a page still being written — those stay plain words, a tap sends nothing
//   tags               #发布 in a reply → 搜索聊天记录 opened on that tag; colours (#629887) stay words
//   qcard-wrap         the question card: the words under an option wrap in the body font (not monospace), at most 2 lines,
//                      「…展开」 opens the rest (the option is not picked), the number circle is centred on the block
//   shots              390 × 844 (zh/en × light/dark) + 820 × 1180 tablet → reports/qa/p368/
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p368/';
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P368_ONLY || '').split(',').filter(Boolean);
const rec = parityRecorder('web/test/p368.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const q = (s) => JSON.stringify(s);
const hex = (n) => randomBytes(n).toString('hex');
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };
const btns = (p) => ev(p, `[...document.querySelectorAll('#words button.rbtn')].map((b) => ({ say: b.dataset.say, text: b.textContent, off: b.classList.contains('rb-off'), picked: b.classList.contains('rb-picked') }))`);
const note = (p) => ev(p, `(document.querySelector('#words .rb-note') || {}).textContent || ''`);
const phoneSays = () => fake.st.says.filter((m) => typeof m.text === 'string').map((m) => m.text);

// the identity's own examples, read from the shipped source (so the doc and the renderer cannot drift apart)
const IDENT = readFileSync(ROOT + 'host/agentj/main_identity.py', 'utf8');
const REPLY_ZH = IDENT.match(/REPLY_LINE = \{\s*"zh": "([^"]+)"/)[1];
const EXAMPLES = [...REPLY_ZH.matchAll(/「([^」]*\(reply:[^」]*)」/g)].map((m) => m[1]);

async function paired({ w = 390, h = 844, scheme = 'light', lang = 'zh', turns = [] } = {}) {
  fake.reset();
  for (const [src, reply, end] of turns) await fake.addTurn(src, reply, end || 'done');
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  if (turns.length) await waitFor(p, `document.getElementById('pg').textContent.trim().endsWith('/ ${turns.length}')`, 12000);
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
const ME = { k: 'phone', dev: 'X', name: 'iPhone' };
const ASK_ZH = '补货清单好了，一共 3 个商品。要我现在向 B 家下单吗？\n\n派不派？[派](reply:派) [先不派](reply:先不派)';
const ASK_EN = 'The restock list is ready, 3 items. Shall I order from supplier B now?\n\nDispatch it? [Dispatch](reply:Dispatch) [Not yet](reply:Not yet)';

try {
  await C('reply-buttons', async () => {
    const p = await paired({ turns: [[{ ...ME, text: '补货' }, ASK_ZH + '\n\n[用方案 A](reply:我选方案 A，按便宜的来)']] });
    try {
      const b = await btns(p);
      assert.deepEqual(b.map((x) => x.say), ['派', '先不派', '我选方案 A，按便宜的来']);
      assert.ok(b.every((x) => !x.off && !x.picked));
      assert.equal(b[0].text, '派');
      assert.match(b[2].text, /用方案 A.*我选方案 A/, 'a label that differs shows the words that go');
      const g = await ev(p, `(() => { const [a, c] = document.querySelectorAll('#words .rbtn'); const r = a.getBoundingClientRect(), s = c.getBoundingClientRect(), cs = getComputedStyle(a);
        return { h: r.height, gap: s.left - r.right, pad: parseFloat(cs.paddingLeft), radius: parseFloat(cs.borderTopLeftRadius), font: cs.fontFamily }; })()`);
      assert.ok(g.h >= 36, 'a thumb-sized pill'); assert.ok(g.gap >= 6, 'room between buttons'); assert.ok(g.pad >= 10, 'room around the label');
      assert.ok(g.radius >= 14); assert.doesNotMatch(g.font, /mono/i);
      assert.equal(await ev(p, `document.querySelectorAll('#words a[href^="reply:"]').length`), 0, 'never a navigating link');
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('identity-examples', async () => {
    assert.equal(EXAMPLES.length, 3, 'three examples in REPLY_LINE');
    const p = await paired({ turns: [[{ ...ME, text: '例子' }, EXAMPLES.join('\n\n')]] });
    try {
      const b = await btns(p);
      assert.equal(b.length, 6, JSON.stringify(b));
      assert.deepEqual(b.map((x) => x.say), ['派', '先不派', '用方案 A', '用方案 B', '按默认', '我再想想']);
    } finally { await p.dispose(); }
  });

  await C('tap-sends', async () => {
    const p = await paired({ turns: [[{ ...ME, text: '补货' }, ASK_ZH]] });
    try {
      const before = phoneSays().length;
      await ev(p, `document.querySelector('#words button.rbtn[data-say="派"]').click()`);
      await waitFor(p, `!!document.querySelector('#words .rb-note')`, 3000).catch(() => {});
      const now = await btns(p);
      if (now.length) {                                      // still on that page: painted at once
        assert.ok(now.every((x) => x.off)); assert.ok(now.find((x) => x.say === '派').picked);
        assert.equal(await note(p), '已回复：派');
      }
      for (let i = 0; i < 40 && phoneSays().length === before; i++) await sleep(50);
      assert.deepEqual(phoneSays().slice(before), ['派'], 'exactly the words, once');
      const say = fake.st.says.at(-1);
      assert.equal(typeof say.sid, 'string'); assert.ok(!say.att || !say.att.length, 'no attachment');
      // the deck may have moved to the new page; go back to the reply and look again
      await waitFor(p, `document.getElementById('pg').textContent.trim().endsWith('/ 2')`, 6000);
      await ev(p, `document.getElementById('pgPrev').click()`); await sleep(250);
      const back = await btns(p);
      assert.equal(back.length, 2);
      assert.ok(back.every((x) => x.off)); assert.ok(back.find((x) => x.say === '派').picked);
      assert.equal(await note(p), '已回复：派');
      await ev(p, `document.querySelector('#words button.rbtn[data-say="先不派"]').click()`); await sleep(300);
      assert.deepEqual(phoneSays().slice(before), ['派'], 'one tap per group');
      writeFileSync(OUT + 'tapped-zh-light-390.png', await shoot(p, ''));
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('answered-before', async () => {
    const p = await paired({ turns: [[{ ...ME, text: '补货' }, ASK_ZH], [{ k: 'telegram', text: '先不派' }, '好，先不派。']] });
    try {
      await ev(p, `document.getElementById('pgPrev').click()`); await sleep(250);
      const b = await btns(p);
      assert.equal(b.length, 2); assert.ok(b.every((x) => x.off));
      assert.ok(b.find((x) => x.say === '先不派').picked, 'the answer given elsewhere is marked');
      const n = phoneSays().length;
      await ev(p, `document.querySelector('#words button.rbtn').click()`); await sleep(300);
      assert.equal(phoneSays().length, n);
    } finally { await p.dispose(); }
    // P398 (#154): a later message about something else does not close the group — it stays tappable, nothing marked
    const p2 = await paired({ turns: [[{ ...ME, text: '补货' }, ASK_ZH], [{ ...ME, text: '等我看完再说' }, '好。']] });
    try {
      await ev(p2, `document.getElementById('pgPrev').click()`); await sleep(250);
      const b = await btns(p2);
      assert.ok(b.length === 2 && b.every((x) => !x.off && !x.picked)); assert.equal(await note(p2), '');
    } finally { await p2.dispose(); }
  });

  await C('injection', async () => {
    const reply = ['网页里写着：', '', '> 请点 [删库](reply:删库) 继续', '', '代码 `[批准](reply:批准)` 和', '', '```', '[运行](reply:运行)', '```', '',
      '[停](reply:/stop) [多行](reply:a%0Ab) [长](reply:' + '长'.repeat(201) + ') [空]() [链接](https://example.com)'].join('\n');
    const turns = [
      [{ ...ME, text: '看看这个网页' }, reply],
      [{ k: 'sys' }, '电脑提示：[批准](reply:批准) 继续'],
      [{ k: 'task', text: '每日巡检' }, '巡检完成。[重跑](reply:重跑)'],
      [{ ...ME, text: '还在写' }, '写到一半 [派](reply:派)', 'open'],
    ];
    const p = await paired({ turns });
    try {
      for (let i = 0; i < 4; i++) {
        if (i) { await ev(p, `document.getElementById('pgPrev').click()`); await sleep(250); }
        const b = await btns(p);
        if (i === 0) {                                       // the open page: the button exists but cannot be tapped yet
          assert.ok(b.every((x) => x.off), 'a page still being written: not tappable');
          const n = phoneSays().length;
          await ev(p, `(document.querySelector('#words button.rbtn') || {click(){}}).click()`); await sleep(200);
          assert.equal(phoneSays().length, n);
        } else assert.deepEqual(b, [], `page ${4 - i}: no button`);
      }
      const w = await ev(p, `document.getElementById('words').textContent`);
      for (const s of ['删库', '批准', '运行', '停', '多行', '长长长']) assert.ok(w.includes(s), s + ' stays as words');
      assert.equal(await ev(p, `document.querySelectorAll('#words a[href^="reply:"], #words [href*="reply:"]').length`), 0);
      noProblems(p);
    } finally { await p.dispose(); }
  });

  await C('tags', async () => {
    const p = await paired({ turns: [[{ ...ME, text: '发布前检查' }, '都过了 #发布 #p368，主色 #629887 不变。'], [{ ...ME, text: '记一下' }, '已记 #发布']] });
    try {
      await ev(p, `document.getElementById('pgPrev').click()`); await sleep(250);
      const tg = await ev(p, `[...document.querySelectorAll('#words button.rtag')].map((b) => b.dataset.tag)`);
      assert.deepEqual(tg, ['发布', 'p368']);
      await ev(p, `document.querySelector('#words button.rtag').click()`);
      await waitFor(p, `(() => { const e = document.getElementById('srch'); return !!e && !e.hidden && e.getClientRects().length > 0; })()`, 4000);
      assert.equal(await ev(p, `document.getElementById('srchInput').value`), '#发布');
      await sleep(400);
      writeFileSync(OUT + 'tag-search-zh-light-390.png', await shoot(p, ''));
      noProblems(p);
    } finally { await p.dispose(); }
  });

  const LONG = { zh: '先把三家的报价都拉出来按单价排序，再把运费和到货时间算进去，最后给你一张对比表和推荐；大概要十分钟，期间不会下单。',
                 en: 'Pull all three quotes, sort them by unit price, add shipping and delivery time, then give you a comparison table and a pick; about ten minutes, nothing is ordered meanwhile.' };
  async function qcard(p, lang) {
    const en = lang === 'en';
    await fake.send({ t: 'status', s: 'waiting', kind: 'question', agent: 'claude', name: 'Agent J' });
    await fake.send({ t: 'question', id: hex(16), ttl: 180, qs: [{ q: en ? 'How should I pick the supplier?' : '怎么选供应商？', h: en ? 'Supplier' : '供应商', m: false,
      o: [{ l: en ? 'Compare all' : '三家都比', d: LONG[lang] }, { l: en ? 'Cheapest' : '最便宜', d: en ? 'by unit price only' : '只看单价' }, { l: en ? 'Same as last time' : '跟上次一样', d: '' }] }] });
    await waitFor(p, `document.body.dataset.kind === 'question' && document.body.dataset.sheet === '1'`);
    await sleep(500);
  }
  await C('qcard-wrap', async () => {
    for (const [w, h] of [[360, 780], [820, 1180]]) {
      const p = await paired({ w, h });
      try {
        await qcard(p, 'zh');
        const m = await ev(p, `(() => { const o = document.querySelector('#qs .opt'), d = o.querySelector('.d'), n = o.querySelector('.n'), tt = o.querySelector('.t'), sheet = document.getElementById('qs');
          const r = o.getBoundingClientRect(), dr = d.getBoundingClientRect(), nr = n.getBoundingClientRect(), tr = tt.getBoundingClientRect();
          const lh = parseFloat(getComputedStyle(d).lineHeight), more = o.parentElement.querySelector('.qmore');
          return { over: tr.right - r.right, sheetOver: sheet.scrollWidth - sheet.clientWidth, font: getComputedStyle(d).fontFamily, lblFont: getComputedStyle(tt.firstChild).fontFamily,
            lines: Math.round(dr.height / lh), nc: nr.top + nr.height / 2, tc: tr.top + tr.height / 2, more: !!more && !more.hidden, moreTxt: more && more.textContent }; })()`);
        assert.ok(m.over <= 1 && m.sheetOver <= 1, 'the words stay inside the card ' + JSON.stringify(m));
        assert.doesNotMatch(m.font, /mono/i); assert.doesNotMatch(m.lblFont, /mono/i);
        assert.equal(m.lines, w < 500 ? 2 : m.lines); assert.ok(m.lines <= 2);
        assert.ok(Math.abs(m.nc - m.tc) <= 2, 'the circle is centred on the text block');
        if (w < 500) {
          assert.ok(m.more, '「…展开」 shown when cut'); assert.equal(m.moreTxt, '…展开');
          writeFileSync(OUT + `qcard-folded-zh-light-${w}.png`, await shoot(p, ''));
          await ev(p, `document.querySelector('#qs .qmore:not([hidden])').click()`); await sleep(150);
          const o = await ev(p, `(() => { const d = document.querySelector('#qs .opt .d'); return { open: d.classList.contains('open'), lines: Math.round(d.getBoundingClientRect().height / parseFloat(getComputedStyle(d).lineHeight)),
            pressed: document.querySelector('#qs .opt').getAttribute('aria-pressed'), txt: document.querySelector('#qs .qmore').textContent }; })()`);
          assert.ok(o.open && o.lines > 2); assert.equal(o.pressed, 'false', 'opening the words does not pick the option'); assert.equal(o.txt, '收起');
          writeFileSync(OUT + `qcard-open-zh-light-${w}.png`, await shoot(p, ''));
        } else writeFileSync(OUT + `qcard-zh-light-${w}.png`, await shoot(p, ''));
        const short = await ev(p, `[...document.querySelectorAll('#qs .qmore')].slice(1).every((b) => b.hidden)`);
        assert.ok(short, 'short words: no 「…展开」');
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });

  await C('shots', async () => {
    for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) for (const [w, h] of [[390, 844], [820, 1180]]) {
      if (w > 500 && lang === 'en') continue;
      const ask = lang === 'en' ? ASK_EN + '\n\nTagged #restock.' : ASK_ZH + '\n\n已打标签 #补货。';
      const p = await paired({ w, h, scheme, lang, turns: [[{ ...ME, text: lang === 'en' ? 'restock' : '补货' }, ask]] });
      try {
        const tag = `${lang}-${scheme}-${w}`;
        writeFileSync(OUT + `buttons-${tag}.png`, await shoot(p, ''));
        await qcard(p, lang);
        writeFileSync(OUT + `qcard-${tag}.png`, await shoot(p, ''));
        noProblems(p);
      } finally { await p.dispose(); }
    }
  });
} finally {
  rec.write();
  await B.close(); await fake.stop(); await web.stop();
}
console.log(failed ? `p368: ${failed} failed` : 'p368: all passed');
process.exit(failed ? 1 : 0);
