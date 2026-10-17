// P374 · Settings → 账号和帮助 → 「Daily AI News」: ONE switch (default off) — the account e-mail gets the daily
// 「Musk + AI 24h」 digest. The phone never talks to the cloud: {t:'news_get'} / {t:'news_set', on} go to the computer
// inside the Noise session, the computer asks the account (POST /v1/host/news) and answers news_res (P374 contract §5):
//   {t:'news_res', ok:true, on, email, sample_url, queued, seats} · {t:'news_res', ok:false, problem: unlinked|not_bound|unreachable|rate_limited|fail}
// queued (P374 追加): all seats taken → on + the place in the waiting list (1 = next); the line under the switch says so.
// A computer without P374 never answers: the switch stays disabled with a hint. The e-mail is shown with textContent only and
// the sample link is accepted only as an https: URL (the host's text is data, never markup or a script URL).
import { t } from './t.js';
import { el, noticeSheet } from './ui.js';
import { ask1, isReady } from './session.js';

export const WAIT_MS = 10000;     // the host's own cloud budget is 8 s (contract §5) — a little longer, so its answer wins the race
const PROBLEMS = ['unlinked', 'not_bound', 'unreachable', 'rate_limited', 'fail'];
const MSG = { unlinked: 'p374.unlinked', not_bound: 'p374.notBound', unreachable: 'p374.unreachable', rate_limited: 'p374.rateLimited', fail: 'p374.fail' };
// state: 'idle' (not asked yet) · 'wait' (news_get out) · 'ok' · 'problem' (host answered ok:false) · 'old' (no news_res) · 'offline'
let S = { state: 'idle', on: false, email: '', sample: '', problem: '', busy: false, want: null, queued: 0, seats: 0 };

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
  S.state = 'ok'; S.problem = '';
}

/** Paints #set-news-row from S (called on every answer, on open and on a language change). */
export function render() {
  const sw = el('set-news'), row = el('set-news-row');
  if (!sw || !row) return;
  // while a write is out the switch shows the position asked for (disabled, not greyed); else what the account has
  sw.setAttribute('aria-checked', String(S.busy ? S.want === true : S.state === 'ok' && S.on));
  sw.disabled = S.state !== 'ok' || S.busy;
  sw.setAttribute('aria-busy', String(S.busy));
  const to = el('set-news-to');
  const queued = S.state === 'ok' && S.on && S.queued > 0;
  to.hidden = !(S.state === 'ok' && S.on && (S.email || queued));
  to.textContent = to.hidden ? '' : queued ? t('p374.queued', { n: String(S.queued), seats: String(S.seats) }) : t('p374.sentTo', { email: S.email });
  const a = el('set-news-sample');
  a.hidden = !S.sample;
  if (S.sample) a.href = S.sample; else a.removeAttribute('href');
  const msg = el('set-news-msg');
  const key = S.state === 'problem' ? MSG[S.problem] : S.state === 'old' ? 'p374.old' : S.state === 'offline' ? 'p374.offline' : '';
  msg.hidden = !key;
  msg.textContent = key ? t(key) : '';
  // 「先把这台电脑连上 Agent J 账号」 / not bound: the existing 「打开账号后台」 link right under the sentence
  el('set-news-acct').hidden = !(S.state === 'problem' && (S.problem === 'unlinked' || S.problem === 'not_bound'));
  el('set-news-links').hidden = a.hidden && el('set-news-acct').hidden;
}

/** settings.js open(): ask once per opening of the panel (a second call while one is out is ignored). */
export async function load() {
  if (S.state === 'wait' || S.busy) return;
  if (!isReady()) { S.state = 'offline'; render(); return; }
  S.state = 'wait'; render();
  const m = await ask1({ t: 'news_get' }, 'news_res', WAIT_MS);
  if (m.t === 'news_res' && m.ok === true) take(m);
  else if (m.t === 'news_res') { S.state = 'problem'; S.problem = problemOf(m); }
  else if (m.t === 'timeout') S.state = 'old';               // a computer before P374 does not know news_get
  else S.state = 'offline';
  render();
}

/** The switch: flip at once, send news_set; ok → the host's answer; anything else → back where it was + a centred card. */
export async function toggle() {
  if (S.state !== 'ok' || S.busy) return false;
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
  } else why = m.t === 'timeout' ? 'noanswer' : 'offline';
  render();
  explain(why);
  return false;
}

function explain(why) {
  const text = why === 'offline' ? t('p302.prefOffline') : why === 'noanswer' ? t('p374.noAnswer') : t(MSG[why] || 'p374.fail');
  noticeSheet(t('p302.prefTitle'), text, t('p302.ok'));
}

export function configure() {
  el('set-news').addEventListener('click', () => { toggle(); });
}
export const _debug = () => ({ ...S });
