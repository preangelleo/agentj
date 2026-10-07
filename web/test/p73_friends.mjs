#!/usr/bin/env node
// P73 (ADR-A176) — the three friend additions in a real headless Chromium (test/browser.mjs, never :9222) against the fake
// relay + host (test/fakehost.mjs: real Noise; every signed write and friend answer is signature-checked there like the host).
//   1. `/add-friend` typed in the main chat: a wrong check character is told at once (nothing sent, the text stays); a valid
//      ID (also lower case with spaces) + note = the page's own signed fr_add, then 「已发出」; no ID → 「加好友」 opens
//   2. `/my-agent-id`'s answer page: 「打开我的名片」 under it; a `fr_add:<ID>` answer opens 「加好友」 prefilled; the answer's
//      links …/friends#card and …/friends#add=… open the screens in place; a `#card` address opens 我的名片
//   3. the friend details' 「补充设定」: loaded with fr_ctx_get, saved as a signed fr_ctx, 4000 characters max (4001 = save off)
//   4. the friend-request card: 「同意」 with a 「补充设定」 signs one more line (host-verified) and carries `ctx`
//   5. every shot audited: no horizontal overflow, tap targets ≥ 44 px, zero console errors / CSP violations, nothing off-site
// Shots + results: reports/qa/p73/friends/ (AJ_P73_SHOTS overrides).   Run: node web/test/p73_friends.mjs
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot as shot0, sleep } from './browser.mjs';
import { dictionaries } from '../build.mjs';

const OUT = process.env.AJ_P73_SHOTS || fileURLToPath(new URL('../../../reports/qa/p73/friends/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const D = dictionaries(), ZH = D.zh, EN = D.en;
const fmt = (s, v = {}) => s.replace(/\{(\w+)\}/g, (m, k) => (k in v ? String(v[k]) : m)).replace(/`/g, '');
const failures = [], results = {};
const check = (ok, msg) => { if (!ok) failures.push(msg); console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); return ok; };
const shots = [];
async function shoot(p, name) { const f = join(OUT, `${name}.png`); writeFileSync(f, await shot0(p, f)); shots.push(f); console.log(`      shot ${f}`); }
async function audit(p, label) {
  const r = await evaluate(p, `(() => {
    const vis = (e) => { if (!e.getClientRects().length) return false; const cs = getComputedStyle(e); return cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
    const small = [...document.querySelectorAll('button, a[href], summary, input:not([type=file]), select, textarea')].filter(vis).filter((e) => !e.closest('[hidden]'))
      .filter((e) => !e.closest('.words, .rdwords'))     // links inside a reply are text, not controls
      .map((e) => ({ e, r: e.getBoundingClientRect() })).filter(({ r }) => r.height < 44 || r.width < 44).map(({ e, r }) => (e.id || e.className || e.tagName) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
    const wide = [...document.querySelectorAll('body *')].filter(vis).filter((e) => { const r = e.getBoundingClientRect(); return r.right > innerWidth + 1 && getComputedStyle(e).position !== 'fixed'; })
      .filter((e) => !e.closest('.tablewrap, .codeblock, pre, .hash, .tray, .deck, .rd, .fr-usage-wrap')).slice(0, 5).map((e) => e.id || e.className || e.tagName);
    return { sw: document.documentElement.scrollWidth, iw: innerWidth, small, wide };
  })()`);
  check(r.sw <= r.iw && !r.wide.length, `${label}: no horizontal overflow${r.wide.length ? ' — ' + r.wide.join(', ') : ''}`);
  check(!r.small.length, `${label}: tap targets ≥ 44 px${r.small.length ? ' — ' + r.small.join(', ') : ''}`);
  check(!p.problems.length, `${label}: zero console errors / CSP violations${p.problems.length ? ' — ' + p.problems.join(' | ') : ''}`);
  check(!p.offsite.length, `${label}: nothing leaves the origin${p.offsite.length ? ' — ' + p.offsite.join(' | ') : ''}`);
}
const click = (p, sel) => evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ${sel.replace(/'/g, '')}'); e.click(); return true; })()`);
const txt = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)})?.textContent ?? null`);
const lastWrite = (F) => F.writes[F.writes.length - 1];
async function setValue(p, sel, v) {
  await evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.value = ${JSON.stringify(v)}; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
}
const until = async (fn, ms = 4000) => { for (let t = 0; t < ms; t += 50) { if (await fn()) return true; await sleep(50); } return false; };
async function typeSend(p, text) {
  await setValue(p, '#input', text);
  await sleep(80);
  await click(p, '#send');
}
const view = (p) => evaluate(p, `document.body.dataset.view + '/' + (document.getElementById('friends-view').dataset.fv || '')`);

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const BASE = web.url;
const fake = await startFakeHost();
const ALLOW = [BASE, fake.relay + '/'];
const B = await launch();
const ADD_ID = 'AJ-PH8E-AJT4-26GJ-ACQ9';     // a valid ID (protocol/vectors/peer-id.json)
const BAD_ID = 'AJ-PH8E-AJT4-26GJ-ACQX';     // the same with the check character wrong
try {
  for (const [tag, w, h, scheme, lang] of [['mobile-light-zh', 390, 844, 'light', 'zh'], ['mobile-dark-en', 360, 800, 'dark', 'en']]) {
    const L = lang === 'en' ? EN : ZH;
    fake.reset();
    const F = fake.friends(sampleFriends());
    const p = await newPage(B, w, h, scheme, { allow: ALLOW, touch: true });
    await navigate(p, fake.newPairing(BASE + (lang === 'en' ? '?lang=en' : '')));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await waitFor(p, `document.body.dataset.view === 'chat'`);

    // ---------- 1. /add-friend in the main chat
    const n0 = F.writes.length;
    await typeSend(p, '/add-friend ' + BAD_ID);
    check(await until(() => evaluate(p, `document.getElementById('toast').textContent === ${JSON.stringify(fmt(L['fr.add.badCheck'], { id: BAD_ID }))}`)),
      `${tag} /add-friend wrong check character: told at once (${await txt(p, '#toast')})`);
    check(F.writes.length === n0 && (await evaluate(p, `document.getElementById('input').value`)) === '/add-friend ' + BAD_ID && fake.st.says.length === 0,
      `${tag} /add-friend wrong check character: nothing sent, the text stays to fix`);
    await shoot(p, `p73-addfriend-badcheck.${tag}`);
    await typeSend(p, '/add-friend hello');
    check(await until(() => evaluate(p, `document.getElementById('toast').textContent === ${JSON.stringify(fmt(L['fr.add.badFormat'], { id: 'hello' }))}`)), `${tag} /add-friend not an ID: told`);
    await typeSend(p, '/add-friend ' + ADD_ID.toLowerCase().replace(/-/g, ' ') + ' 你好，我是王姐，对一下打样排期');
    check(await until(() => F.writes.length === n0 + 1), `${tag} /add-friend valid: one write`);
    const wr = lastWrite(F);
    check(wr?.t === 'fr_add' && wr.id === ADD_ID && wr.note === '你好，我是王姐，对一下打样排期' && wr.sigOk === true,
      `${tag} /add-friend valid: the page's signed fr_add (canonical ID + note, host-verified) ${JSON.stringify({ ...wr, sig: undefined, n: undefined })}`);
    check(await until(() => evaluate(p, `document.body.dataset.view === 'friends' && !!document.getElementById('fr-sent') && document.getElementById('input').value === ''`)),
      `${tag} /add-friend valid: 「已发出」 on the friends page, the field cleared`);
    check(fake.st.says.length === 0, `${tag} /add-friend never reaches the host as a chat message`);
    await audit(p, `${tag} add-friend sent`);
    await shoot(p, `p73-addfriend-sent.${tag}`);
    await click(p, '#fr-back'); await click(p, '#fr-back');
    await waitFor(p, `document.body.dataset.view === 'chat'`);
    await typeSend(p, '/add-friend');
    check(await until(async () => (await view(p)) === 'friends/add' && await evaluate(p, `document.getElementById('fr-add-id').value === ''`)), `${tag} /add-friend without an ID opens 「加好友」`);
    await click(p, '#fr-back'); await click(p, '#fr-back');
    await waitFor(p, `document.body.dataset.view === 'chat'`);
    results[`add-friend.${tag}`] = failures.length ? 'fail' : 'pass';

    // ---------- 2. /my-agent-id answer page (as the host composes it) and its links
    const me = F.me.id;
    const reply = (lang === 'en' ? 'Your Agent ID (tap the code block to copy it):' : '你的 Agent ID（点一下代码块即可复制）：') +
      `\n\n\`\`\`\n${me}\n\`\`\`\n\nhttps://m.agentj.app/friends#add=${me}\n\nhttps://m.agentj.app/friends#card`;
    await fake.addTurn({ k: 'cmd', text: '/my-agent-id' }, reply, 'done', { cmd: 'my-agent-id', ok: true, kind: 'ok', open: 'fr_card' });
    await waitFor(p, `!!document.querySelector('#cmdx .cmdopen')`);
    check((await txt(p, '#cmdx .cmdopen')) === L['cmd.openCard'], `${tag} /my-agent-id: 「${L['cmd.openCard']}」 under the answer`);
    check(await evaluate(p, `[...document.querySelectorAll('#words pre code, #words code')].some((c) => c.textContent.trim() === ${JSON.stringify(me)})`), `${tag} /my-agent-id: the ID alone in a code block`);
    check(await evaluate(p, `!!document.querySelector('#words .copy')`), `${tag} /my-agent-id: the code block has its copy button`);
    await audit(p, `${tag} my-agent-id page`);
    await shoot(p, `p73-my-agent-id.${tag}`);
    await click(p, '#cmdx .cmdopen');
    check(await until(async () => (await view(p)) === 'friends/card' && (await txt(p, '#fr-my-id')) === me), `${tag} 「打开我的名片」 opens 我的名片 with my ID`);
    await click(p, '#fr-back'); await click(p, '#fr-back');
    await waitFor(p, `document.body.dataset.view === 'chat'`);
    await evaluate(p, `window.__p73mark = 1`);             // the same document after the click = no reload, no navigation
    const links = await evaluate(p, `[...document.querySelectorAll('#words a[href]')].map((a) => a.getAttribute('href'))`);
    check(links.includes('https://m.agentj.app/friends#card'), `${tag} the answer's links are links (${links.join(' | ')})`);
    await evaluate(p, `[...document.querySelectorAll('#words a[href]')].find((a) => a.getAttribute('href').endsWith('#card')).click()`);
    check(await until(async () => (await view(p)) === 'friends/card'), `${tag} …/friends#card link opens 我的名片 in place`);
    check(await evaluate(p, `window.__p73mark === 1 && location.origin === ${JSON.stringify(new URL(BASE).origin)}`), `${tag} no navigation away`);
    await click(p, '#fr-back'); await click(p, '#fr-back');
    await waitFor(p, `document.body.dataset.view === 'chat'`);
    await evaluate(p, `[...document.querySelectorAll('#words a[href]')].find((a) => a.getAttribute('href').includes('#add=')).click()`);
    check(await until(async () => (await view(p)) === 'friends/add' && await evaluate(p, `document.getElementById('fr-add-id').value === ${JSON.stringify(me)}`)), `${tag} …/friends#add= link opens 「加好友」 prefilled`);
    await click(p, '#fr-back'); await click(p, '#fr-back');
    await waitFor(p, `document.body.dataset.view === 'chat'`);
    // a /add-friend answer from the host (older page / menu): 「打开加好友」, prefilled
    await fake.addTurn({ k: 'cmd', text: '/add-friend ' + ADD_ID }, 'ID OK', 'done', { cmd: 'add-friend', ok: true, kind: 'info', open: 'fr_add:' + ADD_ID });
    await waitFor(p, `document.querySelector('#cmdx .cmdopen')?.dataset.open === ${JSON.stringify('fr_add:' + ADD_ID)}`);
    check((await txt(p, '#cmdx .cmdopen')) === L['cmd.openAdd'], `${tag} host /add-friend answer: 「${L['cmd.openAdd']}」`);
    await click(p, '#cmdx .cmdopen');
    check(await until(async () => (await view(p)) === 'friends/add' && await evaluate(p, `document.getElementById('fr-add-id').value === ${JSON.stringify(ADD_ID)}`)), `${tag} 「打开加好友」 opens prefilled`);
    // the address #card (the link opened from outside while the page is open)
    await click(p, '#fr-back'); await click(p, '#fr-back');
    await evaluate(p, `location.hash = '#card'`);
    check(await until(async () => (await view(p)) === 'friends/card' && await evaluate(p, `location.hash === ''`)), `${tag} #card opens 我的名片 and is stripped`);
    results[`my-agent-id.${tag}`] = failures.length ? 'fail' : 'pass';

    // ---------- 3. 「补充设定」 in the friend's details
    await click(p, '#fr-back');
    await waitFor(p, `document.getElementById('friends-view').dataset.fv === 'list'`);
    F.ctx = { [ADD_ID]: '王姐是老客户。' };
    await click(p, `#fr-list .fr-item[data-id="${ADD_ID}"]`);
    await waitFor(p, `!!document.getElementById('fr-open-detail')`);
    await click(p, '#fr-open-detail');
    check(await until(() => evaluate(p, `document.getElementById('fr-ctx') && !document.getElementById('fr-ctx').disabled && document.getElementById('fr-ctx').value === '王姐是老客户。'`)),
      `${tag} details: 「补充设定」 loaded with fr_ctx_get`);
    check(await evaluate(p, `document.getElementById('fr-ctx-save').disabled`), `${tag} details: save off until something changes`);
    const ctx = '王姐是老客户。打样进度可以直接说；报价一律先问我。口气热情一点。';
    await setValue(p, '#fr-ctx', ctx);
    check((await txt(p, '#fr-ctx-count')) === fmt(L['fr.ctx.count'], { n: [...ctx].length, max: 4000 }), `${tag} details: the counter (${await txt(p, '#fr-ctx-count')})`);
    await audit(p, `${tag} details + context`);
    await shoot(p, `p73-context.${tag}`);
    const n1 = F.writes.length;
    await click(p, '#fr-ctx-save');
    check(await until(() => F.writes.length === n1 + 1), `${tag} details: one write`);
    check(lastWrite(F).t === 'fr_ctx' && lastWrite(F).friend === ADD_ID && lastWrite(F).text === ctx && lastWrite(F).sigOk === true && F.ctx[ADD_ID] === ctx,
      `${tag} details: save = a signed fr_ctx the host accepts`);
    check(await until(() => evaluate(p, `document.getElementById('toast').textContent === ${JSON.stringify(L['fr.ctx.saved'])}`)), `${tag} details: 「已保存，从下一条消息起生效」`);
    await setValue(p, '#fr-ctx', '中'.repeat(4000));
    check(await evaluate(p, `!document.getElementById('fr-ctx-save').disabled && document.getElementById('fr-ctx-count').dataset.over === ''`), `${tag} details: exactly 4000 characters can be saved`);
    await setValue(p, '#fr-ctx', '中'.repeat(4000) + '😀');
    check(await evaluate(p, `document.getElementById('fr-ctx-save').disabled && document.getElementById('fr-ctx-count').dataset.over === '1'`), `${tag} details: 4001 characters (an emoji counts as one) = save off, counter red`);
    await shoot(p, `p73-context-over.${tag}`);
    await setValue(p, '#fr-ctx', '');
    await click(p, '#fr-ctx-save');
    check(await until(() => lastWrite(F).t === 'fr_ctx' && lastWrite(F).text === '' && lastWrite(F).sigOk), `${tag} details: an empty text clears it (signed)`);
    check(F.refused.length === 0, `${tag} the page never sent an unknown fr_* message`);
    results[`context.${tag}`] = failures.length ? 'fail' : 'pass';

    // ---------- 4. 「同意」 with a 「补充设定」
    await click(p, '#fr-back'); await click(p, '#fr-back'); await click(p, '#fr-back');
    await waitFor(p, `document.body.dataset.view === 'chat'`);
    const fr = { id: ADD_ID, name: '小鹿采购', owner: '王姐', intro: '深圳灯具工厂', note: '对一下打样排期', groups: F.groups.map((g) => ({ id: g.id, name: g.name })) };
    const aid = 'a'.repeat(31) + (lang === 'en' ? '1' : '2');
    await fake.ask({ id: aid, tool: 'friend_request', summary: 'card ' + tag, ttl: 604800, cat: ['friend'], why: '', fr });
    await waitFor(p, `!!document.getElementById('frAskCtxBox') && !document.getElementById('frAsk').hidden`);
    await evaluate(p, `document.getElementById('frAskCtxBox').open = true`);
    await setValue(p, '#frAskCtx', '老客户，报价先问我');
    await audit(p, `${tag} request card + context`);
    await shoot(p, `p73-request-context.${tag}`);
    const s0 = fake.st.sigOk.length;
    await click(p, '#frAskAllow');
    check(await until(() => fake.st.sigOk.length === s0 + 1), `${tag} request card: one answer`);
    const a = fake.st.answers[fake.st.answers.length - 1];
    check(a.ok === true && a.group === 'default' && a.ctx === '老客户，报价先问我' && fake.st.sigOk[s0] === true,
      `${tag} request card: 同意 + 补充设定 = answer{ok, group, ctx}, the ctx line signed (host-verified) ${JSON.stringify({ ...a, sig: undefined })}`);
    results[`accept-context.${tag}`] = failures.length ? 'fail' : 'pass';
    check(!p.problems.length, `${tag}: zero console errors${p.problems.length ? ' — ' + p.problems.join(' | ') : ''}`);
    await p.dispose();
  }
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
writeFileSync(join(OUT, 'results.json'), JSON.stringify({ suite: 'web/test/p73_friends.mjs', results, failures, shots }, null, 2) + '\n');
console.log(`\n${failures.length ? 'FAIL' : 'PASS'}  p73 friends: ${failures.length} failure(s); shots in ${OUT}`);
process.exit(failures.length ? 1 : 0);
