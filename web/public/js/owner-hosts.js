// feedback400: a capability-gated settings component. Discovery never grants trust.
import { t, onLang } from './t.js';
import { signedWriteFa, whyText } from './controls.js';
import { request, done, isReady } from './session.js';
import { copyable } from './copycode.js';
import {groupedRows,batchRows,validDraft,reviewRows,cleanRow,OWNER_HOSTS_MAX} from './owner-hosts-view.js';

let state = null, capable = false, busy = false, editingAlias = null, drafts = [];
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
  state = null; drafts = []; editingAlias=null;
  const form=$('owner-hosts-form');if(form)form.hidden=true;
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
  if(state&&JSON.stringify(state.scope)!==JSON.stringify(m.scope)){drafts=[];editingAlias=null;$('owner-hosts-form').hidden=true;}
  state = m; render();
}
function fill(row = {}) {
  editingAlias = state?.rows.some(r => r.alias === row.alias) ? row.alias : null;
  for (const k of keys) field(k).value = row[k] ?? (k === 'port' ? 22 : k === 'expires' ? Math.floor(Date.now() / 1000) + 30 * 86400 : '');
  $('owner-hosts-form').hidden = false;
  field('alias').focus();
}
async function write(rows, batch = false) {
  if (!state || busy || !isReady()) return;
  if(rows.length>OWNER_HOSTS_MAX){$('owner-hosts-message').textContent=t('p0285.ssh.limit');return;}
  const target=structuredClone(targetFor(state,rows));
  for(const row of target.rows)Object.freeze(row);Object.freeze(target.rows);Object.freeze(target.scope);Object.freeze(target);
  busy = true; $('owner-hosts-message').textContent=t('set.saving'); render();
  try {
    const { faceIdStep } = await import('./settings.js');
    const res = await signedWriteFa('owner_hosts_set', target, { t: 'owner_hosts_set', target },
      r => {
        const result=faceIdStep(r,{title:'ownerHosts.title',text:'ownerHosts.confirm',yes:'ownerHosts.confirmYes'});
        const box=$('confirm');if(box&&!box.hidden)$('confirm-text').append(node('pre',reviewRows(target.rows),'owner-hosts__review'));
        return result;
      });
    $('owner-hosts-message').textContent = res.ok ? t('set.saved') : whyText(res.why);
    if (res.ok) { $('owner-hosts-form').hidden = true; if(batch)drafts=drafts.filter(d=>!d.selected); }
    refresh();
  } finally { busy = false; render(); }
}
export function render() {
  const root = $('owner-hosts'); if (!root) return;
  root.hidden = !capable;
  if (!capable) return;
  root.querySelector('[data-title]').textContent = t('ownerHosts.title');
  root.querySelector('[data-note]').textContent = t('p0285.ssh.note');
  $('owner-hosts-add').textContent = t('ownerHosts.add');
  $('owner-hosts-save').textContent = t('p0285.ssh.stage');
  $('owner-hosts-refresh').textContent = t('ownerHosts.refresh');
  for (const k of keys) field(k).previousElementSibling.textContent = t('ownerHosts.' + k);
  const list = $('owner-hosts-list'); list.replaceChildren();
  const renderRow=(row,extra=0)=>{
    const item=node('div','','owner-hosts__item');
    item.append(node('p',`${row.alias}${extra?' · '+t('p0285.ssh.aliases',{n:extra}):''} · ${row.user}@${row.hostname}:${row.port} · ${t(state.valid===true&&row.status==='active'&&row.owner==='self'?'p0285.ssh.own':'ownerHosts.'+row.status)}`));
    item.append(node('p',row.fingerprint,'setnote'));
    if(row.status==='active')for(const key of ['ssh','scp']){const code=node('code',row[key]);item.append(copyable(code,{label:'set.copy'}));}
    const edit=node('button',t('ownerHosts.edit'),'aj-btn');edit.type='button';edit.disabled=busy;edit.addEventListener('click',()=>fill(row));
    const revoke=node('button',t('ownerHosts.revoke'),'aj-btn');revoke.type='button';revoke.disabled=busy;revoke.addEventListener('click',()=>write(cleanRows().filter(x=>x.alias!==row.alias)).catch(showError));
    item.append(edit,revoke);return item;
  };
  for(const group of groupedRows(state)){
    const item=renderRow(group.rows[0],group.rows.length-1);item.dataset.sshGroup='';
    if(group.rows.length>1){const aliases=node('details','','owner-hosts__aliases');aliases.append(node('summary',t('p0285.ssh.showAliases',{n:group.rows.length})));for(const row of group.rows.slice(1))aliases.append(renderRow(row));item.append(aliases);}
    list.append(item);
  }
  if (state && !state.valid && state.rev) list.append(node('p', t('ownerHosts.invalid'), 'setnote'));
  const others=node('details','','owner-hosts__others'),otherRows=(state?.candidates||[]).filter(row=>row.source!=='same_owner');
  others.append(node('summary',t('p0285.ssh.others',{n:otherRows.length})),node('p',t('p0285.ssh.otherNote'),'setnote'));
  for(const row of state?.candidates||[]){
    const same=row.source==='same_owner';
    if(same&&state.valid===true&&state.rows.some(r=>r.peer===row.peer&&r.status==='active'&&r.owner==='self'))continue;
    // Reuse the existing host row: identity/status is prose, the button names only its action.
    const item=node('div','','owner-hosts__item');
    const note=node('p',`${row.alias||row.peer} · ${t(same?'p0285.ssh.needKey':'ownerHosts.pending')}`);
    const b=node('button',t('ownerHosts.edit'),'aj-btn');b.type='button';b.disabled=busy;b.addEventListener('click',()=>fill(row));
    item.append(note,b);(same?list:others).append(item);
  }
  if(otherRows.length)list.append(others);
  if(state?.candidate_more)list.append(node('p',t('p0285.ssh.more',{n:state.candidate_more}),'setnote'));
  const staged=$('owner-hosts-drafts');staged.replaceChildren();staged.hidden=!drafts.length;
  for(const draft of drafts){
    const label=node('label','','owner-hosts__draft');
    const check=node('input','');check.type='checkbox';check.checked=draft.selected;check.disabled=busy;
    check.addEventListener('change',()=>{draft.selected=check.checked;render();});
    label.append(check,node('span',`${draft.row.alias} · ${draft.row.fingerprint}`));staged.append(label);
  }
  const commit=$('owner-hosts-batch');commit.textContent=t('p0285.ssh.confirmBatch',{n:drafts.filter(d=>d.selected).length});commit.hidden=!drafts.length;commit.disabled=busy||!drafts.some(d=>d.selected)||!state;
  for(const k of keys)field(k).disabled=busy;
  for (const id of ['owner-hosts-save', 'owner-hosts-add']) $(id).disabled = busy || !state;
}
function cleanRows() {
  return (state?.rows || []).map(cleanRow);
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
  form.addEventListener('submit',async e=>{
    e.preventDefault();if(busy||!state)return;
    const scope=JSON.stringify(state.scope),oldAlias=editingAlias;
    const row=Object.fromEntries(keys.map(k=>[k,['port','expires'].includes(k)?Number(field(k).value):field(k).value.trim()]));row.owner='self';
    if(!await validDraft(row)){$('owner-hosts-message').textContent=t('p0285.ssh.invalid');return;}
    if(scope!==JSON.stringify(state?.scope))return;
    const draft={row,editingAlias:oldAlias,selected:true},next=[...drafts.filter(d=>(d.editingAlias||d.row.alias)!==(oldAlias||row.alias)),draft];
    try{batchRows(state,next.filter(d=>d.selected));}catch(err){$('owner-hosts-message').textContent=t('p0285.ssh.'+err.message);return;}
    drafts=next;form.hidden=true;$('owner-hosts-message').textContent='';render();
  });
  const staged=node('div','');staged.id='owner-hosts-drafts';staged.hidden=true;
  const commit=node('button','','aj-btn aj-btn--primary');commit.id='owner-hosts-batch';commit.type='button';commit.hidden=true;
  commit.addEventListener('click',()=>{if(busy||!state)return;try{const selected=drafts.filter(d=>d.selected);if(selected.length)write(batchRows(state,selected),true).catch(showError);}catch(err){$('owner-hosts-message').textContent=t('p0285.ssh.'+err.message);}});
  const message = node('p', '', 'setnote'); message.id = 'owner-hosts-message'; message.setAttribute('role', 'status');
  root.append(title, note, list, add, reload, form, staged, commit, message); anchor.before(root); onLang(render);
}
