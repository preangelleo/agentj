// Real product DOM, Noise and browser; every input is synthetic and local to this run.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep,tap} from './browser.mjs';
const out=join(process.env.OLARES_RUN_OUT || 'reports/qa/p0332','phone');mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),browser=await launch(),results=[];
const src=(kind,role='owner')=>({k:kind==='terminal'?'host':kind,source_kind:kind,source_role:role,text:'Fixture input '+kind});
async function click(p,selector){
 await evaluate(p,`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',behavior:'instant'})`);
 await waitFor(p,`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.width>0&&r.height>0&&!!hit&&e.contains(hit)})()`);
 const point=await evaluate(p,`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2,hit=document.elementFromPoint(x,y);return{x,y,hit:hit?.id||hit?.closest('button')?.id,menuHidden:document.getElementById('menu').hidden}})()`);
 console.log('POINTER_HIT',JSON.stringify({selector,...point,touch:p.uiTouch}));
 if(p.uiTouch)await tap(p,point.x,point.y);
 else for(const type of ['mousePressed','mouseReleased'])await p.send('Input.dispatchMouseEvent',{type,button:'left',clickCount:1,x:point.x,y:point.y});
}
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.st.prefs={appearance:{language:lang,theme}};fake.st.sayMode='queued';
  const p=await newPage(browser,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  p.uiTouch=width===390;
  const label=lang==='zh'?'停止当前任务':'Stop current task';
  const sentence=lang==='zh'?'Agent J 主机正忙，消息我先排上，忙完马上接。':'Got it — finishing the previous task';
  try{
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,`(()=>{document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();return true})()`);
   await fake.addTurn(src('phone'),'Current task reply');
   await waitFor(p,`document.getElementById('words').textContent.includes('Current task reply')`);
   // Every accepted non-owner kind remains invisible while queued, including in background activity.
   for(const source of [{k:'agent',via:'hook',source_kind:'system',source_role:'owner'}, {k:'agent',via:'background_done',source_kind:'system',source_role:'owner'}, {k:'sys',source_kind:'system',source_role:'owner'}, {k:'agent',via:'heartbeat',source_kind:'system',source_role:'owner'}, {k:'task',source_kind:'scheduled_task',source_role:'task'}, {k:'friend_report',source_kind:'friend_task',source_role:'task'},src('telegram','group'),src('email','group'),{...src('phone'),via:'hook'}]){
    const attention=source.via==='background_done' ? {activity_failure:true} : {};
    const t=await fake.addTurn({...source,text:'AUTOMATION PENDING'},'', 'open',{queued:true,...attention});
    await sleep(100);
    assert.match(await evaluate(p,`document.getElementById('words').textContent`),/Current task reply/,'ordinary queued before timeout');
    assert.equal(await evaluate(p,`document.getElementById('pg').textContent`),'1 / 1','no pending page exists to browse');
    await fake.updateTurn(t.id,{end:'done',card:{queued:true,held:'pending',...attention}});
    await sleep(100);
    assert.match(await evaluate(p,`document.getElementById('words').textContent`),/Current task reply/);
    assert.equal(await evaluate(p,`document.getElementById('backgroundEntry')`),null);
    assert.equal(await evaluate(p,`!!document.querySelector('[data-queue-stop]')`),false);
    await fake.updateTurn(t.id,{end:'done',card:null,reply:{text:''}});
    await sleep(100);
    assert.match(await evaluate(p,`document.getElementById('words').textContent`),/Current task reply/,'delivery ACK without reply stays silent');
    assert.equal(await evaluate(p,`document.getElementById('pg').textContent`),'1 / 1','ACK alone adds no page');
    assert.equal(await evaluate(p,`document.getElementById('backgroundEntry')`),null);
    await fake.updateTurn(t.id,{end:'done',card:null,reply:{text:'Delivered automation result'}});
    // Background activity uses its existing section; channel/guest/friend sources retain their normal reply surface.
    // 0335 r2 moves the activity entry into the existing command menu; the main page has no activity button.
    await click(p,'#slashBtn');
    await waitFor(p,`!document.getElementById('menu').hidden && document.getElementById('backgroundCommandEntry')`);
    await click(p,'#backgroundCommandEntry');
    await waitFor(p,`!document.getElementById('silent').hidden`);
    assert.equal(await evaluate(p,`document.getElementById('menu').hidden`),true,'background opens without a covering parent menu');
    await waitFor(p,`document.querySelector('#silentList details[data-id="${t.id}"]') || document.getElementById('words').textContent.includes('Delivered automation result')`);
    await click(p,'#silentClose');
    await waitFor(p,`document.getElementById('silent').hidden && document.getElementById('menu').hidden`);
    await click(p,'#input');
    assert.equal(await evaluate(p,`document.activeElement?.id`),'input','composer accepts real pointer focus after return');
    await fake.updateTurn(t.id,{end:'done',card:{withdrawn:true},src:{k:'phone',text:''},reply:{text:''}});
   }
   const refused=await fake.addTurn({k:'sys',source_kind:'system',source_role:'owner'},'Existing refused request remains visible','done',{kind:'refused'});
   await waitFor(p,`document.getElementById('words').textContent.includes('Existing refused request remains visible')`);
   await fake.updateTurn(refused.id,{src:{k:'phone'},card:{withdrawn:true},reply:{text:''}});
   // The phone's optimistic queue uses the same card and existing /stop fallback.
   await fake.send({t:'status',s:'working',agent:'codex',name:'Agent J'});
   await evaluate(p,`(()=>{const i=document.getElementById('input');i.value='Owner queued phone input';i.dispatchEvent(new Event('input'));document.getElementById('send').click();return true})()`);
   await waitFor(p,`document.body.dataset.pageQueued==='1'`);
   assert.equal(await evaluate(p,`document.querySelector('[data-queue-stop]')?.textContent`),label);
   assert.match(await evaluate(p,`document.getElementById('words').textContent`),new RegExp(sentence));
   writeFileSync(join(out,`${lang}-${theme}-${width}-queued.png`),await shoot(p));
   const geo=await evaluate(p,`(()=>{const e=document.querySelector('[data-queue-stop]'),r=e.getBoundingClientRect();return {height:r.height,left:r.left,right:r.right,scroll:document.documentElement.scrollWidth,theme:document.documentElement.dataset.theme}})()`);
   assert.ok(geo.height>=44 && geo.left>=0 && geo.right<=width && geo.scroll<=width+1);assert.equal(geo.theme,theme);
   await evaluate(p,`document.querySelector('[data-queue-stop]').click()`);await waitFor(p,`true`);assert.equal(fake.st.slashes.filter(x=>x.cmd==='stop').length,1);
   await fake.deliverQueued(false);
   const local=fake.st.turns.find(x=>x.src.text==='Owner queued phone input');
   await fake.updateTurn(local.id,{end:'done',reply:{text:'Phone owner handled'},card:null});
   await waitFor(p,`document.body.dataset.pageQueued==='0'`);
   // Cross-channel queued owner pages get exactly the same sentence, waited line, and stop action.
   for(const kind of ['phone','terminal','wechat','telegram','email']){
    const t=await fake.addTurn(src(kind),'','done',{queued:true,held:'pending',waited_s:20});
    await sleep(150);
    await evaluate(p,`(()=>{for(let i=0;i<40 && !document.getElementById('pgNext').disabled;i++)document.getElementById('pgNext').click();})()`);
    try{await waitFor(p,`document.body.dataset.pageQueued==='1' && document.getElementById('omText').textContent.includes(${JSON.stringify('Fixture input '+kind)})`);}catch(e){console.log('QUEUE_DIAGNOSTIC',await evaluate(p,`import('./js/relay.js').then(r=>({q:document.body.dataset.pageQueued,om:document.getElementById('omText').textContent,pg:document.getElementById('pg').textContent,words:document.getElementById('words').textContent,turns:r.loadedTurns().slice(-3)}))`));throw e;}
    assert.equal(await evaluate(p,`document.querySelector('[data-queue-stop]')?.textContent`),label,kind);
    assert.match(await evaluate(p,`document.getElementById('words').textContent`),new RegExp(sentence),kind);
    assert.equal(await evaluate(p,`document.querySelectorAll('[data-waited]').length`),1);
    await fake.updateTurn(t.id,{end:'done',card:null,reply:{text:'Handled owner '+kind}});
    await waitFor(p,`document.getElementById('words').textContent.includes(${JSON.stringify('Handled owner '+kind)})`);
    assert.equal(await evaluate(p,`document.body.dataset.pageQueued`),'0');assert.equal(await evaluate(p,`!!document.querySelector('[data-queue-stop]')`),false);
    assert.doesNotMatch(await evaluate(p,`document.getElementById('words').textContent`),new RegExp(sentence));
   }
   writeFileSync(join(out,`${lang}-${theme}-${width}-delivered.png`),await shoot(p));
   assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);results.push({lang,theme,width,result:'PASS',geo});
  }finally{await p.dispose();}
 }
 writeFileSync(join(out,'results.json'),JSON.stringify(results,null,2));console.log(`PASS ${results.length} owner-only queue browser journeys`);
}finally{await browser.close();await fake.stop();await web.stop();}
