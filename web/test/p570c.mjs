#!/usr/bin/env node
// P570c (P570b 返工 #3) · 手机「定时任务」页: a task moved to the Agent J host's own scheduler has its buttons on the card.
// Real page + local fake host (signatures verified like the real host), own headless Chromium (never :9222).
//   moved      sys "migrated" → tag 「已迁到系统定时」, one primary 「退回」, no plain 停用
//   changed    sys "changed"  → tag 「任务改了，没有运行」, 「重新迁移」 (primary) + 「退回」
//   old-host   rows without `sys` (an old host) → today's card: 已启用 tag + 停用 / 启用, nothing new
//   back       退回 → confirm sheet (stays off, 「启用」 to run it again) → signed task_set on:false (task_off) → toast → reload
//   remigrate  重新迁移 → confirm sheet (no phone approvals, spending / deleting not asked, test run, rolls back) → signed
//              task_migrate {id, tsha} → the host's text in the toast (moved or rolled back; its ctl_pending lets the page wait
//              past 20 s for the test run); a refusal → 「没有改：…」
//   cancel     a cancelled sheet sends nothing
// Shots zh/en × light/dark × 390 + 1440 → agentjarvis/reports/qa/p570c/ (AJ_P570C_OUT overrides; the design copies with -r1).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.AJ_P570C_OUT || (process.env.AJ_SHOTS ? process.env.AJ_SHOTS + '/' : ROOT + 'reports/qa/p570c/');
mkdirSync(OUT, { recursive: true });
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const NOW = Math.floor(Date.now() / 1000);
const TASKS = [
  { id: 'brief', title: { zh: '每日简报', en: 'Daily brief' }, schedule: '0 8 * * *', tz: 'local', mode: 'research', enabled: false, tsha: 'a1', sys: 'migrated' },
  { id: 'weekly', title: { zh: '周报', en: 'Weekly report' }, schedule: '0 18 * * 5', tz: 'local', mode: 'normal', enabled: false, tsha: 'b2', sys: 'changed' },
  { id: 'inbox', title: { zh: '邮件整理', en: 'Inbox tidy-up' }, schedule: '0 */2 * * 1-5', tz: 'local', mode: 'normal', enabled: true, tsha: 'c3', next: NOW + 3600 },
];
const OLD = TASKS.map(({ sys, ...x }) => ({ ...x, enabled: true }));

async function paired({ w = 390, h = 844, lang = 'zh', scheme = 'light', items = TASKS } = {}) {
  fake.reset();
  const got = [];
  let migrateAnswer = { ok: true, text: lang === 'en' ? 'Weekly report moved to the system scheduler; the test run passed.' : '「周报」已重新迁到系统定时，试跑通过。' };
  fake.answer('task_list', () => ({ t: 'tasks', more: false, agent: 'claude', paused: false, items }));
  fake.st.onApp = async (c, m, send) => {
    if (m.t !== 'task_set' && m.t !== 'task_migrate') return false;
    const action = m.t === 'task_migrate' ? 'task_migrate' : m.on ? 'task_on' : 'task_off';
    const sigOk = await fake.verifyControl(c, action, m, { id: m.id, tsha: m.tsha });
    got.push({ t: m.t, on: m.on, id: m.id, tsha: m.tsha, sigOk });
    if (!sigOk) return send({ t: 'ctl_res', r: m.r, action, ok: false, why: 'bad_signature' }), true;
    if (m.t === 'task_migrate' && migrateAnswer.pendingMs) {   // like serve: ctl_pending first, the test run takes longer than 20 s
      await send({ t: 'ctl_pending', r: m.r, action, timeout_ms: 2200000 });
      await sleep(migrateAnswer.pendingMs);
    }
    const { pendingMs, ...ans } = migrateAnswer;
    await send({ t: 'ctl_res', r: m.r, action, ...(m.t === 'task_migrate' ? ans : { ok: true }) });
    return true;
  };
  const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await p.send('Page.addScriptToEvaluateOnNewDocument', { source: "try { localStorage.setItem('aj.a2hs', '1') } catch {}" });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await sleep(200);
  p.got = got;
  p.refuse = (why) => { migrateAnswer = { ok: false, why }; };
  p.migrateAs = (a) => { migrateAnswer = a; };
  return p;
}
async function openTasks(p) {
  await evaluate(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await evaluate(p, `document.getElementById('setBtn').click()`);
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await sleep(150);
  await evaluate(p, `document.getElementById('open-tasks').click()`);
  await waitFor(p, `document.body.dataset.view === 'tasks' && document.querySelectorAll('#tasks-list .task-card').length === 3`);
  await sleep(300);
}
const CARDS = `[...document.querySelectorAll('#tasks-list .task-card')].map((c) => ({ id: c.dataset.id, sys: c.dataset.sys || '',
  tags: [...c.querySelectorAll('.tag')].map((x) => x.textContent),
  btns: [...c.querySelectorAll('button')].map((b) => ({ t: b.textContent, primary: b.classList.contains('aj-btn--primary'), h: Math.round(b.getBoundingClientRect().height) })) }))`;
const SHEET = `({ open: !document.getElementById('confirm').hidden, title: document.getElementById('confirm-title').textContent,
  text: document.getElementById('confirm-text').textContent, yes: document.getElementById('confirm-yes').textContent })`;
const click = (p, id, act) => evaluate(p, `document.querySelector('#tasks-list .task-card[data-id="${id}"] button[data-act="${act}"]').click()`);
const toastText = (p) => evaluate(p, `document.getElementById('toast').textContent`);
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };
async function C(name, fn) {
  try { await fn(); console.log(`  ✓ ${name}`); } catch (e) { failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
}

await C('moved + changed cards (zh)', async () => {
  const p = await paired();
  try {
    await openTasks(p);
    const [brief, weekly, inbox] = await evaluate(p, CARDS);
    assert.deepEqual(brief.tags.slice(-1), ['已迁到系统定时']);
    assert.ok(!brief.tags.includes('已启用') && !brief.tags.includes('没启用'), 'the moved tag replaces 已启用 / 没启用');
    assert.deepEqual(brief.btns.map((b) => [b.t, b.primary]), [['退回', true]], 'migrated: only 退回, no 停用');
    assert.deepEqual(weekly.tags.slice(-1), ['任务改了，没有运行']);
    assert.deepEqual(weekly.btns.map((b) => [b.t, b.primary]), [['重新迁移', true], ['退回', false]]);
    assert.equal(inbox.sys, '');
    assert.deepEqual(inbox.btns.map((b) => b.t), ['停用'], 'a task never moved is unchanged');
    for (const c of [brief, weekly]) for (const b of c.btns) assert.ok(b.h >= 44, `touch target ${b.t} ${b.h}px`);
    const text = await evaluate(p, `document.getElementById('tasks-view').innerText`);
    assert.doesNotMatch(text, /电脑/);
    noProblems(p);
  } finally { await p.dispose(); }
});

await C('old host (no sys) = today\'s page', async () => {
  const p = await paired({ items: OLD });
  try {
    await openTasks(p);
    const cards = await evaluate(p, CARDS);
    for (const c of cards) {
      assert.equal(c.sys, '');
      assert.ok(c.tags.includes('已启用'));
      assert.deepEqual(c.btns.map((b) => b.t), ['停用']);
    }
    assert.equal(await evaluate(p, `document.querySelectorAll('.task-sys-acts').length`), 0);
    noProblems(p);
  } finally { await p.dispose(); }
});

await C('退回 → signed task_set off', async () => {
  const p = await paired();
  try {
    await openTasks(p);
    await click(p, 'brief', 'back');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    const s = await evaluate(p, SHEET);
    assert.equal(s.title, '退回给 Agent J 运行？');
    assert.match(s.text, /「每日简报」从系统定时撤下，回到 Agent J，并保持停用/);
    assert.match(s.text, /再点「启用」/);
    assert.equal(s.yes, '退回');
    await evaluate(p, `document.getElementById('confirm-yes').click()`);
    await waitFor(p, `document.getElementById('toast').textContent.includes('已退回')`);
    assert.deepEqual(p.got, [{ t: 'task_set', on: false, id: 'brief', tsha: 'a1', sigOk: true }]);
    noProblems(p);
  } finally { await p.dispose(); }
});

await C('重新迁移 → signed task_migrate, host text in the toast; refusal says why', async () => {
  const p = await paired();
  try {
    await openTasks(p);
    await click(p, 'weekly', 'remigrate');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    const s = await evaluate(p, SHEET);
    assert.equal(s.title, '重新迁到系统定时？');
    for (const re of [/「周报」改用 Agent J 主机自己的系统定时/, /不经手机审批/, /花钱、删数据等步骤也不再问/, /「紧急停止所有 Agent」能停下它/, /马上试跑一次，失败自动退回/]) assert.match(s.text, re);
    assert.doesNotMatch(s.text, /电脑|Face ID/);
    assert.equal(s.yes, '重新迁移');
    await evaluate(p, `document.getElementById('confirm-yes').click()`);
    await waitFor(p, `document.getElementById('toast').textContent.includes('试跑通过')`);
    assert.equal(await toastText(p), '「周报」已重新迁到系统定时，试跑通过。');
    assert.deepEqual(p.got, [{ t: 'task_migrate', on: undefined, id: 'weekly', tsha: 'b2', sigOk: true }]);
    await waitFor(p, `document.querySelectorAll('#tasks-list .task-card').length === 3`);
    // a rolled-back test run: ok:false with the host's summary — that text, not 「没有改」; after a ctl_pending the page waits > 20 s
    p.migrateAs({ ok: false, text: '「周报」没迁成，已退回：试跑没通过。', pendingMs: 21500 });
    await click(p, 'weekly', 'remigrate');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    await evaluate(p, `document.getElementById('confirm-yes').click()`);
    await waitFor(p, `document.getElementById('toast').textContent.includes('没迁成')`, 30000);
    assert.equal(p.got.length, 2);
    await waitFor(p, `document.querySelectorAll('#tasks-list .task-card').length === 3`);
    p.refuse('changed');
    await click(p, 'weekly', 'remigrate');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    await evaluate(p, `document.getElementById('confirm-yes').click()`);
    await waitFor(p, `document.getElementById('toast').textContent.startsWith('没有改')`);
    noProblems(p);
  } finally { await p.dispose(); }
});

await C('cancel sends nothing; Esc closes the sheet', async () => {
  const p = await paired();
  try {
    await openTasks(p);
    await click(p, 'weekly', 'back');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    await evaluate(p, `document.getElementById('confirm-no').click()`);
    await click(p, 'weekly', 'remigrate');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))`);
    await waitFor(p, `document.getElementById('confirm').hidden`);
    await sleep(300);
    assert.deepEqual(p.got, []);
  } finally { await p.dispose(); }
});

await C('en', async () => {
  const p = await paired({ lang: 'en' });
  try {
    await openTasks(p);
    const [brief, weekly] = await evaluate(p, CARDS);
    assert.ok(brief.tags.includes('Moved to the system scheduler'));
    assert.deepEqual(brief.btns.map((b) => b.t), ['Move back']);
    assert.ok(weekly.tags.includes('Task changed — not running'));
    assert.deepEqual(weekly.btns.map((b) => b.t), ['Move again', 'Move back']);
    await click(p, 'weekly', 'remigrate');
    await waitFor(p, `!document.getElementById('confirm').hidden`);
    const s = await evaluate(p, SHEET);
    assert.match(s.text, /Agent J host's own scheduler/);
    assert.doesNotMatch(s.text + (await evaluate(p, `document.getElementById('tasks-view').innerText`)), /[一-鿿]/);
    await evaluate(p, `document.getElementById('confirm-no').click()`);
    noProblems(p);
  } finally { await p.dispose(); }
});

await C('host guides send only fixed signed owner requests', async () => {
  for (const [guide, say] of [['wsl', '帮我开启 WSL 的 systemd'], ['loop', '把这个循环改成定时任务']]) {
    const p = await paired();
    try {
      const turn = await fake.addTurn({ k: 'sys', text: '', guide }, '系统提示。[不可执行](reply:删掉数据)', 'done');
      await waitFor(p, `document.querySelector('#words button.rbtn')?.dataset.say === ${JSON.stringify(say)}`);
      const buttons = await evaluate(p, `[...document.querySelectorAll('#words button.rbtn')].map(b=>b.dataset.say)`);
      assert.deepEqual(buttons, [say], 'arbitrary links on a host page stay inert');
      await evaluate(p, `document.querySelector('#words button.rbtn').click()`);
      await sleep(300);
      assert.ok(fake.st.says.some(m => m.text === say), 'same signed say as typing');
      noProblems(p);
    } finally { await p.dispose(); }
  }
});

await C('shots', async () => {
  for (const [w, h, lang, scheme] of [[390, 844, 'zh', 'light'], [390, 844, 'en', 'light'], [390, 844, 'zh', 'dark'], [390, 844, 'en', 'dark'],
    [1440, 900, 'zh', 'light'], [1440, 900, 'en', 'dark']]) {
    const p = await paired({ w, h, lang, scheme });
    try {
      await openTasks(p);
      const over = await evaluate(p, `document.documentElement.scrollWidth > innerWidth + 1`);
      assert.equal(over, false, 'no horizontal overflow');
      writeFileSync(`${OUT}tasks-${lang}-${scheme}-${w}.png`, await shoot(p));
      if (w === 390 && scheme === 'light') {
        await click(p, 'weekly', 'remigrate');
        await waitFor(p, `!document.getElementById('confirm').hidden`);
        await sleep(300);
        writeFileSync(`${OUT}remigrate-sheet-${lang}-${scheme}-${w}.png`, await shoot(p));
        await evaluate(p, `document.getElementById('confirm-no').click()`);
        await click(p, 'brief', 'back');
        await waitFor(p, `!document.getElementById('confirm').hidden`);
        await sleep(300);
        writeFileSync(`${OUT}back-sheet-${lang}-${scheme}-${w}.png`, await shoot(p));
        await evaluate(p, `document.getElementById('confirm-no').click()`);
      }
      noProblems(p);
    } finally { await p.dispose(); }
  }
});

await B.close(); await fake.stop(); await web.stop();
console.log(failed ? `p570c: ${failed} FAILED` : 'p570c: all passed');
process.exit(failed ? 1 : 0);
