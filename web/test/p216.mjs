// P216: 「分享成网页报告」 at the reply card's bottom-left — real page + local fake host, own headless Chromium (never :9222).
//   reply-bar-webreport   a filled Lotion disc alone on the left of the bottom row (not one of the right-hand .act icons,
//                         which stay exactly relay's five); 44 px target, 36 px disc, its left edge on the card's text
//                         inset; lines = currentColor = the card's own colour in idle / working / waiting / question /
//                         offline, light and dark; label + tooltip zh / en
//   webreport-latest      tap on the newest card → the field holds `/web-page-report ` with the cursor after it, the
//                         hint toast shows, nothing is sent, no quote strip
//   webreport-draft       words already in the field stay after the command; a second tap never doubles it
//   webreport-older       on an older card the tap also quotes that card (the 回复 strip); 发送 → say {reply_to: that id,
//                         text starting with the command}
// Shots: 5 states × phone 390×844 / desktop 1440×900 × light / dark (zh) + en → reports/qa/p216/screens/.
import assert from 'node:assert/strict';
import {contrast} from '../public/js/palette-color.js';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p216/screens');
const $ = (id) => `document.getElementById('${id}')`;
const CMD = '/web-page-report';
const S = { zh: { label: '分享成网页报告', hint: '分享哪部分、给谁看', from: '从这条回复开始' },
  en: { label: 'Share as a web report', hint: 'which part, and who should see it', from: 'Starting from this reply' } };
// [name, body dataset] — the colour the page shows; waiting / question / offline set the same attributes status messages do,
// without the sheet / offline card that would cover the row in a screenshot
const STATES = [['idle', { status: 'idle' }], ['working', { status: 'working' }], ['waiting', { status: 'waiting', kind: 'approval' }],
  ['question', { status: 'waiting', kind: 'question' }], ['offline', { status: 'unknown' }]];
export const CASES = { a: 'reply-bar-webreport', b: 'webreport-latest', c: 'webreport-draft', d: 'webreport-older' };
const box = (p, sel) => evaluate(p, `(()=>{const r=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,h:r.height}})()`);
async function tapEl(p, sel) {
  const r = await box(p, sel), x = r.l + r.w / 2, y = r.t + r.h / 2;
  await p.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  await p.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
}
const REPLY = { zh: '## 本周竞品\n结论：A 发了 2.0，B 降价 20%。\n\n| 公司 | 动态 |\n|---|---|\n| A | 2.0 |\n| B | 降价 |',
  en: '## This week\nA shipped 2.0, B cut prices by 20%.\n\n| Company | Move |\n|---|---|\n| A | 2.0 |\n| B | Price cut |' };

export async function runP216() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p216.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const check = (ok, label) => { assert.ok(ok, label); checks++; };
  const done = new Set();
  const step = async (c, f) => { try { await f(); if (!done.has(c)) { rec.record(CASES[c], true); done.add(c); } } catch (e) { rec.record(CASES[c], false); throw e; } };
  const runs = [['zh', 'light', 390, 844, true], ['zh', 'dark', 390, 844, true], ['zh', 'light', 1440, 900, false], ['zh', 'dark', 1440, 900, false],
    ['en', 'light', 390, 844, true], ['en', 'dark', 1440, 900, false]];
  try {
    for (const [lang, theme, w, h, touch] of runs) {
      const s = S[lang];
      fake.reset();
      const p = await newPage(B, w, h, theme, { touch, allow: [web.url, fake.relay + '/'] });
      const save = async (name) => writeFileSync(join(OUT, `${lang}-${theme}-${w}-${name}.png`), await shoot(p, ''));
      const press = (sel) => touch ? tapEl(p, sel) : evaluate(p, `document.querySelector(${JSON.stringify(sel)}).click()`);
      try {
        await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
        await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
        await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'AgentJ' });
        await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '早上的会说了什么' : 'What did the morning meeting decide?' },
          lang === 'zh' ? '三件事：定价、招聘、发布日期。' : 'Three things: pricing, hiring, the launch date.');
        await fake.addTurn({ k: 'phone', dev: 'other', name: 'iPhone', text: lang === 'zh' ? '把这周的竞品动态整理成表' : 'Put this week\'s competitor moves in a table' }, REPLY[lang]);
        await waitFor(p, `!${$('webReportBtn')}.disabled && ${$('words')}.textContent.includes('2.0')`);

        await step('a', async () => {
          check(await evaluate(p, `${$('rmbar')}.firstElementChild===${$('webReportBtn')} && !${$('webReportBtn')}.classList.contains('act')`), 'first in the row, not an .act');
          check(await evaluate(p, `${$('webReportBtn')}.getAttribute('aria-label')`) === s.label, 'aria-label ' + lang);
          check(await evaluate(p, `${$('webReportBtn')}.title`) === s.label, 'tooltip ' + lang);
          const acts = await evaluate(p, `[...${$('rmbar')}.querySelectorAll('.act')].filter(b=>!b.hidden).map(b=>b.id).join(',')`);
          check(acts === 'readBtn,speakBtn,fwdBtn,copyReply,replyBtn', 'right-hand icons unchanged: ' + acts);
          const b = await box(p, '#webReportBtn'), d = await box(p, '#webReportBtn svg'), rd = await box(p, '#readBtn');
          check(b.w >= 44 && b.h >= 44, `44 px target (${b.w}x${b.h})`);
          check(Math.abs(d.w - 36) < 0.5 && Math.abs(d.h - 36) < 0.5, `36 px disc (${d.w})`);
          check(b.r <= rd.l, 'left of the icons, no overlap');
          const inset = await evaluate(p, `${$('words')}.getBoundingClientRect().left-${$('rm')}.getBoundingClientRect().left`);
          const rm = await box(p, '#rm');
          check(Math.abs((d.l - rm.l) - inset) <= 1, `disc edge on the text inset (${(d.l - rm.l).toFixed(1)} vs ${inset.toFixed(1)})`);
          check(await evaluate(p, `document.documentElement.scrollWidth<=innerWidth`), 'no sideways overflow');
          for (const [name, ds] of STATES) {
            await evaluate(p, `(()=>{const b=document.body;b.dataset.status=${JSON.stringify(ds.status)};${ds.kind ? `b.dataset.kind=${JSON.stringify(ds.kind)}` : 'delete b.dataset.kind'};})()`);
            await sleep(60);
            const c = await evaluate(p, `(()=>{const b=${$('webReportBtn')},line=getComputedStyle(b.querySelector('g')).stroke,card=getComputedStyle(${$('rmbar')}).backgroundColor,disc=getComputedStyle(b.querySelector('.disc')).fill,ink=getComputedStyle(${$('readBtn')}).color;return {line,card,disc,ink}})()`);
            check(c.line === 'rgb(44, 43, 38)' && c.disc === 'rgb(250, 249, 245)' && contrast('#2c2b26','#faf9f5') >= 4.5, `${name}: P448 report glyph must be Noir on Lotion, never white on white (${JSON.stringify(c)})`);
            check(/250, 249, 245|0\\.98/.test(c.disc), `${name}: Lotion disc (${c.disc})`);
            await save('state-' + name);
          }
          await evaluate(p, `(()=>{document.body.dataset.status='idle';delete document.body.dataset.kind})()`);
        });

        await step('b', async () => {
          const before = fake.st.says.length;
          await press('#webReportBtn');
          await waitFor(p, `${$('input')}.value===${JSON.stringify(CMD + ' ')}`);
          check(await evaluate(p, `(document.activeElement===${$('input')})===${!touch} && ${$('input')}.selectionStart===${$('input')}.value.length`), 'touch preserves keyboard state; desktop focuses; cursor after the command');
          await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.hint)})`);
          check(!(await evaluate(p, `${$('toast')}.textContent`)).includes(s.from), 'newest card: plain hint');
          check(await evaluate(p, `${$('qbar')}.hidden`), 'newest card: no quote strip');
          check(!(await evaluate(p, `${$('send')}.disabled`)), 'send is ready');
          await sleep(300);
          check(fake.st.says.length === before, 'nothing sent');
          if (lang === 'zh' && theme === 'light') await save('tapped');
        });

        await step('c', async () => {
          await evaluate(p, `(()=>{const i=${$('input')};i.value='给老板看，只要表格';i.dispatchEvent(new Event('input'))})()`);
          await press('#webReportBtn');
          await waitFor(p, `${$('input')}.value.startsWith(${JSON.stringify(CMD + ' ')})`);
          check(await evaluate(p, `${$('input')}.value`) === CMD + ' 给老板看，只要表格', 'words kept after the command');
          await press('#webReportBtn'); await sleep(100);
          check(await evaluate(p, `${$('input')}.value`) === CMD + ' 给老板看，只要表格', 'second tap: not doubled');
          await evaluate(p, `(()=>{const i=${$('input')};i.value='';i.dispatchEvent(new Event('input'))})()`);
        });

        await step('d', async () => {
          await evaluate(p, `${$('pgPrev')}.click()`);
          await waitFor(p, `${$('words')}.textContent.includes(${JSON.stringify(lang === 'zh' ? '定价' : 'pricing')})`);
          const older = fake.st.turns[0].id;
          await press('#webReportBtn');
          await waitFor(p, `!${$('qbar')}.hidden && ${$('input')}.value===${JSON.stringify(CMD + ' ')}`);
          check(true, 'older card: quote strip + command');
          await waitFor(p, `${$('toast')}.textContent.includes(${JSON.stringify(s.from)})`);
          check(true, 'older card: 「从这条回复开始」 hint');
          if (theme === 'light') await save('older');
          await evaluate(p, `(()=>{const i=${$('input')};i.value+=${JSON.stringify(lang === 'zh' ? '公开就行' : 'public is fine')};i.dispatchEvent(new Event('input'))})()`);
          const before = fake.st.says.length;
          await evaluate(p, `${$('send')}.click()`);
          const t0 = Date.now();
          while (fake.st.says.length === before && Date.now() - t0 < 5000) await sleep(50);
          const m = fake.st.says.at(-1);
          check(m && m.reply_to === older && m.text.startsWith(CMD + ' '), 'say quotes the older card: ' + JSON.stringify(m && { reply_to: m.reply_to, text: m.text }));
        });
      } catch (e) { console.error('page problems:', p.problems, await evaluate(p, 'document.body.dataset.uiError||""').catch(() => '')); throw e; } finally { await p.dispose(); }
    }
    console.log(`p216: ${checks} checks passed`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP216();
