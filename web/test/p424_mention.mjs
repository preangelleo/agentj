// P424 #148 · real Chromium + real Noise to the local fake host, 390 (touch) + 1440 (mouse) × light (zh) / dark (en).
// Leo: 「为什么把他的名字搞这么长？我每次 at 的时候，整个输入框从左到右都给他占住……后边那些括号备注有点多余」. The computer's
// CEO field can be 「阿影 · 短视频工作室 CEO (Claude Code)」: the box shows only `@阿影` as a capped chip (a long name ends in …,
// never the whole line), the @ list shows the name with the role in grey under it, no harness name anywhere; the message
// sent still names the folder (`@video-cn（CEO 阿影）`) so the Agent resolves it.
// #149 addendum (「所有的 mention name and slash command 后面都应该自动有个空格」): every insert path — @ list (Enter / tap),
// the Workflow page's @, the / suggestions, the / menu, the card + — leaves exactly one space before the caret.
// Shots → reports/qa/p424/mention/<tag>/ (P424_TAG=before serves HEAD's five-pages.js / five-pages.css, records only).
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
const TAG=process.env.P424_TAG||'after',BEFORE=TAG==='before';
const out=join(ROOT,`reports/qa/p424/mention/${TAG}/`);mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'p424-mention-'));cpSync(PUBLIC_DIR,tmp,{recursive:true});writeFileSync(join(tmp,'i18n.js'),i18nModule());
if(BEFORE)for(const f of ['js/five-pages.js','five-pages.css'])
 writeFileSync(join(tmp,f),execFileSync('git',['show','HEAD:web/public/'+f],{cwd:ROOT,maxBuffer:64<<20}));
const FX=JSON.parse(readFileSync(join(ROOT,'host/tests/fixtures/p244_wf.json'),'utf8'));
const CARDS=JSON.parse(readFileSync(join(ROOT,'host/tests/fixtures/p360_cards.json'),'utf8')).list.cards;
const base=FX.wf_list[0].items.find(w=>w.folder==='video-studio');
const LONG='Professor Maximilian Bartholomew Longname-Featherstonehaugh',
 ITEMS=[{...base,folder:'video-cn',ceo:'阿影 · 短视频工作室 CEO (Claude Code)',company:'短视频工作室',py:'aying ay',robot:3},
  {...base,folder:'research-lab',ceo:`${LONG} · Research Lab CEO (Codex)`,company:'Research Lab',py:'',robot:5},
  ...FX.wf_list[0].items];
const WF=[{...FX.wf_list[0],items:ITEMS},...FX.wf_list.slice(1)];
const MENU={source:'file',items:[{cmd:'/good-morning',desc:'Morning brief',group:'mine'}],skills:[{cmd:'agentj-add-note',desc:'Add a note'}],cmds:['clear','status']};
const rec=parityRecorder('web/test/p424_mention.mjs');let passed=false;
const web=await startWebServer({publicDir:tmp}),fake=await startFakeHost(),B=await launch(),results=[],seen=[];
const check=(ok,msg)=>{if(BEFORE){seen.push({ok:!!ok,msg});return;}assert.ok(ok,msg);};
const q=JSON.stringify;
const state=p=>ev(p,`(()=>{const i=document.getElementById('input'),ed=document.getElementById('workflow-compose'),a=document.activeElement;
 const plain=n=>n.nodeType===3?n.textContent:n.nodeType!==1?'':n.dataset?.mention||(n.tagName==='BR'?'\\n':[...n.childNodes].map(plain).join(''));
 let caret=null;if(a===i)caret=i.selectionStart;else if(a===ed){const s=getSelection();if(s.rangeCount&&ed.contains(s.anchorNode)){const r=s.getRangeAt(0).cloneRange();r.selectNodeContents(ed);r.setEnd(s.anchorNode,s.anchorOffset);caret=[...r.cloneContents().childNodes].map(plain).join('').length;}}
 if(caret===null)caret=i.selectionStart;   // focus elsewhere (touch keeps the keyboard down; a closed sheet): the field's own caret
 const pills=[...document.querySelectorAll('#workflow-compose .workflow-token:not(.cmd-token)')].map(t=>{const b=t.getBoundingClientRect();return {text:t.textContent,w:Math.round(b.width),sw:t.scrollWidth,cw:t.clientWidth,ell:getComputedStyle(t).textOverflow};});
 const box=(ed&&!ed.hidden?ed:i).getBoundingClientRect().width;
 return {v:i.value,focus:a===i||a===ed,active:a?.id||a?.tagName,caret,pills,box:Math.round(box)};})()`);
const clear=p=>ev(p,`(()=>{const i=document.getElementById('input');i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));document.activeElement?.blur();})()`);
async function press(p,sel,touch){
 const r=await rect(p,sel);
 if(touch)await tap(p,r.x,r.y);
 else for(const type of ['mouseMoved','mousePressed','mouseReleased'])await p.send('Input.dispatchMouseEvent',{type,x:r.x,y:r.y,button:'left',clickCount:1});
 await sleep(250);
}
const focusBox=async(p,touch)=>{if(touch)await press(p,'#input',true);else await ev(p,`document.getElementById('input').focus()`);};
const spaceBeforeCaret=s=>s.caret!=null&&s.caret>0&&s.v[s.caret-1]===' '&&s.v[s.caret-2]!==' ';
async function pair(w,scheme,lang){
 fake.reset();fake.st.menu=MENU;fake.st.caps=['wf1','jcards1'];
 const cards=JSON.parse(JSON.stringify(CARDS));
 fake.st.onApp=async(c,m,send)=>{if(m.t==='wf_list'){for(const x of WF)await send({...x,r:m.r});return true;}
  if(m.t==='jcard_list'){await send({t:'jcard_items',r:m.r,cards});return true;}return false;};
 const p=await newPage(B,w,900,scheme,{touch:w<800,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await ev(p,`(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();})()`);
 await p.send('Emulation.setFocusEmulationEnabled',{enabled:true});
 await ev(p,`Promise.all([import('./js/five-pages.js').then(m=>m.refresh()),import('./js/jcards.js').then(m=>m.refresh())]).then(()=>1)`);await sleep(600);
 return p;
}
try{
 for(const [w,scheme,lang] of [[390,'light','zh'],[390,'dark','en'],[1440,'light','zh'],[1440,'dark','en']]){
  const tag=`${w}-${scheme}`,touch=w<800,p=await pair(w,scheme,lang),r={w,scheme,lang};
  const shot=async name=>writeFileSync(join(out,`${tag}-${name}.png`),await shoot(p));
  // 1. the @ list: short name, role in grey under it, no harness name
  await clear(p);await focusBox(p,touch);await p.send('Input.insertText',{text:(lang==='zh'?'请 ':'ask ')+'@'});
  await waitFor(p,`!document.getElementById('five-mentions').hidden&&document.querySelectorAll('#five-mentions .fp-mention').length>=3`);
  r.list=await ev(p,`[...document.querySelectorAll('#five-mentions .fp-mention:not(.fp-mention-card)')].map(b=>({text:b.textContent,
   name:b.querySelector('.fp-mention-name')?.textContent??null,sub:b.querySelector('.fp-mention-sub')?.textContent??null,
   subColor:b.querySelector('.fp-mention-sub')?getComputedStyle(b.querySelector('.fp-mention-sub')).color:null,color:getComputedStyle(b).color,folder:b.dataset.folder||null}))`);
  await shot('list');
  const cn=r.list.find(x=>x.folder==='video-cn')||r.list.find(x=>x.text.includes('阿影'));
  check(cn&&cn.name==='阿影'&&cn.sub==='短视频工作室 CEO · 短视频工作室 · video-cn',`#148 ${tag}: list row = short name + grey role ${q(cn)}`);
  check(r.list.every(x=>!/Claude Code|Codex|OpenCode/.test(x.text)),`#148 ${tag}: no harness name in the @ list ${q(r.list.map(x=>x.text))}`);
  check(cn&&cn.subColor&&cn.subColor!==cn.color,`#148 ${tag}: the role line is the muted colour (${cn?.subColor} vs ${cn?.color})`);
  // 2. pick it with Enter: the box shows `@阿影` only, one space after it, the caret after the space
  await p.send('Input.insertText',{text:lang==='zh'?'阿':'ay'});
  await waitFor(p,`!!document.querySelector('#five-mentions .fp-mention')`);
  await key(p,'Enter');await sleep(300);let s=r.pickEnter=await state(p);await shot('picked-short');
  check(s.v===(lang==='zh'?'请 ':'ask ')+'@video-cn（CEO 阿影） ',`#148 ${tag}: the routing text is folder + short name ${q(s.v)}`);
  check(!/Claude|Codex|·/.test(s.v),`#148 ${tag}: no role / harness in what is sent ${q(s.v)}`);
  check(s.pills.length===1&&s.pills[0].text==='@阿影',`#148 ${tag}: the chip reads only @阿影 ${q(s.pills)}`);
  check(s.focus&&spaceBeforeCaret(s),`#149 ${tag} @ Enter: one space before the caret (${s.active} ${s.caret}/${s.v.length})`);
  // 3. send: the computer gets the routing form
  const n=fake.st.says.length;await p.send('Input.insertText',{text:lang==='zh'?'看看进度':'check progress'});await key(p,'Enter');
  try{await waitFor(p,`document.getElementById('input').value===''`,4000);}catch(_){}await sleep(200);
  const said=fake.st.says.slice(n).map(x=>x.text||'').join('|');r.said=said;
  check(said.includes('@video-cn（CEO 阿影） '),`#148 ${tag}: the sent message names the folder ${q(said)}`);
  // 4. a very long CEO name: capped chip (…), never the whole line; picked with a tap / click
  await clear(p);await focusBox(p,touch);await p.send('Input.insertText',{text:'@research'});
  await waitFor(p,`!!document.querySelector('#five-mentions .fp-mention[data-folder="research-lab"]')||[...document.querySelectorAll('#five-mentions .fp-mention')].some(b=>b.textContent.includes('Research'))`);
  await ev(p,`(()=>{const b=document.querySelector('#five-mentions .fp-mention[data-folder="research-lab"]')||[...document.querySelectorAll('#five-mentions .fp-mention')].find(b=>b.textContent.includes('Research'));b.id='p424pick';})()`);
  await press(p,'#p424pick',touch);s=r.pickLong=await state(p);await shot('picked-long');
  const pl=s.pills[0]||{};
  check(pl.text==='@'+LONG,`#148 ${tag}: the long chip is the name alone ${q(pl.text)}`);
  check(pl.sw>pl.cw&&pl.ell==='ellipsis'&&pl.w<=s.box*0.6+1,`#148 ${tag}: the long chip is cut with … and stays under 60 % of the box (${pl.w}/${s.box}, ${pl.sw}>${pl.cw})`);
  check(s.focus&&spaceBeforeCaret(s),`#149 ${tag} @ tap: one space before the caret (${s.active} ${s.caret}/${s.v.length})`);
  // 5. #149 every other insert path leaves one space before the caret
  await clear(p);await focusBox(p,touch);await p.send('Input.insertText',{text:'/good'});
  await waitFor(p,"!document.getElementById('sug').hidden");await key(p,'Enter');await sleep(250);s=r.slashSug=await state(p);
  check(s.v==='/good-morning '&&spaceBeforeCaret(s),`#149 ${tag} / suggestion: ${q(s.v)} caret ${s.caret}`);
  await clear(p);await ev(p,`document.getElementById('slashBtn').click()`);
  await waitFor(p,`[...document.querySelectorAll('#menu [data-slash]')].some(b=>b.dataset.slash==='/good-morning')`);
  await ev(p,`[...document.querySelectorAll('#menu [data-slash]')].find(b=>b.dataset.slash==='/good-morning').click()`);await sleep(250);s=r.slashMenu=await state(p);
  check(s.v==='/good-morning '&&spaceBeforeCaret(s),`#149 ${tag} / menu: ${q(s.v)} caret ${s.caret}`);
  await clear(p);
  if(await ev(p,`!document.getElementById('cardPlus').hidden`)){await ev(p,`document.getElementById('cardPlus').click()`);await sleep(250);s=r.cardPlus=await state(p);
   check(/^\/[a-z0-9-]+ $/.test(s.v)&&spaceBeforeCaret(s),`#149 ${tag} card +: ${q(s.v)} caret ${s.caret}`);}
  else check(false,`#149 ${tag} card +: the + button is shown`);
  await clear(p);await ev(p,`import('./js/five-pages.js').then(m=>{m.go('workflows');return 1;})`);
  await waitFor(p,`!!document.querySelector('[data-folder="video-cn"] .fp-at')`);
  await ev(p,`document.querySelector('[data-folder="video-cn"] .fp-at').click()`);await sleep(300);s=r.wfAt=await state(p);await shot('workflow-at');
  check(s.v==='@video-cn（CEO 阿影） '&&s.pills[0]?.text==='@阿影',`#148/#149 ${tag} Workflow page @: ${q(s.v)} ${q(s.pills)}`);
  check(s.v.endsWith(' '),`#149 ${tag} Workflow page @: ends with one space`);
  await clear(p);
  check(p.problems.length===0,'no console errors '+p.problems.join(' | '));
  results.push(r);console.log(`P424 #148 mention ${tag} ${BEFORE?'RECORDED':'PASS'}`);await p.dispose();
 }
 writeFileSync(join(out,'result.json'),JSON.stringify({ok:!BEFORE,tag:TAG,scope:'Chromium emulation (390 touch / 1440 mouse); no physical device, no WebKit',results,...(BEFORE?{seen}:{})},null,1)+'\n');
 if(BEFORE){const bad=seen.filter(x=>!x.ok);console.log(`P424 before: ${bad.length} of ${seen.length} checks red`);bad.forEach(x=>console.log('  red:',x.msg));}
 else{passed=true;console.log('P424 #148 short @ mention chip + #149 one space after every insert PASS');}
}catch(e){console.error(e);process.exitCode=1;}finally{if(!BEFORE){rec.record('p424-mention',passed);rec.write();}await B.close();await fake.stop();await web.stop();rmSync(tmp,{recursive:true,force:true});}
