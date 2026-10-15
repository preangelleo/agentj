// P258 · Settings → AI 登录救援 (PROTOCOL §31): a harness whose subscription login expired is signed in again from the phone.
// The host runs the official login command; this page shows its link / device code, and the code or token is pasted only
// into the host's sealed secret card (elevate.js) — never into these messages. Starting needs the owner's passkey (an
// ask card). Everything is textContent; the link opens in a new tab with noopener.
import { t } from './t.js';
import { el } from './ui.js';
import { ask1, isReady } from './session.js';

let view = null;          // the host's last view: {harnesses, flow, dot}
let timer = null;
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
export async function load() { say(t('ar.loading')); const m = await call({ t: 'auth_get' }); if (m) say(''); }

function setView(v) { view = v; dot(); render(); }
function dot() {
  const on = !!(view && view.dot);
  for (const id of ['ar-dot', 'ar-dot-top']) { const d = el(id); if (d) d.hidden = !on; }
}

const STATE = { ok: 'ar.stOk', login: 'ar.stLogin', balance: 'ar.stBalance', rate_limit: 'ar.stRate' };
function btn(label, cls, fn) {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'aj-btn aj-btn--sm' + (cls ? ' ' + cls : ''); b.textContent = label;
  b.addEventListener('click', fn);
  return b;
}
function line(text, cls) { const p = document.createElement('p'); p.className = 'setnote' + (cls ? ' ' + cls : ''); p.textContent = text; return p; }

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
  if (h.state === 'balance') box.append(line(t('ar.balanceNote')));
  if (h.state === 'rate_limit') box.append(line(t('ar.rateNote')));
  if (h.token) box.append(line(t('ar.token').replace('{e}', h.token.expires).replace('{d}', String(h.token.days_left)), h.warn ? 'ardanger' : ''));
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
  return btn(t('ar.copy'), '', () => { navigator.clipboard?.writeText(text).then(() => say(t('ar.copied')), () => {}); });
}

function flowCard(f) {
  const box = el('ar-flow');
  box.replaceChildren();
  clearInterval(timer); timer = null;
  if (!f) { box.hidden = true; return; }
  box.hidden = false;
  const h = document.createElement('h3'); h.className = 'setg'; h.textContent = t('ar.flowTitle').replace('{h}', f.kind === 'codex' ? 'Codex' : 'Claude Code');
  box.append(h);
  const link = (u) => { const a = document.createElement('a'); a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.className = 'arlink'; a.textContent = t('ar.openLink'); return a; };
  if (f.stage === 'starting') box.append(line(t('ar.stStarting')));
  if (f.stage === 'device' && f.url && f.code) {
    box.append(line(t('ar.deviceStep1')));
    const p = document.createElement('p'); p.className = 'mailbtns'; p.append(link(f.url), copyBtn(f.url)); box.append(p);
    box.append(line(t('ar.deviceStep2')));
    const c = document.createElement('p'); c.className = 'arcode'; c.textContent = f.code; box.append(c);
    const q = document.createElement('p'); q.className = 'mailbtns'; q.append(copyBtn(f.code)); box.append(q);
    const left = line(''); box.append(left, line(t('ar.deviceNote')));
    const tick = () => { const s = Math.max(0, (f.expires_at || 0) - Math.floor(Date.now() / 1000)); left.textContent = t('ar.left').replace('{m}', String(Math.floor(s / 60))).replace('{s}', String(s % 60).padStart(2, '0')); };
    tick(); timer = setInterval(tick, 1000);
  }
  if (f.stage === 'url' && f.url) {
    box.append(line(t('ar.urlStep1')));
    const p = document.createElement('p'); p.className = 'mailbtns'; p.append(link(f.url), copyBtn(f.url)); box.append(p);
    box.append(line(t('ar.urlStep2')));
  }
  if (f.stage === 'paste') box.append(line(t('ar.pasteStep')));
  if (f.stage === 'verifying') box.append(line(t('ar.verifying')));
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

export function configure() { /* nothing to wire: buttons are built per render */ }
export function close() { clearInterval(timer); timer = null; }
