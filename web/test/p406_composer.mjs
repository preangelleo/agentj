// P406 · real Chromium + real Noise to the local fake host, 390 (touch) + 1440 (mouse) × light (zh) / dark (en).
// #163 (Leo: 「消息只要发送出去了，鼠标 Cursor 就得出去了。消息还没发送出去，输入框里还有内容，鼠标是不能出去的」):
//   picking a /command or an @ mention — Enter or a click / tap — keeps the focus in the composer, the token followed by exactly
//   one space (an existing one is reused) and the caret after it; the next Enter sends, and only that send releases the focus.
//   An Enter with nothing to send leaves the focus where it is.
// #168 (Leo: 「只要输入框里边有文字……就一直让它往里贴，只要总长度不超就行。输入框里没文字……超过多少，就给它进附件」):
//   words in the box + a short 3-line address → into the field (never an attachment); an empty box: 2 500 characters → an
//   attachment, 300 characters on many lines (taller than the field) → the field; near the single-message limit, a paste that
//   crosses it becomes the attachment with a short note, the words already there kept. ⌘V with no field focused follows suit.
// Shots → reports/qa/p406/composer/ (`P406_TAG=before` serves HEAD's composer modules and only records what it sees).
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,cpSync,mkdtempSync,rmSync,readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {i18nModule} from '../build.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
import {startWebServer,PUBLIC_DIR} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate as ev,navigate,waitState,waitFor,sleep,shoot,key,tap,rect} from './browser.mjs';
const ROOT=fileURLToPath(new URL('../../../',import.meta.url));
const out=join(ROOT,'reports/qa/p406/composer/');mkdirSync(out,{recursive:true});
const TAG=process.env.P406_TAG||'after',BEFORE=TAG==='before';
const tmp=mkdtempSync(join(tmpdir(),'p406-composer-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
if(BEFORE)for(const f of ['js/relay.js','js/five-pages.js','js/workflow-compose.js'])
 writeFileSync(join(tmp,f),execFileSync('git',['show','HEAD:web/public/'+f],{cwd:ROOT,maxBuffer:64<<20}));
const FX=JSON.parse(readFileSync(join(ROOT,'host/tests/fixtures/p360_cards.json'),'utf8'));
const MENU={source:'file',items:[{cmd:'/good-morning',desc:'Morning brief',group:'mine'}],skills:[{cmd:'agentj-add-note',desc:'Add a note'}],cmds:['clear','status']};
const rec=parityRecorder('web/test/p406_composer.mjs');let passed=false;
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[],seen=[];
const check=(ok,msg)=>{if(BEFORE){seen.push({ok:!!ok,msg});return;}assert.ok(ok,msg);};
const q=JSON.stringify;
// the composer = the field, or the chip editor that stands in for it once a /command or @ token is in it (P244 / P302)
const state=p=>ev(p,`(()=>{const i=document.getElementById('input'),ed=document.getElementById('workflow-compose'),a=document.activeElement;
 const plain=n=>n.nodeType===3?n.textContent:n.nodeType!==1?'':n.dataset?.mention||(n.tagName==='BR'?'\\n':[...n.childNodes].map(plain).join(''));
 let caret=null;if(a===i)caret=i.selectionStart;else if(a===ed){const s=getSelection();if(s.rangeCount&&ed.contains(s.anchorNode)){const r=s.getRangeAt(0).cloneRange();r.selectNodeContents(ed);r.setEnd(s.anchorNode,s.anchorOffset);caret=[...r.cloneContents().childNodes].map(plain).join('').length;}}
 return {v:i.value,focus:a===i||a===ed,active:a?.id||a?.tagName,caret,chips:document.getElementById('tray').querySelectorAll('[role=listitem]').length,toast:document.getElementById('toast').textContent};})()`);
const clear=p=>ev(p,`(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));document.activeElement?.blur();})()`);
const clipText=(p,s)=>ev(p,`navigator.clipboard.writeText(${q(s)}).then(()=>1)`);
async function ctrlV(p){
 const base={key:'v',code:'KeyV',windowsVirtualKeyCode:86,modifiers:2};
 await p.send('Input.dispatchKeyEvent',{type:'rawKeyDown',...base,commands:['paste']});
 await p.send('Input.dispatchKeyEvent',{type:'keyUp',...base});await sleep(250);
}
// a real tap (touch) or a real mouse click (desktop) — the browser's own focus handling, not element.click()
async function press(p,sel,touch){
 const r=await rect(p,sel);
 if(touch){await tap(p,r.x,r.y);}
 else{for(const type of ['mouseMoved','mousePressed','mouseReleased'])await p.send('Input.dispatchMouseEvent',{type,x:r.x,y:r.y,button:'left',clickCount:1});}
 await sleep(250);
}
const focusBox=async(p,touch)=>{if(touch)await press(p,'#input',true);else await ev(p,`document.getElementById('input').focus()`);};
async function pair(w,scheme,lang){
 fake.reset();fake.st.menu=MENU;fake.st.caps=['jcards1'];
 const cards=JSON.parse(JSON.stringify(FX.list.cards));
 fake.st.onApp=async(c,m,send)=>{if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards});return true;}return false;};
 const p=await newPage(B,w,900,scheme,{touch:w<800,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();})()`);
 await p.send('Emulation.setFocusEmulationEnabled',{enabled:true});
 await ev(p,`import('./js/jcards.js').then(m=>m.refresh()).then(l=>l.length)`);
 return p;
}
async function sendsAndReleases(p,tag,what){
 const n=fake.st.says.length+fake.st.slashes.length;
 await key(p,'Enter');
 try{await waitFor(p,`document.getElementById('input').value===''`,4000);}catch(_){}
 await sleep(200);const s=await state(p);
 check(fake.st.says.length+fake.st.slashes.length===n+1,`#163 ${tag} ${what}: the next Enter sends (${fake.st.says.length+fake.st.slashes.length - n})`);
 check(!s.focus,`#163 ${tag} ${what}: only the send releases the composer (${s.active})`);
 return s;
}
try{
 for(const [w,scheme,lang] of [[390,'light','zh'],[390,'dark','en'],[1440,'light','zh'],[1440,'dark','en']]){
  const tag=`${w}-${scheme}`,touch=w<800,p=await pair(w,scheme,lang),r={w,scheme,lang};
  const shot=async name=>writeFileSync(join(out,`${TAG}-${tag}-${name}.png`),await shoot(p));
  const notes=lang==='zh'?'记事本':'Notebook',tok=`@${notes}（card/notes）`,pre=lang==='zh'?'看看 ':'see ';

  // 0. an Enter with nothing to send never takes the cursor out
  await clear(p);await focusBox(p,touch);const n0=fake.st.says.length;await key(p,'Enter');await sleep(200);
  r.emptyEnter=await state(p);check(r.emptyEnter.focus&&fake.st.says.length===n0,`#163 ${tag}: Enter on an empty box keeps the focus (${r.emptyEnter.active})`);

  // 1. /good → /good-morning picked with Enter
  await clear(p);await focusBox(p,touch);await p.send('Input.insertText',{text:'/good'});
  await waitFor(p,"!document.getElementById('sug').hidden");await shot('slash-list');
  await key(p,'Enter');await sleep(250);let s=r.slashEnter=await state(p);await shot('slash-enter-picked');
  check(s.v==='/good-morning ',`#163 ${tag} slash Enter: one trailing space ${q(s.v)}`);
  check(s.focus,`#163 ${tag} slash Enter: the composer keeps the focus (${s.active})`);
  check(s.caret===s.v.length,`#163 ${tag} slash Enter: the caret after the space (${s.caret}/${s.v.length})`);
  r.slashEnterSent=await sendsAndReleases(p,tag,'slash Enter');await shot('slash-enter-sent');

  // 2. the same picked with a click / tap on the list
  await clear(p);await focusBox(p,touch);await p.send('Input.insertText',{text:'/good'});
  await waitFor(p,"!document.getElementById('sug').hidden");
  await press(p,'#sug [data-sug="0"]',touch);s=r.slashClick=await state(p);
  check(s.v==='/good-morning ',`#163 ${tag} slash click: one trailing space ${q(s.v)}`);
  check(s.focus&&s.caret===s.v.length,`#163 ${tag} slash click: focus kept, caret at the end (${s.active} ${s.caret})`);
  r.slashClickSent=await sendsAndReleases(p,tag,'slash click');

  // 3. an @ card picked with Enter, then with a click / tap
  for(const how of ['enter','click']){
   await clear(p);await focusBox(p,touch);await p.send('Input.insertText',{text:pre+'@'+notes.slice(0,2)});
   await waitFor(p,`!document.getElementById('five-mentions').hidden&&!!document.querySelector('#five-mentions [data-card="notes"]')`);
   if(how==='enter'){await shot('mention-list');await key(p,'Enter');await sleep(250);}else await press(p,'#five-mentions [data-card="notes"]',touch);
   s=r['mention_'+how]=await state(p);if(how==='enter')await shot('mention-picked');
   check(s.v===pre+tok+' ',`#163 ${tag} @ ${how}: the token + one space ${q(s.v)}`);
   check(s.focus&&s.caret===s.v.length,`#163 ${tag} @ ${how}: focus kept, caret after the space (${s.active} ${s.caret}/${s.v.length})`);
   r['mention_'+how+'_sent']=await sendsAndReleases(p,tag,'@ '+how);
  }

  // 4. a space already after the caret is reused, never doubled; the caret lands after it
  await clear(p);await focusBox(p,touch);const tail=lang==='zh'?' 后面的话':' and more';
  await ev(p,`(()=>{const i=document.getElementById('input');i.value=${q(pre+'@'+notes.slice(0,2)+tail)};const a=${(pre+'@'+notes.slice(0,2)).length};i.setSelectionRange(a,a);i.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  await waitFor(p,`!document.getElementById('five-mentions').hidden&&!!document.querySelector('#five-mentions [data-card="notes"]')`);
  await key(p,'Enter');await sleep(250);s=r.mentionSpace=await state(p);
  check(s.v===pre+tok+tail,`#163 ${tag} @ before an existing space: no second space ${q(s.v)}`);
  check(s.focus&&s.caret===(pre+tok+' ').length,`#163 ${tag} @ before an existing space: caret after that space (${s.active} ${s.caret})`);
  r.mentionSpaceSent=await sendsAndReleases(p,tag,'@ mid-text');

  // 5. #168 a /command already in the box + a 3-line address → into the field
  const addr=lang==='zh'?'东京都港区六本木六丁目 10-1\n六本木新城森大厦 32 层 Agent J 收件处\n王先生 收 电话 090-1234-5678，工作日 9:00-18:00 都可以送':'6-10-1 Roppongi, Minato-ku, Tokyo 106-6108\nRoppongi Hills Mori Tower, 32nd floor\nAttn: Mr. Wang, weekdays 9:00-18:00';
  await clear(p);await focusBox(p,touch);await p.send('Input.insertText',{text:'/agentj-add-note '});await sleep(200);
  let c0=(await state(p)).chips;await clipText(p,addr);await ctrlV(p);s=r.addr=await state(p);await shot('paste-address');
  check(s.v==='/agentj-add-note '+addr,`#168 ${tag}: the address joins the /command in the field ${q(s.v.slice(0,40))}`);
  check(s.chips===c0,`#168 ${tag}: a short paste after words is never an attachment (${c0}→${s.chips})`);
  check(s.focus,`#168 ${tag}: the composer keeps the focus after the paste (${s.active})`);

  // 6. #168 empty box + 300 characters on many lines (taller than the field) → the field, which scrolls
  const tall=Array.from({length:30},(_,i)=>`line ${String(i+1).padStart(2,'0')} ok`).join('\n');
  await clear(p);await focusBox(p,touch);c0=(await state(p)).chips;await clipText(p,tall);await ctrlV(p);s=r.tall=await state(p);
  r.tallScroll=await ev(p,`(()=>{const i=document.getElementById('input');return {sh:i.scrollHeight,ch:i.clientHeight};})()`);await shot('paste-tall');
  check(s.v===tall&&s.chips===c0,`#168 ${tag}: a tall short paste goes into an empty field (${s.v.length} chars, chips ${c0}→${s.chips})`);
  check(r.tallScroll.sh>r.tallScroll.ch,`#168 ${tag}: that paste is taller than the field (${q(r.tallScroll)})`);

  // 7. #168 empty box + 2 500 characters → an attachment
  await clear(p);await focusBox(p,touch);c0=(await state(p)).chips;await clipText(p,'长'.repeat(2500));await ctrlV(p);
  await sleep(200);s=r.long=await state(p);await shot('paste-long');
  check(s.v===''&&s.chips===c0+1,`#168 ${tag}: 2 500 characters into an empty box → one attachment (${c0}→${s.chips}, ${s.v.length})`);

  // 8. #168 near the single-message limit: the paste that crosses it becomes the attachment, with a note; the words stay
  const max=await ev(p,`document.getElementById('input').maxLength`),have='a'.repeat(max-40);
  await clear(p);await focusBox(p,touch);
  await ev(p,`(()=>{const i=document.getElementById('input');i.value=${q(have)};i.dispatchEvent(new Event('input',{bubbles:true}));i.setSelectionRange(i.value.length,i.value.length);})()`);
  c0=(await state(p)).chips;await clipText(p,'b'.repeat(100));await ctrlV(p);await sleep(200);s=r.over=await state(p);await shot('paste-over');
  check(s.v===have,`#168 ${tag}: the words already in the box are kept (${s.v.length}/${have.length})`);
  check(s.chips===c0+1,`#168 ${tag}: the crossing paste is the attachment (${c0}→${s.chips})`);
  check(lang==='zh'?/^合起来超过 .+，这段已作为附件$/.test(s.toast):/^Together that is over .+, so this part was added as an attachment$/.test(s.toast),`#168 ${tag}: the note ${q(s.toast)}`);

  // 9. ⌘V with the focus on no field follows the same rule: words in the box → 2 500 characters join them
  await clear(p);await ev(p,`(()=>{const i=document.getElementById('input');i.value='x';i.dispatchEvent(new Event('input',{bubbles:true}));document.activeElement?.blur();document.body.tabIndex=-1;document.body.focus();})()`);
  c0=(await state(p)).chips;await clipText(p,'y'.repeat(2500));await ctrlV(p);s=r.anywhere=await state(p);
  check(s.v==='x'+'y'.repeat(2500)&&s.chips===c0,`#168 ${tag}: ⌘V anywhere with words in the box goes into the field (${s.v.length}, chips ${c0}→${s.chips})`);
  await clear(p);

  check(p.problems.length===0,'no console errors '+p.problems.join(' | '));
  results.push(r);console.log(`P406 composer ${tag} ${BEFORE?'RECORDED':'PASS'}`);await p.dispose();
 }
 writeFileSync(join(out,`result-${TAG}.json`),JSON.stringify({ok:!BEFORE,tag:TAG,scope:'Chromium emulation (390 touch / 1440 mouse); no physical device, no WebKit',results,...(BEFORE?{seen}:{})},null,1)+'\n');
 if(BEFORE){const bad=seen.filter(x=>!x.ok);console.log(`P406 before: ${bad.length} of ${seen.length} checks red`);bad.forEach(x=>console.log('  red:',x.msg));}
 else{passed=true;console.log('P406 #163 #168 composer focus after picks, paste by length PASS');}
}catch(e){console.error(e);process.exitCode=1;}finally{if(!BEFORE){rec.record('p406-composer',passed);rec.write();}await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
