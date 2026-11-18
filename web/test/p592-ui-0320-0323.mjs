#!/usr/bin/env node
// P592 (chairman INCOMING-0320 / 0321 / 0323): real client + fake host. Five surfaces, 390 + 1440 × zh / en × light / dark:
//   contact  — Settings → 通讯录 → 新增联系人: 取消 and 保存 the same size, 保存 in Lotion with Sooty text (0320)
//   calendar — the calendar card's add editor; Ctrl/Cmd+Enter saves (0321)
//   notes    — the notes / to-do editor: the same chord through the same helper, with the hint (0321)
//   sudo     — the admin-password card, then the host refusing it because the context changed (0323 ⑥)
//   approval — a v2 approval card, then `answer_refused authority_changed` (0323 ⑥): put away at once, never left tappable
// P592_PHASE=before only captures (run it against an older public/ via P592_PUBLIC); after also asserts the behaviour.
// Synthetic data only; screenshots are page pixels (no address bar, no pairing link).
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { randomBytes } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate as ev, shoot, sleep, key } from './browser.mjs';
import * as wire from '../../protocol/wire.js';
import { elevateChallenge } from '../public/js/faceid.js';

const PHASE = process.env.P592_PHASE === 'before' ? 'before' : 'after', AFTER = PHASE === 'after';
const OUT = resolve(process.env.P592_OUT || resolve(new URL('../../../', import.meta.url).pathname, 'reports/qa/p592'), PHASE);
mkdirSync(OUT, { recursive: true });
const q = JSON.stringify, hex = (n) => randomBytes(n).toString('hex');
const click = (p, s) => ev(p, `document.querySelector(${q(s)}).click()`);
const rows = [];
let checks = 0;
const check = (v, name) => { assert.ok(v, name); checks++; };
const ONLY = (process.env.P592_ONLY || '').split(',').filter(Boolean);

const web = await startWebServer(process.env.P592_PUBLIC ? { publicDir: process.env.P592_PUBLIC } : {});
const fake = await startFakeHost();
const B = await launch();

const BOOK = () => [
  { id: 'c000000000000', name: '我', nicks: [], emails: [{ v: 'owner@example.com', label: '' }], phones: [], letter: 'W' },
  { id: 'c1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6', name: '李雷', nicks: ['老李'], emails: [{ v: 'lei@example.com', label: '' }], phones: [], letter: 'L' },
];

async function paired(w, lang, theme, caps = [], onApp = null) {
  fake.reset();
  fake.st.caps = caps;
  if (onApp) fake.st.onApp = onApp;   // before the page asks for its first lists
  fake.st.prefs = { appearance: { language: lang, theme }, voice: { speak_replies: false, wake_enabled: false }, agent: { session_mode: 'shared' } };
  const p = await newPage(B, w, w < 500 ? 844 : 900, theme, { touch: w < 500, allow: [web.url, fake.relay + '/'] });
  await navigate(p, fake.newPairing(web.url + '?lang=' + lang));
  await waitState(p, 'awaiting-approval'); await fake.approve(); await waitState(p, 'ready');
  await fake.send({ t: 'status', s: 'idle', agent: 'claude', name: 'Agent J' });
  await ev(p, `for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click()}`);
  await sleep(300);
  return p;
}
const freeze = (p) => ev(p, `for(const a of document.getAnimations()){if(a.effect?.getTiming().iterations===Infinity){a.pause();a.currentTime=0;}}`);
async function save(p, tag, scene) {
  await freeze(p); await sleep(250);
  const file = `${OUT}/${tag}-${scene}.png`;
  writeFileSync(file, await shoot(p));
  rows.push({ phase: PHASE, tag, scene, file });
}

// ---------------------------------------------------------------- contact (0320 + 0321)
async function contact(w, lang, theme, tag) {
  const p = await paired(w, lang, theme, ['contacts']);
  const book = BOOK(), saved = [];
  fake.st.onApp = async (c, m, send) => {
    if (!/^ct_/.test(m.t)) return false;
    const res = (x) => send({ t: 'ct_res', r: m.r, ok: true, ...x });
    if (m.t === 'ct_list') await res({ contacts: book, count: book.length, me: ['owner@example.com'], owner: book[0], mail: { ready: true } });
    else if (m.t === 'ct_save') { saved.push(m); const x = { id: 'c9' + String(book.length).padStart(30, '0'), letter: 'H', ...m.contact }; book.push(x); await res({ result: 'added', contact: x }); }
    else await res({});
    return true;
  };
  try {
    await click(p, '#setBtn'); await waitFor(p, `!document.getElementById('settings').hidden`); await sleep(200);
    await click(p, '#set-contacts-nav'); await waitFor(p, `document.querySelectorAll('#ctm-list li').length >= 2`);
    await click(p, '#ctm-add'); await waitFor(p, `!document.getElementById('ctmEdit').hidden`);
    await ev(p, `(() => { const set = (id, v) => { const i = document.getElementById(id); i.value = v; i.dispatchEvent(new Event('input', { bubbles: true })); };
      set('ctm-e-name', ${q(lang === 'zh' ? '韩梅梅' : 'Han Meimei')}); set('ctm-e-email', 'meimei@example.com'); })()`);
    await ev(p, `document.activeElement?.blur()`);
    await save(p, tag, 'contact');
    if (AFTER) {
      const g = await ev(p, `(() => { const r = (id) => document.getElementById(id).getBoundingClientRect().toJSON(), css = getComputedStyle(document.documentElement), s = getComputedStyle(document.getElementById('ctm-e-save'));
        return { c: r('ctm-e-cancel'), s: r('ctm-e-save'), text: document.getElementById('ctm-e-save').textContent.trim(), bg: s.backgroundColor, fg: s.color, lotion: css.getPropertyValue('--c-lotion').trim(), sooty: css.getPropertyValue('--c-sooty').trim() }; })()`);
      const rgb = (h) => `rgb(${parseInt(h.slice(1, 3), 16)}, ${parseInt(h.slice(3, 5), 16)}, ${parseInt(h.slice(5, 7), 16)})`;
      check(Math.abs(g.c.width - g.s.width) < 1 && Math.abs(g.c.height - g.s.height) < 1, `cancel and save same size ${tag} ${q(g)}`);
      check(g.s.height >= 44, 'save ≥ 44 px ' + tag);
      check(g.text === (lang === 'zh' ? '保存' : 'Save'), 'save says 保存/Save ' + tag + ' ' + g.text);
      check(g.bg === rgb(g.lotion) && g.fg === rgb(g.sooty), `save is Lotion ground + Sooty text ${tag} ${q(g)}`);
      // 0321: Ctrl+Enter (and ⌘+Enter) in a field saves; a plain Enter while composing does not
      await ev(p, `document.getElementById('ctm-e-name').focus()`);
      await ev(p, `document.getElementById('ctm-e-name').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, isComposing: true, bubbles: true }))`);
      await sleep(200); check(saved.length === 0, 'composing Ctrl+Enter does not save ' + tag);
      await key(p, 'Enter', { ctrl: w < 500, meta: w >= 500, keyCode: 13 });
      await waitFor(p, `document.getElementById('ctmEdit').hidden`, 4000);
      check(saved.length === 1 && saved[0].contact.emails[0].v === 'meimei@example.com', 'Ctrl/Cmd+Enter saved the contact ' + tag);
    }
  } finally { await p.dispose?.(); }
}

// ---------------------------------------------------------------- calendar (0321)
async function calendar(w, lang, theme, tag) {
  const p = await paired(w, lang, theme, ['jcards1']);
  const events = [], ops = [];
  let revision = 0;
  const cards = [{ id: 'calendar', name: { zh: '日历', en: 'Calendar' }, desc: { zh: '共同管理日程', en: 'Your shared schedule' }, icon: 'calendar', accent: 'windstorm', official: true, on: true, plus: 'agentj-calendar' }];
  fake.st.onApp = async (c, m, send) => {
    const reply = async (t, v) => { await send({ t, r: m.r, ...v }); return true; };
    if (m.t === 'jcard_list') return reply('jcard_items', { cards });
    if (m.t === 'jcard_get') return reply('jcard_get', { ok: true, id: 'calendar', template: 'calendar', events, revision: String(revision), plus: 'agentj-calendar', actions: [], anchors: [], links: [] });
    if (m.t === 'jcard_cal') { ops.push(m); if (m.op === 'add') events.push({ ...m.event, id: 'event-' + revision, readonly: false, end: m.event.end || m.event.start }); revision++; return reply(m.t, { ok: true }); }
    return false;
  };
  try {
    await ev(p, "import('./js/jcards.js').then(m=>m.refresh())");
    await ev(p, "import('./js/five-pages.js').then(m=>m.openTarget('calendar'))");
    await waitFor(p, "document.querySelectorAll('.jc-cal-grid button').length===42");
    await click(p, '.jc-cal-grid button[aria-pressed=true]'); await waitFor(p, "document.querySelector('.jc-cal-dialog[open]')");
    await ev(p, `(() => { const i = document.querySelector('.jc-cal-dialog [name=title]'); i.value = ${q(lang === 'zh' ? '与 Agent J 对一下周计划' : 'Weekly plan with Agent J')}; })()`);
    await ev(p, `document.activeElement?.blur()`);
    await save(p, tag, 'calendar');
    if (AFTER) {
      const hint = await ev(p, `document.querySelector('.jc-cal-dialog .aj-save-key')?.textContent || ''`);
      if (w < 500) check(hint === '', 'phone shows no keyboard hint ' + tag);
      else check(/Enter/.test(hint), 'desktop shows the shortcut beside 保存 ' + tag + ' ' + hint);
      // a multi-line note keeps plain Enter as a newline
      await ev(p, `document.querySelector('.jc-cal-dialog [name=note]').focus()`);
      await key(p, 'Enter', { keyCode: 13, text: '\r' });
      await sleep(200); check(ops.length === 0, 'plain Enter in the note does not save ' + tag);
      await key(p, 'Enter', { ctrl: true, keyCode: 13 });
      await waitFor(p, "!document.querySelector('.jc-cal-dialog')", 4000);
      check(ops.length === 1 && ops[0].op === 'add', 'Ctrl+Enter saved the event ' + tag);
    }
  } finally { await p.dispose?.(); }
}

// ---------------------------------------------------------------- notes editor (0321: the P415 chord, now the shared helper)
async function notes(w, lang, theme, tag) {
  const ops = [];
  const entries = [{ id: 'n1', text: lang === 'zh' ? '周五前把报价单发给老李' : 'Send the quote to Lei by Friday', editable: true }];
  fake.st.onApp = async (c, m, send) => {
    const reply = async (t, v) => { await send({ t, r: m.r, ...v }); return true; };
    if (m.t === 'jcard_list') return reply('jcard_items', { cards: [{ id: 'notes', name: { zh: '记事本', en: 'Notes' }, on: true, official: true, accent: 'windstorm', plus: 'agentj-add-note' }] });
    if (m.t === 'jcard_get') return reply('jcard_get', { ok: true, id: 'notes', template: 'notes', entries, items: entries, revision: 'rev' });
    if (m.t === 'jcard_note') { ops.push(m); return reply('jcard_note', { ok: true, id: m.id, item: { id: 'n2' } }); }
    return false;
  };
  const p = await paired(w, lang, theme, ['jcards1'], fake.st.onApp);
  try {
    await ev(p, "import('./js/jcards.js').then(m=>m.refresh())");
    await ev(p, "import('./js/five-pages.js').then(m=>m.openTarget('notes'))");
    await waitFor(p, "document.querySelector('.jc-body[data-card=notes] .jc-action')");
    await ev(p, "document.querySelector('.jc-action[data-act=add]').click()"); await waitFor(p, "document.querySelector('.jc-dialog[open] textarea')");
    await ev(p, `document.querySelector('.jc-dialog[open] textarea').value = ${q(lang === 'zh' ? '下周一和 Agent J 复盘' : 'Review with Agent J on Monday')}; document.activeElement?.blur()`);
    await save(p, tag, 'notes');
    if (AFTER) {
      await ev(p, "document.querySelector('.jc-dialog[open] textarea').focus()");
      await key(p, 'Enter', { ctrl: true, keyCode: 13 });
      await waitFor(p, "!document.querySelector('.jc-dialog')", 4000);
      check(ops.length === 1 && ops[0].op === 'add', 'Ctrl+Enter saved the note ' + tag);
    }
  } finally { await p.dispose?.(); }
}

// ---------------------------------------------------------------- sudo card + context refusal (0323 ⑥)
async function sudo(w, lang, theme, tag) {
  const p = await paired(w, lang, theme);
  try {
    const kp = await crypto.subtle.generateKey({ name: 'X25519' }, true, ['deriveBits']);
    const epk = wire.b64u(new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey)));
    const id = hex(16);
    await fake.send({ t: 'elev', id, kind: 'sudo', n: hex(16), epk, ttl: 120, tries: 3, cmd: 'sudo systemctl restart example-test.service',
      why: lang === 'zh' ? '升级本机测试服务器，先备份再等空闲' : 'Upgrade the local test server: back up first, wait until idle', effect: '', interpreter: false, reason: '', helper_uv: 0 });
    await waitFor(p, `document.body.dataset.elev === '1'`);
    await ev(p, `document.activeElement?.blur()`);
    await save(p, tag, 'sudo');
    await fake.send({ t: 'elev_refused', id, why: 'context' });
    await sleep(700);
    await save(p, tag, 'sudo-context');
    if (AFTER) check(await ev(p, `document.body.dataset.elev !== '1'`), 'context-changed admin card closed at once ' + tag);
  } finally { await p.dispose?.(); }
}

// ---------------------------------------------------------------- v2 approval card + authority_changed (0323 ⑥)
async function approval(w, lang, theme, tag) {
  const p = await paired(w, lang, theme);
  try {
    const id = '592a' + hex(14);
    const text = lang === 'zh' ? '请在 Agent J 主机上执行下面这条命令，再把结果告诉我。' : 'Please run this command on the Agent J host and tell me the result.';
    const original = await fake.addTurn({ k: 'phone', dev: 'review', name: lang === 'zh' ? '你' : 'You', text }, lang === 'zh' ? '好的，我来发一张确认卡。' : 'OK, sending a confirmation card.');
    await sleep(300);
    const envelope = { v: 2, channel: fake.channel, session: String(fake.st.epoch), request_id: id, revision: 1, nonce: 'nonce-1',
      title: lang === 'zh' ? '升级本机测试服务器' : 'Upgrade the local test server',
      source: { message_id: original.id, root_message_id: original.id, session: String(fake.st.epoch), channel: 'phone', time: Date.now(), sender: lang === 'zh' ? '你' : 'You', text, verified: true, suspicious: false, history_outside_window: false, initiator: 'Agent J', task_record: '', chain: [] },
      action: lang === 'zh' ? '重启本机的测试服务。' : 'Restart the local test service.', details: 'sudo systemctl restart example-test.service', effects: [], plan: null, expires_at: Math.floor(Date.now() / 1000) + 120 };
    const card = { id, tool: 'Bash', summary: envelope.details, ttl: 120, cat: [], why: '', passkey_required: true, fa_s: 'ready', fa: 'c3ludGhldGljLXJldmlldy1jcmVk', fa_ch: '', reason: envelope.action,
      pk_kind: 'sandbox', approval_v: 2, envelope, envelope_sha256: await wire.approvalEnvelopeDigest(envelope), revision: 1, review_state: 'pending' };
    card.fa_ch = wire.b64u(await elevateChallenge(fake.channel, fake.devs.at(-1).id, card.id, card.pk_kind, card.id, card.envelope_sha256));
    await fake.ask(card);
    await waitFor(p, `document.body.dataset.sheet==='1'&&document.getElementById('source541').textContent.includes('Message ID')`);
    await sleep(900);
    await save(p, tag, 'approval');
    await fake.send({ t: 'status', s: 'working', agent: 'claude', name: 'Agent J' });
    await fake.send({ t: 'answer_refused', id, why: 'authority_changed', ask: card });
    await sleep(700);
    await save(p, tag, 'approval-context');
    if (AFTER) {
      check(await ev(p, `document.body.dataset.sheet !== '1'`), 'context-changed approval card put away at once ' + tag);
      check(await ev(p, `document.getElementById('apprAllow').disabled`), 'its approve stays disabled ' + tag);
      // reopened from the tag: no countdown, the reason in place of the buttons' prompt
      await ev(p, `document.getElementById('pendTag')?.click()`); await sleep(500);
      if (await ev(p, `document.body.dataset.sheet === '1'`)) {
        check(await ev(p, `document.getElementById('askLeft').hidden`), 'no countdown on a void card ' + tag);
        check(await ev(p, `document.getElementById('apprAllow').disabled`), 'reopened void card cannot be approved ' + tag);
        await save(p, tag, 'approval-context-reopened');
      }
    }
  } finally { await p.dispose?.(); }
}

const SCENES = { contact, calendar, notes, sudo, approval };
try {
  for (const w of [390, 1440]) for (const lang of ['zh', 'en']) for (const theme of ['light', 'dark']) {
    const tag = `${w}-${lang}-${theme}`;
    for (const [name, fn] of Object.entries(SCENES)) {
      if (ONLY.length && !ONLY.includes(name)) continue;
      await fn(w, lang, theme, tag);
      console.log('ok', PHASE, name, tag);
    }
  }
  console.log(`PASS p592 ${PHASE} · ${checks} checks · ${rows.length} shots → ${OUT}`);
} finally {
  writeFileSync(`${OUT}/result.json`, JSON.stringify({ phase: PHASE, checks, rows }, null, 2) + '\n');
  await B.close(); await fake.stop(); await web.stop();
}
