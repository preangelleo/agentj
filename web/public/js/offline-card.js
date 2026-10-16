// P168: the disconnected state. When a paired phone cannot reach its computer for more than a blink, the grey page is
// blurred behind the global centred dialog (P150 gdlg, the same square as 「在办事项」) that says, in plain words: what is
// going on (reconnecting / the computer may be off or asleep), for how long (live), what the automatic reconnect is doing
// (the session's real attempt count and next try — nothing invented), what the owner can do (check the computer,
// `agentj doctor --reconnect`) and 「立即重试」. After LONG_OFFLINE_MS a human fallback appears: write to
// feedback@agentj.app (mailto with a subject and a body template — no key, token, pairing data or URL with a secret; the
// page knows no account ID, so the owner fills it in) + a one-tap copy of the address. Reconnected → it closes by itself.
// 「稍后」 / ESC / a tap outside hide it (the offline queue under it stays usable); it comes back when the long-offline
// section appears, or with the next outage. The outage start survives a reload through the host record (app.js persist).
import { t, onLang, plain } from './t.js';
import { registerLayer } from './layers.js';
import { RelayMD } from './md.js';

export const LONG_OFFLINE_MS = 30 * 60 * 1000;       // Leo: 「超过 30 分钟无法连接，那可以写邮件到 feedback」
export const SHOW_AFTER_MS = 6000;                   // = app.js QUIET_MS: a blink never blurs the page
export const FEEDBACK_EMAIL = 'feedback@agentj.app';
const MAX_SINCE_MS = 30 * 86400 * 1000;              // an older stored start is not believed

let H = { retry() {}, retryInfo: () => ({ attempts: 0, nextAt: null }), persist() {}, version: '', device: () => '' };
let mode = 'gone';           // 'down' | 'ready' | 'other' (an error / removed page owns the screen) | 'gone' (not paired)
let key = '';                // the status key app.js painted (st.hostDown = the relay says the computer is offline)
let since = 0;               // outage start, ms (real clock; may come from the host record)
let seen = 0;                // when this page began to show the outage
let dropSeen = 0;            // a ready session dropped (app.js quiet window): the outage is counted from there
let persisted = false, dismissed = false, dismissedLong = false, timer = 0, skew = 0, copiedTimer = 0;
const now = () => Date.now() + skew;
const $ = (id) => document.getElementById(id);

/** ms → the duration line's key + vars. */
export function sinceText(ms) {
  const m = Math.floor(Math.max(0, ms) / 60000);
  if (m < 1) return ['off.since.lt1', {}];
  if (m < 60) return ['off.since.min', { n: m }];
  return ['off.since.hour', { h: Math.floor(m / 60), m: m % 60 }];
}
export const isLong = (ms) => ms > LONG_OFFLINE_MS;

/** The mailto link: address + subject + a body template. Only display text the page already shows (version, device kind,
 *  local start time) — never a key, token, channel, pairing link or relay address. */
export function mailtoHref({ subject, body }) {
  return 'mailto:' + FEEDBACK_EMAIL + '?subject=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body);
}
function startedAt() {
  const d = new Date(since || now());
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
export function mailParts() {
  return { subject: plain(t('off.mailSubject')), body: plain(t('off.mailBody', { since: startedAt(), ver: H.version || '?', device: H.device() || '?' })) };
}

export function configure(hooks) { H = { ...H, ...hooks }; }
/** A stored outage start (host record) from before a reload. */
export function restore(at) {
  if (!Number.isFinite(at) || at > Date.now() || Date.now() - at > MAX_SINCE_MS) return;
  if (!since || at < since) since = at;
  persisted = true;
}

/** app.js: a ready session just dropped (the screen stays quiet for QUIET_MS while it reconnects underneath). */
export function dropped() { if (!since) since = now(); if (!dropSeen) dropSeen = now(); }

/** app.js, on every painted status / view: m = 'down' | 'ready' | 'other' | 'gone'. */
export function update(m, statusKey = '') {
  key = statusKey;
  if (m === 'down') {
    if (mode !== 'down') { seen = dropSeen || now(); dropSeen = 0; dismissed = false; if (!since) since = now(); }
  } else if (m === 'ready' || m === 'gone') {
    if (persisted) H.persist(null);
    since = 0; seen = 0; dropSeen = 0; persisted = false; dismissed = false; dismissedLong = false;
  }
  mode = m;
  if (mode === 'down' && !timer) timer = setInterval(paint, 1000);
  if (mode !== 'down' && timer) { clearInterval(timer); timer = 0; }
  paint();
}

export const isOpen = () => !!$('offDlg') && !$('offDlg').hidden;
/** P332: 「紧急修复」 opens Settings → 紧急修复 — the card steps aside like 「稍后」. */
export function later() { if (isOpen()) close(); }
function close() {
  dismissed = true;
  if (isLong(now() - since)) dismissedLong = true;
  paint();
}

function paint() {
  const d = $('offDlg');
  if (!d) return;
  const t0 = now();
  const long = mode === 'down' && isLong(t0 - since);
  if (mode === 'down' && !persisted && t0 - seen >= SHOW_AFTER_MS) { persisted = true; H.persist(since); }
  const show = mode === 'down' && t0 - seen >= SHOW_AFTER_MS && (!dismissed || (long && !dismissedLong));
  if (!show) { if (!d.hidden) d.hidden = true; document.body.removeAttribute('data-offline-card'); return; }
  if (dismissed && long) dismissed = false;           // the long-offline section is news: shown again once
  const host = key === 'st.hostDown';
  d.dataset.state = host ? 'computer' : 'reconnecting';
  d.dataset.long = long ? '1' : '0';
  $('offTitle').textContent = t(host ? 'off.title.computer' : 'off.title.reconnecting');
  const [sk, sv] = sinceText(t0 - since);
  const sinceLine = t(sk, sv);
  if ($('offSince').textContent !== sinceLine) $('offSince').textContent = sinceLine;   // aria-live: once a minute, not a second
  $('offProg').textContent = progress(host);
  $('offLong').hidden = !long;
  if (long) {
    $('offMail').href = mailtoHref(mailParts());
    $('offMail').textContent = FEEDBACK_EMAIL;
  }
  if (d.hidden) d.hidden = false;
  document.body.dataset.offlineCard = long ? 'long' : 'short';
}
function progress(host) {
  if (host) return t('off.prog.waitHost');
  const r = H.retryInfo() || {};
  if (r.nextAt) return t('off.prog.next', { n: r.attempts, s: Math.max(1, Math.ceil((r.nextAt - Date.now()) / 1000)) });
  if (r.attempts) return t('off.prog.trying', { n: r.attempts });
  return t('off.prog.connecting');
}

async function copyAddress() {
  const ok = await RelayMD.copyText(FEEDBACK_EMAIL);
  const b = $('offCopy');
  b.classList.toggle('done', ok);
  const label = plain(t(ok ? 'off.copied' : 'r.copy.fail'));
  b.setAttribute('aria-label', label); b.title = label;
  $('offCopyNote').textContent = label;
  clearTimeout(copiedTimer);
  copiedTimer = setTimeout(() => { b.classList.remove('done'); relabel(); $('offCopyNote').textContent = ''; }, 1800);
}
function relabel() { const l = plain(t('off.copy')); $('offCopy').setAttribute('aria-label', l); $('offCopy').title = l; }

export function init() {
  const d = $('offDlg');
  $('offLater').addEventListener('click', close);
  $('offRetry').addEventListener('click', () => { H.retry(); paint(); });
  $('offCopy').addEventListener('click', () => { copyAddress().catch(() => {}); });
  d.addEventListener('click', (e) => { if (e.target === e.currentTarget) close(); });   // outside the square = later
  // never takes focus by itself (the owner may be typing into the offline queue); ESC = 稍后
  registerLayer({ element: () => d, open: isOpen, close, fallback: () => $('setBtn'), trap: false, rank: 4 });
  onLang(() => { relabel(); paint(); });   // the static lines are [data-i18n] (app.js relang → applyStatic)
  relabel();
}

// tests only: move this page's clock (the outage start is real time; the card reads now() + skew)
export function _skew(ms) { skew = ms; paint(); }
export const _debug = () => ({ mode, key, since, seen, persisted, dismissed, dismissedLong, skew });
