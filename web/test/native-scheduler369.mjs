// Synthetic paired host only. Browser/network isolation is supplied by the established harness.
// No system scheduler, production host, external origin or customer data is used.
import assert from 'node:assert/strict';
import {accessSync, constants, writeFileSync, mkdirSync} from 'node:fs';
const executable=process.env.CHROMIUM || '/usr/bin/chromium';
try { accessSync(executable, constants.X_OK); }
catch { console.error('FAIL: Chromium unavailable; 0 browser cases executed (no install attempted)'); process.exit(1); }
const {startWebServer}=await import('./serve.mjs');
const {startFakeHost}=await import('./fakehost.mjs');
const {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep}=await import('./browser.mjs');
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost();
const B=await launch();
let count=0;
try {
  fake.reset();fake.st.caps=['jcards1'];
  fake.st.prefs={appearance:{language:'en',theme:'light'},voice:{speak_replies:false,wake_enabled:false}};
  const target={platform:'linux',source:'systemd',scope:'user',id:'fixture.timer',snapshot:'a'.repeat(64),op:'disable'};
  const writes=[];
  fake.st.onApp=async(c,m,send)=>{
    const reply=(t,v)=>send({t,r:m.r,...v});
    if(m.t==='jcard_list') {await reply('jcard_items',{cards:[{id:'automation',name:{en:'Automation'},on:true,icon:'calendar',accent:'magic-carpet'}]});return true;}
    if(m.t==='jcard_get') {await reply('jcard_get',{ok:true,id:'automation',template:'automation',items:[
      {id:'fixture',name:'fixture',source:'systemd',scope:'user',owner:'unknown',last_result:'exit-code',control:{supported:true,target:{...target}}},
      {id:'readonly',name:'readonly',source:'cron-system',last_result:'unknown',control:{supported:false,reason:'read_only'}}]});return true;}
    if(m.t==='native_scheduler_control') {writes.push(m);assert.equal(m.id,target.id);assert.equal(m.snapshot,target.snapshot);assert.equal(m.op,target.op);assert.ok(m.sig&&m.n);target.op=m.op==='disable'?'restore':'disable';target.snapshot=(m.op==='disable'?'b':'a').repeat(64);await reply('ctl_res',{action:m.t,ok:true});return true;}
    return false;
  };
  const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
  try {
    await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
    await evaluate(p,"document.getElementById('phone-env-dismiss')?.click()");
    await evaluate(p,"import('./js/five-pages.js').then(m=>m.openTarget('automation'))");
    await waitFor(p,"document.querySelector('.jc-body[data-card=automation] .jc-automation')");
    const text=await evaluate(p,"document.querySelector('.jc-body').innerText");
    assert.ok(text.includes('systemd')&&text.includes('Last result')&&text.includes('exit-code'));count++;
    assert.equal(await evaluate(p,"[...document.querySelectorAll('[data-item=readonly] button')].filter(b=>b.textContent==='Disable'||b.textContent==='Restore').length"),0);count++;
    await evaluate(p,"[...document.querySelectorAll('[data-item=fixture] button')].find(b=>b.textContent==='Disable').click()");
    await waitFor(p,"[...document.querySelectorAll('[data-item=fixture] button')].some(b=>b.textContent==='Restore')");
    assert.equal(writes.length,1);count++;
    await evaluate(p,"[...document.querySelectorAll('[data-item=fixture] button')].find(b=>b.textContent==='Restore').click()");
    await waitFor(p,"[...document.querySelectorAll('[data-item=fixture] button')].some(b=>b.textContent==='Disable')");
    // The fake Host validates fixed target fields; cryptographic Host verification is in the Python suite.
    assert.equal(writes.length,2);assert.equal(writes[1].op,'restore');count++;
    await sleep(3000);
    mkdirSync(new URL('../../../reports/qa/p0280-scheduler/',import.meta.url),{recursive:true});
    writeFileSync(new URL('../../../reports/qa/p0280-scheduler/browser.png',import.meta.url),await shoot(p));
  } finally { await p.dispose(); }
  console.log(`PASS: ${count} synthetic browser cases`);
} finally { await B.close(); await fake.stop(); await web.stop(); }
