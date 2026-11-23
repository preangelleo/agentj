import { capture } from './layout-sample.js';
import { Upload } from './blobs.js';
import { ask1, isReady, gen, hostId } from './session.js';
import { paintIOSChrome } from './ios-chrome.js';
import { busyLook } from './theme.js';
import VERSION from '../version.js';
import { t, onLang } from './t.js';
import * as health from './render-diag.js';
export { capture } from './layout-sample.js';
let running=false, result='';
const HOST_GAP_MS=5200;   // the host answers one sample per 5 s per session
const wait=ms=>new Promise(r=>setTimeout(r,ms));
// The media cache's size (a number) comes from app.js: importing media.js here would change the page's module order.
let cacheBytes=()=>null;
export function setCacheBytes(fn){if(typeof fn==='function')cacheBytes=fn;}
const render=reason=>{try{return health.snapshot(reason,{cacheBytes:cacheBytes()});}catch{return null;}};
async function build(reason){
 // Capture before awaiting the hash: geometry / meter / counters all belong to this moment.
 const sample=capture({build:{version:VERSION.version,combined:VERSION.combined},look:busyLook(),render:render(reason)});
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(paintIOSChrome.toString()));
 sample.build.iosCode=Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
 return sample;
}
function paint() { const n=document.getElementById('layout-diag-result');if(n)n.textContent=result?t('p489.'+result):''; }
export async function send() {
 if(running)return;
 if(!isReady()){result='offline';paint();return;}
 running=true;result='sending';paint();const button=document.getElementById('layout-diag-send');if(button)button.disabled=true;
 const generation=gen(), host=hostId();
 try {
  const sample=await build('manual');
  if(gen()!==generation||!isReady()){result='offline';return;}
  let response=await ask1({t:'layout_diag',sample},'layout_diag_res',8000);
  // A host from before the render counters refuses the wider sample (save_failed); an unattended sample may have used
  // this session's 5-second slot (rate). Either way: once more after the slot, without the counters for the older host.
  if(response.ok===false&&(response.why==='save_failed'&&sample.render||response.why==='rate')){
   if(response.why==='save_failed')delete sample.render;
   await wait(HOST_GAP_MS);
   if(gen()!==generation||!isReady()){result='offline';return;}
   response=await ask1({t:'layout_diag',sample},'layout_diag_res',8000);
  }
  if(response.t==='timeout' && gen()===generation && isReady()) {
   // Existing hosts: the normal encrypted attachment receiver, never a chat / Agent turn.
   // Explicitly label its existing 30-minute staging TTL; permanent state/log needs the host candidate.
   const file=new Blob([JSON.stringify(sample)],{type:'application/json'});
   const upload=new Upload(file,{purpose:'att',origin:'file',name:'layout-diagnostic.json'});
   upload.host=host;
   const receipt=await upload.start();result=receipt.ok?'staged':'failed';
  } else result=response.ok===true?'saved':response.t==='offline'?'offline':'failed';
 } catch {result='failed';} finally {running=false;if(button)button.disabled=false;paint();}
}
// Unattended samples: a lost GPU context, or back in front after a long time hidden. Same content-free sample, same
// channel, no upload fallback, nothing on screen; at most health.AUTO_MAX per page per hour (queue rules: render-diag.js).
const queue=health.autoQueue({ready:isReady,busy:()=>running,hidden:()=>document.hidden,send:async reason=>{
 const generation=gen(),sample=await build(reason);
 if(gen()===generation&&isReady())await ask1({t:'layout_diag',sample},'layout_diag_res',8000);
}});
export const auto=reason=>queue.push(reason);
// @ix app.layout_diagnostic
export function configure(){document.getElementById('layout-diag-send')?.addEventListener('click',send);onLang(paint);health.start({emit:auto});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)queue.wake();});}
