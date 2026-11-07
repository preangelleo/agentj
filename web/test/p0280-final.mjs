// Final 0.28.0 business deltas over #576. Synthetic Noise host only.
// P0280_PUBLIC=<576 public> records the same before states without asserting new geometry.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,sleep,shoot,key} from './browser.mjs';
const before=!!process.env.P0280_PUBLIC,out=`reports/qa/p0280-final/${before?'before':'after'}/`;
mkdirSync(out,{recursive:true});
const web=await startWebServer(before?{publicDir:process.env.P0280_PUBLIC}:{}),fake=await startFakeHost(),B=await launch(),results=[];
const geometry=`(()=>{const b=document.getElementById('omQuoteFold'),h=document.getElementById('omqfHead'),t=document.getElementById('omqfText'),r=b.getBoundingClientRect(),hr=h.getBoundingClientRect(),tr=t.getBoundingClientRect();return {height:r.height,width:r.width,headY:hr.y+hr.height/2,textY:tr.y+tr.height/2,expanded:b.getAttribute('aria-expanded'),text:b.textContent,folded:document.getElementById('om').dataset.qfold,moreVisible:[...document.querySelectorAll('#omQuoteMore .omq')].filter(x=>x.getClientRects().length).length}})()`;
async function states(p,id){
 const point=await evaluate(p,`(()=>{const r=document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
 const sample=()=>evaluate(p,`(()=>{const n=document.getElementById(${JSON.stringify(id)}),s=getComputedStyle(n),r=n.getBoundingClientRect();return {background:s.backgroundColor,color:s.color,border:s.borderColor,shadow:s.boxShadow,transform:s.transform,width:r.width,height:r.height,focus:n.matches(':focus-visible'),hover:n.matches(':hover'),active:n.matches(':active'),genericButton:n.classList.contains('aj-btn')}})()`);
 await p.send('Input.dispatchMouseEvent',{type:'mouseMoved',...point});const hover=await sample();
 await key(p,'Tab');await evaluate(p,`document.getElementById(${JSON.stringify(id)}).focus({preventScroll:true})`);const focus=await sample();
 await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});const active=await sample();
 await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
 assert.equal(hover.hover,true);assert.equal(focus.focus,true);assert.equal(active.active,true);assert.equal(active.genericButton,false,'quotes and robots never inherit ordinary action-button paint');
 return {hover,focus,active};
}
async function click(p,id){const pt=await evaluate(p,`(()=>{const r=document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...pt});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...pt});}
try{for(const width of [390,834,1440])for(const theme of ['light','dark']){
 fake.reset();fake.st.autoReply=false;fake.st.extraCaps=['mq5'];fake.st.prefs={appearance:{language:'zh',theme}};
 const refs=[];for(let i=0;i<4;i++)refs.push(await fake.addTurn({k:'phone',dev:'other',text:`Fixture ${i}`},`第 ${i+1} 条摘要，长引用不会撑高折叠行。`.repeat(10)));
 const q=x=>({id:x.id,who:'Agent J',ts:x.ts,text:x.reply.text});
 await fake.addTurn({k:'phone',dev:'other',text:'四条引用均应保留，可展开查看。',quote:{...q(refs[0]),more:refs.slice(1).map(q)}},Array.from({length:60},(_,i)=>`阅读段落 ${i+1}：保留当前位置和阅读模式。`).join('\n\n'));
 const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
  await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await waitFor(p,`!document.getElementById('omQuoteFold').hidden`);await sleep(150);
  const collapsed=await evaluate(p,geometry);writeFileSync(out+`${width}-${theme}-quotes-folded.png`,await shoot(p));
  if(!before){assert.equal(collapsed.folded,'1');assert.equal(collapsed.height,44);assert.ok(Math.abs(collapsed.headY-collapsed.textY)<1,'label and summary are on one line');assert.equal(collapsed.moreVisible,0);assert.match(collapsed.text,/4/);}
  const quoteStates=await states(p,'omQuoteFold');await sleep(100);const expanded=await evaluate(p,geometry);
  assert.equal(expanded.expanded,'true');assert.equal(expanded.moreVisible,3);writeFileSync(out+`${width}-${theme}-quotes-expanded.png`,await shoot(p));
  await click(p,'omQuoteFold');await sleep(100);assert.equal((await evaluate(p,geometry)).folded,'1');
  await fake.send({t:'busy',tasks:[{id:'fixture-task',who:'Agent J',kind:'ceo',label:'Synthetic task',c:0}]});await waitFor(p,`!document.getElementById('busyDots').hidden`);
  const n=fake.st.says.length;const robotStates=await states(p,'busyDots');await waitFor(p,`!document.getElementById('busyDlg').hidden`);await sleep(200);assert.equal(fake.st.says.length,n,'robot click never sends customization');
  writeFileSync(out+`${width}-${theme}-robots-menu.png`,await shoot(p));await key(p,'Escape');await waitFor(p,`document.getElementById('busyDlg').hidden`);
  await click(p,'readBtn');await waitFor(p,`!document.getElementById('rd').hidden`);await sleep(650);await evaluate(p,`document.getElementById('rdScroll').scrollTop=260`);await sleep(100);
  const readBefore=await evaluate(p,`({scroll:document.getElementById('rdScroll').scrollTop,main:document.getElementById('main').scrollTop,page:document.getElementById('pg').textContent,mode:document.body.dataset.reader})`);
  await evaluate(p,`Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('synthetic denied')}}});document.execCommand=()=>false`);
  await click(p,'rdCopyBtn');await waitFor(p,`document.getElementById('rdCopyBtn').dataset.done==='fail'`);await sleep(1900);assert.equal(await evaluate(p,`document.getElementById('rd').hidden`),false,'failed copy stays in reader');
  // A clipboard write substitute records the actual copy payload without external permissions.
  await evaluate(p,`window.__copied=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.__copied.push(text)}})`);
  await click(p,'rdCopyBtn');await sleep(150);const readAfter=await evaluate(p,`({main:document.getElementById('main').scrollTop,page:document.getElementById('pg').textContent,hidden:document.getElementById('rd').hidden,copies:window.__copied.length})`);
  assert.equal(readAfter.copies,1);if(!before){assert.equal(readAfter.hidden,true);assert.equal(readAfter.page,readBefore.page);assert.equal(readAfter.main,readBefore.main);}writeFileSync(out+`${width}-${theme}-reader-copy.png`,await shoot(p));
  await key(p,'Escape');await waitFor(p,`document.getElementById('rd').hidden`);await sleep(650);await click(p,'setBtn');await waitFor(p,`!document.getElementById('settings').hidden`);
  const icons=await evaluate(p,`['scanBtn','srchBtn','setBtn'].map(id=>{const b=document.getElementById(id),s=b.querySelector('svg'),r=b.getBoundingClientRect(),v=s.getBoundingClientRect();return {id,w:r.width,h:r.height,svgW:v.width,svgH:v.height,padding:getComputedStyle(b).padding}})`);
  for(const x of icons){assert.equal(x.svgW,24);assert.equal(x.svgH,24);assert.ok(x.w>=44&&x.h>=44);}
  if(!before)assert.equal(await evaluate(p,`!!document.getElementById('mail-mark-read')`),false);
  writeFileSync(out+`${width}-${theme}-settings.png`,await shoot(p));results.push({width,theme,collapsed,expanded,quoteStates,robotStates,readBefore,readAfter,icons});console.log('PASS',width,theme);
 }finally{await p.dispose();}
}}finally{writeFileSync(out+'results.json',JSON.stringify(results,null,2));await B.close();await fake.stop();await web.stop();}
