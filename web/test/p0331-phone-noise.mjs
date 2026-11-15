// Real product DOM + paired Noise transport; synthetic Host history, no real customer session.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,key,shoot} from './browser.mjs';
const out=join(process.env.OLARES_RUN_OUT || 'reports/qa/p0331','phone');mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),browser=await launch(),results=[];
async function click(p,sel){
 const point=await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(sel)});e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
 await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});
 await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
}
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.st.prefs={appearance:{language:lang,theme}};
  const p=await newPage(browser,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  const hostEvents=process.env.AJ_0331_HOST_HISTORY ? JSON.parse(readFileSync(process.env.AJ_0331_HOST_HISTORY,'utf8')) : [];
  const words={activity:lang==='zh'?'后台动态':'Background activity',owner:lang==='zh'?'主人':'Owner',failure:lang==='zh'?'需检查':'Needs attention'};
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await fake.addTurn({k:'phone',text:'owner asks for summary'},'The final main Agent reply');
   await waitFor(p,`document.getElementById('words').textContent.includes('The final main Agent reply')`);
   await fake.addTurn({k:'auto',name:'hourly',text:'{"schema":"automation-report.v1","payload":"raw background"}'},'Intermediate automation notice');
   await fake.addTurn({k:'sys',text:'internal wakeup'},'Internal system prompt');
   await fake.addTurn({k:'agent',via:'background_done',text:'job event'},'finished result');
   const failed=await fake.addTurn({k:'auto',text:'{"unsafe":"do not show this JSON in summary"}'},'raw failure object','failed');
   if(process.env.AJ_0331_PHASE==='before'){
    await waitFor(p,`document.getElementById('words').textContent.includes('raw failure object')`);
    assert.match(await evaluate(p,`document.getElementById('words').textContent`),/The final main Agent reply/,'background noise must not replace the main reply');
   }
   assert.match(await evaluate(p,`document.getElementById('words').textContent`),/The final main Agent reply/);
   assert.equal(await evaluate(p,`document.getElementById('pg').textContent`),'1 / 1');
   assert.equal(await evaluate(p,`document.getElementById('backgroundEntry')`),null);
   assert.doesNotMatch(await evaluate(p,`document.getElementById('omLabel').textContent`),/^(?:主人|Owner) · /);
   writeFileSync(join(out,`${lang}-${theme}-${width}-main.png`),await shoot(p));
   // Retained background history is reachable through existing quote navigation, without a dedicated entrance.
   await fake.addTurn({k:'phone',text:'Inspect quoted event',quote:{id:failed.id,text:'Quoted event',who:'Owner'}},'Quoted event followup');
   await waitFor(p,`document.getElementById('words').textContent.includes('Quoted event followup')`);
   await click(p,'#omQuote');await waitFor(p,`!document.getElementById('silent').hidden`);
   assert.equal(await evaluate(p,`document.querySelectorAll('#silentList details').length`),4);
   assert.equal(await evaluate(p,`document.getElementById('silentTitle').textContent`),words.activity);
   assert.doesNotMatch(await evaluate(p,`[...document.querySelectorAll('#silentList summary')].map(x=>x.textContent).join(' ')`),/automation-report|unsafe|raw failure/);
   assert.match(await evaluate(p,`document.querySelector('#silentList details[open] .full').textContent`),/unsafe/);
   await key(p,'Escape');await waitFor(p,`document.getElementById('silent').hidden`);
   await fake.addTurn({k:'phone',text:'{"schema":"automation-report.v1"}'},'Owner JSON is an ordinary input');
   await waitFor(p,`document.getElementById('words').textContent.includes('Owner JSON is an ordinary input')`);
   await fake.addTurn({k:'agent',text:''},'{"schema":"automation-report.v1","final":"main Agent final JSON"}');
   await waitFor(p,`document.getElementById('words').textContent.includes('main Agent final JSON')`);
   const merged=await fake.addTurn({k:'phone',text:'First owner instruction'},"I'll answer this together with the next reply.");
   await fake.addTurn({k:'sys',text:'native intermediate system notice'},'Internal intermediate notice');
   await fake.addTurn({k:'phone',text:'Second owner instruction'},'Both instructions are complete');
   await waitFor(p,`document.getElementById('words').textContent.includes('Both instructions are complete')`);
   assert.equal(await evaluate(p,`document.getElementById('omText').textContent`),'First owner instruction\n\nSecond owner instruction');
   assert.doesNotMatch(await evaluate(p,`document.getElementById('words').textContent`),/answer this together/);
   // The original owner page ID remains a valid quote target after display merging.
   await fake.addTurn({k:'phone',text:'Quoted followup',quote:{id:merged.id,text:'First owner instruction',who:'Owner'}},'Quote navigation result');
   await waitFor(p,`document.getElementById('words').textContent.includes('Quote navigation result')`);
   await click(p,'#omQuote');
   await waitFor(p,`document.getElementById('words').textContent.includes('Both instructions are complete')`);
   assert.match(await evaluate(p,`document.getElementById('omText').textContent`),/First owner instruction/);
   // New-host metadata follows the same path and raw owner text that equals a legacy reply is never swallowed.
   await fake.addTurn({k:'phone',text:'这条和下一条回复一起回您。'},'', 'done',{merged:true});
   await fake.addTurn({k:'phone',text:'Actual followup'},'Metadata merge final');
   await waitFor(p,`document.getElementById('pgCorner').dataset.unread==='1'`);
   await click(p,'#pgLatest');
   await waitFor(p,`document.getElementById('words').textContent.includes('Metadata merge final')`);
   assert.match(await evaluate(p,`document.getElementById('omText').textContent`),/这条和下一条回复一起回您。/);
   // These rows came from actual subprocess -> Board -> Notifier -> persistent History on this SHA.
   for(const row of hostEvents) await fake.addTurn(row.src,row.reply.text,row.end,row.card);
   // Actionable recovery and login cards cannot disappear behind the activity filter.
   await fake.addTurn({k:'sys',text:''},'Sign in to continue','done',{recovery:'restart_session'});
   await waitFor(p,`document.getElementById('words').textContent.includes('Sign in to continue')`);
   const proof=await evaluate(p,`(()=>{const r=document.getElementById('slashBtn').getBoundingClientRect();return {theme:document.documentElement.dataset.theme,width:innerWidth,scroll:document.documentElement.scrollWidth,tap:r.height,buttonRight:r.right,buttonLeft:r.left,buttonColor:getComputedStyle(document.getElementById('slashBtn')).color};})()`);
   assert.equal(proof.theme,theme);assert.ok(proof.scroll<=width+1);assert.ok(proof.tap>=44);assert.ok(proof.buttonLeft>=0 && proof.buttonRight<=width,'complete activity and attention label fits viewport');
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);results.push({lang,theme,width,proof,result:'PASS'});
  }finally{await p.dispose();}
 }
 writeFileSync(join(out,'results.json'),JSON.stringify(results,null,2));console.log(`PASS ${results.length} paired Noise phone/desktop journeys`);
}finally{await browser.close();await fake.stop();await web.stop();}
