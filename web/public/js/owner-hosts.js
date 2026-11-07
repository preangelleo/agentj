// feedback400: a capability-gated settings component. Discovery never grants trust.
import { t, onLang } from './t.js';
import { signedWriteFa, whyText } from './controls.js';
import { request, done, isReady } from './session.js';
import { copyable } from './copycode.js';

let state = null, capable = false, busy = false, editingAlias = null;
const $ = id => document.getElementById(id);
const keys = ['alias', 'hostname', 'user', 'port', 'public_key', 'fingerprint', 'peer', 'expires'];
export function targetFor(st, rows) {
  return { op: 'replace', scope: st.scope, rev: st.rev, rows };
}
function node(tag, text, cls) {
  const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls.replace(/\baj-btn(?=\b|--)/g,'aj-action'); const action=tag==='button'; n.setAttribute('data-aj-owner',action?'aj-action':tag==='input'?'aj-settings-control':'aj-settings'); if(action)n.setAttribute('data-aj-action-context','dialog'); return n;
}
function field(k) { return $('owner-hosts-' + k); }
export function capability(host) {
  capable = host?.owner_hosts === 1;
  state = null;
  render();
  if (capable) refresh();
}
function refresh() {
  if (!capable || !isReady()) return;
  const r = request({ t: 'owner_hosts_get' }, m => {
    if (m.t === 'owner_hosts_state' || m.t === 'offline') { done(r); if (m.t === 'owner_hosts_state') onState(m); }
  });
}
export function onState(m) {
  if (!capable || !m || !Array.isArray(m.rows) || !Array.isArray(m.candidates) || typeof m.rev !== 'string') return;
  state = m; render();
}
function fill(row = {}) {
  editingAlias = state?.rows.some(r => r.alias === row.alias) ? row.alias : null;
  for (const k of keys) field(k).value = row[k] ?? (k === 'port' ? 22 : k === 'expires' ? Math.floor(Date.now() / 1000) + 30 * 86400 : '');
  $('owner-hosts-form').hidden = false;
  field('alias').focus();
}
async function write(rows) {
  if (!state || busy || !isReady()) return;
  busy = true; render();
  try {
    const target = targetFor(state, rows);
    const { faceIdStep } = await import('./settings.js');
    const res = await signedWriteFa('owner_hosts_set', target, { t: 'owner_hosts_set', target },
      r => faceIdStep(r, { title: 'ownerHosts.title', text: 'ownerHosts.confirm', yes: 'ownerHosts.confirmYes' }));
    $('owner-hosts-message').textContent = res.ok ? t('set.saved') : whyText(res.why);
    if (res.ok) $('owner-hosts-form').hidden = true;
    refresh();
  } finally { busy = false; render(); }
}
export function render() {
  const root = $('owner-hosts'); if (!root) return;
  root.hidden = !capable;
  if (!capable) return;
  root.querySelector('[data-title]').textContent = t('ownerHosts.title');
  root.querySelector('[data-note]').textContent = t('ownerHosts.note');
  $('owner-hosts-add').textContent = t('ownerHosts.add');
  $('owner-hosts-save').textContent = t('ownerHosts.save');
  $('owner-hosts-refresh').textContent = t('ownerHosts.refresh');
  for (const k of keys) field(k).previousElementSibling.textContent = t('ownerHosts.' + k);
  const list = $('owner-hosts-list'); list.replaceChildren();
  for (const row of state?.rows || []) {
    const item = node('div', '', 'owner-hosts__item');
    item.append(node('p', `${row.alias} · ${row.user}@${row.hostname}:${row.port} · ${t('ownerHosts.' + row.status)}`));
    item.append(node('p', row.fingerprint, 'setnote'));
    if (row.status === 'active') for (const key of ['ssh', 'scp']) {
      const code = node('code', row[key]); item.append(copyable(code, { label: 'set.copy' }));
    }
    const edit = node('button', t('ownerHosts.edit'), 'aj-btn'); edit.type = 'button'; edit.disabled = busy;
    edit.addEventListener('click', () => fill(row));
    const revoke = node('button', t('ownerHosts.revoke'), 'aj-btn'); revoke.type = 'button'; revoke.disabled = busy;
    revoke.addEventListener('click', () => { write(cleanRows().filter(x => x.alias !== row.alias)).catch(showError); });
    item.append(edit, revoke); list.append(item);
  }
  if (state && !state.valid && state.rev) list.append(node('p', t('ownerHosts.invalid'), 'setnote'));
  for (const row of state?.candidates || []) {
    const b = node('button', `${row.alias || row.peer} · ${t('ownerHosts.pending')}`, 'aj-btn'); b.type = 'button'; b.disabled = busy;
    b.addEventListener('click', () => fill(row)); list.append(b);
  }
  for (const id of ['owner-hosts-save', 'owner-hosts-add']) $(id).disabled = busy || !state;
}
function cleanRows() {
  return (state?.rows || []).map(row => Object.fromEntries([...keys, 'owner'].map(k => [k, row[k]])));
}
function showError() { $('owner-hosts-message').textContent = t('ownerHosts.failed'); }
export function init() {
  const anchor = $('set-budget-row'); if (!anchor) return;
  const root = node('section', '', 'owner-hosts'); root.id = 'owner-hosts'; root.hidden = true;
  const title = node('h3', ''); title.dataset.title = '';
  const note = node('p', '', 'setnote'); note.dataset.note = '';
  const list = node('div', ''); list.id = 'owner-hosts-list';
  const add = node('button', '', 'aj-btn'); add.id = 'owner-hosts-add'; add.type = 'button'; add.addEventListener('click', () => fill());
  const reload = node('button', '', 'aj-btn'); reload.id = 'owner-hosts-refresh'; reload.type = 'button'; reload.addEventListener('click', refresh);
  const form = node('form', ''); form.id = 'owner-hosts-form'; form.hidden = true;
  for (const k of keys) {
    const label = node('label', ''), caption = node('span', '');
    const input = node('input', '', 'aj-input'); input.id = 'owner-hosts-' + k;
    input.type = ['port', 'expires'].includes(k) ? 'number' : 'text'; input.required = k !== 'peer';
    input.maxLength = 256; if (k === 'port') { input.min = '1'; input.max = '65535'; }
    label.append(caption, input); form.append(label);
  }
  const save = node('button', '', 'aj-btn aj-btn--primary'); save.id = 'owner-hosts-save'; save.type = 'submit'; form.append(save);
  form.addEventListener('submit', e => {
    e.preventDefault();
    const row = Object.fromEntries(keys.map(k => [k, ['port', 'expires'].includes(k) ? Number(field(k).value) : field(k).value.trim()]));
    row.owner = 'self';
    write([...cleanRows().filter(x => x.alias !== row.alias && x.alias !== editingAlias), row]).catch(showError);
  });
  const message = node('p', '', 'setnote'); message.id = 'owner-hosts-message'; message.setAttribute('role', 'status');
  root.append(title, note, list, add, reload, form, message); anchor.before(root); onLang(render);
}
