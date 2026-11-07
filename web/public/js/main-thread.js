// #579: current native turn, distinct from background workers. ACK is never a model reply.
import {t,onLang} from './t.js';
import {signed,estop} from './controls.js';
import {ask1,isReady,gen} from './session.js';
let state=null, received=0, generation=null, pending=false, accepted=false, notice='', timer=0;
const $=id=>document.getElementById(id);
export function sanitize(x) {
  if(!x||typeof x!=='object')return null;
  const n=v=>Number.isFinite(v)&&v>=0?Math.min(86400,Math.floor(v)):0;
  return {enabled:x.enabled===true,available:x.available!==false,busy:x.busy===true,owner_waiting:x.owner_waiting===true,
    interrupt_reason:['native_turn_binding_unavailable','native_turn_unavailable','native_confirmation_pending'].includes(x.interrupt_reason)?x.interrupt_reason:'',waiting_s:n(x.waiting_s),elapsed_s:n(x.elapsed_s),can_interrupt:x.can_interrupt===true&&(x.turn===null||(Number.isSafeInteger(x.turn)&&x.turn>=1)),turn:Number.isSafeInteger(x.turn)&&x.turn>=1?x.turn:null,session:/^[0-9a-f]{24}$/.test(x.session)?x.session:''};
}
export function set(x){const next=sanitize(x);if(!next?.owner_waiting||!state?.owner_waiting||next?.turn!==state?.turn||next?.session!==state?.session){accepted=false;notice='';}
  state=next;received=performance.now();generation=gen();paint();}
export function connection(on){if(!on){state=null;accepted=false;paint();}}
function paint(){
  if(state&&(!isReady()||generation!==gen()))state=null;
  const b=$('main-thread-interrupt');if(!b)return;
  const seconds=state?state.waiting_s+Math.floor((performance.now()-received)/1000):0;
  b.hidden=!(state?.owner_waiting&&state.busy&&state.available&&state.can_interrupt&&state.session&&seconds>=30&&!estop.on&&!accepted);
  b.disabled=pending;
  const label=notice||t(accepted?'p579.accepted':'p579.interrupt');
  b.setAttribute('aria-label',label);b.title=label;

}
async function interrupt(){
  if(pending||accepted||$('main-thread-interrupt')?.hidden||!state?.can_interrupt||!isReady()||estop.on)return;
  const request={turn:state.turn,session:state.session},g=gen();pending=true;paint();
  try{
    const obj=await signed('main_thread_interrupt',{request},{t:'main_thread_interrupt',request});
    if(g!==gen()||request.turn!==state?.turn||request.session!==state?.session)return;
    const res=await ask1(obj,'main_thread_interrupt_res',10000);
    if(g!==gen()||request.turn!==state?.turn||request.session!==state?.session)return;
    accepted=res.ok===true;
    if(!accepted)notice=t(res.why==='stale'?'p579.stale':'p579.failed');
  }catch{if(g===gen())notice=t('p579.failed');}
  finally{pending=false;paint();}
}
export function init(){ $('main-thread-interrupt')?.addEventListener('click',interrupt);onLang(paint);timer=setInterval(paint,1000);paint(); }
