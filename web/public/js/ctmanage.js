// P207 §3 · Settings → 通讯录: the owner's address book as a page (it only lived inside the 「转发到邮箱」 dialog before).
// The owner (2026-10-13): 「联系人的这个管理界面还没做……只能输入我一个人的联系方式……没有我朋友的联系方式……邮件通道里边也没有
// 看到……只有白名单」 → list / search / add / edit / delete, import / export, and one tap 「加入邮件白名单」 per contact.
// Wire (PROTOCOL §25, approved devices only): ct_list / ct_save (P184) · ct_rm / ct_export / ct_import (P207) → ct_res;
// the allowlist add is a SIGNED phone control `mail_allow {addr, role}` (controls action, object = addr \n role) → ctl_res.
// Slot 0 (the owner) stays on its own card (P194 「我的联系方式」): tapping it opens that card; it cannot be deleted here.
// Names and addresses are textContent only; nothing is kept on the phone after the page closes.
import { t, onLang } from './t.js';
import { el, mk, toast, executionToast, confirmSheet } from './ui.js';
import { ask1, isReady } from './session.js';
import { registerLayer } from './layers.js';
import { signedWrite, whyText } from './controls.js';
import * as contacts from './contacts.js';

export const OWNER_ID = 'c000000000000';
const ADDR = /^[^\s@]{1,64}@[^\s@]{1,253}\.[^\s@]{2,}$/;
const IMPORT_MAX = 1024 * 1024;
const S = { list: [], q: '', allow: new Set(), mailKnown: false, editing: null, timer: 0, seq: 0, wired: false };
/** an envelope + a plus (not on the list yet) or + a tick (already on it): cloned from the <template>s next to the page markup */
function mailIcon(done) { return el(done ? 'ctm-ico-done' : 'ctm-ico-allow').content.firstElementChild.cloneNode(true); }

function say(s) { el('ctm-msg').textContent = s || ''; }
const first = (arr) => (Array.isArray(arr) && arr.length ? arr[0] : null);
const emailsOf = (c) => (Array.isArray(c.emails) ? c.emails.map((e) => (e && typeof e.v === 'string' ? e.v : '')).filter(Boolean) : []);
const phonesOf = (c) => (Array.isArray(c.phones) ? c.phones.map((e) => (e && typeof e.v === 'string' ? e.v : '')).filter(Boolean) : []);
const allowed = (addr) => S.allow.has(String(addr).toLowerCase());

async function load() {
  if (!isReady()) { say(t('ct.offline')); return; }
  const seq = ++S.seq;
  if (!S.list.length) say(t('ct.loading'));
  const m = await ask1({ t: 'ct_list', ...(S.q.trim() ? { q: S.q.trim() } : {}), limit: 500 }, 'ct_res', 15000);
  if (seq !== S.seq) return;                        // a newer search answered first
  if (m.t !== 'ct_res' || !m.ok) { say(m.t === 'timeout' ? t('ct.timeout') : t('ct.offline')); return; }
  S.list = (Array.isArray(m.contacts) ? m.contacts : []).filter((c) => c && typeof c.id === 'string');
  if (m.owner && typeof m.owner === 'object' && !S.q.trim() && !S.list.some((c) => c.id === OWNER_ID)) S.list.unshift({ ...m.owner, id: OWNER_ID });
  say('');
  render();
}
async function loadAllow() {                        // which addresses the e-mail channel already lets through (P170 mail_get)
  const m = await ask1({ t: 'mail_get' }, 'mail_res', 15000);
  if (m.t !== 'mail_res' || !m.view || !m.view.config) return;
  S.allow = new Set((m.view.config.allow || []).map((r) => String(r.addr || '').toLowerCase()).filter(Boolean));
  S.mailKnown = true;
  render();
}

function row(c) {
  const li = mk('li');
  li.dataset.id = c.id;
  const own = c.id === OWNER_ID;
  const main = mk('button', 'ctm-main'); main.type = 'button';
  const av = mk('i', 'ctm-av'); av.textContent = (c.letter || (c.name || '?').trim().charAt(0) || '?').toUpperCase().slice(0, 1);
  const txt = mk('span', 'ctm-txt');
  const nm = mk('b'); nm.textContent = (c.name || first(c.nicks) || first(emailsOf(c)) || first(phonesOf(c)) || '—') + (own ? ' · ' + t('p207set.ct.me') : '');
  const sub = mk('small'); sub.textContent = [...emailsOf(c), ...phonesOf(c)].slice(0, 2).join(' · ') || t('p207set.ct.noWay');
  txt.append(nm, sub);
  main.append(av, txt);
  main.setAttribute('aria-label', t(own ? 'set.owner' : 'p207set.ct.edit') + '：' + nm.textContent);
  main.addEventListener('click', () => (own ? contacts.openOwner() : edit(c)));
  li.append(main);
  const addr = first(emailsOf(c));
  if (addr) {
    const on = allowed(addr);
    const b = mk('button', 'menuicon ctm-allow'); b.type = 'button';
    b.append(mailIcon(on));
    const label = t(on ? 'p207set.ct.allowed' : 'p207set.ct.allow');
    b.setAttribute('aria-label', label + '：' + addr); b.title = label;
    b.dataset.on = on ? '1' : '0';
    b.disabled = on;
    b.addEventListener('click', () => allow(c, addr, own ? 'owner' : 'guest', b));
    li.append(b);
  }
  return li;
}
function render() {
  const list = el('ctm-list');
  if (!list) return;
  list.replaceChildren(...S.list.map(row));
  el('ctm-empty').hidden = S.list.length > 0 || !!S.q.trim();
  if (!S.list.length && S.q.trim()) say(t('ct.noHit', { q: S.q.trim().slice(0, 40) }));
}

async function allow(c, addr, role, btn) {
  if (!isReady()) { toast(t('ct.offline'), 2400); return; }
  btn.disabled = true;
  const res = await signedWrite('mail_allow', { addr, role }, { t: 'mail_allow', addr, role });
  if (res && res.ok) {
    S.allow.add(addr.toLowerCase());
    executionToast(t(res.already ? 'p207set.ct.allowAlready' : 'p207set.ct.allowDone', { a: addr }), 2600);
    render();
    return;
  }
  btn.disabled = false;
  toast(t('p207set.ct.allowFail', { why: whyText(res && res.why) }), 4000);
}

// ------------------------------------------------------------------ the edit dialog (global centred dialog)
function edit(c) {
  S.editing = c || null;
  el('ctmEditTitle').textContent = t(c ? 'p207set.ct.edit' : 'ct.add');
  el('ctm-e-name').value = c ? c.name || '' : '';
  el('ctm-e-nick').value = c && Array.isArray(c.nicks) ? c.nicks.join('、') : '';
  el('ctm-e-email').value = c ? first(emailsOf(c)) || '' : '';
  el('ctm-e-phone').value = c ? first(phonesOf(c)) || '' : '';
  el('ctm-e-err').textContent = '';
  el('ctm-e-del').hidden = !c || c.id === OWNER_ID;
  el('ctmEdit').hidden = false;
  el('ctm-e-name').focus({ preventScroll: true });
}
function closeEdit() {
  const d = el('ctmEdit');
  if (!d || d.hidden) return;
  d.hidden = true;
  const back = S.editing && el('ctm-list').querySelector(`li[data-id="${S.editing.id}"] .ctm-main`);
  S.editing = null;
  (back || el('ctm-add')).focus({ preventScroll: true });
}
async function saveEdit(e) {
  if (e) e.preventDefault();
  const c = S.editing;
  const name = el('ctm-e-name').value.trim(), email = el('ctm-e-email').value.trim(), phone = el('ctm-e-phone').value.trim();
  const nicks = el('ctm-e-nick').value.split(/[、,，;；]/).map((x) => x.trim()).filter(Boolean).slice(0, 8);
  const err = (k) => { el('ctm-e-err').textContent = t(k); };
  if (!name && !nicks.length) return err('ct.needName');
  if (!email && !phone) return err('ct.needOne');
  if (email && !ADDR.test(email)) return err('ct.badAddr');
  // edit: the first e-mail / phone is the one shown here; the others stay as they were
  const keep = (arr, v) => { const rest = (Array.isArray(arr) ? arr : []).slice(1); return v ? [{ v, label: (first(arr) || {}).label || '' }, ...rest] : rest; };
  const contact = { name, nicks, emails: keep(c && c.emails, email), phones: keep(c && c.phones, phone) };
  err('ct.saving');
  el('ctm-e-save').disabled = true;
  const m = await ask1({ t: 'ct_save', contact, ...(c ? { id: c.id } : {}) }, 'ct_res', 15000);
  el('ctm-e-save').disabled = false;
  if (m.t !== 'ct_res' || !m.ok) return err(m.t === 'timeout' ? 'ct.timeout' : 'ct.saveFail');
  closeEdit();
  toast(t('ct.saved'), 2200, 'success');
  load();
}
async function remove() {
  const c = S.editing;
  if (!c || c.id === OWNER_ID) return;
  const name = c.name || first(emailsOf(c)) || first(phonesOf(c)) || '';
  el('ctmEdit').hidden = true;                      // one centred card at a time: the confirmation takes its place
  const yes = await confirmSheet(t('p207set.ct.delTitle'), t('p207set.ct.delText', { name }), t('p207set.ct.delete'), true);
  if (!yes) { el('ctmEdit').hidden = false; el('ctm-e-del').focus({ preventScroll: true }); return; }
  el('ctmEdit').hidden = false;
  const m = await ask1({ t: 'ct_rm', id: c.id }, 'ct_res', 15000);
  if (m.t !== 'ct_res' || !m.ok) { el('ctm-e-err').textContent = m.t === 'timeout' ? t('ct.timeout') : t('ct.saveFail'); return; }
  closeEdit();
  toast(t('p207set.ct.deleted'), 2200);
  load();
}

// ------------------------------------------------------------------ import / export
async function exportAll() {
  if (!isReady()) { say(t('ct.offline')); return; }
  say(t('ct.loading'));
  const m = await ask1({ t: 'ct_export', fmt: 'vcf' }, 'ct_res', 20000);
  if (m.t !== 'ct_res' || !m.ok || typeof m.text !== 'string') { say(m.t === 'timeout' ? t('ct.timeout') : t('p207set.ct.exportFail')); return; }
  const url = URL.createObjectURL(new Blob([m.text], { type: 'text/vcard;charset=utf-8' }));
  const a = mk('a'); a.href = url; a.download = 'agentj-contacts.vcf'; a.hidden = true;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  say(t('p207set.ct.exported', { n: Number(m.count) || 0 }));
}
// A phone → computer message must fit one frame (PROTOCOL §10.0, ≈ 60 KiB of JSON), so a bigger file goes over in several
// ct_import calls, split between whole records (vCard: after END:VCARD; CSV: whole lines, the header repeated). Every call
// merges into the book, so the pieces add up to the same result as one call.
export const PIECE_BYTES = 36 * 1024;
const bytes = (x) => new TextEncoder().encode(x).length;
/** CSV records with their line ends, never cut inside a quoted field (an address or a note may hold a line break). */
export function csvRecords(text) {
  const out = []; let start = 0, q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') q = !q;                         // "" inside a quoted field toggles twice: still inside
    else if (c === '\n' && !q) { out.push(text.slice(start, i + 1)); start = i + 1; }
  }
  if (start < text.length) out.push(text.slice(start));
  return out;
}
export function pieces(text, name = '') {
  if (bytes(text) <= PIECE_BYTES) return [text];
  const pack = (items, head = '') => {
    const out = []; let cur = head;
    for (const it of items) {
      if (bytes(it) + bytes(head) > PIECE_BYTES) return null;        // one record alone is too big
      if (cur !== head && bytes(cur) + bytes(it) > PIECE_BYTES) { out.push(cur); cur = head; }
      cur += it;
    }
    if (cur !== head) out.push(cur);
    return out;
  };
  if (/BEGIN:VCARD/i.test(text)) return pack(text.split(/(?<=END:VCARD[ \t]*(?:\r?\n|$))/i).filter((x) => x.trim()));
  if (/\.csv$/i.test(name) || /^[^\n]*[,;\t][^\n]*\r?\n/.test(text)) {
    const lines = csvRecords(text);
    return pack(lines.slice(1).filter((x) => x.trim()), lines[0].endsWith('\n') ? lines[0] : lines[0] + '\n');
  }
  return null;                                                         // JSON and the rest: one piece or nothing
}
async function importFile(f) {
  if (!f) return;
  if (f.size > IMPORT_MAX) { say(t('p207set.ct.tooBig')); return; }
  if (!isReady()) { say(t('ct.offline')); return; }
  const text = await f.text();
  const parts = pieces(text, f.name || '');
  if (!parts) { say(t('p207set.ct.splitFail')); return; }
  const sum = { added: 0, merged: 0, skipped: 0 };
  for (let i = 0; i < parts.length; i++) {
    say(parts.length > 1 ? t('p207set.ct.importing', { i: i + 1, n: parts.length }) : t('ct.saving'));
    const m = await ask1({ t: 'ct_import', name: String(f.name || '').slice(0, 120), text: parts[i], fmt: 'auto' }, 'ct_res', 30000);
    if (m.t !== 'ct_res' || !m.ok) { say(m.t === 'timeout' ? t('ct.timeout') : t('p207set.ct.importFail')); if (i) load(); return; }
    for (const k of Object.keys(sum)) sum[k] += Number(m[k]) || 0;
    sum.skipped += k0(m.same);
  }
  say(t('p207set.ct.imported', { a: sum.added, m: sum.merged, s: sum.skipped }));
  load();
}
const k0 = (v) => Number(v) || 0;

// ------------------------------------------------------------------ open / wire
export function open() {
  wire();
  S.q = ''; el('ctm-q').value = '';
  say('');
  render();
  load();
  loadAllow();
}
export function close() { closeEdit(); }
export const available = () => contacts.forwardAvailable();

function wire() {
  if (S.wired) return;
  S.wired = true;
  el('ctm-q').addEventListener('input', () => {
    S.q = el('ctm-q').value;
    clearTimeout(S.timer);
    S.timer = setTimeout(load, 250);
  });
  el('ctm-add').addEventListener('click', () => edit(null));
  el('ctm-export').addEventListener('click', exportAll);
  el('ctm-import').addEventListener('click', () => el('ctm-file').click());
  el('ctm-file').addEventListener('change', () => { const f = el('ctm-file').files[0]; el('ctm-file').value = ''; importFile(f); });
  el('ctm-form').addEventListener('submit', saveEdit);
  el('ctm-e-cancel').addEventListener('click', closeEdit);
  el('ctm-e-del').addEventListener('click', remove);
  el('ctmEdit').addEventListener('click', (e) => { if (e.target === el('ctmEdit')) closeEdit(); });
  registerLayer({ element: () => el('ctmEdit'), open: () => !el('ctmEdit').hidden, close: closeEdit, fallback: () => el('ctm-add'), rank: 50 });
  onLang(() => { if (!el('set-contacts').hidden) render(); });
}

export const _debug = () => ({ list: S.list.length, q: S.q, allow: [...S.allow], editing: S.editing && S.editing.id });
