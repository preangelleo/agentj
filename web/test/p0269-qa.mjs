// 0.26.9 selfheal wave 2 — independent QA walkthrough "as the owner" (QA lane, not a lane gate). Real phone page against the local
// fake host (real Noise, own headless Chromium, never :9222). Complements the lane suites (p0269-chat / -term / -assist / -split)
// with what they do not cover:
//   A. #471 the host's REAL page shape for an absorbed message: serve.msg_steered writes card.steered = <running turn id> (an
//      integer, `True` only when no turn is known). A page loaded fresh (second phone / reload / history sync) must still say
//      「已插入当前这一轮」, not 「（无文字回复）」.
//   B. #471 the uncertain steer (security-r2 N-4): serve writes reply text = humane `steer.uncertain` + card {steered, uncertain}
//      and sends msg_steered {uncertain:true}. The owner must see the uncertain line, not 「已插入当前这一轮」, and nothing queued.
//   C. #468 task feed: every state with its reason as the host sends them (assist.py set_state why values), incl. stopped/ended,
//      stopped/interrupted, done/outbound_gate, refused/quota; the 「官方远程协助」 label on a task turn; 收回 while a task runs.
//   D. the queued notice of the owner's own message (merged 「上一件还在做，已等 N 分钟」 + 「停下上一件」) in every language/theme.
// Runs zh/en × light/dark at 390 and zh-light 1440. Shots + browser.json → $P0269_QA_OUT (default reports/qa/selfheal-0269/qa-w2/walk).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const OUT = process.env.P0269_QA_OUT || fileURLToPath(new URL('../../../reports/qa/selfheal-0269/qa-w2/walk/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const W = {
  zh: { task: '把这周的周报整理好', s1: '第一步：读完了 12 份会议记录。', say: '顺便把上个月的也看一下', steered: '已插入当前这一轮', noReply: '（无文字回复）',
    uncertain: '已发给 Codex，但它没确认收到：可能已插进这一轮，也可能没有。不会重发。', waited: '上一件还在做，已等不到 1 分钟', stop: '停下上一件',
    src: '官方远程协助', st: { queued: '排队中', running: '正在做', done: '已完成', refused: '已拦下', stopped: '已停下' },
    why: { endedStop: '协助结束了，这条任务停在了一半。', interrupted: '这条任务被中途停下了。', outbound_gate: '回复里有像密码或密钥的内容，已拦下，没有发给官方客服。', quota: '这次协助能发的任务数已经用完了。' },
    banner: /^官方远程协助中 · 剩 \d+ 分钟$/, revoke: '收回' },
  en: { task: "Put together this week's report", s1: 'Step 1: read all 12 meeting notes.', say: "Also look at last month's", steered: 'Added to the current turn', noReply: '(No text reply)',
    uncertain: 'Sent to Codex but not confirmed: it may or may not be in this turn. It will not be re-sent.', waited: 'Previous task still running · waiting under a minute', stop: 'Stop previous task',
    src: 'Agent J support', st: { queued: 'Waiting', running: 'Working', done: 'Done', refused: 'Blocked', stopped: 'Stopped' },
    why: null, banner: /^Agent J support helping · \d+ min left$/, revoke: null },
};
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost(), B = await launch();
const results = {}, shots = [], findings = [];
let checks = 0;
const soft = (ok, label, tag) => { checks++; if (!ok) { findings.push(`${tag}: ${label}`); console.log('FINDING ' + tag + ': ' + label); } return ok; };
const check = (ok, label) => { assert.ok(ok, label); checks++; };
try {
  const runs = [['zh', 390, 'light'], ['zh', 390, 'dark'], ['en', 390, 'light'], ['en', 390, 'dark'], ['zh', 1440, 'light']];
  for (const [lang, w, scheme] of runs) {
    const X = W[lang], tag = `${lang}-${scheme}-${w}`;
    fake.reset(); fake.st.extraCaps = ['ss1', 'assist1']; fake.st.sayMode = 'queued'; fake.st.prefs = { appearance: { language: lang, theme: scheme } };
    const revokes = [];
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'assist_revoke') {
        const ok = await fake.verifyControl(c, 'assist_revoke', m, {});
        revokes.push(ok);
        await send({ t: 'ctl_res', r: m.r, action: 'assist_revoke', ok });
        if (ok) { await fake.send({ t: 'assist_task', id: 'q-run', ts: Date.now(), state: 'stopped', why: 'ended' }); await fake.send({ t: 'assist_state', active: false, until: 0, tasks: [] }); await fake.send({ t: 'assist_notice', what: 'revoked' }); }
        return true;
      }
      return false;
    };
    const p = await newPage(B, w, w > 1000 ? 900 : 844, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 1000 });
    const snap = async (name) => { const f = `qa-${name}-${tag}.png`; writeFileSync(resolve(OUT, f), await shoot(p, '')); shots.push(f); };
    const words = () => evaluate(p, `document.getElementById('words').textContent`);
    const has = (sel) => evaluate(p, `!!document.querySelector(${JSON.stringify(sel)})`);
    const say = (text) => evaluate(p, `(()=>{const i=document.getElementById('input');i.value=${JSON.stringify(text)};i.dispatchEvent(new Event('input'));document.getElementById('send').click();return true})()`);
    const latest = () => evaluate(p, `(()=>{document.getElementById('pgLatest')?.click();return 1})()`);
    const seen = [];
    const goTo = async (id) => { seen.length = 0; for (let i = 0; i < 12; i++) { await evaluate(p, `document.getElementById('pgNext').click()`); await sleep(120); } for (let i = 0; i < 12; i++) { const o = await evaluate(p, `document.getElementById('omText').textContent + ' | ' + document.getElementById('pg').textContent`); seen.push(o); if (o.split(' | ')[0] === id) return true; await evaluate(p, `document.getElementById('pgPrev').click()`); await sleep(150); } return false; };
    try {
      await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await evaluate(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();return true})()`);
      await sleep(500);
      await fake.send({ t: 'status', s: 'working', agent: 'codex', name: 'Agent J' });

      // ---- D. the owner's own queued message behind a running turn
      const T = await fake.addTurn({ k: 'phone', text: X.task }, '', 'open');
      await fake.updateTurn(T.id, { reply: { text: X.s1 } });
      await waitFor(p, `document.getElementById('words').textContent.includes(${JSON.stringify(X.s1)})`);
      await say(X.say);
      await waitFor(p, `document.body.dataset.pageQueued==='1'`, 8000);
      await waitFor(p, `!!document.querySelector('#cmdx [data-waited]')`, 6000);
      check((await evaluate(p, `document.querySelector('#cmdx .sh-waited').textContent`)) === X.waited, 'D merged waited line ' + tag);
      check((await evaluate(p, `document.querySelector('#cmdx [data-queue-stop]')?.textContent`)) === X.stop, 'D 停下上一件 ' + tag);
      await snap('D-queued');

      // ---- B. uncertain steer, live: host updates the owner's page exactly like serve.msg_steered(uncertain=True)
      const M = fake.st.turns.at(-1), sid = [...fake.st.queued.keys()].at(-1);
      await fake.updateTurn(M.id, { end: 'done', reply: { text: X.uncertain }, card: { steered: T.id, uncertain: true } });
      await fake.send({ t: 'msg_steered', mid: M.id, turn: T.id, uncertain: true });
      await fake.send({ t: 'say_state', sid, s: 'delivered' });
      await sleep(900); await latest(); await sleep(300); await goTo(X.say);
      const wb = await words();
      soft(wb.includes(X.uncertain), 'B uncertain line visible on the owner message page', tag);
      soft(!wb.includes(X.steered), 'B uncertain page must not claim 「已插入当前这一轮」 (got: ' + wb.slice(0, 80) + ')', tag);
      check(await evaluate(p, `document.body.dataset.pageQueued==='0'`), 'B not queued ' + tag);
      check(!await has('#cmdx [data-queue-stop]'), 'B no 停下上一件 ' + tag);
      await snap('B-uncertain-live');
      fake.st.queued.clear();

      // ---- A. absorbed message as the host stores it (card.steered = running turn id), seen on a fresh load
      const A1 = await fake.addTurn({ k: 'phone', text: X.say + ' (A)' }, '', 'done', { steered: T.id });
      await fake.updateTurn(T.id, { end: 'done' });
      await evaluate(p, `location.reload()`); await sleep(400);
      await waitState(p, 'ready'); await sleep(900);
      await latest(); await sleep(300);
      check(await goTo(X.say + ' (A)'), 'A found the absorbed page ' + tag + ' ' + JSON.stringify(seen));
      const wa = await words();
      soft(wa.includes(X.steered), `A fresh load: absorbed page (card.steered=${T.id}) should say 「${X.steered}」, shows 「${wa.slice(0, 60)}」`, tag);
      await snap('A-steered-fresh-load');
      // the same with card.steered === true (host had no current turn id) — the lane suite's shape
      await fake.updateTurn(A1.id, { card: { steered: true } }); await sleep(600);
      soft((await words()).includes(X.steered), 'A card.steered===true says 已插入当前这一轮', tag);
      // B persisted: uncertain page after reload
      await goTo(X.say);
      soft((await words()).includes(X.uncertain) && !(await words()).includes(X.steered), 'B uncertain page after reload: uncertain line only', tag);
      await snap('B-uncertain-fresh-load');

      // ---- C. official remote assist: banner, feed with every state + reason, chat label, 收回 while running
      const until = Math.floor(Date.now() / 1000) + 30 * 60 - 20;
      await fake.send({ t: 'assist_state', active: true, until, id: 'g-qa', key_id: 'k-qa', tasks: [] });
      await waitFor(p, `!!document.getElementById('assist-banner') && !document.getElementById('assist-banner').hidden`, 6000);
      const bt = await evaluate(p, `document.getElementById('assist-banner-text')?.textContent`);
      check(X.banner.test(bt), 'C banner text ' + tag + ' ' + bt);
      if (X.revoke) check((await evaluate(p, `document.getElementById('assist-banner-revoke')?.textContent`)) === X.revoke, 'C 收回 ' + tag);
      const now = Date.now();
      const tasks = [['q-end', 'stopped', 'ended', '先看日志'], ['q-int', 'stopped', 'interrupted', '重启服务'], ['q-gate', 'done', 'outbound_gate', '查看配置'],
        ['q-quota', 'refused', 'quota', '再查一次'], ['q-ok', 'done', null, '检查网络'], ['q-que', 'queued', null, '看磁盘'], ['q-run', 'running', null, '修复 Agent J 服务']];
      for (const [id, state, why, text] of tasks) await fake.send({ t: 'assist_task', id, ts: now + tasks.findIndex((x) => x[0] === id), text, state, gid: 'g-qa', ...(why ? { why } : {}) });
      const tt = await fake.addTurn({ k: 'official_assist', text: '修复 Agent J 服务', assist: 'q-run' }, '正在检查服务…', 'open');
      await fake.send({ t: 'assist_state', active: true, until, id: 'g-qa', key_id: 'k-qa', tasks: tasks.map(([id, state]) => ({ id, state })) });
      await sleep(400);
      await evaluate(p, `document.getElementById('assist-banner-view').click()`);
      await waitFor(p, `!!document.getElementById('assist-view') && !document.getElementById('assist-view').hidden && document.querySelectorAll('#assist-feed .asfeed__item').length===${tasks.length}`, 6000);
      const items = await evaluate(p, `[...document.querySelectorAll('#assist-feed .asfeed__item')].map(li=>({id:li.dataset.id,state:li.dataset.state,st:li.querySelector('.asfeed__st')?.textContent,why:li.querySelector('.asfeed__why')?.textContent||''}))`);
      for (const [id, state] of tasks) {
        const it = items.find((x) => x.id === id);
        check(it && it.state === state && it.st === X.st[state], `C ${id} state word ${tag} ${JSON.stringify(it)}`);
      }
      if (X.why) for (const [id, , why] of tasks.filter((x) => x[2])) {
        const it = items.find((x) => x.id === id), key = id === 'q-end' ? 'endedStop' : why;
        check(it.why === X.why[key], `C ${id} reason ${tag} ${JSON.stringify(it)}`);
      } else for (const [id] of tasks.filter((x) => x[2])) check(items.find((x) => x.id === id).why.length > 0, `C ${id} has a reason ${tag}`);
      check(!await has('#assist-view input, #assist-view textarea, #assist-view .xterm'), 'C feed is read-only ' + tag);
      await snap('C-feed-states');
      await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`); await sleep(300);
      await latest(); await sleep(300);
      check((await evaluate(p, `document.getElementById('omLabel').textContent`)) === X.src, 'C chat label 「官方远程协助」 ' + tag);
      await snap('C-chat-label');
      await evaluate(p, `document.getElementById('assist-banner-revoke').click()`);
      await waitFor(p, `!document.body.hasAttribute('data-assist')`, 8000);
      check(revokes.length === 1 && revokes[0], 'C 收回 = signed assist_revoke ' + tag);
      check(!(await evaluate(p, `!!document.getElementById('assist-banner') && !document.getElementById('assist-banner').hidden`)), 'C banner gone ' + tag);
      await fake.updateTurn(tt.id, { end: 'stopped' });
      await sleep(300);
      await snap('C-revoked');

      const problems = p.problems.filter((x) => !/WebSocket|ERR_CONNECTION|net::/i.test(x));
      check(!problems.length, 'no console errors ' + tag + ' ' + problems.slice(0, 3).join(' | '));
      check(!p.offsite.length, 'no off-site requests ' + tag + ' ' + p.offsite.slice(0, 3).join(' '));
      results[tag] = findings.some((f) => f.startsWith(tag)) ? 'FINDINGS' : 'PASS';
      console.log(results[tag] + ' p0269-qa ' + tag);
    } finally { await p.dispose(); }
  }
} finally { fake.st.onApp = null; await B.close(); await fake.stop(); await web.stop(); }
writeFileSync(resolve(OUT, 'browser.json'), JSON.stringify({ checks, results, findings, shots }, null, 2) + '\n');
console.log(`p0269-qa: ${checks} checks, ${findings.length} findings; shots → ${OUT}`);
process.exit(findings.length ? 1 : 0);
