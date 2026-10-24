// P160 (PROTOCOL §22): Codex / OpenCode sub-agents are the same robots as Claude's, and one row of the 「在办」 dialog opens
// that task's own card (P154's 24 px robot) with the read-only detail the Agent J host gives (`busy_get` → `busy_info`).
// Real page + local fake host, independent headless Chromium (never :9222).
//   a) three sources in one list (a dispatch unit, a Codex sub-agent, an OpenCode sub-agent) → three robots, three rows
//   b) a tap on a row → the card: its robot at 24 px, who as the title, the whole title, where it runs + how long, the
//      latest step; the Agent J host is asked again while it stays open (the step changes on screen); 返回 → the list
//   c) a dispatch's card shows the report path + its first line; a report not written yet says so
//   d) the task finishes while its card is open → 「已经做完了」, 返回 with nothing left closes the dialog
//   e) an older computer (no `busyget` cap): the card shows what the list knows + a note, and asks nothing
//   f) host text is text: markup in a label / step never becomes elements
// Shots → reports/qa/p160/screens/<lang>-<theme>-<state>.png.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = process.env.P160_OUT || join(ROOT, 'reports/qa/p160/screens');
const $ = (id) => `document.getElementById('${id}')`;
const NAME = 'AgentJ-Father';
const NOW = () => Math.floor(Date.now() / 1000);
const T = {
  zh: [['掌枢', 'P160 harness 对等：Codex / OpenCode 子 Agent 接进在办机器人', 'ceo', 0], [null, '检查依赖版本，列出要升级的包', 'temp', 1],
    ['code-reviewer', '审一遍 busy.py 的改动', 'temp', 2]],
  en: [['Relay', 'P160 harness parity: Codex / OpenCode sub-agents as robots', 'ceo', 0], [null, 'Check dependency versions, list upgrades', 'temp', 1],
    ['code-reviewer', 'Review the busy.py change', 'temp', 2]],
};
const busyMsg = (lang, ids = [0, 1, 2], since = NOW() - 754) => ({ t: 'busy', by: NAME, tasks: ids.map((i) => {
  const [who, label, kind, c] = T[lang][i];
  return { id: 'k' + i, who, kind, label, since: since + i, c };
}) });
const CMD200 = '/usr/bin/bash ~/work/run-px.sh 160 p160-busy claude';   // P200: the command line, card only
const S = { zh: { temp: '临时助理', codex: 'Codex 子 Agent', oc: 'OpenCode 子 Agent', unit: '后台派单', since: /已经干了 12 分钟/, back: '返回',
  noReport: '还没写出来', gone: '已经做完了', old: /版本较旧/ },
en: { temp: 'Helper', codex: 'Codex sub-agent', oc: 'OpenCode sub-agent', unit: 'Background dispatch', since: /working for 12 min/, back: 'Back',
  noReport: 'Not written yet', gone: 'Done', old: /older/ } };
export const CASES = { a: 'three-harnesses-one-list', b: 'row-card-latest-step', c: 'dispatch-report-first-line', d: 'finished-while-open',
  e: 'older-computer-no-cap', f: 'host-text-is-text' };
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);
async function tapAt(p, x, y) {
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
async function tapEl(p, sel) { const r = await box(p, sel); await tapAt(p, r.l + r.w / 2, r.t + r.h / 2); }
const txt = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)})?.textContent ?? null`);

export async function runP160() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p160.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const step = async (c, f) => { try { await f(); rec.record(CASES[c], true); } catch (e) { rec.record(CASES[c], false); throw e; } };
  try {
    for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
      const s = S[lang];
      fake.reset();
      fake.st.busyget = true;
      const asked = [];
      let lastStep = lang === 'zh' ? 'Bash · pip list --outdated' : 'Bash · pip list --outdated';
      let report = { path: '~/work/app/notes/REPORT-160.md', head: null, at: null };
      fake.st.onApp = async (_c, m, send) => {
        if (m.t !== 'busy_get') return false;
        asked.push(m.id);
        const item = {
          k0: { id: 'k0', src: 'unit', since: NOW() - 754, report, cmd: CMD200 },
          k1: { id: 'k1', src: 'codex', since: NOW() - 754, last: { text: lastStep, at: NOW() } },
          k2: { id: 'k2', src: 'opencode', since: NOW() - 754, last: { text: 'read · agentj/busy.py', at: NOW() } },
          kx: { id: 'kx', src: 'claude', since: NOW() - 30, last: { text: '<img src=x onerror="window.__pwn=1">', at: NOW() } },
        }[m.id] || null;
        await send({ t: 'busy_info', r: m.r, id: m.id, item });
        return true;
      };
      const p = await newPage(B, 390, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-${name}.png`), await shoot(p, ''));
      const freeze = () => evaluate(p, `document.getAnimations().forEach(a=>{a.pause();a.currentTime=600})`);
      const play = () => evaluate(p, `document.getAnimations().forEach(a=>a.play())`);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'working', agent: 'codex', name: NAME });
        await waitFor(p, `${$('agent')}.textContent===${JSON.stringify('Agent J - '+NAME)}`);

        await step('a', async () => {
          await fake.send(busyMsg(lang));
          await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===3`);
          await tapEl(p, '#busyDots'); await waitFor(p, `!${$('busyDlg')}.hidden`);
          const rows = await evaluate(p, `[...document.querySelectorAll('#busyList li')].map(li=>[li.getAttribute('role'),li.tabIndex,li.querySelector('i.rb24')?1:0])`);
          check(rows.length === 3 && rows.every(([r, ti, rb]) => r === 'button' && ti === 0 && rb === 1), 'three rows, each a button with its 24 px robot ' + JSON.stringify(rows));
          check(await evaluate(p, `${$('busyBack')}.hidden && ${$('busyOne')}.hidden`), 'list view: no back, no card');
        });

        await step('b', async () => {
          await tapEl(p, '#busyList li:nth-child(2)');
          await waitFor(p, `!${$('busyOne')}.hidden && ${$('busyOne')}.querySelector('dd')`);
          await waitFor(p, `${$('busyOne')}.textContent.includes(${JSON.stringify(lastStep)})`, 4000);
          check(await txt(p, '#busyTitle') === s.temp, 'who is the title ' + lang);
          check(await txt(p, '#busyOne .bo-what') === T[lang][1][1], 'the whole title');
          const meta = await txt(p, '#busyOne .bo-meta');
          check(meta.includes(s.codex) && s.since.test(meta), 'where + how long: ' + meta);
          check(await evaluate(p, `${$('busyBig')}.querySelectorAll('i.rb.rb24').length===1 && getComputedStyle(${$('busyBig')}).display!=='none'`), 'its one robot at 24 px above the name (P154 card)');
          check((await box(p, '#busyBig')).b <= (await box(p, '#busyTitle')).t + 1, 'robot above the title');
          check(await evaluate(p, `getComputedStyle(${$('busyList')}).display==='none'`), 'list hidden on the card');
          check(await evaluate(p, `!${$('busyBack')}.hidden && document.activeElement.id==='busyBack'`), 'back shown and focused');
          check(await txt(p, '#busyBack') === s.back, 'back label ' + lang);
          check(asked.includes('k1'), 'asked the Agent J host for k1 only');
          await freeze(); await save('card-codex'); await play();
          if (lang === 'zh' && theme === 'light') {          // polling: the step changes while the card stays open (once)
            const n = asked.length;
            lastStep = 'Bash · uv pip compile requirements.in';
            await waitFor(p, `${$('busyOne')}.textContent.includes(${JSON.stringify(lastStep)})`, 9000);
            check(asked.length > n, 'asked again while open');
          }
          await tapEl(p, '#busyBack');
          await waitFor(p, `${$('busyOne')}.hidden && getComputedStyle(${$('busyList')}).display!=='none'`);
          check(await evaluate(p, `document.activeElement.id`) === 'busyClose', 'back → list, focus to 知道了');
          const n = asked.length; await sleep(4600);
          check(asked.length === n, 'no more asking once the card is closed');
          await tapEl(p, '#busyList li:nth-child(3)');
          await waitFor(p, `${$('busyOne')}.textContent.includes('read · agentj/busy.py')`, 4000);
          check(await txt(p, '#busyTitle') === 'code-reviewer' && (await txt(p, '#busyOne .bo-meta')).includes(s.oc), 'OpenCode sub-agent card');
          await tapEl(p, '#busyBack'); await waitFor(p, `${$('busyOne')}.hidden`);
        });

        await step('c', async () => {
          await tapEl(p, '#busyList li:nth-child(1)');
          await waitFor(p, `${$('busyOne')}.querySelector('code')`, 4000);
          check(await txt(p, '#busyOne code') === report.path, 'report path');
          check((await txt(p, '#busyOne')).includes(s.noReport), 'not written yet');
          check(!(await txt(p, '#busyOne dl')).includes(lang === 'zh' ? '最近一步' : 'Latest step'), 'a dispatch has no 「latest step」 row');
          check((await txt(p, '#busyOne .bo-meta')).includes(s.unit), 'a dispatch');
          report = { ...report, head: lang === 'zh' ? '完成条目数：3/3；候选 ready；需要贾维斯：P165 集成' : 'Done 3/3; candidate ready; Jarvis: integrate in P165', at: NOW() };
          await waitFor(p, `${$('busyOne')}.textContent.includes(${JSON.stringify(report.head)})`, 9000);
          check(true, 'report first line appears');
          check((await txt(p, '#busyOne dl')).includes(CMD200) && (await txt(p, '#busyOne dl')).includes(lang === 'zh' ? '启动命令' : 'Command'), 'P200: the command line is in the card (启动命令)');
          check(!(await txt(p, '#busyList')).includes('run-px.sh'), 'P200: never on the list');
          await freeze(); await save('card-dispatch-report'); await play();
        });

        await step('d', async () => {
          await fake.send(busyMsg(lang, [1, 2]));
          await waitFor(p, `${$('busyTitle')}.textContent===${JSON.stringify(s.gone)}`, 4000);
          check(!(await evaluate(p, `${$('busyDlg')}.hidden`)), 'the card stays open to say it is done');
          await tapEl(p, '#busyBack'); await waitFor(p, `${$('busyOne')}.hidden`);
          check(await evaluate(p, `document.querySelectorAll('#busyList li').length`) === 2, 'back to the two left');
          await tapEl(p, '#busyList li:nth-child(1)'); await waitFor(p, `!${$('busyOne')}.hidden`);
          await fake.send({ t: 'busy', by: NAME, tasks: [] });
          await waitFor(p, `${$('busyTitle')}.textContent===${JSON.stringify(s.gone)}`, 4000);
          await tapEl(p, '#busyBack');
          await waitFor(p, `${$('busyDlg')}.hidden`, 4000); check(true, 'nothing left: 返回 closes');
        });

        await step('f', async () => {
          const evil = '<b id="pwn">x</b><img src=x onerror="window.__pwn=1">';
          await fake.send({ t: 'busy', by: NAME, tasks: [{ id: 'kx', who: null, kind: 'temp', label: evil, since: NOW() - 30, c: 3 }] });
          await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===1`);
          await tapEl(p, '#busyDots'); await waitFor(p, `!${$('busyDlg')}.hidden`);
          await tapEl(p, '#busyList li:nth-child(1)');
          await waitFor(p, `${$('busyOne')}.textContent.includes('onerror')`, 4000);
          check(await evaluate(p, `!document.getElementById('pwn') && !${$('busyOne')}.querySelector('img,b#pwn') && !window.__pwn`), 'markup stays text');
          await evaluate(p, `${$('busyClose')}.click()`); await waitFor(p, `${$('busyDlg')}.hidden`);
        });
        check(!p.problems.length && !p.offsite.length, 'no CSP/network errors ' + JSON.stringify(p.problems.slice(0, 2)));
      } finally { await p.dispose(); }
    }

    await step('e', async () => {                      // an older computer: no busyget cap
      fake.reset();
      const asked = [];
      fake.st.onApp = async (_c, m) => { if (m.t === 'busy_get') { asked.push(m.id); return true; } return false; };
      const p = await newPage(B, 390, 844, 'light', { touch: true, allow: [web.url, fake.relay + '/'] });
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=zh')); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'working', agent: 'claude', name: NAME });
        await fake.send(busyMsg('zh', [1]));
        await waitFor(p, `${$('busyDots')}.querySelectorAll('i.rb:not(.bye)').length===1`);
        await tapEl(p, '#busyDots'); await waitFor(p, `!${$('busyDlg')}.hidden`);
        await tapEl(p, '#busyList li:nth-child(1)');
        await waitFor(p, `${$('busyOne')}.querySelector('.bo-note')`, 4000);
        check(S.zh.old.test(await txt(p, '#busyOne .bo-note')), 'older computer note');
        check(await txt(p, '#busyOne .bo-what') === T.zh[1][1] && S.zh.since.test(await txt(p, '#busyOne .bo-meta')), 'what the list knows');
        await sleep(300);
        check(asked.length === 0, 'asks nothing without the cap');
        writeFileSync(join(OUT, 'zh-light-older-computer.png'), await shoot(p, ''));
      } finally { await p.dispose(); }
    });
    writeFileSync(join(OUT, 'results.json'), JSON.stringify({ scope: 'real Chromium + local fake host; not a physical phone', checks, complete: true }, null, 2) + '\n');
    console.log(`p160: ${checks} checks passed`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP160();
