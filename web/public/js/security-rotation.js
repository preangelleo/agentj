// P490: no destructive action before a genuinely new, independently resumed remote has proved its passkey.
import { t } from './t.js';
import { mk, confirmSheet, toast } from './ui.js';
import { ask1 } from './session.js';
import { signedWriteFa } from './controls.js';
export async function rotationWizard() {
  let response=await ask1({t:'security_status'},'ctl_res',6000);
  if(!response.ok||!response.rotation) { toast(t('p490.unsupported'));return; }
  const root=document.getElementById('rm-security-view')||mk('div','rmadd');root.id='rm-security-view';
  document.getElementById('rm-security').after(root);
  async function render(m) {
    const v=m.rotation;if(v.stage==='verify')document.getElementById('rm-full-note').textContent=t('p490.preserve');root.replaceChildren(mk('h3','',t('p490.title')),mk('p','',t('p490.guide')));
    root.append(mk('p','',t('p490.stage_'+v.stage)));
    if(v.stage==='new'||v.stage==='done') {
      const b=mk('button','aj-btn',t('p490.begin'));b.type='button';b.onclick=()=>run('security_begin',v,[]);root.append(b);
    } else {
      root.append(mk('p','',t('p490.preserve')));
      if(v.can_verify) {const b=mk('button','aj-btn',t('p490.verify'));b.type='button';b.onclick=()=>run('security_verify',v,[]);root.append(b);}
      if(v.verified.includes(v.me)) {
        const b=mk('button','aj-btn',t('p490.retire'));b.type='button';
        b.onclick=async()=>{if(await confirmSheet(t('p490.title'),t('p490.confirm'),t('p490.retire'),true)) await run('security_retire',v,v.verified);};root.append(b);
      }
      root.append(mk('p','',t('p490.count',{n:v.verified.length,old:v.old.length})));
    }
  }
  async function run(action,v,keep) {
    const {faceIdStep}=await import('./settings.js');
    const target={epoch:v.started_at??0,keep};
    const m=await signedWriteFa(action,target,{t:action,...target},faceIdStep);
    if(!m.ok) {toast(t('p490.failed'));return;}
    await render(m);
  }
  await render(response);
}
