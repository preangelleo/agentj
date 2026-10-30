#!/usr/bin/env node
// P575 (#390) the state-change sounds + 全部完成, real page + local fake host, own headless Chromium (never :9222).
//   a) Settings → 阅读和声音: under 提示音音量 the group 「状态切换提示音」 (5 switches, each with a line-icon 试听) and
//      「全部完成提示音」; all on by default; switches are phone-local (aj.cueOff) and survive a reload; no slider of their own
//   b) after the first tap: idle → working → waiting (card) → working → idle with a task still in 在办 → 在办 empty play
//      working · waiting · working · idle · all-done — the card and its orange are one sound
//   c) a switched-off sound is skipped (all-done off → the green tick); 提示音音量 0 % silences every sound
// Shots: 390 / 1440 × 844, light / dark × zh / en → reports/qa/p575-fix-web/{before,after}/ (P575_BEFORE=<old public/> = before shots, a only)
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, tap, rect } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const BEFORE = process.env.P575_BEFORE || '';
const OUT = join(ROOT, `reports/qa/p575-fix-web/${BEFORE ? 'before' : 'after'}`);
const $ = (id) => `document.getElementById('${id}')`;
const hex = (n) => Array.from(webcrypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, '0')).join('');
const IDS = ['idle', 'working', 'waiting', 'question', 'offline', 'all-done'];
const SETTLE = 400 + 350;
const LAB = { zh: ['变绿：做完了', '变蓝：开始干活', '变橙：等你点头', '变紫：有问题问你', '变灰：离线', '全部完成提示音'],
  en: ['Turns green: done', 'Turns blue: started working', 'Turns orange: needs your OK', 'Turns purple: has a question', 'Turns grey: offline', 'All-done sound'] };

mkdirSync(OUT, { recursive: true });
const web = await startWebServer(BEFORE ? { publicDir: BEFORE } : {});
const fake = await startFakeHost();
const B = await launch({ args: ['--autoplay-policy=user-gesture-required'] });
let checks = 0;
const check = (ok, label) => { assert.ok(ok, label); checks++; };
const pair = async (p, lang) => {
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await evaluate(p, `${$('phone-env-dismiss')}?.click()`);
};
const openSet = async (p) => {
  await evaluate(p, `${$('settings')}.hidden && ${$('setBtn')}.click()`);
  await waitFor(p, `!${$('settings')}.hidden`);
  await evaluate(p, `${$('set-s-read')}.scrollIntoView({block:'start'})`);
  await sleep(300);
};
const tapEl = async (p, sel) => {
  await evaluate(p, `document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`); await sleep(150);
  const r = await rect(p, sel); await tap(p, r.x, r.y); await sleep(150);
};
const closeSet = async (p) => { await evaluate(p, `${$('settings')}.hidden || ${$('setBtn')}.click()`); await waitFor(p, `${$('settings')}.hidden`); };
const cues = (p) => evaluate(p, `({n: +(document.body.dataset.cues||0), last: document.body.dataset.cue||null})`);
const status = async (s, extra = {}) => { await fake.send({ t: 'status', s, agent: 'claude', name: 'AgentJ', ...extra }); };
const busy = (n) => fake.send({ t: 'busy', by: 'AgentJ', task_count: n, tasks: Array.from({ length: n }, (_, i) => ({ id: 'k' + i, who: null, kind: 'temp', label: 'task ' + i, since: 1760000000 + i, c: i })) });

try {
  for (const [lang, theme, width] of ['zh', 'en'].flatMap(lang => ['light', 'dark'].flatMap(theme => [390, 1440].map(width => [lang, theme, width])))) {
    fake.reset();
    fake.st.prefSet = true;
    fake.st.prefs = { appearance: { language: lang, theme: 'system' } };
    const p = await newPage(B, width, 844, theme, { touch: true, allow: [web.url, fake.relay + '/'] });
    await p.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.__AJ_TEST_CUES__ = true;' });
    const tag = `${lang}-${theme}-${width}`;
    try {
      await pair(p, lang);
      await status('idle');
      await openSet(p);
      writeFileSync(join(OUT, `${tag}-settings-sound.png`), await shoot(p, ''));
      if (!BEFORE) {
        await evaluate(p, `${$('set-cue-all-done')}.closest('.setrow').scrollIntoView({block:'end'})`); await sleep(200);
        writeFileSync(join(OUT, `${tag}-settings-sound-end.png`), await shoot(p, ''));
      }
      if (BEFORE) { await p.dispose(); continue; }

      // a) the rows
      for (const [i, id] of IDS.entries()) {
        const r = await evaluate(p, `(()=>{const b=${$('set-cue-' + id)};const pl=document.querySelector('[data-cue-play="${id}"]');const lab=${$('set-cue-' + id + '-lab')};
          const rb=b.getBoundingClientRect(),rp=pl.getBoundingClientRect();return {on:b.getAttribute('aria-checked'),lab:lab.textContent,name:b.getAttribute('aria-labelledby'),
          bw:rb.width,bh:rb.height,pw:rp.width,ph:rp.height,svg:!!pl.querySelector('svg path'),fill:getComputedStyle(pl.querySelector('svg')).fill,aria:pl.getAttribute('aria-label')}})()`);
        check(r.on === 'true', `${id} on by default ${tag}`);
        check(r.lab === LAB[lang][i], `${id} label ${JSON.stringify(r.lab)} ${tag}`);
        check(r.bh >= 44 && r.ph >= 44 && r.pw >= 44, `${id} tap targets ≥ 44 px ${tag}`);
        check(r.svg && r.fill === 'none', `${id} play is a line icon ${tag}`);
        check(!!r.aria && r.aria !== r.lab, `${id} play has its own name ${tag}`);
      }
      const geo = await evaluate(p, `(()=>{const v=${$('set-sfx-row')}.getBoundingClientRect(),h=${$('set-cues')}.getBoundingClientRect();
        return {after:h.top>=v.bottom-1, ranges:document.querySelectorAll('#set-s-read input[type=range]').length, over:document.documentElement.scrollWidth>innerWidth}})()`);
      check(geo.after, 'the group sits right under 提示音音量 ' + tag);
      check(geo.ranges === 1, 'one volume slider only ' + tag);
      check(!geo.over, 'no horizontal overflow ' + tag);
      const note = await evaluate(p, `document.querySelector('[data-i18n="cue.gStateNote"]').textContent`);
      check(lang === 'zh' ? note.includes('短促的提示音') && note.includes('页面在后台时不出声') && !note.includes('木质') : note.includes('short cue') && note.includes('background') && !note.includes('wooden'), 'current-language sound and hidden-page copy ' + tag);

      if (lang === 'zh' && theme === 'light' && width === 390) {
        // switches are phone-local and survive a reload
        await tapEl(p, '#set-cue-offline');
        check(await evaluate(p, `${$('set-cue-offline')}.getAttribute('aria-checked')`) === 'false', 'offline switched off');
        check(await evaluate(p, `localStorage.getItem('aj.cueOff')`) === 'offline', 'stored as aj.cueOff');
        await navigate(p, web.url + '?lang=zh'); await waitState(p, 'ready'); await status('idle');
        await openSet(p);
        check(await evaluate(p, `${$('set-cue-offline')}.getAttribute('aria-checked')`) === 'false', 'still off after a reload');
        await tapEl(p, '#set-cue-offline');
        const dbg = await evaluate(p, `(()=>{const r=${$('set-cue-offline')}.getBoundingClientRect();const e=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {k:localStorage.getItem('aj.cueOff'),on:${$('set-cue-offline')}.getAttribute('aria-checked'),top:r.top,hit:e&&(e.id||e.className)}})()`);
        check(dbg.k === null, 'all on again: key removed ' + JSON.stringify(dbg));
        const n0 = (await cues(p)).n;
        await tapEl(p, '[data-cue-play="question"]');
        const pv = await cues(p);
        check(pv.n === n0 + 1 && pv.last === 'question', '试听 plays that sound ' + JSON.stringify(pv));
        await closeSet(p);

        // b) the sequence (the taps above already unlocked the players)
        await status('idle'); await sleep(SETTLE);
        const base = (await cues(p)).n;
        await status('working'); await sleep(SETTLE);
        check((await cues(p)).last === 'working', 'blue → working');
        await sleep(2100);
        await status('waiting'); await fake.ask({ id: hex(16), tool: 'Bash', summary: 'git status', ttl: 120 });
        await waitFor(p, `document.body.dataset.sheet==='1'`); await sleep(SETTLE);
        let c = await cues(p);
        check(c.last === 'waiting' && c.n === base + 2, 'card + orange = one sound ' + JSON.stringify(c));
        writeFileSync(join(OUT, `${tag}-card-waiting.png`), await shoot(p, ''));
        await fake.askDone([...fake.st.asksOpen.keys()][0], 'allow');
        await busy(1);
        await status('working'); await sleep(SETTLE);
        await sleep(2100);
        await status('idle'); await sleep(SETTLE);
        c = await cues(p);
        check(c.last === 'idle', 'idle with a task in 在办 = the green tick ' + JSON.stringify(c));
        await busy(0); await sleep(SETTLE);
        c = await cues(p);
        check(c.last === 'all-done' && c.n === base + 5, '在办 empty → 全部完成 ' + JSON.stringify(c));
        await busy(0); await status('idle'); await sleep(SETTLE);
        check((await cues(p)).n === base + 5, 'no second 全部完成 without new work');

        // Ordinary replies must not inherit the preceding turn's background tasks.
        await sleep(2100);
        const ordinary = (await cues(p)).n;
        await status('working'); await sleep(SETTLE);
        await status('idle'); await sleep(SETTLE);
        c = await cues(p);
        check(c.last === 'idle' && c.n === ordinary + 2, 'ordinary reply after background work → green only ' + JSON.stringify(c));
        await sleep(2100);
        const during = (await cues(p)).n;
        await status('working'); await sleep(SETTLE);
        await busy(1); await sleep(SETTLE);
        await busy(0); await sleep(SETTLE);
        await status('idle'); await sleep(SETTLE);
        c = await cues(p);
        check(c.last === 'all-done' && c.n === during + 2, 'tasks start and finish while main stays blue → all-done ' + JSON.stringify(c));
        // Explicitly disabling the test hook leaves production playback free of DOM counters.
        await evaluate(p, `delete window.__AJ_TEST_CUES__; delete document.body.dataset.cue; delete document.body.dataset.cues`);
        await openSet(p); await tapEl(p, '[data-cue-play="question"]');
        check(await evaluate(p, `!('cue' in document.body.dataset) && !('cues' in document.body.dataset)`), 'production playback has no cue DOM diagnostics');
        await closeSet(p);

        // c) all-done switched off → the green tick; 0 % → silence
        await evaluate(p, `localStorage.setItem('aj.cueOff','all-done')`);
        await navigate(p, web.url + '?lang=zh'); await waitState(p, 'ready'); await status('idle'); await busy(0); await sleep(SETTLE);
        await tap(p, 200, 400); await sleep(100);   // the first tap after a load unlocks
        await status('working'); await busy(1); await sleep(SETTLE); await busy(0); await status('idle'); await sleep(SETTLE);
        c = await cues(p);
        check(c.last === 'idle' && c.n === 2, 'all-done off → the green tick plays instead ' + JSON.stringify(c));
        await evaluate(p, `localStorage.setItem('aj.sfxVolume','-15')`);
        await navigate(p, web.url + '?lang=zh'); await waitState(p, 'ready'); await status('idle'); await sleep(SETTLE);
        await tap(p, 200, 400); await sleep(100);
        await status('working'); await sleep(SETTLE); await sleep(2100); await status('idle'); await sleep(SETTLE);
        check((await cues(p)).n === 0, '提示音音量 0 % = every sound muted');
        await evaluate(p, `localStorage.removeItem('aj.cueOff'); localStorage.removeItem('aj.sfxVolume')`);
      }
    } finally { await p.dispose(); }
  }
  console.log(`p575 ${BEFORE ? 'before shots' : 'OK'} — ${checks} checks; shots → ${OUT}`);
} finally {
  await B.close().catch((e) => { if (e.code !== "ENOENT") throw e; });   // macOS: the reaper reads /proc (Linux only)
  await fake.stop(); await web.stop();
}
