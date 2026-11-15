// Actual browser reverse-code crypto/workerd rendezvous and paired Noise signed control.
// Synthetic Host result only: this UI test does not qualify backend admission, owner UV or physical pairing.
import assert from'node:assert/strict';import{mkdirSync,writeFileSync}from'node:fs';
import{startWebServer}from'./serve.mjs';import{startFakeHost}from'./fakehost.mjs';import{startLocalRelay}from'../../relay/test/local_relay.mjs';
import{launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep}from'./browser.mjs';
const out=process.env.OLARES_RUN_OUT||'reports/qa/p252-eviction';mkdirSync(out,{recursive:true});let checks=0;const rows=[];
const relay=await startLocalRelay({}),fake=await startFakeHost(),web=await startWebServer({...process.env.P252_ACK_PUBLIC?{publicDir:process.env.P252_ACK_PUBLIC}:{},relayCsp:relay.url+' '+fake.relay}),B=await launch();
try{for(const width of[390,1440])for(const lang of['zh','en'])for(const evicted of(process.env.P252_ACK_SCENARIOS==='unnamed'?['unnamed']:[true,false])){
 const theme=lang==='zh'?'light':'dark';fake.reset();fake.setUp(true);fake.st.remotes={items:[],limit:10};fake.st.prefs={appearance:{theme,language:lang}};const calls=[];
 fake.st.onApp=async(c,m,send)=>{
  if(m.t==='dev_add'){
   assert.equal(await fake.verifyControl(c,m.t,m,{rv:m.rv}),true);calls.push(m.t);await send({t:'ctl_res',r:m.r,action:m.t,ok:true,rv:true,full:evicted,link:fake.newPairing(web.url)});return true;
  }
  if(m.t==='dev_confirm'){
   assert.equal(await fake.verifyControl(c,m.t,m,{device:'wrong-target',code:m.code}),false);assert.equal(await fake.verifyControl(c,m.t,m,{device:m.device,code:m.code}),true);calls.push(m.t);await fake.approve();await send({t:'ctl_res',r:m.r,action:m.t,ok:true,name:'Synthetic new remote',...(evicted?{evicted:evicted==='unnamed'?{}:{name:'Synthetic old remote'}}:{})});return true;
  }return false;
 };
 const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/',relay.url+'/']}),q=await newPage(B,390,900,theme,{allow:[web.url,fake.relay+'/',relay.url+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click()`);
  await navigate(q,web.url+'?relay='+encodeURIComponent(relay.url));await waitFor(q,`document.querySelector('#rv-status')?.dataset.k==='wait'&&!!document.querySelector('#rv-qr')?.dataset.link`);
  const link=await evaluate(q,`document.querySelector('#rv-qr').dataset.link`);await evaluate(p,`import('./js/rvpair.js').then(m=>m.approve(${JSON.stringify(link)}))`);
  await waitState(q,'awaiting-approval');const sas=await evaluate(q,`document.getElementById('sas').textContent.replace(/\\D/g,'')`);
  const code=new URL(link).hash.slice(3);const parsed=await evaluate(p,`import('./proto/wire.js').then(m=>{const c=m.parseReverse(${JSON.stringify(link)});return{channel:c.channel}})`);
  await fake.remotePending(fake.devs.at(-1).id,'Synthetic new remote',{rv:parsed.channel,sas,...(evicted?{evict:{name:'Synthetic old remote'}}:{})});
  await waitFor(p,`document.getElementById('rvDlg').dataset.phase==='confirm'`);await evaluate(p,`document.getElementById('rvYes').click()`);await waitFor(p,`document.getElementById('rvDlg').dataset.phase==='done'`);
  await sleep(2900);const view=await evaluate(p,`(()=>{const n=document.getElementById('rvDlg'),b=document.getElementById('rvNo'),r=b.getBoundingClientRect();return{hidden:n.hidden,text:document.getElementById('rvDone').textContent,h:r.height,w:r.width}})()`);
  if(evicted){assert.equal(view.hidden,false);assert.ok(view.text.includes(evicted==='unnamed'?'—':'Synthetic old remote'));assert.ok(view.h>=44&&view.w>=44);writeFileSync(`${out}/${width}-${theme}-${lang}-eviction.png`,await shoot(p));await evaluate(p,`document.getElementById('rvNo').click()`);await waitFor(p,`document.getElementById('rvDlg').hidden`);}else assert.equal(view.hidden,true);
  assert.deepEqual(calls,['dev_add','dev_confirm']);await waitState(q,'ready');checks+=5;rows.push({width,lang,theme,evicted,explicitAck:evicted,signedMutations:calls.length});
 }catch(e){writeFileSync(`${out}/${width}-${lang}-${evicted}-FAIL.png`,await shoot(p));throw e;}finally{await p.dispose();await q.dispose();}
}writeFileSync(out+'/results.json',JSON.stringify({checks,rows,scope:'actual workerd rendezvous/reverse crypto/Noise signed controls; synthetic Host confirmation results; no real host/UV'},null,2));console.log('P252 eviction ack PASS '+checks);}finally{await B.close();await fake.stop();await web.stop();await relay.stop();}
