// R-0005 (approved 0.30.34): paired real Chromium + fake host, synthetic history only. Every served page shows
// source + time + one line on /background rows (a failed row: source · 需检查 + the Agent's line). Screenshots of the
// open /background sheet for zh/en × light/dark × 390/1440.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,key,shoot} from './browser.mjs';
const out=process.env.R0005_OUT || 'reports/qa/r0005';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),browser=await launch(),results=[];
const DATA={
 zh:[[{k:'agent',via:'heartbeat',text:'定时心跳检查'},'检查了 3 个定时任务，全部按时跑完。'],
  [{k:'auto',name:'每日新闻',text:'今日 AI 新闻已发到邮箱，共 12 条'},'已记下。'],
  [{k:'auto',text:'{"schema":"automation-report.v1"}'},'自动化报告已收到：备份 2 个文件夹，用时 41 秒。'],
  [{k:'sys',text:'主机已升级到新版本，服务已重启'},'好的，升级后的检查都通过了。'],
  [{k:'agent',via:'background_done',text:'后台任务结束'},'夜间备份退出码 2：目标磁盘空间不足，需要清理后重跑。','failed',{activity_failure:true}]],
 en:[[{k:'agent',via:'heartbeat',text:'Scheduled heartbeat check'},'Checked 3 scheduled jobs; all ran on time.'],
  [{k:'auto',name:'Daily news',text:"Today's AI news was sent to your inbox, 12 items"},'Noted.'],
  [{k:'auto',text:'{"schema":"automation-report.v1"}'},'Automation report received: 2 folders backed up in 41 s.'],
  [{k:'sys',text:'The host was updated to a new version and restarted'},'Good, the post-update checks passed.'],
  [{k:'agent',via:'background_done',text:'Background job finished'},'Nightly backup exited with code 2: the target disk is full; clean up and run it again.','failed',{activity_failure:true}]]};
const run=async(lang,theme,width)=>{
 fake.reset();fake.st.prefs={appearance:{language:lang,theme}};
 const p=await newPage(browser,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
 try{
  const q='?lang='+lang;
  await navigate(p,fake.newPairing(web.url+q));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await fake.addTurn({k:'phone',text:lang==='zh'?'帮我看一下今晚的安排':'What is on tonight?'},lang==='zh'?'今晚没有安排。':'Nothing is scheduled tonight.');
  await waitFor(p,`document.getElementById('words').textContent.length>0`);
  for(const [src,reply,end,card] of DATA[lang])await fake.addTurn(src,reply,end||'done',card||null);
  await evaluate(p,`document.getElementById('input').focus();document.getElementById('input').value='/background';document.getElementById('input').dispatchEvent(new Event('input'))`);
  await key(p,'Escape');
  const sp=await evaluate(p,`(()=>{const r=document.getElementById('send').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await p.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...sp});
  await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...sp});
  await waitFor(p,`!document.getElementById('silent').hidden && document.querySelectorAll('#silentList details').length===5`);
  const rows=await evaluate(p,`[...document.querySelectorAll('#silentList summary')].map(s=>({t:s.querySelector('.t').textContent,s:s.querySelector('.s').textContent,l:s.querySelector('.l').textContent,h:s.getBoundingClientRect().height,lw:s.querySelector('.l').scrollWidth>s.querySelector('.l').clientWidth}))`);
  const rec=lang==='zh'?'已收起，点开查看':'Collected here; open to inspect',fail=lang==='zh'?'需检查':'Needs attention';
  const d=DATA[lang];
  assert.equal(rows[0].l,d[4][1]);assert.ok(rows[0].s.endsWith(' · '+fail),rows[0].s);
  assert.equal(rows[1].l,d[3][0].text);assert.equal(rows[2].l,d[2][1],'JSON input never becomes the line');
  assert.equal(rows[2].s,lang==='zh'?'后台':'Background','an unnamed automation keeps the kind label');
  assert.equal(rows[3].l,d[1][0].text);assert.match(rows[3].s,lang==='zh'?/每日新闻/:/Daily news/);
  assert.equal(rows[4].l,d[0][1]);
  for(const r of rows){assert.notEqual(r.l,rec);assert.match(r.t,/^\d{2}:\d{2}$/);assert.ok(r.h>=44);}
  // no new control: each row is still exactly time + source + line, the sheet has no extra button
  assert.equal(await evaluate(p,`[...document.querySelectorAll('#silentList summary')].every(s=>s.children.length===3)`),true);
  assert.equal(await evaluate(p,`document.querySelectorAll('#silentList button,#silentList a,#silentList input').length`),0);
  const shot=await shoot(p);
  // tap-to-expand still opens the whole record
  await evaluate(p,`document.querySelector('#silentList details:nth-child(3) summary').click()`);
  await waitFor(p,`document.querySelector('#silentList details:nth-child(3)').open`);
  assert.match(await evaluate(p,`document.querySelector('#silentList details[open]').textContent`),/automation-report\.v1/);
  const proof=await evaluate(p,`({theme:document.documentElement.dataset.theme,scroll:document.documentElement.scrollWidth})`);
  assert.equal(proof.theme,theme);assert.ok(proof.scroll<=width+1);
  await key(p,'Escape');await waitFor(p,`document.getElementById('silent').hidden`);
  assert.deepEqual(p.problems,[]);assert.deepEqual(p.offsite,[]);
  return {shot,rows};
 }finally{await p.dispose();}
};
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  const after=await run(lang,theme,width);
  writeFileSync(join(out,`after-${lang}-${theme}-${width}.png`),after.shot);
  results.push({lang,theme,width,after:after.rows.map(r=>[r.t.length,r.s,r.l]),result:'PASS'});
 }
 writeFileSync(join(out,'results.json'),JSON.stringify(results,null,2));
 console.log(`PASS ${results.length} /background journeys`);console.log('r0005_background_summary PASS');
}finally{await browser.close();await fake.stop();await web.stop();}
