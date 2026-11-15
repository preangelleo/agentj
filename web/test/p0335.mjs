// Paired real Chromium, synthetic history: no new main-page entrance; background uses the existing menu/dialog.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,key,shoot} from './browser.mjs';
const out=join(process.env.OLARES_RUN_OUT || 'reports/qa/p0335','phone');mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),browser=await launch(),results=[];
async function click(p,sel){
 const point=await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(sel)});e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
 await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});
 await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
}
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.st.prefs={appearance:{language:lang,theme}};
  fake.st.menu={source:'file',items:[{cmd:'/owner-command',desc:'Owner configured command'}],skills:[]};
  const p=await newPage(browser,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   assert.equal(await evaluate(p,`document.getElementById('commandsOpen')`),null);
   assert.equal(await evaluate(p,`document.getElementById('commandsEntry')`),null);
   await fake.addTurn({k:'phone',text:'Owner asks for summary'},'The final main Agent reply');
   await waitFor(p,`document.getElementById('words').textContent.includes('The final main Agent reply')`);
   await fake.addTurn({k:'auto',text:'{"schema":"automation-report.v1"}'},'Raw background notice');
   await fake.addTurn({k:'sys',text:'internal wakeup'},'Internal system prompt');
   await fake.addTurn({k:'agent',via:'background_done',text:'job event'},'raw failed result','failed',{activity_failure:true});
   await click(p,'#slashBtn');await waitFor(p,`!document.getElementById('menu').hidden && document.querySelector('#menuList [data-slash="/owner-command"]')`);
   assert.equal(await evaluate(p,`document.getElementById('backgroundEntry')`),null);
   assert.doesNotMatch(await evaluate(p,`document.getElementById('deck').textContent`),/Raw background|Internal system|raw failed|后台动态|Background activity/);
   assert.match(await evaluate(p,`document.getElementById('words').textContent`),/The final main Agent reply/);
   assert.equal(await evaluate(p,`document.getElementById('pg').textContent`),'1 / 1');
   assert.equal(await evaluate(p,`document.getElementById('backgroundCommandEntry').textContent`),'/background'+(lang==='zh'?'后台动态':'Background activity'));
   const top=await evaluate(p,`document.getElementById('menuList').textContent`);
   await click(p,'#backgroundCommandEntry');await waitFor(p,`!document.getElementById('silent').hidden`);
   assert.equal(await evaluate(p,`document.querySelectorAll('#silentList details').length`),3);
   assert.equal(await evaluate(p,`document.getElementById('silentTitle').textContent`),lang==='zh'?'后台动态':'Background activity');
   await click(p,'#silentList details summary');
   assert.match(await evaluate(p,`document.querySelector('#silentList details[open]').textContent`),/raw failed/);
   await key(p,'Escape');await waitFor(p,`document.getElementById('silent').hidden`);
   assert.equal(await evaluate(p,`document.getElementById('menu').hidden`),true,'background closes its parent menu');
   // This reopen has cached rows: their presence does not acknowledge the new async loadMenu reply.
   // Observe both actual renders (openMenu sync + loadMenu response) before pressing a replaceable row.
   await evaluate(p,`(()=>{window.__p0335MenuRenders=0;window.__p0335MenuObserver=new MutationObserver(rows=>{window.__p0335MenuRenders+=rows.filter(r=>r.type==='childList').length});window.__p0335MenuObserver.observe(document.getElementById('menuList'),{childList:true})})()`);
   await click(p,'#slashBtn');await waitFor(p,`!document.getElementById('menu').hidden && window.__p0335MenuRenders>=2`);
   await evaluate(p,`window.__p0335MenuObserver.disconnect()`);
   assert.equal(await evaluate(p,`document.getElementById('menuList').textContent`),top);
   await evaluate(p,`document.querySelector('#menuList [data-slash="/owner-command"]').scrollIntoView({block:'center'})`);
   // Preserve real pointer activation; wait for the moving sheet and two painted frames, not a fixed sleep.
   await waitFor(p,`(async()=>{const menu=document.getElementById('menu');if(menu.getAnimations({subtree:true}).some(a=>a.playState==='running'))return false;const selector='#menuList [data-slash="/owner-command"]',e=document.querySelector(selector);const rect=()=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};const a=rect();await new Promise(requestAnimationFrame);const b=rect();await new Promise(requestAnimationFrame);const c=rect(),hit=document.elementFromPoint(c[0]+c[2]/2,c[1]+c[3]/2);return e===document.querySelector(selector)&&JSON.stringify(a)===JSON.stringify(b)&&JSON.stringify(b)===JSON.stringify(c)&&e.contains(hit)})()`);
   await click(p,'#menuList [data-slash="/owner-command"]');
   await waitFor(p,`document.getElementById('input').value.includes('/owner-command')`);
   assert.equal(await evaluate(p,`document.getElementById('menu').hidden`),true);
   await evaluate(p,`document.getElementById('input').focus();document.getElementById('input').value='/background';document.getElementById('input').dispatchEvent(new Event('input'))`);
   await key(p,'Escape');await click(p,'#send');await waitFor(p,`!document.getElementById('silent').hidden`);
   await key(p,'Escape');await waitFor(p,`document.getElementById('silent').hidden`);
   await fake.addTurn({k:'sys',text:''},'Sign in to continue' ,'done',{recovery:'restart_session'});
   await waitFor(p,`document.getElementById('words').textContent.includes('Sign in to continue')`);
   const proof=await evaluate(p,`(()=>{const r=document.getElementById('slashBtn').getBoundingClientRect();return {theme:document.documentElement.dataset.theme,width:innerWidth,scroll:document.documentElement.scrollWidth,tap:r.height,left:r.left,right:r.right};})()`);
   assert.equal(proof.theme,theme);assert.ok(proof.scroll<=width+1);assert.ok(proof.tap>=44);assert.ok(proof.left>=0 && proof.right<=width);
   writeFileSync(join(out,`${lang}-${theme}-${width}.png`),await shoot(p));
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);results.push({lang,theme,width,proof,result:'PASS'});
  }finally{await p.dispose();}
 }
 writeFileSync(join(out,'results.json'),JSON.stringify(results,null,2));console.log(`PASS ${results.length} paired Noise slash-menu journeys`);console.log("p0335_slash_entry PASS");
}finally{await browser.close();await fake.stop();await web.stop();}
