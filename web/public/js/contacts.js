// P184 (PROTOCOL §25) · the owner's address book on the phone, in two P150 centred dialogs (gdlg):
//  1. 「转发到邮箱」 — a reply page or one of its files → the owner's own address (default) or people from the address book
//     (sorted by 最近 / 最常用 / 字母, searched on the computer by name, nickname, pinyin and address; 「新增」 = address +
//     nickname). Picking someone else unticks 「发给我自己」. Mail goes through the owner's own mailbox (P170 SMTP) to exactly
//     the addresses ticked here; with no mailbox set up the dialog leads to Settings → 邮件通道.
//  2. the contact card — the Agent asks to save a new contact (pre-filled with what the owner said) or to complete one; the
//     owner edits, then 保存 (≥ one e-mail or phone) or 不保存. Nothing is saved without that tap.
// P194: mode 'owner' = slot 0 of the address book, the owner themself (name, main e-mail, optional phone): raised once by the
// host after the first welcome, by the Agent (`agentj contacts owner set`), or from Settings → 我的联系方式 (`openOwner()`, a
// local card saved with `ct_save {id: OWNER_ID}`). 「跳过」 saves nothing.
// Entry for other cards (P183's file cards and the reply card): `openForward({turn, mid, name, whole})`, `forwardAvailable()`,
// (P189: `whole` = the reply card's 转发到邮箱 — the owner's message goes along unless 「不带我的提问」 is ticked)
// or any element with data-mailfwd (data-turn / data-mid / data-name). Names and addresses are always textContent.
import { t, onLang } from './t.js';
import { human, mk, toast } from './ui.js';
import { ask1, isReady, peer, sendApp } from './session.js';
import { registerLayer } from './layers.js';

const ADDR = /^[A-Za-z0-9._%+'=-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;
const MAX_TO = 10;
export const OWNER_ID = 'c000000000000';            // P194: slot 0 (host contacts.OWNER_ID)
const ICON = [['rect', { x: 3, y: 5, width: 18, height: 14, rx: 2.5 }], ['path', { d: 'm4 7 8 6 8-6' }]];
const PERSON = [['circle', { cx: 12, cy: 8, r: 4 }], ['path', { d: 'M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6' }]];

let openSettingsMail = null;
export function configure(hooks = {}) { openSettingsMail = hooks.openSettingsMail || null; }
export const forwardAvailable = () => !!(peer.p33 && peer.contacts);

// ------------------------------------------------------------------ shared dialog shell
function shell(id, rank, onClose) {
  const d = mk('div', 'gdlg ct-dlg'); d.id = id; d.hidden = true;
  d.setAttribute('role', 'dialog'); d.setAttribute('aria-modal', 'true'); d.setAttribute('aria-labelledby', id + '-title');
  const sq = mk('div', 'sq wide'); d.append(sq);
  d.addEventListener('click', (e) => { if (e.target === d) onClose(); });     // outside the square
  registerLayer({ element: () => d, open: () => !d.hidden, close: onClose, rank,
    fallback: () => document.getElementById('input') || document.getElementById('setBtn') });
  document.body.append(d);
  return { d, sq };
}
function icon(shapes) {
  const NS = 'http://www.w3.org/2000/svg', i = mk('div', 'gdlg-icon'), s = document.createElementNS(NS, 'svg');
  i.setAttribute('aria-hidden', 'true');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })) s.setAttribute(k, v);
  for (const [tag, attrs] of shapes) { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); s.append(e); }
  i.append(s);
  return i;
}

// ------------------------------------------------------------------ 1. forward to e-mail
const F = { root: null, what: null, list: [], me: [], mail: null, mode: 'me', picked: new Map(), sort: 'recent', q: '', busy: false, qTimer: 0, seq: 0,
  noInput: false,    // P189: a whole turn (the reply card's 转发到邮箱) may leave the owner's message out
  result: null };    // P295: what came back, shown in this same centred dialog (never a toast): {ok, to, m} | {dup, ago} | {why}

function fwdClose() { if (F.root) F.root.d.hidden = true; F.busy = false; clearTimeout(F.qTimer); }

function buildForward() {
  if (F.root) return F.root;
  F.root = shell('ctFwd', 47, fwdClose);   // P273: above the full-screen reader (45) it can be opened from
  const sq = F.root.sq;
  const h = mk('h2'); h.id = 'ctFwd-title';
  const sub = mk('p', 'gdlg-sub'); sub.id = 'ctFwd-what';
  const body = mk('div', 'ct-body'); body.id = 'ctFwd-body';
  const note = mk('p', 'ct-note'); note.id = 'ctFwd-note'; note.setAttribute('aria-live', 'polite');
  const row = mk('div', 'gdlg-row');
  const no = mk('button'); no.type = 'button'; no.id = 'ctFwd-cancel';
  const go = mk('button', 'go'); go.type = 'button'; go.id = 'ctFwd-send';
  row.append(no, go);
  sq.append(icon(ICON), h, sub, body, note, row);
  no.addEventListener('click', () => { if (!settling()) fwdClose(); });
  go.addEventListener('click', () => {
    if (settling()) return;
    const r = F.result;
    if (r && r.ok) fwdClose();
    else if (r) { F.result = null; send(!!r.dup); }   // 再发一次 / 重试 (after a timeout the host answers dup if it went)
    else if (F.mail && F.mail.ready === false) goSettings();
    else send();
  });
  onLang(() => { if (!F.root.d.hidden) renderForward(); });
  return F.root;
}

// P295: the tap that sent (or a double tap) must not land on the result's own buttons — 知道了 / 再发一次 sit where 发送 was
const settling = () => !!F.result && performance.now() - (F.result.at || 0) < 600;

function goSettings() { fwdClose(); if (openSettingsMail) openSettingsMail(); }

/** Open the dialog for a reply page (`turn`) or one file of it (`mid`, `name`). */
export async function openForward(what = {}) {
  if (!forwardAvailable()) { toast(t('ct.unsupported'), 2600); return false; }
  buildForward();
  F.what = { turn: Number.isInteger(what.turn) ? what.turn : null, mid: typeof what.mid === 'string' ? what.mid : null,
    name: typeof what.name === 'string' ? what.name.slice(0, 128) : '', whole: what.whole === true && Number.isInteger(what.turn) && typeof what.mid !== 'string' };
  F.mode = 'me'; F.picked = new Map(); F.q = ''; F.list = []; F.mail = null; F.me = []; F.noInput = false; F.result = null;
  F.root.d.hidden = false;
  renderForward();
  document.getElementById('ctFwd-cancel').focus({ preventScroll: true });
  await refresh();
  return true;
}

async function refresh() {
  if (!isReady()) { say(t('ct.offline')); return; }
  const seq = ++F.seq;
  const m = await ask1({ t: 'ct_list', sort: F.sort, ...(F.q.trim() ? { q: F.q.trim() } : {}) }, 'ct_res', 15000);
  if (seq !== F.seq || !F.root || F.root.d.hidden || F.result) return;
  if (m.t !== 'ct_res' || !m.ok) { say(m.t === 'timeout' ? t('ct.timeout') : t('ct.offline')); return; }
  F.list = Array.isArray(m.contacts) ? m.contacts : [];
  F.me = Array.isArray(m.me) ? m.me.filter((a) => typeof a === 'string') : [];
  F.mail = m.mail && typeof m.mail === 'object' ? m.mail : { ready: false };
  if (!F.me.length && F.mode === 'me') F.mode = 'other';
  renderForward();
}

function say(s) { const n = document.getElementById('ctFwd-note'); if (n) n.textContent = s || ''; }

function rows() {
  // one row per address (a contact with two e-mails offers both)
  const out = [];
  for (const c of F.list) for (const e of c.emails || []) out.push({ c, addr: e.v, label: e.label || '' });
  return out;
}

function renderForward() {
  if (F.result) { renderResult(); return; }
  const { sq } = F.root;
  sq.querySelector('#ctFwd-cancel').hidden = false; sq.querySelector('#ctFwd-what').hidden = false;
  sq.querySelector('#ctFwd-title').textContent = t('ct.fwdTitle');
  sq.querySelector('#ctFwd-what').textContent = F.what && F.what.mid ? t('ct.fwdFile', { name: F.what.name || t('ct.file') })
    : F.what && F.what.whole && !F.noInput ? t('ct.fwdTurn') : t('ct.fwdReply');
  const cancel = sq.querySelector('#ctFwd-cancel'), go = sq.querySelector('#ctFwd-send');
  cancel.textContent = t('ct.cancel');
  const body = sq.querySelector('#ctFwd-body');
  body.replaceChildren();
  if (!F.mail) {                             // the host has not answered yet: nothing to choose from
    body.append(mk('p', 'ct-setup ct-wait', t('ct.loading')));
    go.textContent = t('ct.send'); go.disabled = true;
    return;
  }
  if (F.mail.ready === false) {
    const p = mk('p', 'ct-setup', F.mail.why === 'no_password' ? t('ct.noKey') : t('ct.noMail'));
    body.append(p);
    go.textContent = t('ct.toSettings'); go.disabled = false;
    return;
  }
  // 发给我自己 / 发给别人
  const modes = mk('div', 'ct-modes'); modes.setAttribute('role', 'radiogroup'); modes.setAttribute('aria-label', t('ct.fwdTitle'));
  const opt = (mode, title, sub, disabled) => {
    const b = mk('button', 'ct-mode'); b.type = 'button'; b.setAttribute('role', 'radio');
    b.dataset.mode = mode; b.setAttribute('aria-checked', String(F.mode === mode)); b.disabled = !!disabled;
    const dot = mk('i'); dot.setAttribute('aria-hidden', 'true');
    const tx = mk('span'); tx.append(mk('b', '', title), mk('small', '', sub));
    b.append(dot, tx);
    b.addEventListener('click', () => { F.mode = mode; if (mode === 'me') F.picked.clear(); renderForward(); });
    return b;
  };
  modes.append(opt('me', t('ct.toMe'), F.me[0] || t('ct.noMe'), !F.me.length),
    opt('other', t('ct.toOther'), F.picked.size ? t('ct.picked', { n: F.picked.size }) : t('ct.toOtherSub')));
  body.append(modes);
  if (F.what && F.what.whole) body.append(inputToggle());
  if (F.mode === 'other') body.append(otherPanel());
  const n = F.mode === 'me' ? (F.me.length ? 1 : 0) : F.picked.size;
  go.textContent = F.busy ? t('ct.sending') : t('ct.send');
  go.disabled = F.busy || !n || !F.mail;
}

// P189: 「不带我的提问」 — unticked by default (the mail carries the owner's message above the reply, for context)
function inputToggle() {
  const b = mk('button', 'ct-person ct-noinput'); b.type = 'button'; b.id = 'ctFwd-noinput'; b.setAttribute('role', 'checkbox');
  b.setAttribute('aria-checked', String(F.noInput));
  const box = mk('i'); box.setAttribute('aria-hidden', 'true');
  const tx = mk('span'); tx.append(mk('b', '', t('ct.noInput')), mk('small', '', t('ct.noInputSub')));
  b.append(box, tx);
  b.addEventListener('click', () => { F.noInput = !F.noInput; renderForward(); document.getElementById('ctFwd-noinput')?.focus({ preventScroll: true }); });
  return b;
}

function otherPanel() {
  const box = mk('div', 'ct-other');
  const search = mk('input', 'ct-search'); search.id = 'ctFwd-q'; search.type = 'search';
  search.placeholder = t('ct.search'); search.setAttribute('aria-label', t('ct.search')); search.value = F.q;
  search.autocomplete = 'off'; search.setAttribute('autocapitalize', 'off'); search.spellcheck = false;
  search.addEventListener('input', () => { F.q = search.value; clearTimeout(F.qTimer); F.qTimer = setTimeout(refresh, 220); });
  const sorts = mk('div', 'ct-sorts'); sorts.setAttribute('role', 'tablist');
  for (const k of ['recent', 'frequent', 'name']) {
    const b = mk('button', '', t('ct.sort.' + k)); b.type = 'button'; b.dataset.sort = k; b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(F.sort === k)); b.disabled = !!F.q.trim();
    b.addEventListener('click', () => { F.sort = k; refresh(); });
    sorts.append(b);
  }
  const ul = mk('ul', 'ct-list'); ul.id = 'ctFwd-list';
  let lastLetter = '';
  const rs = rows();
  for (const r of rs) {
    if (F.sort === 'name' && !F.q.trim() && r.c.letter !== lastLetter) { lastLetter = r.c.letter; ul.append(mk('li', 'ct-letter', lastLetter)); }
    const li = mk('li');
    const b = mk('button', 'ct-person'); b.type = 'button'; b.setAttribute('role', 'checkbox');
    b.setAttribute('aria-checked', String(F.picked.has(r.addr))); b.dataset.addr = r.addr;
    const box2 = mk('i'); box2.setAttribute('aria-hidden', 'true');
    const tx = mk('span');
    const nm = mk('b', '', r.c.name || r.addr);
    if (r.c.nicks && r.c.nicks.length) nm.append(mk('em', '', ' · ' + r.c.nicks.slice(0, 2).join('、')));
    const meta = r.c.send_count ? t('ct.sentN', { n: r.c.send_count }) : '';
    tx.append(nm, mk('small', '', r.addr + (meta ? ' · ' + meta : '')));
    b.append(box2, tx);
    b.addEventListener('click', () => pick(r.addr, r.c.name || ''));
    li.append(b); ul.append(li);
  }
  if (!rs.length) ul.append(mk('li', 'ct-empty', F.q.trim() ? t('ct.noHit', { q: F.q.trim() }) : t('ct.empty')));
  const add = mk('details', 'ct-add'); add.id = 'ctFwd-add';
  const sm = mk('summary', '', t('ct.add'));
  const a = mk('input'); a.id = 'ctFwd-addr'; a.type = 'email'; a.inputMode = 'email'; a.placeholder = t('ct.addr'); a.setAttribute('aria-label', t('ct.addr'));
  a.autocomplete = 'off'; a.setAttribute('autocapitalize', 'off'); a.spellcheck = false;
  const nk = mk('input'); nk.id = 'ctFwd-nick'; nk.type = 'text'; nk.placeholder = t('ct.nick'); nk.setAttribute('aria-label', t('ct.nick'));
  if (ADDR.test(F.q.trim())) a.value = F.q.trim();
  else if (F.q.trim() && !rs.length) nk.value = F.q.trim();
  const ok = mk('button', 'ct-addgo', t('ct.addGo')); ok.type = 'button'; ok.id = 'ctFwd-addgo';
  ok.addEventListener('click', () => addNew(a.value.trim(), nk.value.trim()));
  for (const inp of [a, nk]) inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); ok.click(); } });
  add.append(sm, a, nk, ok);
  if (!rs.length && F.q.trim()) add.open = true;
  box.append(search, sorts, ul, add);
  return box;
}

function pick(addr, name) {
  if (F.picked.has(addr)) F.picked.delete(addr);
  else if (F.picked.size < MAX_TO) F.picked.set(addr, name);
  else { say(t('ct.max', { n: MAX_TO })); return; }
  F.mode = 'other';                          // someone else is picked: 「发给我自己」 is no longer ticked
  const keep = document.getElementById('ctFwd-list')?.scrollTop || 0;
  renderForward();
  const ul = document.getElementById('ctFwd-list'); if (ul) ul.scrollTop = keep;
  document.querySelector(`#ctFwd-list [data-addr="${CSS.escape(addr)}"]`)?.focus({ preventScroll: true });
}

async function addNew(addr, nick) {
  if (!ADDR.test(addr)) { say(t('ct.badAddr')); document.getElementById('ctFwd-addr')?.focus(); return; }
  say(t('ct.saving'));
  const m = await ask1({ t: 'ct_save', contact: { name: nick, nicks: nick ? [nick] : [], emails: [addr] } }, 'ct_res', 15000);
  if (m.t !== 'ct_res' || !m.ok) { say(m.t === 'timeout' ? t('ct.timeout') : t('ct.saveFail')); return; }
  say('');
  F.picked.set(addr.toLowerCase(), nick); F.mode = 'other'; F.q = '';
  await refresh();
}

async function send(again = false) {
  if (F.busy || !F.what) return;
  const to = F.mode === 'me' ? F.me.slice(0, 1).map((addr) => ({ addr })) : [...F.picked].map(([addr, name]) => ({ addr, ...(name ? { name } : {}) }));
  if (!to.length) return;
  F.busy = true; F.result = null; renderForward(); say('');
  const m = await ask1({ t: 'mail_fwd', to, ...(F.what.mid ? { mid: F.what.mid } : {}), ...(F.what.turn !== null ? { turn: F.what.turn } : {}),
    ...(F.what.whole && F.noInput ? { input: false } : {}), ...(again ? { again: true } : {}) }, 'mail_fwd_res', 90000);
  F.busy = false;
  if (!F.root || F.root.d.hidden) return;
  const why = m.t === 'mail_fwd_res' ? (m.ok ? null : m.why) : m.t;
  if (why === 'mail_not_configured' || why === 'no_password') { F.mail = { ready: false, why }; renderForward(); return; }
  F.result = !why ? { ok: true, to: to.map((x) => x.addr), me: F.mode === 'me', m } : why === 'dup' ? { dup: true, ago: m.ago | 0 } : { why };
  F.result.at = performance.now();
  renderForward();
  document.getElementById('ctFwd-send')?.focus({ preventScroll: true });
}

// P295 (Leo 10-15): the result in the same centred dialog — 已发到 + each address, every file left out with its real reason
// (size against the limit, a key-like file held back from others, moved / changed); failure = the reason + 重试; a second
// send of the same page to the same people within 10 min = 「刚刚已经发过了」 + 再发一次. ESC / outside close it (shell).
function renderResult() {
  const { sq } = F.root, r = F.result;
  const no = sq.querySelector('#ctFwd-cancel'), go = sq.querySelector('#ctFwd-send'), body = sq.querySelector('#ctFwd-body');
  const sub = sq.querySelector('#ctFwd-what');
  body.replaceChildren(); say('');
  go.disabled = false;
  if (r.ok) {
    const m = r.m || {};
    sq.querySelector('#ctFwd-title').textContent = t('p295.ct.okTitle');
    sub.textContent = t('p295.ct.okTo');
    const ul = mk('ul', 'ct-res'); ul.id = 'ctFwd-to';
    for (const a of r.to) ul.append(mk('li', '', a));
    body.append(ul);
    const notes = [];
    for (const x of Array.isArray(m.skips) ? m.skips : []) {
      if (!x || typeof x.name !== 'string') continue;
      const k = 'p295.ct.skip.' + (x.why === 'too_big' || x.why === 'secret' ? x.why : 'gone');
      notes.push(t(k, { name: x.name, size: human(x.bytes | 0).replace(' ', '\u00a0'), limit: human(m.limit | 0).replace(' ', '\u00a0') }));
    }
    if (!notes.length && m.skipped > 0) notes.push(t('ct.sentSkip', { n: m.skipped }));    // an older computer: count only
    if (m.refused > 0) notes.push(t('p295.ct.refused', { n: m.refused }));
    if (!r.me) notes.push(t('p295.ct.spam'));
    for (const n of notes) body.append(mk('p', 'gdlg-sub ct-res-note', n));
    no.hidden = true;
    go.textContent = t('p295.ct.ok');
    return;
  }
  no.hidden = false; no.textContent = t('p295.ct.close');
  if (r.dup) {
    sq.querySelector('#ctFwd-title').textContent = t('p295.ct.dupTitle');
    const ago = r.ago < 60 ? t('p295.ct.agoS', { n: Math.max(1, r.ago) }) : t('p295.ct.agoM', { n: Math.round(r.ago / 60) });
    sub.textContent = ''; sub.hidden = true;
    body.append(mk('p', 'gdlg-sub ct-res-note', t('p295.ct.dupSub', { ago })));
    go.textContent = t('p295.ct.again');
    return;
  }
  sq.querySelector('#ctFwd-title').textContent = t('p295.ct.failTitle');
  const k = 'ct.why.' + r.why, s = t(k);
  sub.textContent = ''; sub.hidden = true;
  body.append(mk('p', 'gdlg-sub ct-res-note', s === k ? t('ct.why.send_failed') : s));
  go.textContent = t('p295.ct.retry');
}

// data-mailfwd on any button (P183's file cards and reply card): the dialog opens for that page / file
document.addEventListener('click', (e) => {
  const b = e.target.closest?.('[data-mailfwd]');
  if (!b) return;
  e.preventDefault();
  const turn = b.dataset.turn !== undefined && b.dataset.turn !== '' ? Number(b.dataset.turn) : undefined;
  openForward({ turn: Number.isInteger(turn) ? turn : undefined, mid: b.dataset.mid || undefined, name: b.dataset.name || '' });
});

// ------------------------------------------------------------------ 2. the contact card
const K = { root: null, cards: new Map(), shown: null, err: '' };

function cardClose() { if (K.shown) answer(false); }

function buildCard() {
  if (K.root) return K.root;
  K.root = shell('ctCard', 54, cardClose);
  const sq = K.root.sq;
  const h = mk('h2'); h.id = 'ctCard-title';
  const sub = mk('p', 'gdlg-sub'); sub.id = 'ctCard-why';
  const form = mk('form', 'ct-form'); form.id = 'ctCard-form'; form.noValidate = true; form.autocomplete = 'off';
  const note = mk('p', 'ct-note'); note.id = 'ctCard-note'; note.setAttribute('aria-live', 'polite');
  const row = mk('div', 'gdlg-row');
  const no = mk('button'); no.type = 'button'; no.id = 'ctCard-no';
  const go = mk('button', 'go'); go.type = 'submit'; go.id = 'ctCard-save'; go.setAttribute('form', 'ctCard-form');
  row.append(no, go);
  sq.append(icon(PERSON), h, sub, form, note, row);
  no.addEventListener('click', () => answer(false));
  form.addEventListener('submit', (e) => { e.preventDefault(); answer(true); });
  form.addEventListener('input', () => { if (K.err) { K.err = ''; sq.querySelector('#ctCard-note').textContent = ''; } });
  onLang(() => { if (K.shown) renderCard(true); });
  return K.root;
}

const LIST_FIELDS = [['emails', 'email'], ['phones', 'tel'], ['addresses', 'text']];

function field(label, id, value, type = 'text', fresh = false) {
  const w = mk('label', 'ct-field' + (fresh ? ' fresh' : ''));
  w.append(mk('span', '', label));
  const i = mk(type === 'note' ? 'textarea' : 'input'); i.id = id; if (type !== 'note') i.type = type === 'email' ? 'email' : type === 'tel' ? 'tel' : 'text';
  if (type === 'email') { i.inputMode = 'email'; i.setAttribute('autocapitalize', 'off'); i.spellcheck = false; }
  if (type === 'tel') i.inputMode = 'tel';
  i.value = value || '';
  w.append(i);
  return w;
}

function merged(c) {
  // update mode: the existing contact plus what the Agent heard (new values marked fresh)
  const base = c.base || {}, add = c.contact || {};
  const out = { name: base.name || add.name || '', nicks: [...(base.nicks || [])], org: base.org || add.org || '', title: base.title || add.title || '',
    note: base.note || '', fresh: new Set() };
  for (const n of add.nicks || []) if (!out.nicks.includes(n)) { out.nicks.push(n); out.fresh.add('nicks'); }
  if (!base.name && add.name) out.fresh.add('name');
  if (!base.org && add.org) out.fresh.add('org');
  if (add.note && !(base.note || '').includes(add.note)) { out.note = (base.note ? base.note + '\n' : '') + add.note; out.fresh.add('note'); }
  for (const [k] of LIST_FIELDS) {
    const have = (base[k] || []).map((x) => ({ ...x }));
    for (const x of add[k] || []) if (!have.some((y) => y.v.toLowerCase() === x.v.toLowerCase())) { have.push({ ...x, fresh: true }); }
    out[k] = have;
  }
  return out;
}

function renderCard(keepValues) {
  const c = K.shown; if (!c) return;
  const { sq } = K.root;
  const name = (c.base && c.base.name) || (c.contact && c.contact.name) || '';
  const own = c.mode === 'owner';
  sq.querySelector('#ctCard-title').textContent = own ? t('ct.cardOwner') : c.mode === 'update' ? t('ct.cardUpdate', { name }) : t('ct.cardNew');
  sq.querySelector('#ctCard-why').textContent = own ? (c.local ? t('ct.ownerWhy') : c.why || t('ct.ownerWhy')) : c.why || '';
  sq.querySelector('#ctCard-why').classList.toggle('ct-wrap', own);
  sq.querySelector('#ctCard-no').textContent = t(own ? 'ct.cardSkip' : 'ct.cardNo');
  sq.querySelector('#ctCard-save').textContent = t('ct.cardSave');
  sq.querySelector('#ctCard-note').textContent = K.err;
  if (keepValues) { relabel(); return; }
  const v = c.mode === 'update' || (own && c.base) ? merged(c) : { ...(c.contact || {}), fresh: new Set() };
  const form = sq.querySelector('#ctCard-form');
  form.replaceChildren();
  if (own) {        // P194: slot 0 asks only for what "send it to me" needs — name, main e-mail (first), phone (optional)
    form.append(field(t('ct.f.name'), 'ct-f-name', v.name, 'text', v.fresh.has('name')));
    for (const [k, type] of LIST_FIELDS.slice(0, 2)) {
      const grp = mk('div', 'ct-multi'); grp.dataset.k = k;
      const items = (v[k] || []).length ? v[k] : [{ v: '' }];
      items.forEach((x, i) => grp.append(field(t(k === 'emails' ? (i ? 'ct.f.emails' : 'ct.f.mainEmail') : 'ct.f.phoneOpt') + (i ? ' ' + (i + 1) : ''),
        `ct-f-${k}-${i}`, x.v, type, !!x.fresh)));
      form.append(grp);
    }
    return;
  }
  form.append(field(t('ct.f.name'), 'ct-f-name', v.name, 'text', v.fresh.has('name')),
    field(t('ct.f.nick'), 'ct-f-nick', (v.nicks || []).join('、'), 'text', v.fresh.has('nicks')));
  for (const [k, type] of LIST_FIELDS) {
    const grp = mk('div', 'ct-multi'); grp.dataset.k = k;
    const items = (v[k] || []).length ? v[k] : [{ v: '' }];
    items.forEach((x, i) => grp.append(field(t('ct.f.' + k) + (items.length > 1 ? ' ' + (i + 1) : ''), `ct-f-${k}-${i}`, x.v, type, !!x.fresh)));
    const more = mk('button', 'ct-more', t('ct.more.' + k)); more.type = 'button';
    more.addEventListener('click', () => { const n = grp.querySelectorAll('input').length; grp.insertBefore(field(t('ct.f.' + k) + ' ' + (n + 1), `ct-f-${k}-${n}`, '', type), more); grp.querySelectorAll('input')[n].focus(); });
    grp.append(more);
    form.append(grp);
  }
  form.append(field(t('ct.f.org'), 'ct-f-org', v.org, 'text', v.fresh.has('org')), field(t('ct.f.note'), 'ct-f-note', v.note, 'note', v.fresh.has('note')));
}
function relabel() {
  const f = K.root.sq;
  const set = (id, k) => { const n = f.querySelector('#' + id)?.previousElementSibling; if (n) n.textContent = t(k); };
  set('ct-f-name', 'ct.f.name'); set('ct-f-nick', 'ct.f.nick'); set('ct-f-org', 'ct.f.org'); set('ct-f-note', 'ct.f.note');
}

function collect() {
  const f = K.root.sq, val = (id) => (f.querySelector('#' + id)?.value || '').trim();
  const list = (k) => [...f.querySelectorAll(`.ct-multi[data-k="${k}"] input`)].map((i) => i.value.trim()).filter(Boolean).map((v) => ({ v }));
  return { name: val('ct-f-name'), nicks: val('ct-f-nick').split(/[,，;；、]+/).map((x) => x.trim()).filter(Boolean),
    emails: list('emails'), phones: list('phones'), addresses: list('addresses'), org: val('ct-f-org'), note: val('ct-f-note') };
}

function showNext() {
  K.shown = [...K.cards.values()][0] || null;
  K.err = '';
  if (!K.shown) { if (K.root) K.root.d.hidden = true; return; }
  buildCard(); renderCard(false); K.root.d.hidden = false;
  (K.root.sq.querySelector('#ct-f-name')).focus({ preventScroll: true });
}

async function answer(ok) {
  const c = K.shown; if (!c) return;
  let contact;
  if (ok) {
    contact = collect();
    const bad = contact.emails.find((e) => !ADDR.test(e.v));
    if (bad) { K.err = t('ct.badAddr'); renderCard(true); return; }
    if (c.mode === 'owner' && !contact.emails.length) { K.err = t('ct.needEmail'); renderCard(true); return; }
    if (!contact.emails.length && !contact.phones.length) { K.err = t('ct.needOne'); renderCard(true); return; }
    if (c.mode !== 'owner' && !contact.name && !contact.nicks.length) { K.err = t('ct.needName'); renderCard(true); return; }
  }
  if (c.local) return ownerSave(c, ok ? contact : null);
  try { await sendApp({ t: 'ct_card_answer', id: c.id, n: c.n, ok, ...(ok ? { contact } : {}) }); }
  catch (_) { K.err = t('ct.offline'); renderCard(true); return; }
  if (!ok) { K.cards.delete(c.id); showNext(); return; }
  K.err = t('ct.saving'); renderCard(true);
}

/** host → `ct_card` (also re-sent after ready) */
export function card(m) {
  if (!m || typeof m.id !== 'string' || !/^[0-9a-f]{32}$/.test(m.id)) return;
  const known = K.cards.has(m.id);
  K.cards.set(m.id, { id: m.id, n: String(m.n || ''), mode: m.mode === 'update' || m.mode === 'owner' ? m.mode : 'new', contact: m.contact || {},
    base: m.base || null, why: typeof m.why === 'string' ? m.why : '' });
  if (!known && !K.shown) showNext();
}
/** P194: Settings → 我的联系方式 — slot 0 on a local card (read with ct_list, saved with ct_save {id: OWNER_ID}). */
export async function openOwner() {
  if (!forwardAvailable()) { toast(t('ct.unsupported'), 2600); return false; }
  if (!isReady()) { toast(t('ct.offline'), 2400); return false; }
  const m = await ask1({ t: 'ct_list', limit: 1 }, 'ct_res', 15000);
  if (m.t !== 'ct_res' || !m.ok) { toast(m.t === 'timeout' ? t('ct.timeout') : t('ct.offline'), 2400); return false; }
  const base = m.owner && typeof m.owner === 'object' ? m.owner : null;
  const me = Array.isArray(m.me) ? m.me.filter((a) => typeof a === 'string') : [];
  K.cards.set('local-owner', { id: 'local-owner', local: true, n: '', mode: 'owner', base,
    contact: base ? {} : { emails: me.slice(0, 1).map((v) => ({ v })) }, why: '' });
  if (!K.shown) showNext();
  return true;
}
async function ownerSave(c, contact) {
  if (!contact) { K.cards.delete(c.id); showNext(); return; }
  K.err = t('ct.saving'); renderCard(true);
  const m = await ask1({ t: 'ct_save', id: OWNER_ID, contact }, 'ct_res', 15000);
  if (m.t !== 'ct_res' || !m.ok) { K.err = m.t === 'timeout' ? t('ct.timeout') : m.why === 'no_email' ? t('ct.needEmail') : t('ct.saveFail'); renderCard(true); return; }
  K.cards.delete(c.id); showNext(); toast(t('ct.saved'), 2200);
}
export function cardDone(m) {
  if (!m || !K.cards.has(m.id)) return;
  K.cards.delete(m.id);
  if (K.shown && K.shown.id === m.id) {
    if (m.result === 'saved' || m.result === 'merged' || m.result === 'same') toast(t('ct.saved'), 2200);
    showNext();
  }
}
export function cardErr(m) {
  if (!m || !K.shown || m.id !== K.shown.id) return;
  if (m.why === 'gone') { K.cards.delete(m.id); showNext(); toast(t('ct.gone'), 2400); return; }
  K.err = m.why === 'no_contact' ? t('ct.needOne') : t('ct.saveFail'); renderCard(true);
}
const HOST_ID = /^[0-9a-f]{32}$/;                    // P203: only the computer's cards (never the local 0-slot card)
export const ids = () => [...K.cards.keys()].filter((id) => HOST_ID.test(id));
export function prune(gone) { for (const id of ids()) if (gone(id)) cardDone({ id, result: 'gone' }); }
export function clear({ keepForward = false } = {}) { K.cards.clear(); K.shown = null; if (K.root) K.root.d.hidden = true; if (!keepForward) fwdClose(); }

export const _debug = () => ({ F: { noInput: F.noInput, mode: F.mode, picked: [...F.picked.keys()], sort: F.sort, list: F.list.length, mail: F.mail, me: F.me },
  K: { open: K.cards.size, shown: K.shown && K.shown.id } });
window.AgentJMailForward = { open: openForward, available: forwardAvailable };
