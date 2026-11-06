// #474: metadata only. Credentials use the existing signed secret_out pickup flow.
import { mk } from './ui.js';
import { t, locale } from './t.js';
import { copyButton } from './copycode.js';
import { createInput } from './externalmcp-model.js';
export { parseConnectors } from './externalmcp-model.js';
let pending = false, notice = '';
function setNotice(key) { notice=key; const node=document.querySelector('.mcp474-status'); if(node) node.textContent=key?t(key):''; }
const stamp = sec => new Date(sec * 1000).toLocaleString(locale());
export function connectorsSection(connectors, D) {
  const box = mk('section', 'mcp474'), title = mk('h3', 'setg', t('mcp474.title'));
  box.append(title, mk('p','setnote',t('mcp474.disclosure')), mk('p','setnote',t('mcp474.pickup')));
  const status = mk('p','setnote mcp474-status',notice?t(notice):''); status.setAttribute('role','status');
  const button = (label, cls, fn) => { const b = mk('button','aj-btn aj-btn--sm '+cls,t(label)); b.type='button'; b.disabled=pending; b.addEventListener('click',fn); return b; };
  async function operate(op, fields) {
    if (pending) return;
    if (!D.ready()) { setNotice('mcp474.failed'); return; }
    pending=true; for(const b of box.querySelectorAll('button')) b.disabled=true;
    setNotice('mcp474.pending');
    try {
      const m = await D.ask({t:'hook_set',op,...fields},'hook_list_res',130000);
      if(m.t==='hook_list_res' && m.ok===true) { pending=false; setNotice('mcp474.done'); await D.refresh(); }
      else setNotice('mcp474.failed');
    } catch { setNotice('mcp474.failed'); }
    finally { pending=false; for(const b of document.querySelectorAll('.mcp474 button')) b.disabled=false; }
  }
  // @ix app.mcp_copy app.mcp_rotate app.mcp_revoke
  const ul=mk('ul','whlist mcp474-list');
  if(!connectors.length) box.append(mk('p','setnote',t('mcp474.empty')));
  for(const c of connectors) {
    const li=mk('li','whrow'); li.dataset.id=c.id; li.dataset.state=c.state;
    const head=mk('div','whhead'); head.append(mk('b','whname',c.name),mk('span','whchip',t('mcp474.state.'+c.state)));
    li.append(head,mk('p','whline',t('mcp474.scope')),mk('p','whline',c.origins.join('\n')));
    if(c.created!==null) li.append(mk('p','whline',t('mcp474.created',{time:stamp(c.created)})));
    if(c.last!==null) li.append(mk('p','whline',t('mcp474.last',{time:stamp(c.last)})));
    if(c.state!=='revoked') {
      const acts=mk('div','whacts');
      if(c.state==='active') {
        if(c.url) acts.append(button('mcp474.copy','mcp474-copy',e=>copyButton(c.url,e.currentTarget)));
        acts.append(button('mcp474.rotate','mcp474-rotate',async()=>{
          if(await D.confirm(t('mcp474.confirm',{name:c.name}),t('mcp474.rotateNote'),t('mcp474.rotate'),true)) operate('mcp-rotate',{id:c.id});
        }));
      }
      acts.append(button('mcp474.revoke','aj-btn--danger mcp474-revoke',async()=>{
        if(await D.confirm(t('mcp474.confirm',{name:c.name}),t('mcp474.revokeNote'),t('mcp474.revoke'),true)) operate('mcp-revoke',{id:c.id});
      })); li.append(acts);
    }
    ul.append(li);
  }
  box.append(ul);
  const form=mk('form','mcp474-form');
  const name=mk('input','aj-input mcp474-name'); name.id='mcp474-name'; name.maxLength=40; name.required=true; name.autocomplete='off';
  const origins=mk('textarea','aj-textarea mcp474-origins'); origins.id='mcp474-origins'; origins.rows=3; origins.required=true; origins.maxLength=4096; origins.autocapitalize='none'; origins.spellcheck=false;
  const nl=mk('label','',t('mcp474.name')); nl.htmlFor=name.id;
  const ol=mk('label','',t('mcp474.origins')); ol.htmlFor=origins.id;
  const go=button('mcp474.create','mcp474-create',()=>{}); go.type='submit';
  form.append(nl,name,ol,origins,mk('p','setnote',t('mcp474.example')),go);
  // @ix app.mcp_create
  form.addEventListener('submit',e=>{e.preventDefault();const fields=createInput(form.querySelector('#mcp474-name').value,form.querySelector('#mcp474-origins').value);if(!fields){setNotice('mcp474.invalid');return;}operate('mcp-create',fields);});
  if(pending) status.textContent=t('mcp474.pending');
  box.append(form,status); return box;
}
