import { copyButton } from './copycode.js';
// P366 · Settings → Webhook (AGENTJ-HOOKS-SPEC §8.3): the computer's webhooks as a list the owner can read and steer, inside
// the paired session (`hook_list` → `hook_list_res`; `hook_set {id, op: pause | resume | revoke}` → the same fresh list;
// `hook_changed` from the host → re-read while the page is open, debounced). The host never sends a secret: a hook whose
// token sits in its URL arrives with the literal placeholder `/<令牌/token>` — 「复制地址」 then says where the full address is
// and copies nothing. There is no copy-secret action. Live hooks first (resident before the others), ended ones (24 h) greyed
// below, then 「最近投递」 (≤ 50). Everything is text through textContent; nothing is stored on the phone.
import { t, lang, locale, onLang } from './t.js';
import { mk, toast, confirmSheet } from './ui.js';
import { ask1, isReady, peer } from './session.js';
import { RelayMD } from './md.js';

export const DEBOUNCE_MS = 250;
export const TOKEN_TAIL = '/<令牌/token>';
const ID = /^[A-Za-z0-9_-]{22}$/;
const DID = /^d[0-9a-f]{12}$/;
const PRESETS = ['callback', 'stripe', 'github', 'hmac', 'bearer', 'alert', 'form', 'ping'];
const LIVES = ['once', 'ttl', 'resident'];
const AUTHS = ['stripe', 'github', 'hmac', 'bearer', 'token'];
const STATES = ['active', 'pending', 'used', 'revoked', 'expired'];
const RESULTS = ['ok', 'dup', 'signature', 'stale', 'ip', 'method', 'quota', 'seal', 'plain'];
const VERDICTS = ['continue', 'ask', 'spam'];
const DAY = 86400;

const str = (v, n) => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, n) : '');
const num = (v) => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null);
const one = (v, list) => (list.includes(v) ? v : null);

function target(v) {
  if (v === 'main' || v === 'owner' || v === 'wait') return { k: v, id: '' };
  const m = typeof v === 'string' && /^(order|bot):([\w.-]{1,64})$/.exec(v);
  return m ? { k: m[1], id: m[2] } : { k: 'main', id: '' };
}
function hook(h) {
  if (!h || typeof h !== 'object' || !ID.test(h.id || '')) return null;
  const last = h.last && typeof h.last === 'object' && num(h.last.at) !== null ? { at: h.last.at, result: one(h.last.result, RESULTS) || 'other' } : null;
  const url = typeof h.url === 'string' && /^https:\/\/[^\s]{1,600}$/.test(h.url) ? h.url : '';
  return {
    id: h.id, name: str(h.name, 80) || h.id.slice(0, 8), preset: one(h.preset, PRESETS) || 'hmac', life: one(h.life, LIVES) || 'once',
    expires: num(h.expires), target: target(h.target), auth: one(h.auth, AUTHS) || 'hmac', enabled: h.enabled !== false,
    state: one(h.state, STATES) || 'expired', ended: num(h.ended), last, count: Number.isInteger(h.count) && h.count >= 0 ? h.count : null,
    url, created: num(h.created && h.created.at) || 0, e2e: h.e2e === true,
  };
}
function delivery(d) {
  if (!d || typeof d !== 'object' || !DID.test(d.id || '') || num(d.at) === null) return null;
  const tr = d.triage && typeof d.triage === 'object' ? one(d.triage.v, VERDICTS) : null;
  return { id: d.id, hook: typeof d.hook === 'string' ? d.hook.slice(0, 22) : '', at: d.at, result: one(d.result, RESULTS) || 'other', triage: tr };
}

/** A `hook_list_res` → the page model, or null (pure; unit-tested). Anything malformed is dropped; no field but these is kept. */
export function parse(m) {
  if (!m || m.t !== 'hook_list_res' || m.ok !== true) return null;
  const hooks = (Array.isArray(m.hooks) ? m.hooks.slice(0, 40) : []).map(hook).filter(Boolean);
  const recent = (Array.isArray(m.recent) ? m.recent.slice(0, 50) : []).map(delivery).filter(Boolean).sort((a, b) => b.at - a.at);
  const q = m.quota && typeof m.quota === 'object' ? m.quota : {};
  return { hooks, recent, up: m.up === true, quota: { used: num(q.used) ?? 0, day_max: num(q.day_max) ?? 1000, hooks_max: num(q.hooks_max) ?? 20 } };
}

/** Live (active / pending; resident first, then newest) and ended within 24 h (newest end first). */
export function sortHooks(hooks, now = Date.now() / 1000) {
  const live = hooks.filter((h) => h.state === 'active' || h.state === 'pending')
    .sort((a, b) => (b.life === 'resident') - (a.life === 'resident') || b.created - a.created);
  const ended = hooks.filter((h) => h.state !== 'active' && h.state !== 'pending' && (h.ended === null || now - h.ended < DAY))
    .sort((a, b) => (b.ended || 0) - (a.ended || 0));
  return { live, ended };
}

/** What 「复制地址」 may put on the clipboard: the URL, never the token placeholder (→ null = 「完整地址在电脑上」). */
export function copyable(h) {
  if (!h || !h.url || h.auth === 'token' || h.url.endsWith(TOKEN_TAIL) || h.url.includes('<')) return null;
  return h.url;
}

export function when(sec) {
  const d = new Date(sec * 1000), now = new Date();
  const time = d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', hour12: false });
  if (d.toDateString() === now.toDateString()) return time;
  return d.toLocaleDateString(locale(), { month: 'numeric', day: 'numeric' }) + ' ' + time;
}
/** The row's plain-words lines (unit-tested): kind = 「预置 · 寿命 · 验证方式 → 送到哪」, then expiry, last delivery, count. */
export function lines(h) {
  const tg = t('p366.to.' + h.target.k, { id: h.target.id });
  const out = { kind: [t('p366.preset.' + h.preset), t('p366.life.' + h.life), t('p366.auth.' + h.auth)].join(' · ') + ' → ' + tg };
  if (h.life !== 'resident' && h.state === 'active') out.expiry = h.expires ? t('p366.expires', { t: when(h.expires) }) : h.life === 'once' ? t('p366.onceNote') : '';
  out.last = h.last ? t('p366.last', { t: when(h.last.at), r: t('p366.res.' + h.last.result) }) : t('p366.never');
  if (h.count !== null && (h.count > 0 || h.last)) out.count = t('p366.count', { n: h.count });   // 「还没收到过」 alone, not 「· 共 0 条」
  out.mode = t(h.e2e ? 'p366.e2e' : 'p366.plain');     // P366 add-on 3: say plainly whether our relay sees the body
  return out;
}
function chip(h) {
  if (h.state === 'pending') return ['pending', t('p366.st.pending')];
  if (h.state === 'active') return h.enabled ? null : ['paused', t('p366.st.paused')];
  return [h.state, t('p366.st.' + h.state)];
}

// ---------------------------------------------------------------- the page
let D = { ask: ask1, ready: isReady, supported: () => peer.p33 !== true || peer.hooks1 === true, confirm: confirmSheet,
  copy: (s) => RelayMD.copyText(s), toast };
/** Tests replace the session / dialog / clipboard seams; the page uses the real ones. */
export function configure(deps) { D = { ...D, ...deps }; }

let root = null, S = null, open = false, seq = 0, busy = false, timer = null, err = '';

/** settings.js: the sub-page opened (`box` = #hooks-box) → read the list. */
export function show(box) { root = box || root || globalThis.document?.getElementById('hooks-box') || null; open = true; return load(); }
export function close() { open = false; clearTimeout(timer); timer = null; }
export const isOpen = () => open;
/** app.js: `hook_changed` from the host → re-read once if the page is open (several changes in a burst = one read). */
export function changed() {
  if (!open) return;
  clearTimeout(timer);
  timer = setTimeout(() => { timer = null; if (open) load(); }, DEBOUNCE_MS);
}

function whyOf(m) {
  if (m.t === 'offline') return 'p366.offline';
  if (m.t === 'timeout') return 'p366.old';
  return 'p366.failed';
}
export async function load() {
  if (!root) return;
  const my = ++seq;
  if (!D.ready()) { err = 'p366.offline'; S = null; return draw(); }
  if (!D.supported()) { err = 'p366.old'; S = null; return draw(); }   // an older computer without `hooks1`: say so at once
  if (!S) { err = ''; draw(t('p366.loading')); }
  const m = await D.ask({ t: 'hook_list' }, 'hook_list_res', 15000);
  if (my !== seq || !open) return;
  take(m);
}
function take(m) {
  const s = parse(m);
  if (s) { S = s; err = ''; } else { err = whyOf(m); if (m.t !== 'hook_list_res') S = null; }
  draw();
}

async function setOp(h, op) {
  if (busy) return;
  if (!D.ready()) { D.toast(t('p366.offline'), 3000); return; }
  busy = true; draw();
  const my = ++seq;
  try {
    const m = await D.ask({ t: 'hook_set', id: h.id, op }, 'hook_list_res', 15000);
    if (m.t === 'hook_list_res' && m.ok === true) {
      if (my === seq) take(m);
      D.toast(t('p366.done.' + op, { name: h.name }), 2400, 'success');
    } else {
      D.toast(t(m.t === 'hook_list_res' ? 'p366.gone' : whyOf(m)), 4000);   // refused (`shape`: the hook is gone) → read again
      if (m.t === 'hook_list_res') { busy = false; return load(); }
    }
  } finally { busy = false; draw(); }
}
async function revoke(h) {
  const yes = await D.confirm(t('p366.revokeTitle', { name: h.name }), t('p366.revokeText'), t('p366.revoke'), true);
  if (yes) await setOp(h, 'revoke');
}
async function copyUrl(h, button) {
  const u = copyable(h);
  if (!u) { D.toast(t('p366.urlOnComputer'), 5000); return; }
  await copyButton(u, button, D.copy);
}

function btn(label, cls, fn) {
  const b = mk('button', 'aj-btn aj-btn--sm ' + cls, label); b.type = 'button'; b.disabled = busy;
  b.addEventListener('click', fn);
  return b;
}
function row(h, ended) {
  const li = mk('li', 'whrow' + (ended ? ' whrow--ended' : '')); li.dataset.id = h.id; li.dataset.state = h.state;
  if (h.state === 'active' && !h.enabled) li.dataset.paused = '1';
  const head = mk('div', 'whhead');
  head.append(mk('b', 'whname', h.name));
  const c = chip(h);
  if (c) { const s = mk('span', 'whchip whchip--' + c[0], c[1]); head.append(s); }
  li.append(head);
  const L = lines(h);
  li.append(mk('p', 'whline whkind', L.kind));
  const md = mk('p', 'whline whmode whmode--' + (h.e2e ? 'e2e' : 'plain'), L.mode); md.dataset.e2e = h.e2e ? '1' : '0'; li.append(md);
  if (L.expiry) li.append(mk('p', 'whline whexp', L.expiry));
  li.append(mk('p', 'whline whlast', L.last + (L.count ? ' · ' + L.count : '')));
  if (ended) return li;
  const acts = mk('div', 'whacts');
  if (h.state === 'active') {
    acts.append(btn(t(h.enabled ? 'p366.pause' : 'p366.resume'), 'whtoggle', () => setOp(h, h.enabled ? 'pause' : 'resume')));
    acts.append(btn(t('p366.copyUrl'), 'whcopy', e => copyUrl(h, e.currentTarget)));
  }
  acts.append(btn(t('p366.revoke'), 'aj-btn--danger whrevoke', () => revoke(h)));
  li.append(acts);
  return li;
}
function draw(status) {
  if (!root) return;
  const kids = [];
  if (status || err) { const p = mk('p', 'setnote whmsg', status || t(err)); p.setAttribute('role', 'status'); kids.push(p); }
  if (S) {
    const top = mk('p', 'whtop');
    top.append(mk('span', 'whused', t('p366.today', { n: S.quota.used, max: S.quota.day_max })));
    const up = mk('span', 'whup whup--' + (S.up ? 'on' : 'off'), t(S.up ? 'p366.up' : 'p366.down')); up.dataset.up = S.up ? '1' : '0';
    top.append(up);
    kids.push(top);
    const { live, ended } = sortHooks(S.hooks);
    if (!live.length && !ended.length) kids.push(mk('p', 'setnote whempty', t('p366.empty')));
    if (live.length) {
      kids.push(mk('h3', 'setg', t('p366.live')));
      const ul = mk('ul', 'whlist whlist--live'); for (const h of live) ul.append(row(h, false)); kids.push(ul);
    }
    if (ended.length) {
      kids.push(mk('h3', 'setg', t('p366.ended')));
      const ul = mk('ul', 'whlist whlist--ended'); for (const h of ended) ul.append(row(h, true)); kids.push(ul);
    }
    if (S.recent.length) {
      kids.push(mk('h3', 'setg', t('p366.recent')));
      const names = new Map(S.hooks.map((h) => [h.id, h.name]));
      const ul = mk('ul', 'whlist whrecent');
      for (const d of S.recent) {
        const li = mk('li', 'whdel'); li.dataset.id = d.id; li.dataset.result = d.result;
        li.append(mk('span', 'whdel-at', when(d.at)), mk('span', 'whdel-name', names.get(d.hook) || t('p366.goneHook')),
          mk('span', 'whdel-res whdel-res--' + (d.result === 'ok' ? 'ok' : 'no'), t('p366.res.' + d.result)));
        if (d.triage) li.append(mk('span', 'whdel-tri whdel-tri--' + d.triage, t('p366.tri.' + d.triage)));
        ul.append(li);
      }
      kids.push(ul);
    }
  }
  kids.push(mk('p', 'setnote whroutes', t('p366.routes')));
  kids.push(mk('p', 'setnote whsafe', t('p366.safe')));
  kids.push(mk('p', 'setnote whhonest', t('p366.honest')));
  root.replaceChildren(...kids);
}

onLang(() => { if (open && root) draw(); });
/** for tests: the current model and state (no secret is ever in it — the host sends none). */
export const _debug = () => ({ S, open, busy, err, lang: lang() });
