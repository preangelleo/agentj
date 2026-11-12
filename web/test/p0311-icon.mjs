// P0311 icon row: isolated fake Noise host + real phone client; before/after run on the same frozen fixture.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const ROOT=fileURLToPath(new URL('../../../',import.meta.url));
const OUT=join(ROOT,'reports/qa/p0311-icon');
const phase=process.env.P0311_ICON_PHASE;
assert.ok(['before','after'].includes(phase),'choose before or after');
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 for(const width of [390,1440])for(const theme of ['dark','light'])for(const language of ['zh','en']){
  const p=await newPage(B,width,844,theme,{allow:[web.url,'ws://127.0.0.1:']});
  try {
   fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
   const items=[{id:'t1',text:language==='zh'?'规划下一步并核对结果':'Plan the next step and review the result',owner:'agent',plan:true,status:'pending',task:'card/todo#t2',report:'card/notes',acts:[{id:'done',label:'Done',draft:'mark done'}]},
    {id:'t2',text:'Review result',owner:'owner',status:'pending',task:'https://example.invalid/',report:'../../private',acts:[]},
    {id:'t3',text:'One linked task',owner:'agent',status:'pending',task:'card/todo#t1',acts:[]}];
   fake.st.onApp=async(c,m,send)=>{const reply=async(t,v)=>{await send({t,r:m.r,...v});return true};if(m.t==='jcard_list')return reply('jcard_items',{cards:[{id:'todo',name:{zh:'待办',en:'To-do'},on:true,official:true,accent:'windstorm'}]});if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:m.id,revision:'r0311-icons',template:'checklist',items,todo:{counts:{all:3,agent:2,owner:1}}});return false};
   await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
   await evaluate(p,`import('./js/jcards.js').then(m=>m.refresh())`);
   await evaluate(p,`import('./js/five-pages.js').then(m=>m.openTarget('todo'))`);
   await waitFor(p,`!!document.querySelector('.jc-body[data-card="todo"] .jc-item[data-item="t1"]')`);
   const state=await evaluate(p,`(()=>{const rows=[...document.querySelectorAll('.jc-item[data-item]')];return rows.map(row=>({id:row.dataset.item,overflow:row.scrollWidth>row.clientWidth,links:[...row.querySelectorAll('[data-todo-link]')].map(b=>({kind:b.dataset.todoLink,text:b.textContent,label:b.getAttribute('aria-label'),title:b.title,svg:b.querySelector('svg')?.getAttribute('aria-hidden'),width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height,css:getComputedStyle(b).backgroundColor,peerCss:getComputedStyle(row.querySelector('[data-todo-op=edit]')).backgroundColor,inActions:row.querySelector('.jc-todo-actions')?.contains(b),style:b.className})),buttons:[...row.querySelectorAll('[data-todo-op]')].map(b=>({width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height}))}))})()`);
   assert.deepEqual(state.map(x=>x.links.length),[2,0,1]);assert.ok(state.every(x=>!x.overflow));
   if(phase==='after')for(const row of state){
    assert.ok(row.buttons.every(b=>b.width>=44&&b.height>=44));
    for(const b of row.links){assert.equal(b.text,'');assert.equal(b.svg,'true');assert.equal(b.label,b.title);assert.equal(b.label,language==='zh'?(b.kind==='task'?'关联任务':'关联报告'):(b.kind==='task'?'Linked task':'Linked report'));assert.ok(b.width>=44&&b.height>=44);assert.equal(b.css,b.peerCss);assert.equal(b.inActions,true);assert.ok(b.style.includes('jc-entry-icon'))}
   }
   else assert.ok(state[0].links.some(b=>b.text.length>0));
   mkdirSync(join(OUT,phase),{recursive:true});writeFileSync(join(OUT,phase,`${language}-${width}-${theme}.png`),await shoot(p,''));
   console.log('PASS',phase,width,theme,language);
  }finally{await p.dispose()}
 }
}finally{await B.close();await fake.stop();await web.stop()}
