// #573/#444: display aliases only, through the existing paired preferences channel. Never executes an address.
import { t } from './t.js';
import { mk as nativeMk } from './ui.js';
// Exact factory for this schema/view; ordinary aj-action controls remain independent actors.
function mk(tag, cls, text) {
  cls=cls?.replace(/\baj-btn(?=\b|--)/g,'aj-action');
  const action=typeof cls==='string'&&cls.split(/\s+/).includes('aj-action');
  return nativeMk(tag,cls,text,action?'aj-action':'aj-remotes',action?'dialog':undefined);
}
export const REMOTE_NAMES = 'ui.remote_servers';
import { validRemoteNames } from './remote-names-rules.js';
export { validRemoteNames } from './remote-names-rules.js';
let box, editing = false;
export function renderRemoteNames(rows, enabled, save) {
  const parent = document.getElementById('set-s-app'); if (!parent) return;
  if (!box) { box = mk('details','setrow remote-names'); box.id='set-remote-names'; parent.append(box); }
  box.hidden = !enabled;
  if (!enabled || editing) return;
  box.replaceChildren();
  box.append(mk('summary','setlab',t('feedback573.remoteTitle')), mk('p','setnote',t('feedback573.remoteNote')));
  const list=mk('div'), error=mk('p','setnote ardanger'); error.setAttribute('role','alert');
  const addButton=mk('button','aj-btn aj-btn--sm',t('feedback573.remoteAdd')); addButton.type='button';
  const saveButton=mk('button','aj-btn aj-btn--sm aj-btn--primary',t('feedback573.remoteSave')); saveButton.type='button';
  const add=(row={host:'',name:''})=>{
    const item=mk('div','remote-name-row');
    for(const key of ['host','name']) {
      const label=mk('label','setlab',t('feedback573.remote'+(key==='host'?'Host':'Name'))), input=mk('input','aj-input');
      input.type='text'; input.autocomplete='off'; input.spellcheck=false; input.maxLength=key==='host'?80:40;
      input.value=row[key]||''; input.dataset.field=key; input.addEventListener('input',()=>{editing=true;error.textContent='';}); label.append(input); item.append(label);
    }
    const del=mk('button','aj-btn aj-btn--sm',t('feedback573.remoteRemove')); del.type='button';
    del.addEventListener('click',()=>{item.remove();editing=true;addButton.disabled=list.children.length>=32;});item.append(del);list.append(item);addButton.disabled=list.children.length>=32;
  };
  for(const row of Array.isArray(rows)?rows:[]) add(row);
  addButton.addEventListener('click',()=>{if(list.children.length<32){add();editing=true;}});
  saveButton.addEventListener('click',async()=>{
    const value=[...list.children].map(item=>Object.fromEntries([...item.querySelectorAll('input')].map(i=>[i.dataset.field,i.value.trim()])));
    if(!validRemoteNames(value)){error.textContent=t('feedback573.remoteInvalid');return;}
    saveButton.disabled=true;
    try{if(await save(REMOTE_NAMES,value)){editing=false;box.open=false;}else error.textContent=t('feedback573.remoteFailed');}finally{saveButton.disabled=false;}
  });
  const actions=mk('p','mailbtns');actions.append(addButton,saveButton);box.append(list,error,actions);
}
