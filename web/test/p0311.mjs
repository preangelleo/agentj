// P0311 paired phone; real page and local Noise fakehost; no account or pairing data in shots.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot} from './browser.mjs';
const ROOT=fileURLToPath(new URL('../../../',import.meta.url)),OUT=join(ROOT,'reports/qa/p0311');
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 for(const width of [390,1440])for(const theme of ['dark','light'])for(const language of ['zh','en']){
  const p=await newPage(B,width,844,theme,{allow:[web.url,'ws://127.0.0.1:']});
  try {
   fake.reset();fake.st.caps=['jcards1'];fake.st.prefs={appearance:{language,theme},voice:{speak_replies:false,wake_enabled:false},agent:{session_mode:'shared'}};
   const items=[{id:'t1',text:language==='zh'?'规划项目下一步':'Plan the next project step',owner:'agent',plan:true,status:'pending',task:'card/todo#t2',report:'card/notes',acts:[{id:'done',label:'Done',draft:'mark done'}]},{id:'t2',text:'Review result',owner:'owner',status:'pending',task:'https://example.invalid/secret',report:'../../private',acts:[]}];
   fake.st.onApp=async(c,m,send)=>{const reply=async(t,v)=>{await send({t,r:m.r,...v});return true};if(m.t==='jcard_list')return reply('jcard_items',{cards:[{id:'todo',name:{zh:'待办',en:'To-do'},on:true,official:true,accent:'windstorm'}]});if(m.t==='jcard_get')return reply('jcard_get',{ok:true,id:m.id,revision:'r0311',template:'checklist',items,todo:{counts:{all:2,agent:1,owner:1}}});return false};
   await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});
   await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();document.documentElement.dataset.theme='${theme}'`);
   await evaluate(p,`import('./js/jcards.js').then(m=>m.refresh())`);
   await evaluate(p,`import('./js/five-pages.js').then(m=>m.openTarget('todo'))`);
   await waitFor(p,`!!document.querySelector('.jc-body[data-card="todo"] .jc-item[data-item="t1"]')`);
   const plan=await evaluate(p,`document.querySelector('[data-item=t1] .jc-todo-plan')?.textContent`);
   assert.equal(plan,language==='zh'?'Agent 计划':'Agent plan');
   const refs=await evaluate(p,`(()=>({good:[...document.querySelectorAll('[data-item=t1] [data-todo-link]')].map(a=>({name:a.textContent,w:a.getBoundingClientRect().width,h:a.getBoundingClientRect().height})),bad:document.querySelectorAll('[data-item=t2] [data-todo-link]').length,overflow:document.querySelector('[data-item=t1]').scrollWidth>document.querySelector('[data-item=t1]').clientWidth}))()`);
   assert.equal(refs.good.length,2);assert.equal(refs.bad,0);assert.ok(!refs.overflow);assert.ok(refs.good.every(x=>x.w>=44&&x.h>=44));
   // Baseline screenshot: restore the HEAD row DOM (no plan/links) without exposing pairing UI.
   await evaluate(p,`document.querySelectorAll('.jc-todo-plan,.jc-todo-links').forEach(el=>el.hidden=true)`);
   mkdirSync(join(OUT,'before'),{recursive:true});writeFileSync(join(OUT,'before',`${language}-${width}-${theme}.png`),await shoot(p,''));
   await evaluate(p,`document.querySelectorAll('.jc-todo-plan,.jc-todo-links').forEach(el=>el.hidden=false)`);
   mkdirSync(join(OUT,'after'),{recursive:true});writeFileSync(join(OUT,'after',`${language}-${width}-${theme}.png`),await shoot(p,''));
   console.log('PASS',width,theme,language);
  }finally{await p.dispose()}
 }
}finally{await B.close();await fake.stop();await web.stop()}
