#!/usr/bin/env node
// P71 (0.16, PROTOCOL §17.7) — the friends page and the two friend cards in a real headless Chromium (test/browser.mjs, never
// :9222) against the fake relay + host (test/fakehost.mjs, friends responder, real Noise; the six signed writes and the
// friend_request answer are signature-checked there exactly like the real host).
//   1. /friends unpaired → pairing hint; pairing from /friends lands on the friends list; the address stays /friends
//   2. list · conversation (read-only, oldest first, auto / owner / undelivered marks) · details (group change, usage with
//      and without a token source, block / unblock, delete) · my card (ID, QR, discoverable, owner / intro) · add a friend
//      (`#add=` fragment stripped + prefilled, check character, "sent" whatever the host says) · groups (edit, new, delete,
//      built-ins not deletable) · fr_changed re-reads within 2 s
//   3. friend_request card (group picked → friendAnswerMessage with the group line; decline = plain deny) and peer_question
//      card (send draft = allow, 「我来说」 = deny + main chat prefilled)
//   4. no field on /friends sends text to a friend (every visible input / textarea / select is one of the allowed ones)
//   5. every shot audited: no horizontal overflow, tap targets ≥ 44 px, zero console errors / CSP violations, nothing off-site
// Shots + results: reports/qa/p71/web/ (AJ_P71_SHOTS overrides).   Run: node web/test/p71_friends.mjs
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost, sampleFriends } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot as shot0, sleep } from './browser.mjs';
import { dictionaries } from '../build.mjs';

const OUT = process.env.AJ_P71_SHOTS || fileURLToPath(new URL('../../../reports/qa/p71/web/', import.meta.url));
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
// The only text fields the friends page may show: the friend ID + note (add), owner name + intro (my card), a group's name /
// numbers / topics; the only select: a friend's group. None of them sends anything to a friend.
const ALLOWED_FIELDS = /^(fr-add-id|fr-add-note|fr-owner|fr-intro|fr-group|fr-g-[a-z_]+|fr-ctx)$/;   // + P73 fr-ctx: the owner's 「补充设定」, never sent to the friend
async function noSendField(p, label) {
  const f = await evaluate(p, `[...document.querySelectorAll('#friends-view input, #friends-view textarea, #friends-view select, #friends-view [contenteditable]')]
    .filter((e) => e.getClientRects().length).map((e) => e.id || e.tagName)`);
  check(f.every((id) => ALLOWED_FIELDS.test(id)), `${label}: no field that sends to a friend (${f.join(',') || 'none'})`);
  return f;
}
const click = (p, sel) => evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ${sel.replace(/'/g, '')}'); e.click(); return true; })()`);
const txt = (p, sel) => evaluate(p, `document.querySelector(${JSON.stringify(sel)})?.textContent ?? null`);
const lastWrite = (F) => F.writes[F.writes.length - 1];
async function typeInto(p, sel, s) {
  await evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.focus(); e.select?.(); return true; })()`);
  await p.send('Input.insertText', { text: s });
}
async function setValue(p, sel, v) {
  await evaluate(p, `(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.value = ${JSON.stringify(v)}; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
}
async function confirmYes(p) { await waitFor(p, `!document.getElementById('confirm').hidden`); await click(p, '#confirm-yes'); }
const until = async (fn, ms = 4000) => { for (let t = 0; t < ms; t += 50) { if (await fn()) return true; await sleep(50); } return false; };

const web = await startWebServer({ port: 0, relayCsp: 'ws://127.0.0.1:*' });
const BASE = web.url;
const fake = await startFakeHost();
const ALLOW = [BASE, fake.relay + '/'];
const B = await launch();
const ADD_ID = 'AJ-PH8E-AJT4-26GJ-ACQ9';     // a valid ID (protocol/vectors/peer-id.json)
try {
  // ---------- 1. unpaired /friends, then pairing from /friends
  {
    const F = fake.friends(sampleFriends());
    const p = await newPage(B, 390, 844, 'light', { allow: ALLOW, touch: true });
    await navigate(p, BASE + 'friends#add=' + ADD_ID.toLowerCase());
    await waitState(p, 'idle');
    check(await evaluate(p, `document.body.dataset.view === 'pair' && !document.getElementById('pair-friends').hidden`), 'unpaired /friends: the pairing screen says to pair first');
    check(await evaluate(p, `location.pathname === '/friends' && location.hash === ''`), 'unpaired /friends: #add= stripped from the address at once');
    await audit(p, 'unpaired');
    await shoot(p, 'friends-unpaired.mobile');
    await p.send('Page.navigate', { url: fake.newPairing(BASE + 'friends') });
    await waitState(p, 'awaiting-approval');
    await fake.approve();
    await waitState(p, 'ready');
    await waitFor(p, `document.body.dataset.view === 'friends'`);
    check(await evaluate(p, `document.getElementById('friends-view').dataset.fv === 'add' && document.getElementById('fr-add-id')?.value === ${JSON.stringify(ADD_ID)}`),
      'after pairing from a friend\'s link: 「加好友」 opens prefilled with the canonical ID');
    check(await evaluate(p, `location.pathname === '/friends'`), 'the address stays /friends');
    await click(p, '#fr-back');
    await waitFor(p, `document.getElementById('friends-view').dataset.fv === 'list' && document.querySelectorAll('#fr-list .fr-item').length === 3`);
    check(true, 'list: three friends from fr_list');
    check(await evaluate(p, `document.querySelectorAll('#fr-pending .fr-pend').length === 1`), 'list: one request waiting');
    check((await txt(p, '.fr-global')) === fmt(ZH['fr.global'], { used: '4.8万', limit: '30万' }), `list: the all-friends total (${await txt(p, '.fr-global')})`);
    check(await evaluate(p, `!!document.querySelector('#fr-list .fr-item[data-id="${ADD_ID}"] .fr-dot')`), 'list: unread dot');
    check(await evaluate(p, `[...document.querySelectorAll('#fr-list .tag')].some((t) => t.textContent === ${JSON.stringify(ZH['fr.blocked'])})`), 'list: a blocked friend is marked');
    await noSendField(p, 'list');
    await audit(p, 'list');
    await shoot(p, 'friends-list.mobile');
    results['friends-route'] = failures.length ? 'fail' : 'pass';

    // ---------- 2a. conversation
    await click(p, `#fr-list .fr-item[data-id="${ADD_ID}"]`);
    await waitFor(p, `document.querySelectorAll('#fr-msgs .fr-msg').length === 5`);
    const msgs = await evaluate(p, `[...document.querySelectorAll('#fr-msgs .fr-msg')].map((li) => ({ dir: li.classList.contains('fr-msg--out') ? 'out' : 'in', meta: li.querySelector('.fr-msg__meta').textContent, text: li.querySelector('.fr-msg__text').textContent }))`);
    check(msgs[0].text === '你好，我是助理一号。' && msgs[4].text.startsWith('主人说这批'), 'conversation: oldest first');
    check(msgs[2].dir === 'out' && msgs[2].meta.includes(ZH['fr.s.auto']), 'conversation: an automatic reply is marked 自动回复');
    check(msgs[4].meta.includes(ZH['fr.s.owner']), 'conversation: an owner-confirmed reply is marked');
    check(msgs[0].meta.includes(ZH['fr.s.undelivered']) && msgs[3].meta.includes(ZH['fr.s.queued_for_owner']), 'conversation: 未送达 / 已转给你决定');
    check(await evaluate(p, `!!document.querySelector('.fr-lock') && !!document.getElementById('fr-tell')`), 'conversation: the read-only notice + 「跟 Agent 说」');
    await waitFor(p, `!!document.querySelector('.fr-today')`);
    check((await txt(p, '.fr-today')) === fmt(ZH['fr.chat.today'], { n: 9, nmax: 50, tok: '1.2万', tmax: '15万' }), `conversation: today\'s usage (${await txt(p, '.fr-today')})`);
    await noSendField(p, 'conversation');
    await audit(p, 'conversation');
    await shoot(p, 'friends-chat.mobile');

    // ---------- 2b. details: usage, group change, block, delete
    await click(p, '#fr-open-detail');
    await waitFor(p, `!!document.getElementById('fr-usage')`);
    const row = await evaluate(p, `[...document.querySelectorAll('#fr-usage td[data-k="tok"]')].map((td) => td.textContent)`);
    check(row[2] === '1.2万 / 15万', `details: tokens today used / limit (${row.join(' | ')})`);
    check(await evaluate(p, `!document.querySelector('.fr-tok-none')`), 'details: a harness token source → no 「—」 note');
    await audit(p, 'details');
    await shoot(p, 'friends-detail.mobile');
    const n0 = F.writes.length;
    await setValue(p, '#fr-group', 'friend');
    await until(() => F.writes.length > n0);
    check(lastWrite(F)?.t === 'fr_set' && lastWrite(F).op === 'group' && lastWrite(F).value === 'friend' && lastWrite(F).sigOk, 'details: group change = a signed fr_set the host accepts');
    check(await until(() => evaluate(p, `document.getElementById('fr-group').value === 'friend'`)), 'details: fr_changed re-read shows the new group');
    await click(p, '#fr-block'); await confirmYes(p);
    await until(() => lastWrite(F)?.op === 'block');
    check(lastWrite(F).sigOk && F.friends.find((f) => f.id === ADD_ID).blocked, 'details: block (confirmed) = signed fr_set op=block');
    await until(() => evaluate(p, `document.getElementById('fr-block').textContent === ${JSON.stringify(ZH['fr.detail.unblock'])}`));
    await click(p, '#fr-block'); await confirmYes(p);
    await until(() => lastWrite(F)?.op === 'unblock');
    check(lastWrite(F).sigOk && !F.friends.find((f) => f.id === ADD_ID).blocked, 'details: unblock');
    // Kai: the harness reports no tokens → 「—」 and the note
    await click(p, '#fr-back'); await click(p, '#fr-back');
    await click(p, '#fr-list .fr-item[data-id="AJ-QNRR-7DZ1-DTBE-79B7"]');
    await waitFor(p, `!!document.getElementById('fr-open-detail')`);
    await click(p, '#fr-open-detail');
    await waitFor(p, `!!document.getElementById('fr-usage')`);
    const krow = await evaluate(p, `[...document.querySelectorAll('#fr-usage td[data-k="tok"]')].map((td) => td.textContent)`);
    check(krow.every((x) => x.startsWith('— /')), `details without a token source: tokens 「—」 (${krow.join(' | ')})`);
    check((await txt(p, '.fr-tok-none')) === ZH['fr.u.noTok'], 'details without a token source: the one-line note');
    check(await evaluate(p, `[...document.querySelectorAll('#fr-usage td[data-k="msg"]')][3].textContent === '140 / ' + ${JSON.stringify(ZH['fr.u.nolimit'])}`), 'details: a null window limit reads 不限');
    await shoot(p, 'friends-detail-notok.mobile');
    await click(p, '#fr-delete'); await confirmYes(p);
    await until(() => lastWrite(F)?.op === 'delete');
    check(lastWrite(F).sigOk && !F.friends.some((f) => f.id === 'AJ-QNRR-7DZ1-DTBE-79B7'), 'details: delete (confirmed) = signed fr_set op=delete');
    check(await until(() => evaluate(p, `document.getElementById('friends-view').dataset.fv === 'list' && document.querySelectorAll('#fr-list .fr-item').length === 2`)), 'details: after delete, back on the list with two friends');
    results['friends-detail'] = failures.length ? 'fail' : 'pass';

    // ---------- 2c. my card
    await click(p, '#fr-open-card');
    await waitFor(p, `!!document.getElementById('fr-my-id')`);
    check((await txt(p, '#fr-my-id')) === F.me.id, 'my card: the Agent ID in large type');
    check(await evaluate(p, `!!document.querySelector('#fr-qr svg path') && document.querySelector('#fr-qr svg path').getAttribute('d').length > 500`), 'my card: a QR code of the share link');
    check(await evaluate(p, `document.getElementById('fr-discoverable').getAttribute('aria-checked') === 'true'`), 'my card: 「允许别人加我」 on');
    await noSendField(p, 'my card');
    await audit(p, 'my card');
    await shoot(p, 'friends-card.mobile');
    await click(p, '#fr-discoverable');
    await until(() => lastWrite(F)?.t === 'fr_discoverable');
    check(lastWrite(F).on === false && lastWrite(F).sigOk && F.me.discoverable === false, 'my card: switching off = signed fr_discoverable');
    check(await until(() => evaluate(p, `document.getElementById('fr-discoverable').getAttribute('aria-checked') === 'false'`)), 'my card: the switch shows off');
    await setValue(p, '#fr-owner', '王利'); await setValue(p, '#fr-intro', '做灯具出口');
    await click(p, '#fr-card-save');
    await until(() => lastWrite(F)?.t === 'fr_card');
    check(lastWrite(F).owner === '王利' && lastWrite(F).intro === '做灯具出口' && lastWrite(F).sigOk, 'my card: owner / intro = signed fr_card');

    // ---------- 2d. add a friend (fragment while open; check character; "sent" always)
    await p.send('Page.navigate', { url: BASE + 'friends#add=' + encodeURIComponent('aj ph8e ajt4 26gj acq9') });
    await waitFor(p, `document.getElementById('friends-view').dataset.fv === 'add'`);
    check(await evaluate(p, `location.hash === '' && document.getElementById('fr-add-id').value === ${JSON.stringify(ADD_ID)}`), 'add: a #add= link opened while the page is open → stripped + prefilled');
    await setValue(p, '#fr-add-id', 'AJ-PH8E-AJT4-26GJ-ACQX');
    check(await evaluate(p, `document.getElementById('fr-add-hint').dataset.ok === '0' && document.getElementById('fr-add-go').disabled`), 'add: a wrong check character is refused before sending');
    await shoot(p, 'friends-add-bad.mobile');
    await setValue(p, '#fr-add-id', ADD_ID.toLowerCase().replace(/-/g, ' '));
    await typeInto(p, '#fr-add-note', 'Leo 让我联系你，对一下 10 月的打样排期');
    check(await evaluate(p, `document.getElementById('fr-add-hint').dataset.ok === '1' && !document.getElementById('fr-add-go').disabled`), 'add: a valid ID enables 「发送请求」');
    await noSendField(p, 'add');
    await audit(p, 'add');
    await shoot(p, 'friends-add.mobile');
    await click(p, '#fr-add-go');
    await waitFor(p, `!!document.getElementById('fr-sent')`);
    check(lastWrite(F).t === 'fr_add' && lastWrite(F).id === ADD_ID && lastWrite(F).note.startsWith('Leo') && lastWrite(F).sigOk, `add: signed fr_add with the canonical ID + note (${JSON.stringify({ ...lastWrite(F), sig: undefined })})`);
    check((await txt(p, '.fr-sent__title')) === ZH['fr.add.sent'], 'add: 「已发出，等待对方确认」');
    await shoot(p, 'friends-add-sent.mobile');
    // Q5: a refusal on the host side reads the same
    fake.st.friends.writes.length; const origOn = fake.st.onApp;
    fake.st.onApp = async (c, m, send) => { if (m.t === 'fr_add') { await send({ t: 'ctl_res', r: m.r, ok: false, why: 'not_found' }); return true; } return false; };
    await click(p, '#fr-sent-back'); await click(p, '#fr-open-add');
    await setValue(p, '#fr-add-id', ADD_ID);
    await click(p, '#fr-add-go');
    check(await until(() => evaluate(p, `!!document.getElementById('fr-sent')`)), 'add: a host-side "no" shows the same 「已发出，等待对方确认」 (Q5)');
    fake.st.onApp = origOn;
    results['friends-card-add'] = failures.length ? 'fail' : 'pass';

    // ---------- 2e. groups
    await click(p, '#fr-sent-back');
    await click(p, '#fr-open-groups');
    await waitFor(p, `document.querySelectorAll('#fr-groups .fr-gitem').length === 4`);
    check(true, 'groups: three built-ins + one of mine');
    await audit(p, 'groups');
    await shoot(p, 'friends-groups.mobile');
    await click(p, '#fr-groups .fr-gitem[data-id="default"]');
    await waitFor(p, `!!document.getElementById('fr-g-msg_day')`);
    check(await evaluate(p, `!document.getElementById('fr-g-delete') && document.getElementById('fr-g-name').disabled`), 'group: a built-in cannot be deleted or renamed');
    check(await evaluate(p, `document.getElementById('fr-g-msg_day').value === '50' && document.getElementById('fr-g-tok_month').value === '1500000'`), 'group: the ADR numbers are shown');
    await noSendField(p, 'group');
    await audit(p, 'group edit');
    await shoot(p, 'friends-group.mobile');
    await setValue(p, '#fr-g-msg_day', 'abc');
    await click(p, '#fr-g-save');
    check(await evaluate(p, `!document.getElementById('fr-g-err').hidden`), 'group: a bad number is refused on the phone');
    await setValue(p, '#fr-g-msg_day', '20');
    await evaluate(p, `document.querySelector('#fr-g-mode button[data-mode="off"]').click()`);
    await click(p, '#fr-g-save');
    await until(() => lastWrite(F)?.t === 'pg_set');
    const pg = lastWrite(F).group;
    check(lastWrite(F).sigOk && pg.id === 'default' && pg.limits.msg.day === 20 && pg.auto.mode === 'off' && pg.limits.tok.day === 150000 && pg.builtin === true,
      'group: save = signed pg_set over the canonical group');
    await waitFor(p, `document.querySelectorAll('#fr-groups .fr-gitem').length === 4`);
    await click(p, '#fr-group-new');
    await setValue(p, '#fr-g-name', '供应商');
    await setValue(p, '#fr-g-allow', '寒暄\n交期');
    await click(p, '#fr-g-save');
    await until(() => lastWrite(F)?.t === 'pg_set' && lastWrite(F).group.name === '供应商');
    check(lastWrite(F).sigOk && /^g-[0-9a-f]{8}$/.test(lastWrite(F).group.id) && lastWrite(F).group.builtin === false && lastWrite(F).group.auto.allow.join('|') === '寒暄|交期', 'group: a new group = signed pg_set with a fresh id');
    await waitFor(p, `document.querySelectorAll('#fr-groups .fr-gitem').length === 5`);
    await click(p, '#fr-groups .fr-gitem[data-id="g-vip"]');
    await waitFor(p, `!!document.getElementById('fr-g-delete')`);
    await click(p, '#fr-g-delete'); await confirmYes(p);
    await until(() => lastWrite(F)?.t === 'pg_del');
    check(lastWrite(F).id === 'g-vip' && lastWrite(F).sigOk && !F.groups.some((g) => g.id === 'g-vip'), 'group: delete my group = signed pg_del');
    results['friends-groups'] = failures.length ? 'fail' : 'pass';

    // ---------- 2f. fr_changed: something changed on the computer → re-read within 2 s
    await click(p, '#fr-back');
    await waitFor(p, `document.getElementById('friends-view').dataset.fv === 'list'`);
    F.friends.push({ id: 'AJ-NEW0-0000-0000-0009', name: '新朋友', owner: '', intro: '', group: 'default', state: 'friend', blocked: false, last: Date.now(), unread: 1 });
    const t0 = Date.now();
    await fake.send({ t: 'fr_changed', friend: 'AJ-NEW0-0000-0000-0009' });
    const seen = await until(() => evaluate(p, `document.querySelectorAll('#fr-list .fr-item').length === 3`), 2000);
    check(seen, `fr_changed: the list shows the new friend in ${Date.now() - t0} ms (≤ 2 s)`);
    check(F.refused.length === 0, 'the page never sent an unknown fr_* message');
    results['friends-changed'] = seen ? 'pass' : 'fail';

    // ---------- 3. the cards in the main chat
    await click(p, '#fr-back');
    await waitFor(p, `document.body.dataset.view === 'chat' && location.pathname === '/'`);
    check(true, 'back from the list → the main chat, address /');
    const summary = '小鹿采购（主人：王姐）想加你为好友\n深圳灯具工厂，负责报价和打样\n附言：Leo 让我联系你，对一下 10 月的打样排期';
    const fr = { id: ADD_ID, name: '小鹿采购', owner: '王姐', intro: '深圳灯具工厂，负责报价和打样', note: 'Leo 让我联系你，对一下 10 月的打样排期',
      groups: F.groups.map((g) => ({ id: g.id, name: g.name })) };
    await fake.ask({ id: 'f'.repeat(32), tool: 'friend_request', summary, ttl: 604800, cat: ['friend'], why: '', fr });
    await waitFor(p, `document.body.dataset.sheet === '1' && !document.getElementById('frAsk').hidden`);
    check((await txt(p, '#sheetTitle')) === ZH['fr.ask.title'], 'friend request card: title');
    check(await evaluate(p, `document.getElementById('frAskGroup').value === 'default' && document.getElementById('cmdBox').hidden && document.getElementById('apprBtns').hidden && document.getElementById('askLeft').hidden`),
      'friend request card: group defaults to 默认; the tool box, hold buttons and seconds countdown are hidden');
    check((await txt(p, '.fr-askcard__note')) === fmt(ZH['fr.ask.note'], { note: fr.note }), 'friend request card: the note');
    await audit(p, 'friend request card');
    await shoot(p, 'card-friend-request.mobile');
    await setValue(p, '#frAskGroup', 'colleague');
    await click(p, '#frAskAllow');
    await until(() => fake.st.sigOk.length === 1);
    const a1 = fake.st.answers[0];
    check(a1.ok === true && a1.group === 'colleague' && fake.st.sigOk[0] === true, `friend request: 同意 + group = answer{ok,group} signed with friendAnswerMessage (host verified) (${JSON.stringify({ ...a1, sig: undefined })} ${fake.st.sigOk})`);
    await waitFor(p, `document.body.dataset.sheet === '0' || document.getElementById('frAsk').hidden || !!document.getElementById('frAskState')`);
    await fake.ask({ id: 'e'.repeat(32), tool: 'friend_request', summary: summary + '2', ttl: 604800, cat: ['friend'], why: '', fr });
    await waitFor(p, `!!document.getElementById('frAskDeny') && !document.getElementById('frAsk').hidden`);
    await click(p, '#frAskDeny');
    await until(() => fake.st.sigOk.length === 2);
    check(fake.st.answers[1].ok === false && !('group' in fake.st.answers[1]) && fake.st.sigOk[1] === true, 'friend request: 拒绝 = a plain signed deny');
    // peer_question: send the draft
    const pq = { friend: ADD_ID, name: '小鹿采购', text: '500 个打样单价能不能降到 3.2 美元？', draft: '主人说这批先按 3.5，量到 2000 可以谈 3.2。', reason: '涉及报价（策略组「默认」要求先问你）' };
    const pqSummary = `小鹿采购：${pq.text}\n分身拟的回复：${pq.draft}`;
    await sleep(300);
    await fake.ask({ id: 'd'.repeat(32), tool: 'peer_question', summary: pqSummary, ttl: 3600, cat: ['send'], why: pq.reason, pq });
    await waitFor(p, `!!document.getElementById('frQSend') && !document.getElementById('frAsk').hidden`);
    check((await txt(p, '#sheetTitle')) === fmt(ZH['fr.q.title'], { name: '小鹿采购' }), 'friend question card: title');
    check(await evaluate(p, `!!document.getElementById('frQSkip') && !!document.getElementById('frQTell') && document.querySelector('.fr-askcard__draft').textContent.includes('3.5')`), 'friend question card: draft + 照草稿回 / 不回 / 我来说');
    await audit(p, 'friend question card');
    await shoot(p, 'card-peer-question.mobile');
    await click(p, '#frQSend');
    await until(() => fake.st.sigOk.length === 3);
    check(fake.st.answers[2].ok === true && fake.st.sigOk[2] === true, 'friend question: 照草稿回 = signed allow');
    await sleep(300);
    await fake.ask({ id: 'c'.repeat(32), tool: 'peer_question', summary: pqSummary + '?', ttl: 3600, cat: ['send'], why: pq.reason, pq });
    await waitFor(p, `!!document.getElementById('frQTell') && !document.getElementById('frAsk').hidden`);
    await click(p, '#frQTell');
    await until(() => fake.st.sigOk.length === 4);
    check(fake.st.answers[3].ok === false && fake.st.sigOk[3] === true, 'friend question: 我来说 = signed deny');
    check(await until(() => evaluate(p, `document.getElementById('input').value === ${JSON.stringify(fmt(ZH['fr.tellPrefix'], { name: '小鹿采购' }))} && document.body.dataset.view === 'chat'`)),
      'friend question: 我来说 opens the main chat prefilled 「告诉 小鹿采购：」');
    await sleep(400);
    await shoot(p, 'card-peer-question-tell.mobile');
    check(!p.problems.length, `cards: zero console errors${p.problems.length ? ' — ' + p.problems.join(' | ') : ''}`);
    results['friend-cards'] = failures.length ? 'fail' : 'pass';
    await p.dispose();
  }

  // ---------- desktop (two columns) + dark + English
  for (const [tag, w, h, scheme, lang] of [['desktop-light-zh', 1440, 900, 'light', 'zh'], ['mobile-dark-en', 360, 800, 'dark', 'en'], ['desktop-dark-en', 1440, 900, 'dark', 'en']]) {
    fake.reset();
    const F = fake.friends(sampleFriends());
    const p = await newPage(B, w, h, scheme, { allow: ALLOW });
    await navigate(p, fake.newPairing(BASE + 'friends' + (lang === 'en' ? '?lang=en' : '')));
    await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
    await waitFor(p, `document.querySelectorAll('#fr-list .fr-item').length === 3`);
    if (lang === 'en') check((await txt(p, '#fr-title')) === EN['fr.title'] && (await txt(p, '#fr-open-add')) === EN['fr.add.btn'], `${tag}: English`);
    await audit(p, `${tag} list`);
    await shoot(p, `friends-list.${tag}`);
    await click(p, `#fr-list .fr-item[data-id="${ADD_ID}"]`);
    await waitFor(p, `document.querySelectorAll('#fr-msgs .fr-msg').length === 5`);
    if (w > 960) check(await evaluate(p, `getComputedStyle(document.getElementById('fr-side')).display !== 'none' && getComputedStyle(document.getElementById('fr-main')).display !== 'none'`), `${tag}: list and conversation side by side`);
    await noSendField(p, `${tag} conversation`);
    await audit(p, `${tag} conversation`);
    await shoot(p, `friends-chat.${tag}`);
    await click(p, '#fr-open-card');
    await waitFor(p, `!!document.getElementById('fr-qr')`);
    await audit(p, `${tag} card`);
    await shoot(p, `friends-card.${tag}`);
    await click(p, '#fr-back');
    await click(p, '#fr-open-groups');
    await waitFor(p, `!!document.getElementById('fr-groups')`);
    await click(p, '#fr-groups .fr-gitem[data-id="friend"]');
    await waitFor(p, `!!document.getElementById('fr-g-more')`);
    await evaluate(p, `document.getElementById('fr-g-more').open = true`);
    await audit(p, `${tag} group`);
    await shoot(p, `friends-group.${tag}`);
    // the friend-request card in this theme / language
    await click(p, '#fr-back'); await click(p, '#fr-back'); await click(p, '#fr-back');
    await waitFor(p, `document.body.dataset.view === 'chat'`);
    await fake.ask({ id: 'b'.repeat(32), tool: 'friend_request', summary: 'x', ttl: 604800, cat: ['friend'], why: '',
      fr: { id: ADD_ID, name: '小鹿采购', owner: '王姐', intro: '深圳灯具工厂', note: '对一下打样排期', groups: F.groups.map((g) => ({ id: g.id, name: g.name })) } });
    await waitFor(p, `!document.getElementById('frAsk').hidden`);
    await audit(p, `${tag} friend request card`);
    await shoot(p, `card-friend-request.${tag}`);
    await p.dispose();
    results[`friends-${tag}`] = 'pass';
  }
} finally {
  await B.close(); await fake.stop(); await web.stop();
}
writeFileSync(join(OUT, 'results.json'), JSON.stringify({ suite: 'web/test/p71_friends.mjs', results, failures, shots }, null, 2) + '\n');
console.log(`\n${failures.length ? 'FAIL' : 'PASS'}  p71 friends: ${failures.length} failure(s); shots in ${OUT}`);
process.exit(failures.length ? 1 : 0);
