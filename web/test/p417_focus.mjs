// P417 #188 (Leo, 10-21, Mac Chrome: 「这个slash command后边输入到文字框以后，我的鼠标客设也不再输入文字框，我要自己手动点击进来」):
// on a desktop with a real mouse, picking a command from (1) the 「/」 suggestion list under / over the message box, (2) the
// ≡ 全部命令 sheet, (3) the @ mention list, (4) the full-screen editor's 「/」 list puts the words in AND leaves the keyboard focus
// in the box (or the editor's text area), caret at the end. Enter picks do the same. Real CDP mouse events (mouseMoved →
// mousePressed → mouseReleased at the centre), not element.click(). On touch (390) the P375 / P406 rule still holds: a tap on
// the ≡ sheet never raises the keyboard by script, and a pick made while the box had the focus keeps it.
// #167 (P415): the 记事本 card's 「+」 saves straight to the card (jcard_note add), never as a message to the agent — 1440 mouse
// and 390 touch once each. Shots → reports/qa/p417/focus/.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {i18nModule} from '../build.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitState,waitFor,sleep,shoot,key,tap,rect} from './browser.mjs';
const ROOT=fileURLToPath(new URL('../../../',import.meta.url));
const out=join(ROOT,'reports/qa/p417/focus/');mkdirSync(out,{recursive:true});
const TAG=process.env.P417_TAG||'after',BEFORE=TAG==='before';   // `P417_TAG=before` serves HEAD's layers.js and only records
const tmp=mkdtempSync(join(tmpdir(),'p417-focus-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
if(BEFORE)writeFileSync(join(tmp,'js/layers.js'),execFileSync('git',['show','HEAD:web/public/js/layers.js'],{cwd:ROOT}));
const MENU={source:'file',items:[{cmd:'/good-morning',desc:'Morning brief',group:'mine'}],skills:[{cmd:'agentj-add-note',desc:'Add a note'}],cmds:['clear','status']};
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch();
let checks=0;const failures=[];
const check=(ok,msg)=>{checks++;if(!ok){failures.push(msg);console.log('  ✗',msg);}};
const q=JSON.stringify;
// where the keyboard is: the box, the chip editor standing in for it (P244 / P302) or the full-screen editor's text area
const state=p=>ev(p,`(()=>{const a=document.activeElement,i=document.getElementById('input'),ed=document.getElementById('workflow-compose'),fs=document.getElementById('fsText');
 const plain=n=>n.nodeType===3?n.textContent:n.nodeType!==1?'':n.dataset?.mention||(n.tagName==='BR'?'\\n':[...n.childNodes].map(plain).join(''));
 const box=a===fs?fs:i;let caret=null;
 if(a===box)caret=box.selectionStart;
 else if(a===ed){const s=getSelection();if(s.rangeCount&&ed.contains(s.anchorNode)){const r=s.getRangeAt(0).cloneRange();r.selectNodeContents(ed);r.setEnd(s.anchorNode,s.anchorOffset);caret=[...r.cloneContents().childNodes].map(plain).join('').length;}}
 return {v:box.value,active:a?.id||a?.tagName,inBox:a===i||a===ed,inFs:a===fs,caret,len:box.value.length};})()`);
const clear=p=>ev(p,`(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));document.activeElement?.blur();})()`);
async function press(p,sel,touch){
 const r=await rect(p,sel);
 if(touch)await tap(p,r.x,r.y);
 else for(const type of ['mouseMoved','mousePressed','mouseReleased'])await p.send('Input.dispatchMouseEvent',{type,x:r.x,y:r.y,button:'left',clickCount:type==='mouseMoved'?0:1});
 await sleep(300);
}
async function pair(w,scheme,lang,onApp){
 fake.reset();fake.st.menu=MENU;fake.st.caps=['jcards1'];fake.st.onApp=onApp||(async()=>false);
 const p=await newPage(B,w,900,scheme,{touch:w<800,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();})()`);
 await p.send('Emulation.setFocusEmulationEnabled',{enabled:true});
 return p;
}
const results=[];
try{
 for(const [w,scheme,lang] of [[1440,'light','zh'],[1440,'dark','en'],[390,'light','zh'],[390,'dark','en']]){
  const tag=`${w}-${scheme}-${lang}`,touch=w<800,r={w,scheme,lang};
  const notes=lang==='zh'?'记事本':'Notebook';
  const cards=[{id:'notes',name:{zh:'记事本',en:'Notebook'},on:true,official:true,accent:'windstorm',plus:'agentj-add-note'}];
  const writes=[];let entries=[{id:'n1',text:'First note',editable:true}];
  const p=await pair(w,scheme,lang,async(c,m,send)=>{
   const reply=async(t,v)=>{await send({t,r:m.r,...v});return true;};
   if(m.t==='jcard_list')return reply('jcard_items',{cards});
   if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:m.id,template:'notes',entries,revision:'rev'});
   if(m.t==='jcard_note'){writes.push(m);if(m.op==='add')entries=[{id:'new',text:m.text,editable:true},...entries];return reply('jcard_note',{ok:true,id:m.id});}
   return false;
  });
  await ev(p,`import('./js/jcards.js').then(m=>m.refresh()).then(l=>l.length)`);
  const shot=async name=>writeFileSync(join(out,`${TAG}-${tag}-${name}.png`),await shoot(p));
  const focusBox=async()=>{if(touch)await press(p,'#input',true);else await ev(p,`document.getElementById('input').focus()`);};
  const atEnd=s=>s.caret===s.len;

  // 1. the 「/」 suggestion list: a click / tap, then Enter
  for(const how of ['click','enter']){
   await clear(p);await focusBox();await p.send('Input.insertText',{text:'/good'});
   await waitFor(p,"!document.getElementById('sug').hidden");if(how==='click')await shot('sug-list');
   if(how==='click')await press(p,'#sug [data-sug="0"]',touch);else{await key(p,'Enter');await sleep(250);}
   const s=r['sug_'+how]=await state(p);
   check(s.v==='/good-morning ',`#188 ${tag} / list ${how}: the command is in the box ${q(s.v)}`);
   check(s.inBox&&atEnd(s),`#188 ${tag} / list ${how}: focus in the box, caret at the end (${s.active} ${s.caret}/${s.len})`);
  }

  // 2. ≡ 全部命令: a click on a command row inserts it; on a desktop the box has the focus (not ≡), on touch nothing raises the
  //    keyboard by script (P375: a touch owner focuses the box only by tapping it)
  await clear(p);if(!touch)await focusBox();
  await press(p,'#slashBtn',touch);await waitFor(p,"!document.getElementById('menu').hidden");
  await ev(p,`document.querySelector('#menu [data-slash="/good-morning"]').scrollIntoView({block:'center'})`);await sleep(200);await shot('menu');
  await press(p,'#menu [data-slash="/good-morning"]',touch);await sleep(200);
  {const s=r.menu=await state(p);
   check(s.v==='/good-morning ',`#188 ${tag} ≡ menu: the command is in the box ${q(s.v)}`);
   if(!touch)check(s.inBox&&atEnd(s),`#188 ${tag} ≡ menu click: focus in the box, caret at the end (${s.active} ${s.caret}/${s.len})`);
   else check(!s.inBox,`#188 ${tag} ≡ menu tap: P375 — no keyboard raised by script (${s.active})`);}

  // 3. the @ mention list: a click / tap, then Enter
  for(const how of ['click','enter']){
   await clear(p);await focusBox();await p.send('Input.insertText',{text:'@'+notes.slice(0,2)});
   await waitFor(p,`!document.getElementById('five-mentions').hidden&&!!document.querySelector('#five-mentions [data-card="notes"]')`);
   if(how==='click')await press(p,'#five-mentions [data-card="notes"]',touch);else{await key(p,'Enter');await sleep(250);}
   const s=r['at_'+how]=await state(p);
   check(s.v.startsWith('@'+notes)&&s.v.endsWith(' '),`#188 ${tag} @ ${how}: the token is in the box ${q(s.v)}`);
   check(s.inBox&&atEnd(s),`#188 ${tag} @ ${how}: focus in the box, caret at the end (${s.active} ${s.caret}/${s.len})`);
  }

  // 4. the full-screen editor's 「/」 list (desktop: the editor's text area has the focus after the pick)
  if(!touch){
   await clear(p);await focusBox();await p.send('Input.insertText',{text:'hi'});
   await press(p,'#tFull',false);await waitFor(p,"!document.getElementById('fsEd').hidden");
   await press(p,'#fsSlash',false);await waitFor(p,"!document.getElementById('fsCmds').hidden&&!!document.querySelector('#fsCmdList [data-i]')");
   await ev(p,`(()=>{const q=document.getElementById('fsCmdQ');q.value='good';q.dispatchEvent(new Event('input',{bubbles:true}));})()`);await sleep(150);
   await shot('fs-cmds');
   await press(p,'#fsCmdList [data-i="0"]',false);
   const s=r.fs=await state(p);
   check(s.v==='hi /good-morning ',`#188 ${tag} full-screen / list: the command is in the editor ${q(s.v)}`);
   check(s.inFs&&atEnd(s),`#188 ${tag} full-screen / list click: focus in the editor, caret at the end (${s.active} ${s.caret}/${s.len})`);
   await key(p,'Escape');await sleep(200);await clear(p);
  }

  // 5. #167: 记事本 「+」 → type → save goes straight to the card, never to the agent (once per width, zh)
  if(lang==='zh'){
   await clear(p);const said=fake.st.says.length+fake.st.slashes.length;
   await ev(p,`import('./js/five-pages.js').then(m=>m.openTarget('notes'))`);await waitFor(p,`document.querySelector('.jc-body[data-card=notes] .jc-action[data-act=add]')`);
   await press(p,'.jc-body[data-card=notes] .jc-action[data-act=add]',touch);await waitFor(p,"!!document.querySelector('dialog[open] textarea')");
   if(touch)await press(p,'dialog[open] textarea',true);else await ev(p,"document.querySelector('dialog[open] textarea').focus()");
   await p.send('Input.insertText',{text:'p417 direct note'});await shot('notes-plus');
   const save=await ev(p,`(()=>{const b=[...document.querySelectorAll('dialog[open] > button')].at(-1);if(!b)return null;b.id||(b.id='p417save');return '#'+b.id;})()`);
   check(!!save,`#167 ${tag}: the dialog has a save button`);
   if(save)await press(p,save,touch);
   try{await waitFor(p,"!document.querySelector('.jc-dialog')",4000);}catch(_){}
   const wr=writes.at(-1);
   check(wr&&wr.op==='add'&&wr.id==='notes'&&wr.text==='p417 direct note',`#167 ${tag}: direct jcard_note add (${q(wr)})`);
   check(fake.st.says.length+fake.st.slashes.length===said,`#167 ${tag}: no message to the agent`);
   r.direct167=!!wr;
   await ev(p,`import('./js/five-pages.js').then(m=>m.openTarget('chat'))`).catch(()=>{});await sleep(200);
  }
  check(p.problems.length===0,`${tag}: no console errors ${p.problems.join(' | ')}`);
  results.push(r);console.log(`P417 focus ${tag} ${failures.length?'…':'PASS'}`);await p.dispose();
 }
}catch(e){failures.push(String(e&&e.stack||e));console.error(e);}
finally{await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
writeFileSync(join(out,`result-${TAG}.json`),JSON.stringify({ok:!failures.length,tag:TAG,scope:'Chromium (1440 mouse via CDP Input.dispatchMouseEvent / 390 touch emulation); no physical device, no WebKit',checks,failures,results},null,1)+'\n');
console.log(JSON.stringify({checks,failures}));
process.exit(failures.length&&!BEFORE?1:0);
