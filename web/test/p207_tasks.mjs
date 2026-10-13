#!/usr/bin/env node
// P207 §2 · 定时任务 shows the computer's own scheduled tasks. Real page + local fake host, own headless Chromium (never :9222).
//   groups      a host announcing `systasks`: the page shows 「Agent J 的定时任务」 (unchanged cards, 启用/停用) and below it
//               「这台电脑上的其它定时任务」 — one read-only card per row (name, state tag, source tag, schedule in words, next /
//               last run), the read-only note, no button inside the group, the count line
//   schedules   cron / @daily / every N / at start-up / systemd OnCalendar / Windows raw text all become one readable line
//   empty       the host found nothing → 「这台电脑上没有读到其它定时任务。」
//   old-host    a host without the capability is never asked (`systask_list` not sent) and the group stays hidden
//   leak        the rendered page never contains anything the host did not send (no command text exists to leak)
// Shots (zh/en, dark, 1440 + 390) → agentjarvis/reports/qa/p207/tasks/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'agentjarvis/reports/qa/p207/tasks/';
mkdirSync(OUT, { recursive: true });
const ONLY = (process.env.AJ_P207T_ONLY || '').split(',').filter(Boolean);
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
let failed = 0;
const ev = (p, s) => evaluate(p, s);
const NOW = Math.floor(Date.now() / 1000);
const OWN = [{ id: 'daily', title: { zh: '每日简报', en: 'Daily brief' }, schedule: '0 9 * * *', tz: 'local', mode: 'research', enabled: true, tsha: 'x', next: NOW + 3600 }];
const SYS = [
  { src: 'systemd', id: 'backup.timer', name: 'Nightly backup of photos', when: [{ k: 'cal', v: '*-*-* 03:00:00' }], state: 'waiting', next: NOW + 7200, last: NOW - 80000 },
  { src: 'systemd', id: 'status.timer', name: 'status every minute', when: [{ k: 'every', v: 60 }, { k: 'boot', v: 30 }], state: 'active', next: NOW + 30, last: NOW - 30 },
  { src: 'systemd', id: 'wiki.timer', name: 'LLM Wiki sync', when: [{ k: 'cal', v: '*-*-* 00,01,02,03:35:00 Asia/Tokyo' }], state: 'failed', next: NOW + 900 },
  { src: 'cron', id: 'report.sh', name: 'report.sh', when: [{ k: 'cron', v: '0 9 * * 1-5' }], state: 'waiting' },
  { src: 'cron', id: 'cleanup', name: 'cleanup', when: [{ k: 'cron', v: '@daily' }], state: 'waiting' },
  { src: 'launchd', id: 'com.me.tunnel', name: 'com.me.tunnel', when: [{ k: 'boot', v: 0 }], state: 'disabled' },
  { src: 'schtasks', id: 'Backup Photos', name: 'Copies photos', when: [{ k: 'raw', v: 'Daily 3:00:00 AM' }], state: 'waiting', next_s: '10/14/2026 3:00:00 AM' },
];

async function paired({ w = 1440, h = 900, lang = 'zh', caps = ['systasks'], sys = SYS } = {}) {
  fake.reset();
  fake.st.extraCaps = caps;
  const asked = [];
  fake.answer('task_list', () => ({ t: 'tasks', more: false, agent: 'claude', paused: false, items: OWN }));
  fake.answer('systask_list', (m) => { asked.push(m); return [{ t: 'systasks', more: true, items: sys.slice(0, 3) }, { t: 'systasks', more: false, max: 200, items: sys.slice(3) }]; });
  const p = await newPage(B, w, h, 'dark', { allow: [web.url, fake.relay + '/'], touch: w < 500 });
  await navigate(p, fake.newPairing(web.url + (lang === 'en' ? '?lang=en' : '')));
  await waitState(p, 'awaiting-approval');
  await fake.approve();
  await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await sleep(200);
  p.asked = asked;
  return p;
}
async function openTasks(p) {
  await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
  await ev(p, `document.getElementById('setBtn').click()`);
  await waitFor(p, `!document.getElementById('settings').hidden`);
  await sleep(150);
  await ev(p, `document.getElementById('open-tasks').click()`);
  await waitFor(p, `document.body.dataset.view === 'tasks' && document.querySelectorAll('#tasks-list .task-card').length > 0`);
  await sleep(300);
}
async function C(name, fn) {
  if (ONLY.length && !ONLY.includes(name)) return;
  try { await fn(); console.log(`  ✓ ${name}`); } catch (e) { failed++; console.log(`  ✗ ${name}: ${e && e.stack || e}`); }
}
const noProblems = (p) => { assert.equal(p.problems.length, 0, p.problems.join('\n')); assert.equal(p.offsite.length, 0, p.offsite.join('\n')); };
const STATE = `(() => ({
  ownHead: !document.getElementById('tasks-h-own').hidden ? document.getElementById('tasks-h-own').textContent : null,
  sysShown: !document.getElementById('systasks').hidden && document.getElementById('systasks').getClientRects().length > 0,
  sysHead: document.getElementById('tasks-h-sys').textContent,
  note: document.querySelector('#systasks [data-i18n="tasks.sysNote"]').textContent,
  status: document.getElementById('systasks-status').textContent,
  own: [...document.querySelectorAll('#tasks-list .task-card')].map((c) => ({ title: c.querySelector('.task-title').textContent, btn: !!c.querySelector('button') })),
  sys: [...document.querySelectorAll('#systasks-list .systask-card')].map((c) => ({ src: c.dataset.src, state: c.dataset.state,
    title: c.querySelector('.task-title').textContent, tags: [...c.querySelectorAll('.tag')].map((x) => x.textContent),
    line: c.querySelectorAll('p.small')[0].textContent, id: c.querySelector('.systask-id')?.textContent ?? null })),
  buttons: document.querySelectorAll('#systasks button, #systasks a, #systasks input').length,
  order: document.getElementById('tasks-list').compareDocumentPosition(document.getElementById('systasks')) & Node.DOCUMENT_POSITION_FOLLOWING,
  text: document.getElementById('tasks-view').innerText,
}))()`;

await C('groups', async () => {
  const p = await paired();
  await openTasks(p);
  await waitFor(p, `document.querySelectorAll('#systasks-list .systask-card').length === ${SYS.length}`);
  const s = await ev(p, STATE);
  assert.equal(s.ownHead, 'Agent J 的定时任务');
  assert.deepEqual(s.own, [{ title: '每日简报', btn: true }], 'Agent J\'s own task unchanged, still with 启用/停用');
  assert.ok(s.sysShown && s.order, 'the computer\'s group is shown below Agent J\'s');
  assert.equal(s.sysHead, '这台电脑上的其它定时任务');
  assert.match(s.note, /只能看，不能修改或删除/);
  assert.equal(s.buttons, 0, 'read-only: no control in the group');
  assert.equal(s.status, '共 7 个');
  assert.equal(p.asked.length, 1);
  const by = Object.fromEntries(s.sys.map((x) => [x.id ?? x.title, x]));
  assert.deepEqual(by['backup.timer'].tags, ['等待下次运行', 'systemd 定时器']);
  assert.match(by['backup.timer'].line, /^时间：每天 03:00 · 下次 .+ · 上次 .+$/);
  assert.equal(by['status.timer'].state, 'active');
  assert.match(by['status.timer'].line, /每 1 分钟；开机后 30 秒/);
  assert.equal(by['wiki.timer'].state, 'failed');
  assert.deepEqual(by['wiki.timer'].tags, ['上次运行失败', 'systemd 定时器']);
  assert.match(by['wiki.timer'].line, /日历规则 \*-\*-\* 00,01,02,03:35:00/);
  assert.match(by['report.sh'].line, /工作日/);
  assert.equal(by['report.sh'].id, null, 'id line only when it differs from the name');
  assert.match(by.cleanup.line, /每天 00:00/);
  assert.equal(by['com.me.tunnel'].state, 'disabled');
  assert.match(by['com.me.tunnel'].line, /开机时/);
  assert.deepEqual(by['Backup Photos'].tags, ['等待下次运行', 'Windows 计划任务']);
  assert.match(by['Backup Photos'].line, /Daily 3:00:00 AM · 下次 10\/14\/2026 3:00:00 AM/);
  for (const x of ['--', 'rclone', 'password', 'sk-']) assert.ok(!s.text.includes(x), 'nothing like a command on the page: ' + x);
  noProblems(p);
  writeFileSync(OUT + 'tasks-1440-dark-zh.png', await shoot(p));
  await ev(p, `document.getElementById('systasks').scrollIntoView({ block: 'start' })`);
  writeFileSync(OUT + 'tasks-1440-dark-zh-computer.png', await shoot(p));
  await p.close();
});

await C('shots', async () => {
  for (const [w, h, lang] of [[390, 844, 'zh'], [1440, 900, 'en'], [390, 844, 'en']]) {
    const p = await paired({ w, h, lang });
    await openTasks(p);
    await waitFor(p, `document.querySelectorAll('#systasks-list .systask-card').length === ${SYS.length}`);
    const s = await ev(p, STATE);
    if (lang === 'en') {
      assert.equal(s.sysHead, 'Other scheduled tasks on this computer');
      assert.match(s.note, /only look at them here/);
      assert.equal(s.ownHead, "Agent J's scheduled tasks");
    }
    const over = await ev(p, `[...document.querySelectorAll('#systasks-list *')].filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible').length + (document.documentElement.scrollWidth > innerWidth + 1 ? 100 : 0)`);
    assert.equal(over, 0, 'no horizontal overflow');
    noProblems(p);
    writeFileSync(`${OUT}tasks-${w}-dark-${lang}.png`, await shoot(p));
    await ev(p, `document.getElementById('systasks').scrollIntoView({ block: 'start' })`);
    writeFileSync(`${OUT}tasks-${w}-dark-${lang}-computer.png`, await shoot(p));
    await p.close();
  }
});

await C('empty', async () => {
  const p = await paired({ sys: [] });
  await openTasks(p);
  await waitFor(p, `document.getElementById('systasks-status').textContent === '这台电脑上没有读到其它定时任务。'`);
  assert.equal(await ev(p, `document.querySelectorAll('#systasks-list li').length`), 0);
  noProblems(p);
  await p.close();
});

await C('old-host', async () => {
  const p = await paired({ caps: [] });
  await openTasks(p);
  await sleep(400);
  const s = await ev(p, STATE);
  assert.equal(p.asked.length, 0, 'a host without `systasks` is never asked');
  assert.equal(s.sysShown, false);
  assert.equal(s.ownHead, null, 'and the page looks exactly as before');
  assert.equal(s.own.length, 1);
  noProblems(p);
  writeFileSync(OUT + 'old-host-390-dark-zh.png', await shoot(p));
  await p.close();
});

await B.close();
await fake.stop?.();
await web.stop();
console.log(failed ? `p207 tasks: ${failed} failed` : 'p207 tasks: all passed');
process.exit(failed ? 1 : 0);
