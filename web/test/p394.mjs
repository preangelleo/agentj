// P394 (#130) the To-do card kept by the Agent and the owner together — end to end: the REAL host code (a throw-away
// AGENTJ_STATE_DIR, `agentj card todo …` run as the Agent runs it, jcards.phone_get for every jcard_get) behind the real page
// and a local Noise fake host. An old version-1 file opens without loss; the Agent writes items → the card shows them with
// owner, start / due and marks, in the card's order; the filter All / Agent J's / Mine shows one side; tapping a row confirms
// the exact toggle message, the Agent runs it → the row is struck through and the item waiting for it may start.
// Own headless Chromium (never :9222); 390 px touch emulation is not a physical-device claim. Shots → reports/qa/p394/.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p394');
const HOST = join(ROOT, 'agentjarvis/host');
const venv = [join(HOST, '.venv/bin/python'), join(ROOT, '../../host/.venv/bin/python')].find(existsSync);
const PY = process.env.AJ_PYTHON || venv || 'python3';
const SCRATCH = join(process.env.P394_SCRATCH || join(homedir(), '.cache/p394'));
const ev = evaluate, q = JSON.stringify;
const click = (p, sel) => ev(p, `document.querySelector(${q(sel)}).click()`);
const L = {
  zh: { report: '明天把周报发给主人', rent: '交房租', archive: '周报发出后归档', a: '全部', ag: 'Agent J 负责', me: '我负责', overdue: '已过期', ready: '可以开始', soon: '快到期', old1: '旧的：买牛奶', old2: '旧的：已经做完的' },
  en: { report: 'Send the weekly report tomorrow', rent: 'Pay the rent', archive: 'Archive once the report is sent', a: 'All', ag: "Agent J's", me: 'Mine', overdue: 'Overdue', ready: 'Can start', soon: 'Due soon', old1: 'Old: buy milk', old2: 'Old: already done' },
};

function py(state, code) {
  return execFileSync(PY, ['-c', code], { cwd: HOST, env: { ...process.env, AGENTJ_STATE_DIR: state, PYTHONPATH: HOST, PYTHONDONTWRITEBYTECODE: '1' }, encoding: 'utf8' });
}
/** `agentj card <args…>` exactly as the Agent runs it (the real argparse tree, the real atomic write). */
function agentj(state, ...args) {
  return py(state, `import sys; from agentj import jcards; sys.exit(jcards.command(${q(args)}))`);
}
const phoneGet = (state, lang) => JSON.parse(py(state, `import json, pathlib; from agentj import jcards; print(json.dumps(jcards.phone_get(pathlib.Path(${q(state)}), 'todo', ${q(lang)}), ensure_ascii=False))`));

mkdirSync(OUT, { recursive: true }); mkdirSync(SCRATCH, { recursive: true });
const rec = parityRecorder('web/test/p394.mjs');
const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' }), fake = await startFakeHost(), B = await launch();
let checks = 0; const results = [];
const check = (ok, name) => { assert.ok(ok, name); checks++; results.push(name); };
try {
  for (const [w, h] of [[390, 844], [1440, 900]]) for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) {
    if (w === 1440 && scheme === 'dark') continue;
    const T = L[lang], tag = `${w}-${lang}-${scheme}`;
    const state = mkdtempSync(join(SCRATCH, 's-'));
    try {
      // the owner switches the card on; an old version-1 file is already there (it must open without loss)
      agentj(state, 'on', 'todo');
      writeFileSync(join(state, 'agentperm/cards-data/todo/todo.json'), JSON.stringify({ items: [{ id: 't1', text: T.old1, done: false }, { id: 't2', text: T.old2, done: true }] }));
      // the Agent writes what it promised and what it should remind the owner of (as the agentj-add-todo skill says)
      const added = JSON.parse(agentj(state, 'todo', 'add', '--text', T.report, '--owner', 'agent', '--end', '+1d', '--by', 'owner', '--source', 'chat', '--json'));
      check(added.ok && added.item.id === 't3' && added.address === 'card/todo#t3', 'agent writes t3 ' + tag);
      agentj(state, 'todo', 'add', '--text', T.rent, '--owner', 'owner', '--end', '2026-10-01', '--by', 'owner', '--json');
      agentj(state, 'todo', 'add', '--text', T.archive, '--owner', 'agent', '--start', 'after:t3', '--json');
      const due0 = JSON.parse(agentj(state, 'todo', 'due', '--json'));
      check(due0.items.map((x) => x.id).join() === 't3', 'due: only the report (the archive waits for it) ' + tag);

      fake.reset(); fake.st.caps = ['jcards1'];
      fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
      const card = { id: 'todo', name: { zh: '待办', en: 'To-do' }, desc: { zh: '待办', en: 'To-do' }, icon: 'checklist', accent: 'windstorm', on: true, official: true, plus: 'agentj-add-todo' };
      fake.st.onApp = async (c, m, send) => {
        if (m.t === 'jcard_list') { await send({ t: 'jcard_items', r: m.r, cards: [card], colors: {} }); return true; }
        if (m.t === 'jcard_get') { await send({ t: 'jcard_get', r: m.r, ...phoneGet(state, m.lang === 'en' ? 'en' : 'zh') }); return true; }
        return false;
      };
      const p = await newPage(B, w, h, scheme, { touch: w === 390, allow: [web.url, fake.relay + '/'] });
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
        await ev(p, "document.getElementById('phone-env-dismiss')?.click()"); await sleep(250);
        await ev(p, "import('./js/jcards.js').then(m=>m.refresh())");
        const open = async () => { await ev(p, `import('./js/five-pages.js').then(m=>m.openTarget('todo'))`); await waitFor(p, `document.querySelectorAll('.jc-body[data-card=todo] .jc-item').length===5`); await sleep(150); };
        await open();
        const order = await ev(p, "[...document.querySelectorAll('.jc-item')].map(li=>li.dataset.item).join()");
        check(order === 't4,t3,t1,t5,t2', `card order: overdue, due soon, open, waiting, done (${order}) ${tag}`);
        check(await ev(p, `(()=>{const li=document.querySelector('.jc-item[data-item=t4]');return li.dataset.mark==='overdue'&&li.textContent.includes(${q(T.overdue)})&&getComputedStyle(li).boxShadow!=='none';})()`), 'overdue row marked ' + tag);
        check(await ev(p, `(()=>{const li=document.querySelector('.jc-item[data-item=t3]');return li.dataset.owner==='agent'&&li.dataset.mark==='due'&&li.textContent.includes(${q(T.soon)})&&li.querySelector('.jc-when');})()`), 'agent row: owner, due soon, due time ' + tag);
        check(await ev(p, `(()=>{const t=[...document.querySelectorAll('.jc-item[data-item=t1],.jc-item[data-item=t2]')];return t.every(li=>li.dataset.owner==='owner')&&t.map(li=>li.querySelector('.jc-text').textContent).join('|')===${q(T.old1 + '|' + T.old2)};})()`), 'old version-1 items: kept, owner = me ' + tag);
        check(await ev(p, `getComputedStyle(document.querySelector('.jc-item[data-item=t2] .jc-text')).textDecorationLine.includes('line-through')`), 'old done item struck through ' + tag);
        check(await ev(p, "[...document.querySelectorAll('.jc-row,.jc-filter-chip')].every(b=>b.getBoundingClientRect().height>=44)"), 'rows and filter ≥ 44 px ' + tag);
        const wide = await ev(p, "[...document.querySelectorAll('.jc-item')].filter(e=>e.scrollWidth>e.clientWidth+1).map(e=>e.dataset.item+':'+e.scrollWidth+'/'+e.clientWidth).join()");
        check(!wide, 'rows fit the width ' + wide + ' ' + tag);
        writeFileSync(join(OUT, `todo-all-${tag}.png`), await shoot(p));
        // the filter
        const shown = () => ev(p, "[...document.querySelectorAll('.jc-item')].filter(li=>getComputedStyle(li).display!=='none').map(li=>li.dataset.item).join()");
        check(await ev(p, `[...document.querySelectorAll('.jc-filter-chip')].map(b=>b.textContent).join('|')===${q(`${T.a} 5|${T.ag} 2|${T.me} 3`)}`), 'filter labels with counts ' + tag);
        await click(p, '.jc-filter-chip[data-filter=agent]');
        check(await shown() === 't3,t5' && await ev(p, "document.querySelector('.jc-filter-chip[data-filter=agent]').getAttribute('aria-pressed')==='true'"), "filter Agent J's " + tag);
        writeFileSync(join(OUT, `todo-agent-${tag}.png`), await shoot(p));
        await click(p, '.jc-filter-chip[data-filter=owner]');
        check(await shown() === 't4,t1,t2', 'filter mine ' + tag);
        await click(p, '.jc-filter-chip[data-filter=all]');
        check(await shown() === 't4,t3,t1,t5,t2', 'filter all ' + tag);
        // the owner taps the Agent's row: the exact toggle message is confirmed and sent; the Agent runs it
        const n = fake.st.says.length;
        await click(p, '.jc-item[data-item=t3] .jc-row'); await waitFor(p, "!document.getElementById('confirm').hidden");
        check(await ev(p, "document.getElementById('confirm-text').textContent==='/agentj-add-todo toggle t3'"), 'tap → the exact toggle message ' + tag);
        await click(p, '#confirm-yes'); await sleep(600);
        const said = fake.st.says.slice(n).map((m) => m.text);
        check(said.length === 1 && said[0] === '/agentj-add-todo toggle t3', 'sent to the Agent: ' + said.join('|') + ' ' + tag);
        agentj(state, 'todo', 'toggle', '--entry', 't3', '--json');            // what the skill does with that message
        await click(p, '.jc-filter-chip[data-filter=agent]');                    // the filter survives a re-draw of this card
        await open();
        check(await ev(p, "(()=>{const li=document.querySelector('.jc-item[data-item=t3]');return li.dataset.done==='1'&&getComputedStyle(li.querySelector('.jc-text')).textDecorationLine.includes('line-through');})()"), 'done → struck through ' + tag);
        check(await ev(p, `(()=>{const li=document.querySelector('.jc-item[data-item=t5]');return li.dataset.mark==='ready'&&li.textContent.includes(${q(T.ready)});})()`), 'the item waiting for it may start now ' + tag);
        check(await shown() === 't5,t3', 'agent filter kept, done after open ' + tag);
        const due1 = JSON.parse(agentj(state, 'todo', 'due', '--json'));
        check(due1.items.map((x) => x.id + ':' + x.state).join() === 't5:ready', 'due after done: the archive ' + tag);
        writeFileSync(join(OUT, `todo-done-${tag}.png`), await shoot(p));
        check(p.problems.length === 0, 'no console errors ' + tag + ': ' + p.problems.join('|'));
      } finally { await p.dispose(); }
    } finally { rmSync(state, { recursive: true, force: true }); }
  }
  writeFileSync(join(OUT, 'web-browser.json'), JSON.stringify({ ok: true, checks, results, host: 'real agentj.jcards / jcards_todo in a throw-away AGENTJ_STATE_DIR', ios: '390px Chromium touch emulation; not a physical iOS device' }, null, 2) + '\n');
  rec.record('p394-todo-card', true); rec.write();
  console.log(`P394 PASS ${checks} checks → reports/qa/p394/`);
} catch (e) { rec.record('p394-todo-card', false); rec.write(); throw e; } finally { await B.close(); await fake.stop(); await web.stop(); }
