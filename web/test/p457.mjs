// P457: real page + local encrypted fake host. No external account or production state.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const require=createRequire(import.meta.url);
const pw=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const tag=process.env.P457_TAG||'after',check=tag==='after',out=`reports/qa/p457/${tag}`;
mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.P457_PUBLIC_DIR?{publicDir:process.env.P457_PUBLIC_DIR}:{}),fake=await startFakeHost();
const results=[],failures=[];
const icons='.top button,.rmbar button,.tools button,.omtools button,#send,#slashBtn,#clr,.history-corner';
const record=(ok,msg)=>{if(!ok)failures.push(msg)};
try {
 for(const engine of ['webkit','chromium']){
 const B=await pw[engine].launch(engine==='chromium'?{executablePath:'/usr/bin/chromium',args:['--no-sandbox']}:{});
 try {for(const theme of ['light','dark'])for(const lang of ['zh','en']){
 const width=engine==='webkit'?390:1440,key=`${engine}-${width}-${lang}-${theme}`;
 const ctx=await B.newContext({...(engine==='webkit'?pw.devices['iPhone 13']:{}),viewport:{width,height:engine==='webkit'?844:900},colorScheme:theme});
 await ctx.addInitScript(()=>{
   Object.defineProperty(navigator,'standalone',{get:()=>true});
   const match=window.matchMedia.bind(window);window.matchMedia=q=>q==='(display-mode: standalone)'?new Proxy(match(q),{get:(t,k)=>k==='matches'?true:Reflect.get(t,k)}):match(q);
   window.Audio=undefined;for(const k of ['play','pause','load'])HTMLMediaElement.prototype[k]=function(){return k==='play'?Promise.resolve():undefined};
 });
 const p=await ctx.newPage();p.setDefaultTimeout(15000);p.on('pageerror',e=>console.log('pageerror '+e.message.replace(/https?:[^ ]+/g,'<url>')));console.log('start '+tag+' '+key);
 await p.route('**/*',route=>{const u=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(u.hostname)?route.continue():route.abort()});
 try {
 fake.reset();fake.st.caps=['wf1','jcards1'];fake.setUp(true);
 await p.goto(fake.newPairing(web.url.replace('127.0.0.1','localhost')+'?lang='+lang));
 await p.waitForTimeout(500);console.log(await p.evaluate(()=>({state:window.__ajState,secure:isSecureContext,crypto:!!crypto.subtle,text:document.getElementById('status')?.textContent})));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(theme=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=theme},theme);
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await fake.addTurn({k:'phone',dev:'fixture',text:lang==='zh'?'测试消息：图标保持线条外观。':'Test message: keep line icons.'},lang==='zh'?'已收到。工具栏与附件栏保持透明。':'Received. Toolbars keep transparent backgrounds.');
 await p.waitForFunction(()=>!document.getElementById('om').hidden&&document.getElementById('words').textContent.includes('。')||document.getElementById('words').textContent.includes('Received'));
 await p.evaluate(()=>{document.getElementById('mailBtn').hidden=false;document.getElementById('cardPlus').hidden=false;document.getElementById('fwdBtn').dataset.tg='1';document.activeElement.blur()});
 await p.waitForTimeout(300);
 await p.screenshot({path:`${out}/${key}-chat.png`});
 for(const [name,sel] of [['top','.top'],['toolbar','.rmbar'],['attachments','.tools'],['title','.omhd']])await p.locator(sel).screenshot({path:`${out}/${key}-${name}.png`});
 const audit=async state=>{
 const rows=await p.locator(icons).evaluateAll((nodes)=>nodes.filter(e=>e.getClientRects().length&&!e.closest('[hidden]')).map(e=>{const s=getComputedStyle(e);return {id:e.id,bg:s.backgroundColor,border:s.borderStyle,visibility:s.visibility,opacity:s.opacity}}));
 results.push({key,state,rows});
 for(const r of rows){const a=r.bg.match(/[\d.]+/g)?.map(Number)||[];record(a.length===4&&a[3]<1,`${key} ${state} ${r.id} background ${r.bg}`)}
 };
 await audit('normal');
 // Composited backgrounds must change with the body, even when the icon itself is transparent.
 const stateRows=[];
 for(const status of ['idle','working','waiting','question','offline']){
   await p.evaluate(status=>{document.body.dataset.status=status==='question'?'waiting':status==='offline'?'unknown':status;document.body.dataset.kind=status==='question'?'question':'permission';document.body.dataset.conn=status==='offline'?'off':'on';document.activeElement.blur()},status);
   await p.waitForTimeout(400);
   const rows=await p.locator(icons).evaluateAll(nodes=>{
     const rgb=s=>{const a=s.match(/[\d.]+/g).map(Number),k=s.startsWith('color(')?255:1;return [...a.slice(0,3).map(v=>v*k),a[3]??1]};
     const bg=e=>{if(!e)return [250,249,245];const s=getComputedStyle(e),under=bg(e.parentElement),v=rgb(s.backgroundColor);return v.slice(0,3).map((n,i)=>Math.round(n*v[3]+under[i]*(1-v[3])))};
     return nodes.filter(e=>e.getClientRects().length&&!e.closest('[hidden]')&&getComputedStyle(e).visibility!=='hidden').map(e=>({id:e.id,bg:getComputedStyle(e).backgroundColor,effective:bg(e),border:getComputedStyle(e).borderStyle}));
   });
   stateRows.push({status,rows});
   for(const r of rows){const a=r.bg.match(/[\d.]+/g)?.map(Number)||[];record(a.length===4&&a[3]<1,`${key} ${status} ${r.id} fixed background ${r.bg}`)}
   await p.screenshot({path:`${out}/${key}-state-${status}.png`});
 }
 const required=['cardPlus','scanBtn','srchBtn','setBtn','webReportBtn','readBtn','speakBtn','fwdBtn','mailBtn','copyReply','replyBtn','tDoc','mic','tPhoto','tCamera','tFull','slashBtn','send'];
 for(const id of required){const colors=stateRows.map(s=>s.rows.find(r=>r.id===id)?.effective);record(colors.every(Boolean),`${key} missing icon ${id}`);record(new Set(colors.map(JSON.stringify)).size===5,`${key} ${id} background does not follow all five states`)}
 results.push({key,state:'body-five-states',stateRows});
 await p.evaluate(()=>{document.body.dataset.status='idle';document.body.dataset.kind='permission';document.body.dataset.conn='on'});

 const unread=await p.locator('#omUnread').evaluate(e=>({v:getComputedStyle(e).visibility,n:e.dataset.n,aria:e.getAttribute('aria-disabled')}));
 record(unread.v==='hidden',`${key} empty unread visible ${JSON.stringify(unread)}`);
 // Preserve the three-column header and show the actual badge when content arrives.
 await p.evaluate(()=>{document.getElementById('om').dataset.unread='1';document.getElementById('omUnread').dataset.n='2';document.getElementById('omUnreadN').textContent='2';document.getElementById('omUnread').setAttribute('aria-disabled','false')});
 record(await p.locator('#omUnreadN').isVisible(),key+' nonempty badge hidden');
 await p.locator('.omhd').screenshot({path:`${out}/${key}-title-unread.png`});
 // Native focus, hover and disabled states, without triggering send/record/action handlers.
 for(const state of ['hover','focus','disabled']){
 for(const id of await p.locator(icons).evaluateAll(ns=>ns.filter(e=>e.id&&e.getClientRects().length&&!e.closest('[hidden]')).map(e=>e.id))){
  const loc=p.locator('#'+id);if(state==='hover')await loc.hover({force:true});
  if(state==='focus')await loc.evaluate(e=>e.focus());
  if(state==='disabled')await loc.evaluate(e=>{e.dataset.p457Disabled=String(e.disabled);e.disabled=true});
  const r=await loc.evaluate(e=>({bg:getComputedStyle(e).backgroundColor,id:e.id}));
  const a=r.bg.match(/[\d.]+/g)?.map(Number)||[];record(a.length===4&&a[3]<1,`${key} ${state} ${r.id} background ${r.bg}`);
 }
 await audit(state);
 if(state==='disabled')await p.locator('[data-p457-disabled]').evaluateAll(ns=>ns.forEach(e=>{e.disabled=e.dataset.p457Disabled==='true';delete e.dataset.p457Disabled}));
 }
 if(engine==='chromium'){
 const cdp=await ctx.newCDPSession(p);await cdp.send('DOM.enable');await cdp.send('CSS.enable');
 const {root}=await cdp.send('DOM.getDocument');const {nodeIds}=await cdp.send('DOM.querySelectorAll',{nodeId:root.nodeId,selector:icons});
 for(const nodeId of nodeIds)await cdp.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:['active']});
 await audit('active');
 for(const nodeId of nodeIds)await cdp.send('CSS.forcePseudoState',{nodeId,forcedPseudoClasses:[]});await cdp.detach();
 }
 // Real approval and sudo cards retain the existing strict label contrast contract.
 const epk=generateKeyPairSync('x25519').publicKey.export({format:'jwk'}).x;
 await fake.send({t:'elev',id:'a'.repeat(32),kind:'sudo',n:'b'.repeat(32),epk,ttl:600,tries:3,cmd:'systemctl status backup',why:'Check backup',effect:'Read status'});
 await p.waitForFunction(()=>document.getElementById('elev-allow')?.getClientRects().length>0);
 await p.screenshot({path:`${out}/${key}-sudo.png`});
 await p.evaluate(()=>import('./js/elevate.js').then(m=>m.done({id:'a'.repeat(32),result:'denied'})));
 await fake.ask({id:'c'.repeat(32),tool:'Bash',summary:'echo safe fixture',ttl:600,cat:['read']});await p.waitForFunction(()=>document.body.dataset.sheet==='1');
 await p.screenshot({path:`${out}/${key}-approval.png`});
 console.log('checked '+tag+' '+key);
 }finally{await ctx.close()}
 }}finally{await B.close()}
 }
}finally{await fake.stop();await web.stop()}
writeFileSync(`${out}/results.json`,JSON.stringify({tag,combined:JSON.parse((await import('node:fs')).readFileSync(new URL('../public/version.json',import.meta.url),'utf8')).combined,physical:false,standaloneEmulated:true,results,failures},null,2)+'\n');
console.log(JSON.stringify({tag,samples:results.length,failures:failures.length,first:failures.slice(0,8)}));
if(check)assert.deepEqual(failures,[]);
