// Actual owner UI over paired Noise, fixture host. No production or credential access.
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitFor,waitState,shoot} from './browser.mjs';
import * as wire from '../../protocol/wire.js';
import {parityRecorder} from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p82-owner-browser.mjs'),web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const out=new URL('../../../reports/qa/p82/owner-browser/',import.meta.url);mkdirSync(out,{recursive:true});
let failed=0;
try{
 for(const language of ['zh','en']){
  const name='owner-bots-'+language;let p;
  try{
   fake.reset();let config=null;const writes=[];
   fake.st.onApp=async(c,m,send)=>{
    if(!['bots_read','bots_write'].includes(m.t))return false;
    if(m.t==='bots_write'){
     const device=fake.devs.find(d=>d.id===c.devId),pk=await webcrypto.subtle.importKey('raw',wire.unb64u(device.sk),'Ed25519',false,['verify']);
     assert.equal(await webcrypto.subtle.verify('Ed25519',pk,wire.unb64u(m.sig),await wire.controlMessage(fake.channel,c.devId,'bots_write',m.n,m.ts,{request:m.request})),true);
     writes.push(m.request);if(m.request.op==='create')config={id:'a'.repeat(32),description:'Public help',prompt:'Product questions only',background:'',language:'auto',timezone:'Asia/Tokyo',hours:null,provider:{source:'main'},limits:{visitor_messages:20,bot_messages:100,host_messages:100,host_tokens:100000,concurrency:4},outbound_domains:[],embed_origins:[],...m.request.config};
     if(m.request.op==='save')config={id:config.id,...m.request.config};
     await send({t:'ctl_res',r:m.r,action:m.t,ok:true,bot:config});return true;
    }
    const r={t:'bots_res',r:m.r,ok:true};
    if(m.request.op==='list')Object.assign(r,{bots:config?[config]:[],host_timezone:'Asia/Tokyo'});
    if(m.request.op==='detail')Object.assign(r,{bot:config,knowledge:[{name:'manual.md',bytes:12}],tools:[{name:'order_status',description:'Own authorized order',enabled:false,level:'write'}],provider:{provider:'fixture',model:'small'}});
    if(m.request.op==='history')Object.assign(r,m.request.visitor?{messages:[{role:'user',text:'<img src=x onerror=alert(1)>',created:1}]}:{sessions:[{visitor:'b'.repeat(32),messages:1}]});
    if(m.request.op==='statistics')Object.assign(r,{days:[{day:'2026-10-09',calls:3,charged_tokens:12,actual_tokens:12}],tools:[{day:'2026-10-09',tool:'order_status',calls:1}]});
    await send(r);return true;
   };
   p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/']});await navigate(p,fake.newPairing(web.url+'?lang='+language));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await evaluate(p,"import('/js/bots.js').then(m=>m.open())");await waitFor(p,"document.querySelector('#bots-side button:nth-child(2)')");
   const click=expression=>evaluate(p,expression+'.click()');
   await click("document.querySelector('#bots-side button:nth-child(2)')");await click("document.querySelector('#bots-main button')");
   await evaluate(p,"document.querySelectorAll('#bots-main input')[0].value='Shop';document.querySelectorAll('#bots-main input')[1].value='sample-shop'");await click("document.querySelector('#bots-main button')");await evaluate(p,"document.querySelectorAll('#bots-main input[type=checkbox]').forEach(e=>e.checked=true)");await click("document.querySelector('#bots-main button')");
   await waitFor(p,"document.querySelectorAll('.bots-tabs button').length===7");assert.equal(writes[0].config.enabled,false);assert.equal(writes[0].config.timezone,'Asia/Tokyo');assert.equal(writes[0].config.subscription_risk_accepted,true);
   for(let i=0;i<7;i++){
    await click(`document.querySelectorAll('.bots-tabs button')[${i}]`);await waitFor(p,`document.querySelectorAll('.bots-tabs button')[${i}]?.getAttribute('aria-current')==='page'`);
    if(i===0){assert.match(await evaluate(p,"document.getElementById('bots-main').textContent"),/OpenRouter/);const widths=await evaluate(p,"[...document.querySelectorAll('.bots-tabs button')].map(e=>({w:e.getBoundingClientRect().width,parent:e.parentElement.getBoundingClientRect().width,nowrap:getComputedStyle(e).whiteSpace}))");assert.ok(widths.every(e=>Math.abs(e.w-e.parent)<2&&e.nowrap==='nowrap'));writeFileSync(new URL(language+'-settings-390.png',out),await shoot(p));}
    if(i===3)assert.match(await evaluate(p,"document.getElementById('bots-main').textContent"),/WRITE|写入/);
    if(i===4){await waitFor(p,"document.querySelector('#bots-main button:not(.bots-tabs button)')");assert.equal(await evaluate(p,"document.querySelectorAll('#bots-main textarea,#bots-main input').length"),0);}
    if(i===5)await waitFor(p,"document.getElementById('bots-main').textContent.includes('12')");
    if(i===6)assert.match(await evaluate(p,"document.getElementById('bots-main').textContent"),/https:\/\/agentj.app\/bots\/sample-shop/);
   }
   const geometry=await evaluate(p,"({overflow:document.documentElement.scrollWidth>innerWidth,taps:[...document.querySelectorAll('#bots-view button')].filter(e=>e.getClientRects().length).map(e=>Math.round(e.getBoundingClientRect().height))})");
   assert.equal(geometry.overflow,false);assert.ok(geometry.taps.every(h=>h>=44),JSON.stringify(geometry));assert.deepEqual(p.problems,[]);assert.equal(p.offsite.length,0);
   writeFileSync(new URL(language+'.png',out),await shoot(p));rec.record(name,true);console.log(name+' PASS');
  }catch(e){failed++;rec.record(name,false);console.error(name,e.stack);}finally{await p?.dispose();}
 }
}finally{rec.write();await B.close();await fake.stop();await web.stop();}
process.exitCode=failed?1:0;
