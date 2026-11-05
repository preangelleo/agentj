// Real paired Noise client; host lifecycle replies are a protocol fixture, not a live-host signature test.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,shoot,sleep,key} from './browser.mjs';
const before=process.env.P474_MCP_BEFORE;
const out=`reports/qa/p474-mcp/${before?'before':'after'}`;mkdirSync(out,{recursive:true});
const web=await startWebServer(before?{publicDir:before}:{}),fake=await startFakeHost(),B=await launch();
const calls=[],connectors=[];let refuse=false;
fake.st.extraCaps=['hooks1','externalmcp1'];
fake.st.onApp=async(c,m,send)=>{
 if(!['hook_list','hook_set'].includes(m.t))return false;
 calls.push(m);
 if(m.t==='hook_set'){
  if(refuse){await send({t:'hook_list_res',r:m.r,ok:false,why:'denied'});return true;}
  if(m.op==='mcp-create')connectors.push({id:'M'.repeat(22),name:m.name,origins:m.origins,state:'active',scope:'request-only',created:1700000000,url:'https://hook.agentj.app/'+'H'.repeat(22)+'/'+'M'.repeat(22)});
  if(m.op==='mcp-revoke')connectors[0].state='revoked';
 }
 await send({t:'hook_list_res',r:m.r,ok:true,up:true,hooks:[],recent:[],connectors});return true;
};
try{
 const p=await newPage(B,390,844,'dark',{allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,"document.getElementById('phone-env-dismiss')?.click();document.getElementById('setBtn').click();document.getElementById('set-hooks-nav').click()");
 await waitFor(p,"document.getElementById('settings').dataset.sub==='hooks'");
 if(!before){
  await waitFor(p,"!!document.querySelector('.mcp474-form')");
  await evaluate(p,"document.querySelector('.mcp474-name').value='Assistant';document.querySelector('.mcp474-origins').value='https://assistant.example/path';document.querySelector('.mcp474-form').requestSubmit()");
  assert.equal(calls.filter(x=>x.t==='hook_set').length,0);
  assert.ok(await evaluate(p,"document.querySelector('.mcp474-status').textContent.includes('Enter a name')"));
  await evaluate(p,"document.querySelector('.mcp474-origins').value='https://assistant.example';document.querySelector('.mcp474-form').requestSubmit()");
  await waitFor(p,"!!document.querySelector('.mcp474-rotate')").catch(async e=>{console.error(JSON.stringify({calls,dom:await evaluate(p,"document.querySelector('.mcp474').textContent")}));throw e;});
  await evaluate(p,"document.querySelector('.mcp474-copy').click()");
  await waitFor(p,"navigator.clipboard.readText().then(s=>s==='https://hook.agentj.app/'+ 'H'.repeat(22)+'/'+ 'M'.repeat(22))");
  assert.deepEqual(calls.find(x=>x.op==='mcp-create').origins,['https://assistant.example']);
  assert.ok(await evaluate(p,"document.querySelector('.mcp474').textContent.includes('hosted relay memory')"));
  assert.ok(await evaluate(p,"document.querySelector('.mcp474').textContent.includes('pickup card')"));
 }
 for(const language of ['en','zh'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await p.send('Emulation.setDeviceMetricsOverride',{width,height:width===390?844:900,deviceScaleFactor:1,mobile:width===390});
  await fake.send({t:'preferences',value:{appearance:{language,theme},voice:{speak_replies:false,wake_enabled:false}},problem:null});
  await waitFor(p,`document.documentElement.dataset.theme===${JSON.stringify(theme)}`);
  assert.equal(await evaluate(p,'document.documentElement.dataset.theme'),theme,'requested appearance.theme reached the real DOM');
  // Settings intentionally keeps its Noir canvas in both preferences (menu.css).
  await sleep(150);
  if(!before){
   assert.ok(await evaluate(p,"document.getElementById('set-title').textContent.includes('Webhook')"));
   assert.ok(await evaluate(p,`document.querySelector('.mcp474-status').textContent.includes(${JSON.stringify(language==='zh'?'操作已确认':'Action confirmed')})`));
   assert.ok(await evaluate(p,"[...document.querySelectorAll('.mcp474 button,.mcp474 input,.mcp474 textarea')].filter(b=>b.type!=='hidden').every(b=>b.getBoundingClientRect().height>=44)"));
   assert.ok(await evaluate(p,"document.documentElement.scrollWidth<=innerWidth"));
   await evaluate(p,"document.querySelector('.mcp474').scrollIntoView({block:'start'})");
  }
  writeFileSync(`${out}/${language}-${theme}-${width}.png`,await shoot(p,''));
 }
 if(!before){
  await evaluate(p,"document.querySelector('.mcp474-rotate').click()");await waitFor(p,"!document.getElementById('confirm').hidden");await key(p,'Escape');await sleep(80);
  assert.equal(calls.filter(x=>x.op==='mcp-rotate').length,0);
  await evaluate(p,"document.querySelector('.mcp474-rotate').click()");await waitFor(p,"!document.getElementById('confirm').hidden");await evaluate(p,"document.getElementById('confirm-yes').click()");await waitFor(p,"!!document.querySelector('.mcp474-rotate:not(:disabled)')");
  assert.equal(calls.filter(x=>x.op==='mcp-rotate').length,1);
  refuse=true;
  await evaluate(p,"document.querySelector('.mcp474-revoke').click()");await waitFor(p,"!document.getElementById('confirm').hidden");await evaluate(p,"document.getElementById('confirm-yes').click()");await sleep(180);
  assert.ok(await evaluate(p,"!!document.querySelector('.mcp474-rotate:not(:disabled)')"));
  assert.ok(await evaluate(p,"document.querySelector('.mcp474-status').textContent.includes('操作未完成')"));
  refuse=false;
  await evaluate(p,"document.querySelector('.mcp474-revoke').click()");await waitFor(p,"!document.getElementById('confirm').hidden");await evaluate(p,"document.getElementById('confirm-yes').click()");await waitFor(p,"document.querySelector('.mcp474-list li').dataset.state==='revoked'");
  assert.ok(await evaluate(p,"!document.querySelector('.mcp474-list button')"));
  console.log('PASS MCP Noise phone: validation/create/rotate/cancel/refused/revoke; 390/1440 × dark/light × en/zh');
 }
}finally{await B.close();await fake.stop();await web.stop();}
