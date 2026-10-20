// P374 · Settings → 账号和帮助 → 「Daily AI News」: ONE switch (default off) — the account e-mail gets the daily
// 「Musk + AI 24h」 digest. The phone never talks to the cloud: {t:'news_get'} / {t:'news_set', on} go to the computer
// inside the Noise session, the computer asks the account (POST /v1/host/news) and answers news_res (P374 contract §5):
//   {t:'news_res', ok:true, on, email, sample_url, queued, seats} · {t:'news_res', ok:false, problem: unlinked|not_bound|unreachable|rate_limited|fail}
// queued (P374 追加): all seats taken → on + the place in the waiting list (1 = next); the line under the switch says so.
// A computer without P374 never answers: the switch stays disabled with a hint. The e-mail is shown with textContent only and
// the sample link is accepted only as an https: URL (the host's text is data, never markup or a script URL).
// P393 #128: the news belongs to the person of THIS host's seat. need_email:true = no address yet → the row explains it and asks
// for one: {t:'news_email', email, lang} mails a 6-digit code, {t:'news_verify', email, code} confirms it (answer = a news_get read);
// 「重新发送」 after 60 s. account_email = this host uses the account's own address → the account link. Errors stay in the row.
import { t, lang } from './t.js';
import { el, noticeSheet } from './ui.js';
import { ask1, isReady } from './session.js';

export const WAIT_MS = 10000;     // the host's own cloud budget is 8 s (contract §5) — a little longer, so its answer wins the race
const PROBLEMS = ['unlinked', 'not_bound', 'unreachable', 'rate_limited', 'fail', 'need_email', 'invalid_code', 'bad_email', 'account_email', 'mail_failed'];
const MSG = { unlinked: 'p374.unlinked', not_bound: 'p374.notBound', unreachable: 'p374.unreachable', rate_limited: 'p374.rateLimited', fail: 'p374.fail',
  account_email: 'p393news.accountEmail' };
// P393 #128: the e-mail step's own lines (shown in the row, never a card: the person is typing)
const MAIL_MSG = { bad_email: 'p393news.badEmail', invalid_code: 'p393news.invalidCode', mail_failed: 'p393news.mailFailed',
  rate_limited: 'p393news.rateLimited', unreachable: 'p374.unreachable', fail: 'p374.fail', noanswer: 'p393news.noAnswer',
  offline: 'p374.offline', verified: 'p393news.verified' };
export const RESEND_MS = 60000, CODE_MS = 10 * 60000;   // 「重新发送」 after 60 s; the mailed code lives 10 min (contract)
// state: 'idle' (not asked yet) · 'wait' (news_get out) · 'ok' · 'problem' (host answered ok:false) · 'old' (no news_res) · 'offline'
let S = { state: 'idle', on: false, email: '', sample: '', problem: '', busy: false, want: null, queued: 0, seats: 0 };
// P393 #128 e-mail step: need (no address yet) · step 'email' | 'code' · to (the address the code went to) · sentAt · note (a MAIL_MSG key)
let M = { need: false, step: 'email', to: '', sentAt: 0, note: '', busy: false, verified: false };
let tick = null;

const safeUrl = (u) => {
  if (typeof u !== 'string' || u.length > 300) return '';
  try { const x = new URL(u); return x.protocol === 'https:' ? x.href : ''; } catch { return ''; }
};
const cleanEmail = (e) => (typeof e === 'string' && e.length <= 254 && /^[^\s@]+@[^\s@]+$/.test(e) ? e : '');
const posInt = (v) => (Number.isSafeInteger(v) && v > 0 && v <= 1e7 ? v : 0);
const problemOf = (m) => (PROBLEMS.includes(m && m.problem) ? m.problem : 'fail');

function take(m) {
  S.on = m.on === true;
  S.email = cleanEmail(m.email);
  S.queued = S.on ? posInt(m.queued) : 0;
  S.seats = posInt(m.seats) || 500;
  const u = safeUrl(m.sample_url);
  if (u) S.sample = u;
  S.state = 'ok'; S.problem = ''; M.note = '';
  M.need = m.need_email === true && !S.on;
  if (!M.need) { M.step = 'email'; M.to = ''; M.sentAt = 0; }
  else if (M.step === 'code' && Date.now() - M.sentAt > CODE_MS) M.step = 'email';   // that code has lapsed: start over
}

/** Paints #set-news-row from S (called on every answer, on open and on a language change). */
export function render() {
  const sw = el('set-news'), row = el('set-news-row');
  if (!sw || !row) return;
  // while a write is out the switch shows the position asked for (disabled, not greyed); else what the account has
  sw.setAttribute('aria-checked', String(S.busy ? S.want === true : S.state === 'ok' && S.on));
  sw.disabled = S.state !== 'ok' || S.busy || M.need;
  sw.setAttribute('aria-busy', String(S.busy));
  const to = el('set-news-to');
  const queued = S.state === 'ok' && S.on && S.queued > 0;
  // P393 #128: right after the address is confirmed the line names it while still off (「发到 x」 = where it will go)
  to.hidden = !(S.state === 'ok' && !M.need && (S.on || M.verified) && (S.email || queued));
  to.textContent = to.hidden ? '' : queued ? t('p374.queued', { n: String(S.queued), seats: String(S.seats) }) : t('p374.sentTo', { email: S.email });
  const a = el('set-news-sample');
  a.hidden = !S.sample;
  if (S.sample) a.href = S.sample; else a.removeAttribute('href');
  const msg = el('set-news-msg');
  const key = S.state === 'problem' ? MSG[S.problem] || 'p374.fail' : S.state === 'old' ? 'p374.old' : S.state === 'offline' ? 'p374.offline'
    : S.state === 'ok' && M.note ? MAIL_MSG[M.note] || 'p374.fail' : '';
  msg.hidden = !key;
  msg.textContent = key ? t(key) : '';
  // 「先把这台电脑连上 Agent J 账号」 / not bound: the existing 「打开账号后台」 link right under the sentence
  el('set-news-acct').hidden = !(S.state === 'problem' && (S.problem === 'unlinked' || S.problem === 'not_bound' || S.problem === 'account_email'));
  el('set-news-links').hidden = a.hidden && el('set-news-acct').hidden;
  renderMail();
}

/** P393 #128: the e-mail step under the switch (only while this host's person has no address yet). */
function renderMail() {
  const f = el('set-news-mail');
  if (!f) return;
  f.hidden = !(S.state === 'ok' && M.need);
  const code = M.step === 'code';
  el('set-news-step1').hidden = code;
  el('set-news-step2').hidden = !code;
  el('set-news-again').hidden = !code;
  const sent = el('set-news-sent');
  sent.hidden = !code;
  sent.textContent = code ? t('p393news.sentTo', { email: M.to }) : '';
  el('set-news-send').disabled = M.busy;
  el('set-news-send').setAttribute('aria-busy', String(M.busy && !code));
  const c = el('set-news-code');
  el('set-news-verify').disabled = M.busy || !/^\d{6}$/.test(c.value);
  el('set-news-verify').setAttribute('aria-busy', String(M.busy && code));
  el('set-news-email').setAttribute('aria-invalid', String(M.note === 'bad_email'));
  c.setAttribute('aria-invalid', String(M.note === 'invalid_code'));
  const left = Math.ceil((RESEND_MS - (Date.now() - M.sentAt)) / 1000);
  const r = el('set-news-resend');
  r.disabled = M.busy || left > 0;
  r.textContent = left > 0 ? t('p393news.resendIn', { s: String(left) }) : t('p393news.resend');
  // one 1 s tick only while a countdown is on screen
  if (code && left > 0 && !f.hidden) { if (!tick) tick = setInterval(renderMail, 1000); }
  else if (tick) { clearInterval(tick); tick = null; }
}

/** settings.js open(): ask once per opening of the panel (a second call while one is out is ignored). */
export async function load() {
  if (S.state === 'wait' || S.busy) return;
  if (!isReady()) { S.state = 'offline'; render(); return; }
  S.state = 'wait'; M.verified = false; M.note = ''; render();
  const m = await ask1({ t: 'news_get' }, 'news_res', WAIT_MS);
  if (m.t === 'news_res' && m.ok === true) take(m);
  else if (m.t === 'news_res') { S.state = 'problem'; S.problem = problemOf(m); }
  else if (m.t === 'timeout') S.state = 'old';               // a computer before P374 does not know news_get
  else S.state = 'offline';
  render();
}

/** The switch: flip at once, send news_set; ok → the host's answer; anything else → back where it was + a centred card. */
export async function toggle() {
  if (S.state !== 'ok' || S.busy || M.need) return false;
  const want = !S.on;
  if (!isReady()) { S.state = 'offline'; render(); explain('offline'); return false; }
  S.busy = true; S.want = want;
  render();
  const m = await ask1({ t: 'news_set', on: want }, 'news_res', WAIT_MS);
  S.busy = false; S.want = null;
  if (m.t === 'news_res' && m.ok === true) { take(m); render(); return true; }
  // the switch goes back to what the account still has
  let why;
  if (m.t === 'news_res') {
    why = problemOf(m);
    if (why === 'unlinked' || why === 'not_bound') { S.state = 'problem'; S.problem = why; }   // nothing to retry until linked
    if (why === 'need_email') { M.need = true; M.note = ''; render(); el('set-news-email').focus(); return false; }   // P393: the row asks
  } else why = m.t === 'timeout' ? 'noanswer' : 'offline';
  render();
  explain(why);
  return false;
}

function explain(why) {
  const text = why === 'offline' ? t('p302.prefOffline') : why === 'noanswer' ? t('p374.noAnswer') : t(MSG[why] || 'p374.fail');
  noticeSheet(t('p302.prefTitle'), text, t('p302.ok'));
}

/** P393 #128 news_email: mail a code to the typed address (a bad-looking one never leaves the phone). */
export async function sendCode(again = false) {
  if (S.state !== 'ok' || !M.need || M.busy) return false;
  const email = again ? M.to : el('set-news-email').value.trim();
  if (!cleanEmail(email)) { M.note = 'bad_email'; render(); el('set-news-email').focus(); return false; }
  if (!isReady()) { M.note = 'offline'; render(); return false; }
  M.busy = true; M.note = ''; render();
  const m = await ask1({ t: 'news_email', email, lang: lang() }, 'news_res', WAIT_MS);   // the code mail in the phone's language
  M.busy = false;
  if (m.t === 'news_res' && m.ok === true) {
    M.step = 'code'; M.to = email; M.sentAt = Date.now();
    el('set-news-code').value = '';
    render(); el('set-news-code').focus();
    return true;
  }
  mailFailed(m);
  return false;
}

/** P393 #128 news_verify: the 6 digits from the mail → the host's answer is a normal read (the address now this host's). */
export async function verify() {
  const code = el('set-news-code').value.trim();
  if (S.state !== 'ok' || !M.need || M.busy || M.step !== 'code' || !/^\d{6}$/.test(code)) return false;
  if (!isReady()) { M.note = 'offline'; render(); return false; }
  M.busy = true; M.note = ''; render();
  const m = await ask1({ t: 'news_verify', email: M.to, code }, 'news_res', WAIT_MS);
  M.busy = false;
  if (m.t === 'news_res' && m.ok === true) {
    const to = M.to;
    take(m);
    if (M.need) { M.note = 'fail'; render(); return false; }       // the account still has no address: say so, keep the form
    if (!S.email) S.email = cleanEmail(to);
    M.verified = true; M.note = 'verified';
    el('set-news-code').value = '';
    render();
    return true;
  }
  mailFailed(m);
  return false;
}

function mailFailed(m) {
  const why = m.t === 'news_res' ? problemOf(m) : m.t === 'timeout' ? 'noanswer' : 'offline';
  if (why === 'unlinked' || why === 'not_bound' || why === 'account_email') { S.state = 'problem'; S.problem = why; M.need = false; }
  else if (why === 'need_email') M.note = 'fail';
  else M.note = MAIL_MSG[why] ? why : 'fail';
  render();
  if (M.note === 'bad_email') el('set-news-email').focus();
  else if (M.note === 'invalid_code') el('set-news-code').select();
}

export function configure() {
  el('set-news').addEventListener('click', () => { toggle(); });
  // P393 #128: one form, Enter works in both fields — the visible step decides what it sends
  el('set-news-mail').addEventListener('submit', (e) => { e.preventDefault(); if (M.step === 'code') verify(); else sendCode(); });
  el('set-news-code').addEventListener('input', (e) => {
    const v = e.target.value.replace(/\D/g, '').slice(0, 6);
    if (v !== e.target.value) e.target.value = v;
    if (M.note === 'invalid_code') M.note = '';
    render();
  });
  el('set-news-email').addEventListener('input', () => { if (M.note === 'bad_email') { M.note = ''; render(); } });
  el('set-news-resend').addEventListener('click', () => { sendCode(true); });
  el('set-news-change').addEventListener('click', () => {
    M.step = 'email'; M.note = ''; M.sentAt = 0;
    render(); el('set-news-email').focus();
  });
}
export const _debug = () => ({ ...S, mail: { ...M } });
/** tests only: pretend the last code went out `ms` earlier (the 60 s 「重新发送」 wait). */
export function _rewind(ms) { M.sentAt -= ms; renderMail(); }
