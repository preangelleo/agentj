// 0.26.9 #468 (CONTRACT §B3, ADR-0269-w2-web; the terminal design §B2 is void): official remote assist — the phone side.
// Settings → 紧急修复 ② 「请 Agent J 远程帮忙」 gains the step 「请 Agent J 官方远程协助」 (no new top-level entry): a plain
// explanation (the helper can only send tasks to YOUR Agent · your Agent runs them under its normal rules · dangerous steps
// still ask you one by one · you see every task live · take it back any time · it ends by itself), 15 / 30 / 60 minute chips
// (30 by default) and a Face ID button. The grant is two steps (wire unchanged from wave 1):
//   1. unsigned `assist_grant {minutes}` → the host's `ctl_res {ok:false, why:"passkey_required", n, fa, fa_ch, key_id, seat}`
//   2. the owner's tap starts WebAuthn synchronously (iOS gesture rule) over fa_ch; the host's nonce n is then signed (§8
//      control-v1, action `assist_grant`, object {minutes, key_id, seat} — wire.js controlObject) and sent with the assertion.
// While a grant is active (`assist_state {active:true, until, id, key_id, tasks:[{id, state}]}`) every page shows one banner
// 「官方远程协助中 · 剩 N 分钟」 with 「收回」 (signed `assist_revoke`, object {} — narrowing never needs Face ID); its text opens
// 「看实时画面」: a READ-ONLY task feed — every `assist_task {id, ts, text, state: queued|running|done|refused|stopped, why?}` as
// an entry (the task's words, its state, and the reply your Agent gave, from the turn the task started in the normal chat,
// labelled 「官方远程协助」 by relay.js). Nothing terminal-shaped: no term_* frame is read or sent here (app.js drops
// `assist:true` terminal frames). The feed lives in memory only (≤ TASK_MAX entries) and is wiped when the grant ends.
// `assist_notice {what}` (grant / open / close / expired / revoked / refused) → one Noir notification each.
import { request, done, channel, isReady, peer } from './session.js';
import { signedWrite } from './controls.js';
import { controlMessage, b64u, unb64u } from '../proto/wire.js';
import { signKey } from './store.js';
import { myDeviceId } from './api.js';
import { approve, canAssert } from './faceid.js';
import { registerLayer } from './layers.js';
import { isAssistSrc } from './assistsrc.js';
import { t, onLang } from './t.js';
import { el, mk, toast } from './ui.js';
import { SVG } from './icons.js';

// ---------------------------------------------------------------- wire names (one block: the CEO aligns them with the host lane)
export const WIRE = {
  cap: 'assist1',                 // host_info / ready caps: the host can grant official remote assist
  grant: 'assist_grant',          // phone → host: step 1 {minutes} · step 2 {minutes, n, ts, sig, fa}
  revoke: 'assist_revoke',        // phone → host: {n, ts, sig} (object {})
  state: 'assist_state',          // host → phones: {active, until, id, key_id, tasks:[{id, state}]}
  task: 'assist_task',            // host → phones: {id, ts, text, state, why?} — one task the helper sent, as it moves
  notice: 'assist_notice',        // host → phones: {what, minutes?}
};
export const MINUTES = [15, 30, 60];
export const DEFAULT_MINUTES = 30;
export const NOTICES = ['grant', 'open', 'close', 'expired', 'revoked', 'refused'];
export const TASK_MAX = 50;             // the feed keeps the newest 50 tasks (memory only)
export const TEXT_MAX = 4000;           // a task's words / a reply shown in the feed (the chat has the whole reply)
export const STATES = ['queued', 'running', 'done', 'refused', 'stopped'];
export const NONCE_MS = 100000;         // a fetched challenge older than this is fetched again before Face ID
const HEX32 = /^[0-9a-f]{32}$/;
const ID = /^[A-Za-z0-9._:-]{1,64}$/;

const fresh = () => ({ active: false, until: 0, key_id: '', id: null, tasks: [] });
let S = fresh();
let G = { open: false, minutes: DEFAULT_MINUTES, phase: 'idle', ch: null, r1: null, r2: null, why: '' };
let feed = new Map(), viewOpen = false, revoking = false, tick = 0, lastNote = { what: '', at: 0 }, pending = null;
let H = { openRescue() {}, turns: () => [], toTurn() {} };

// ---------------------------------------------------------------- pure helpers (web-unit)
/** `until` from the host: seconds or milliseconds since the epoch → ms (0 = not a time). */
export function untilMs(v) { return Number.isFinite(v) && v > 0 ? (v < 1e12 ? v * 1000 : v) : 0; }
/** Minutes left, rounded up (a grant with 29 min 10 s left says 30); 0 = over. */
export function minutesLeft(until, now = Date.now()) { const ms = until - now; return ms > 0 ? Math.ceil(ms / 60000) : 0; }
/** The host's `assist_state` → our state, or null when malformed. Inactive / past = inactive. */
export function parseState(m, now = Date.now()) {
  if (!m || typeof m !== 'object') return null;
  const until = untilMs(m.until);
  const active = m.active === true && until > now;
  const tasks = active && Array.isArray(m.tasks) ? m.tasks.filter((x) => x && typeof x.id === 'string' && ID.test(x.id) && STATES.includes(x.state))
    .slice(-TASK_MAX).map((x) => ({ id: x.id, state: x.state })) : [];
  return { active, until: active ? until : 0, key_id: typeof m.key_id === 'string' && ID.test(m.key_id) ? m.key_id : '',
    id: active && typeof m.id === 'string' && ID.test(m.id) ? m.id : null, tasks };
}
/** One `assist_task` → {id, ts (ms), text, state, why} or null. The words are display text (control characters out, capped). */
export function parseTask(m) {
  if (!m || typeof m !== 'object' || typeof m.id !== 'string' || !ID.test(m.id) || !STATES.includes(m.state)) return null;
  const text = typeof m.text === 'string' ? m.text.replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u2028\u2029]/g, ' ').trim() : '';
  const cut = [...text].length > TEXT_MAX ? [...text].slice(0, TEXT_MAX - 1).join('') + '…' : text;
  const why = typeof m.why === 'string' && /^[a-z_]{1,32}$/.test(m.why) ? m.why : '';
  return { id: m.id, ts: untilMs(m.ts), text: cut, state: m.state, why };
}
/** The feed after one task message: a known id keeps its words (a later frame may omit them) and takes the newer state. */
export function upsertTask(map, t, max = TASK_MAX) {
  const old = map.get(t.id);
  const next = { ...t, text: t.text || old?.text || '', ts: t.ts || old?.ts || Date.now() };
  map.delete(t.id); map.set(t.id, next);
  while (map.size > max) map.delete(map.keys().next().value);
  return next;
}
/** The chat turn a task started: the host's task id on the source, else the newest assist turn whose words are the task's. */
export function turnOf(task, turns) {
  const mine = (turns || []).filter((x) => x && isAssistSrc(x.source));
  const byId = mine.findLast((x) => x.source.assist_id === task.id);
  if (byId) return byId;
  const w = (task.text || '').replace(/…$/, '').trim();
  if (!w) return null;
  return mine.findLast((x) => typeof x.source.text === 'string' && x.source.text.includes(w) && (!task.ts || !x.ts || x.ts * 1000 >= task.ts - 60000)) || null;
}
/** A reply for the feed: plain text, capped. */
export function replyText(turn) {
  const r = turn && typeof turn.reply === 'string' ? turn.reply.trim() : '';
  return [...r].length > TEXT_MAX ? [...r].slice(0, TEXT_MAX - 1).join('') + '…' : r;
}
/** The host's passkey_required step → the challenge we sign over, or null. */
export function parseChallenge(m) {
  if (!m || m.ok !== false || m.why !== 'passkey_required' || !HEX32.test(m.n || '')) return null;
  if (typeof m.fa !== 'string' || !m.fa || typeof m.fa_ch !== 'string' || !m.fa_ch) return null;
  const seat = typeof m.seat === 'number' && Number.isInteger(m.seat) ? String(m.seat) : m.seat;
  if (typeof m.key_id !== 'string' || !ID.test(m.key_id) || typeof seat !== 'string' || !ID.test(seat)) return null;
  return { n: m.n, fa: m.fa, fa_ch: m.fa_ch, key_id: m.key_id, seat, at: Date.now() };
}
/** The object the grant signature covers (wire.js controlObject('assist_grant', …) = minutes \n key_id \n seat). */
export const grantObject = (minutes, ch) => ({ minutes, key_id: ch.key_id, seat: ch.seat });
// ---------------------------------------------------------------- host messages (app.js)
export function onState(m) {
  const n = parseState(m);
  if (!n) return;
  const was = S;
  if (!n.active) { S = fresh(); if (was.active) ended(); paint(); return; }
  if (was.id && n.id && was.id !== n.id) wipe();   // another grant: the old feed goes
  S = n;
  for (const x of n.tasks) { const t0 = feed.get(x.id); if (t0 && t0.state !== x.state) feed.set(x.id, { ...t0, state: x.state }); }
  if (pending && S.active) { G = { ...G, open: false, phase: 'idle', ch: null, why: '' }; pending = null; }
  paint();
}
export function onNotice(m) {
  if (!m || !NOTICES.includes(m.what)) return;
  // host assist.py: notice "refused" + why = a grant this host turned down (estop / unavailable / no_passkey / busy …)
  if (m.what === 'refused' && typeof m.why === 'string' && /^[a-z_]{1,32}$/.test(m.why)) { toast(whyText(m.why), 6000, 'warn'); return; }
  note(m.what, Number.isInteger(m.minutes) && m.minutes > 0 && m.minutes <= 60 ? m.minutes : null);
}
/** `assist_task`: a task the helper sent, as it moves (queued → running → done / refused / stopped). Only during a grant. */
export function onTask(m) {
  const x = parseTask(m);
  if (!x || !S.active) return;
  const isNew = !feed.has(x.id);
  upsertTask(feed, x);
  if (isNew) note('task');
  paint();
}
/** relay.js: a turn changed — the feed shows the reply of a task's turn as it grows. */
export function onTurn(p) { if (viewOpen && p && isAssistSrc(p.source)) paintFeed(); }
export const active = () => S.active;

function ended() {
  closeView();
  wipe();
}
function wipe() { feed = new Map(); }
const NOTE_KIND = { grant: 'success', open: 'info', close: 'info', expired: 'info', revoked: 'info', refused: 'warn', task: 'info' };
function note(what, minutes) {
  const now = Date.now();
  if (lastNote.what === what && now - lastNote.at < 10000) return;   // our own result and the host's notice say it once
  lastNote = { what, at: now };
  toast(t('p0269.assist.note.' + what, { n: minutes || G.minutes }), 6000, NOTE_KIND[what]);
}

// ---------------------------------------------------------------- the grant (step 1: the host's nonce · step 2: Face ID + signature)
function fetchChallenge() {
  if (G.r1) done(G.r1);
  G.ch = null; G.phase = 'fetch'; G.why = '';
  let timer = 0;
  const r = request({ t: WIRE.grant, minutes: G.minutes }, (m) => {
    if (m.t !== 'ctl_res' && m.t !== 'offline') return;
    clearTimeout(timer); done(r); if (G.r1 === r) G.r1 = null;
    if (!G.open || G.phase !== 'fetch') return;
    if (m.t === 'offline') return fail('offline');
    const c = parseChallenge(m);
    if (c) { G.ch = c; G.phase = 'ready'; paint(); return; }
    fail(m.ok === false ? String(m.why || 'shape') : 'shape');
  });
  G.r1 = r;
  if (!r) { fail('offline'); return; }
  timer = setTimeout(() => { done(r); if (G.r1 === r) G.r1 = null; if (G.phase === 'fetch') fail('timeout'); }, 20000);
  paint();
}
function fail(why) { G.phase = 'fail'; G.why = why; pending = null; paint(); }
async function signGrant(c, minutes) {
  const sk = await signKey();
  if (!sk) throw new Error('no_key');
  const ts = Date.now();
  const msg = await controlMessage(channel(), await myDeviceId(), WIRE.grant, c.n, ts, grantObject(minutes, c));
  return { n: c.n, ts, sig: b64u(new Uint8Array(await crypto.subtle.sign({ name: 'Ed25519' }, sk.priv, msg))) };
}
/** 「用 Face ID 授权」: WebAuthn starts here, inside the tap. */
function grantYes() {
  const c = G.ch;
  if (!c || G.phase !== 'ready') return;
  if (Date.now() - c.at > NONCE_MS) { fetchChallenge(); return; }
  const minutes = G.minutes;
  let p;
  try { p = approve(unb64u(c.fa_ch), c.fa); } catch { p = Promise.reject(new Error('webauthn')); }
  G.phase = 'signing'; paint();
  p.then(async (fa) => {
    if (G.phase !== 'signing') return;
    let signed;
    try { signed = await signGrant(c, minutes); } catch (e) { return fail(e && e.message === 'no_key' ? 'no_key' : 'shape'); }
    G.ch = null; G.phase = 'sending'; paint();
    send({ t: WIRE.grant, minutes, ...signed, fa }, minutes);
  }, () => {
    if (G.phase !== 'signing') return;
    fetchChallenge();             // nothing was sent; a fresh nonce costs nothing
    G.why = 'face'; paint();
  });
}
function send(obj, minutes) {
  let timer = 0;
  const arm = (ms) => { clearTimeout(timer); timer = setTimeout(() => { done(r); if (G.r2 === r) G.r2 = null; if (G.phase === 'sending') fail('timeout'); }, ms); };
  const r = request(obj, (m) => {
    if (m.t === 'ctl_pending') { arm(Number.isInteger(m.timeout_ms) && m.timeout_ms > 0 && m.timeout_ms <= 120000 ? m.timeout_ms : 60000); return; }
    if (m.t !== 'ctl_res' && m.t !== 'offline') return;
    clearTimeout(timer); done(r); if (G.r2 === r) G.r2 = null;
    if (G.phase !== 'sending') return;
    if (m.t === 'offline') return fail('offline');
    if (m.ok !== true) return fail(String(m.why || 'shape'));
    note('grant', minutes);
    const until = untilMs(m.until);
    if (until > Date.now() && !S.active) S = { ...fresh(), active: true, until };
    if (S.active) { G = { ...G, open: false, phase: 'idle', ch: null, why: '' }; pending = null; }
    else pending = { at: Date.now() };   // the host's assist_state follows
    paint();
  });
  G.r2 = r;
  if (!r) { fail('offline'); return; }
  arm(20000);
}
function openPanel() {
  if (G.open) return;
  if (!canAssert()) { G = { ...G, open: true, phase: 'fail', why: 'no_passkey' }; paint(); return; }
  G = { ...G, open: true, minutes: DEFAULT_MINUTES, phase: 'idle', why: '' };
  if (!isReady()) { fail('offline'); return; }
  fetchChallenge();
  el('rs-assist-yes')?.focus({ preventScroll: true });
}
function closePanel() {
  if (G.r1) done(G.r1);
  if (G.phase === 'sending') return;   // the signed grant is out: its answer decides
  G = { ...G, open: false, phase: 'idle', ch: null, r1: null, why: '' };
  paint();
  el('rs-assist-go')?.focus({ preventScroll: true });
}
function pick(n) {
  if (!MINUTES.includes(n) || G.phase === 'signing' || G.phase === 'sending') return;
  G.minutes = n; paint();
}

// ---------------------------------------------------------------- revoke
export async function revoke() {
  if (revoking || !S.active) return;
  revoking = true; paint();
  const res = await signedWrite(WIRE.revoke, {}, { t: WIRE.revoke });
  revoking = false;
  if (res && res.ok) { S = fresh(); ended(); note('revoked'); }
  else toast(t('p0269.assist.revokeFail'), 6000, 'warn');
  paint();
}

// ---------------------------------------------------------------- 「看实时画面」 (the read-only task feed)
let layer = null;
function viewRoot() {
  let v = el('assist-view');
  if (v) return v;
  v = mk('section', 'asview'); v.id = 'assist-view'; v.hidden = true;
  v.setAttribute('role', 'dialog'); v.setAttribute('aria-modal', 'true'); v.setAttribute('aria-labelledby', 'assist-view-h');
  const head = mk('header', 'asview__head');
  const h = mk('h2', 'asview__title'); h.id = 'assist-view-h';
  const left = mk('p', 'asview__left'); left.id = 'assist-view-left'; left.setAttribute('role', 'status');
  const titles = mk('div', 'asview__titles'); titles.append(h, left);
  const rv = btn('assist-view-revoke', '', 'asbtn asbtn--revoke', revoke);
  const x = btn('assist-view-close', '', 'asview__x', closeView);
  x.append(icon(['M6 6l12 12M18 6L6 18']));
  const acts = mk('div', 'asview__acts'); acts.append(rv, x);
  head.append(titles, acts);
  const noteP = mk('p', 'asview__note'); noteP.id = 'assist-view-note';
  const wait = mk('p', 'asview__wait'); wait.id = 'assist-view-wait'; wait.setAttribute('role', 'status');
  const list = mk('ol', 'asfeed'); list.id = 'assist-feed'; list.setAttribute('aria-labelledby', 'assist-view-h'); list.setAttribute('aria-live', 'polite');
  v.append(head, noteP, wait, list);
  document.body.append(v);
  layer = registerLayer({ element: () => el('assist-view'), open: () => viewOpen, close: closeView, fallback: () => el('assist-banner-view'), rank: 60 });
  return v;
}
/** A line icon (stroke only, currentColor) built as DOM — never markup strings. */
function icon(paths, cls = '') {
  const svg = document.createElementNS(SVG, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.9', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(k, v);
  if (cls) svg.setAttribute('class', cls);
  for (const d of paths) { const p = document.createElementNS(SVG, 'path'); p.setAttribute('d', d); svg.append(p); }
  return svg;
}
function btn(id, label, cls, fn) { const b = mk('button', cls, label); b.type = 'button'; b.id = id; b.addEventListener('click', fn); return b; }
export function openView() {
  if (!S.active) return;
  const v = viewRoot();
  viewOpen = true; v.hidden = false; document.body.toggleAttribute('data-assist-view', true);
  paint();
  el('assist-view-close')?.focus({ preventScroll: true });
}
export function closeView() {
  if (!viewOpen) return;
  viewOpen = false;
  const v = el('assist-view'); if (v) v.hidden = true;
  document.body.removeAttribute('data-assist-view');
  paint();
}
/** One entry per task, oldest first: the state, the time, the task's words, and the reply your Agent gave (plain text). */
// the host's codes (host/agentj/assist.py): refused → ended · length · admission_filter · busy · quota · main_unavailable ·
// submit_failed; stopped → ended · interrupted; done (the reply did not reach the helper) → failed · outbound_gate · not_sent
export const TASK_WHY = ['ended', 'length', 'admission_filter', 'busy', 'quota', 'main_unavailable', 'submit_failed', 'interrupted', 'failed', 'outbound_gate', 'not_sent'];
/** The plain line under a task for the host's code; an unknown code → a generic line per state. */
export function whyKey(x) {
  if (TASK_WHY.includes(x.why) && !(x.why === 'ended' && x.state === 'done')) return 'p0269.assist.twhy.' + (x.why === 'ended' && x.state === 'stopped' ? 'endedStop' : x.why);
  return 'p0269.assist.twhy.' + (x.state === 'done' ? 'otherDone' : 'other');
}
const whyLine = (x) => t(whyKey(x));
function paintFeed() {
  const list = el('assist-feed');
  if (!list) return;
  const turns = H.turns() || [];
  const items = [];
  for (const x of feed.values()) {
    const li = mk('li', 'asfeed__item'); li.dataset.id = x.id; li.dataset.state = x.state;
    const top = mk('p', 'asfeed__top');
    const st = mk('span', 'asfeed__st', t('p0269.assist.st.' + x.state)); st.dataset.state = x.state;
    top.append(st);
    if (x.ts) { const d = new Date(x.ts); const tm = mk('span', 'asfeed__time', String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')); top.append(tm); }
    const words = mk('p', 'asfeed__text', x.text || t('p0269.assist.noText'));
    li.append(top, words);
    if (x.why && x.state !== 'queued' && x.state !== 'running') li.append(mk('p', 'asfeed__why', whyLine(x)));
    const turn = turnOf(x, turns), out = replyText(turn);
    const o = mk('div', 'asfeed__out');
    o.append(mk('p', 'asfeed__lab', t('p0269.assist.reply')));
    const body = mk('p', 'asfeed__reply' + (out ? '' : ' asfeed__reply--none'),
      out || t(x.state === 'running' || x.state === 'queued' ? 'p0269.assist.replyWait' : 'p0269.assist.replyNone'));
    o.append(body);
    if (out || x.state === 'running' || x.state === 'done') li.append(o);
    items.push(li);
  }
  list.replaceChildren(...items);
}

// ---------------------------------------------------------------- painting
/** Two copies of one banner: `assist-banner` in the chat's banner stack (#reply-notices, with the rescue banner — in the flow,
 *  never over a button) and `assist-float` on the body, which CSS shows only while Settings / a menu page / a full-screen view
 *  covers the chat (the stack lives under those pages). Same words, same two buttons. */
function bannerRoot(id = 'assist-banner') {
  let b = el(id);
  if (b) return b;
  b = mk('div', 'asbanner' + (id === 'assist-float' ? ' asbanner--float' : '')); b.id = id; b.hidden = true; b.setAttribute('role', 'status');
  const go = btn(id + '-view', '', 'asbanner__go', openView);
  go.append(icon(['M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z', 'M9 12l2 2 4-4'], 'asbanner__ic'));
  const tx = mk('span', 'asbanner__t'); tx.id = id + '-text'; go.append(tx);
  const rv = btn(id + '-revoke', '', 'asbtn asbtn--revoke', revoke);
  b.append(go, rv);
  const slot = el('rescue-banner');
  if (id === 'assist-banner' && slot && slot.parentElement) slot.before(b); else document.body.append(b);
  if (id === 'assist-float') b.setAttribute('aria-hidden', 'true');   // the chat copy is the one a screen reader announces
  return b;
}
function leftText() {
  const n = minutesLeft(S.until);
  return n <= 1 ? t('p0269.assist.bannerLast') : t('p0269.assist.banner', { n });
}
function paintBanner() {
  if (!S.active && !el('assist-banner')) return;
  document.body.toggleAttribute('data-assist', S.active);
  for (const id of ['assist-banner', 'assist-float']) {
    const b = bannerRoot(id);
    b.hidden = !S.active || viewOpen;
    if (!S.active) continue;
    el(id + '-text').textContent = leftText();
    const go = el(id + '-view');
    go.setAttribute('aria-label', leftText() + ' · ' + t('p0269.assist.view'));
    go.title = t('p0269.assist.view');
    const rv = el(id + '-revoke');
    rv.textContent = revoking ? t('p0269.assist.revoking') : t('p0269.assist.revoke');
    rv.disabled = revoking;
  }
}
function paintView() {
  const v = el('assist-view');
  if (!v) return;
  el('assist-view-h').textContent = t('p0269.assist.viewTitle');
  el('assist-view-left').textContent = S.active ? leftText() : t('p0269.assist.viewEnded');
  el('assist-view-note').textContent = t('p0269.assist.viewNote');
  const w = el('assist-view-wait');
  w.hidden = feed.size > 0; w.textContent = t('p0269.assist.viewWait');
  paintFeed();
  const rv = el('assist-view-revoke');
  rv.textContent = revoking ? t('p0269.assist.revoking') : t('p0269.assist.revoke'); rv.disabled = revoking || !S.active;
  const x = el('assist-view-close'); x.setAttribute('aria-label', t('p0269.assist.close')); x.title = t('p0269.assist.close');
}
const WHY = ['estop', 'no_passkey', 'busy', 'unavailable', 'offline', 'timeout', 'bad_signature', 'stale', 'replay', 'no_key', 'face', 'device_not_allowed', 'shape', 'io', 'passkey_failed', 'passkey_required'];
function whyText(why) { return t('p0269.assist.why.' + (WHY.includes(why) ? why : 'other')); }
/** The step inside ② (js/rescue.js paint() hands us its body; our section moves along, keeping its state). */
export function paintCard(body) {
  if (!body) return;
  let box = el('rs-assist');
  const show = !!peer.assist1 || S.active;
  if (!show) { box?.remove(); return; }
  if (!box) { box = mk('div', 'rsassist'); box.id = 'rs-assist'; box.setAttribute('role', 'group'); box.setAttribute('aria-labelledby', 'rs-assist-h'); }
  if (box.parentElement !== body || body.lastElementChild !== box) body.append(box);
  const parts = [];
  const h = mk('h5', 'rsassist__h', t('p0269.assist.title')); h.id = 'rs-assist-h'; parts.push(h);
  if (S.active) {
    const p = mk('p', 'rsmsg rsassist__on', t('p0269.assist.onLine', { n: Math.max(1, minutesLeft(S.until)) })); p.id = 'rs-assist-on'; p.setAttribute('role', 'status');
    const rv = btn('rs-assist-revoke', revoking ? t('p0269.assist.revoking') : t('p0269.assist.revoke'), 'aj-btn', revoke); rv.disabled = revoking;
    parts.push(p, acts(btn('rs-assist-view', t('p0269.assist.view'), 'aj-btn aj-btn--primary', openView), rv));
  } else if (!G.open) {
    parts.push(mk('p', 'rsnote', t('p0269.assist.lead')), acts(btn('rs-assist-go', t('p0269.assist.go'), 'aj-btn', openPanel)));
  } else {
    const ul = mk('ul', 'rsassist__list'); ul.id = 'rs-assist-list';
    for (const k of ['what', 'rules', 'ask', 'see', 'back', 'long']) { const li = mk('li', '', t('p0269.assist.x.' + k)); li.dataset.k = k; ul.append(li); }
    parts.push(ul);
    const lab = mk('p', 'rsassist__lab', t('p0269.assist.howLong')); lab.id = 'rs-assist-min-l';
    const chips = mk('div', 'rsassist__chips'); chips.id = 'rs-assist-min'; chips.setAttribute('role', 'radiogroup'); chips.setAttribute('aria-labelledby', lab.id);
    const busy = G.phase === 'signing' || G.phase === 'sending';
    for (const n of MINUTES) {
      const c = btn('rs-assist-min-' + n, t('p0269.assist.min', { n }), 'rsassist__chip', () => pick(n));
      c.setAttribute('role', 'radio'); c.setAttribute('aria-checked', String(G.minutes === n)); c.dataset.min = String(n); c.disabled = busy;
      chips.append(c);
    }
    parts.push(lab, chips);
    const yesLabel = G.phase === 'fetch' ? t('p408.preparing') : busy ? t('p0269.assist.granting') : t('p0269.assist.yes', { n: G.minutes });
    const yes = btn('rs-assist-yes', yesLabel, 'aj-btn aj-btn--primary', () => (G.phase === 'fail' ? fetchChallenge() : grantYes()));
    yes.disabled = busy || G.phase === 'fetch' || (G.phase === 'fail' && G.why === 'no_passkey');
    if (G.phase === 'fail') yes.textContent = t('p332.again');
    const no = btn('rs-assist-cancel', t('sheet.cancel'), 'aj-btn', closePanel); no.disabled = G.phase === 'sending';
    parts.push(acts(yes, no));
    if (G.why) { const m = mk('p', 'rsmsg rsfail', whyText(G.why)); m.id = 'rs-assist-msg'; m.setAttribute('role', 'status'); parts.push(m); }
  }
  box.replaceChildren(...parts);
}
function acts(...bs) { const p = mk('div', 'rsacts'); p.append(...bs.filter(Boolean)); return p; }
function paint() {
  paintBanner(); paintView();
  const body = el('rs-help-body');
  if (body) paintCard(body);
  clearInterval(tick); tick = 0;
  if (S.active) tick = setInterval(onTick, 10000);
}
/** every 10 s while a grant runs: the countdown; past `until` the page lets go by itself (the host says why). */
export function onTick() {
  if (!S.active) return;
  if (minutesLeft(S.until) <= 0) { S = fresh(); ended(); }
  paint();
}

export function configure(h = {}) {
  H = { ...H, ...h };
  onLang(paint);
}

// tests
export const _debug = () => ({ S: { ...S, tasks: S.tasks.slice() }, G: { open: G.open, minutes: G.minutes, phase: G.phase, why: G.why, ch: !!G.ch },
  feed: [...feed.values()].map((x) => ({ id: x.id, state: x.state })), view: viewOpen, layer: !!layer });
export function _reset() { closeView(); S = fresh(); G = { open: false, minutes: DEFAULT_MINUTES, phase: 'idle', ch: null, r1: null, r2: null, why: '' }; feed = new Map(); revoking = false; pending = null; lastNote = { what: '', at: 0 }; paint(); }
