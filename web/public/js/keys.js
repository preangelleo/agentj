// P352 「模型和 API Key」 (owner: the models page becomes the key manager). The host sends only NAMES and states of the keys
// in its key file (`keys`): one dashed card per base-model vendor (catalog on the host, agentj/keys.py) and one list of the
// owner's other keys. Row buttons are icons only:
//   · set row   — 拷贝 · 修改 · 删除      · empty row — 填写      · sign-in rows (Claude / Codex login) — state only
//   · 拷贝 / 删除 need Face ID first: WebAuthn get() over elevateChallenge(channel, device, page id, "key_"+op, nonce,
//     keysDigest(op, NAME)), started synchronously inside the tap (iOS gesture rule); the host verifies it, then answers
//     the copy with the value to this session only. The value goes straight into the clipboard — a ClipboardItem whose
//     text/plain is a Promise (Safari wants the write CALLED in the tap); where that is refused, the value waits in a closure
//     for ≤ 30 s behind one more tap 「再点一次拷贝」, and is dropped on pagehide / hidden. Never in the DOM, storage or logs.
//   · 修改 / 填写 (and 「添加」 for a new NAME) ask the host for the EXISTING encrypted key card (keys_set): the value is typed
//     on that card, whose 「同意」 asks for Face ID itself — the Agent never sees it.
//   · no passkey on this phone (`fa` absent): 拷贝 / 删除 say so and offer 「设置 Face ID」 (F20), never a fallback.
import { channel } from './session.js';
import * as api from './api.js';
import { t, lang, onLang } from './t.js';
import { mk, toast, toastAction, notify, confirmSheet, toastOff } from './ui.js';
import { SVG } from './icons.js';
import { registerLayer } from './layers.js';
import { elevateChallenge, approve, canAssert, keysDigest } from './faceid.js';

export const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const NAME_MAX = 64;
export const HOLD_MS = 30000;                     // a value the clipboard refused waits this long for 「再点一次拷贝」
const HEX32 = /^[0-9a-f]{32}$/;
const TYPES = new Set(['oauth', 'sub', 'key']);

// line icons, 24-unit grid, stroke 1.9, round caps (the reply card's / icons.js style)
export const KEY_GLYPH = {
  copy: [['rect', { x: 8.5, y: 8.5, width: 11, height: 11, rx: 2 }], ['path', { d: 'M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2' }]],
  edit: [['path', { d: 'M4.5 19.5l1-4.2L15.8 5a2 2 0 0 1 2.8 0l.4.4a2 2 0 0 1 0 2.8L8.7 18.5z' }], ['path', { d: 'M13.8 7l3.2 3.2' }]],
  delete: [['path', { d: 'M4.5 7h15' }], ['path', { d: 'M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2' }], ['path', { d: 'M6.5 7l.9 11.6a1.6 1.6 0 0 0 1.6 1.4h6a1.6 1.6 0 0 0 1.6-1.4L17.5 7' }], ['path', { d: 'M10.3 11v5M13.7 11v5' }]],
  add: [['path', { d: 'M12 5v14M5 12h14' }]],
};
export function keyGlyph(name) {
  const s = document.createElementNS(SVG, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.9', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: 'kico' })) s.setAttribute(k, v);
  for (const [tag, at] of KEY_GLYPH[name]) {
    const e = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(at)) e.setAttribute(k, String(v));
    s.appendChild(e);
  }
  return s;
}

const str = (v, n) => (typeof v === 'string' && v.length > 0 && v.length <= n ? v : null);
const okName = (v) => typeof v === 'string' && v.length <= NAME_MAX && NAME_RE.test(v);
const label = (nm) => (nm && typeof nm === 'object' ? (lang() === 'en' ? nm.en || nm.zh : nm.zh || nm.en) : null);
function row(r) {
  if (!r || !TYPES.has(r.type) || !okName(r.name)) return null;
  if (r.type === 'oauth') return { type: 'oauth', name: r.name, state: r.state === 'in' ? 'in' : 'out' };
  const tail = typeof r.tail === 'string' && /^[\x21-\x7e]{1,4}$/.test(r.tail) ? r.tail : null;
  return { type: r.type, name: r.name, set: r.set === true, tail: r.set === true ? tail : null, in_use: r.in_use === true };
}
/** A `keys` message → the page model, or null (pure; unit-tested). Anything malformed is dropped, never shown. */
export function parse(m) {
  if (!m || m.t !== 'keys' || !HEX32.test(m.id || '') || !HEX32.test(m.n || '')) return null;
  const base = [];
  for (const v of Array.isArray(m.base) ? m.base.slice(0, 40) : []) {
    if (!v || !str(v.id, 40) || !v.name || typeof v.name !== 'object') continue;
    const name = { zh: str(v.name.zh, 80), en: str(v.name.en, 80) };
    if (!name.zh && !name.en) continue;
    const rows = (Array.isArray(v.rows) ? v.rows.slice(0, 12) : []).map(row).filter(Boolean);
    if (rows.length) base.push({ id: v.id, name, rows });
  }
  const third = (Array.isArray(m.third) ? m.third.slice(0, 200) : []).map((r) => row({ ...r, type: 'key', set: true })).filter(Boolean);
  const fa = typeof m.fa === 'string' && m.fa.length > 0 && m.fa.length <= 1400 ? m.fa : null;
  return { id: m.id, n: m.n, fa, base, third };
}

let S = null;                       // the page model (names / states only) + the armed challenges
let root = null, hooks = {}, busy = false, seq = 0, held = null, addForm = null;
const chs = new Map();              // `${op}\n${name}` → Face ID challenge for the current nonce

/** app.js: {setupFaceId()} — the F20 「用 Face ID 记住这台手机？」 offer (same hook as the secret pickup card). */
export function configure(h) { hooks = h || {}; }

/** Precompute every set row's copy / delete challenge for nonce `n`, so a tap goes straight to get(). */
async function arm(n) {
  chs.clear();
  if (!S || !S.fa || !n) return;
  S.n = n;
  const dev = await api.myDeviceId(), ch = channel(), id = S.id;
  for (const r of [...S.base.flatMap((v) => v.rows), ...S.third]) {
    if (r.type === 'oauth' || !r.set) continue;
    for (const op of ['copy', 'delete']) {
      const c = await elevateChallenge(ch, dev, id, 'key_' + op, n, await keysDigest(op, r.name));
      if (S && S.n === n && S.id === id) chs.set(op + '\n' + r.name, c);
    }
  }
}
function rearm(n) { if (S && HEX32.test(n || '')) arm(n).catch(() => chs.clear()); }

/** Open / refresh: ask the host for the list and draw it into `box` (the models page's #keys-box). */
export async function load(box) {
  root = box || root;
  if (!root) return;
  const my = ++seq;
  status(t('p352.loading'));
  const m = await api.keysGet();
  if (my !== seq) return;
  S = parse(m);
  chs.clear();
  if (!S) { root.replaceChildren(); status(t('p352.unavailable')); return; }
  status('');
  draw();
  rearm(S.n);
}
function status(text) {
  let p = root && root.parentElement && root.parentElement.querySelector('#keys-status');
  if (!p && root) { p = mk('p', 'keys-status'); p.id = 'keys-status'; p.setAttribute('role', 'status'); root.after(p); }
  if (p) { p.textContent = text; p.hidden = !text; }
}

function iconBtn(op, r, vendor) {
  const b = mk('button', 'kbtn kbtn--' + op); b.type = 'button';
  const lab = t('p352.' + op);
  b.setAttribute('aria-label', lab + ' ' + r.name); b.title = lab; b.dataset.op = op; b.dataset.name = r.name;
  b.appendChild(keyGlyph(op === 'set' ? 'add' : op));
  b.addEventListener('click', () => act(op, r, vendor));
  return b;
}
/** NAME with a break chance after every underscore (long names wrap at word joints, not mid-word). */
function nameNode(name) {
  const c = mk('span', 'kname');   // a span, not <code>: the row's own 拷贝 copies the VALUE; a ⧉ on the name would read as the same thing
  name.split('_').forEach((p, i, a) => { c.append(p + (i < a.length - 1 ? '_' : '')); if (i < a.length - 1) c.append(document.createElement('wbr')); });
  return c;
}
function rowNode(r, vendor) {
  const li = mk('li', 'krow'); li.dataset.name = r.name; li.dataset.type = r.type;
  const main = mk('div', 'kmain');
  const top = mk('div', 'ktop');
  if (vendor !== null) top.append(mk('span', 'ktag ktag--' + r.type, t('p352.tag.' + r.type)));   // third-party rows: no tag
  if (r.type === 'oauth') top.append(mk('span', 'kname kname--login', ['claude', 'codex'].includes(r.name) ? t('p352.login.' + r.name) : r.name));
  else top.append(nameNode(r.name));
  main.append(top);
  const st = mk('div', 'kstate');
  if (r.type === 'oauth') { st.textContent = t(r.state === 'in' ? 'p352.signedIn' : 'p352.signedOut'); st.dataset.on = r.state === 'in' ? '1' : '0'; }
  else if (r.set) { st.textContent = '••••••••' + (r.tail || ''); st.dataset.on = '1'; }
  else { st.textContent = t('p352.notSet'); st.dataset.on = '0'; }
  if (r.in_use) st.append(mk('span', 'kuse', t('p352.inUse')));
  main.append(st);
  li.append(main);
  if (r.type !== 'oauth') {
    const acts = mk('div', 'kacts');
    if (r.set) acts.append(iconBtn('copy', r, vendor), iconBtn('edit', r, vendor), iconBtn('delete', r, vendor));
    else acts.append(iconBtn('set', r, vendor));
    li.append(acts);
  }
  return li;
}
function draw() {
  if (!root || !S) return;
  const kids = [];
  kids.push(mk('h2', 'keys-h', t('p352.base')));
  for (const v of S.base) {
    const card = mk('section', 'kvendor'); card.dataset.vendor = v.id;
    card.append(mk('h3', 'kvendor-h', label(v.name)));
    const ul = mk('ul', 'klist');
    for (const r of v.rows) ul.append(rowNode(r, label(v.name)));
    card.append(ul); kids.push(card);
  }
  const head = mk('div', 'keys-h keys-h--third');
  head.append(mk('h2', '', t('p352.third')));
  const add = mk('button', 'kbtn kbtn--add'); add.type = 'button'; add.id = 'keys-add';
  add.setAttribute('aria-label', t('p352.addKey')); add.title = t('p352.addKey');
  add.append(keyGlyph('add')); add.addEventListener('click', () => openAdd());
  head.append(add); kids.push(head);
  const card = mk('section', 'kvendor kvendor--third');
  card.append(buildAdd());
  const ul = mk('ul', 'klist');
  for (const r of S.third) ul.append(rowNode(r, null));
  if (!S.third.length) card.append(mk('p', 'kempty', t('p352.thirdEmpty')));
  card.append(ul); kids.push(card);
  root.replaceChildren(...kids);
}

// ---------------------------------------------------------------- 「添加」: a NAME, then the normal key card
function buildAdd() {
  const f = mk('form', 'kadd'); f.hidden = !addForm?.open; f.noValidate = true;
  const lab = mk('label', 'kadd-l', t('p352.addName'));
  const inp = mk('input', 'kadd-i'); inp.id = 'keys-add-name'; inp.autocomplete = 'off'; inp.spellcheck = false; inp.maxLength = NAME_MAX;
  inp.setAttribute('autocapitalize', 'characters'); inp.placeholder = 'ELEVENLABS_API_KEY'; lab.htmlFor = inp.id;
  if (addForm?.value) inp.value = addForm.value;
  const go = mk('button', 'aj-btn aj-btn--primary kadd-go', t('p352.addGo')); go.type = 'submit';
  const err = mk('p', 'kadd-err'); err.setAttribute('role', 'status'); err.hidden = true;
  const line = mk('div', 'kadd-row'); line.append(inp, go);
  f.append(lab, line, err);
  inp.addEventListener('input', () => { if (addForm) addForm.value = inp.value; err.hidden = true; });
  f.addEventListener('submit', (e) => {
    e.preventDefault();
    const name = inp.value.trim();
    if (!okName(name)) { err.textContent = t('p352.badName'); err.hidden = false; inp.focus(); return; }
    closeAdd();
    act('set', { type: 'key', name, set: false }, null);
  });
  return f;
}
function openAdd() {
  addForm = { open: true, value: addForm?.value || '' };
  const f = root?.querySelector('.kadd');
  if (f) { f.hidden = false; f.querySelector('input').focus(); }
}
function closeAdd() {
  addForm = null;
  const f = root?.querySelector('.kadd');
  if (f) { f.hidden = true; f.querySelector('input').value = ''; f.querySelector('.kadd-err').hidden = true; }
}
const addOpen = () => !!root && !!root.querySelector('.kadd:not([hidden])') && !root.closest('[hidden]');
registerLayer({ element: () => root?.querySelector('.kadd'), open: addOpen, close: () => { closeAdd(); document.getElementById('keys-add')?.focus(); }, rank: 2, trap: false });

// ---------------------------------------------------------------- the row actions
function noPasskey() {
  toastAction(t('p352.needFaceId'), t('sout.setup'), () => { if (hooks.setupFaceId) hooks.setupFaceId(); else toast(t('sout.noFaceId'), 4000); }, 8000);
}
const why = (w) => ({ busy: 'p352.cardBusy', no_passkey: 'p352.needFaceId', passkey: 'p352.faRefused', gone: 'p352.gone', missing: 'p352.gone', offline: 'p352.offline', timeout: 'p352.offline' }[w] || 'models.failed');

function act(op, r, vendor) {
  if (op === 'edit' || op === 'set') return requestCard(r.name);
  if (busy) return toast(t('p352.busy'));
  if (!S || !S.fa || !canAssert()) return noPasskey();
  const ch = chs.get(op + '\n' + r.name);
  if (!ch) return toast(t('p352.busy'));
  if (op === 'copy') return copy(r, ch);
  return remove(r, vendor, ch);
}

async function requestCard(name) {
  const res = await api.keysSet(name);
  toast(t(res.ok ? 'models.card' : why(res.why)), 4200);
  if (res.n) rearm(res.n);
}

/** One operation's round trip after Face ID: → the host's keys_res (with a fresh nonce re-armed). Not async before fa. */
async function run(op, name, faP) {
  const id = S.id, n = S.n;
  busy = true;
  try {
    let fa;
    try { fa = await faP; } catch (e) { return { ok: false, why: e && e.name === 'NotAllowedError' ? 'cancel' : 'fa_error' }; }   // the owner closed the sheet = quiet
    chs.clear();                                       // this nonce is spent the moment it is sent
    const res = await api.keysOp(op, name, id, n, fa);
    if (res.n) rearm(res.n);
    return res;
  } finally { busy = false; }
}

/** 拷贝: approve() and clipboard.write() both start in this tap. */
function copy(r, ch) {
  let give, drop;
  const value = new Promise((ok, no) => { give = ok; drop = no; });
  const blob = value.then((v) => new Blob([v], { type: 'text/plain' }));
  blob.catch(() => {});
  const faP = approve(ch, S.fa);
  let wrote = null;
  const nav = globalThis.navigator, CI = globalThis.ClipboardItem;
  if (nav?.clipboard?.write && typeof CI === 'function') {
    try { wrote = nav.clipboard.write([new CI({ 'text/plain': blob })]).then(() => true, () => false); } catch { wrote = null; }
  }
  run('copy', r.name, faP).then(async (res) => {
    if (!res.ok || typeof res.value !== 'string') {
      drop(new Error('no value'));
      if (res.why !== 'cancel') toast(t(why(res.why)), 4200);
      if (res.why === 'gone' || res.why === 'missing') load();
      return;
    }
    let v = res.value;
    res.value = null;
    give(v);
    let ok = wrote ? await wrote : false;
    if (!ok && !wrote && nav?.clipboard?.writeText) ok = await nav.clipboard.writeText(v).then(() => true, () => false);
    if (ok) { v = null; toast(t('p352.copied'), 2400, 'success'); return; }
    hold(v); v = null;
  });
}
/** The clipboard refused (no gesture left): one more tap copies it; gone after 30 s / pagehide / hidden. */
function hold(v) {
  wipe();
  held = { v, timer: setTimeout(wipe, HOLD_MS) };
  notify({ kind: 'confirm', text: t('p352.copyAgain'), actions: [{ label: t('p352.copyAgainBtn'), fn: copyHeld }], ms: HOLD_MS });
}
function copyHeld() {
  if (!held) return toast(t('p352.copyExpired'), 3000);
  const v = held.v;
  wipe();
  navigator.clipboard.writeText(v).then(() => toast(t('p352.copied'), 2400, 'success'), () => toast(t('p352.copyFailed'), 4200));
}
export function wipe() { if (held) { clearTimeout(held.timer); held.v = null; held = null; } }
export const holding = () => !!held;
if (typeof document !== 'undefined') {
  addEventListener('pagehide', () => { if (held) { wipe(); toastOff(); } });
  document.addEventListener('visibilitychange', () => { if (document.hidden && held) { wipe(); toastOff(); } });
}

/** 删除: a confirmation naming the key (stronger when it is the one in use); Face ID starts in the 「删除」 tap itself. */
async function remove(r, vendor, ch) {
  const yes = document.getElementById('confirm-yes');
  let faP = null;
  const pre = () => { faP = approve(ch, S.fa); faP.catch(() => {}); };
  yes.addEventListener('click', pre);
  const sep = lang() === 'en' ? ' ' : '';             // the in-use warning leads: it is the one thing that breaks at once
  const text = (r.in_use ? t('p352.delInUse') + sep : '') + t(vendor ? 'p352.delText' : 'p352.delTextThird', { name: r.name, vendor: vendor || '' });
  let ok;
  try { ok = await confirmSheet(t('p352.delTitle', { name: r.name }), text, t('p352.delete'), true); }
  finally { yes.removeEventListener('click', pre); }
  if (!ok || !faP) return;
  const res = await run('delete', r.name, faP);
  if (res.ok) { toast(t('p352.deleted', { name: r.name }), 3000, 'success'); return load(); }
  if (res.why !== 'cancel') toast(t(why(res.why)), 4200);
  if (res.why === 'gone' || res.why === 'missing') load();
}

onLang(() => { if (S) draw(); });
