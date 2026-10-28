// P458: compare actual pre-P448 styles on the current deterministic page fixture.
// No live host, account, command, password or external traffic.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {generateKeyPairSync} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {contrast} from '../public/js/palette-color.js';
const require=createRequire(import.meta.url),pw=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const out='reports/qa/p458', baselineRef='718b90b57^';
for(const d of ['baseline','after'])mkdirSync(`${out}/${d}`,{recursive:true});
const oldCss=new Map();let mode='baseline';
const web=await startWebServer(),fake=await startFakeHost(),B=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
const results=[],failures=[],pictures=[];
const ok=(v,msg)=>{if(!v)failures.push(msg)};
try {for(const lang of ['zh','en'])for(const theme of ['light','dark']){
 const key=`390-${lang}-${theme}`,ctx=await B.newContext({viewport:{width:390,height:844},colorScheme:theme,hasTouch:true});
 await ctx.addInitScript(()=>{Object.defineProperty(navigator,'standalone',{get:()=>true});window.Audio=undefined;for(const k of ['play','pause','load'])HTMLMediaElement.prototype[k]=function(){return k==='play'?Promise.resolve():undefined}});
 await ctx.addInitScript(()=>{const now=Date.now();Date.now=()=>now});
 const p=await ctx.newPage();p.setDefaultTimeout(10000);
 await p.route('**/*',async route=>{
  const u=new URL(route.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return route.abort();
  if(u.pathname.endsWith('.css')&&mode==='baseline'){
   const path='web/public'+u.pathname;
   if(!oldCss.has(path)){try{oldCss.set(path,execFileSync('git',['show',baselineRef+':'+path],{encoding:'utf8',stdio:['ignore','pipe','ignore']}))}catch{oldCss.set(path,'')}}
   return route.fulfill({status:200,contentType:'text/css',body:oldCss.get(path)});
  }return route.continue();
 });
 try{
 mode='baseline';fake.reset();fake.st.caps=['wf1','jcards1'];fake.setUp(true);
 await p.goto(fake.newPairing(web.url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(theme=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=theme},theme);
 await fake.addTurn({k:'phone',dev:'fixture',text:'P458 fixture'},'Selectable message.\n\n```text\ncopy this code\n```');
 await p.waitForFunction(()=>document.getElementById('words').textContent.includes('Selectable'));
 await fake.send({t:'status',s:'working',agent:'claude',name:'Agent J'});
 await fake.send({t:'busy',by:'Agent J',load:0.6,tasks:[{id:'p458',who:'P458',kind:'temp',label:'Local screenshot fixture',c:1,since:Math.floor(Date.now()/1000)}]});
 await p.waitForFunction(()=>!document.getElementById('busyDots').hidden);
 await p.evaluate(()=>{document.getElementById('om').dataset.unread='1';document.getElementById('omUnread').dataset.n='2';document.getElementById('omUnreadN').textContent='2';document.getElementById('omUnread').setAttribute('aria-disabled','false');document.getElementById('mailBtn').hidden=false;document.getElementById('cardPlus').hidden=false});
 const cdp=await ctx.newCDPSession(p);await cdp.send('DOM.enable');await cdp.send('CSS.enable');
 let serial=0;
 const styles=async which=>{
  mode=which;
  await p.evaluate(async serial=>{await Promise.all([...document.querySelectorAll('link[rel=stylesheet]')].map(e=>new Promise((resolve,reject)=>{e.onload=resolve;e.onerror=reject;const u=new URL(e.href);u.searchParams.set('p458',serial);e.href=u.href})))},String(++serial));
  await p.evaluate(async()=>{await document.fonts.ready;document.styleSheets[0].insertRule('* ,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}',document.styleSheets[0].cssRules.length);document.activeElement?.blur()});
  await p.mouse.move(389,843);await p.evaluate(()=>{document.getElementById('toast').classList.remove('on');for(const e of document.querySelectorAll('*')){if(e.scrollTop)e.scrollTop=0;if(e.scrollLeft)e.scrollLeft=0}});await p.waitForTimeout(80);
 };
 const shot=async(name,selector)=>{
  const path=`${out}/${mode}/${key}-${name}.png`;
  if(selector)await p.locator(selector).screenshot({path,animations:'disabled'});else await p.screenshot({path,animations:'disabled'});
  if(mode==='after')pictures.push(`${key}-${name}.png`);
 };
 const props=['backgroundColor','color','opacity','borderColor','borderWidth','boxShadow','transform','outlineColor','outlineStyle','outlineWidth','filter','fill','stroke'];
 const sample=loc=>loc.evaluate((e,props)=>[e,...e.querySelectorAll('*')].map(n=>{const s=getComputedStyle(n);return Object.fromEntries(props.map(k=>[k,s[k]]))}),props);
 const state=async(loc,s)=>{
  await p.mouse.move(389,843);await p.evaluate(()=>document.activeElement?.blur());
  const {root}=await cdp.send('DOM.getDocument');const id=await loc.getAttribute('data-p458');
  const {nodeId}=await cdp.send('DOM.querySelector',{nodeId:root.nodeId,selector:`[data-p458="${id}"]`});
  await cdp.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:[]});
  if(s==='hover'||s==='active')await loc.hover({force:true});
  if(s==='active')await cdp.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:['active','hover']});
  if(s==='focus')await loc.focus();
  await p.waitForTimeout(20);
  const v=await sample(loc);
  if(s==='hover'&&await loc.evaluate(e=>!e.disabled&&getComputedStyle(e).pointerEvents!=='none'))ok(await loc.evaluate(e=>e.matches(':hover')),`${key} real hover missing ${id}`);
  return {v,nodeId};
 };
 const audit=async(surface,full=false)=>{
  console.log('surface '+key+' '+surface);
  await p.evaluate(()=>{let n=0;for(const e of document.querySelectorAll('button,[role=button],a,input,textarea,select,[tabindex]:not([tabindex="-1"]),#orb')){if(e.getClientRects().length&&!e.closest('[hidden]')&&getComputedStyle(e).visibility!=='hidden')e.dataset.p458=String(n++)}});
  const ids=await p.locator('[data-p458]').evaluateAll(ns=>ns.filter(e=>e.getClientRects().length&&!e.closest('[hidden]')&&getComputedStyle(e).visibility!=='hidden').map(e=>({n:e.dataset.p458,id:e.id,approval:e.matches('#elev-allow'),hold:e.matches('.apprallow')})));   // P565 追加 C 补充: the Noir pair is the sudo card's only
  const scope=surface==='chat'?null:surface==='settings'?'#settings':surface==='busy'?'#busyDlg':surface==='sudo'?'#elev-form':'#sheet';
  const activeIds=scope?await p.locator(scope).locator('[data-p458]').evaluateAll(ns=>ns.map(e=>e.dataset.p458)):null;
  const samples={};
  for(const which of ['baseline','after']){
   await styles(which);await shot(surface);
   if(surface==='chat')for(const [name,sel]of [['top','.top'],['toolbar','.rmbar'],['attachments','.tools'],['composer','.composer']]){if(await p.locator(sel).count())await shot(name,sel)}
   for(const item of ids){if(activeIds&&!activeIds.includes(item.n))continue;const loc=p.locator(`[data-p458="${item.n}"]`);if(!await loc.isVisible())continue;
    for(const s of (full?['normal','hover','active','focus']:['normal','hover'])){
     const {v,nodeId}=await state(loc,s),k=item.n+'-'+s;
     if(which==='baseline')samples[k]=v;
     else if(!item.approval){ok(JSON.stringify(samples[k])===JSON.stringify(v),`${key} ${surface} ${item.id||item.n} ${s} style differs`);
      // P565 追加 C 补充 (董事长 10-28): 长按批准 is unchanged under the finger — hover / active / focus keep its resting colours
      if(item.hold&&s!=='normal'){const rest=samples[item.n+'-normal']?.[0]||{};ok(v[0].backgroundColor===rest.backgroundColor&&v[0].color===rest.color,`${key} ${surface} 长按批准 ${s} changed colour ${JSON.stringify([v[0].backgroundColor,v[0].color,rest.backgroundColor,rest.color])}`)}}
     else if(s!=='normal'){
      const css=await loc.evaluate(e=>({bg:getComputedStyle(e).backgroundColor,fg:getComputedStyle(e).color}));
      ok(css.bg==='rgb(44, 43, 38)'&&css.fg==='rgb(250, 249, 245)',`${key} ${surface} approval ${s} pair ${JSON.stringify(css)}`);
      ok(contrast('#2c2b26','#faf9f5')>=4.5,'approval contrast');
     }else ok(JSON.stringify(samples[k])===JSON.stringify(v),`${key} approval resting changed`);
     if(s==='hover'&&(surface==='chat'||item.approval||item.hold))await shot(`${surface}-hover-${item.id||item.n}`,`[data-p458="${item.n}"]`);
     if(surface==='chat'&&['sayCancel','loadHit','busyDots'].includes(item.id)&&s!=='normal')await shot(`${surface}-${item.id}-${s}`);
     await cdp.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:[]});
     results.push({key,surface,which,id:item.id||item.n,state:s,approval:item.approval});
    }
   }
   await p.locator('[data-p458]').evaluateAll(ns=>ns.forEach(e=>e.blur()));await p.mouse.move(389,843);
  }
  await p.locator('[data-p458]').evaluateAll(ns=>ns.forEach(e=>delete e.dataset.p458));
 };
 await audit('chat',true);
 for(const sel of ['#input','#words p','#words pre'])ok(await p.locator(sel).evaluate(e=>getComputedStyle(e).userSelect)!=='none',`${key} ${sel} selectable`);
 await p.evaluate(()=>import('./js/settings.js').then(m=>m.open()));await p.waitForFunction(()=>!document.getElementById('settings').hidden);await audit('settings');
 await p.evaluate(()=>import('./js/settings.js').then(m=>m.close()));
 await p.evaluate(()=>import('./js/busy.js').then(m=>m.open()));await p.waitForFunction(()=>!document.getElementById('busyDlg').hidden);await audit('busy',true);
 await p.locator('#busyClose').click();
 const epk=generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x;
 await fake.send({t:'elev',id:'a'.repeat(32),kind:'sudo',n:'b'.repeat(32),epk,ttl:600,tries:3,cmd:'systemctl status backup',why:'Check backup',effect:'Read status'});
 await p.waitForFunction(()=>document.getElementById('elev-allow')?.getClientRects().length>0);await audit('sudo',true);
 await p.evaluate(()=>import('./js/elevate.js').then(m=>m.done({id:'a'.repeat(32),result:'denied'})));
 await fake.ask({id:'c'.repeat(32),tool:'Bash',summary:'echo safe fixture',ttl:600,cat:['read']});await p.waitForFunction(()=>document.body.dataset.sheet==='1');await audit('permission',true);
 await fake.send({t:'ask_done',id:'c'.repeat(32),result:'denied'});
 // P457 retained empty-unread behavior is an explicit baseline exception.
 await p.evaluate(()=>document.getElementById('om').dataset.unread='0');
 ok(await p.locator('#omUnread').evaluate(e=>getComputedStyle(e).visibility)==='hidden',`${key} empty unread hidden`);
 // #225 real trusted touch hold, selection guard, editable/text/code exceptions.
 for(const id of ['webReportBtn','tDoc','tPhoto','slashBtn']){
  const loc=p.locator('#'+id);await loc.scrollIntoViewIfNeeded();const r=await loc.boundingBox();
  ok(await loc.evaluate(e=>getComputedStyle(e).userSelect)==='none',`${key} ${id} selection CSS`);
  await p.evaluate(()=>getSelection().removeAllRanges());
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:r.x+r.width/2,y:r.y+r.height/2}]});await p.waitForTimeout(710);
  ok(await p.evaluate(()=>String(getSelection()))==='',`${key} ${id} native hold selected`);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});await p.keyboard.press('Escape');
 }
 console.log('checked '+key);
 }finally{await ctx.close()}
 }}finally{await B.close();await fake.stop();await web.stop()}
writeFileSync(`${out}/results.json`,JSON.stringify({baselineRef,baselineMethod:'All stylesheet bytes from git 718b90b57^; current JS/DOM and same paired fixture isolate CSS. Animations/transitions paused identically. Non-CSS later changes retained.',combined:JSON.parse(readFileSync('web/public/version.json')).combined,physical:false,results,pictures,failures},null,2)+'\n');
console.log(JSON.stringify({samples:results.length,failures:failures.length,first:failures.slice(0,15)}));assert.deepEqual(failures,[]);
