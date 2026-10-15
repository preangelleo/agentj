// P285: the reply card's bottom-left disc is a hot key — real page + local fake host, own headless Chromium (never :9222).
//   hot-sheet          a hold on the disc opens the 快捷技能键 sheet from the bottom: the binding, what a tap does, 「换成别的」 and
//                      the 「/」 list's commands + the host's menu items + skills, each a name and a line; search filters;
//                      nothing is sent, the field is untouched; Esc closes it
//   hot-bind           pick a row → the confirm row → 确认绑定 → pref_set ui.card_hot_action; the sheet closes, the button's
//                      label names the binding and its icon is byte-for-byte the same
//   hot-run            bound to /status, a tap sends the command at once (no draft); W does the same
//   hot-clear          bound to /clear, a tap asks first (the same confirmation as typing /clear); 取消 sends nothing, 确定 → slash
//   hot-skill          bound to a skill, a tap on an older card drafts `/<skill> ` and quotes that card; never sent
//   hot-reset          「恢复默认」 writes web-page-report back and the label returns to 分享成网页报告
//   hot-old-host       a host that does not list the key: the confirm row shows `agentj config set ui.card_hot_action …`
// Shots: sheet + confirm, phone 390×844 / desktop 1440×900, zh + en → reports/qa/p285/screens/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p285/screens');
const $ = (id) => `document.getElementById('${id}')`;
const KEY = 'ui.card_hot_action';
const S = { zh: { label: '分享成网页报告', title: '快捷技能键', bound: '快捷技能键：/status', yes: '确认绑定' },
  en: { label: 'Share as a web report', title: 'Hot key', bound: 'Hot key: /status', yes: 'Bind it' } };
const MENU = { source: 'file', items: [{ cmd: '/weekly-brief', desc: '整理本周竞品简报', group: '我的命令' }],
  skills: [{ cmd: 'agentj-recall', desc: 'Recall what was decided earlier' }, { cmd: 'web-page-report', desc: 'Share a reply as a web page' }],
  upgrade: { current: '0.20.4a1', latest: '0.20.4a1' } };
export const CASES = { a: 'hot-sheet', b: 'hot-bind', c: 'hot-run', d: 'hot-clear', e: 'hot-skill', f: 'hot-reset', g: 'hot-old-host' };
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);

export async function runP285() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p285.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const done = new Set();
  const step = async (c, f) => { try { await f(); if (!done.has(c)) { rec.record(CASES[c], true); done.add(c); } } catch (e) { rec.record(CASES[c], false); throw e; } };
  // [lang, theme, w, h, touch, full]: every case on the zh phone and desktop; the en pages check labels + shots
  const runs = [['zh', 'light', 390, 844, true, true], ['zh', 'dark', 1440, 900, false, true], ['en', 'dark', 390, 844, true, false], ['en', 'light', 1440, 900, false, false]];
  try {
    for (const [lang, theme, w, h, touch, full] of runs) {
      const s = S[lang];
      fake.reset();
      fake.st.menu = MENU; fake.st.prefSet = true; fake.st.version = '0.20.4a1';
      fake.st.prefs = { appearance: { language: lang, theme: 'system' }, ui: { card_hot_action: 'web-page-report' } };
      const p = await newPage(B, w, h, theme, { touch, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-${w}-${name}.png`), await shoot(p, ''));
      const tapAt = async (sel) => {
        const r = await box(p, sel), x = r.l + r.w / 2, y = r.t + r.h / 2;
        if (touch) { await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }); await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); }
        else { await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }); await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }); await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }); }
      };
      const hold = async (sel) => {
        const r = await box(p, sel), x = r.l + r.w / 2, y = r.t + r.h / 2;
        if (touch) { await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] }); await sleep(750); await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); }
        else { await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }); await p.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }); await sleep(750); await p.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }); }
        await sleep(150);
      };
      const rowSel = (n) => `#hotList button[data-hot="${n}"]`;
      const bind = async (n) => {
        await hold('#webReportBtn');
        await waitFor(p, `!${$('hotMenu')}.hidden && !!document.querySelector(${JSON.stringify(rowSel(n))})`);
        await sleep(900);   // longpress.js eats the click right after a hold (EAT_MS)
        await evaluate(p, `document.querySelector(${JSON.stringify(rowSel(n))}).click()`);
        await waitFor(p, `!${$('hotAsk')}.hidden`);
        await evaluate(p, `${$('hotYes')}.click()`);
        await waitFor(p, `${$('hotMenu')}.hidden && ${$('webReportBtn')}.dataset.hot===${JSON.stringify(n)}`);
      };
      const slashes = () => (fake.st.slashes || []).map((m) => m.cmd);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ' });
        await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '早上的会说了什么' : 'What did the morning meeting decide?' },
          lang === 'zh' ? '三件事：定价、招聘、发布日期。' : 'Three things: pricing, hiring, the launch date.');
        await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '这周竞品' : 'Competitors this week' }, lang === 'zh' ? 'A 发了 2.0，B 降价 20%。' : 'A shipped 2.0, B cut prices by 20%.');
        await waitFor(p, `!${$('webReportBtn')}.disabled && ${$('words')}.textContent.includes('2.0')`);
        const icon0 = await evaluate(p, `${$('webReportBtn')}.innerHTML`);

        await step('a', async () => {
          check(await evaluate(p, `${$('webReportBtn')}.getAttribute('aria-label')`) === s.label, 'default label ' + lang);
          const says0 = fake.st.says.length;
          await hold('#webReportBtn');
          await waitFor(p, `!${$('hotMenu')}.hidden`);
          check(await evaluate(p, `${$('hotTitle')}.textContent`) === s.title, 'title');
          check((await evaluate(p, `${$('hotNow')}.textContent`)).includes('/web-page-report'), 'names the binding');
          await waitFor(p, `!!document.querySelector('#hotList button[data-hot="agentj-recall"]')`);   // the host's skills, re-read on open
          const names = await evaluate(p, `[...document.querySelectorAll('#hotList button[data-hot]')].map(b=>b.dataset.hot)`);
          for (const n of ['compact', 'clear', 'status', 'context', 'customize', 'meeting-notes', 'weekly-brief', 'agentj-recall'])
            check(names.includes(n), 'lists ' + n);
          check(!names.includes('update'), '/update is not a binding');
          check(await evaluate(p, `[...document.querySelectorAll('#hotList button[data-hot]')].every(b=>b.querySelector('b').textContent && b.querySelector('span'))`), 'name + line on every row');
          check(await evaluate(p, `document.querySelector('#hotList button[data-hot="web-page-report"]').classList.contains('cur')`), 'current marked');
          const sh = await box(p, '#hotSheet');
          check(sh.b <= h + 1 && sh.b >= h - 60 && sh.t >= 0 && sh.w <= 482, `bottom sheet in view (${JSON.stringify(sh)})`);
          check(await evaluate(p, `document.documentElement.scrollWidth<=innerWidth`), 'no sideways overflow');
          await save('sheet');
          await evaluate(p, `(()=>{const q=${$('hotQ')};q.value='stat';q.dispatchEvent(new Event('input'))})()`);
          const hits = await evaluate(p, `[...document.querySelectorAll('#hotList button[data-hot]')].map(b=>b.dataset.hot)`);
          check(hits.includes('status') && !hits.includes('compact'), 'search filters: ' + hits);
          await evaluate(p, `(()=>{const q=${$('hotQ')};q.value='zzzz';q.dispatchEvent(new Event('input'))})()`);
          check(await evaluate(p, `!!document.querySelector('#hotList .hotnone')`), 'no match line');
          await evaluate(p, `(()=>{const q=${$('hotQ')};q.value='';q.dispatchEvent(new Event('input'))})()`);
          await evaluate(p, `document.querySelector(${JSON.stringify(rowSel('status'))}).click()`);
          await waitFor(p, `!${$('hotAsk')}.hidden`);
          check((await evaluate(p, `${$('hotAskT')}.textContent`)).includes('/status'), 'confirm names the pick');
          check(await evaluate(p, `${$('hotYes')}.textContent`) === s.yes, 'confirm button');
          await save('confirm');
          await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);
          await waitFor(p, `${$('hotMenu')}.hidden`);
          check(fake.st.says.length === says0 && !(fake.st.prefSets || []).length, 'nothing sent, nothing saved');
          check(await evaluate(p, `${$('input')}.value`) === '', 'field untouched');
        });
        if (!full) {
          await bind('status');
          check(await evaluate(p, `${$('webReportBtn')}.getAttribute('aria-label')`) === s.bound, 'bound label ' + lang);
          continue;
        }

        await step('b', async () => {
          await bind('status');
          const last = fake.st.prefSets.at(-1);
          check(last && last.key === KEY && last.value === 'status', 'pref_set ' + JSON.stringify(last));
          check(await evaluate(p, `${$('webReportBtn')}.getAttribute('aria-label')`) === s.bound, 'label names the binding');
          check(await evaluate(p, `${$('webReportBtn')}.title`) === s.bound, 'tooltip names the binding');
          check(await evaluate(p, `${$('webReportBtn')}.innerHTML`) === icon0, 'icon unchanged');
          await save('bound');
        });

        await step('c', async () => {
          const n0 = slashes().length, says0 = fake.st.says.length;
          await tapAt('#webReportBtn');
          const t0 = Date.now(); while (slashes().length === n0 && Date.now() - t0 < 4000) await sleep(50);
          check(slashes().at(-1) === 'status', 'slash status sent: ' + slashes());
          check(await evaluate(p, `${$('input')}.value`) === '' && fake.st.says.length === says0, 'no draft, no say');
          await evaluate(p, `${$('pgLatest')}?.click()`); await sleep(200);
          await evaluate(p, `document.activeElement && document.activeElement.blur()`);
          const n1 = slashes().length;
          await evaluate(p, `document.dispatchEvent(new KeyboardEvent('keydown',{key:'W',shiftKey:true,bubbles:true}))`);
          const t1 = Date.now(); while (slashes().length === n1 && Date.now() - t1 < 4000) await sleep(50);
          check(slashes().at(-1) === 'status', 'W runs the hot key');
        });

        await step('d', async () => {
          await bind('clear');
          const n0 = slashes().length;
          await tapAt('#webReportBtn');
          await waitFor(p, `!${$('confirm')}.hidden`);
          check(true, '/clear asks first');
          if (w < 500) await save('clear-confirm');
          await evaluate(p, `${$('confirm-no')}.click()`); await sleep(400);
          check(slashes().length === n0, 'cancel: nothing sent');
          await tapAt('#webReportBtn');
          await waitFor(p, `!${$('confirm')}.hidden`);
          await evaluate(p, `${$('confirm-yes')}.click()`);
          const t0 = Date.now(); while (slashes().length === n0 && Date.now() - t0 < 4000) await sleep(50);
          check(slashes().at(-1) === 'clear', 'confirmed: /clear sent');
        });

        await step('e', async () => {
          await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: 'again' }, lang === 'zh' ? '新的一条：上线日期改到周五。' : 'New one: launch moved to Friday.');
          await bind('agentj-recall');
          await evaluate(p, `${$('pgPrev')}.click()`); await sleep(250);
          const says0 = fake.st.says.length;
          await tapAt('#webReportBtn');
          await waitFor(p, `${$('input')}.value==='/agentj-recall '`);
          check(!(await evaluate(p, `${$('qbar')}.hidden`)), 'older card quoted');
          await tapAt('#webReportBtn'); await sleep(150);
          check(await evaluate(p, `${$('input')}.value`) === '/agentj-recall ', 'not doubled');
          await sleep(300);
          check(fake.st.says.length === says0, 'never sent');
          if (w < 500) await save('skill-draft');
          await evaluate(p, `(()=>{const i=${$('input')};i.value='';i.dispatchEvent(new Event('input'))})()`);
          await evaluate(p, `${$('qbX')}.click()`);
        });

        await step('f', async () => {
          await hold('#webReportBtn');
          await waitFor(p, `!${$('hotMenu')}.hidden && !!document.querySelector('#hotList .hotreset')`);
          await sleep(900);
          await evaluate(p, `document.querySelector('#hotList .hotreset').click()`);
          await waitFor(p, `!${$('hotAsk')}.hidden`);
          await evaluate(p, `${$('hotYes')}.click()`);
          await waitFor(p, `${$('hotMenu')}.hidden && ${$('webReportBtn')}.dataset.hot==='web-page-report'`);
          check(fake.st.prefSets.at(-1).value === 'web-page-report', 'default written back');
          check(await evaluate(p, `${$('webReportBtn')}.getAttribute('aria-label')`) === s.label, 'label back');
        });

        await step('g', async () => {
          await fake.send({ t: 'preferences', value: fake.st.prefs, problem: null, host: { version: '0.20.3a1', language_at: 0, settable: ['appearance.language'], devices: [] } });
          await sleep(200);
          const n0 = fake.st.prefSets.length;
          await hold('#webReportBtn');
          await waitFor(p, `!${$('hotMenu')}.hidden`);
          await sleep(900);
          await evaluate(p, `document.querySelector(${JSON.stringify(rowSel('compact'))}).click()`);
          await waitFor(p, `!${$('hotAsk')}.hidden`);
          await evaluate(p, `${$('hotYes')}.click()`);
          await waitFor(p, `${$('hotAskW')}.textContent.includes('agentj config set ${KEY} compact')`);
          check(fake.st.prefSets.length === n0, 'nothing sent to an older host');
          check(!(await evaluate(p, `${$('hotMenu')}.hidden`)), 'sheet stays open with the command');
          if (w < 500) await save('old-host');
          await evaluate(p, `${$('hotClose')}.click()`);
        });
      } catch (e) { console.error('page problems:', p.problems, await evaluate(p, 'document.body.dataset.uiError||""').catch(() => '')); throw e; } finally { await p.dispose(); }
    }
    console.log(`p285: ${checks} checks passed`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP285();
