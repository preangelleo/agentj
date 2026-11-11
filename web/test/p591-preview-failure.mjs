// P591: formal-page preview failure marker, real browser + fake Noise host, eight bilingual/theme/viewport screenshots.
import assert from 'node:assert/strict';
import {mkdirSync, writeFileSync} from 'node:fs';
import {resolve, join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {seatId} from '../public/js/seats.js';
import {launch, newPage, navigate, evaluate, waitFor, waitState, shoot, key, sleep} from './browser.mjs';
const out=resolve('reports/qa/p591-preview-failure');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0}),fake=await startFakeHost(),browser=await launch();
let checks=0;
const check=(v,message)=>{assert.ok(v,message);checks++};
const capture=async(p,name)=>writeFileSync(join(out,name+'.png'),await shoot(p));
try {
 for(const language of ['zh','en'])for(const theme of ['light','dark'])for(const [width,height] of [[390,844],[1440,900]]){
  fake.reset();fake.st.prefSet=true;fake.st.prefs={appearance:{language,theme}};
  const p=await newPage(browser,width,height,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  const label=`${language}-${theme}-${width}`;
  try {
   const seatPath=await seatId(fake.channel);
   await navigate(p,fake.newPairing(web.url.replace(/\/$/,'')+'/'+seatPath+'/?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();true`);
   const seat=await evaluate(p,`globalThis.AJSeat?.id`);
   check(/^[a-z2-7]{8}$/.test(seat),label+': paired seat path');
   await capture(p,`before-${label}`);
   check(!await evaluate(p,`document.getElementById('toast').textContent.includes(${JSON.stringify(language==='zh'?'预览没能打开，已回到正式版':"Preview couldn't open — you're on the regular version.")}) && document.getElementById('toast').classList.contains('on')`),label+': no false failure toast');
   // A bootstrap sets the failure marker after loading the formal app. This reproduces its asynchronous report
   // without changing the frozen signed bootstrap or forging a preview cookie/signature.
   await evaluate(p,`sessionStorage.setItem('aj.preview.failed.'+globalThis.AJSeat.id,'1');true`);
   await waitFor(p,`document.getElementById('toast').classList.contains('on') && document.getElementById('toast').textContent.includes(${JSON.stringify(language==='zh'?'预览没能打开，已回到正式版':"Preview couldn't open — you're on the regular version.")})`);
   const state=await evaluate(p,`(()=>{const n=document.getElementById('toast'),b=n.querySelector('.undo'),r=b.getBoundingClientRect();return {text:n.textContent,kind:n.dataset.kind,background:getComputedStyle(n).backgroundColor,foreground:getComputedStyle(n).color,buttonHeight:r.height,top:n.getBoundingClientRect().top,viewport:innerHeight,marker:sessionStorage.getItem('aj.preview.failed.'+globalThis.AJSeat.id),ready:document.body.dataset.state}})()`);
   check(state.text.includes(language==='zh'?'预览没能打开，已回到正式版':"Preview couldn't open — you're on the regular version."),label+': exact formal fallback copy');
   check(state.kind==='warn'&&state.background==='rgb(44, 43, 38)'&&state.foreground==='rgb(250, 249, 245)'&&state.buttonHeight>=44,label+': Noir/Lotion and 44px dismiss');
   check(state.top>=50&&state.top<state.viewport/3,label+': fixed banner at top, not the reply bottom');
   check(state.marker===null,label+': failure marker consumed');
   await capture(p,`after-${label}`);
   if(language==='zh'&&theme==='light'&&width===390){
    await key(p,'Escape');check(!await evaluate(p,`document.getElementById('toast').classList.contains('on')`),'Esc dismisses notice');
    await evaluate(p,`import('./js/ui.js').then(({notify})=>notify({kind:'warn',text:'dismiss test',actions:[{label:'Got it',fn:()=>{}}],ms:10000}))`);
    await evaluate(p,`document.querySelector('#toast .undo').click();true`);
    check(!await evaluate(p,`document.getElementById('toast').classList.contains('on')`),'button dismisses notice');
    await evaluate(p,`import('./js/ui.js').then(({notify})=>notify({kind:'warn',text:'timeout test',actions:[{label:'Got it',fn:()=>{}}],ms:10000}))`);
    await sleep(10300);
    check(!await evaluate(p,`document.getElementById('toast').classList.contains('on')`),'auto dismisses after 10 seconds');
   }
   check(p.offsite.length===0,label+': no outside requests');
  }finally{await p.dispose();}
 }
 console.log(`p591-phone PASS ${checks} checks; before/after screenshots: ${out}`);
}finally{await browser.close();await fake.stop();await web.stop();}
