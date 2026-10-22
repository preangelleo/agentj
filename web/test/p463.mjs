// P463: local paired fixtures; real browser clicks; P458 before/after, notes pixel equality.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)('/tmp/aj-webmcp-playwright/node_modules/playwright');
const out='reports/qa/p463',base='e96cc19ae',results=[],unchanged=new Map(),heights=new Map();
const web=await startWebServer(),fake=await startFakeHost(),B=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of [390,1280])for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const which of ['before','after']){
 const key=`${width}-${lang}-${theme}`;mkdirSync(`${out}/${which}`,{recursive:true});
 const ctx=await B.newContext({viewport:{width,height:844},colorScheme:theme});const p=await ctx.newPage();p.setDefaultTimeout(10000);
 const mutations=[];const text=lang==='zh'?'整理本周项目进展并准备下一次讨论需要的所有资料':'Review this week’s project progress and prepare all materials needed for the next discussion';
 let items=[{id:'t1',text,owner:'agent',status:'pending',note:lang==='zh'?'和主人一起检查细节':'Review details with the owner',end:{kind:'at',at:1792656000,date:true},acts:[{id:'done',label:'Done',draft:'mark done'}]},{id:'t2',text:'https://example.invalid/'+ 'long'.repeat(30),owner:'owner',status:'done',done:true,editable:false}];
 try{
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();if(which==='before'&&['/cards.css','/js/jcards.js'].includes(u.pathname))return r.fulfill({status:200,contentType:u.pathname.endsWith('.css')?'text/css':'text/javascript',body:execFileSync('git',['show',base+':web/public'+u.pathname])});return r.continue()});
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.st.onApp=async(c,m,send)=>{const reply=async(t,v)=>{await send({t,r:m.r,...v});return true;};
 if(m.t==='jcard_list')return reply('jcard_items',{cards:['notes','todo'].map(id=>({id,name:{zh:id==='todo'?'待办':'记事本',en:id==='todo'?'To-do':'Notes'},on:true,official:true,accent:'windstorm'}))});
 if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:m.id,revision:'rev463',template:m.id==='notes'?'notes':'checklist',entries:[{id:'n1',text:'Notebook fixture / 记事本测试'}],items,todo:m.id==='todo'?{counts:{all:2,agent:1,owner:1}}:undefined});
 if(m.t==='jcard_note'){mutations.push(m);if(m.op==='edit')items=items.map(x=>x.id===m.entry?{...x,text:m.text}:x);if(m.op==='delete')items=items.filter(x=>x.id!==m.entry);return reply('jcard_note',{ok:true});}return false;};
 await p.goto(fake.newPairing(web.url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
 await p.evaluate(theme=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme=theme;const s=document.createElement('style');s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)},theme);
 await p.evaluate(()=>import('./js/jcards.js').then(m=>m.refresh()));
 for(const surface of ['notes','todo']){
 await p.evaluate(id=>import('./js/five-pages.js').then(m=>m.openTarget(id)),surface);await p.locator(`.jc-body[data-card=${surface}]`).waitFor();await p.waitForTimeout(150);
 const shot=await p.locator('#five-page').screenshot({path:`${out}/${which}/${key}-${surface}.png`});
 if(surface==='notes'){if(which==='before')unchanged.set(key,shot);else assert.ok(shot.equals(unchanged.get(key)),'notes pixel equality '+key);continue;}
 const rowHeights=await p.locator('.jc-item').evaluateAll(rows=>rows.map(e=>e.getBoundingClientRect().height));
 if(which==='before')heights.set(key,rowHeights);
 if(which==='after'){
 rowHeights.forEach((h,i)=>assert.ok(h<=heights.get(key)[i]+1,'no extra row height '+key+' row '+i+': '+h+' vs '+heights.get(key)[i]));
 const geometry=await p.locator('.jc-item').first().evaluate(li=>{const r=e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,right:b.right,bottom:b.bottom,width:b.width,height:b.height}};const main=li.querySelector('.jc-row'),text=li.querySelector('.jc-text'),a=[...li.querySelectorAll('[data-todo-op]')];return {li:r(li),main:r(main),text:r(text),buttons:a.map(e=>({op:e.dataset.todoOp,rect:r(e),label:e.getAttribute('aria-label'),title:e.title,words:e.textContent,bg:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderWidth,svg:getComputedStyle(e.querySelector('svg')).width})),overflow:li.scrollWidth>li.clientWidth}});
 assert.ok(!geometry.overflow);assert.ok(Math.abs(geometry.main.right-geometry.li.right)<2);assert.ok(geometry.text.width>geometry.li.width-70);
 for(const b of geometry.buttons){assert.ok(b.rect.width>=40&&b.rect.height>=40);assert.ok(b.rect.y>=geometry.text.bottom);assert.ok(b.label&&b.label===b.title);assert.equal(b.words,'');assert.equal(b.border,'0px');assert.equal(b.bg,'rgba(0, 0, 0, 0)');assert.equal(b.svg,'21px');assert.ok(b.rect.bottom<=geometry.li.bottom+1)}
 assert.ok(Math.abs(geometry.buttons[1].rect.right-geometry.li.right)<2);assert.ok(await p.locator('[data-item=t2] [data-todo-op=edit]').isDisabled());
 const edit=p.locator('[data-item=t1] [data-todo-op=edit]');await edit.focus();await p.keyboard.press('Enter');await p.locator('.jc-dialog textarea').waitFor();await p.locator('.jc-dialog textarea').fill('Edited P463');await p.keyboard.press('Control+Enter');await p.locator('.jc-dialog').waitFor({state:'detached'});await p.waitForFunction(()=>document.querySelector('[data-item=t1] .jc-text')?.textContent==='Edited P463');
 assert.equal(mutations.at(-1).revision,'rev463');assert.equal(mutations.at(-1).op,'edit');
 const del=()=>p.locator('[data-item=t1] [data-todo-op=delete]');await del().click();await p.locator('#confirm-no').click();assert.equal(mutations.length,1);await del().click();await p.locator('#confirm-yes').click();await p.locator('[data-item=t1]').waitFor({state:'detached'});assert.equal(mutations.at(-1).op,'delete');assert.equal(mutations.at(-1).entry,'t1');
 results.push({key,pass:true,notesPixelEqual:true,geometry});
 }
 }
 console.log('PASS',key,which);
 }finally{await ctx.close()}
}}finally{await B.close();await fake.stop();await web.stop()}
writeFileSync(out+'/results.json',JSON.stringify({base,combined:JSON.parse(readFileSync('web/public/version.json')).combined,failures:[],results},null,2)+'\n');
