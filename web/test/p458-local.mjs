// P458 additions: real clicks/hover, P457 before vs candidate after, local paired fixtures only.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const out='reports/qa/p458',web=await startWebServer(),fake=await startFakeHost(),B=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
const checks=[],pictures=[];
try{for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const which of ['before','after']){
 mkdirSync(`${out}/${which}`,{recursive:true});
 const ctx=await B.newContext({viewport:{width:390,height:844},colorScheme:theme,permissions:['clipboard-read','clipboard-write']});
 const p=await ctx.newPage();p.setDefaultTimeout(10000);console.log("START",lang,theme,which);
 try{
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();if(which==='before'&&(u.pathname.endsWith('.css')||u.pathname==='/js/copycode.js'))return r.fulfill({status:200,contentType:u.pathname.endsWith('.css')?'text/css':'text/javascript',body:execFileSync('git',['show','e77d2e0d1:web/public'+u.pathname],{encoding:'utf8'})});return r.continue()});
 fake.reset();fake.setUp(true);await p.goto(fake.newPairing(web.url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(theme=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=theme;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{}}});const st=document.createElement('style');st.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(st)},theme);
 await fake.addTurn({k:'phone',text:'P458 fixture'},'P458 response fixture.');await p.waitForFunction(()=>document.getElementById('words').textContent.includes('response fixture'));
 const shot=async name=>{await p.waitForTimeout(80);const file=`390-${lang}-${theme}-${name}.png`;await p.screenshot({path:`${out}/${which}/${file}`});if(which==='after')pictures.push(file)};
 await p.locator('#copyReply').hover();await p.locator('#copyReply').click();await p.waitForFunction(()=>document.getElementById('copyReply').dataset.done==='1');await shot('copy-click-hover');
 if(which==='after'){assert.equal(await p.locator('#copyReply .cc-result').count(),0);assert.equal(await p.locator('#copyReply .cc-ok').count(),1);assert.equal(await p.locator('#toast').evaluate(e=>e.classList.contains('on')),false)}
 await p.evaluate(()=>import('./js/relay.js').then(m=>m.remoteReader(true)));await p.waitForFunction(()=>document.body.dataset.reader==='1');
 for(const unread of [0,1]){await p.evaluate(n=>{document.getElementById('rd').dataset.unread=String(n);document.getElementById('rdUnread').dataset.n=String(n);document.getElementById('rdUnreadN').textContent=String(n)},unread);await p.locator('#rdUnread').hover({force:true});await shot('reader-unread-'+unread+'-hover');if(which==='after')assert.equal(await p.locator('#rdUnread').evaluate(e=>getComputedStyle(e).borderWidth),'0px')}
 await p.evaluate(()=>import('./js/relay.js').then(m=>m.remoteReader(false)));await p.waitForTimeout(500);await p.locator('#tFull').click();await p.waitForFunction(()=>!document.getElementById('fsEd').hidden);
 for(const id of ['fsExit','fsClear','fsSlash']){await p.locator('#'+id).hover();await shot('editor-'+id+'-hover');await p.locator('#'+id).focus();await shot('editor-'+id+'-focus');if(which==='after')assert.deepEqual(await p.locator('#'+id).evaluate(e=>[getComputedStyle(e).borderWidth,getComputedStyle(e).outlineWidth]),['0px','0px'])}
 await p.locator('#fsExit').click();await p.evaluate(()=>import('./js/ui.js').then(m=>m.toastAction('P458 temporary notification','Undo',()=>{},60000)));await p.waitForFunction(()=>document.querySelector('#toast.on.rect'));await p.mouse.move(389,843);await shot('bottom-notice');
 if(which==='after'){const style=await p.locator('#toast').evaluate(e=>({shadow:getComputedStyle(e).boxShadow,filter:getComputedStyle(e).filter}));assert.equal(style.filter,'none');assert.equal(style.shadow,'none')}
 checks.push({lang,theme,which,pass:true});console.log('PASS',lang,theme,which);
 }finally{await ctx.close()}
}}finally{await B.close();await fake.stop();await web.stop()}
writeFileSync(`${out}/local-results.json`,JSON.stringify({checks,pictures,baseline:'e77d2e0d1'},null,2)+'\n');
