#!/usr/bin/env node
// P363 (backlog #69 #70 #72 #73) · the UI batch on the real page + local fake host, own headless Chromium (never :9222).
//   keys-stacked   #69: on the shortcut sheet a row's combinations stand one per line (Ctrl+A · Ctrl+E · … no longer one wide
//                  block); the key column is as wide as its longest single combination, the descriptions get the rest.
//   om-tools       #73: the input card's 展开 / 收起 sits at the card's bottom right in both states, a copy button right
//                  beside it on its left (after the 📎 count); a tap copies the owner's words.
//   voice-asr      #72: a voice-only message shows the computer's transcript (folded: in place of 「（只有附件）」; open: under the
//                  attachment, foldable), a failed one says why; copy = the transcript; a read-back (reload) still has it.
//   five-search    #70: 扫码 / 搜索 / 设置 stand where the chat has them on 工作流 / 技能 / 记忆 / Agent J; the magnifier searches
//                  that page (cards), not the chat; Esc clears + closes it, the next Esc leaves the page; / opens it too;
//                  an open document and a Settings sub-page get the in-text find; on the chat it stays 搜索聊天记录.
//   wf-link        #70: a workflow link in a reply opens 工作流 with that company first and expanded, at the top.
// Shots (dark, zh × 390 iPhone / 412 Android / 800 tablet + 1440) → agentjarvis/reports/qa/p363/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, key, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'agentjarvis/reports/qa/p363/';
mkdirSync(OUT, { recursive: true });
const fx = JSON.parse(readFileSync(ROOT + 'host/tests/fixtures/p244_wf.json', 'utf8'));
const DOC = '# 说明\n\n' + Array.from({ length: 30 }, (_, i) => `第 ${i + 1} 段：这里讲 Reel 的剪辑流程。`).join('\n\n') + '\n';
const rec = parityRecorder('web/test/p363.mjs');
let web, fake, B, checks = 0;
const ev = evaluate;
const check = (v, label) => { assert.ok(v, label); checks++; };
const VIEWS = [[390, 844, 'iphone'], [412, 915, 'android'], [800, 1280, 'tablet'], [1440, 900, 'desktop']];
const box = (sel) => `(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height, shown: r.width > 0 && !e.closest('[hidden]') && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none' }; })()`;
const run = async (name, fn) => { let ok = false; try { await fn(); ok = true; } finally { rec.record(name, ok); } };
try {
  web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  fake = await startFakeHost();
  B = await launch();
  for (const [w, h, dev] of VIEWS) {
    fake.reset();
    fake.st.caps = ['wf1'];
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'wf_list') { for (const x of fx.wf_list) await send({ ...x, r: m.r }); return true; }
      if (m.t === 'wf_doc') { await send({ t: 'wf_doc', ok: true, r: m.r, folder: m.folder, name: m.name, title: m.name, text: DOC }); return true; }
      return false;
    };
    const p = await newPage(B, w, h, 'dark', { allow: [web.url, fake.relay + '/'], touch: w < 1000 });
    const snap = async (file) => writeFileSync(OUT + `${file}-${dev}.png`, await shoot(p, ''));
    const esc = async () => { await key(p, 'Escape'); await sleep(250); };
    const newest = async () => { await sleep(200); await ev(p, `document.activeElement && document.activeElement.blur()`); await key(p, 'g', { code: 'KeyG' }); await sleep(250); };
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=zh'));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await ev(p, `if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click()`);
      await ev(p, `document.getElementById('phone-env-dismiss')?.click()`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await ev(p, `window.__cp = []; navigator.clipboard.writeText = (t) => { window.__cp.push(t); return Promise.resolve(); }; true`);

      // ---------------------------------------------------------------- #69
      await run('keys-stacked', async () => {
        await ev(p, `document.activeElement && document.activeElement.blur()`);
        await key(p, '?', { code: 'Slash', shift: true, keyCode: 191 });
        await waitFor(p, `!document.getElementById('keys').hidden`);
        const g = await ev(p, `(() => { const out = []; for (const dt of document.querySelectorAll('#keysCols dt')) { const ls = [...dt.querySelectorAll(':scope > .kcombo')]; if (!dt.getClientRects().length) continue;
          const rs = ls.map(l => l.getBoundingClientRect()), dd = dt.nextElementSibling.getBoundingClientRect(), d = dt.getBoundingClientRect();
          out.push({ id: dt.dataset.ix, n: ls.length, stacked: rs.every((r, i) => !i || r.top >= rs[i - 1].bottom - 1), left: rs.every(r => Math.abs(r.left - rs[0].left) < 2), oneLine: rs.every(r => r.height < 40),
            widest: Math.max(...rs.map(r => r.width)), dt: d.width, dd: dd.width }); } return out; })()`);
        const multi = g.filter(x => x.n > 1);
        check(multi.length >= 3, 'rows with several combinations exist: ' + multi.length);
        check(multi.every(x => x.stacked && x.left), 'several combinations: one per line, left-aligned ' + JSON.stringify(multi.filter(x => !x.stacked || !x.left).map(x => x.id)));
        check(g.every(x => x.oneLine), 'a combination never wraps inside itself');
        const col = Math.max(...g.map(x => x.widest));
        check(g.every(x => x.dt <= col + 24), 'key column = the widest single combination');
        if (w >= 700) check(g.every(x => x.dd > x.dt), 'descriptions get more width than the keys');
        await ev(p, `[...document.querySelectorAll('#keysCols dt')].sort((a, b) => b.querySelectorAll('.kcombo').length - a.querySelectorAll('.kcombo').length)[0].scrollIntoView({block:'center'})`);
        await snap('69-keys');
        await esc();
        check(await ev(p, `document.getElementById('keys').hidden`), 'Esc closes the sheet');
      });

      // ---------------------------------------------------------------- #73
      await run('om-tools', async () => {
        const words = Array.from({ length: 12 }, (_, i) => `第 ${i + 1} 句：把这个需求写清楚一点，给手机端看。`).join('');
        await fake.addTurn({ k: 'phone', text: words, att: [{ name: 'a.png', mime: 'image/png', bytes: 10, kind: 'image' }, { name: 'b.pdf', mime: 'application/pdf', bytes: 10, kind: 'file' }] }, '收到。', 'done');
        await waitFor(p, `document.getElementById('omText').textContent.includes('第 12 句')`);
        await sleep(300);
        const tools = async () => ({ om: await ev(p, box('#om')), more: await ev(p, box('#omMore')), copy: await ev(p, box('#omCopy')), att: await ev(p, box('#omAttCount')) });
        let t = await tools();
        check(t.more.shown && t.copy.shown, 'folded: copy + expand shown');
        check(t.om.r - t.more.r <= 12 && t.om.b - t.more.b <= 14 && t.om.b - t.more.b >= -1, 'folded: expand at the bottom right ' + JSON.stringify([t.om.r - t.more.r, t.om.b - t.more.b]));
        check(t.copy.r <= t.more.l + 1 && t.more.l - t.copy.r <= 8 && Math.abs(t.copy.t - t.more.t) < 3, 'folded: copy just left of expand');
        check(t.att.shown && t.att.r <= t.copy.l + 1, 'folded: 📎 count left of copy');
        check(t.copy.w >= 44 && t.copy.h >= 44, 'copy is a 44 px target');
        await snap('73-folded');
        await ev(p, `document.getElementById('omMore').click()`);
        await sleep(350);
        t = await tools();
        await snap('73-open');
        const view = await ev(p, `(() => { const r = document.getElementById('om').getBoundingClientRect(); return Math.min(r.bottom, innerHeight); })()`);
        check(t.om.r - t.more.r <= 20 && view - t.more.b <= 28 && view - t.more.b >= -1, 'open: expand still at the bottom right ' + JSON.stringify([t.om.r - t.more.r, view - t.more.b]));
        check(t.copy.r <= t.more.l + 1 && Math.abs(t.copy.t - t.more.t) < 3, 'open: copy just left of collapse');
        await ev(p, `window.__cp.length = 0; document.getElementById('omCopy').click()`);
        await sleep(200);
        check(await ev(p, `window.__cp[0] === ${JSON.stringify(words)}`), 'copy = the owner\'s words');
        await ev(p, `document.getElementById('omMore').click()`);
      });

      // ---------------------------------------------------------------- #72
      await run('voice-asr', async () => {
        const said = '明天上午十点提醒我给律动音乐的乐师阿律回电话，顺便把上周的报告发给我。';
        const v = await fake.addTurn({ k: 'phone', text: '', att: [{ name: 'voice.webm', mime: 'audio/webm', bytes: 4000, kind: 'audio' }] }, '', 'open');
        await newest();
        await waitFor(p, `document.getElementById('omText').classList.contains('none')`);
        await fake.updateTurn(v.id, { src: { ...v.src, att: [{ ...v.src.att[0], asr: { ok: true, text: said, secs: 4.2 } }] }, reply: { text: '好的，已经记下。' }, end: 'done' });
        await waitFor(p, `document.getElementById('omText').textContent.includes('乐师阿律')`);
        check(await ev(p, `document.getElementById('omText').classList.contains('asr') && !document.getElementById('omText').classList.contains('none')`), 'folded: the transcript in place of 「只有附件」');
        await snap('72-folded');
        await ev(p, `document.getElementById('omMore').click()`);
        await sleep(300);
        check(await ev(p, `(() => { const d = document.querySelector('#omAtt .omasr'); return !!d && d.open && d.querySelector('.omasr-text').textContent.includes('乐师阿律') && d.previousElementSibling.classList.contains('omchip'); })()`), 'open: the transcript under the voice attachment');
        await snap('72-open');
        await ev(p, `document.querySelector('#omAtt .omasr summary').click()`);
        check(await ev(p, `!document.querySelector('#omAtt .omasr').open`), 'the transcript folds');
        await ev(p, `window.__cp.length = 0; document.getElementById('omCopy').click()`);
        await sleep(200);
        check(await ev(p, `window.__cp[0] === ${JSON.stringify(said)}`), 'copy of a voice note = its transcript');
        await ev(p, `document.getElementById('omMore').click()`);
        // a failed transcription says why
        await fake.addTurn({ k: 'phone', text: '', att: [{ name: 'v2.webm', mime: 'audio/webm', bytes: 4000, kind: 'audio', asr: { ok: false, why: 'not_installed' } }] }, '收到语音。', 'done');
        await newest();
        await waitFor(p, `document.getElementById('omText').textContent.includes('没转出文字')`);
        check(await ev(p, `document.getElementById('omText').textContent.includes('Agent J 主机上还没装转写')`), 'failed: the reason');
        // read-back: a fresh load gets the page from hist_page with its transcript
        await ev(p, `location.reload()`);
        await waitState(p, 'ready');
        await ev(p, `window.__cp = []; navigator.clipboard.writeText = (t) => { window.__cp.push(t); return Promise.resolve(); }; true`);
        await newest();
        await waitFor(p, `document.getElementById('omText').textContent.includes('没转出文字')`, 8000);
        await ev(p, `document.activeElement && document.activeElement.blur()`);
        await key(p, 'j', { code: 'KeyJ' });
        await waitFor(p, `document.getElementById('omText').textContent.includes('乐师阿律')`, 8000);
        check(await ev(p, `document.getElementById('omText').classList.contains('asr')`), 'read-back keeps the transcript');
      });

      // ---------------------------------------------------------------- #70
      await run('five-search', async () => {
        const trio = async () => ({ scan: await ev(p, box('#scanBtn')), srch: await ev(p, box('#srchBtn')), set: await ev(p, box('#setBtn')) });
        const chat = await trio();
        check(chat.srch.shown && chat.scan.shown && chat.set.shown, 'chat: 扫码 / 搜索 / 设置');
        for (const page of ['workflows', 'skills', 'memory', 'agentj']) {
          await ev(p, `(() => { const e = document.getElementById('orb'); e.dispatchEvent(new PointerEvent('pointerdown', {button: 0})); e.dispatchEvent(new PointerEvent('pointerup', {button: 0})); })()`);
          await waitFor(p, `!document.getElementById('five-menu').hidden`);
          await ev(p, `document.querySelector('.fp-menu-card[data-page="${page}"]').click()`);
          await waitFor(p, `!document.getElementById('five-page').hidden && document.querySelectorAll('#five-body .fp-card').length > 0`);
          await sleep(300);   // the header refit (topfit.js) runs a frame after the menu's magnifier comes back
          const here = await trio();
          for (const k of ['scan', 'srch', 'set']) check(here[k].shown && Math.abs(here[k].l - chat[k].l) < 1.5 && Math.abs(here[k].t - chat[k].t) < 1.5, `${page}: ${k} where the chat has it ${JSON.stringify([here[k], chat[k]])}`);
          await ev(p, `document.getElementById('srchBtn').click()`);
          await sleep(150);
          check(await ev(p, `!document.getElementById('five-find').hidden && document.getElementById('srch').hidden && document.activeElement.id === 'five-find-input'`), `${page}: the magnifier searches this page, not the chat`);
          const all = await ev(p, `document.querySelectorAll('#five-body .fp-card').length`);
          const q = { workflows: '阿律', skills: fx.wf_list[0].skills?.[0]?.name || 'a', memory: 'MEMORY', agentj: 'core' }[page];
          await ev(p, `(() => { const i = document.getElementById('five-find-input'); i.value = ${JSON.stringify(q)}; i.dispatchEvent(new Event('input')); })()`);
          await sleep(150);
          const n = await ev(p, `document.querySelectorAll('#five-body .fp-card').length`);
          check(n >= 1 && n <= all, `${page}: filtered ${all} → ${n}`);
          if (page === 'workflows') { check(n === 1 && await ev(p, `document.querySelector('#five-body .fp-card').dataset.folder === 'music-studio'`), 'workflows: CEO name finds the company'); await snap('70-workflows-find'); }
          await ev(p, `(() => { const i = document.getElementById('five-find-input'); i.value = 'zzzz-无此项'; i.dispatchEvent(new Event('input')); })()`);
          await sleep(100);
          check(await ev(p, `document.getElementById('five-find-n').textContent === '没找到' && document.querySelectorAll('#five-body .fp-card').length === 0`), `${page}: nothing found = one line`);
          await esc();
          check(await ev(p, `document.getElementById('five-find').hidden && !document.getElementById('five-page').hidden && document.querySelectorAll('#five-body .fp-card').length === ${all}`), `${page}: Esc clears + closes the field, the page stays`);
          await ev(p, `document.activeElement && document.activeElement.blur()`);
          await key(p, '/', { code: 'Slash', keyCode: 191 });
          await sleep(100);
          check(await ev(p, `!document.getElementById('five-find').hidden`), `${page}: / opens the search`);
          await esc(); await esc();
          check(await ev(p, `document.getElementById('five-page').hidden`), `${page}: the next Esc leaves the page`);
        }
        // an open document: in-text find
        await ev(p, `(() => { const e = document.getElementById('orb'); e.dispatchEvent(new PointerEvent('pointerdown', {button: 0})); e.dispatchEvent(new PointerEvent('pointerup', {button: 0})); })()`);
        await waitFor(p, `!document.getElementById('five-menu').hidden`);
        await ev(p, `document.querySelector('.fp-menu-card[data-page="agentj"]').click()`);
        await waitFor(p, `document.querySelectorAll('#five-body .fp-card').length > 0`);
        await ev(p, `document.querySelector('#five-body .fp-card .fp-card-head').click()`);
        await waitFor(p, `!document.getElementById('five-detail').hidden && document.getElementById('five-detail').textContent.includes('第 30 段')`);
        await ev(p, `document.getElementById('srchBtn').click()`);
        await sleep(150);
        check(await ev(p, `!!document.querySelector('#five-detail > .pfind:not([hidden])') && document.getElementById('srch').hidden`), 'document: the magnifier is the in-text find');
        await ev(p, `(() => { const i = document.querySelector('#five-detail > .pfind input'); i.value = '剪辑'; i.dispatchEvent(new Event('input')); })()`);
        await sleep(150);
        check(await ev(p, `document.querySelector('#five-detail > .pfind .pfind-n').textContent === '找到 30 处'`), 'document: every hit counted');
        await snap('70-doc-find');
        await esc();
        check(await ev(p, `!document.getElementById('five-detail').hidden && document.querySelector('#five-detail > .pfind').hidden`), 'document: Esc closes the find first');
        await esc(); await esc();
        // Settings → 遥控器管理: the sub-page searches itself
        await ev(p, `document.getElementById('setBtn').click()`);
        await waitFor(p, `!document.getElementById('settings').hidden`);
        await ev(p, `document.getElementById('set-remotes-open').click()`);
        await waitFor(p, `!document.getElementById('set-remotes').hidden`);
        await ev(p, `document.getElementById('srchBtn').click()`);
        await sleep(150);
        check(await ev(p, `!document.getElementById('set-remotes').hidden && !!document.querySelector('#set-remotes > .pfind:not([hidden])')`), 'Settings sub-page: searches in place (no jump to the main page)');
        await snap('70-settings-sub-find');
        await esc();
        check(await ev(p, `!document.getElementById('set-remotes').hidden && document.querySelector('#set-remotes > .pfind').hidden`), 'Settings sub-page: Esc closes the find first');
        await ev(p, `document.getElementById('settings').hidden || (window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})), true)`);
        await esc(); await esc();
        // a menu page (活动): the in-text find under its header row
        await ev(p, `document.getElementById('open-act').click()`);
        await waitFor(p, `!document.getElementById('act-view').hidden`);
        await sleep(200);
        const onAct = await trio();
        for (const k of ['scan', 'srch', 'set']) check(onAct[k].shown && Math.abs(onAct[k].l - chat[k].l) < 1.5, `menu page: ${k} where the chat has it ${JSON.stringify([onAct[k], chat[k]])}`);
        await ev(p, `document.getElementById('srchBtn').click()`);
        await sleep(150);
        check(await ev(p, `(() => { const f = document.querySelector('#act-view > .pfind'); return !!f && !f.hidden && f.previousElementSibling.classList.contains('menuhead') && document.getElementById('srch').hidden; })()`), 'menu page: the in-text find under its header');
        await ev(p, `(() => { const i = document.querySelector('#act-view > .pfind input'); i.value = '30 天'; i.dispatchEvent(new Event('input')); })()`);
        await sleep(100);
        check(await ev(p, `/找到 \\d+ 处/.test(document.querySelector('#act-view > .pfind .pfind-n').textContent)`), 'menu page: hits counted');
        await snap('70-menu-page-find');
        await esc();
        check(await ev(p, `!document.getElementById('act-view').hidden && document.querySelector('#act-view > .pfind').hidden`), 'menu page: Esc closes the find first');
        await esc();
        await waitFor(p, `document.getElementById('act-view').hidden`);
        if (!(await ev(p, `document.getElementById('settings').hidden`))) await esc();
        // the chat keeps 搜索聊天记录
        await ev(p, `document.getElementById('srchBtn').click()`);
        await sleep(150);
        check(await ev(p, `!document.getElementById('srch').hidden`), 'chat: the magnifier is still 搜索聊天记录');
        await esc();
      });

      // ---------------------------------------------------------------- #70 workflow link
      await run('wf-link', async () => {
        await fake.addTurn({ k: 'phone', text: '进度？' }, '我去问了 video-studio 那边，导演阿影说明天交片。', 'done');
        await newest();
        await waitFor(p, `!!document.querySelector('.fp-link-workflow')`);
        await ev(p, `[...document.querySelectorAll('.fp-link-workflow')].find(b => b.textContent === 'video-studio').click()`);
        await waitFor(p, `!document.getElementById('five-page').hidden && !!document.querySelector('#five-body .fp-card[data-open]')`);
        await sleep(200);
        const first = await ev(p, `(() => { const c = document.querySelector('#five-body .fp-card'); return { folder: c.dataset.folder, open: c.dataset.open === '1', gap: c.getBoundingClientRect().top - document.querySelector('#five-page .fp-sticky').getBoundingClientRect().bottom, scrolled: document.getElementById('five-page').scrollTop, max: document.getElementById('five-page').scrollHeight - document.getElementById('five-page').clientHeight }; })()`);
        check(first.folder === 'video-studio' && first.open, 'the linked company first and open ' + JSON.stringify(first));
        check(Math.abs(first.gap - 12) < 2 || (first.gap > 12 && first.scrolled >= first.max - 1)   /* a short list cannot lift it further */, 'its card right under the header ' + JSON.stringify(first));
        await snap('70-wf-link');
        await esc();
      });
      check(p.problems.length === 0, 'no page errors: ' + p.problems.join(' | '));
    } finally { await p.dispose?.(); }
  }
  console.log(`p363: ${checks} checks ok → ${OUT}`);
} catch (e) {
  console.error(e); process.exitCode = 1;
} finally {
  rec.write();
  await Promise.allSettled([B?.close(), fake?.stop(), web?.stop()]);
}
process.exit(process.exitCode || 0);
