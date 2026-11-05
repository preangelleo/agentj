// 0.26.9 #471 (CONTRACT §D, lane w2-web) — a long turn the owner talked past, on the real phone page against the local fake host
// (real Noise, own headless Chromium, never :9222). No customer machine, no real Codex, no credentials.
//   · turn T is answering (a live reply card); the owner sends a message → it is queued: the merged notice 「上一件还在做，已等
//     N 分钟」 + 「停下上一件」 under it (an older host without the turn watchdog: P481's /stop)
//   · T says more → that output is on a NEW card at the bottom (same turn, continuation), the page goes there; the old card keeps
//     only what it showed, says 「继续见下方」, gets nothing more (T's further output lands below), a tap goes to the continuation
//   · T ends, then the queued message is answered → its answer is the newest page (T's rest sits above it)
//   · negative control: a turn with no owner message after it streams on its own card as before (no split, no button)
//   · msg_steered {mid, turn} → that message says 「已插入当前这一轮」: not queued, no 「停下上一件」 (mid = page id, and = send id)
//   · on a host that runs the turn watchdog (a queued notice carries waited_s) 「停下上一件」 is the signed rescue_do end_turn
// Shots (zh light / dark 390, en light 390, zh light 1440) → reports/qa/p0269-split/ (P0269_W2_OUT overrides: the ticket's shot folder).
// AJ_P0269_BEFORE=1 + AJ_P0269_PUBLIC=<base public/> plays the same scene on the base build (before shots, no new-UI checks).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const before = process.env.AJ_P0269_BEFORE === '1';
const OUT = process.env.P0269_W2_OUT || fileURLToPath(new URL('../../../reports/qa/p0269-split/', import.meta.url));
const W = {
  zh: { task: '把这周的周报整理好', s1: '第一步：读完了 12 份会议记录。', s2: '第二步：草稿写好了，在检查数字。', s3: '第三步：数字核对完，周报已经保存。',
    say: '顺便把上个月的也看一下', answer: '上个月的周报也看过了，没有问题。', cont: '继续见下方', contOf: '接着回复：把这周的周报整理好', steered: '已插入当前这一轮',
    waited: '上一件还在做，已等不到 1 分钟', stop: '停下上一件', other: '再查一下磁盘空间', otherReply: '磁盘还剩 120 GB。' },
  en: { task: 'Put together this week\'s report', s1: 'Step 1: read all 12 meeting notes.', s2: 'Step 2: the draft is written, checking the numbers.', s3: 'Step 3: numbers checked, the report is saved.',
    say: 'Also look at last month\'s', answer: 'Last month\'s report looks fine too.', cont: 'Continued below', contOf: 'Continuing the reply: Put together this week\'s report', steered: 'Added to the current turn',
    waited: 'Previous task still running · waiting under a minute', stop: 'Stop previous task', other: 'Check the disk space again', otherReply: '120 GB free.' },
};

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*', ...(process.env.AJ_P0269_PUBLIC ? { publicDir: process.env.AJ_P0269_PUBLIC } : {}) });
const fake = await startFakeHost(), B = await launch();
mkdirSync(OUT, { recursive: true });
let checks = 0;
const check = (ok, label) => { assert.ok(ok, label); checks++; };
const shots = [];
try {
  const runs = before ? [['zh', 390, 'light'], ['zh', 390, 'dark']] : [['zh', 390, 'light'], ['zh', 390, 'dark'], ['en', 390, 'light'], ['zh', 1440, 'light']];
  for (const [lang, w, scheme] of runs) {
    const X = W[lang], tag = `${lang}-${scheme}-${w}${before ? '-before' : ''}`;
    fake.reset(); fake.st.extraCaps = ['ss1']; fake.st.sayMode = 'queued';
    const msgs = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'rescue_do' && m.action === 'end_turn') {
        const ok = await fake.verifyControl(c, 'rescue_do', m, { action: 'end_turn' });
        msgs.push({ action: m.action, ok });
        await send({ t: 'reset_step', r: m.r, flow: 'end_turn', step: 1, state: 'run', text: '' });
        await sleep(200);
        await send({ t: 'reset_done', r: m.r, flow: 'end_turn', ok, step: 1, text: '' });
        return true;
      }
      return false;
    };
    const p = await newPage(B, w, w > 1000 ? 900 : 844, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 1000 });
    const snap = async (name) => { const f = `split-${name}-${tag}.png`; writeFileSync(resolve(OUT, f), await shoot(p, '')); shots.push(f); };
    const words = () => evaluate(p, `document.getElementById('words').textContent`);
    const pg = () => evaluate(p, `document.getElementById('pg').textContent`);
    const om = () => evaluate(p, `document.getElementById('omText').textContent`);
    const has = (sel) => evaluate(p, `!!document.querySelector(${JSON.stringify(sel)})`);
    const say = async (text) => {
      await evaluate(p, `(()=>{const i=document.getElementById('input');i.value=${JSON.stringify(text)};i.dispatchEvent(new Event('input'));document.getElementById('send').click();return true})()`);
    };
    try {
      await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();return true})()`);
      await sleep(500);
      await fake.send({ t: 'status', s: 'working', agent: 'codex', name: 'Agent J' });
      // ---- negative control: a turn nobody talked past streams on its own card
      const N = await fake.addTurn({ k: 'phone', text: X.other }, '', 'open');
      await fake.updateTurn(N.id, { reply: { text: X.otherReply } });
      await fake.updateTurn(N.id, { reply: { text: X.otherReply + '\n\n' + X.otherReply }, end: 'done' });
      await waitFor(p, `document.getElementById('words').textContent.includes(${JSON.stringify(X.otherReply)})`);
      check(!await has('[data-cont]') && (await pg()) === '1 / 1', 'no owner message after it → no split ' + tag);

      // ---- T answers; the owner speaks while it runs
      const T = await fake.addTurn({ k: 'phone', text: X.task }, '', 'open');
      await fake.updateTurn(T.id, { reply: { text: X.s1 } });
      await waitFor(p, `document.getElementById('words').textContent.includes(${JSON.stringify(X.s1)})`);
      await say(X.say);
      await waitFor(p, `document.body.dataset.pageQueued==='1'`, 8000);
      await sleep(900);
      const M = fake.st.turns.at(-1);
      check(M.src.text === X.say, 'the owner message is a page of its own ' + tag);
      if (!before) {
        await waitFor(p, `!!document.querySelector('#cmdx [data-waited]')`, 6000);
        check((await evaluate(p, `document.querySelector('#cmdx .sh-waited').textContent`)) === X.waited, 'merged notice 「上一件还在做，已等…」 ' + tag);
        check((await evaluate(p, `document.querySelector('#cmdx [data-queue-stop]')?.textContent`)) === X.stop, '「停下上一件」 under it ' + tag);
        const g = await evaluate(p, `(()=>{const r=document.querySelector('#cmdx [data-queue-stop]').getBoundingClientRect();return {w:r.width,h:r.height,x:r.x,right:r.right}})()`);
        check(g.h >= 44 && g.x >= 0 && g.right <= w, 'button ≥ 44 px, on screen ' + tag + JSON.stringify(g));
      }
      await snap('1-queued');
      // ---- T says more: a new card at the bottom, the page goes there
      await fake.updateTurn(T.id, { reply: { text: X.s1 + '\n\n' + X.s2 } });
      await sleep(700);
      if (!before) {
        await waitFor(p, `document.getElementById('words').textContent.includes(${JSON.stringify(X.s2)})`, 6000);
        check(!(await words()).includes(X.s1), 'the continuation has only the new output ' + tag);
        check((await pg()) === '4 / 4', 'a new page at the bottom ' + tag + ' ' + await pg());
        check((await om()) === X.contOf, 'its top line says what it continues ' + tag + ' ' + await om());
        check(await evaluate(p, `document.body.dataset.pageOpen==='1'`), 'the continuation is the live card ' + tag);
      }
      await snap('2-continuation');
      // the old card: only what it showed, 「继续见下方」, nothing more
      await fake.updateTurn(T.id, { reply: { text: X.s1 + '\n\n' + X.s2 + '\n\n' + X.s3 } });
      await sleep(400);
      await evaluate(p, `document.getElementById('pgPrev').click()`); await sleep(250);
      if (!before) await evaluate(p, `document.getElementById('pgPrev').click()`);
      await sleep(400);
      const head = await words();
      if (!before) {
        check(head.includes(X.s1) && !head.includes(X.s2) && !head.includes(X.s3), 'the old card keeps its words and gets nothing more ' + tag);
        check((await pg()) === '2 / 4', 'the old card is page 2 ' + tag + ' ' + await pg());
        check((await evaluate(p, `document.querySelector('#cmdx [data-cont]')?.textContent`)) === X.cont, '「继续见下方」 ' + tag);
        check(await evaluate(p, `document.body.dataset.pageOpen==='0' && getComputedStyle(document.querySelector('.thinking')||document.body).display!=='block' && (document.querySelector('.thinking')?.getClientRects().length||0)===0`), 'the old card is closed (no working dots) ' + tag);
        const g = await evaluate(p, `(()=>{const r=document.querySelector('#cmdx [data-cont]').getBoundingClientRect();return {w:r.width,h:r.height,x:r.x,right:r.right}})()`);
        check(g.h >= 44 && g.w >= 44 && g.x >= 0 && g.right <= w, '「继续见下方」 ≥ 44 px ' + tag + JSON.stringify(g));
      } else check(head.includes(X.s3), 'before: the output piles onto the old card ' + tag);
      await snap('3-old-card');
      if (!before) {
        await evaluate(p, `document.querySelector('#cmdx [data-cont]').click()`); await sleep(400);
        check((await pg()) === '4 / 4' && (await words()).includes(X.s3) && !(await words()).includes(X.s1), 'the tap goes to the continuation ' + tag);
      }
      // ---- T ends, the queued message is answered: its answer is the newest page
      await fake.updateTurn(T.id, { end: 'done' });
      await fake.deliverQueued(false);
      await fake.updateTurn(M.id, { reply: { text: X.answer }, end: 'done' });
      if (before) { await sleep(400); for (let i = 0; i < 3; i++) { await evaluate(p, `document.getElementById('pgNext').click()`); await sleep(200); } }   // the base build left us on the old card
      await waitFor(p, `document.getElementById('words').textContent.includes(${JSON.stringify(X.answer)})`, 6000);
      if (!before) check((await pg()) === '4 / 4' && (await om()) === X.say, 'the answer stays the newest page (T\'s rest above it) ' + tag);
      await snap('4-answered');

      if (!before) {
        // ---- msg_steered: absorbed, not queued (zh: mid = the page id · en: mid = the send id)
        const T2 = await fake.addTurn({ k: 'phone', text: X.other }, X.otherReply, 'open');
        await sleep(300);
        await say(X.say + ' (2)');
        await waitFor(p, `document.body.dataset.pageQueued==='1'`, 8000);
        const M2 = fake.st.turns.at(-1), sid = [...fake.st.queued.keys()].at(-1);
        await fake.send({ t: 'msg_steered', mid: lang === 'en' ? sid : M2.id, turn: T2.id });
        await waitFor(p, `document.getElementById('words').textContent.includes(${JSON.stringify(X.steered)})`, 6000);
        check(!await has('[data-queue-stop]') && !await has('#cmdx [data-waited]'), 'steered: no queued notice, no 「停下上一件」 ' + tag);
        check(await evaluate(p, `document.body.dataset.pageQueued==='0'`), 'steered: the page is not queued ' + tag);
        await sleep(2400);   // the pill's 「已送达」 moment
        check((await evaluate(p, `(()=>{const b=document.getElementById('sayCancel');return b.hidden?'':b.dataset.mode})()`)) !== 'queued', 'steered: the composer no longer holds it as queued ' + tag);
        await snap('5-steered');
        await fake.updateTurn(T2.id, { end: 'done' });
        fake.st.queued.clear();

        // ---- the turn watchdog's host: 「停下上一件」 = signed end_turn
        if (tag === 'zh-light-390') {
          const T3 = await fake.addTurn({ k: 'phone', text: X.task }, X.s1, 'open');
          await fake.addTurn({ k: 'sys', text: '' }, 'Codex 正在回答，等它说完我就把您的消息递过去。', 'done', { queued: true, waited_s: 30 });
          await say(X.say + ' (3)');
          await waitFor(p, `!!document.querySelector('#cmdx [data-queue-stop]')`, 8000);
          await evaluate(p, `document.querySelector('#cmdx [data-queue-stop]').click()`);
          await waitFor(p, `!document.getElementById('confirm').hidden`, 3000);
          await evaluate(p, `document.getElementById('confirm-yes').click()`);
          await waitFor(p, `document.getElementById('sh-end-msg')?.classList.contains('rsok')`, 6000);
          check(msgs.length === 1 && msgs[0].ok && fake.st.slashes.filter((x) => x.cmd === 'stop').length === 0, 'signed end_turn, no /stop ' + JSON.stringify(msgs));
          await fake.updateTurn(T3.id, { end: 'done' });
        }
        // an older host (no waited_s anywhere): P481's /stop — covered by test/p481.mjs on this build
      }
      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length, 'no console errors ' + tag + ' ' + problems.slice(0, 3).join(' | '));
      console.log('PASS p0269-split ' + tag);
    } finally { await p.dispose(); }
  }
} finally { fake.st.onApp = null; await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(resolve(OUT, `split-${before ? 'before' : 'after'}.json`), JSON.stringify({ checks, shots }, null, 2) + '\n');
console.log(`p0269-split: ${checks} checks; shots → ${OUT}`);
