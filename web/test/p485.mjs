// P485: exact eleven-control scope, real DOM rendering, 8 configurations and excluded pixels.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const fixedNow=Date.now();
const base='4c6bb4942',out=process.env.P485_OUT||'reports/qa/p485',rows=[],snapshots=new Map();
const ids=['quoteDlgDel','quoteDlgJump','ctm-e-del','ctm-e-save','phone-env-copy','rv-refresh','set-refresh','mail-add','rm-add','rm-copy'];
const changed=['index.html','menu.css','js/jcards.js'];
const old=p=>execFileSync('git',['show',base+':web/public/'+p],{maxBuffer:8e6}).toString();
const normalize=s=>ids.reduce((s,id)=>s.replace(new RegExp('<button\\b[^>]*\\bid="'+id+'"[^>]*>[\\s\\S]*?</button>'),'<button id="'+id+'"></button>'),s);
assert.equal(reverseApprovedHTML(readFileSync('web/public/js/jcards.js','utf8'),'d2b1fd1b3','js/jcards.js').replace(/^    if \(id === 'notes'\) \{ add\.replaceChildren\(document\.querySelector\('#rm-add > svg'\)\.cloneNode\(true\)\);[^\n]*\n/m,''),old('js/jcards.js'),'all other card code byte-identical');
// P523 reverses only the exact approved P517/P518/P519 additions; P513 settings rearrangement is chairman-approved on 2026-10-25; reverse its exact source delta too.
// P489 LIVE added this exact diagnostic card after P485; all other non-icon HTML must still match.
const p489LiveCard="            <div class=\"rscard\">\n              <button type=\"button\" id=\"layout-diag-send\" class=\"aj-btn\" data-i18n=\"p489.send\">发送画面诊断</button>\n              <p class=\"setnote\" data-i18n=\"p489.note\">画面有色带或错位时点一下。只把尺寸、颜色和用量发到你的 Agent J 主机，不含聊天内容。</p>\n              <p class=\"setnote\" id=\"layout-diag-result\" role=\"status\" aria-live=\"polite\"></p>\n            </div>\n";
// P526/P527 exact approved additions; retain every historical comparator below.
let html=readFileSync('web/public/index.html','utf8');
const diagnostic='            <div class="rscard" id="layout-diag-card">';
assert.equal(html.split(diagnostic).length,2);html=html.replace(diagnostic,'            <div class="rscard">');
const jevButton='              <button type="button" id="set-jev-key" class="aj-btn" hidden></button>\n';
assert.equal(html.split(jevButton).length,2);html=html.replace(jevButton,'');
html=reverseApprovedHTML(html,'2231d6c4a');
assert.equal(html.split(p489LiveCard).length,2,'exactly one approved P489 diagnostic card');
assert.ok(execFileSync('git',['show','c3a88d122:web/public/index.html'],{encoding:'utf8'}).includes(p489LiveCard),'card is byte-pinned to the live P489 source');
// Reverse only exact HTML fragments introduced by the approved P501/P499/P500 commits.
function reverseApprovedHTML(text,ref,path="index.html"){
 const patch=execFileSync('git',['diff','--unified=5',ref+'^',ref,'--','web/public/'+path],{encoding:'utf8'});
 const hunks=[];let h=null;
 for(const line of patch.split('\n')){
  if(line.startsWith('@@')){if(h)hunks.push(h);h={before:[],after:[]};continue;}
  if(!h)continue;if(line.startsWith('+'))h.after.push(line.slice(1));else if(line.startsWith('-'))h.before.push(line.slice(1));else if(line.startsWith(' ')){h.before.push(line.slice(1));h.after.push(line.slice(1));}
 }
 if(h)hunks.push(h);
 for(const h of hunks){const after=h.after.join('\n')+'\n',before=h.before.length?h.before.join('\n')+'\n':'';assert.ok(h.after.length,'approved HTML delta must have unique new bytes');assert.equal(text.split(after).length,2,'exact approved fragment '+ref);text=text.replace(after,before);}
 return text;
}
let withoutApproved=html;
for(const ref of ['afdf19607','2a303bac2','71eb0cd29','3832375bf','850e08209'])withoutApproved=reverseApprovedHTML(withoutApproved,ref);
assert.equal(normalize(withoutApproved.replace(p489LiveCard,'')),normalize(old('index.html')),'all other HTML byte-identical outside exact approved integrations');
// The integrated CSS is byte-pinned: P488/P492 repairs plus approved P499/P501 panels.
// P485 changes no app.css. Historical baseline remains the actual pixel comparator below.
assert.equal(reverseApprovedHTML(reverseApprovedHTML(reverseApprovedHTML(readFileSync('web/public/app.css','utf8'),'2231d6c4a','app.css'),'bb8a40b8b','app.css'),'2a303bac2','app.css'),execFileSync('git',['show','9b64fe71f:web/public/app.css'],{encoding:'utf8'}),'all CSS exactly matches reviewed integration');
assert.ok(readFileSync('web/public/menu.css','utf8').startsWith(old('menu.css')),'existing menu CSS byte-identical');
const temp=mkdtempSync('/tmp/p485-base-');execFileSync('tar',['-x','-C',temp],{input:execFileSync('git',['archive',base,'web/public'],{maxBuffer:100e6})});const before=await startWebServer({publicDir:temp+'/web/public'});
const web=await startWebServer(),fake=await startFakeHost(),browser=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of [390,1280])for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const phase of ['before','after']){
 mkdirSync(out+'/'+phase,{recursive:true});console.log('START',width,lang,theme,phase);const ctx=await browser.newContext({viewport:{width,height:844},colorScheme:theme,serviceWorkers:'block'}),p=await ctx.newPage();
 try{
 await p.addInitScript(now=>{const D=Date;window.Date=class extends D{constructor(...a){super(...(a.length?a:[now]))}static now(){return now}}},fixedNow);
 p.on('console',m=>{if(m.text().startsWith('P485'))console.log(m.text())});p.on('pageerror',e=>console.log('PAGEERROR',e.message));
 await p.route('**/*',r=>['127.0.0.1','localhost'].includes(new URL(r.request().url()).hostname)?r.continue():r.abort());
 p.on('websocket',ws=>{ws.on('socketerror',()=>console.log('P485 websocket error'));ws.on('close',()=>console.log('P485 websocket closed'))});
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.st.onApp=async(c,m,send)=>{if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards:['notes','todo'].map(id=>({id,name:{zh:id==='notes'?'记事本':'待办',en:id==='notes'?'Notes':'To-do'},official:true,on:true}))});return true}if(m.t==='jcard_get'){await send({t:'jcard_get',r:m.r,ok:true,id:m.id,revision:'fixture485',template:m.id==='notes'?'notes':'checklist',entries:[{id:'n1',title:'P485',text:'Notebook fixture'}],items:[]});return true}return false};
 await p.goto(fake.newPairing((phase==='before'?before:web).url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval').catch(async e=>{console.log('FAKE ERRORS',fake.st.errors,'date',await p.evaluate(()=>Date.now()),Date.now());console.log('STATE',await p.evaluate(()=>({state:window.__ajState,text:document.body.innerText.slice(0,100)})));throw e});await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(async theme=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme=theme;const s=document.createElement('style');// Isolate the approved P517 badge before any backdrop blur; P517 has independent approved-image checks.
 s.textContent='.remote-count-badge{visibility:hidden!important}*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);await document.fonts.ready;},theme);
 const shot=async(name,loc,equal=false)=>{await p.mouse.move(1,1);await p.evaluate(()=>{delete document.documentElement.dataset.kbd;document.activeElement?.blur()});const key=`${width}-${lang}-${theme}-${name}`,path=`${phase}/${key}.png`,bytes=await loc.screenshot({path:out+'/'+path});if(phase==='before')snapshots.set(key,{bytes,path});else{const b=snapshots.get(key);const same=bytes.equals(b.bytes);if(equal)assert.ok(same,'excluded pixels '+key);else assert.ok(!same,'target changed '+key);rows.push({width,lang,theme,name,before:b.path,after:path,equal:same,excluded:equal})}};
 for(const id of ids){
  await p.evaluate(id=>{window.__reveal=[];let e=document.getElementById(id);for(;e&&e!==document.body;e=e.parentElement){window.__reveal.push([e,e.getAttribute('hidden'),e.getAttribute('style')]);e.hidden=false;if(getComputedStyle(e).display==='none')e.style.display='block';}document.getElementById(id).disabled=false;},id);
  const loc=p.locator('#'+id);if(phase==='after'){
   const g=await loc.evaluate(e=>{const r=e.getBoundingClientRect();return {width:r.width,height:r.height,text:e.textContent,label:e.getAttribute('aria-label'),title:e.title,svg:e.querySelector('svg')?.getAttribute('aria-hidden'),glyph:e.querySelector('svg')?.getBoundingClientRect().toJSON()}});
   assert.ok(g.width>=44&&g.height>=44,JSON.stringify({id,...g}));assert.equal(g.text,'');assert.ok(g.label&&g.label===g.title);assert.equal(g.svg,'true');assert.equal(g.glyph.width,21,id+' glyph width');assert.equal(g.glyph.height,21,id+' glyph height');
   // Keyboard activation reaches the original button, including submit controls, without sending a refresh/control.
   await loc.evaluate(e=>{window.__pressed=0;e.addEventListener('click',ev=>{window.__pressed++;ev.preventDefault();ev.stopImmediatePropagation()},{capture:true,once:true})});await loc.focus();await p.keyboard.press('Enter');assert.equal(await p.evaluate(()=>window.__pressed),1);
  }
  await loc.evaluate(e=>e.blur());await shot(id,loc.locator('..'));
  await p.evaluate(()=>{for(const [e,h,s]of window.__reveal){if(h===null)e.removeAttribute('hidden');else e.setAttribute('hidden',h);if(s===null)e.removeAttribute('style');else e.setAttribute('style',s)}});
 }
 await p.evaluate(()=>import('./js/jcards.js').then(m=>m.refresh()));
 for(const card of ['notes','todo']){
 await p.evaluate(id=>import('./js/five-pages.js').then(m=>m.openTarget(id)),card);await p.locator(`.jc-body[data-card="${card}"] .jc-actions`).waitFor();
 const body=p.locator(`.jc-body[data-card="${card}"]`);if(card==='notes'&&phase==='after'){const add=body.locator('[data-act=add]');const g=await add.boundingBox();assert.ok(g.width>=44&&g.height>=44);assert.equal(await add.textContent(),'');assert.equal(await add.locator('svg path').getAttribute('d'),await p.locator('#rm-add svg path').getAttribute('d'));const glyph=await add.locator('svg').boundingBox();assert.equal(glyph.width,21);assert.equal(glyph.height,21);assert.equal(await add.getAttribute('title'),await add.getAttribute('aria-label'));assert.equal(await body.locator('[data-act=collaborate]').textContent(),await p.evaluate(()=>import('./js/t.js').then(m=>m.t('p415.collaborate'))));}
 await shot(card,body,card==='todo');
 await p.evaluate(()=>import('./js/five-pages.js').then(m=>m.go('chat')));
 }
 // Unchanged dialogs and settings pages: entire original containers, not synthesized button replicas.
 for(const id of ['offDlg','confirmDlg','rd','set-auth','set-ios','set-notify','set-hooks']){
 if(!await p.locator('#'+id).count())continue;
 await p.evaluate(id=>{window.__reveal=[];for(let e=document.getElementById(id);e&&e!==document.body;e=e.parentElement){window.__reveal.push([e,e.getAttribute('hidden'),e.getAttribute('style')]);e.hidden=false;if(getComputedStyle(e).display==='none')e.style.display='block'}},id);
 await shot('excluded-'+id,p.locator('#'+id),true);
 await p.evaluate(()=>{for(const [e,h,s]of window.__reveal){if(h===null)e.removeAttribute('hidden');else e.setAttribute('hidden',h);if(s===null)e.removeAttribute('style');else e.setAttribute('style',s)}});
 }
 console.log('PASS',width,lang,theme,phase);
 }finally{await ctx.close()}
}}finally{await browser.close();await fake.stop();await web.stop();await before.stop();rmSync(temp,{recursive:true,force:true})}
writeFileSync(out+'/comparison.json',JSON.stringify({base,combined:JSON.parse(readFileSync('web/public/version.json')).combined,failures:[],rows},null,2)+'\n');
writeFileSync(out+'/index.html','<!doctype html><meta charset="utf-8"><title>P485 comparisons</title><style>body{font:16px system-ui}article{display:flex}img{max-width:48%;object-fit:contain}</style><h1>P485 before / after</h1>'+rows.map(r=>`<h2>${r.width} ${r.lang} ${r.theme} ${r.name} · equal=${r.equal}</h2><article><img loading="lazy" src="${r.before}"><img loading="lazy" src="${r.after}"></article>`).join(''));
console.log('PASS',rows.length,'comparisons');
