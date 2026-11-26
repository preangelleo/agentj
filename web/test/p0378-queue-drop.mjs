// 0378 (chairman 11-26): the queue panel's 「清空」 and per-row removal, and the plain words for an owner message that left the
// queue without being sent. Real product DOM, Noise and browser against fakehost.mjs; every input is synthetic and local.
//   node web/test/p0378-queue-drop.mjs                      checks + after-*.png
//   P0378_BEFORE=<public/ of the base tree> node …/p0378-queue-drop.mjs  before-*.png
//   P0378_STYLE_ONLY=1 captures the queue controls and computed styles at 390 and 1440 px in both themes, including a baseline predating queuedrop1.
//   P0378_OUT=<dir> overrides the shot folder (default reports/qa/p0378)
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync, readFileSync} from 'node:fs';
import {join} from 'node:path';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch, newPage, navigate, waitState, waitFor, evaluate, shoot, sleep, tap} from './browser.mjs';

const BEFORE = process.env.P0378_BEFORE || '', TAG = BEFORE ? 'before' : 'after';
const STYLE_ONLY = process.env.P0378_STYLE_ONLY === '1';
const out = process.env.P0378_OUT || process.env.OLARES_RUN_OUT || 'reports/qa/p0378';
mkdirSync(out, {recursive: true});
const web = await startWebServer(BEFORE ? {publicDir: BEFORE, relayCsp: 'ws://127.0.0.1:*'} : {port: 0, relayCsp: 'ws://127.0.0.1:*'});
const fake = await startFakeHost(), browser = await launch();
const T_WITHDRAWN = JSON.parse(readFileSync(new URL('../i18n/web.zh.json', import.meta.url), 'utf8')).r.say.withdrawn;
const J = JSON.stringify, results = [], shots = [];
const WORDS = {
  zh: {clear: '清空', drop: '从排队中删除', fail: '没删掉，请再试一次。', dropped: '已从排队中删除，没有发送', stop: '关闭上一条任务',
    running: '整理今天的会议记录', rows: ['列出下周要办的三件事', '把确认过的日期整理成表，再把每一项对应的负责人、截止时间和需要提前准备的材料都写清楚，最后按周分组', '查一下周五的航班有没有改时间']},
  en: {clear: 'Clear', drop: 'Remove from queue', fail: 'Could not remove it. Please try again.', dropped: 'Removed from the queue. Not sent.', stop: 'Stop previous task',
    running: 'Tidy up the notes from today', rows: ['List three things for next week', 'Put the confirmed dates into a table, then add the owner, the deadline and the materials to prepare for every item, grouped by week', 'Check whether the Friday flight changed']},
};
const HOST_LINE = '这条消息没有送达主会话：主会话在收到它之前重启了。';
const UNSURE_LINE = '已从排队中移除，但主会话可能已经收到这条消息，仍可能执行。';
const acts = p => evaluate(p, `Object.fromEntries(['readBtn','copyReply','speakBtn'].map(id=>[id,document.getElementById(id).disabled]))`);
const robot = p => evaluate(p, `(()=>{const e=document.getElementById('thinking');return !!e&&e.getClientRects().length>0&&getComputedStyle(e).display!=='none'&&getComputedStyle(e).visibility!=='hidden'})()`);
const owner = (kind, text) => ({k: kind === 'terminal' ? 'host' : kind, source_kind: kind, source_role: 'owner', text});
const rowsNow = p => evaluate(p, `Array.from(document.querySelectorAll('#ownerQueueDialog[open] #ownerQueueList li'),li=>({text:li.querySelector('p').textContent,drop:li.querySelector('[data-queue-drop]')?.dataset.queueDrop||null,disabled:li.querySelector('[data-queue-drop]')?.disabled===true}))`);
const drops = () => fake.log.filter(m => m.t === 'queue_drop');
const stops = () => fake.log.filter(m => m.t === 'slash' && m.cmd === 'stop' || m.t === 'end_turn' || m.t === 'say_cancel');
async function click(p, selector){
  await waitFor(p, `(()=>{const e=document.querySelector(${J(selector)});if(!e)return false;const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.width>0&&r.height>0&&!!hit&&e.contains(hit)})()`);
  const pt = await evaluate(p, `(()=>{const r=document.querySelector(${J(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
  if (p.uiTouch) await tap(p, pt.x, pt.y);
  else for (const type of ['mousePressed', 'mouseReleased']) await p.send('Input.dispatchMouseEvent', {type, button: 'left', clickCount: 1, x: pt.x, y: pt.y});
}
async function open(p){
  await waitFor(p, `!!document.querySelector('#ownerQueueBtn:not([hidden])')`);
  if (!await evaluate(p, `document.querySelector('#ownerQueueDialog').open`)) await click(p, '#ownerQueueBtn');
  await waitFor(p, `document.querySelector('#ownerQueueDialog').open && document.querySelectorAll('#ownerQueueList li').length>0`);
}
/** Walk the pages (newest first, then back) until the owner's words on the page contain `needle`. */
async function seek(p, needle){
  const at = () => evaluate(p, `document.getElementById('omText').textContent.includes(${J(needle)})`);
  for (const id of ['pgNext', 'pgPrev']) for (let i = 0; i < 8 && !(await at()); i++) {
    if (await evaluate(p, `document.getElementById(${J(id)}).disabled`)) break;
    await evaluate(p, `(()=>{document.getElementById(${J(id)}).click();return true})()`); await sleep(200);
  }
  assert.ok(await at(), 'page with ' + needle);
}
async function shot(p, name){ await sleep(350); writeFileSync(join(out, name + '.png'), await shoot(p)); shots.push(name + '.png'); }
async function start(lang, theme, width, caps = ['queuedrop1']){
  fake.reset(); fake.log.length = 0;
  fake.st.prefs = {appearance: {language: lang, theme}}; fake.st.extraCaps = caps; fake.st.queueDrop = caps.length ? true : false; fake.st.autoReply = false;
  const p = await newPage(browser, width, width === 390 ? 844 : 900, theme, {touch: width === 390, allow: [web.url, fake.relay + '/']});
  p.uiTouch = width === 390;
  await navigate(p, 'about:blank');
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();return true})()`);
  const W = WORDS[lang];
  await fake.addTurn(owner('phone', W.running), '', 'open');                 // the running turn
  await fake.send({t: 'status', s: 'working', agent: 'claude', name: 'Agent J'});
  const ids = [];
  for (const [i, kind] of ['phone', 'terminal', 'phone'].entries()) ids.push((await fake.addTurn(owner(kind, W.rows[i]), '', 'open', {queued: true})).id);
  await waitFor(p, `document.querySelector('#ownerQueueBtn:not([hidden]) .queueCount')?.textContent==='3'`);
  return {p, W, ids, running: fake.st.turns[0].id};
}
const geometry = p => evaluate(p, `(()=>{const d=document.querySelector('#ownerQueueDialog').getBoundingClientRect(),box=e=>{const r=e.getBoundingClientRect();return{w:r.width,h:r.height,l:r.left,r:r.right,t:r.top,b:r.bottom}};
  const btn=[...document.querySelectorAll('#ownerQueueList [data-queue-drop]')].map(box),clear=box(document.querySelector('#ownerQueueClear')),title=box(document.querySelector('#ownerQueueTitle'));
  const texts=[...document.querySelectorAll('#ownerQueueList li p')].map(box);
  return{dialog:{l:d.left,r:d.right,w:d.width},btn,clear,title,texts,scroll:document.documentElement.scrollWidth,inner:innerWidth,
    markup:document.querySelectorAll('#ownerQueueList li p *').length,fg:getComputedStyle(document.querySelector('#ownerQueueClear')).color,bg:getComputedStyle(document.querySelector('#ownerQueueClear')).backgroundColor}})()`);

async function checkFooterStyle(p, theme) {
  const footer = await evaluate(p, `(() => {
    const style = el => {
      const cs = getComputedStyle(el), rect = el.getBoundingClientRect();
      return {bg: cs.backgroundColor, fg: cs.color, border: cs.borderWidth, x: rect.x, y: rect.y, w: rect.width, h: rect.height};
    };
    const dialog = document.getElementById('ownerQueueDialog');
    const stop = style(document.getElementById('ownerQueueStop'));
    const close = style(document.getElementById('ownerQueueClose'));
    const probe = document.createElement('span');
    probe.style.cssText = 'background:var(--lotion);color:var(--noir)';
    dialog.append(probe);
    const {bg: lotion, fg: noir} = style(probe);
    probe.style.color = 'var(--sooty)';
    const {fg: sooty} = style(probe);
    probe.remove();
    return {stop, close, lotion, noir, sooty, panel: style(dialog).bg, gap: close.x - stop.x - stop.w,
      theme: document.documentElement.dataset.theme};
  })()`);
  const {stop, close, lotion, noir, sooty, panel, gap} = footer;
  assert.equal(footer.theme, theme, 'the page uses the requested theme');
  assert.equal(panel, noir, 'the queue panel is Noir');
  assert.equal(stop.y, close.y, 'the actions stay side by side');
  assert.ok(gap >= 8, 'the actions have at least 8px between them');
  for (const b of [stop, close]) assert.ok(b.w >= 44 && b.h >= 44, 'the actions retain 44px touch targets');
  assert.deepEqual([close.bg, close.fg, close.border], [lotion, sooty, '1px'], 'Collapse keeps the original solid action style');
  if (!BEFORE) assert.deepEqual([stop.bg, stop.fg, stop.border], [lotion, noir, '0px'], 'Stop uses the solid action style');
  return footer;
}

try {
  if (STYLE_ONLY) {
    const measurements = [];
    for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
      const {p} = await start('zh', theme, width, BEFORE ? [] : undefined);
      try {
        await open(p);
        if (BEFORE) {
          measurements.push({width, ...await checkFooterStyle(p, theme)});
          await shot(p, `${TAG}-${width}-${theme}-zh`);
          continue;
        }
        const g = await geometry(p);
        assert.equal(g.btn.length, 3);
        assert.ok(g.title.r <= g.clear.l - 6);
        assert.ok(g.texts.every((text, i) => text.r <= g.btn[i].l - 6));
        const fills = await evaluate(p, `(() => {
          const probe = document.createElement('span');
          probe.style.cssText = 'background:var(--lotion);color:var(--noir)';
          document.querySelector('#ownerQueueDialog').append(probe);
          const expected = getComputedStyle(probe);
          const controls = [...document.querySelectorAll('#ownerQueueClear, #ownerQueueList [data-queue-drop]')];
          const solid = controls.every(e => {
            const s = getComputedStyle(e);
            return s.backgroundColor === expected.backgroundColor && s.color === expected.color && s.borderWidth === '0px';
          });
          probe.remove();
          return solid;
        })()`);
        assert.equal(fills, true);
        measurements.push({width, ...await checkFooterStyle(p, theme)});
        await shot(p, `${TAG}-${width}-${theme}-zh`);
      } finally { await p.dispose(); }
    }
    writeFileSync(join(out, `${TAG}-footer-style.json`), JSON.stringify(measurements, null, 2) + '\n');
  } else if (BEFORE) {
    for (const theme of ['light', 'dark']) {
      const {p} = await start('zh', theme, 390, []);
      try { await open(p); await shot(p, `before-390-${theme}-zh`); } finally { await p.dispose(); }
    }
  } else {
    // 1 · the panel on a computer that answers queue_drop: both controls, every size / theme / language
    for (const [lang, theme, width] of [['zh', 'light', 390], ['zh', 'dark', 390], ['en', 'light', 390], ['en', 'dark', 1440], ['zh', 'light', 1440]]) {
      const {p, W, ids, running} = await start(lang, theme, width);
      const name = `${width}-${theme}-${lang}`;
      try {
        await open(p);
        let rows = await rowsNow(p);
        assert.deepEqual(rows.map(r => r.drop), ids.map(String), name + ' every queued row offers removal, in order');
        assert.equal(rows.some(r => r.drop === String(running)), false, name + ' the running turn is not in the list');
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueClear').hidden`), false);
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueClear').textContent`), W.clear);
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueList [data-queue-drop]').getAttribute('aria-label')`), W.drop);
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueStop').textContent`), W.stop, 'the existing button keeps its words');
        await checkFooterStyle(p, theme);
        const g = await geometry(p);
        assert.equal(g.markup, 0, name + ' row text stays text');
        assert.ok(g.scroll <= g.inner, name + ' no sideways scroll');
        for (const b of [...g.btn, g.clear]) {
          assert.ok(b.w >= 44 && b.h >= 44, name + ' touch target ' + J(b));
          assert.ok(b.l >= g.dialog.l && b.r <= g.dialog.r, name + ' control inside the panel ' + J(b));
        }
        for (const [i, tx] of g.texts.entries()) assert.ok(tx.r <= g.btn[i].l - 6, name + ' text keeps clear of its remove button');
        assert.ok(g.title.r <= g.clear.l - 6, name + ' title keeps clear of 「清空」');
        assert.doesNotMatch(g.fg + g.bg, /rgb\((2[0-9]{2}), ?([0-9]|[1-4][0-9]), ?([0-9]|[1-4][0-9])\)/, name + ' no red');
        if (width === 390 || lang === 'zh') await shot(p, `after-${name}`);
        // one row: the frame names that page only; the row leaves with the history update, the others stay
        await click(p, `[data-queue-drop="${ids[1]}"]`);
        await waitFor(p, `document.querySelectorAll('#ownerQueueDialog[open] #ownerQueueList li').length===2`);
        assert.deepEqual(drops(), [{t: 'queue_drop', turns: [ids[1]]}], name + ' one row → its page id');
        rows = await rowsNow(p);
        assert.deepEqual(rows.map(r => r.drop), [ids[0], ids[2]].map(String));
        assert.equal(rows.some(r => r.disabled), false, 'nothing left disabled');
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueBtn .queueCount').textContent`), '2');
        if (width === 390 && lang === 'zh') await shot(p, `after-delete-one-${name}`);
        // the removed message's own page says so (existing reply surface, text only)
        await click(p, '#ownerQueueClose');
        await waitFor(p, `!document.querySelector('#ownerQueueDialog').open`);
        await seek(p, W.rows[1].slice(0, 12));
        await waitFor(p, `document.getElementById('omText').textContent.includes(${J(W.rows[1].slice(0, 12))}) && document.getElementById('words').textContent.trim()===${J(W.dropped)}`);
        assert.equal(await evaluate(p, `document.getElementById('words').querySelectorAll('button,a,input').length`), 0, 'no new element types on the page');
        assert.deepEqual(await acts(p), {readBtn: true, copyReply: true, speakBtn: true}, name + ' no reply button acts on a removed page (it has no reply text)');
        assert.equal(await evaluate(p, `document.getElementById('fwdBtn').disabled`), true);
        assert.equal(await robot(p), false, name + ' no working robot on a removed page');
        if (width === 390 && lang === 'zh') await shot(p, `after-chat-dropped-${name}`);
        // 「清空」: one frame, `all`; never a stop of the running turn
        await open(p);
        await click(p, '#ownerQueueClear');
        await waitFor(p, `!document.querySelector('#ownerQueueDialog').open && document.querySelector('#ownerQueueBtn').hidden`);
        assert.deepEqual(drops().slice(1), [{t: 'queue_drop', all: true}], name + ' 清空 → all');
        assert.deepEqual(stops(), [], name + ' 清空 stops nothing');
        assert.equal(fake.st.turns.find(x => x.id === running).end, 'open', 'the running turn is still running');
        assert.deepEqual(fake.st.turns.filter(x => ids.includes(x.id)).map(x => [x.end, x.card?.dropped]), ids.map(() => ['stopped', true]));
        assert.deepEqual(p.problems, [], name + ' console');
        results.push({case: 'drop', lang, theme, width});
      } finally { await p.dispose(); }
    }

    // 2 · a second remote: its rows leave on the same history update, with no answer of its own
    {
      const {p, ids} = await start('zh', 'dark', 390);
      try {
        await open(p);
        await fake.updateTurn(ids[0], {end: 'stopped', card: {dropped: true}});
        await waitFor(p, `document.querySelectorAll('#ownerQueueDialog[open] #ownerQueueList li').length===2`);
        // not delivered across a restart (the host's exact page: failed + card.undelivered + its own line): leaves the
        // queue; the page shows the host's line as an ordinary reply, so the reply buttons act on what is on screen
        const lost = {end: 'failed', card: {undelivered: true}, reply: {text: HOST_LINE}};
        await fake.updateTurn(ids[1], lost);
        await waitFor(p, `document.querySelectorAll('#ownerQueueDialog[open] #ownerQueueList li').length===1`);
        await fake.updateTurn(ids[2], lost);
        await waitFor(p, `!document.querySelector('#ownerQueueDialog').open && document.querySelector('#ownerQueueBtn').hidden`);
        await seek(p, WORDS.zh.rows[2].slice(0, 8));
        await waitFor(p, `document.getElementById('words').textContent.trim()===${J(HOST_LINE)}`);
        assert.deepEqual(await acts(p), {readBtn: false, copyReply: false, speakBtn: false}, 'the buttons act on the shown host line');
        assert.equal(await robot(p), false, 'no working robot on an undelivered page');
        assert.deepEqual(drops(), [], 'another remote sends nothing');
        await shot(p, 'after-chat-undelivered-390-dark-zh');
        await evaluate(p, `import('./js/settings.js').then(s => s.applyThemeLocal('light'))`);
        await waitFor(p, `document.documentElement.dataset.theme==='light'`);
        await shot(p, 'after-chat-undelivered-390-light-zh');
        results.push({case: 'other-remote+undelivered'});
      } finally { await p.dispose(); }
    }

    // 2b · removed from the queue while a copy may still run (stopped + card.unconfirmed + the host's line): leaves the
    //      queue, the host's words are the reply, no working robot; a later correction renders as a normal turn
    {
      const {p, ids} = await start('zh', 'light', 390);
      try {
        await open(p);
        await fake.updateTurn(ids[2], {end: 'stopped', card: {unconfirmed: true}, reply: {text: UNSURE_LINE}});
        await waitFor(p, `document.querySelectorAll('#ownerQueueDialog[open] #ownerQueueList li').length===2`);
        await click(p, '#ownerQueueClose');
        await waitFor(p, `!document.querySelector('#ownerQueueDialog').open`);
        await seek(p, WORDS.zh.rows[2].slice(0, 8));
        await waitFor(p, `document.getElementById('words').textContent.trim()===${J(UNSURE_LINE)}`);
        assert.deepEqual(await acts(p), {readBtn: false, copyReply: false, speakBtn: false});
        assert.equal(await robot(p), false, 'no working robot on an unconfirmed page');
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueBtn .queueCount').textContent`), '2');
        await shot(p, 'after-chat-unconfirmed-390-light-zh');
        await fake.updateTurn(ids[2], {end: 'open', card: null, reply: {text: '周五的航班没有改时间。'}});
        await waitFor(p, `document.getElementById('words').textContent.trim()==='周五的航班没有改时间。'`);
        assert.equal(await evaluate(p, `document.body.dataset.pageOpen`), '1', 'corrected: an ordinary open turn');
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueBtn .queueCount').textContent`), '2', 'and not back in the queue');
        await fake.updateTurn(ids[2], {end: 'done'});
        await waitFor(p, `document.body.dataset.pageOpen==='0'`);
        results.push({case: 'unconfirmed'});
      } finally { await p.dispose(); }
    }

    // 3 · a page the Agent already writes on is listed but not offered; with nothing to remove 「清空」 hides too
    {
      const {p, ids} = await start('zh', 'light', 390);
      try {
        await fake.updateTurn(ids[0], {reply: {text: '正在处理这一条'}});
        await open(p);
        await waitFor(p, `!document.querySelector('[data-queue-drop="${ids[0]}"]')`);
        assert.deepEqual((await rowsNow(p)).map(r => r.drop), [null, String(ids[1]), String(ids[2])]);
        await fake.updateTurn(ids[1], {reply: {text: '这一条也开始了'}}); await fake.updateTurn(ids[2], {reply: {text: '这一条也开始了'}});
        await waitFor(p, `document.querySelectorAll('#ownerQueueList [data-queue-drop]').length===0`);
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueClear').hidden`), true);
        results.push({case: 'running-row-not-offered'});
      } finally { await p.dispose(); }
    }

    // 4 · the computer refuses (skipped) → the failure line, the control comes back; no answer → the same after 8 s
    {
      const {p, W, ids} = await start('zh', 'light', 390);
      try {
        fake.st.queueDrop = 'skip';
        await open(p);
        await click(p, `[data-queue-drop="${ids[0]}"]`);
        await waitFor(p, `document.body.innerText.includes(${J(W.fail)})`);
        assert.equal((await rowsNow(p)).length, 3);
        assert.equal((await rowsNow(p)).some(r => r.disabled), false);
        fake.st.queueDrop = 'mute';
        await waitFor(p, `!document.body.innerText.includes(${J(W.fail)})`, 6000);
        const t0 = Date.now();
        await click(p, `[data-queue-drop="${ids[2]}"]`);
        await waitFor(p, `document.querySelector('[data-queue-drop="${ids[2]}"]').disabled && document.querySelector('#ownerQueueClear').disabled && !document.querySelector('[data-queue-drop="${ids[0]}"]').disabled`);
        await waitFor(p, `document.body.innerText.includes(${J(W.fail)})`, 11000);
        const waited = Date.now() - t0;
        assert.ok(waited >= 7000 && waited <= 10500, 'gives up after about 8 s: ' + waited);
        assert.equal((await rowsNow(p)).some(r => r.disabled), false, 'controls come back');
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueClear').disabled`), false);
        results.push({case: 'refused+timeout', waited});
      } finally { await p.dispose(); }
    }

    // 5 · an older computer (no `queuedrop1`): the panel is exactly the old one
    {
      const {p} = await start('zh', 'light', 390, []);
      try {
        await open(p);
        assert.equal((await rowsNow(p)).length, 3);
        assert.equal(await evaluate(p, `document.querySelectorAll('#ownerQueueList [data-queue-drop]').length`), 0);
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueClear').hidden`), true);
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueClear').getBoundingClientRect().width`), 0);
        assert.deepEqual(drops(), []);
        results.push({case: 'older-host'});
      } finally { await p.dispose(); }
    }

    // 6 · this phone's own queued send (no host queue card yet) can be removed the same way and stops counting as queued
    {
      const {p, ids} = await start('zh', 'light', 390);
      try {
        fake.st.sayMode = 'queued';
        await evaluate(p, `(()=>{const i=document.getElementById('input');i.value='手机上刚发的排队消息';i.dispatchEvent(new Event('input'));document.getElementById('send').click();return true})()`);
        await waitFor(p, `document.querySelector('#ownerQueueBtn .queueCount')?.textContent==='4'`);
        const mine = fake.st.turns[fake.st.turns.length - 1].id;
        await open(p);
        await click(p, `[data-queue-drop="${mine}"]`);
        await waitFor(p, `document.querySelectorAll('#ownerQueueDialog[open] #ownerQueueList li').length===3`);
        assert.deepEqual(drops(), [{t: 'queue_drop', turns: [mine]}]);
        assert.deepEqual((await rowsNow(p)).map(r => r.drop), ids.map(String));
        await sleep(300);
        assert.equal(await evaluate(p, `document.getElementById('input').value`), '', 'a removed message does not come back into the field');
        assert.equal(await evaluate(p, `document.querySelector('#ownerQueueBtn .queueCount').textContent`), '3');
        results.push({case: 'own-send'});
      } finally { await p.dispose(); }
    }
    // 7 · say_state cancelled: `dropped` (another remote removed this phone's queued send) never refills the field or says
    //     「已撤回」; `stopped` keeps today's behaviour (the words come back)
    {
      const {p, W} = await start('zh', 'light', 390);
      try {
        fake.st.sayMode = 'queued';
        const send = async text => { await evaluate(p, `(()=>{const i=document.getElementById('input');i.value=${J(text)};i.dispatchEvent(new Event('input'));document.getElementById('send').click();return true})()`);
          await waitFor(p, `!document.getElementById('input').value`); await sleep(900); const [sid, q] = [...fake.st.queued].pop(); return {sid, turn: q.turn}; };
        const a = await send('别的遥控器删掉的这条');
        await waitFor(p, `document.querySelector('#ownerQueueBtn .queueCount')?.textContent==='4'`);
        fake.st.queued.delete(a.sid);
        await fake.send({t: 'say_state', sid: a.sid, s: 'cancelled', why: 'dropped'});
        await sleep(400);
        assert.equal(await evaluate(p, `document.getElementById('input').value`), '', 'dropped: the field stays empty');
        assert.equal(await evaluate(p, `document.body.innerText.includes(${J(T_WITHDRAWN)})`), false, 'dropped: no 已撤回 notice');
        await fake.updateTurn(a.turn, {end: 'stopped', card: {dropped: true}});
        await waitFor(p, `document.querySelector('#ownerQueueBtn .queueCount')?.textContent==='3'`);
        await seek(p, '别的遥控器删掉');
        await waitFor(p, `document.getElementById('words').textContent.trim()===${J(W.dropped)}`);
        assert.equal(await evaluate(p, `document.getElementById('input').value`), '');
        const b = await send('停下时退回来的这条');
        fake.st.queued.delete(b.sid);
        await fake.send({t: 'say_state', sid: b.sid, s: 'cancelled', why: 'stopped'});
        await waitFor(p, `document.getElementById('input').value==='停下时退回来的这条'`);
        assert.equal(await evaluate(p, `document.body.innerText.includes(${J(T_WITHDRAWN)})`), true, 'stopped: today\'s notice');
        assert.deepEqual(drops(), []);
        results.push({case: 'say_state-dropped-vs-stopped'});
      } finally { await p.dispose(); }
    }
    const rec = parityRecorder('web/test/p0378-queue-drop.mjs'); rec.record('p0378 queue drop', true); rec.write();   // AJ_PARITY_OUT overrides the file
    writeFileSync(join(out, 'result.json'), J({ok: true, results, shots}, null, 2));
  }
  console.log(TAG === 'before' ? 'p0378 before shots: ' + shots.join(', ') : `p0378 queue drop: PASS (${results.length} cases, ${shots.length} shots)`);
} finally {
  await browser.close(); await fake.stop(); await web.stop();
}
