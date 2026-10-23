import { rotationWizard } from './security-rotation.js';
import { copyButton } from './copycode.js';
// P171 · 遥控器管理 (Settings → its own second-level page, PROTOCOL §24): the remotes paired with this computer — list, add
// (the computer makes a one-time pairing link + QR for THIS remote only; the new device shows a 6-digit safety code; the human
// types it here), rename and unbind. Every write is a signed phone control (controls.signedWrite); reads are plain requests.
// Labels and ids are host data: textContent only. A host without §24 (no `devs` answer) → read-only list + the commands.
import { t, fillText, onLang, locale } from './t.js';
import { el, mk, toast, confirmSheet } from './ui.js';
import { ask1, isReady } from './session.js';
import { signedWrite } from './controls.js';
import { RelayMD } from './md.js';
import { copyable } from './copycode.js';   // P292

let H = { myId: () => null, hostDevices: () => null, forget: () => {}, say: () => {} };
let snap = null;          // {limit, items:[{id,name,paired_at,seen,online,me,source}], can_add, busy} — null = not read yet
let legacy = false;       // the host never answered dev_list
let shown = false;
let add = null;           // {link, expires, timer, pending:{device,name,evict,replaces}|null, ended:bool}
let editing = null;       // device id whose rename field is open

const LIMIT_DEFAULT = 10;
const validLimit = (n) => Number.isInteger(n) && n > 0 && n <= 64 ? n : LIMIT_DEFAULT;
const QR_PREFIX = 'data:image/png;base64,';
const WHY = { disabled: 'rm.disabled', busy: 'rm.busy', offline: 'rm.offline', gone: 'rm.gone', code_mismatch: 'rm.mismatch',
  bad_name: 'rm.badName', timeout: 'rm.ended' };
const whyText = (w) => (WHY[w] ? t(WHY[w]) : t('rm.fail', { why: String(w ?? '?').slice(0, 40) }));

const clean = (s, n = 64) => String(s ?? '').replace(/[\u0000-\u001f\u007f]/g, '').slice(0, n);
function take(m) {
  const items = Array.isArray(m.items) ? m.items.slice(0, 64).filter((d) => d && typeof d.id === 'string') : [];
  const limit = validLimit(m.limit);
  return { limit, items, security_stage:m.security_stage, can_add: m.can_add !== false, busy: m.busy === true };
}
/** From the `preferences` host block (older hosts, or before the first dev_list answer). */
function fromPrefs() {
  const h = H.hostDevices();
  if (!h || !Array.isArray(h.devices)) return null;
  const me = H.myId();
  return { limit: validLimit(h.limit), items: h.devices.slice(0, 64).map((d) => ({ ...d, me: d.id === me })),
    can_add: false, busy: false };
}
/** Settings' row: 「已用 N / 10」 (null when nothing is known yet). */
export function count() {
  const s = snap || fromPrefs();
  return s ? { n: s.items.length, max: s.limit } : null;
}

// ---------------------------------------------------------------- open / refresh
export async function open(opts = {}) {
  shown = true;
  editing = null;
  if (!snap) snap = fromPrefs();
  render();
  await refresh();
  // P265: the same signed Add action; retain offline/limit/legacy/busy refusals and an existing QR.
  if (opts.add && shown && !add && !legacy && isReady() && !el('rm-add').disabled) { await startAdd(); return; }
  el('rm-add').focus({ preventScroll: true });
}
export function close() {
  shown = false;
  editing = null;
  if (add && !add.pending && !add.card) cancelAdd(true);   // P165: a device already waits → its card (on any remote) decides
}
/** P165: the P153 card for a new remote reached this remote while its add view is open (app.js closes Settings next). */
export function onPairCard() { if (add) add.card = true; }
export async function refresh() {
  if (!isReady()) { render(); return; }
  const m = await ask1({ t: 'dev_list' }, 'devs', 6000);
  if (m.t === 'devs') { legacy = false; snap = take(m); }
  else if (m.t === 'timeout') { legacy = true; snap = fromPrefs() || snap; }
  render();
}
/** app.js: an unsolicited `devs` (any remote changed the list). */
export function onDevs(m) {
  if (m.r) return;
  legacy = false;
  snap = take(m);
  if (shown) render();
  H.changed?.();
}

// ---------------------------------------------------------------- render
function fmt(sec) {
  if (!Number.isInteger(sec) || sec <= 0) return '';
  try { return new Date(sec * 1000).toLocaleString(locale(), { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
  catch { return ''; }
}
function render() {
  const s = snap || { limit: LIMIT_DEFAULT, items: [], can_add: false, busy: false };
  const n = s.items.length;
  el('rm-used').textContent = String(n);
  el('rm-limit').textContent = String(s.limit);
  el('rm-bar').style.width = Math.min(100, Math.round((n / s.limit) * 100)) + '%';
  el('rm-bar').parentElement.dataset.full = String(n >= s.limit);
  el('rm-full-note').textContent = s.security_stage==='verify'?t('p490.preserve'):t('rm.fullNote', { max: s.limit });
  el('rm-add').hidden = legacy;
  el('rm-add').disabled = !!add || !isReady();
  el('rm-legacy').hidden = !legacy;
  if (legacy) fillText(el('rm-legacy'), t('rm.legacy'));
  const list = el('rm-list');
  if (!snap) { list.replaceChildren(mk('li', 'rmempty', t('rm.loading'))); return; }
  if (!n) { list.replaceChildren(mk('li', 'rmempty', t('rm.empty'))); return; }
  // this device first, then the online ones, then by pairing time (newest first)
  const rows = s.items.slice().sort((a, b) => (b.me === true) - (a.me === true) || (b.online === true) - (a.online === true)
    || (b.paired_at || 0) - (a.paired_at || 0));
  list.replaceChildren(...rows.map(row));
  let safety=el('rm-security');
  if(!safety) {
    safety=mk('button','aj-btn');safety.id='rm-security';safety.type='button';
    safety.addEventListener('click',()=>rotationWizard());
    el('rm-list').before(safety);
  }
  safety.textContent=t('p490.title');
  renderAdd();
}
function row(d) {
  const li = mk('li', 'rmitem');
  li.dataset.id = d.id;
  if (d.me) li.dataset.me = '1';
  const head = mk('div', 'rmitem__head');
  const dot = mk('span', 'rmdot');
  dot.dataset.on = String(d.online === true);
  dot.title = t(d.online ? 'rm.online' : 'rm.offlineDot');
  const txt = mk('div', 'rmitem__txt');
  const name = mk('p', 'rmname');
  name.append(mk('span', 'rmname__t', clean(d.name) || '—'));
  if (d.me) name.append(mk('span', 'rmtag', t('rm.this')));
  // P179: who gave this name — the device itself when it paired, or the owner (the device's own label on hover / long tap)
  if (d.named_by === 'owner' || d.named_by === 'device') {
    const note = mk('small', 'name-note', t(d.named_by === 'owner' ? 'set.nameOwner' : 'set.nameDevice'));
    if (d.named_by === 'owner' && d.self_name) note.title = t('set.nameSelf', { name: clean(d.self_name) });
    name.append(' ', note);
  }
  const meta = mk('p', 'rmmeta');
  const src = { account: 'rm.srcAccount', phone: 'rm.srcPhone' }[d.source] || 'rm.srcLocal';
  const bits = [t(d.online ? 'rm.online' : 'rm.offlineDot'), fmt(d.paired_at) && t('rm.pairedAt', { d: fmt(d.paired_at) }),
    d.seen && !d.online && fmt(d.seen) && t('rm.seenAt', { d: fmt(d.seen) }), d.source && t(src)].filter(Boolean);
  meta.textContent = bits.join(' · ');
  const id = mk('p', 'rmid');
  id.append(mk('span', '', t('rm.idLabel') + ' '), copyable(mk('code', '', clean(d.id, 32))));   // P292: the ID can be copied
  txt.append(name, meta, id);
  head.append(dot, txt);
  li.append(head);
  if (legacy) return li;
  if (editing === d.id) {
    const f = mk('form', 'rmrename');
    const lab = mk('label', 'vh', t('rm.renameLabel'));
    const inp = mk('input');
    inp.id = 'rm-name-' + d.id.replace(/[^A-Za-z0-9_-]/g, '');
    lab.htmlFor = inp.id;
    inp.type = 'text'; inp.maxLength = 64; inp.value = clean(d.name); inp.autocomplete = 'off'; inp.enterKeyHint = 'done';
    const save = mk('button', 'aj-btn aj-btn--sm aj-btn--primary', t('rm.save'));
    save.type = 'submit';
    const cancel = mk('button', 'aj-btn aj-btn--sm', t('rm.cancel'));
    cancel.type = 'button';
    cancel.addEventListener('click', () => { editing = null; render(); });
    f.addEventListener('submit', (e) => { e.preventDefault(); rename(d, inp.value); });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); editing = null; render(); } });
    f.append(lab, inp, mk('div', 'setacts', ''));
    f.lastChild.append(cancel, save);
    li.append(f);
    queueMicrotask(() => { inp.focus(); inp.select(); });
    return li;
  }
  const acts = mk('div', 'setacts rmitem__acts');
  const ren = mk('button', 'aj-btn aj-btn--sm', t('rm.rename'));
  ren.type = 'button'; ren.dataset.act = 'rename';
  ren.addEventListener('click', () => { editing = d.id; render(); });
  const unb = mk('button', 'aj-btn aj-btn--sm aj-btn--danger', t('rm.unbind'));
  unb.type = 'button'; unb.dataset.act = 'unbind';
  unb.addEventListener('click', () => unbind(d));
  acts.append(ren, unb);
  li.append(acts);
  return li;
}

// ---------------------------------------------------------------- rename / unbind
async function rename(d, value) {
  const name = clean(value).trim();
  if (!name || name.length > 64) return toast(t('rm.badName'), 3000);
  if (name === clean(d.name)) { editing = null; return render(); }
  const res = await signedWrite('dev_name', { device: d.id, name }, { t: 'dev_name', device: d.id, name });
  if (!res.ok) return toast(whyText(res.why), 4000);
  editing = null;
  d.name = name;
  toast(t('rm.renamed'));
  render();
  refresh();
}
async function unbind(d) {
  const self = d.me === true;
  const nm = clean(d.name) || d.id;
  const ok = await confirmSheet(t(self ? 'rm.unbindSelfTitle' : 'rm.unbindTitle', { name: nm }),
    t(self ? 'rm.unbindSelfText' : 'rm.unbindText'), t('rm.unbind'), true);
  if (!ok) return;
  const res = await signedWrite('dev_unbind', { device: d.id }, { t: 'dev_unbind', device: d.id });
  if (!res.ok) return toast(whyText(res.why), 4000);
  if (self) { H.forget(); return; }
  if (snap) snap.items = snap.items.filter((x) => x.id !== d.id);
  toast(t('rm.unbound', { name: nm }), 3000);
  render();
  H.changed?.();
}

// ---------------------------------------------------------------- add
async function startAdd() {
  if (add) cancelAdd(true);
  el('rm-add').disabled = true;
  const res = await signedWrite('dev_add', {}, { t: 'dev_add' });
  if (!res.ok) { el('rm-add').disabled = false; return toast(whyText(res.why), 5000); }
  const qr = typeof res.qr === 'string' && res.qr.startsWith(QR_PREFIX) && res.qr.length < 200000 ? res.qr : '';
  const link = typeof res.link === 'string' && res.link.length < 2000 ? res.link : '';
  const exp = Number.isInteger(res.expires) ? res.expires : Math.floor(Date.now() / 1000) + 300;
  add = { link, expires: exp, pending: null, ended: false, timer: setInterval(tick, 1000) };
  el('rm-qr').src = qr;
  el('rm-addbox').hidden = false;
  el('rm-code').value = '';
  tick();
  renderAdd();
  el('rm-addbox').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  el('rm-copy').focus({ preventScroll: true });
}
function tick() {
  if (!add) return;
  const left = Math.max(0, add.expires - Math.floor(Date.now() / 1000));
  const mmss = Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0');
  if (add.ended) return;
  if (!left && !add.pending) { endAdd('expired'); return; }
  el('rm-expire').textContent = add.pending ? '' : t('rm.expires', { t: mmss });
}
function renderAdd() {
  const box = el('rm-addbox');
  if (!add) { box.hidden = true; return; }
  box.hidden = false;
  const p = add.pending;
  el('rm-pending').hidden = !p;
  el('rm-qr').parentElement.dataset.done = String(!!p || add.ended);
  el('rm-copy').hidden = !!p || add.ended || !add.link;
  el('rm-again').hidden = !add.ended;
  el('rm-cancel').textContent = t(add.ended ? 'rm.close' : 'rm.cancel');
  if (p) {
    el('rm-pend-name').textContent = t('rm.pending', { name: clean(p.name) || '—' });
    const ev = p.replaces ? t('rm.replaces', { name: clean(p.replaces.name) }) : p.evict ? t('rm.evict', { name: clean(p.evict.name), max: (snap && snap.limit) || LIMIT_DEFAULT }) : '';
    el('rm-evict').hidden = !ev;
    el('rm-evict').textContent = ev;
    el('rm-approve').disabled = !/^[0-9]{6}$/.test(el('rm-code').value);
  }
}
/** host → this remote: the new device reached its safety code. */
export function onPending(m) {
  if (!add || typeof m.device !== 'string') return;
  add.pending = { device: m.device, name: clean(m.name), evict: m.evict || null, replaces: m.replaces || null };
  el('rm-expire').textContent = '';
  renderAdd();
  if (shown) el('rm-code').focus();
}
/** host → this remote: its pairing ended (approved / denied / expired / replaced). */
export function onPairEnd(m) {
  if (!add) return;
  if (add.card) { cancelAdd(false); refresh().catch(() => {}); return; }   // P165: decided on a P153 card (any remote) → this page just resets
  if (m.ev === 'approved') return;        // dev_confirm's own answer says what happened
  endAdd(m.reason === 'code_mismatch' ? 'mismatch' : m.reason === 'cancelled' ? null : m.ev === 'expired' ? 'expired' : 'ended');
}
function endAdd(kind) {
  if (!add) return;
  if (!kind) { cancelAdd(false); return; }
  add.ended = true;
  add.pending = null;
  clearInterval(add.timer);
  el('rm-qr').removeAttribute('src');
  el('rm-expire').textContent = t('rm.' + kind);
  renderAdd();
  el('rm-add').disabled = false;
}
function cancelAdd(tellHost) {
  if (!add) return;
  clearInterval(add.timer);
  const was = add;
  add = null;
  el('rm-qr').removeAttribute('src');
  el('rm-code').value = '';
  renderAdd();
  if (tellHost && !was.ended && isReady()) ask1({ t: 'dev_cancel' }, 'ctl_res', 4000).catch(() => {});
  if (shown) el('rm-add').disabled = false;
}
async function confirmCode(approve) {
  if (!add || !add.pending) return;
  const p = add.pending;
  if (!approve) { cancelAdd(true); toast(t('rm.denied'), 3000); return; }
  const code = el('rm-code').value.trim();
  if (!/^[0-9]{6}$/.test(code)) return;
  el('rm-approve').disabled = true;
  const res = await signedWrite('dev_confirm', { device: p.device, code }, { t: 'dev_confirm', device: p.device, code });
  if (!res.ok) {
    if (res.why === 'code_mismatch' || res.why === 'gone' || res.why === 'timeout') endAdd(res.why === 'code_mismatch' ? 'mismatch' : 'ended');
    else { el('rm-approve').disabled = false; toast(whyText(res.why), 4000); }
    return;
  }
  clearInterval(add.timer);
  add = null;
  renderAdd();
  el('rm-add').disabled = false;
  toast(t('rm.added', { name: clean(res.name || p.name) }), 4000);
  refresh();
}

/** hooks: { myId(), hostDevices() → preferences.host, forget() (this remote unbound itself), changed() } */
export function configure(hooks) {
  H = { ...H, ...hooks };
  el('rm-add').addEventListener('click', startAdd);
  el('rm-again').addEventListener('click', startAdd);
  el('rm-cancel').addEventListener('click', () => cancelAdd(true));
  el('rm-copy').addEventListener('click', async () => {
    if (!add || !add.link) return;
    await copyButton(add.link, el('rm-copy'));
  });
  el('rm-code').addEventListener('input', () => {
    const v = el('rm-code').value.replace(/[^0-9]/g, '').slice(0, 6);
    if (v !== el('rm-code').value) el('rm-code').value = v;
    el('rm-approve').disabled = v.length !== 6;
  });
  el('rm-code').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !el('rm-approve').disabled) { e.preventDefault(); confirmCode(true); } });
  el('rm-approve').addEventListener('click', () => confirmCode(true));
  el('rm-deny').addEventListener('click', () => confirmCode(false));
  onLang(() => { if (shown) { render(); tick(); } });
}
// for tests
export const _debug = () => ({ snap, legacy, add: add && { link: !!add.link, pending: add.pending, ended: add.ended }, editing });
