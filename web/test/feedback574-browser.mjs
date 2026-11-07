// Offline real Chromium + real Noise pairing; synthetic host/mailbox only.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep} from './browser.mjs';
const out='/work/.qa574/browser';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost();let B;const results=[];
try {
 B=await launch({args:['--no-sandbox']});
 for(const lang of ['zh','en'])for(const width of [390,1440]) {
  fake.reset();fake.st.mail=null;fake.st.autoReply=false;
  const p=await newPage(B,width,900,'light',{allow:[web.url,fake.relay+'/']});
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`(async()=>{document.querySelector('#phone-env-dismiss')?.click();document.querySelector('#a2hs-ok')?.click();const s=await import('./js/settings.js');s.open();document.getElementById('set-mail-nav').click();})()`);
   await waitFor(p,`document.getElementById('mail-blocked').children.length`);
   await evaluate(p,`(async()=>{const {ask1}=await import('./js/session.js');await ask1({t:'mail_set',cfg:{address:'synthetic@example.test',preset:'qq'}},'mail_res');await (await import('./js/mail.js')).load();document.getElementById('mail-test').click();})()`);
   await waitFor(p,`!document.getElementById('mail-enable-next').hidden`);
   assert.equal(fake.st.mail.config.enabled,false,'test must not enable');
   await evaluate(p,`document.getElementById('mail-enable-next').click()`);
   await waitFor(p,`document.getElementById('mail-on').getAttribute('aria-checked')==='true'`);
   assert.equal(fake.st.mail.config.enabled,true);
   await evaluate(p,`document.getElementById('mail-mark-read').click()`);
   await waitFor(p,`!document.getElementById('mail-mark-read').disabled`);
   assert.equal(fake.st.mail.config.mark_processed_read,false);
   await evaluate(p,`(async()=>{const s=await import('./js/settings.js');s.close();const root=document.createElement('div');root.id='p574-fixture';root.className='words';root.style.cssText='position:fixed;top:20px;left:20px;right:20px;z-index:99999;background:var(--paper);padding:16px';root.innerHTML='<p><span class="rlink" data-say="A longer explanation that wraps across multiple lines on a narrow phone">Start</span></p>';document.body.append(root);const page={id:574,end:'done',source:{k:'phone'}};const rb=await import('./js/replybtn.js');rb.paint(root,page,{pages:()=>[page],send:()=>{}});})()`);
   const layout=await evaluate(p,`(()=>{const b=document.querySelector('#p574-fixture .rbtn'),x=b.querySelector('small');return {direction:getComputedStyle(b).flexDirection,align:getComputedStyle(x).textAlign,below:x.getBoundingClientRect().top>b.getBoundingClientRect().top};})()`);
   assert.deepEqual(layout,{direction:'column',align:'left',below:true});
   const copy = await evaluate(p,`(async()=>{const root=document.createElement('p');root.textContent='Run '+String.fromCharCode(96)+'agentj service restart'+String.fromCharCode(96);document.querySelector('#p574-fixture').append(root);(await import('./js/shortfields.js')).shortFields(root,{inlineCode:true});const field=root.querySelector('[data-cc]');return {value:field.textContent,copy:field.getAttribute('data-cc'),executors:root.querySelectorAll('[data-run],a').length};})()`);
   assert.deepEqual(copy,{value:'agentj service restart',copy:'bare',executors:0});
   writeFileSync(out+'/'+lang+'-'+width+'.png',await shoot(p));
   assert.deepEqual(p.problems,[]);
   if(width===390) {
    fake.kick(1001);fake.setUp(false);await sleep(6100);fake.setUp(true);await waitState(p,'ready');
    await waitFor(p,`document.getElementById('toast').textContent.includes(${JSON.stringify(lang==='zh'?'原因未知':'cause is unknown')})`);
   }
   results.push({lang,width,mailEnable:true,markRead:false,layout,copy,errors:p.problems});
  } finally {await p.close();}
 }
} finally {if(B)await B.close();await fake.stop();await web.stop();writeFileSync(out+'/results.json',JSON.stringify(results,null,2));}
console.log(JSON.stringify(results));
