// P360 Agent J Cards (PROTOCOL §34): the real page + local fake host (real Noise), own headless Chromium (never :9222).
// The fake answers jcard_* with what the real host code produced (host/tests/fixtures/p360_cards.json, test_p360_cards).
//   cards-deck        logo → 5 system cards + the owner's switched-on cards: fixed Wallet stack (44 px peek, 220 px card, width
//                     min(screen − 2 × logo margin, 360)), left edge = the logo's left edge, five cards no longer fill the screen
//   cards-plus        the header's + on every page names that page's adding skill (chat / wall → agentj-cards, workflows →
//                     agentj-capability, skills → agentj-commands, memory → agentj-add-memory, Agent J → agentj-add-doc, a card →
//                     its `plus`): `/<skill> ` chip + space in the message box, back on the chat, nothing sent; + · 扫码 · 搜索 · 设置
//   cards-links       a card's links: web → the address on the confirm card, then a new tab (noopener, noreferrer); 取消 → nothing;
//                     internal → the page / card#place inside Agent J
//   cards-open        `agentj card open` (jcard_open): the phone goes to the card and the place (a checklist row flashes)
//   cards-mention     @ in the message box offers the owner's cards (chip 「@记事本」, the text carries card/<id>); in messages
//                     `@名`, `card/<id>#<anchor>` and a `card/<id>` code span are links; two cards with one name → a chooser
//   cards-notes       記事本 page: the notes drawn from the host's text, two buttons (P415: the official notes / to-do cards no
//                     longer draw the host's 「记一条 / 帮我整理」 actions): add (data-act=add) → the direct-save dialog, nothing goes
//                     to the Agent, Esc closes it; collaborate (data-act=collaborate) → `/agentj-add-note ` in the message box,
//                     nothing sent
//   cards-todo        待办 page: 4 rows, done ones struck through; tap a row → confirm shows the whole message → 取消 sends nothing;
//                     again → 发送 → exactly that text is the say (moved here from 「帮我整理」, P415); add / collaborate as on notes
//   (P413: the official 「日历」 card is on by default, so the deck is 5 system + 5 owner cards = 10, Settings lists 5 rows)
//   cards-settings    Settings → Cards: official + mine with their switches; off → jcard_set {id, on:false}, the deck loses it
//   cards-many        35 cards: the same 44 px peek, the deck scrolls to the last card
//   cards-old-host    a computer without jcards1: five cards, no Settings row
// Shots: zh/en × light/dark × 390×844 (iPhone) / 412×915 (Android) → reports/qa/p360/.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parityRecorder } from '../../parity/lib.mjs';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, key } from './browser.mjs';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const OUT = join(ROOT, 'reports/qa/p360');
const FX = JSON.parse(readFileSync(join(ROOT, 'host/tests/fixtures/p360_cards.json'), 'utf8'));
const WF = JSON.parse(readFileSync(join(ROOT, 'host/tests/fixtures/p244_wf.json'), 'utf8'));
export const CASES = { deck: 'cards-deck', notes: 'cards-notes', todo: 'cards-todo', settings: 'cards-settings', many: 'cards-many', old: 'cards-old-host',
  plus: 'cards-plus', links: 'cards-links', open: 'cards-open', mention: 'cards-mention' };
const DECK = 10;   // P413: 5 system cards + notes, todo, automation, calendar (official, default on), reading-list
const PEEK = 44, CARD_H = 220, CARD_W = 360;
const S = { zh: { notes: '记事本', todo: '待办', tidy: '帮我整理', label: '〔来自卡片：记事本〕', send: '发送', nav: 'Cards 卡片', official: '官方', mine: '我的',
                   reply: '加好了：card/todo#t3 ，也可以看 `card/notes` 或者 @待办。', dup: '记在 @记事本 里了。' },
            en: { notes: 'Notebook', todo: 'To-do', tidy: 'Tidy it up', label: '[From card: Notebook] ', send: 'Send', nav: 'Cards', official: 'official', mine: 'mine',
                   reply: 'Added: card/todo#t3 — see also `card/notes` or @To-do.', dup: 'Noted in @Notebook.' } };
const ev = (p, s) => evaluate(p, s);
const q = (s) => JSON.stringify(s);
const click = (p, sel) => ev(p, sel === '#orb'
  ? `(()=>{const e=document.getElementById('orb');e.dispatchEvent(new PointerEvent('pointerdown',{button:0}));e.dispatchEvent(new PointerEvent('pointerup',{button:0}));})()`
  : `document.querySelector(${q(sel)}).click()`);
const byText = (p, sel, text) => ev(p, `[...document.querySelectorAll(${q(sel)})].find(b=>b.textContent.trim()===${q(text)}).click()`);

export async function runP360() {
  mkdirSync(OUT, { recursive: true });
  const rec = parityRecorder('web/test/p360.mjs');
  const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
  const fake = await startFakeHost();
  const B = await launch();
  let checks = 0;
  const results = [];
  const check = (ok, label) => { assert.ok(ok, label); checks++; results.push(label); };
  const done = new Set();
  const step = async (c, f) => { try { await f(); if (!done.has(c)) { rec.record(CASES[c], true); done.add(c); } } catch (e) { rec.record(CASES[c], false); throw e; } };
  let cards, sets;
  const host = (caps = ['wf1', 'jcards1']) => {
    fake.reset();
    cards = JSON.parse(JSON.stringify(FX.list.cards)); sets = [];
    fake.st.caps = caps;
    fake.st.onApp = async (c, m, send) => {
      if (m.t === 'wf_list') { if (m.etag) { await send({ t: 'wf_same', r: m.r }); return true; } for (const x of WF.wf_list) await send({ ...x, r: m.r }); return true; }
      if (!caps.includes('jcards1')) return false;
      if (m.t === 'jcard_list') { await send({ t: 'jcard_items', r: m.r, cards }); return true; }
      if (m.t === 'jcard_get') { const g = FX.get[m.lang === 'en' ? 'en' : 'zh'][m.id] || { ok: false, why: 'not_found' }; await send({ t: 'jcard_get', r: m.r, ...g }); return true; }
      if (m.t === 'jcard_set') { sets.push(m); const x = cards.find((y) => y.id === m.id); if (x) x.on = m.on; await send({ t: 'jcard_res', r: m.r, ok: !!x, id: x ? m.id : null, on: x ? m.on : null }); return true; }
      return false;
    };
  };
  const pair = async (p, lang) => {
    await navigate(p, fake.newPairing(web.url + '?lang=' + lang)); await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
    await ev(p, `document.getElementById('phone-env-dismiss')?.click()`); await sleep(250);
  };
  const openMenu = async (p, n) => {
    if (await ev(p, `document.getElementById('five-menu').hidden`)) await click(p, '#orb');
    await waitFor(p, `document.querySelectorAll('#five-menu .fp-menu-card').length===${n}`);
    await sleep(300);
  };
  const deckGeom = (p) => ev(p, `(()=>{const m=document.getElementById('five-menu');const r=[...m.querySelectorAll('.fp-menu-card')].map(b=>b.getBoundingClientRect().top+m.scrollTop);return {tops:r,sw:m.scrollWidth,cw:m.clientWidth,sh:m.scrollHeight,ch:m.clientHeight};})()`);
  const tag = (w, lang, scheme) => `${w}-${lang}-${scheme}`;
  try {
    for (const [w, h] of [[390, 844], [412, 915]]) for (const lang of ['zh', 'en']) for (const scheme of ['light', 'dark']) {
      const T = S[lang], name = tag(w, lang, scheme);
      host();
      fake.st.prefs = { appearance: { language: lang, theme: scheme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
      const p = await newPage(B, w, h, scheme, { allow: [web.url, fake.relay + '/'], touch: true });
      await pair(p, lang);

      await step('deck', async () => {
        await openMenu(p, DECK);
        const g = await deckGeom(p);
        const steps = g.tops.slice(1).map((t, i) => Math.round(t - g.tops[i]));
        check(steps.every((s) => Math.abs(s - PEEK) <= 1), `deck: one fixed ${PEEK} px peek ${steps.join(',')} ${name}`);
        const box = await ev(p, `(()=>{const o=document.getElementById('orb').getBoundingClientRect(),c=[...document.querySelectorAll('#five-menu .fp-menu-card')].map(b=>b.getBoundingClientRect());return {orb:o.left,lefts:c.map(r=>Math.round(r.left)),ws:c.map(r=>Math.round(r.width)),hs:c.map(r=>Math.round(r.height)),bottom:c.at(-1).bottom,vw:innerWidth,vh:innerHeight};})()`);
        check(box.lefts.every((l) => Math.abs(l - box.orb) <= 1), `deck: left edge = the logo's left edge ${box.orb}/${box.lefts[0]} ${name}`);
        check(box.ws.every((x) => Math.abs(x - Math.min(box.vw - 2 * box.orb, CARD_W)) <= 1) && box.hs.every((x) => x === CARD_H), `deck: fixed card ${box.ws[0]}×${box.hs[0]} ${name}`);
        check(box.bottom < box.vh - 100, `deck: ten cards (P413 calendar) do not fill the screen ${Math.round(box.bottom)}/${box.vh} ${name}`);
        check(g.sw <= g.cw, `deck: no sideways overflow ${name}`);
        check(await ev(p, `[...document.querySelectorAll('.jc-card')].map(b=>b.dataset.card).join()==='notes,todo,automation,calendar,reading-list'`), `deck: owner cards after the five, in order (P413 calendar) ${name}`);
        check(await ev(p, `document.querySelector('.jc-card[data-card="notes"] .jc-name').textContent===${q(T.notes)}`), `deck: card name in the title strip ${name}`);
        check(await ev(p, `(()=>{const b=document.querySelector('.jc-card[data-card="todo"]');const s=getComputedStyle(b);return s.backgroundColor==='rgb(106, 155, 204)'&&s.color==='rgb(250, 249, 245)';})()`), `deck: accent fill + Lotion ink from cards.css (P393 #132) ${name}`);
        writeFileSync(join(OUT, `deck-${name}.png`), await shoot(p, ''));
      });

      await step('notes', async () => {
        await click(p, '.jc-card[data-card="notes"]');
        await waitFor(p, `!!document.querySelector('.jc-body[data-card="notes"] .jc-notes')`);
        check(await ev(p, `document.getElementById('five-title').textContent===${q(T.notes)}`), `notes: title ${name}`);
        check(await ev(p, `document.querySelector('.jc-notes').textContent.includes('Cards')&&document.querySelectorAll('.jc-entry').length===3`), `notes: host entries drawn as individual note cards ${name}`);
        check(await ev(p, `document.querySelectorAll('.jc-actions .jc-action').length===2`), `notes: two buttons ${name}`);
        check(await ev(p, `(()=>{const b=document.getElementById('five-page');return b.scrollWidth<=b.clientWidth&&[...document.querySelectorAll('.jc-action')].every(x=>x.getBoundingClientRect().height>=44);})()`), `notes: no overflow, taps ≥ 44 px ${name}`);
        writeFileSync(join(OUT, `notes-${name}.png`), await shoot(p, ''));
        // P415: the official notes card's buttons are add (direct save, no AI) + collaborate (a draft), not the host's actions
        check(await ev(p, `[...document.querySelectorAll('.jc-actions .jc-action')].map(b=>b.dataset.act).join()==='add,collaborate'`), `notes: add + collaborate (P415) ${name}`);
        const before = fake.st.says.length;
        await click(p, '.jc-action[data-act="add"]');
        await waitFor(p, `!!document.querySelector('.jc-dialog[open] textarea')`);
        check(fake.st.says.length === before && await ev(p, `document.getElementById('confirm').hidden`), `notes: add opens the direct-save dialog, nothing to the Agent (P415) ${name}`);
        writeFileSync(join(OUT, `confirm-${name}.png`), await shoot(p, ''));
        await key(p, 'Escape', { code: 'Escape' });
        await waitFor(p, `!document.querySelector('.jc-dialog')`);
        check(fake.st.says.length === before && await ev(p, `!document.getElementById('five-page').hidden`), `notes: Esc closes the dialog, nothing sent, stays (P415) ${name}`);
        await click(p, '.jc-action[data-act="collaborate"]'); await sleep(300);
        check(await ev(p, `document.getElementById('input').value==='/agentj-add-note '`) && fake.st.says.length === before, `notes: collaborate is a /agentj-add-note draft, nothing sent (P415) ${name}`);
        await ev(p, `(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input'));})()`);
      });

      await step('todo', async () => {
        await openMenu(p, DECK); await click(p, '.jc-card[data-card="todo"]');
        await waitFor(p, `document.querySelectorAll('.jc-item').length===4`);
        check(await ev(p, `[...document.querySelectorAll('.jc-item[data-done="1"] .jc-text')].every(e=>getComputedStyle(e).textDecorationLine.includes('line-through'))&&document.querySelectorAll('.jc-item[data-done="1"]').length===2`), `todo: done rows struck through ${name}`);
        check(await ev(p, `[...document.querySelectorAll('.jc-row')].every(b=>b.getBoundingClientRect().height>=44)`), `todo: rows ≥ 44 px ${name}`);
        writeFileSync(join(OUT, `todo-${name}.png`), await shoot(p, ''));
        const n = fake.st.says.length;
        await click(p, '.jc-item[data-item="t3"] .jc-row');
        await waitFor(p, `!document.getElementById('confirm').hidden`);
        const want = FX.get[lang].todo.items.find((i) => i.id === 't3').acts[0].prompt;
        check(await ev(p, `document.getElementById('confirm-text').textContent===${q(want)}`), `todo: item message shown whole ${name}`);
        await click(p, '#confirm-no'); await sleep(250);
        check(fake.st.says.length === n && await ev(p, `!document.getElementById('five-page').hidden`), `todo: 取消 sends nothing, stays ${name}`);
        // the 「confirm → 发送 → exactly that text」 path (was 「帮我整理」 on notes before P415)
        await click(p, '.jc-item[data-item="t3"] .jc-row');
        await waitFor(p, `!document.getElementById('confirm').hidden`);
        check(await ev(p, `document.getElementById('confirm-yes').textContent===${q(T.send)}`), `todo: the official confirm card says ${T.send} ${name}`);
        await click(p, '#confirm-yes'); await sleep(400);
        const said = fake.st.says.slice(n);
        check(said.length === 1 && said[0].text === want, `todo: exactly that text was sent ${name}`);
        check(await ev(p, `document.getElementById('five-page').hidden`), `todo: back on the chat after sending ${name}`);
        // P415: the official to-do card's buttons are add (direct save) + collaborate (draft)
        await openMenu(p, DECK); await click(p, '.jc-card[data-card="todo"]');
        await waitFor(p, `!!document.querySelector('.jc-body[data-card="todo"] .jc-action[data-act="add"]')`);
        const n2 = fake.st.says.length;
        await click(p, '.jc-action[data-act="add"]'); await waitFor(p, `!!document.querySelector('.jc-dialog[open] textarea')`);
        await key(p, 'Escape', { code: 'Escape' });
        await waitFor(p, `!document.querySelector('.jc-dialog')`);
        check(fake.st.says.length === n2, `todo: add opens the direct-save dialog, Esc closes, nothing sent (P415) ${name}`);
        await click(p, '.jc-action[data-act="collaborate"]'); await sleep(300);
        check(await ev(p, `document.getElementById('input').value==='/agentj-add-todo '`) && fake.st.says.length === n2, `todo: collaborate is a /agentj-add-todo draft, nothing sent (P415) ${name}`);
        await ev(p, `(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input'));})()`);
        await ev(p, `document.querySelector('#five-page .fp-back').click()`); await sleep(200);
      });

      const plusOf = () => ev(p, `(()=>{const b=document.getElementById('cardPlus');return b.hidden||!b.getClientRects().length?null:b.dataset.skill;})()`);
      const tapPlus = async (want) => {
        const n = fake.st.says.length;
        await click(p, '#cardPlus'); await sleep(250);
        const v = await ev(p, `document.getElementById('input').value`);
        check(v.startsWith('/' + want + ' ') && fake.st.says.length === n && await ev(p, `document.getElementById('five-page').hidden&&document.getElementById('five-menu').hidden`),
          `plus: /${want} + space in the message box, back on the chat, nothing sent (${JSON.stringify(v)}) ${name}`);
        check(await ev(p, `[...document.querySelectorAll('#workflow-compose .cmd-token')].some(c=>c.textContent.trim()==='/${want}')`), `plus: /${want} is a chip ${name}`);
      };
      const clearInput = () => ev(p, `(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input'));})()`);
      const order = (ids) => ev(p, `(()=>{const r=${q(ids)}.map(id=>document.getElementById(id)).map(b=>b&&b.getClientRects().length?b.getBoundingClientRect():null);if(r.some(x=>!x))return null;return r.map(x=>Math.round(x.left));})()`);

      await step('plus', async () => {
        await ev(p, `document.querySelector('#five-page .fp-back')?.click()`); await sleep(150);
        check(await plusOf() === 'agentj-cards', `plus: on the chat → agentj-cards ${name}`);
        const xs = await order(['cardPlus', 'scanBtn', 'srchBtn', 'setBtn']);
        check(xs && xs.every((x, i) => !i || x > xs[i - 1]), `plus: + · 扫码 · 搜索 · 设置 left to right ${xs} ${name}`);
        await ev(p, `(()=>{const i=document.getElementById('input');i.value='想要一张读书卡';i.dispatchEvent(new Event('input'));})()`);
        await tapPlus('agentj-cards');
        check(await ev(p, `document.getElementById('input').value==='/agentj-cards 想要一张读书卡'`), `plus: what was typed stays after the command ${name}`);
        await openMenu(p, DECK);
        check(await plusOf() === 'agentj-cards' && !!(await order(['cardPlus', 'scanBtn', 'srchBtn', 'setBtn'])), `plus: card wall shows + · 扫码 · 搜索 · 设置 ${name}`);
        if (lang === 'zh') writeFileSync(join(OUT, `plus-wall-${w}-${scheme}.png`), await shoot(p, ''));
        await tapPlus('agentj-cards');
        check(await ev(p, `document.getElementById('input').value==='/agentj-cards 想要一张读书卡'`), `plus: a second + does not repeat the command ${name}`);
        for (const [page, want] of [['workflows', 'agentj-capability'], ['skills', 'agentj-commands'], ['memory', 'agentj-add-memory'], ['agentj', 'agentj-add-doc']]) {
          await openMenu(p, DECK); await click(p, `.fp-menu-card[data-page="${page}"]`); await sleep(250);
          check(await plusOf() === want, `plus: ${page} page → ${want} ${name}`);
          await tapPlus(want);
          check(await ev(p, `document.getElementById('input').value==='/${want} 想要一张读书卡'`), `plus: ${page} swaps the page command, keeps the words ${name}`);
        }
        await clearInput();
        for (const [cid, want] of [['notes', 'agentj-add-note'], ['todo', 'agentj-add-todo'], ['reading-list', 'agentj-add-todo']]) {
          await openMenu(p, DECK); await click(p, `.jc-card[data-card="${cid}"]`); await waitFor(p, `!!document.querySelector('.jc-body[data-card="${cid}"]')`);
          check(await plusOf() === want, `plus: card ${cid} → its plus ${want} ${name}`);
          if (cid === 'todo' && lang === 'zh') writeFileSync(join(OUT, `plus-card-${w}-${scheme}.png`), await shoot(p, ''));
          if (cid === 'reading-list') { await tapPlus(want); continue; }
          // P415: on the official notes / to-do card the + saves directly (the same dialog as add), no draft, nothing sent
          const n = fake.st.says.length;
          await click(p, '#cardPlus'); await waitFor(p, `!!document.querySelector('.jc-dialog[open] textarea')`);
          await key(p, 'Escape', { code: 'Escape' }); await waitFor(p, `!document.querySelector('.jc-dialog')`);
          check(fake.st.says.length === n && await ev(p, `document.getElementById('input').value===''`), `plus: official ${cid} + opens the direct-save dialog, Esc closes, nothing sent (P415) ${name}`);
        }
        await clearInput();
      });

      await step('links', async () => {
        await openMenu(p, DECK); await click(p, '.jc-card[data-card="reading-list"]');
        await waitFor(p, `document.querySelectorAll('.jc-links .jc-link').length===3`);
        check(await ev(p, `[...document.querySelectorAll('.jc-link')].every(b=>b.getBoundingClientRect().height>=44)&&!!document.querySelector('.jc-link-web .jc-link-icon')`), `links: three ≥ 44 px, the web one marked ↗ ${name}`);
        if (lang === 'zh') writeFileSync(join(OUT, `links-${w}-${scheme}.png`), await shoot(p, ''));
        await ev(p, `window.__opened=[];window.open=(...a)=>{window.__opened.push(a);return null;}`);
        const url = FX.get[lang]['reading-list'].links.find((l) => l.href).href;
        await click(p, '.jc-link[data-link="web"]'); await waitFor(p, `!document.getElementById('confirm').hidden`);
        check(await ev(p, `document.getElementById('confirm-text').textContent===${q(url)}`), `links: the whole address first ${name}`);
        if (lang === 'zh' && w === 390) writeFileSync(join(OUT, `links-confirm-${w}-${scheme}.png`), await shoot(p, ''));
        await click(p, '#confirm-no'); await sleep(200);
        check(await ev(p, `window.__opened.length===0`), `links: 取消 opens nothing ${name}`);
        await click(p, '.jc-link[data-link="web"]'); await waitFor(p, `!document.getElementById('confirm').hidden`);
        await click(p, '#confirm-yes'); await sleep(200);
        check(await ev(p, `JSON.stringify(window.__opened)===${q(JSON.stringify([[url, '_blank', 'noopener,noreferrer']]))}`), `links: a new tab, no opener, no referrer ${name}`);
        await click(p, '.jc-link[data-link="skills"]'); await sleep(250);
        check(await ev(p, `document.body.dataset.fivePage==='skills'&&!document.getElementById('five-page').hidden`), `links: page/skills opens the skills page ${name}`);
        await openMenu(p, DECK); await click(p, '.jc-card[data-card="reading-list"]'); await waitFor(p, `!!document.querySelector('.jc-link[data-link="todo"]')`);
        await click(p, '.jc-link[data-link="todo"]');
        await waitFor(p, `!!document.querySelector('.jc-body[data-card="todo"] .jc-item[data-item="t3"].jc-flash')`);
        check(await ev(p, `(()=>{const r=document.querySelector('.jc-item[data-item="t3"]').getBoundingClientRect(),h=document.querySelector('#five-page .fp-sticky').getBoundingClientRect().bottom;return r.top>=h-1&&r.bottom<=innerHeight;})()`), `links: card/todo#t3 lands on the row, below the header ${name}`);
      });

      await step('open', async () => {
        await ev(p, `document.querySelector('#five-page .fp-back')?.click()`); await sleep(150);
        await fake.send({ t: 'jcard_open', id: 'todo', at: 't4' });
        await waitFor(p, `!!document.querySelector('.jc-body[data-card="todo"] .jc-item[data-item="t4"].jc-flash')`);
        check(await ev(p, `document.body.dataset.fivePage==='card'`), `open: agentj card open → the card and the row ${name}`);
        if (lang === 'zh' && scheme === 'dark') writeFileSync(join(OUT, `open-${w}.png`), await shoot(p, ''));
        await fake.send({ t: 'jcard_open', id: 'memory' }); await sleep(300);
        check(await ev(p, `document.body.dataset.fivePage==='memory'`), `open: a system page ${name}`);
        await fake.send({ t: 'jcard_open', id: 'cards' }); await sleep(300);
        check(await ev(p, `!document.getElementById('five-menu').hidden`), `open: cards → the card wall ${name}`);
        await fake.send({ t: 'jcard_open', id: 'Bad id!' }); await sleep(200);
        check(await ev(p, `!document.getElementById('five-menu').hidden`), `open: a bad id does nothing ${name}`);
        await fake.send({ t: 'jcard_open', id: 'cards' }); await sleep(250);
        check(await ev(p, `!document.getElementById('five-menu').hidden`), `open: cards again keeps the wall open (no toggle) ${name}`);
        // P415: notes has no 「帮我整理」 confirm any more — a to-do row's confirm card stands in
        await click(p, '.jc-card[data-card="todo"]'); await waitFor(p, `!!document.querySelector('.jc-item[data-item="t3"] .jc-row')`);
        await click(p, '.jc-item[data-item="t3"] .jc-row'); await waitFor(p, `!document.getElementById('confirm').hidden`);
        await fake.send({ t: 'jcard_open', id: 'notes' }); await sleep(300);
        check(await ev(p, `!document.getElementById('confirm').hidden&&document.querySelector('.jc-body')?.dataset.card==='todo'`), `open: never while a confirm card is open ${name}`);
        await click(p, '#confirm-no'); await sleep(150);
        await ev(p, `document.querySelector('#five-page .fp-back').click()`); await sleep(150);
      });

      await step('mention', async () => {
        const T2 = T;
        await ev(p, `(()=>{const i=document.getElementById('input');i.focus();i.value='看看 @'+${q(T2.notes.slice(0, 2))};i.setSelectionRange(i.value.length,i.value.length);i.dispatchEvent(new Event('input'));})()`);
        await waitFor(p, `!document.getElementById('five-mentions').hidden&&!!document.querySelector('#five-mentions .fp-mention-card[data-card="notes"]')`);
        if (lang === 'zh' && scheme === 'light') writeFileSync(join(OUT, `mention-pop-${w}.png`), await shoot(p, ''));
        await click(p, '#five-mentions .fp-mention-card[data-card="notes"]'); await sleep(200);
        const v = await ev(p, `document.getElementById('input').value`);
        check(v === `看看 @${T2.notes}\uFF08card/notes\uFF09 `, `mention: the message carries the card's address ${JSON.stringify(v)} ${name}`);
        check(await ev(p, `[...document.querySelectorAll('#workflow-compose .workflow-token')].some(c=>c.textContent===${q('@' + T2.notes)})`), `mention: chip 「@${T2.notes}」 ${name}`);
        await clearInput();
        await fake.addTurn({ k: 'phone', text: `看看 @${T2.notes}\uFF08card/notes\uFF09` }, T2.reply); await sleep(500);
        const links = await ev(p, `[...document.querySelectorAll('#chat-view .fp-link-card')].map(b=>b.textContent+'='+(b.dataset.card||''))`);
        check(['@' + T2.notes + '=notes', 'card/todo#t3=todo', 'card/notes=notes', '@' + T2.todo + '=todo'].every((x) => links.includes(x)), `mention: links in both bubbles ${links.join(' | ')} ${name}`);
        if (lang === 'zh') writeFileSync(join(OUT, `mention-msg-${w}-${scheme}.png`), await shoot(p, ''));
        await ev(p, `[...document.querySelectorAll('#chat-view .fp-link-card')].find(b=>b.textContent==='card/todo#t3').click()`);
        await waitFor(p, `!!document.querySelector('.jc-body[data-card="todo"] .jc-item[data-item="t3"].jc-flash')`);
        await ev(p, `document.querySelector('#five-page .fp-back').click()`); await sleep(150);
        // a second card with the same name (the owner's): the chip and the pop say which, a bare @name asks
        cards.push({ ...cards[2], id: 'my-notes', name: { zh: '记事本', en: 'Notebook' }, official: false, on: true });
        await openMenu(p, DECK + 1); await click(p, '#orb'); await sleep(150);
        await ev(p, `(()=>{const i=document.getElementById('input');i.focus();i.value='@'+${q(T2.notes.slice(0, 2))};i.setSelectionRange(i.value.length,i.value.length);i.dispatchEvent(new Event('input'));})()`);
        await waitFor(p, `document.querySelectorAll('#five-mentions .fp-mention-card').length===2`);
        check(await ev(p, `[...document.querySelectorAll('#five-mentions .fp-mention-card')].map(b=>b.textContent).join('|')===${q(`${T2.notes} · ${T2.official}|${T2.notes} · ${T2.mine}`)}`), `mention: same name → 官方 / 我的 ${name}`);
        await ev(p, `document.getElementById('five-mentions').hidden=true`); await clearInput();
        await fake.addTurn({ k: 'phone', text: 'ok' }, T2.dup); await sleep(500);
        await ev(p, `[...document.querySelectorAll('#chat-view .fp-link-card')].filter(b=>b.textContent===${q('@' + T2.notes)}).at(-1).click()`);
        await waitFor(p, `document.querySelectorAll('#five-detail .jc-choose').length===2`);
        if (lang === 'zh' && scheme === 'dark') writeFileSync(join(OUT, `mention-choose-${w}.png`), await shoot(p, ''));
        await click(p, '#five-detail .jc-choose[data-card="my-notes"]'); await sleep(250);
        check(await ev(p, `document.body.dataset.fivePage==='card'&&document.querySelector('.jc-body')?.dataset.card==='my-notes'`), `mention: the chooser opens the one picked ${name}`);
        cards.pop();
        await ev(p, `document.querySelector('#five-page .fp-back').click()`); await openMenu(p, DECK); await click(p, '#orb'); await sleep(150);
      });

      await step('settings', async () => {
        await ev(p, `document.getElementById('settings').hidden && document.getElementById('setBtn').click()`);
        await waitFor(p, `!document.getElementById('settings').hidden && !document.getElementById('set-cards-nav').hidden`);
        await click(p, '#set-cards-nav');
        await waitFor(p, `document.querySelectorAll('#set-cards-list .jc-setrow').length===8`);   // + P432 Files, Obsidian (official, default off)
        check(await ev(p, `document.getElementById('set-title').textContent===${q(T.nav)}`), `settings: page title ${name}`);
        check(await ev(p, `[...document.querySelectorAll('#set-cards-list .jc-setrow')].every(row=>row.querySelector('.tgl').getAttribute('aria-checked')===String(!['files','obsidian','official-palette'].includes(row.dataset.card)))`), `settings: switches show the computer's state (official cards default on, P393 #129; Files / Obsidian off, P432) ${name}`);
        writeFileSync(join(OUT, `settings-${name}.png`), await shoot(p, ''));
        await click(p, '#set-cards-list .jc-setrow[data-card="reading-list"] .tgl');
        await waitFor(p, `document.querySelector('#set-cards-list .jc-setrow[data-card="reading-list"] .tgl').getAttribute('aria-checked')==='false'`);
        await sleep(200);
        check(sets.length === 1 && sets[0].id === 'reading-list' && sets[0].on === false, `settings: one jcard_set off ${name}`);
        await ev(p, `document.getElementById('setBtn').click()`); await waitFor(p, `document.getElementById('settings').hidden`);
        await openMenu(p, DECK - 1);
        check(!(await ev(p, `!!document.querySelector('.jc-card[data-card="reading-list"]')`)), `settings: the deck lost the card ${name}`);
        await click(p, '#orb'); await sleep(200);
      });
      check(p.problems.length === 0, `no console errors ${name}: ${p.problems.join(' | ')}`);
      await p.dispose();
    }

    for (const [w, h] of [[360, 780], [390, 844], [412, 915]]) await step('many', async () => {
      host();
      // P413: the fixture already has 5 owner cards (calendar joined), so 25 more → 35 in all
      for (let i = 0; i < 25; i++) cards.push({ ...cards.find(c=>c.id==='reading-list'), id: `c${i}`, name: { zh: `卡片 ${i + 4}`, en: `Card ${i + 4}` }, accent: ['emerald-oasis', 'windstorm', 'magic-carpet', 'peachy-feeling', 'noir'][i % 5] });
      const p = await newPage(B, w, h, 'dark', { allow: [web.url, fake.relay + '/'], touch: true });
      await pair(p, 'zh');
      await openMenu(p, 35);
      const g = await deckGeom(p);
      const steps = g.tops.slice(1).map((t, i) => Math.round(t - g.tops[i]));
      check(steps.every((s) => Math.abs(s - PEEK) <= 1), `many: the same ${PEEK} px peek ${w}`);
      check(g.sh > g.ch + 600 && Math.abs(g.sh - (32 + PEEK * 34 + CARD_H)) <= 2, `many: the deck is as tall as its cards and scrolls ${g.sh}/${g.ch} ${w}`);
      check(g.sw <= g.cw, `many: no sideways overflow ${w}`);
      writeFileSync(join(OUT, `many-top-${w}.png`), await shoot(p, ''));
      await ev(p, `document.getElementById('five-menu').scrollTop=1e6`); await sleep(200);
      check(await ev(p, `(()=>{const b=[...document.querySelectorAll('.fp-menu-card')].at(-1).getBoundingClientRect();return b.bottom<=innerHeight+1&&b.top>=0;})()`), `many: the last card is reachable ${w}`);
      writeFileSync(join(OUT, `many-bottom-${w}.png`), await shoot(p, ''));
      await p.dispose();
    });

    await step('old', async () => {
      host(['wf1']);
      const p = await newPage(B, 390, 844, 'light', { allow: [web.url, fake.relay + '/'], touch: true });
      await pair(p, 'zh');
      await openMenu(p, 5);
      check(await ev(p, `!document.querySelector('.jc-card')`), 'old host: five cards only');
      await click(p, '#orb'); await sleep(150);
      await ev(p, `document.getElementById('setBtn').click()`); await waitFor(p, `!document.getElementById('settings').hidden`);
      check(await ev(p, `document.getElementById('set-cards-nav').hidden`), 'old host: no Settings row');
      await p.dispose();
    });
    writeFileSync(join(OUT, 'browser.json'), JSON.stringify({ ok: true, checks, results }, null, 2));
    console.log(`P360 PASS ${checks} checks → reports/qa/p360/`);
  } finally { await B.close(); await fake.stop(); await web.stop(); rec.write(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) await runP360();
