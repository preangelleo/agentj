import { capture } from './layout-sample.js';
import { Upload } from './blobs.js';
import { ask1, isReady, gen, hostId } from './session.js';
import { paintIOSChrome } from './ios-chrome.js';
import { busyLook } from './theme.js';
import VERSION from '../version.js';
import { t, onLang } from './t.js';
export { capture } from './layout-sample.js';
let running=false, result='';
function paint() { const n=document.getElementById('layout-diag-result');if(n)n.textContent=result?t('p489.'+result):''; }
export async function send() {
 if(running)return;
 if(!isReady()){result='offline';paint();return;}
 running=true;result='sending';paint();const button=document.getElementById('layout-diag-send');if(button)button.disabled=true;
 const generation=gen(), host=hostId();
 try {
  // Capture before awaiting the hash: geometry / meter all belong to this click.
  const sample=capture({build:{version:VERSION.version,combined:VERSION.combined},look:busyLook()});
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(paintIOSChrome.toString()));
  sample.build.iosCode=Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
  if(gen()!==generation||!isReady()){result='offline';return;}
  const response=await ask1({t:'layout_diag',sample},'layout_diag_res',8000);
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
// @ix app.layout_diagnostic
export function configure(){document.getElementById('layout-diag-send')?.addEventListener('click',send);onLang(paint);}
