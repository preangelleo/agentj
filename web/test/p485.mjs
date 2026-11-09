// P485: exact eleven-control scope, real DOM rendering, 8 configurations and excluded pixels.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdirSync,readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
const pw=createRequire(import.meta.url)(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const fixedNow=Date.now(),captureControl=process.argv.includes('--capture-control');let controlRun=0;
const base='4c6bb4942',approved='c97096e917',integration='519f95a0fcc4a238801e8da428320cfba4b58864',out=process.env.P485_OUT||'reports/qa/p485',rows=[],snapshots=new Map(),approvedSnapshots=new Map(),presence=new Map(),controlText=new Map(),referenceControls=new Map(),versionReceipts=[];
const ids=['quoteDlgDel','quoteDlgJump','ctm-e-del','ctm-e-save','phone-env-copy','rv-refresh','set-refresh','mail-add','rm-add','rm-copy'];
const hash=x=>createHash('sha256').update(x).digest('hex');
const at=(ref,path)=>execFileSync('git',['show',ref+':web/public/'+path],{maxBuffer:8e6}).toString();
const sourcePins=[];
const versionInput=new Map(['version.js','version.json'].map(p=>['/'+p,{body:at(approved,p),sha256:hash(at(approved,p)),source:approved}]));
// The current whole files may contain only the exact already-approved c970 -> initial 0300 integration delta.
// Never derive this allowlist from mutable HEAD or replace an image with a fresh screenshot.
function reverseInitialIntegration(text,path){
 const patch=execFileSync('git',['diff','--unified=5',approved,integration,'--','web/public/'+path],{encoding:'utf8'}),hunks=[];let h=null;
 for(const line of patch.split('\n')){
  if(line.startsWith('@@')){if(h)hunks.push(h);h={before:[],after:[]};continue;}
  if(!h)continue;if(line.startsWith('+'))h.after.push(line.slice(1));else if(line.startsWith('-'))h.before.push(line.slice(1));else if(line.startsWith(' ')){h.before.push(line.slice(1));h.after.push(line.slice(1));}
 }
 if(h)hunks.push(h);
 for(const h of hunks){const after=h.after.join('\n')+'\n',before=h.before.length?h.before.join('\n')+'\n':'';assert.ok(h.after.length,'allowed initial delta contains actual bytes');assert.equal(text.split(after).length,2,'exact unique approved initial-integration fragment '+path);text=text.replace(after,before);}
 return text;
}
const cards=readFileSync('web/public/js/jcards.js','utf8');assert.equal(cards,at(approved,'js/jcards.js'),'whole card source exactly matches chairman-reviewed 0287 source');sourcePins.push({path:'js/jcards.js',current:hash(cards),approved:hash(at(approved,'js/jcards.js'))});
for(const path of ['index.html','app.css','menu.css']){const current=readFileSync('web/public/'+path,'utf8'),reversed=reverseInitialIntegration(current,path),known=at(approved,path);assert.equal(reversed,known,'whole source is approved reference plus only exact immutable initial-0300 delta: '+path);sourcePins.push({path,current:hash(current),approved:hash(known),afterExactReverse:hash(reversed),allowedDeltaFrom:approved,allowedDeltaTo:integration});}
mkdirSync(out,{recursive:true});writeFileSync(out+'/source-pins.json',JSON.stringify({historical:base,approved,integration,sourcePins},null,2)+'\n');
if(process.argv.includes('--static-only')){console.log('STATIC_ONLY P485 immutable whole-source pins passed; no browser evidence');process.exit(0);}
const temp=mkdtempSync('/tmp/p485-base-');execFileSync('tar',['-x','-C',temp],{input:execFileSync('git',['archive',base,'web/public'],{maxBuffer:100e6})});const before=await startWebServer({publicDir:temp+'/web/public'});
const approvedTemp=mkdtempSync('/tmp/p485-approved-');execFileSync('tar',['-x','-C',approvedTemp],{input:execFileSync('git',['archive',approved,'web/public'],{maxBuffer:100e6})});const approvedWeb=await startWebServer({publicDir:approvedTemp+'/web/public'});
const web=await startWebServer(),browser=await pw.chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of captureControl?[390]:[390,1280])for(const lang of captureControl?['zh']:['zh','en'])for(const theme of captureControl?['light']:['light','dark'])for(const phase of captureControl?['approved','approved']:['before','approved','after']){
 const phaseDir=captureControl?'control-'+(++controlRun):phase;mkdirSync(out+'/'+phaseDir,{recursive:true});console.log('START',width,lang,theme,phase);const fake=await startFakeHost();const ctx=await browser.newContext({viewport:{width,height:844},colorScheme:theme,serviceWorkers:'block'}),p=await ctx.newPage();
 try{
 await p.addInitScript(now=>{const D=Date;window.Date=class extends D{constructor(...a){super(...(a.length?a:[now]))}static now(){return now}}},fixedNow);
 p.on('console',m=>{if(m.text().startsWith('P485'))console.log(m.text())});p.on('pageerror',e=>console.log('PAGEERROR',e.message));
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname))return r.abort();const v=phase!=='before'&&versionInput.get(u.pathname);if(v){versionReceipts.push({phase,path:u.pathname,source:v.source,sha256:v.sha256});return r.fulfill({status:200,contentType:u.pathname.endsWith('.js')?'text/javascript':'application/json',body:v.body})}return r.continue()});
 p.on('websocket',ws=>{ws.on('socketerror',()=>console.log('P485 websocket error'));ws.on('close',()=>console.log('P485 websocket closed'))});
 fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 fake.st.onApp=async(c,m,send)=>{if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards:['notes','todo'].map(id=>({id,name:{zh:id==='notes'?'记事本':'待办',en:id==='notes'?'Notes':'To-do'},official:true,on:true}))});return true}if(m.t==='jcard_get'){await send({t:'jcard_get',r:m.r,ok:true,id:m.id,revision:'fixture485',template:m.id==='notes'?'notes':'checklist',entries:[{id:'n1',title:'P485',text:'Notebook fixture'}],items:[]});return true}return false};
 await p.goto(fake.newPairing((phase==='before'?before:phase==='approved'?approvedWeb:web).url+'?lang='+lang));await p.waitForFunction(()=>window.__ajState==='awaiting-approval').catch(async e=>{console.log('FAKE ERRORS',fake.st.errors,'date',await p.evaluate(()=>Date.now()),Date.now());console.log('STATE',await p.evaluate(()=>({state:window.__ajState,text:document.body.innerText.slice(0,100)})));throw e});await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');
 await p.evaluate(async theme=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme=theme;const s=document.createElement('style');// Each phase has a fresh real fake-Host inventory; no badge or action is hidden for equality.
 s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s);await document.fonts.ready;},theme);
 // The original component fixture reveals ancestors. Open any approved accordion through its real summary.
 const expandDetails=async loc=>{const hs=await loc.locator('xpath=ancestor::details').elementHandles(),ordered=[];for(const h of hs)ordered.push({h,depth:await h.evaluate(e=>{let n=0;for(;e;e=e.parentElement)n++;return n})});ordered.sort((a,b)=>a.depth-b.depth);for(const {h}of ordered){if(await h.evaluate(e=>e.open))continue;const summary=await h.$(':scope > summary');assert.ok(summary,'actual details summary exists');await summary.click();assert.equal(await h.evaluate(e=>e.open),true,'trusted summary click opens actual approved accordion');}};
 const assertKeyboardActivation=async loc=>{await loc.evaluate(e=>{window.__pressed=0;e.addEventListener('click',ev=>{window.__pressed++;ev.preventDefault();ev.stopImmediatePropagation()},{capture:true,once:true})});await loc.focus();await p.keyboard.press('Enter');assert.equal(await p.evaluate(()=>window.__pressed),1);};
 const scrollOffset=(g,k)=>g.scroll[k];
 const shot=async(name,loc,excluded=false)=>{await expandDetails(loc);
  await p.mouse.move(1,1);await p.evaluate(()=>{delete document.documentElement.dataset.kbd;document.activeElement?.blur()});
  const key=`${width}-${lang}-${theme}-${name}`,path=`${phaseDir}/${key}.png`,bytes=await loc.screenshot({path:out+'/'+path});
  const geometry=await loc.evaluate(e=>({rect:e.getBoundingClientRect().toJSON(),devicePixelRatio,scroll:{x:scrollX,y:scrollY},ancestors:(()=>{const a=[];for(let x=e;x;x=x.parentElement){const c=getComputedStyle(x);a.push({tag:x.tagName,id:x.id,rect:x.getBoundingClientRect().toJSON(),scrollTop:x.scrollTop,scrollLeft:x.scrollLeft,open:x.open,transform:c.transform,font:c.font,background:c.backgroundColor,border:c.border,borderRadius:c.borderRadius})}return a})(),children:[...e.children].map(x=>{const c=getComputedStyle(x);return {id:x.id,rect:x.getBoundingClientRect().toJSON(),color:c.color,background:c.backgroundColor,border:c.border,borderRadius:c.borderRadius,transform:c.transform}})}));
  writeFileSync(out+'/'+path.replace(/\.png$/,'.geometry.json'),JSON.stringify({phase,name,geometry},null,2)+'\n');
  const item={bytes,path,geometry,sha256:hash(bytes),source:phase==='before'?base:phase==='approved'?approved:'selected-tree'};
  if(captureControl&&name==='phone-env-copy'){
   const r=geometry.rect,clip={x:Math.floor(r.x+scrollOffset(geometry,'x')),y:Math.floor(r.y+scrollOffset(geometry,'y')),width:Math.ceil(r.right+scrollOffset(geometry,'x'))-Math.floor(r.x+scrollOffset(geometry,'x')),height:Math.ceil(r.bottom+scrollOffset(geometry,'y'))-Math.floor(r.y+scrollOffset(geometry,'y'))};
   const elementAgain=await loc.screenshot({path:out+'/'+path.replace('.png','-element-again.png')}),viewport1=await p.screenshot({clip,path:out+'/'+path.replace('.png','-viewport-int1.png')}),viewport2=await p.screenshot({clip,path:out+'/'+path.replace('.png','-viewport-int2.png')});
   writeFileSync(out+'/'+path.replace('.png','-control.json'),JSON.stringify({source:approved,path,geometry,clip,rawSHA:{element:hash(bytes),elementAgain:hash(elementAgain),viewport1:hash(viewport1),viewport2:hash(viewport2)},samePage:{elementStable:bytes.equals(elementAgain),viewportStable:viewport1.equals(viewport2)}},null,2)+'\n');
  }

  if(phase==='before')snapshots.set(key,item);
  else if(phase==='approved')approvedSnapshots.set(key,item);
  else{
    const ref=approvedSnapshots.get(key);assert.ok(ref,'actual approved reference screenshot exists: '+key);
    const equal=bytes.equals(ref.bytes);if(!equal)console.log('P485_PIXEL_DIAGNOSTIC',JSON.stringify({key,current:item,approved:ref},(k,v)=>k==='bytes'?undefined:v));assert.ok(equal,'zero-pixel approved reference '+key);
    const historical=snapshots.get(key);if(!excluded){assert.ok(historical,'actual historical target screenshot exists: '+key);assert.ok(!bytes.equals(historical.bytes),'historical target still changes from pre-icon source '+key);}
    rows.push({width,lang,theme,name,before:historical?.path||null,approved:ref.path,after:path,equal,excluded,historicalChanged:historical?!bytes.equals(historical.bytes):null,sourceRefs:{historical:base,approved,current:'selected-tree'},sha256:{historical:historical?.sha256||null,approved:ref.sha256,current:item.sha256}});
  }
 };
 for(const id of ids){
  await p.evaluate(id=>{window.__reveal=[];let e=document.getElementById(id);for(;e&&e!==document.body;e=e.parentElement){window.__reveal.push([e,e.getAttribute('hidden'),e.getAttribute('style'),e.getAttribute('open')]);e.hidden=false;if(getComputedStyle(e).display==='none')e.style.display='block';}document.getElementById(id).disabled=false;},id);
  const loc=p.locator('#'+id);await expandDetails(loc);const key=`${width}-${lang}-${theme}-${id}`;if(phase==='before')controlText.set(key,(await loc.textContent()).trim());
  const g=await loc.evaluate(e=>{const r=e.getBoundingClientRect();return {width:r.width,height:r.height,text:e.textContent,label:e.getAttribute('aria-label'),title:e.title,svg:e.querySelector('svg')?.getAttribute('aria-hidden'),glyph:e.querySelector('svg')?.getBoundingClientRect().toJSON()}});
  if(phase==='approved')referenceControls.set(key,g);
  if(phase==='after'){assert.ok(controlText.get(key),'historical target genuinely had visible text: '+id);
   assert.ok(g.width>=44&&g.height>=44,JSON.stringify({id,...g}));assert.ok(g.label&&g.label===g.title);assert.equal(g.svg,'true');
   if(id==='set-refresh'||id==='rm-copy'){const ref=referenceControls.get(key);assert.ok(ref&&ref.text.trim(),'formal approved labelled control is real: '+id);assert.equal(g.text,ref.text,'visible label exactly matches actual approved reference: '+id);assert.equal(g.glyph.width,ref.glyph.width);assert.equal(g.glyph.height,ref.glyph.height);assert.equal(g.label,ref.label);assert.equal(g.title,ref.title);}
   else{assert.equal(g.text,'');assert.equal(g.glyph.width,21,id+' glyph width');assert.equal(g.glyph.height,21,id+' glyph height');}
   // Keyboard activation reaches the original button, including submit controls, without sending a refresh/control.
   await assertKeyboardActivation(loc);
  }
  // Compare approved/current after the identical real keyboard callback sequence; diagnostic controls remain explicit.
  if(!captureControl&&phase==='approved')await assertKeyboardActivation(loc);
  if(captureControl&&process.argv.includes('--capture-interaction')&&controlRun===2)await assertKeyboardActivation(loc);
  await loc.evaluate(e=>e.blur());await shot(id,loc.locator('..'));
  await p.evaluate(()=>{for(const [e,h,s,o]of window.__reveal){if(e.tagName==='DETAILS'){if(o===null)e.removeAttribute('open');else e.setAttribute('open',o)}if(h===null)e.removeAttribute('hidden');else e.setAttribute('hidden',h);if(s===null)e.removeAttribute('style');else e.setAttribute('style',s)}});
  if(captureControl&&id==='phone-env-copy')break;
 }
 if(captureControl){console.log('CAPTURE_CONTROL',phaseDir,'actual approved ref only; not gate qualification');continue;}
 await p.evaluate(()=>import('./js/jcards.js').then(m=>m.refresh()));
 for(const card of ['notes','todo']){
 await p.evaluate(id=>import('./js/five-pages.js').then(m=>m.openTarget(id)),card);await p.locator(`.jc-body[data-card="${card}"] .jc-actions`).waitFor();
 const body=p.locator(`.jc-body[data-card="${card}"]`);if(card==='notes'&&phase==='before')controlText.set(`${width}-${lang}-${theme}-notes-add`,(await body.locator('[data-act=add]').textContent()).trim());if(card==='notes'&&phase==='after'){assert.ok(controlText.get(`${width}-${lang}-${theme}-notes-add`),'historical note add had visible text');const add=body.locator('[data-act=add]');const g=await add.boundingBox();assert.ok(g.width>=44&&g.height>=44);assert.equal(await add.textContent(),'');assert.equal(await add.locator('svg path').getAttribute('d'),await p.locator('#rm-add svg path').getAttribute('d'));const glyph=await add.locator('svg').boundingBox();assert.equal(glyph.width,21);assert.equal(glyph.height,21);assert.equal(await add.getAttribute('title'),await add.getAttribute('aria-label'));assert.equal(await body.locator('[data-act=collaborate]').textContent(),await p.evaluate(()=>import('./js/t.js').then(m=>m.t('p415.collaborate'))));}
 await shot(card,body,card==='todo');
 await p.evaluate(()=>import('./js/five-pages.js').then(m=>m.go('chat')));
 }
 // Unchanged dialogs and settings pages: entire original containers, not synthesized button replicas.
 for(const id of ['offDlg','confirmDlg','rd','set-auth','set-ios','set-notify','set-hooks']){
 const count=await p.locator('#'+id).count(),key=`${width}-${lang}-${theme}-presence-${id}`;assert.ok(count<=1,'region id unique');if(phase==='approved')presence.set(key,count);if(phase==='after')assert.equal(count,presence.get(key),'all original excluded regions retained or explicitly absent on the approved reference: '+id);if(!count){rows.push({width,lang,theme,name:'excluded-'+id,phase,absent:true,source:phase==='before'?base:phase==='approved'?approved:'selected-tree'});continue;}
 await p.evaluate(id=>{window.__reveal=[];for(let e=document.getElementById(id);e&&e!==document.body;e=e.parentElement){window.__reveal.push([e,e.getAttribute('hidden'),e.getAttribute('style'),e.getAttribute('open')]);e.hidden=false;if(getComputedStyle(e).display==='none')e.style.display='block'}},id);
 await shot('excluded-'+id,p.locator('#'+id),true);
 await p.evaluate(()=>{for(const [e,h,s,o]of window.__reveal){if(e.tagName==='DETAILS'){if(o===null)e.removeAttribute('open');else e.setAttribute('open',o)}if(h===null)e.removeAttribute('hidden');else e.setAttribute('hidden',h);if(s===null)e.removeAttribute('style');else e.setAttribute('style',s)}});
 }
 console.log('PASS',width,lang,theme,phase);
 }finally{await ctx.close();await fake.stop()}
}}finally{await browser.close();await web.stop();await before.stop();await approvedWeb.stop();rmSync(temp,{recursive:true,force:true});rmSync(approvedTemp,{recursive:true,force:true})}
if(captureControl){console.log('DIAGNOSTIC_ONLY two independent actual c970 pages captured; no gate verdict');process.exit(0)}
writeFileSync(out+'/version-data-receipts.json',JSON.stringify({inputRef:approved,comparison:'approved/current component style comparison under identical complete actual version data; historical archive remains its own version',receipts:versionReceipts},null,2)+'\n');
writeFileSync(out+'/comparison.json',JSON.stringify({base,approved,integration,sourcePins,comparisonContract:'historical targets change; all current target/excluded screenshots equal actual approved reference; no masking',combined:JSON.parse(readFileSync('web/public/version.json')).combined,failures:[],rows},null,2)+'\n');
writeFileSync(out+'/index.html','<!doctype html><meta charset="utf-8"><title>P485 comparisons</title><style>body{font:16px system-ui}article{display:flex}img{max-width:48%;object-fit:contain}</style><h1>P485 before / after</h1>'+rows.map(r=>`<h2>${r.width} ${r.lang} ${r.theme} ${r.name} · equal=${r.equal}</h2><article><img loading="lazy" src="${r.before}"><img loading="lazy" src="${r.approved||''}"><img loading="lazy" src="${r.after||''}"></article>`).join(''));
console.log('PASS',rows.length,'comparisons');
