import { reopenAuth } from './elevate.js';
import { authOf } from './authcard.js';
// P258 · Settings → AI 登录救援 (PROTOCOL §31): a harness whose subscription login expired is signed in again from the phone.
// The host runs the official login command; this page shows its link / device code, and the code or token is pasted only
// into the host's sealed secret card (elevate.js) — never into these messages. Starting needs the owner's passkey (an
// ask card). Everything is textContent; the link opens in a new tab with noopener.
// P288: 「Claude 登录还有 N 天」 per row (or how a login without a fixed expiry is watched: the daily health check), a
// 「现在续期」 button inside the last 7 days, and a thin chat banner (the host's view.renew) that starts the same route A —
// dismissed per reminder mark on this phone. The authorize round shows its countdown and reopens by itself on the host.
import { t, fillText } from './t.js';
import { el } from './ui.js';
import { copyable, copyNow } from './copycode.js';   // P292
import { ask1, isReady } from './session.js';

let view = null;          // the host's last view: {harnesses, flow, dot, renew?}
let hooks = {};           // app.js: {openAuth} (Settings → AI 登录救援)
const RENEW_KEY = 'aj.arRenew';
let timer = null, poll = null, loadId = 0;
const LONG = 11 * 60 * 1000;   // auth_start answers after the passkey card (up to its 10 minutes)

function say(s) { const m = el('ar-msg'); if (m) m.textContent = s || ''; }
const tw = (k, fb) => { const s = t(k); return s === k ? (fb || '') : s; };

async function call(obj, ms = 20000) {
  if (!isReady()) { say(t('set.offline')); return null; }
  const m = await ask1(obj, 'auth_res', ms);
  if (m.t !== 'auth_res') { say(m.t === 'timeout' ? t('ar.timeout') : t('set.offline')); return null; }
  if (m.view && typeof m.view === 'object') setView(m.view);
  if (!m.ok && m.why) say(tw('ar.why.' + m.why, m.why));
  return m;
}

/** app.js: the host's `auth_rescue` push (a flow moved on, a new alert, a reminder; on ready only while the dot is on). */
export function onPush(m) { if (m && m.view && typeof m.view === 'object') setView(m.view); }
export async function load() {
  close(); const id = ++loadId; view = null; el('ar-list')?.replaceChildren(); el('ar-flow')?.replaceChildren(); say(t('ar.checking'));
  const m = await call({ t: 'auth_get' }); if (m) say('');
  if (id !== loadId) return;
  poll = setInterval(() => { if (!document.hidden) call({ t:'auth_get', check:false }); }, 5000);
}

const watchers = [];      // P332: Settings → 紧急修复 card ③ shows this flow's state (rescue.js)
function setView(v) {
  const before = view?.flow, flow = v.flow;
  const moved = flow && (!before || ['kind','stage','round','route'].some(k => before[k] !== flow[k]));
  view = v; dot(); render(); banner();
  // Long multi-harness pages preserve their scroll anchor. Bring a new device/code card into view once; health polling
  // never pulls the owner away from what they are reading, and a hidden Settings page is never opened here.
  if (moved && el('set-auth')?.getClientRects().length) el('ar-flow')?.scrollIntoView({ block:'start' });
  for (const f of watchers) { try { f(view); } catch { /* a painter must not stop the rest */ } }
}
/** P332: card ③ of 紧急修复 follows the view (flow stage → progress / green / failure). */
export function watch(fn) { watchers.push(fn); if (view) fn(view); }

/** P288: the chat's renew banner — view.renew {kind, days_left, expires, mark}; ✕ hides it until the next reminder mark. */
function renewTag(r) { return r ? `${r.expires}:${r.mark ?? ''}` : ''; }
function banner() {
  const b = el('ar-renew');
  if (!b) return;
  const r = view && view.renew;
  let gone = '';
  try { gone = localStorage.getItem(RENEW_KEY) || ''; } catch { /* private mode */ }
  b.hidden = !r || gone === renewTag(r);
  if (b.hidden) return;
  const open = el('ar-renew-open');
  if (open) open.textContent = r.days_left > 0 ? t('ar.renewBanner').replace('{d}', String(r.days_left)) : t('ar.renewBannerToday');
}
function renewLater() {
  try { localStorage.setItem(RENEW_KEY, renewTag(view && view.renew)); } catch { /* private mode */ }
  banner();
}
function renewNow() {
  if (hooks.openAuth) hooks.openAuth();
  start('claude', 'setup_token');
}
function dot() {
  const on = !!(view && view.dot);
  for (const id of ['ar-dot', 'ar-dot-top']) { const d = el(id); if (d) d.hidden = !on; }
}

const STATE = { ok: 'ar.stOk', login: 'ar.stLogin', balance: 'ar.stBalance', rate_limit: 'ar.stRate', checking: 'ar.checking', unknown: 'ar.unknown' };
function btn(label, cls, fn) {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'aj-btn aj-btn--sm' + (cls ? ' ' + cls : ''); b.textContent = label;
  b.addEventListener('click', fn);
  return b;
}
function line(text, cls) { const p = document.createElement('p'); p.className = 'setnote' + (cls ? ' ' + cls : ''); fillText(p, text); return p; }   // P292: `commands` → code + ⧉

async function start(kind, route) {
  say(t('ar.confirm'));
  const m = await call({ t: 'auth_start', kind, route }, LONG);
  if (m && m.ok) say(t('ar.started'));
}

function harnessCard(h) {
  const box = document.createElement('div');
  box.className = 'setrow arrow';
  box.dataset.kind = h.kind;
  const head = document.createElement('div');
  head.className = 'arhead';
  const b = document.createElement('b'); b.textContent = h.label;
  const chip = document.createElement('span');
  chip.className = 'archip archip--' + (h.state || 'ok');
  chip.textContent = t(STATE[h.state] || 'ar.stOk');
  head.append(b, chip);
  box.append(head);
  if (h.state === 'checking') return box;
  if (h.state === 'balance') box.append(line(t('ar.balanceNote')));
  if (h.state === 'rate_limit') box.append(line(t('ar.rateNote')));
  if (h.token) {
    const d = h.token.days_left;
    const k = d > 0 ? 'ar.token' : d === 0 ? 'ar.tokenToday' : 'ar.tokenPast';
    box.append(line(t(k).replace('{e}', h.token.expires).replace('{d}', String(d)), h.warn ? 'ardanger artoken' : 'artoken'));
  } else if (h.installed && h.expiry) {
    const k = { unknown: 'ar.expUnknown', auto: h.kind === 'codex' ? 'ar.expAutoCodex' : h.kind === 'kimi' ? 'ar.expAutoKimi' : 'ar.expAuto', none: 'ar.expNone' }[h.expiry];
    if (k) box.append(line(t(k), 'arexp'));
  }
  if (h.installed) box.append(btn(t('ar.checkNow'), '', () => call({t:'auth_get'})));
  if (h.health && h.health.at) {
    const when = new Date(h.health.at * 1000).toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    box.append(line(t(h.health.ok ? 'ar.healthOk' : 'ar.healthFail').replace('{t}', when), h.health.ok ? 'arhealth' : 'arhealth ardanger'));
  }
  if (h.kind === 'claude' && h.installed && (h.warn || h.expiry === 'unknown')) {
    const p = document.createElement('p'); p.className = 'mailbtns';
    p.append(btn(t('ar.renewNow'), 'aj-btn--primary arrenewbtn', () => start('claude', 'setup_token')));
    box.append(p, line(t('ar.renewNote')));
  }
  if (h.conflicts && h.conflicts.length) box.append(line(t('ar.conflict').replace('{n}', h.conflicts.join(', ')), 'ardanger'));
  const last = h.last;
  if (last) {
    if (last.result === 'recovered') box.append(line(t('ar.lastOk')));
    else if (last.screen) {
      box.append(line(t('ar.blocked').replace('{s}', last.screen.label || last.screen.id), 'ardanger'));
      if (last.screen.last) box.append(line(last.screen.last, 'arlast'));
      if (last.screen.action === 'confirm') {
        const p = document.createElement('p'); p.className = 'mailbtns';
        p.append(btn(t('ar.press'), 'aj-btn--primary', () => press(h.kind, last.screen.id)));
        box.append(p);
      }
    } else if (last.why) box.append(line(t('ar.lastFail').replace('{w}', tw('ar.why.' + last.why, last.why)), 'ardanger'));
  }
  if (!h.installed) { box.append(line(t('ar.notInstalled'))); return box; }
  const p = document.createElement('p'); p.className = 'mailbtns';
  if (h.kind === 'claude') {
    p.append(btn(t('ar.claudeA'), 'aj-btn--primary', () => start('claude', 'setup_token')),
             btn(t('ar.claudeB'), '', () => start('claude', 'paste')));
    box.append(p, line(t('ar.claudeNote')));
    const d = document.createElement('details'); d.className = 'mailadv';
    const s = document.createElement('summary'); s.textContent = t('ar.more');
    const q = document.createElement('p'); q.className = 'mailbtns';
    q.append(btn(t('ar.claudeC'), '', () => start('claude', 'login')));
    d.append(s, q, line(t('ar.claudeCNote')));
    box.append(d);
  } else if (h.kind === 'codex') {
    const before = line(t('ar.codexBefore'), 'arbefore');
    const security = document.createElement('a');
    security.href = 'https://chatgpt.com/#settings/Security';
    security.target = '_blank'; security.rel = 'noopener noreferrer';
    security.className = 'arlink'; security.textContent = t('ar.codexSecurity');
    before.append(document.createTextNode(' '), security);
    box.append(before);
    p.append(btn(t('ar.codexA'), 'aj-btn--primary', () => start('codex', 'device')));
    box.append(p, line(t('ar.codexNote')));
  } else if (h.kind === 'kimi') {   // P305: `kimi login` device code (kimi.com / kimi.ai), or a key card
    p.append(btn(t('ar.kimiA'), 'aj-btn--primary', () => start('kimi', 'device')));
    box.append(p, line(t('ar.kimiNote')), line(t('ar.kimiKey')));
  } else {
    p.append(btn(t('ar.opencodeA'), '', () => start('opencode', 'provider_key')));
    box.append(p, line(t('ar.opencodeNote')));
  }
  return box;
}

async function press(kind, id) {
  say(t('ar.confirm'));
  const m = await call({ t: 'auth_press', kind, id }, LONG);
  if (m && m.ok) say(t('ar.verifying'));
}

function copyBtn(text) {
  return btn(t('ar.copy'), '', () => { copyNow(text).then((ok) => say(t(ok ? 'ar.copied' : 'r.copy.fail')), () => {}); });   // P292: + the execCommand fallback
}

function flowCard(f) {
  const box = el('ar-flow');
  box.replaceChildren();
  clearInterval(timer); timer = null;
  if (!f) { box.hidden = true; return; }
  box.hidden = false;
  const h = document.createElement('h3'); h.className = 'setg'; h.textContent = t('ar.flowTitle').replace('{h}', { codex: 'Codex', kimi: 'Kimi Code' }[f.kind] || 'Claude Code');
  box.append(h);
  const link = (u) => { const a = document.createElement('a'); a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.className = 'arlink'; a.textContent = t('ar.openLink'); return a; };
  if (['url','device'].includes(f.stage)) box.append(line(t('p332.auth.wait')));
  if (f.stage === 'starting') box.append(line(t('ar.stStarting')));
  if (f.stage === 'device' && authOf({...f,round:f.round||1,rounds:f.rounds||1}) && f.code) {
    box.append(line(t('ar.deviceStep1')));
    const p = document.createElement('p'); p.className = 'mailbtns'; p.append(link(f.url), copyBtn(f.url)); box.append(p);
    box.append(line(t('ar.deviceStep2')));
    const c = document.createElement('p'); c.className = 'arcode'; c.textContent = f.code; box.append(c);
    const q = document.createElement('p'); q.className = 'mailbtns'; const cb = copyBtn(f.code); q.append(cb); box.append(q);
    copyable(c, { variant: 'ext', btn: cb });   // P292: a tap on the code = 复制
    box.append(line(t('ar.round', { n: f.round || 1, max: f.rounds || 1 })));
    const left = line(''); box.append(left, line(t('ar.deviceNote')));
    const tick = () => { const s = Math.max(0, (f.expires_at || 0) - Math.floor(Date.now() / 1000)); left.textContent = t('ar.left').replace('{m}', String(Math.floor(s / 60))).replace('{s}', String(s % 60).padStart(2, '0')); };
    tick(); timer = setInterval(tick, 1000);
  }
  if (f.reopened && ['starting', 'url'].includes(f.stage)) box.append(line(t(f.reopened === 'expired' ? 'ar.reopenedExpired' : 'ar.reopenedRejected'), 'arreopen'));
  if (f.stage === 'url' && authOf({...f,code:f.code||'',round:f.round||1,rounds:f.rounds||3})) {
    box.append(line(t('ar.urlStep1')));
    const p = document.createElement('p'); p.className = 'mailbtns'; p.append(link(f.url), copyBtn(f.url)); box.append(p);
    box.append(line(t('ar.urlStep2')), btn(t('ar.openCard'), '', reopenAuth));
    if (f.expires_at) {   // P288: the link's 10 minutes; at 0 the host opens the next round by itself
      const left = line('', 'arleft'); box.append(left);
      const tick = () => {
        const s = Math.max(0, f.expires_at - Math.floor(Date.now() / 1000));
        left.textContent = t('ar.urlLeft').replace('{m}', String(Math.floor(s / 60))).replace('{s}', String(s % 60).padStart(2, '0'))
          + (f.round ? ' · ' + t('ar.round').replace('{n}', String(f.round)).replace('{max}', String(f.rounds || 3)) : '');
      };
      tick(); timer = setInterval(tick, 1000);
    }
  }
  if (f.stage === 'paste') box.append(line(t('ar.pasteStep')));
  if (f.stage === 'verifying') box.append(line(t(f.saved ? 'ar.savedChecking' : 'ar.verifying')));
  if (f.stage === 'done') box.append(line(t('ar.done')));
  if (f.stage === 'failed') box.append(line(f.why || t('ar.failed'), 'ardanger'));
  if (['starting', 'device', 'url', 'paste'].includes(f.stage)) {
    const p = document.createElement('p'); p.className = 'mailbtns';
    p.append(btn(t('ar.cancel'), '', () => call({ t: 'auth_cancel' })));
    box.append(p);
  }
}

function render() {
  const list = el('ar-list');
  if (!list || !view) return;
  list.replaceChildren(...(view.harnesses || []).filter((h) => h.installed || h.current).map(harnessCard));
  flowCard(view.flow);
}

export function configure(h = {}) {
  hooks = h || {};
  document.addEventListener('visibilitychange', () => { if (poll && !document.hidden) call({ t:'auth_get', check:false }); });
  el('ar-renew-open')?.addEventListener('click', renewNow);
  el('ar-renew-x')?.addEventListener('click', renewLater);
}
export function close() { ++loadId; clearInterval(timer); clearInterval(poll); timer = poll = null; }
