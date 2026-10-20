#!/usr/bin/env node
// P393 (#124 「很多页面的搜索没用」) · one end-to-end check per search surface on the real page + local Noise fake host, own
// headless Chromium (never :9222). For every surface a word known to be on that page is typed and the test asserts the page
// itself answers: the list is filtered in place, or the hit is marked (CSS Highlight API) and scrolled into view, or — for a
// result list — a tap opens the right page, scrolls to the row and flashes it.
//   chat          搜索聊天记录: a word in an older page → tap the hit → the deck shows that page and flashes it
//   workflows / skills / memory / agentj   five pages: the cards are filtered
//   document      an open document: in-text find, every hit counted, the current one marked
//   card-notes    记事本 (one record per card): 「免费」 keeps exactly the record with 「永久免费码」, marks it; Esc brings all back
//   card-todo / card-automation   the same over checklist rows and automations
//   menu pages    记忆 / 活动 / 后台任务 / 模型和 API Key / 好友 / Bot / 首次准备: in-text find (a word from the page itself)
//   settings      「Telegram」: the page filters to the Telegram row (other sections step aside), the word is marked; the hit
//                 in the list → the row scrolled into view and flashed; a sub-page row in the list opens that sub-page there
//   settings-sub  a Settings sub-page (邮件通道) searches itself in place
//   contacts      通讯录: the magnifier goes to the address book's own search (the computer searches the whole book)
//   keys          快捷键 sheet: rows filtered
//   hotkey        快捷技能键 sheet: rows filtered
// Shots 390 + 1440 × dark + light (zh; en at two of them) → reports/qa/p393/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot, key, sleep } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = ROOT + 'reports/qa/p393/';
mkdirSync(OUT, { recursive: true });
const fx = JSON.parse(readFileSync(ROOT + 'host/tests/fixtures/p244_wf.json', 'utf8'));
const DOC = '# 说明\n\n' + Array.from({ length: 30 }, (_, i) => `第 ${i + 1} 段：这里讲 Reel 的剪辑流程。`).join('\n\n') + '\n';
const ev = evaluate, q = JSON.stringify;
let checks = 0, passed = false;
const rec = parityRecorder('web/test/p393_search.mjs');
const results = [];
const check = (v, label) => { assert.ok(v, label); checks++; results.push(label); };
const RUNS = [[390, 844, 'dark', 'zh'], [390, 844, 'light', 'zh'], [1440, 900, 'dark', 'zh'], [1440, 900, 'light', 'zh'], [390, 844, 'dark', 'en'], [1440, 900, 'light', 'en']];
const BOOK = [
  { id: 'c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6', slot: 0, name: '王董', nicks: [], emails: [{ v: 'boss@corp.example', label: '' }], phones: [], letter: 'W' },
  { id: 'c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6', name: '李雷', nicks: ['Lei'], emails: [{ v: 'lei@friend.example', label: 'work' }], phones: [], letter: 'L' },
  { id: 'c3a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6', name: '张伟', nicks: [], emails: [], phones: [{ v: '+86 139 0000 0002', label: 'mobile' }], letter: 'Z' },
];
const type = (sel, text) => `(() => { const i = document.querySelector(${q(sel)}); i.value = ${q(text)}; i.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`;
// the CSS highlight 'aj-find-cur' (the current hit) exists and its text is on screen
const curShown = `(() => { const h = CSS.highlights.get('aj-find-cur'); if (!h || !h.size) return false; const r = [...h][0].getBoundingClientRect(); return r.height > 0 && r.top >= 0 && r.bottom <= innerHeight; })()`;
const marked = `(() => { const h = CSS.highlights.get('aj-find'); return h ? h.size : 0; })()`;
const inView = (sel) => `(() => { const n = document.querySelector(${q(sel)}); if (!n) return false; const r = n.getBoundingClientRect(); return r.height > 0 && r.top < innerHeight && r.bottom > 0; })()`;

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const fake = await startFakeHost();
const B = await launch();
try {
  for (const [w, h, scheme, lang] of RUNS) {
    const zh = lang === 'zh', tag = `${w}-${lang}-${scheme}`;
    fake.reset();
    fake.st.caps = ['jcards1', 'wf1', 'contacts'];
    fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
    fake.st.got = [];
    const cards = [
      { id: 'notes', name: { zh: '记事本', en: 'Notebook' }, desc: { zh: '常用地址、电话和备忘', en: 'Addresses and memos' }, icon: 'note', accent: 'emerald-oasis', on: true, official: true },
      { id: 'todo', name: { zh: '待办', en: 'To-do' }, desc: { zh: '要做的事', en: 'Things to do' }, icon: 'checklist', accent: 'magic-carpet', on: true, official: true },
      { id: 'automation', name: { zh: '自动化', en: 'Automation' }, desc: { zh: '后台运行的自动化', en: 'Scheduled work' }, icon: 'calendar', accent: 'magic-carpet', on: true, official: true },
    ];
    const entries = [
      { id: 'free', text: zh ? '永久免费码：AJ-FREE-0000（朋友送的，别外传）' : 'Lifetime free code: AJ-FREE-0000 (a gift, keep private)', editable: true },
      { id: 'addr', text: zh ? '预约地点：花园餐厅\n电话：010-5550-0000' : 'Meeting: Garden restaurant\nPhone: 010-5550-0000', editable: true },
      { id: 'wifi', text: zh ? '家里 Wi-Fi 在客厅路由器背面' : 'Home Wi-Fi is on the back of the living-room router', editable: true },
    ];
    fake.st.onApp = async (c, m, send) => {
      const reply = (t, v) => send({ t, r: m.r, ...v }).then(() => true);
      if (m.t === 'wf_list') { for (const x of fx.wf_list) await send({ ...x, r: m.r }); return true; }
      if (m.t === 'wf_doc') return reply('wf_doc', { ok: true, folder: m.folder, name: m.name, title: m.name, text: DOC });
      if (m.t === 'jcard_list') return reply('jcard_items', { cards, colors: {} });
      if (m.t === 'jcard_get') {
        if (m.id === 'notes') return reply('jcard_get', { ok: true, id: 'notes', template: 'notes', entries, revision: 'r1' });
        if (m.id === 'todo') return reply('jcard_get', { ok: true, id: 'todo', template: 'checklist', items: [{ id: 'milk', text: zh ? '买牛奶' : 'Buy milk', done: false }, { id: 'call', text: zh ? '给阿律回电话' : 'Call Lu back', done: false }, { id: 'tax', text: zh ? '交季度税' : 'Pay the quarterly tax', done: true }] });
        return reply('jcard_get', { ok: true, id: 'automation', template: 'automation', items: [
          { id: 'morning-brief', name: 'morning-brief', source: 'systemd', schedule: [{ k: 'cal', v: '*-*-* 08:00:00' }], description: zh ? '整理每日新闻摘要' : 'Daily news briefing', prompt: '/agentj-automation @morning-brief' },
          { id: 'weekly-report', name: 'weekly-report', source: 'systemd', schedule: [{ k: 'cal', v: 'Mon *-*-* 09:00:00' }], description: zh ? '每周一汇总上周进度' : 'Monday progress summary', prompt: '/agentj-automation @weekly-report' }] });
      }
      if (/^ct_/.test(m.t)) {
        fake.st.got.push(m);
        if (m.t !== 'ct_list') return reply('ct_res', { ok: true });
        const s = (m.q || '').toLowerCase(), list = s ? BOOK.filter((x) => JSON.stringify(x).toLowerCase().includes(s)) : BOOK;
        return reply('ct_res', { ok: true, contacts: list, count: list.length, me: ['boss@corp.example'], owner: BOOK[0], mail: { ready: true } });
      }
      return false;
    };
    const p = await newPage(B, w, h, scheme, { touch: w === 390, allow: [web.url, fake.relay + '/'] });
    const snap = async (name) => writeFileSync(OUT + `web-${name}-${tag}.png`, await shoot(p, ''));
    const esc = async () => { await key(p, 'Escape'); await sleep(250); };
    const go = (page) => ev(p, `import('./js/five-pages.js').then(m => { m.go(${q(page)}); return true; })`);
    const openCard = async (id) => { await ev(p, `import('./js/five-pages.js').then(m => m.openTarget(${q(id)}))`); await waitFor(p, `!!document.querySelector('.jc-body[data-card=${id}] :is(.jc-entry,.jc-item)')`); await sleep(100); };
    const magnifier = async () => { await ev(p, `document.getElementById('srchBtn').click()`); await sleep(150); };
    try {
      await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
      await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
      await ev(p, `document.getElementById('phone-env-dismiss')?.click(); if(!document.getElementById('a2hs').hidden)document.getElementById('a2hs-ok').click(); true`);
      await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
      await ev(p, `import('./js/jcards.js').then(m => m.refresh()).then(() => true)`);
      check(await ev(p, `document.documentElement.dataset.theme === ${q(scheme)}`), 'theme ' + tag);

      // ---------------------------------------------------------------- chat
      const word = zh ? '季度预算' : 'quarterly budget';
      await fake.addTurn({ k: 'phone', text: zh ? '请把季度预算表整理一下' : 'Please tidy up the quarterly budget sheet' }, zh ? '好的，已整理。' : 'Done.', 'done');
      await fake.addTurn({ k: 'phone', text: zh ? '明天几点开会？' : 'When is the meeting tomorrow?' }, zh ? '上午十点。' : '10 am.', 'done');
      await waitFor(p, `document.getElementById('omText').textContent.includes(${q(zh ? '明天几点' : 'meeting tomorrow')})`);
      await magnifier();
      check(await ev(p, `!document.getElementById('srch').hidden`), 'chat: the magnifier opens 搜索聊天记录 ' + tag);
      await ev(p, type('#srchInput', word));
      await waitFor(p, `document.querySelectorAll('#srchList .srhit').length >= 1`);
      await ev(p, `document.querySelector('#srchList .srhit').click()`);
      await waitFor(p, `document.getElementById('omText').textContent.includes(${q(zh ? '季度预算' : 'quarterly budget')})`);
      check(await ev(p, `document.getElementById('srch').hidden && (document.getElementById('om').classList.contains('srflash') || document.getElementById('rm').classList.contains('srflash'))`), 'chat: tap a hit → that page, flashed ' + tag);

      // ---------------------------------------------------------------- five pages
      const fq = { workflows: '阿律', skills: fx.wf_list[1].skills[0].name, memory: 'MEMORY', agentj: 'core' };
      for (const page of ['workflows', 'skills', 'memory', 'agentj']) {
        await go(page);
        await waitFor(p, `document.querySelectorAll('#five-body .fp-card').length > 0`);
        const all = await ev(p, `document.querySelectorAll('#five-body .fp-card').length`);
        await magnifier();
        check(await ev(p, `!document.getElementById('five-find').hidden && document.getElementById('srch').hidden`), `${page}: the magnifier searches this page ${tag}`);
        await ev(p, type('#five-find-input', fq[page]));
        await sleep(150);
        const n = await ev(p, `document.querySelectorAll('#five-body .fp-card').length`);
        check(n >= 1 && n <= all && await ev(p, `[...document.querySelectorAll('#five-body .fp-card')].every(c => c.textContent.toLowerCase().includes(${q(fq[page].toLowerCase())}) || ${page !== 'skills'})`), `${page}: filtered ${all} → ${n} ${tag}`);
        if (page === 'workflows' && w === 390) await snap('workflows');
        await esc();
        check(await ev(p, `document.getElementById('five-find').hidden && document.querySelectorAll('#five-body .fp-card').length === ${all}`), `${page}: Esc brings every card back ${tag}`);
      }
      // ---------------------------------------------------------------- document
      await ev(p, `document.querySelector('#five-body .fp-card .fp-card-head').click()`);
      await waitFor(p, `!document.getElementById('five-detail').hidden && document.getElementById('five-detail').textContent.includes('第 30 段')`);
      await magnifier();
      await ev(p, type('#five-detail > .pfind input', '剪辑'));
      await sleep(150);
      check(await ev(p, `${marked} === 30`) && await ev(p, curShown), 'document: 30 hits marked, the current one on screen ' + tag);
      await esc(); await esc();
      await go('chat');

      // ---------------------------------------------------------------- cards (one record per card)
      await openCard('notes');
      const nEntries = await ev(p, `document.querySelectorAll('.jc-entry').length`);
      await magnifier();
      check(await ev(p, `!document.getElementById('five-find').hidden && document.getElementById('five-find-input').placeholder === ${q(zh ? '搜这张卡片' : 'Search this card')}`), 'notes: the magnifier searches this card ' + tag);
      await ev(p, type('#five-find-input', zh ? '免费' : 'free'));
      await sleep(200);
      const shownIds = await ev(p, `[...document.querySelectorAll('.jc-entry')].filter(e => e.getClientRects().length).map(e => e.dataset.item)`);
      check(nEntries === 3 && shownIds.length === 1 && shownIds[0] === 'free', `notes: 「${zh ? '免费' : 'free'}」 keeps the record with 「${zh ? '永久免费码' : 'Lifetime free code'}」 ${JSON.stringify(shownIds)} ${tag}`);
      check(await ev(p, `document.getElementById('five-find-n').textContent === ${q(zh ? '1 条符合' : '1 matching entries')}`), 'notes: the count line says one record (not 没找到) ' + tag);
      check(await ev(p, `${marked} >= 1 && [...CSS.highlights.get('aj-find')][0].startContainer.parentElement.closest('.jc-entry').dataset.item === 'free'`), 'notes: the word is marked inside the record ' + tag);
      check(await ev(p, inView('.jc-entry[data-item="free"]')), 'notes: the record is on screen ' + tag);
      await snap('notes-find');
      await ev(p, type('#five-find-input', 'zzzz-none'));
      await sleep(100);
      check(await ev(p, `document.getElementById('five-find-n').textContent === ${q(zh ? '没找到' : 'Nothing found')} && [...document.querySelectorAll('.jc-entry')].every(e => !e.getClientRects().length)`), 'notes: nothing found = one line ' + tag);
      await esc();
      check(await ev(p, `document.getElementById('five-find').hidden && [...document.querySelectorAll('.jc-entry')].filter(e => e.getClientRects().length).length === 3 && !CSS.highlights.get('aj-find')?.size`), 'notes: Esc brings every record back, marks cleared ' + tag);
      check(await ev(p, `!document.getElementById('five-page').hidden`), 'notes: the page stays after the first Esc ' + tag);
      await openCard('todo');
      await magnifier();
      await ev(p, type('#five-find-input', zh ? '阿律' : 'Lu'));
      await sleep(150);
      check(await ev(p, `(() => { const s = [...document.querySelectorAll('.jc-item')].filter(e => e.getClientRects().length); return s.length === 1 && s[0].dataset.item === 'call'; })()`), 'todo: rows filtered to the one that matches ' + tag);
      await esc();
      await openCard('automation');
      await magnifier();
      await ev(p, type('#five-find-input', 'weekly'));
      await sleep(150);
      check(await ev(p, `(() => { const s = [...document.querySelectorAll('.jc-automation')].filter(e => e.getClientRects().length); return s.length === 1 && s[0].dataset.item === 'weekly-report'; })()`), 'automation: filtered to weekly-report ' + tag);
      await esc(); await esc();
      await go('chat');

      // ---------------------------------------------------------------- menu pages (in-text find)
      const VIEWS = { mem: 'mem-view', act: 'act-view', tasks: 'tasks-view', models: 'models-view', friends: 'friends-view', bots: 'bots-view', setup: 'setup-view' };
      for (const [v, id] of Object.entries(VIEWS)) {
        await ev(p, `document.getElementById('open-${v}').click()`);
        await waitFor(p, `!document.getElementById(${q(id)}).hidden`);
        await sleep(400);
        // a word from the page itself (outside its header row), as the owner would read it
        const pick = await ev(p, `(() => { const page = document.getElementById(${q(id)}); const tw = document.createTreeWalker(page, NodeFilter.SHOW_TEXT); const all = [];
          for (let n = tw.nextNode(); n; n = tw.nextNode()) { if (n.parentElement.closest('[hidden],script,style,.pfind,svg,.menuhead,button,input,textarea,select')) continue; const s = n.nodeValue.trim(); if (s.length < 2) continue; const r = document.createRange(); r.selectNodeContents(n); if (!r.getClientRects().length) continue; all.push(s); }
          const s = all[Math.min(all.length - 1, 1)] || ''; const m = s.match(/[A-Za-z]{4,}|[\\u4e00-\\u9fff]{2,4}/); return m ? m[0] : ''; })()`);
        check(!!pick, `${v}: the page has text to search ${tag}`);
        await magnifier();
        check(await ev(p, `(() => { const f = document.querySelector('#${id} > .pfind'); return !!f && !f.hidden && document.getElementById('srch').hidden; })()`), `${v}: the magnifier searches this page ${tag}`);
        await ev(p, type(`#${id} > .pfind input`, pick));
        await sleep(150);
        check(await ev(p, `${marked} >= 1`) && await ev(p, curShown), `${v}: 「${pick}」 marked, the current hit on screen ${tag}`);
        if (v === 'act' && w === 390) await snap('menu-page-find');
        await esc();
        check(await ev(p, `!document.getElementById(${q(id)}).hidden && document.querySelector('#${id} > .pfind').hidden`), `${v}: Esc closes the find first ${tag}`);
        await esc();
        await waitFor(p, `document.getElementById(${q(id)}).hidden`);
        if (!(await ev(p, `document.getElementById('settings').hidden`))) await esc();
      }

      // ---------------------------------------------------------------- Settings main: 「Telegram」
      await ev(p, `document.getElementById('setBtn').click()`);
      await waitFor(p, `!document.getElementById('settings').hidden`);
      await magnifier();
      check(await ev(p, `!document.getElementById('set-find').hidden && document.activeElement.id === 'set-find-input'`), 'settings: the magnifier opens its search ' + tag);
      await ev(p, type('#set-find-input', 'Telegram'));
      await sleep(200);
      const tg = `document.getElementById('set-chan').closest('.setrow')`;
      check(await ev(p, `${tg}.getClientRects().length > 0 && !document.getElementById('set-s-look').getClientRects().length && !document.getElementById('set-s-read').getClientRects().length`), 'settings: the page itself filters to the Telegram row ' + tag);
      check(await ev(p, `!document.getElementById('set-mail-nav').getClientRects().length`), 'settings: non-matching rows in the same section step aside ' + tag);
      if (w === 1440) check(await ev(p, `!document.querySelector('[data-set-jump="set-s-look"]').getClientRects().length && document.querySelector('[data-set-jump="set-s-channels"]').getClientRects().length > 0`), 'settings: the section list follows the filter ' + tag);
      check(await ev(p, `${marked} >= 1`) && await ev(p, curShown), 'settings: 「Telegram」 marked on the page, the first on screen ' + tag);
      check(await ev(p, `[...document.querySelectorAll('#set-find-results button b')].some(b => b.textContent === 'Telegram')`), 'settings: the list names the Telegram row ' + tag);
      await snap('settings-find');
      await ev(p, `[...document.querySelectorAll('#set-find-results button')].find(b => b.querySelector('b').textContent === 'Telegram').click()`);
      await sleep(250);
      check(await ev(p, `document.getElementById('set-find').hidden && ${tg}.classList.contains('set-found') && document.getElementById('set-s-look').getClientRects().length > 0`), 'settings: tap the hit → the full page again, the Telegram row flashed ' + tag);
      check(await ev(p, `(() => { const r = ${tg}.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })()`), 'settings: the Telegram row is scrolled into view ' + tag);
      await snap('settings-located');
      // a row on a sub-page: the list names 「邮件通道 › …」, a tap opens that page on it
      const subWord = await ev(p, `[...document.querySelectorAll('#set-mail h3.setg')].map(h => h.textContent.trim()).find(Boolean)`);
      await magnifier();
      await ev(p, type('#set-find-input', subWord));
      await sleep(200);
      const mailTitle = zh ? '邮件通道' : await ev(p, `document.querySelector('#set-mail-nav b').textContent`);
      check(await ev(p, `[...document.querySelectorAll('#set-find-results button')].some(b => b.querySelector('small').textContent === ${q(mailTitle)} && b.querySelector('b').textContent.includes(${q(subWord)}))`), `settings: the list reaches the sub-page row 「${subWord}」 ${tag}`);
      await ev(p, `[...document.querySelectorAll('#set-find-results button')].find(b => b.querySelector('small').textContent === ${q(mailTitle)}).click()`);
      await sleep(300);
      check(await ev(p, `!document.getElementById('set-mail').hidden && !!document.querySelector('#set-mail .set-found')`), 'settings: tap a sub-page hit → that sub-page, the row flashed ' + tag);
      check(await ev(p, inView('#set-mail .set-found')), 'settings: the sub-page row is on screen ' + tag);
      // ---------------------------------------------------------------- Settings sub-page: in place
      await sleep(2500);
      await magnifier();
      check(await ev(p, `!!document.querySelector('#set-mail > .pfind:not([hidden])')`), 'settings-sub: the magnifier searches the sub-page in place ' + tag);
      await ev(p, type('#set-mail > .pfind input', subWord));
      await sleep(150);
      check(await ev(p, `${marked} >= 1`) && await ev(p, curShown), 'settings-sub: hits marked, the current on screen ' + tag);
      await esc();
      await ev(p, `document.getElementById('set-back').click()`);
      await sleep(200);
      // ---------------------------------------------------------------- contacts
      await ev(p, `document.getElementById('set-contacts-nav').click()`);
      await waitFor(p, `document.querySelectorAll('#ctm-list li').length >= 3`);
      await magnifier();
      check(await ev(p, `document.activeElement.id === 'ctm-q'`), 'contacts: the magnifier goes to the address book search ' + tag);
      await ev(p, type('#ctm-q', '李'));
      await waitFor(p, `document.querySelectorAll('#ctm-list li').length === 1`);
      check(fake.st.got.some((m) => m.t === 'ct_list' && m.q === '李') && await ev(p, `document.getElementById('ctm-list').textContent.includes('李雷')`), 'contacts: the whole book searched, the list filtered ' + tag);
      await ev(p, type('#ctm-q', ''));
      await ev(p, `document.getElementById('set-back').click()`);
      await sleep(150);
      // ---------------------------------------------------------------- shortcut sheet
      await ev(p, `document.getElementById('keysBtn').click()`);
      await waitFor(p, `!document.getElementById('keys').hidden`);
      const keysAll = await ev(p, `document.querySelectorAll('#keysCols dt').length`);
      await ev(p, `document.getElementById('keysFindBtn').click()`);
      const kWord = zh ? '截图' : 'screenshot';
      await ev(p, type('#keysFindInput', kWord));
      await sleep(150);
      const keysN = await ev(p, `document.querySelectorAll('#keysCols dt').length`);
      check(keysN >= 1 && keysN < keysAll, `keys: rows filtered ${keysAll} → ${keysN} ${tag}`);
      await esc(); await esc();
      if (!(await ev(p, `document.getElementById('settings').hidden`))) await esc();
      // ---------------------------------------------------------------- 快捷技能键 sheet
      await ev(p, `import('./js/hotkey.js').then(m => { m.openHot(); return true; })`);
      await waitFor(p, `!document.getElementById('hotMenu').hidden`);
      const hotAll = await ev(p, `document.querySelectorAll('#hotList [data-hot]').length`);
      await ev(p, type('#hotQ', 'clear'));
      await sleep(150);
      const hotN = await ev(p, `document.querySelectorAll('#hotList [data-hot]').length`);
      check(hotN >= 1 && hotN < hotAll && await ev(p, `[...document.querySelectorAll('#hotList [data-hot]')].every(b => b.textContent.toLowerCase().includes('clear'))`), `hotkey: rows filtered ${hotAll} → ${hotN} ${tag}`);
      await esc();
      check(p.problems.length === 0, 'no console errors ' + tag + ': ' + p.problems.join(' | '));
    } finally { await p.dispose(); }
  }
  writeFileSync(OUT + 'web-search.json', JSON.stringify({ ok: true, checks, results, ios: '390px Chromium touch emulation; not a physical device' }, null, 2));
  passed = true;
  console.log(`P393 search PASS ${checks} checks → ${OUT}`);
} catch (e) {
  console.error(e); process.exitCode = 1;
} finally {
  rec.record('p393-search', passed); rec.write();
  await Promise.allSettled([B.close(), fake.stop(), web.stop()]);
}
process.exit(process.exitCode || 0);
