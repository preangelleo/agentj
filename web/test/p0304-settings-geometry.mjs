#!/usr/bin/env node
// Rendered Settings geometry: --baseline captures fixed e3353f0d03; default checks the current source.
import assert from 'node:assert/strict';
import {mkdirSync,mkdtempSync,rmSync,writeFileSync,cpSync} from 'node:fs';
import {homedir} from 'node:os';
import {join,resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,sleep} from './browser.mjs';
import {SETTINGS_CARD_GAPS} from './settings-card-spacing.mjs';
const baseline=process.argv.includes('--baseline');
const headCss=process.argv.includes('--head-css'); // Same current client, but app.css from HEAD in an isolated static fixture.
const root=resolve(headCss||process.argv.includes('--select-fix')?'.jarvis/out/0305-integrate':'.jarvis/out/0304-settings-ui',headCss?'before':baseline?'before':'after');
const pages=[['main',null,'set-main'],['mail','set-mail-nav','set-mail'],['auth','set-auth-nav','set-auth'],['accts','set-accts-nav','set-accts'],['files','set-local-files-nav','set-local-files'],['imessage','set-imsg-nav','set-imessage'],['cards','set-cards-nav','set-cards'],['notify','set-notify-nav','set-notify'],['hooks','set-hooks-nav','set-hooks'],['contacts','set-contacts-nav','set-contacts'],['remotes','set-remotes-open','set-remotes'],['ios','set-a2hs','set-ios']];
let archiveDir,web,fake,B;
const rows=[];let failures=0;
const shot=async(p,path)=>{mkdirSync(root,{recursive:true});await sleep(80);const {data}=await p.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});writeFileSync(path,Buffer.from(data,'base64'));};
const geometry=`(()=>{const root=document.querySelector(SCOPE),visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';const box=e=>e.getBoundingClientRect();const viewport=innerWidth;const overflow=[...root.querySelectorAll('input,select,textarea,button,.setcard,.rscard,.mailadd')].filter(visible).map(e=>({id:e.id||e.className||e.tagName,left:box(e).left,right:box(e).right})).filter(x=>x.left < -1||x.right>viewport+1);const pairs=[];for(const parent of [root,...root.querySelectorAll('*')]){const children=[...parent.children].filter(visible);for(let i=0;i<children.length-1;i++){const a=children[i];if(!a.matches('.setnote'))continue;const b=children[i+1];if(!b.matches('.setcard,.rscard,.setrow,.setnav,.setg,.mailf,.mailadv,.aj-action,.setlinks,.setcmd,.setacts,input,select,textarea,button,.mail-triage-card')&&!b.querySelector('input,select,textarea,button,.setcard,.rscard'))continue;const x=box(a),y=box(b);if(Math.min(x.right,y.right)-Math.max(x.left,y.left)<1)continue;pairs.push({from:a.id||a.className||a.tagName,to:b.id||b.className||b.tagName,gap:Math.round((y.top-x.bottom)*100)/100});}}const probe=document.createElement('i');probe.style.cssText='display:block;height:var(--set-paragraph-gap)';root.append(probe);const token=box(probe).height;probe.remove();return{scrollWidth:document.documentElement.scrollWidth,scopeWidth:root.scrollWidth,scopeClient:root.clientWidth,overflow,pairs,token}})()`;
const measure=(p,selector)=>evaluate(p,geometry.replace('SCOPE',JSON.stringify(selector)));
// Assert label column size AND actual line breaks: overflow alone misses a select crushing English words.
const selectLabels=`(()=>[...document.querySelectorAll('#set-main .setrow:has(> select)')].filter(row=>row.getClientRects().length&&getComputedStyle(row).visibility!=='hidden').map(row=>{const label=row.querySelector(':scope > .setlab');const select=row.querySelector(':scope > select');if(!label||!select)return null;const text=label.textContent.trim();const range=document.createRange();const words=[...text.matchAll(/\\S+/g)].map(m=>{range.setStart(label.firstChild,m.index);range.setEnd(label.firstChild,m.index+m[0].length);return {word:m[0],lines:range.getClientRects().length}});const b=label.getBoundingClientRect(),s=select.getBoundingClientRect();const canvas=document.createElement('canvas');const ctx=canvas.getContext('2d');ctx.font=getComputedStyle(label).font;const longest=Math.max(0,...words.map(w=>ctx.measureText(w.word).width));return {id:row.id||select.id,labelWidth:b.width,longest,words,selectTop:s.top,labelBottom:b.bottom,gridColumns:getComputedStyle(row).gridTemplateColumns};}).filter(Boolean))()`;
try{
 let publicDir;
 if(baseline){
  const cache=join(homedir(),'.cache','p0304');
  mkdirSync(cache,{recursive:true});
  archiveDir=mkdtempSync(join(cache,'baseline-'));
  const tar=execFileSync('git',['archive','--format=tar','e3353f0d03','web/public'],{maxBuffer:64*1024*1024});
  execFileSync('tar',['-xf','-','-C',archiveDir],{input:tar});
  publicDir=join(archiveDir,'web/public/');
 }else if(headCss){
  const cache=join(homedir(),'.cache','p0304');mkdirSync(cache,{recursive:true});archiveDir=mkdtempSync(join(cache,'head-css-'));
  publicDir=join(archiveDir,'public');cpSync(resolve('web/public'),publicDir,{recursive:true});
  writeFileSync(join(publicDir,'app.css'),execFileSync('git',['show','HEAD:web/public/app.css']));
 }
 web=await startWebServer({publicDir});fake=await startFakeHost();B=await launch();
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
 const tag=`${width}-${theme}-${lang}`;fake.reset();fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
 // Each tab gets an isolated browser context, so no real HOME/XDG state is loaded.
 const p=await newPage(B,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await fake.send(theme==='light'?{t:'theme',v:1,id:'qa-light',colors:{menu:{background:'#faf9f5',panel:'#f0eee6',text:'#141414',accent:'#141414',line:'#e3dacc'}}}:{t:'theme',v:1,factory:'robots'});
 await waitFor(p,`getComputedStyle(document.documentElement).getPropertyValue('--menu-bg').trim()==='${theme==='light'?'#faf9f5':'#2c2b26'}'`);
 await evaluate(p,`(async()=>{for(const id of ['phone-env-dismiss','a2hs-ok'])document.getElementById(id)?.click();document.documentElement.dataset.theme=${JSON.stringify(theme)};await document.fonts.ready;document.getElementById('setBtn').click()})()`);
 await waitFor(p,`!document.getElementById('settings').hidden`);
 // Fake host does not advertise the new retention preference yet; expose its real markup solely for layout measurement.
 await evaluate(p,`document.getElementById('set-voice-keep-row').hidden=false`);
 const record={tag,pages:{},problems:[]};rows.push(record);
 for(const [name,nav,id] of pages){if(nav){if(name!=='mail')await evaluate(p,`document.getElementById('set-back').click()`);await waitFor(p,`!document.getElementById('set-main').hidden`);if(name==='ios')await evaluate(p,`document.getElementById('set-a2hs').dataset.kind='ios'`);
 // Hidden capability links still get clicked: the page itself is the subject of the geometry test.
 await evaluate(p,`document.getElementById(${JSON.stringify(nav)}).click()`);await waitFor(p,`!document.getElementById(${JSON.stringify(id)}).hidden`);}
 if(name==='mail')await evaluate(p,`(()=>{document.getElementById('mail-allow-card').open=true;document.getElementById('mail-custom').open=true})()`);
 await sleep(120);const v=await measure(p,'#'+id);record.pages[name]=v;
 await shot(p,`${root}/${tag}-${name}.png`);
 if(width===390&&(v.scrollWidth>width+1||v.overflow.length))record.problems.push(`${name}: overflow ${v.scrollWidth} ${JSON.stringify(v.overflow)}`);
 {const bad=v.pairs.filter(x=>x.gap+0.5<v.token);if(bad.length)record.problems.push(`${name}: paragraph gap < ${v.token}: ${JSON.stringify(bad)}`);}
 if(name==='main'){const labels=await evaluate(p,selectLabels);record.selectLabels=labels;
 if(width===390){const bad=labels.filter(x=>x.words.some(w=>w.lines>1)||x.labelWidth+1<x.longest||x.selectTop<x.labelBottom-1);if(bad.length)record.problems.push('select label squeezed or not stacked '+JSON.stringify(bad));}
 const cards=await evaluate(p,SETTINGS_CARD_GAPS);record.cards=cards;if(cards.violations.length)record.problems.push('main card gap '+JSON.stringify(cards.violations));
 // The frozen pre-fix proof required 24px between top-level sections on a 390px phone.
 // Its --ui-space-control probe is only 12px, so it cannot detect the original 16px defect.
 const sectionFloor=baseline&&width===390?24:cards.floor;
 const section=cards.pairs.filter(x=>x.from.startsWith('set-s-')&&x.to.startsWith('set-s-')&&x.gap+0.5<sectionFloor);if(section.length)record.problems.push(`settings section spacing <${sectionFloor}px `+JSON.stringify(section));}
 }
 // Negative controls exercise the same measurement and predicate, then restore inline styles.
 if(tag==='390-light-zh'&&!baseline){await evaluate(p,`document.getElementById('set-back').click()`);await evaluate(p,`document.getElementById('set-mail-nav').click()`);const before=await measure(p,'#set-mail');await evaluate(p,`document.getElementById('mail-trusted-status').style.marginTop='-30px'`);const after=await measure(p,'#set-mail');record.negative={before:before.pairs.find(x=>x.to==='mail-trusted-status')?.gap,after:after.pairs.find(x=>x.to==='mail-trusted-status')?.gap,triggered:after.pairs.some(x=>x.to==='mail-trusted-status'&&x.gap<before.pairs.find(y=>y.to==='mail-trusted-status').gap-20)};assert.ok(record.negative.triggered,'negative margin triggers paragraph detector');await evaluate(p,`document.getElementById('mail-trusted-status').style.removeProperty('margin-top')`);
 await evaluate(p,`document.getElementById('set-back').click()`);const bad=await evaluate(p,`(()=>{const e=document.getElementById('set-s-rescue');e.style.marginTop='-30px';return true})()`);const c=await evaluate(p,SETTINGS_CARD_GAPS);assert.ok(bad&&c.violations.length,'negative margin triggers card detector');await evaluate(p,`document.getElementById('set-s-rescue').style.removeProperty('margin-top')`);}
 record.console=p.problems;record.offsite=p.offsite;if(p.problems.length||p.offsite.length)record.problems.push('browser '+JSON.stringify({problems:p.problems,offsite:p.offsite}));if(record.problems.length)failures++;console.log(tag,record.problems.length?'RED '+record.problems.join('; '):'PASS');
 }catch(e){failures++;rows.push({tag,error:String(e),problems:p.problems});console.error(tag,String(e));}finally{await p.dispose();}
}}finally{try{mkdirSync(root,{recursive:true});writeFileSync(`${root}/browser.json`,JSON.stringify({source:baseline?'frozen HEAD e3353f0d03':'current worktree',rows,failures},null,2)+'\n');await B?.close();await fake?.stop();await web?.stop();}finally{if(archiveDir)rmSync(archiveDir,{recursive:true,force:true});}}
assert.equal(failures,0,`${failures} geometry scenarios red; see browser.json`);
