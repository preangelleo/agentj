// #427 · Settings → 备用 AI 账号: each harness (Claude Code, Codex) keeps several named accounts on the host; one tap makes one
// current. Wire (reports/design/harness-multi-account/CONTRACT.md): acct_get / acct_use / acct_rename / acct_del → acct_res
// {r, ok, view, why?}; adding an account = the AI 登录救援 flow (`auth_start {kind, route, account:{name}}`) writing into a new
// slot — the flow card itself is drawn by authrescue.js into #acct-flow. The view carries names, methods and quota readings
// only (never a token or a path). Everything is textContent.
import { t } from './t.js';
import { resetCreditText } from './reset-credits.js';
import { el, mk, toast, notify } from './ui.js';
import { ask1, isReady } from './session.js';
import * as authrescue from './authrescue.js';
import { scrollToPkRow } from './pkassist.js';

/** The harnesses this page knows, in order: [view key, label, the login route a new account takes]. */
export const KINDS = [['claude', 'Claude Code', 'setup_token'], ['codex', 'Codex', 'device']];
export const NAME_MAX = 24, ITEMS_MAX = 8;
const LONG = 11 * 60 * 1000;   // auth_start answers after the passkey card (up to its 10 minutes)
const METHOD = { token: 'acct.mToken', login: 'acct.mLogin', device: 'acct.mDevice' };

let view = null;          // the host's last acct view
let form = null;          // {harness, id?} — the name field open for a new account (no id) or a rename
let adding = null;        // {harness, name, seen} — a login flow for a new account is running
let poll = null, loadId = 0, watching = false;
let addGeneration = 0;   // monotonic even after an attempt ends (adding returns to null)
let deleting = '';        // the id whose removal waits for the owner's answer on the host's confirmation card

const tw = (k, fb) => { const s = t(k); return s === k ? (fb || '') : s; };
function say(s, danger = false, why = '') {
  const m = el('acct-msg'); if (!m) return;
  m.textContent = s || ''; m.classList.toggle('ardanger', !!danger);
  if (why === 'passkey') {
    const actions = mk('span', 'mailbtns');
    actions.append(btn(t('p0300.pk.setup'), 'acct-pk-setup', () => scrollToPkRow()));
    m.append(actions);
  }
}
function whyText(why) { if (why === 'same_account') return t('acct.sameAccountHelp'); if (why === 'unregistered_login') return t('acct.loginMismatchHelp'); return tw('ar.why.' + why, tw('acct.why.' + why, t('acct.failed'))); }

/** Name rules shared with the host: trimmed, 1–24 characters, unique (case-insensitive) within the harness. → '' or an i18n key. */
export function nameProblem(name, items = [], exceptId = '') {
  const n = String(name || '').trim();
  if (!n) return 'acct.nameEmpty';
  if ([...n].length > NAME_MAX) return 'acct.nameLong';
  if (items.some((it) => it.id !== exceptId && String(it.name || '').trim().toLowerCase() === n.toLowerCase())) return 'acct.nameDup';
  return '';
}
/** `quota:{pct, window, at}` → one short line; anything missing → 「—」. Never estimated. */
export function quotaText(q, active) {
  const lead = t(active ? 'acct.quota' : 'acct.quotaLast');
  if (!q || typeof q.pct !== 'number' || !isFinite(q.pct)) return lead + ' —';
  const w = { '5h': 'acct.w5h', h5: 'acct.w5h', week: 'acct.wWeek', weekly: 'acct.wWeek', '7d': 'acct.wWeek' }[q.window];
  const parts = [w ? t(w) : (typeof q.window === 'string' && q.window.length <= 12 ? q.window : '')];
  if (typeof q.at === 'number' && q.at > 0) {
    const ms = q.at > 1e12 ? q.at : q.at * 1000;
    parts.push(new Date(ms).toLocaleString(document.documentElement.lang || undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }));
  }
  const tail = parts.filter(Boolean).join(' · ');
  return lead + ' ' + t('acct.used', { p: String(Math.round(Math.min(100, Math.max(0, q.pct)))) }) + (tail ? ' · ' + tail : '');
}

async function call(obj, ms = 15000) {
  if (!isReady()) { say(t('set.offline'), true); return null; }
  const owner = loadId;
  const m = await ask1(obj, 'acct_res', ms);
  if (owner !== loadId) return null;   // closing/reopening invalidates an older page response
  if (m.t !== 'acct_res') { say(m.t === 'timeout' ? t('acct.noAnswer') : t('set.offline'), true); return null; }
  if (m.view && typeof m.view === 'object') { view = m.view; render(); }
  if (!m.ok) say(whyText(m.why), true);
  return m;
}

export async function load() {
  stop(); const id = ++loadId; view = null; form = null;
  el('acct-list')?.replaceChildren(); el('acct-primary')?.replaceChildren(); say(t('ar.checking'));
  watchFlow();
  const m = await call({ t: 'acct_get' });
  if (id !== loadId) return;
  if (m && m.ok) say('');
  // Resume the account-owned flow; authrescue keeps rescue and account destinations separate.
  const attempt = adding, generation = addGeneration;   // a pre-add snapshot cannot finish a newer same-name attempt
  if (isReady()) ask1({ t: 'auth_get', check: false }, 'auth_res', 15000).then((r) => { if (id === loadId && generation === addGeneration && adding === attempt && r?.t === 'auth_res') authrescue.onPush(r); });
}
export function close() { ++loadId; stop(); form = null; }
/** Settings' Esc / Back (layers.js owns Esc in the capture phase): an open name field is put away first → true. */
export function escape() {
  if (!form) return false;
  const h = form.harness; form = null; render();
  el('acct-list')?.querySelector(`[data-kind="${h}"] .acctadd, [data-kind="${h}"] .acctren`)?.focus({ preventScroll: true });
  return true;
}
function stop() { clearInterval(poll); poll = null; }

function watchFlow() {
  if (watching) return; watching = true;
  authrescue.watch((v) => {
    if (!adding?.accepted) return;
    const f = v && v.flow;
    const account = typeof f?.account === 'string' ? f.account : f?.account?.name;
    const own = f?.kind === adding.harness && account === adding.name;
    if (own) adding.seen = true;
    const ended = own && adding.seen && (f.stage === 'done' || f.stage === 'failed');
    if (!ended) return;
    const ok = f.stage === 'done', name = adding.name;
    adding = null; stop(); render();
    notify({kind: ok ? 'success' : 'warn', shape:'rect', text: ok ? t('acct.added', {name}) : (f.why || t('ar.failed')), ms: authrescue.END_MS});
    if (ok) call({ t: 'acct_get' });   // Only this accepted account flow owns its one timed result notice.
  });
}

async function startAdd(harness, route, name) {
  form = null;
  if (!isReady()) { render(); say(t('set.offline'), true); return; }
  ++addGeneration;
  const attempt = adding = { harness, name, seen: false, accepted: false }; render();   // 添加账号 stays off while this login runs
  say(t('ar.confirm'));
  clearInterval(poll);
  poll = setInterval(() => {
    if (document.hidden || !isReady() || !attempt.accepted) return;
    ask1({ t: 'auth_get', check: false }, 'auth_res', 15000).then((r) => { if (adding === attempt && r?.t === 'auth_res') authrescue.onPush(r); });
  }, 5000);
  const m = await ask1({ t: 'auth_start', kind: harness, route, account: { name } }, 'auth_res', LONG);
  if (adding !== attempt) return;   // a terminal push or newer attempt already owns the result
  if (m.t !== 'auth_res') { adding = null; stop(); render(); say(m.t === 'timeout' ? t('ar.timeout') : t('set.offline'), true); return; }
  attempt.accepted = !!m.ok;
  if (m.view && typeof m.view === 'object') authrescue.onPush(m);
  if (!m.ok) { adding = null; stop(); render(); say(whyText(m.why), true, m.why); return; }
  if (adding === attempt) say(t('ar.started'));
}

async function use(harness, it) {
  say('');
  const m = await call({ t: 'acct_use', harness, id: it.id });
  if (m && m.ok) toast(t('acct.switched', { name: it.name }), 5000);
}
async function rename(harness, it, name) {
  const m = await call({ t: 'acct_rename', harness, id: it.id, name });
  if (m && m.ok) { form = null; say(''); render(); }
}
// CEO 11-03: the host's own confirmation card is the one confirmation (no sheet here); the page waits as long as that card can.
async function remove(harness, it) {
  if (deleting) return;
  deleting = it.id; render(); say(t('acct.delWait', { name: it.name }));
  const m = await call({ t: 'acct_del', harness, id: it.id }, LONG);
  deleting = ''; render();
  if (m && m.ok) { say(''); toast(t('acct.deleted', { name: it.name })); }
}
/** view.conflict, variable names only (anything else is dropped). */
function conflictNames() {
  return view && Array.isArray(view.conflict) ? view.conflict.filter((n) => typeof n === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(n)) : [];
}

function btn(label, cls, fn) {
  const b = document.createElement('button'); b.setAttribute('data-aj-owner', 'aj-action'); b.setAttribute('data-aj-action-context','dialog');
  b.type = 'button'; b.className = 'aj-action aj-action--sm' + (cls ? ' ' + cls : ''); b.textContent = label;
  b.addEventListener('click', fn);
  return b;
}
const note = (text, cls) => mk('p', 'setnote' + (cls ? ' ' + cls : ''), text);

/** The inline name field (a new account, or a rename of `it`). Enter = go on, Esc = put it away. */
function nameForm(harness, label, route, items, it) {
  const box = mk('div', 'acctform');
  const id = 'acct-name-' + harness + (it ? '-' + it.id : '');
  const lab = mk('label', 'setlab', t(it ? 'acct.renameLab' : 'p0269w4.nameLab')); lab.htmlFor = id;   // #452: a name for the NEW account
  const input = document.createElement('input'); input.setAttribute('data-aj-owner', 'aj-account');
  input.id = id; input.type = 'text'; input.className = 'aj-input'; input.autocomplete = 'off'; input.spellcheck = false;
  input.maxLength = NAME_MAX * 2; input.value = it ? it.name : spareName(items);   // 0.26.9 #452: 「备用 1」 ready to go
  input.placeholder = t(harness === 'claude' ? 'acct.namePhClaude' : 'acct.namePh');
  const err = mk('p', 'setnote ardanger acctform__err'); err.setAttribute('role', 'alert'); err.hidden = true;
  const go = () => {
    const n = input.value.trim(), k = nameProblem(n, items, it ? it.id : '');
    if (k) { err.textContent = t(k, { n: String(NAME_MAX) }); err.hidden = false; input.focus(); return; }
    if (it) rename(harness, it, n); else startAdd(harness, route, n);
  };
  const cancel = () => { form = null; render(); };
  input.addEventListener('input', () => { err.hidden = true; });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); go(); } });   // Esc = escape() via the Settings layer
  const p = mk('p', 'mailbtns');
  p.append(btn(it ? t('acct.save') : t('acct.next', { h: label }), 'aj-action--primary', go), btn(t('ar.cancel'), '', cancel));
  box.append(lab, input, err);
  if (!it) box.append(note(t(harness === 'claude' ? 'acct.addNoteClaude' : 'acct.addNoteCodex')), note(t('acct.noOverwrite')));
  box.append(p);
  queueMicrotask(() => input.focus({ preventScroll: true }));
  return box;
}

/** 0.26.9 #452: the first free 「备用 N」 / "Backup N" (never the name of an account already saved). */
export function spareName(items = []) {
  const taken = new Set(items.map((it) => String(it.name || '').trim().toLowerCase()));
  for (let n = 1; n <= ITEMS_MAX + 1; n++) { const name = t('p0269w4.spare', { n: String(n) }); if (!taken.has(name.toLowerCase())) return name; }
  return '';
}
/** 0.26.9 #452: nothing saved yet — the login in use is listed at once as 「当前」 (view.in_use: method + quota, read-only). */
function inUseRow(use) {
  const li = mk('li', 'acctrow acctinuse'); li.dataset.active = '1';
  const head = mk('div', 'arhead');
  head.append(mk('b', 'acctname', t('p0269w4.inUseName')), mk('span', 'archip acctcur', t('acct.current')));
  li.append(head, note(t(METHOD[use.method] || 'acct.mOther') + ' · ' + quotaText(use.quota, true), 'acctmeta'), note(t('p0269w4.inUseNote'), 'acctinusenote'));
  return li;
}
function itemRow(harness, label, route, h, it) {
  const active = it.id === h.active;
  const li = mk('li', 'acctrow'); li.dataset.id = it.id; if (active) li.dataset.active = '1';
  const head = mk('div', 'arhead');
  head.append(mk('b', 'acctname', it.name));
  if (active) head.append(mk('span', 'archip acctcur', t('acct.current')));
  li.append(head);
  li.append(note(t(METHOD[it.method] || 'acct.mOther') + ' · ' + quotaText(it.quota, active), 'acctmeta'));
  if (harness === 'codex') li.append(note(resetCreditText(it.reset_credits ?? it.quota?.reset_credits), 'acctcredits'));
  if (form && form.harness === harness && form.id === it.id) { li.append(nameForm(harness, label, route, h.items, it)); return li; }
  const p = mk('p', 'mailbtns');
  // CEO 11-03: an ANTHROPIC_* variable overrides Claude's account → 设为当前 greyed, the reason (names only) right below it
  const blocked = harness === 'claude' && !active ? conflictNames() : [];
  if (!active) {
    const u = btn(t('acct.use'), 'aj-action--primary acctuse', () => use(harness, it));
    if (blocked.length) { u.disabled = true; u.setAttribute('aria-describedby', 'acct-cf-' + it.id); }
    p.append(u);
  }
  p.append(btn(t('acct.rename'), 'acctren', () => { form = { harness, id: it.id }; say(''); render(); }));
  const del = btn(t('acct.del'), 'aj-action--danger acctdel', () => remove(harness, it));
  if (active) { del.disabled = true; del.setAttribute('aria-describedby', 'acct-delnote-' + harness); }
  else if (deleting) del.disabled = true;
  p.append(del);
  li.append(p);
  if (blocked.length) { const n = note(t('acct.conflict', { n: blocked.join(', ') }), 'ardanger acctconflict'); n.id = 'acct-cf-' + it.id; li.append(n); }
  if (active && h.items.length > 1) { const n = note(t('acct.delActive'), 'acctdelnote'); n.id = 'acct-delnote-' + harness; li.append(n); }
  else if (active) { const n = note(t('acct.delOnly'), 'acctdelnote'); n.id = 'acct-delnote-' + harness; li.append(n); }
  return li;
}

function harnessCard([harness, label, route]) {
  const h = view[harness];
  const items = Array.isArray(h.items) ? h.items : [];
  const box = mk('div', 'setrow arrow acctgrp'); box.dataset.kind = harness;
  const head = mk('div', 'arhead'); head.append(mk('b', '', label));
  box.append(head);
  if (!items.length && !view.primary && h.in_use && typeof h.in_use === 'object') { const ul = mk('ul', 'acctlist'); ul.append(inUseRow(h.in_use)); box.append(ul); }
  else if (!items.length) box.append(note(t('acct.none')));
  else {
    const ul = mk('ul', 'acctlist');
    for (const it of items) ul.append(itemRow(harness, label, route, { ...h, items }, it));
    box.append(ul);
  }
  if (form && form.harness === harness && !form.id) box.append(nameForm(harness, label, route, items));
  else {
    const p = mk('p', 'mailbtns');
    const add = btn(t(items.length || h.in_use || view.primary?.[harness]?.state === 'in' ? 'p0269w4.addAnother' : 'acct.add'), 'acctadd', () => { form = { harness }; say(''); render(); });   // #452: another account, not a re-login
    if (items.length >= ITEMS_MAX || adding) add.disabled = true;
    p.append(add); box.append(p);
    if (items.length >= ITEMS_MAX) box.append(note(t('acct.full', { n: String(ITEMS_MAX) })));
  }
  return box;
}

function renderPrimary() {
  const box = el('acct-primary'); if (!box) return;
  box.replaceChildren(mk('b', '', t('acct.primary')), note(t('acct.primaryNote')));
  for (const [kind, label] of KINDS) {
    const r = view?.primary?.[kind];
    const row = mk('div', 'setrow acctprimary'); row.dataset.kind = kind;
    row.append(mk('b', '', label));
    if (!r || !['in', 'out'].includes(r.state)) row.append(note(t('acct.primaryUnknown')));
    else {
      row.append(note(t(r.state === 'in' ? 'p352.signedIn' : 'p352.signedOut')));
      if (['login', 'sub', 'key', 'cloud', 'helper', 'none'].includes(r.use)) {
        row.append(note(t('p362.use.' + r.use, { name: /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(r.use_name || '') ? r.use_name : '' })));
      }
      if (r.pending === true) row.append(note(t('p362.pending')));
    }
    box.append(row);
  }
  const p = mk('p', 'mailbtns');
  p.append(btn(t('acct.primaryEdit'), 'acctprimaryedit', () => el('open-models')?.click()));
  box.append(p, mk('b', '', t('acct.backups')), note(t('acct.preserved')));
}

function render() {
  const list = el('acct-list');
  if (!list || !view) return;
  renderPrimary();
  const out = [];
  for (const k of KINDS) if (view[k[0]] && typeof view[k[0]] === 'object') out.push(harnessCard(k));
  if (!out.length) out.push(note(t('acct.noHarness')));
  list.replaceChildren(...out);
}
