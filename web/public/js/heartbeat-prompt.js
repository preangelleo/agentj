// P537: paired E2E reads; signed writes bind exact operation, draft and prior hash.
import {ask1,isReady,peer,gen} from './session.js';
import {signedWrite,whyText} from './controls.js';
import {el,toast,confirmSheet} from './ui.js';
import {t} from './t.js';
import {open} from './text-editor.js';
let view=null;
async function load() {
  if (!isReady()) {toast(t('set.offline'));return null;}
  const m=await ask1({t:'heartbeat_get'},'heartbeat_state',5000);
  if(m.t!=='heartbeat_state'||!m.ok){toast(t('p537.upgrade'));return null;}
  view=m;el('set-hb-update').hidden=!m.update_available;return m;
}
async function write(op,text,sha,g) {
  if(g!==gen()){toast(t('set.offline'));return false;}
  const target={op,text,sha};
  const r=await signedWrite('heartbeat_write',target,{t:'heartbeat_write',...target});
  if(!r.ok){toast(t('p537.failed')+' '+whyText(r.why));return false;}
  toast(t('set.saved'));await load();return true;
}
export function configure(){
  el('set-hb-prompt').addEventListener('click',async()=>{
    const m=await load();if(!m)return;
    const g=gen();
    open({title:t('p537.prompt'),value:m.text,maxLength:16000,trigger:el('set-hb-prompt'),
      reset:()=>m.default,preview:true,save:text=>write('save',text,m.sha,g)});
  });
  el('set-hb-merge').addEventListener('click',async()=>{const m=await load();if(m)await write('merge',null,m.sha,gen());});
  el('set-hb-update').hidden=true;
  // Refresh the notice when settings opens; no write or default creation on a read.
  new MutationObserver(()=>{if(!el('settings').hidden&&isReady()&&peer.heartbeat_prompt)load();}).observe(el('settings'),{attributes:true,attributeFilter:['hidden']});
}
