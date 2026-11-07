// Read-only log pagination on the real customer page over local Noise. No approval or password path is invoked.
import assert from 'node:assert/strict';import {mkdirSync,writeFileSync} from 'node:fs';import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep,rect} from './browser.mjs';
const out=resolve(process.env.P468_LOG_OUT||'reports/qa/remote-assist-468-20261106/web-log-pages');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch(),url=web.url.replace('127.0.0.1','localhost');let checks=0;const check=(v,k)=>{assert.ok(v,k);checks++;};
const G1='log-grant-one',G2='log-grant-two';const entries=(gid,from,to)=>Array.from({length:to-from+1},(_,i)=>({gid,ts:1700000000+from+i,ev:'terminal_command',command:'record-'+String(from+i).padStart(3,'0'),state:'confirmed'}));
try{
for(const [lang,theme,width]of[['zh','light',390],['en','dark',1440]]){
 fake.reset();fake.setUp(true);fake.st.extraCaps=['assist_term1'];fake.st.prefs={appearance:{language:lang,theme}};const h={calls:[],last:null,hold:false,held:null,mode:'normal',wrong:null};
 const sendState=id=>fake.send({t:'assist_state',scope:'terminal',active:true,id,key_id:'official-test',until:Math.floor(Date.now()/1000)+1800});
 fake.st.onApp=async(c,m,send)=>{
  if(m.t!=='assist_log')return false;
  h.calls.push({gid:m.gid||'',cursor:m.cursor||null});check(!m.sig&&!m.fa,'read-only pagination adds no write approval');
  if(h.hold){h.held={m,send};return true;}
  const gid=m.gid||G1;
  if(h.mode==='pruned'){await send({t:'assist_log_res',r:m.r,gid,why:'pruned'});return true;}
  let page;
  if(gid===G2)page={entries:entries(gid,1,1),more:false,next_cursor:null};
  else if(!m.cursor)page={entries:entries(gid,401,450),more:true,next_cursor:'opaque-earlier-a'};
  else if(m.cursor==='opaque-earlier-a')page={entries:entries(gid,201,400),more:true,next_cursor:'opaque-earlier-b'};
  else page={entries:entries(gid,1,200),more:false,next_cursor:null};
  if(m.cursor==='opaque-earlier-a'){
   h.wrong={m,send,page,gid};await send({t:'assist_log_res',r:m.r,gid:'old-grant',entries:[{gid:'old-grant',command:'CROSS-GRANT-RECORD'}],more:false,next_cursor:null});return true;
  }
  const response={t:'assist_log_res',r:m.r,gid,...page,report:{id:gid,summary:lang==='zh'?'修复摘要':'Repair summary'}};h.last=response;await send(response);return true;
 };
 const p=await newPage(B,width,900,theme,{touch:width<500,allow:[web.url,url,fake.relay+'/']});
 const click=async id=>{await evaluate(p,`document.getElementById('${id}').scrollIntoView({block:'center'})`);await sleep(30);const r=await rect(p,'#'+id);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});};
 const dbg=()=>evaluate(p,`import('./js/assist-terminal.js').then(m=>m._debug())`),text=()=>evaluate(p,`document.getElementById('ast-log-text').textContent`);
 const save=async name=>{await evaluate(p,`document.getElementById('ast-logs').scrollIntoView({block:'start'})`);await sleep(60);writeFileSync(resolve(out,`${name}-${lang}-${theme}-${width}.png`),await shoot(p,''));};
 try{
  await navigate(p,fake.newPairing(url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.openRescue();return true})`);await sendState(G1);await waitFor(p,`!!document.getElementById('ast-open')`);await click('ast-open');await click('ast-log');await waitFor(p,`!document.getElementById('ast-logs').hidden`);
  await waitFor(p,`import('./js/assist-terminal.js').then(m=>m._debug().log?.count===50)`);const newest=h.last;
  check((await text()).includes('record-450')&&!(await text()).includes('record-200'),'latest page is bounded and does not pretend to include earlier entries');await save('latest-page');
  if(process.env.P468_LOG_SHOTS_ONLY==='1'){await evaluate(p,`document.querySelector('#ast-logs details').open=true`);check(await evaluate(p,`!document.getElementById('ast-log-scope').textContent.includes('opaque')&&document.getElementById('ast-log-scope').textContent.includes('${G1.slice(0,8)}…')`),'display contains only human page range');await save('human-range');console.log('PASS display-only',lang,theme,width);continue;}
  const seen=new Set((await text()).match(/record-\d{3}/g));await click('ast-log-earlier');for(let i=0;i<50&&!h.wrong;i++)await sleep(20);
  check(!!h.wrong&&(await dbg()).logBusy,'wrong-gid response cannot finish a current page request');check(!(await text()).includes('CROSS-GRANT-RECORD'),'cross-grant record not displayed');
  const wrong=h.wrong;await wrong.send({t:'assist_log_res',r:wrong.m.r,gid:wrong.gid,...wrong.page,report:{id:wrong.gid}});await waitFor(p,`import('./js/assist-terminal.js').then(m=>m._debug().log?.count===200&&!m._debug().logBusy)`);
  for(const x of (await text()).match(/record-\d{3}/g)||[])seen.add(x);check(!(await text()).includes('record-450'),'only one page is retained in the displayed log');
  await fake.send(newest);await sleep(100);check((await dbg()).log.cursor==='opaque-earlier-a','duplicate old response cannot replace current page');
  check(await evaluate(p,`document.getElementById('ast-log-scope').textContent.includes('${G1.slice(0,8)}…')&&!document.getElementById('ast-log-scope').textContent.includes('opaque-earlier-a')&&!document.getElementById('ast-log-scope').textContent.includes('opaque-earlier-b')`),'customer page range hides raw cursor tokens');
  await click('ast-log-earlier');await waitFor(p,`import('./js/assist-terminal.js').then(m=>m._debug().log?.more===false&&m._debug().log?.number===3)`);
  for(const x of (await text()).match(/record-\d{3}/g)||[])seen.add(x);check(seen.size===450,'all 450 records can be read across pages');check(await evaluate(p,`document.getElementById('ast-log-earlier').hidden`),'earlier control ends at log beginning');await save('beginning-page');
  h.hold=true;await click('ast-log');for(let i=0;i<50&&!h.held;i++)await sleep(20);check(!!h.held,'old-grant latest request held');await sendState(G2);await sleep(80);check((await dbg()).log===null&&!(await dbg()).logBusy,'changing grant clears cursor and cancels pending page');
  const old=h.held;h.hold=false;await click('ast-log');await waitFor(p,`import('./js/assist-terminal.js').then(m=>m._debug().log?.gid==='${G2}')`);await old.send({t:'assist_log_res',r:old.m.r,gid:G1,entries:entries(G1,401,450),more:true,next_cursor:'old-cursor'});await sleep(100);check((await dbg()).log.gid===G2&&!(await text()).includes('record-450'),'old-grant delayed result remains isolated');
  h.mode='pruned';await click('ast-log');await waitFor(p,`import('./js/assist-terminal.js').then(m=>m._debug().logError==='pruned')`);check(await evaluate(p,`document.getElementById('ast-log-status').textContent.length>20`),'pruned records show a visible explanation');await save('pruned');
  await p.send('Page.reload');await waitState(p,'ready');await sleep(150);check((await dbg()).log===null,'reload retains no old page or cursor');
  check(!p.offsite.length,'no off-site requests');check(!p.problems.filter(x=>!/WebSocket|ERR_CONNECTION|net::/i.test(x)).length,'no console problems');console.log('PASS',lang,theme,width);
 }finally{await p.dispose();}
}
writeFileSync(resolve(out,'result.json'),JSON.stringify({checks,kind:'real-page-local-noise-readonly-pagination',recordsPerGrant:450,displayOnly:process.env.P468_LOG_SHOTS_ONLY==='1'},null,2)+'\n');console.log('checks',checks);
}finally{await B.close();await fake.stop();await web.stop();}
