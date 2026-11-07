// P547 (#315 / #317, PASSKEY-SEAT-MODEL §2): this remote's own Face ID / fingerprint key, approved by ANOTHER remote of the
// owner. The host decides (never this page): the owner's first device gets the old direct offer (pk_peer_res mode
// "direct" → pk_offer); any other device asks the remotes that already hold a key. Each remote has its own key; the old key
// of this record (lost, or borrowed from another device) is never verified again, so the browser's cross-device QR sheet
// never opens here.
//
// Requester (this device):  pk_peer_req → pk_peer_res {mode, id, code, exp, online} → waiting sheet (对照码, 「取消」 =
//   pk_peer_cancel) → pk_peer_done {ok, by | why} + pk_offer {n, peer:id} → 「已批准 · 点「保存」」 → register(…, peer:true)
//   straight from the tap (navigator.credentials.create needs the gesture; the host record is read BEFORE the sheet shows).
// Approver (a remote with a key):  pk_peer_ask {id, device, name, at, exp, code, fa, fa_ch} → card 「「name」请求在本机设置
//   Face ID」 + time + 对照码 + 「批准（Face ID）」「拒绝」 → approve(fa_ch, fa) synchronously in the tap → pk_peer_answer
//   {id, ok:true, fa} · {id, ok:false}; pk_peer_close {id} puts the card away.
// Logic and view are apart: configure({view}) lets node tests drive the state machine without a DOM (p547_pkpeer.test.mjs).
import { t, locale, onLang, fillText } from './t.js';
import { mk, toast, notify } from './ui.js';
import { registerLayer } from './layers.js';
import { unb64u, b64u, sealValue } from '../proto/wire.js';
import { keyPlatform } from './phone-env.js';

export const ID = /^[A-Za-z0-9_-]{16,43}$/;          // the host's request id (b64u of 16 bytes = 22 chars)
export const CODE = /^\d{6}$/;
const WHY = ['busy', 'rate', 'no_approver'];
const DONE_WHY = ['denied', 'expired', 'cancelled'];
const ASK_MS = 10000;                                // pk_peer_res: an older host answers nothing → the old pk_offer_req

let H = {
  ask: async () => ({ t: 'offline' }), send: async () => {}, register: null, approve: null, regOpts: async () => null,
  supported: async () => false, canAssert: () => false, legacy: () => {}, savedHint: () => {}, now: () => Date.now(), notSaved: () => 'pk.notSaved',
  ua: () => (globalThis.navigator ? navigator.userAgent : ''), touch: () => (globalThis.navigator ? navigator.maxTouchPoints || 0 : 0),
};
let view = null;                                      // {show(spec), hide()} — the DOM view below unless a test gives one
export function configure(h) { H = { ...H, ...h }; if (h && h.view) view = h.view; }

// ---------------------------------------------------------------- state
const S = { req: null, offer: null, by: '', direct: false, busy: false, asks: new Map(), shown: null, timer: 0, dev: '' };
export const _state = () => ({ req: S.req && { ...S.req }, offer: S.offer && { ...S.offer }, direct: S.direct, asks: [...S.asks.keys()], shown: S.shown });
export function _reset() { clearTimeout(S.timer); Object.assign(S, { req: null, offer: null, by: '', direct: false, busy: false, shown: null, timer: 0, dev: '' }); S.asks.clear(); }

const cleanName = (v) => (typeof v === 'string' ? v.replace(/[\p{Cc}\p{Cf}]/gu, '').trim().slice(0, 64) : '') || t('pkp.unnamed');

function show(spec) { S.shown = spec.mode; (view || domView()).show(spec); }
function hide() {
  S.shown = null;
  root?.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
  (view || domView()).hide();
  if ([...S.asks.values()].some(a => !a.sent && (!a.exp || a.exp * 1000 > H.now())))
    notify({ kind: 'confirm', text: t('pkp.pending'), ms: 0,
      actions: [{ label: t('pkp.reopen'), fn: reopenPending }] });
}

/** 「设置 Face ID」 / a card's 「在本机设置 Face ID」 (app.js setupFaceId): ask the host which way this device goes.
 *  → 'direct' | 'peer' | 'legacy' | 'fail' | 'busy' | 'unsupported'. */
export const startRosterIntro = () => startPeerSetup({ roster: true });
export async function startPeerSetup({ roster = false } = {}) {
  if (S.busy) return 'busy';
  if (!(roster ? H.canAssert() : await H.supported())) { H.unsupported?.(); return 'unsupported'; }
  S.busy = true;
  let m;
  try { m = await H.ask({ t: 'pk_peer_req', ...(roster ? { roster: true } : {}) }, 'pk_peer_res', ASK_MS); } finally { S.busy = false; }
  if ((!m || m.t === 'timeout') && roster) { result('fail'); return 'fail'; }
  if (!m || m.t === 'timeout') { S.direct = false; H.legacy(); return 'legacy'; }   // a host from before P547: the direct offer as before
  if (m.t === 'offline') { toast(t('elev.fail'), 3000); return 'fail'; }
  if (m.ok !== true) { result(WHY.includes(m.why) ? m.why : 'fail'); return 'fail'; }
  if (m.mode === 'direct' && roster) { result('fail'); return 'fail'; }
  if (m.mode === 'direct') { S.direct = true; return 'direct'; }   // the host's pk_offer follows (app.js offerPasskey, pkForce)
  if (m.mode !== 'peer' || !ID.test(m.id || '') || !CODE.test(String(m.code ?? ''))) { result('fail'); return 'fail'; }
  S.direct = false;
  S.req = { id: m.id, code: String(m.code), exp: Number(m.exp) || 0, online: Number.isInteger(m.online) ? m.online : 0, host: !roster && m.host === 1, roster, done: false };
  S.offer = null; S.by = '';
  arm();
  show({ mode: 'wait', ...(S.req.roster ? { roster: true } : {}), code: S.req.code, online: S.req.online, host: S.req.host, where: keyPlatform(H.ua(), H.touch()) });
  return 'peer';
}
function arm() {
  clearTimeout(S.timer);
  const left = S.req && S.req.exp ? S.req.exp * 1000 - H.now() : 0;
  if (left > 0) S.timer = setTimeout(() => { if (S.req && !S.req.done) { S.req = null; result('expired'); } }, left + 1500);
}
/** 「取消」 on the waiting sheet (or Esc): the host drops the request; the approvers' cards go away. */
export function cancelWait() {
  const r = S.req;
  clearTimeout(S.timer);
  S.req = null; S.offer = null;
  hide();
  if (r && !r.done) H.send({ t: 'pk_peer_cancel', id: r.id }).catch(() => {});
}
function result(why) {
  clearTimeout(S.timer);
  show({ mode: 'result', why: ['denied', 'expired', 'cancelled', 'busy', 'rate', 'no_approver'].includes(why) ? why : 'fail' });
}
/** host → this device: the approval ended. ok → wait for the one-time offer that follows (onOffer). */
export function onDone(m) {
  if (!m || (S.req && m.id !== S.req.id)) return;
  if (m.ok === true && S.req?.roster) {
    clearTimeout(S.timer);
    if (m.roster !== true) { S.req = null; result('fail'); return; }
    S.req = null; S.offer = null; hide();
    notify({ kind: 'success', text: t('p574.introduced'), ms: 6000 });
    return;
  }
  if (m.ok === true) {
    if (S.req) S.req.done = true;
    clearTimeout(S.timer);
    S.by = m.by === 'host' ? t('pkp.byHost') : cleanName(m.by);   // P555: approved at the Agent J host's terminal
    return maybeApproved();
  }
  if (!S.req) return;
  S.req = null;
  result(DONE_WHY.includes(m.why) ? m.why : 'fail');
}
/** app.js: a pk_offer carrying `peer` (never asked again, never replaces with the old key). */
export async function onOffer(m) {
  if (S.req?.roster) return false;
  if (!m || typeof m.n !== 'string' || !ID.test(String(m.peer || ''))) return false;
  if (S.req && m.peer !== S.req.id) return false;
  let n; try { n = unb64u(m.n); } catch { return false; }
  if (n.length !== 32) return false;
  const opts = await H.regOpts();                     // read the host record now: nothing slow may run after the tap
  if (!opts) return false;
  S.offer = { n: m.n, peer: m.peer, nonce: n, opts };
  if (!S.req) { S.req = { id: m.peer, code: '', exp: 0, online: 0, done: true }; S.by = S.by || ''; }   // reconnected within the TTL
  else S.req.done = true;
  maybeApproved();
  return true;
}
function maybeApproved() {
  if (!S.req || !S.req.done || !S.offer) return;
  show({ mode: 'approved', by: S.by });
}
/** 「保存」 on the approved sheet. NOT async before create(): register() reaches navigator.credentials.create in this tick. */
export function saveTap() {
  const o = S.offer;
  if (!o) return null;
  const req = S.req, by = S.by;
  S.offer = null; S.req = null;
  const p = H.register(o.nonce, { ...o.opts, replace: null, peer: true });
  hide();
  return Promise.resolve(p).then((reg) => H.send(reg).catch(() => toast(t('pk.saveFail'))), (e) => {
    // P561 追加 11/12 (Mac Safari 10-28 00:57): the system made no key, nothing went to the host — the other remote's approval
    // still stands until it expires (the host consumes the offer only on a key it takes), so the same sheet comes back with
    // what happened and 「再试一次」 runs create() again from that tap. No second approval, no toast that hides behind it.
    if (S.offer || S.req || !req) { toast(t(H.notSaved()), 8000); return; }   // another flow started meanwhile
    S.offer = o; S.req = req; S.by = by;
    show({ mode: 'approved', by, err: errName(e), notSaved: H.notSaved() });
  });
}
const errName = (e) => (e && typeof e.name === 'string' && /^[A-Za-z]{1,40}$/.test(e.name) ? e.name : 'Error');
/** P561 追加 11: app.js on every ready — this page's remote id. A page whose remote was replaced (a ticket restore under a new
 *  id) drops the old id's request: its approval and offer belong to a record the host has already removed. */
export function onReady(device) {
  if (typeof device !== 'string' || !device) return;
  if (S.dev && S.dev !== device && (S.req || S.offer)) { clearTimeout(S.timer); S.req = null; S.offer = null; S.by = ''; if (S.shown && S.shown !== 'ask') hide(); }
  S.dev = device;
}
export function closeResult() { hide(); }
/** host → this device: pk_reg_res. `{ok:false, why:"peer_required"}` = the host refused a direct offer because another
 *  device of the owner already holds a key → the peer flow starts by itself (H.restart, else startPeerSetup), no failure
 *  toast. → true when handled here (app.js pkSaved says nothing else). */
export function onRegRes(m) {
  if (!m || m.ok !== false || m.why !== 'peer_required') return false;
  S.direct = false; S.offer = null;
  Promise.resolve().then(() => (H.restart || startPeerSetup)()).catch(() => {});
  return true;
}
/** app.js pk_reg_res ok: the account's first key (direct mode) → 「再在另一台设备加一把」 (PASSKEY-SEAT-MODEL §5). */
export function saved(ok) {
  const first = S.direct; S.direct = false;
  if (ok && first) H.savedHint(t('pkp.addAnother'));
  return ok && first;
}

// ---------------------------------------------------------------- approver
/** host → a remote that holds a key: another device of the owner asks. */
export function onAsk(m) {
  if (!m || !ID.test(String(m.id || '')) || !CODE.test(String(m.code ?? '')) || (m.needs_setup !== true && typeof m.fa !== 'string') || typeof m.fa_ch !== 'string') return false;
  let ch; try { ch = unb64u(m.fa_ch); } catch { return false; }
  if (!ch.length) return false;
  const a = { id: m.id, name: cleanName(m.name), at: Number(m.at) || 0, exp: Number(m.exp) || 0, code: String(m.code), roster: m.roster === true, fa: m.fa, ch, needsSetup: m.needs_setup === true, bootstrap: m.bootstrap === true && m.needs_setup === true && m.roster !== true, bootstrapUnavailable: m.bootstrap_unavailable === true, epk: m.epk, n: m.n, digest: m.digest };
  S.asks.set(m.id, a);
  if (m.sign_pub && !a.needsSetup) { // P574: bind the introduced Ed25519 key, before showing an approvable card (no await in the tap).
    a.ch = null;
    Promise.resolve().then(() => H.prepareAsk(m)).then((prepared) => {
      if (S.asks.get(m.id) === a && b64u(prepared) === m.fa_ch) { a.ch = prepared; paintAsk(); }
    }).catch(() => {});
  }
  paintAsk();
  return true;
}
function paintAsk() {
  if (S.shown && S.shown !== 'ask') return;           // the requester's own sheet first (the two never meet on one device)
  const a = [...S.asks.values()].find((x) => !x.sent && x.ch && (!x.exp || x.exp * 1000 > H.now()));
  if (!a) { if (S.shown === 'ask') hide(); return; }
  show({ mode: 'ask', id: a.id, name: a.name, ...(a.roster ? { roster: true } : {}), at: a.at, code: a.code, can: !a.needsSetup && H.canAssert(), ...(a.needsSetup ? { needsSetup: true, bootstrap: a.bootstrap, ...(a.bootstrapUnavailable ? {bootstrapUnavailable: true} : {}) } : {}) });
}
export const currentAsk = () => (S.shown === 'ask' ? [...S.asks.values()].find((x) => !x.sent && x.ch && (!x.exp || x.exp * 1000 > H.now())) || null : null);
/** 「批准（Face ID）」: the assertion starts synchronously inside the tap (iOS gesture rule). */
export function approveTap() {
  const a = currentAsk();
  if (!a || a.needsSetup || (a.exp && a.exp * 1000 <= H.now())) return null;
  let p;
  try { p = H.approve(a.ch, a.fa); } catch (e) { p = Promise.reject(e); }
  return Promise.resolve(p).then(async (fa) => {
    a.sent = true;                                    // the card steps aside; pk_peer_answer_res says how it went
    hide();
    await H.send({ t: 'pk_peer_answer', id: a.id, ok: true, fa });
  }, () => { toast(t('pkp.approveFail'), 5000); });
}
/** host → the approver: its answer was taken (ok) or not (passkey_failed: the card comes back · gone / expired: it ended). */
export function onAnswerRes(m) {
  const a = m && S.asks.get(m.id);
  if (m && m.ok === true) {
    S.asks.delete(m.id); if (S.shown === 'ask') hide(); paintAsk();
    if (a && a.sent) notify({ kind: 'success', text: t('pkp.approveSent'), ms: 3000 });
    return;
  }
  if (a && ['wrong', 'locked', 'bad_seal', 'not_set'].includes(m.why)) { a.sent = false; toast(t('pkp.bootstrapFail'), 5000); paintAsk(); return; }
  if (a && m.why === 'passkey_failed') { a.sent = false; toast(t('pkp.approveFail'), 5000); paintAsk(); return; }
  if (a) { S.asks.delete(m.id); if (S.shown === 'ask') hide(); paintAsk(); }
  toast(t('pkp.answerGone'), 4000);
}
/** Controlled input only: the value never enters chat, tool input, or a retry queue. */
export async function bootstrapTap(input) {
  const a = currentAsk();
  if (!a || !a.bootstrap || (a.exp && a.exp * 1000 <= H.now())) { input.value = ''; return; }
  const value = new TextEncoder().encode(input.value);
  input.value = '';
  a.sent = true;
  try {
    const channel = H.channel(), device = await H.device();
    const sealed = await sealValue(unb64u(a.epk), value, channel, device, a.id, 'pk_bootstrap', a.n, a.digest);
    if (S.asks.get(a.id) !== a || (a.exp && a.exp * 1000 <= H.now()) || H.channel() !== channel) return;
    // Direct send: sealed bootstrap proofs must never be handed in across a new session.
    await H.sendBootstrap({ t: 'pk_peer_answer', id: a.id, ok: true, bootstrap: true, epk: b64u(sealed.epk), ct: b64u(sealed.ct) });
    hide();
  } catch { a.sent = false; toast(t('pkp.bootstrapFail'), 5000); paintAsk(); }
  finally { value.fill(0); }
}
export function reopenPending() { if (S.shown !== 'wait') { hide(); paintAsk(); } }

export function denyTap() {
  const a = currentAsk();
  if (!a) return null;
  S.asks.delete(a.id); hide(); paintAsk();
  toast(t('pkp.denySent'), 2400);
  return H.send({ t: 'pk_peer_answer', id: a.id, ok: false }).catch(() => {});
}
/** host: the request was answered elsewhere / cancelled / expired → the card goes. */
export function onClose(m) {
  if (!m || !S.asks.delete(m.id)) return;
  if (S.shown === 'ask') { hide(); paintAsk(); }
}
/** app.js: the session dropped — the host re-sends open asks after ready; the requester keeps its request (TTL). */
export function gone() { S.asks.clear(); if (S.shown === 'ask') hide(); }

/** One app message → handled? (app.js onApp) */
export function handle(m) {
  switch (m && m.t) {
    case 'pk_peer_done': onDone(m); return true;
    case 'pk_peer_ask': onAsk(m); return true;
    case 'pk_peer_close': onClose(m); return true;
    case 'pk_peer_answer_res': onAnswerRes(m); return true;
    default: return false;
  }
}

// ---------------------------------------------------------------- the DOM view (one centred card, Noir)
let root = null, spec = null;
function when(sec) {
  if (!sec) return '';
  try { return new Date(sec * 1000).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }); } catch { return ''; }
}
function btn(id, cls, key, fn) {
  const b = mk('button', 'aj-action ' + cls, undefined, 'aj-action', 'dialog'); b.type = 'button'; b.id = id; b.textContent = t(key); b.dataset.k = key;
  b.addEventListener('click', fn);
  return b;
}
function domView() {
  return {
    show(s) { spec = s; paint(); },
    hide() { spec = null; if (root) root.hidden = true; },
  };
}
function build() {
  if (root) return root;
  root = mk('div', 'ajmodal dlgcard pkp');
  root.id = 'pkp'; root.hidden = true;
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'pkp-title');
  document.body.appendChild(root);
  registerLayer({ element: () => root, open: () => !!root && !root.hidden, rank: 50,
    fallback: () => document.getElementById('input') || document.getElementById('setBtn'),
    // Esc: the waiting sheet = 「取消」; an approval card only steps aside (the request stays open on the host until it ends)
    close: () => { if (spec && spec.mode === 'wait') cancelWait(); else hide(); } });
  return root;
}
function paint() {
  if (!spec) return;
  const r = build(), s = spec;
  const box = mk('div', 'box');
  const kick = mk('p', 'elev-kicker', t(s.mode === 'ask' ? 'pkp.askKicker' : 'pkp.kicker'));
  const title = mk('h2', '', '', 'aj-passkey'); title.id = 'pkp-title';
  const kids = [kick, title];
  const codeRow = (code) => { const d = mk('div', 'pkp-code'); d.id = 'pkp-code'; d.append(mk('span', 'pkp-code-l', t('pkp.codeLabel')), mk('b', 'pkp-code-v', code)); return d; };
  const row = mk('div', 'ajrow pkp-acts');
  if (s.mode === 'wait') {
    const hostOnly = s.host && !(s.online > 0);       // P555: nobody online to approve → the Agent J host's terminal first
    title.textContent = t(s.roster ? 'p574.introWaitTitle' : hostOnly ? 'pkp.waitHostTitle' : 'pkp.waitTitle');
    kids.push(mk('p', 'pkp-text', t(s.roster ? 'p574.introText' : hostOnly ? 'pkp.waitHostText' : 'pkp.waitText')), codeRow(s.code));
    if (!hostOnly) kids.push(mk('p', 'elev-note', s.online > 0 ? t('pkp.online', { n: s.online }) : t('pkp.noneOnline')));
    if (s.host) {
      const cmd = mk('p', 'elev-note pkp-host'); cmd.id = 'pkp-host';
      fillText(cmd, t('pkp.hostWay', { code: s.code }));
      kids.push(cmd);
    }
    kids.push(mk('p', 'elev-note', t('pkp.eachOwn') + ' ' + t('pkp.where.' + s.where)));
    row.append(btn('pkp-cancel', 'aj-action--quiet', 'sheet.cancel', () => cancelWait()));
  } else if (s.mode === 'approved') {
    title.textContent = t(s.err ? 'p561.save.failTitle' : 'pkp.approvedTitle');
    if (s.err) {                                       // P561 追加 11: the system made no key — say so plainly, the approval stands
      const f = mk('p', 'pkp-text pkp-fail', t('p561.save.fail')); f.id = 'pkp-fail'; f.dataset.err = s.err; f.setAttribute('role', 'status');
      kids.push(f, mk('p', 'elev-note', t(s.notSaved || 'pk.notSaved')));
    } else {
      kids.push(mk('p', 'pkp-text', s.by ? t('pkp.approvedBy', { by: s.by }) : t('pkp.approvedText')));
      kids.push(mk('p', 'elev-note', t('pkp.eachOwn') + ' ' + t('pkp.where.' + keyPlatform(H.ua(), H.touch()))));
    }
    row.append(btn('pkp-save', 'aj-action--primary', s.err ? 'p539.fail.retry' : 'pk.save', () => saveTap()), btn('pkp-later', 'aj-action--quiet', 'pk.later', () => { S.offer = null; S.req = null; hide(); }));
  } else if (s.mode === 'result') {
    title.textContent = t('pkp.r.' + s.why + '.title');
    kids.push(mk('p', 'pkp-text', t('pkp.r.' + s.why)));
    row.append(btn('pkp-ok', 'aj-action--primary', 'pkp.ok', () => closeResult()));
  } else if (s.mode === 'ask') {
    title.textContent = t(s.roster ? 'p574.introTitle' : 'pkp.askTitle', { name: s.name });
    const at = when(s.at);
    if (at) kids.push(mk('p', 'elev-note pkp-at', t('pkp.askAt', { time: at })));
    kids.push(codeRow(s.code), mk('p', 'pkp-text', t(s.roster ? 'p574.introText' : 'pkp.askText')));
    if (s.needsSetup) kids.push(mk('p', 'elev-note', t(s.bootstrapUnavailable ? 'pkp.passwordNotSet' : s.bootstrap ? 'pkp.bootstrapText' : 'pkp.setupFirst')));
    else if (!s.can) kids.push(mk('p', 'elev-note', t('pkp.noAssert')));
    if (s.bootstrap) {
      const input = mk('input', 'aj-input', undefined, 'aj-passkey'); input.type = 'password'; input.autocomplete = 'off';
      input.maxLength = 256; input.setAttribute('aria-label', t('pkp.pairPassword'));
      kids.push(input);
      row.append(btn('pkp-bootstrap', 'aj-action--primary', 'pkp.bootstrapApprove', () => bootstrapTap(input)));
    }
    if (s.needsSetup && !s.bootstrap && !s.bootstrapUnavailable) row.append(btn('pkp-setup-first', 'aj-action--quiet', 'pkp.setupFirstAction', () => { hide(); (H.restart || startPeerSetup)(); }));
    const ok = btn('pkp-approve', 'aj-action--primary', 'pkp.approve', () => approveTap());
    ok.hidden = !s.can;
    row.append(ok, btn('pkp-deny', 'aj-action--quiet', 'pkp.deny', () => denyTap()));
  }
  kids.push(row);
  box.append(...kids);
  r.replaceChildren(box);
  r.dataset.mode = s.mode;
  r.hidden = false;
  (r.querySelector('.aj-action--primary:not([hidden])') || r.querySelector('.aj-action'))?.focus({ preventScroll: true });
}
onLang(() => { if (root && !root.hidden) paint(); });
