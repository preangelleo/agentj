// 0.26.9 selfheal (CONTRACT §C, #464 — chairman: 「把 login 这样的 slash command 变成可以点击的……在弹窗里边完成 authentication」)
// The AI-login rescue over the chat: a reply whose AI coding tool is signed out (host `login: {agent, why}`) gets
// 「重新登录」; the tap opens this sheet with that agent preselected and starts the host's `login` route at once:
//   phone → host  signed `auth_rescue {action:"login", agent}`          (control-v1 action `auth_rescue`, target {action, agent})
//   host → phone  `auth_rescue {view:{step:"url", url, code?}}`         the authorisation link (+ the device code to type there)
//   phone → host  signed `auth_rescue {action:"code", code}`             the one-time code the owner pasted (target {action, code})
//   host → phone  `auth_rescue {view:{step:"verifying"|"done", ok?, why?, text?}}`
// `done ok:true` comes only after the host proved the MAIN session answers (ADR-462); then — and only then — the sheet
// closes by itself. A failure says the host's plain reason, offers 「再试一次」 and 「其他登录方式」 (Settings → AI 登录救援, the
// full P258 component with every route). Esc / × / a tap outside closes the sheet at any time; the host's run is not
// cancelled by that, and 「重新登录」 opens the same run again. The pasted code is sent once and never kept on the page.
// Everything here is textContent; the link opens in a new tab with noopener.
import { request, done, isReady } from './session.js';
import { signed } from './controls.js';
import { t, onLang } from './t.js';
import { mk } from './ui.js';
import { copyNow, copyable } from './copycode.js';
import { registerLayer } from './layers.js';
import { AGENTS, WHYS, AGENT_NAME } from './selfheal.js';

export const FIRST_MS = 45000;        // no first answer this long → the host is too old or stuck
export const URL_MS = 15 * 60000;     // the owner signs in on another tab
export const CHECK_MS = 4 * 60000;    // the host checks the code and proves the main session
const STEPS = ['starting', 'url', 'verifying', 'cancelling', 'done'];

let root = null, hooks = {};
const fresh = () => ({ agent: '', why: '', st: 'idle', url: '', code: '', paste: true, text: '', fail: '', rs: [], timer: 0, closeTimer: 0, sent: false });
let S = fresh();

const str = (v, n) => (typeof v === 'string' ? v.replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '').slice(0, n) : '');
const httpsUrl = (u) => { try { const x = new URL(u); return x.protocol === 'https:' ? x.href : ''; } catch { return ''; } };

/** app.js: {openAuth()} — Settings → AI 登录救援 (the full component). */
export function configure(h) { hooks = h || {}; }

function build() {
  if (root) return root;
  root = mk('div', 'ajmodal dlgcard lgs');
  root.id = 'lgs'; root.hidden = true;
  root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'lgs-title');
  const box = mk('div', 'box');
  const head = mk('div', 'lgs-head');
  const h = mk('h2', undefined, undefined, 'aj-auth-card'); h.id = 'lgs-title';
  const x = mk('button', 'menuicon lgs-x', '×', 'aj-auth-card'); x.type = 'button'; x.id = 'lgs-x';
  x.addEventListener('click', close);
  head.append(h, x);
  const body = mk('div', 'lgs-body'); body.id = 'lgs-body'; body.setAttribute('aria-live', 'polite');
  box.append(head, body);
  root.append(box);
  root.addEventListener('click', (e) => { if (e.target === root) close(); });
  document.body.append(root);
  registerLayer({ element: () => root, open: () => !!root && !root.hidden, close, rank: 40,
    fallback: () => document.getElementById('input') });
  onLang(paint);
  return root;
}

/** relay.js: the reply's 「重新登录」 / `/login`. Same agent while a run is under way → just show it again. */
export function open(agent, why = 'not_logged_in') {
  if (!AGENTS.includes(agent)) return;
  build();
  const same = S.agent === agent && ['run', 'url', 'check'].includes(S.st);
  if (!same) { stop(); S = { ...fresh(), agent, why: WHYS.includes(why) ? why : 'not_logged_in' }; }
  root.hidden = false;
  paint();
  if (!same && agent !== 'opencode') start();
  (root.querySelector('#lgs-code') || root.querySelector('.lgs-acts .aj-action--primary') || root.querySelector('#lgs-x'))?.focus({ preventScroll: true });
}
export function close() {
  if (!root || root.hidden) return;
  clearTimeout(S.closeTimer); S.closeTimer = 0;
  root.hidden = true;
  if (S.st === 'ok' || S.st === 'fail') { stop(); S = fresh(); }   // an ended run is over; a running one waits for the next open
}
export const isOpen = () => !!root && !root.hidden;

function stop() { clearTimeout(S.timer); S.timer = 0; for (const r of S.rs) done(r); S.rs = []; }
function arm(ms) { clearTimeout(S.timer); S.timer = setTimeout(() => fail('', 'noAnswer'), ms); }

async function send(target) {
  if (!isReady()) { fail('', 'offline'); return false; }
  let obj;
  try { obj = await signed('auth_rescue', target, { t: 'auth_rescue', ...target }); }
  catch (e) { fail('', e && e.message === 'no_key' ? 'no_key' : 'offline'); return false; }
  const r = request(obj, onMsg);
  if (!r) { fail('', 'offline'); return false; }
  S.rs.push(r);
  return true;
}
async function start() {
  stop();
  Object.assign(S, { st: 'run', url: '', code: '', paste: true, text: '', fail: '', sent: false });
  paint();
  if (await send({ action: 'login', agent: S.agent })) arm(FIRST_MS);
}
async function submit() {
  const f = root && root.querySelector('#lgs-code');
  const code = f ? f.value.replace(/\s+/g, '') : '';
  if (!code || code.length > 4096) { const n = root.querySelector('#lgs-note'); if (n) n.textContent = t('p0269.login.empty'); f?.focus(); return; }
  if (f) f.value = '';                      // never kept on the page
  Object.assign(S, { st: 'check', sent: true, text: '' });
  paint();
  if (await send({ action: 'code', code })) arm(CHECK_MS);
}

function onMsg(m) {
  if (!m) return;
  if (m.t === 'offline') return fail('', 'offline');
  if (m.t === 'ctl_res' && m.ok === false) return fail(str(m.text, 300), m.why);
  if (m.t === 'auth_rescue' && m.view && typeof m.view === 'object') onView(m.view);
}
/** authrescue.js: an `auth_rescue` push whose view is a login step (`view.step`) belongs to this sheet. */
export function onView(v) {
  if (!v || !STEPS.includes(v.step) || S.st === 'idle' || S.st === 'ok') return;
  if (typeof v.agent === 'string' && v.agent !== S.agent) return;
  if (v.step === 'starting') { S.st = 'run'; S.text = str(v.text, 300); arm(FIRST_MS); }
  if (v.step === 'url') {
    const url = httpsUrl(v.url);
    if (!url) return fail(t('p0269.login.badLink'));
    const code = str(v.code, 64).trim();
    // `again: "bad_code"` = the pasted code was refused: the same link stays, the field asks for it once more
    Object.assign(S, { st: 'url', url, code, paste: !code || v.paste === true, text: v.again === 'bad_code' ? t('p0269.login.badCode') : str(v.text, 300) });
    arm(URL_MS);
  }
  if (v.step === 'cancelling') return;
  if (v.step === 'verifying') { S.st = 'check'; S.text = str(v.text, 300); arm(CHECK_MS); }
  if (v.step === 'done') {
    if (v.ok === true) return ok();
    return fail(str(v.text, 300), str(v.why, 40));   // the host's plain sentence; else what its reason code means here
  }
  paint();
}
function ok() {
  stop();
  S.st = 'ok';
  paint();
  hooks.done?.(S.agent);
  clearTimeout(S.closeTimer);
  S.closeTimer = setTimeout(close, 1600);   // the green line stays a moment, then the chat is back
}
function fail(text, why = '') {
  stop();
  S.st = 'fail';
  S.fail = text || (why === 'offline' ? t('p332.offline') : why === 'noAnswer' ? t('p0269.login.noAnswer')
    : why === 'no_key' ? t('p332.why.no_key') : ['stale', 'bad_signature', 'replay', 'busy', 'shape', 'unavailable'].includes(why) ? t('p332.why.' + why) : '');
  paint();
}

// ---------------------------------------------------------------- painting
function btn(id, label, cls, fn) {
  const b = mk('button', 'aj-action ' + (cls || ''), label, 'aj-action', 'dialog'); b.type = 'button'; if (id) b.id = id;
  b.addEventListener('click', fn); return b;
}
const p = (text, cls, id) => { const n = mk('p', 'lgs-line ' + (cls || ''), text); if (id) n.id = id; return n; };
const acts = (...bs) => { const d = mk('div', 'lgs-acts'); d.append(...bs.filter(Boolean)); return d; };
function bar(step) {
  const b = mk('div', 'rsbar'); b.id = 'lgs-bar';
  const pct = Math.round(((step - 0.5) / 3) * 100);
  b.setAttribute('role', 'progressbar'); b.setAttribute('aria-valuemin', '0'); b.setAttribute('aria-valuemax', '100');
  b.setAttribute('aria-valuenow', String(pct)); b.setAttribute('aria-label', t('p332.progress'));
  const i = mk('i'); i.style.setProperty('--p', pct + '%'); b.append(i);
  return b;
}
function other() {
  return btn('lgs-other', t('p0269.login.other'), '', () => { close(); hooks.openAuth?.(); });
}
export function paint() {
  if (!root) return;
  const name = AGENT_NAME[S.agent] || '';
  root.querySelector('#lgs-title').textContent = t('p0269.login.title', { agent: name });
  const x = root.querySelector('#lgs-x'); x.setAttribute('aria-label', t('p0269.login.close')); x.title = t('p0269.login.close');
  root.dataset.state = S.st;
  const parts = [];
  if (S.st !== 'ok') parts.push(p(t('p0269.login.why.' + (S.why || 'not_logged_in'), { agent: name }), 'lgs-why', 'lgs-why'));
  if (S.agent === 'opencode') {
    parts.push(p(t('p0269.login.opencode'), '', 'lgs-msg'), acts(btn('lgs-other', t('p0269.login.other'), 'aj-action--primary', () => { close(); hooks.openAuth?.(); })));
  } else if (S.st === 'run') {
    parts.push(bar(1), p(S.text || t('p0269.login.starting'), 'lgs-now', 'lgs-now'));
  } else if (S.st === 'url') {
    parts.push(p(t('p0269.login.step1'), 'lgs-step'));
    const a = mk('a', 'aj-action aj-action--primary lgs-open', t('p0269.login.open'), 'aj-action', 'dialog');
    a.id = 'lgs-open'; a.href = S.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    parts.push(acts(a, btn('lgs-copy', t('p0269.login.copyLink'), '', () => copyNow(S.url))));
    if (S.code) {
      parts.push(p(t('p0269.login.step2code'), 'lgs-step'));
      const c = mk('p', 'arcode lgs-devcode', S.code); c.id = 'lgs-devcode';
      const cb = btn('lgs-copycode', t('ar.copy'), '', () => copyNow(S.code));
      copyable(c, { variant: 'ext', btn: cb });
      parts.push(c, acts(cb));
    }
    if (S.paste) {
      parts.push(p(t(S.code ? 'p0269.login.step3paste' : 'p0269.login.step2paste'), 'lgs-step'));
      const f = mk('input', 'lgs-code', undefined, 'aj-auth-card'); f.id = 'lgs-code'; f.type = 'text'; f.autocomplete = 'off'; f.spellcheck = false;
      f.setAttribute('autocapitalize', 'off'); f.setAttribute('autocorrect', 'off'); f.setAttribute('enterkeyhint', 'send');
      f.placeholder = t('p0269.login.paste'); f.setAttribute('aria-label', t('p0269.login.paste')); f.maxLength = 4096;
      f.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); submit(); } });
      parts.push(f, p('', 'lgs-note', 'lgs-note'), acts(btn('lgs-submit', t('p0269.login.submit'), 'aj-action--primary', submit)));
    }
    if (S.text) parts.push(p(S.text, S.text === t('p0269.login.badCode') ? 'lgs-host lgs-fail' : 'lgs-host', 'lgs-host'));
  } else if (S.st === 'check') {
    parts.push(bar(3), p(S.text || t('p0269.login.checking'), 'lgs-now', 'lgs-now'));
  } else if (S.st === 'ok') {
    parts.push(p(t('p0269.login.done'), 'rsok lgs-ok', 'lgs-msg'));
  } else if (S.st === 'fail') {
    parts.push(p(S.fail ? t('p0269.login.fail', { why: S.fail }) : t('p0269.login.failPlain'), 'rsfail lgs-fail', 'lgs-msg'),
      acts(btn('lgs-again', t('p332.again'), 'aj-action--primary', start), other()));
  }
  root.querySelector('#lgs-body').replaceChildren(...parts);
}

// tests
export const _state = () => ({ agent: S.agent, why: S.why, st: S.st, url: S.url, code: S.code, paste: S.paste, fail: S.fail, timer: !!S.timer, rs: S.rs.length });
export function _reset() { stop(); clearTimeout(S.closeTimer); S = fresh(); if (root) root.hidden = true; }
