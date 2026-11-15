// Actual paired Noise delivery to the production enrollment UI; rejected branch must never call UV/start another setup.
// Credential identifiers are synthetic. No enrollment or physical authenticator is used.
import assert from'node:assert/strict';import{mkdirSync,writeFileSync}from'node:fs';
import{startWebServer}from'./serve.mjs';import{startFakeHost}from'./fakehost.mjs';
import{launch,newPage,navigate,waitState,waitFor,evaluate,shoot,sleep}from'./browser.mjs';
const out=process.env.OLARES_RUN_OUT||'reports/qa/p0285-ineligible';mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.P0285_KNOWN_PUBLIC?{publicDir:process.env.P0285_KNOWN_PUBLIC}:{}),fake=await startFakeHost(),B=await launch(),rows=[];let checks=0;
const id=Buffer.alloc(16,37).toString('base64url'),fa_ch=Buffer.alloc(32,39).toString('base64url');
try{for(const width of[390,1440])for(const theme of['light','dark'])for(const lang of['zh','en']){
 fake.reset();fake.st.prefs={appearance:{theme,language:lang}};const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`document.getElementById('phone-env-dismiss')?.click();document.getElementById('a2hs-ok')?.click();import('./js/pkpeer.js').then(m=>{window.__uv=0;window.__request=0;m.configure({canAssert:()=>true,approve:()=>{window.__uv++;throw Error('ineligible must never call UV')},ask:async()=>{window.__request++;return{t:'timeout'}}});})`);
  for(const known of[true,false]){
   await evaluate(p,`import('./js/pkpeer.js').then(m=>m._reset())`);
   await fake.send({t:'pk_peer_ask',id,fa_ch,fa:known?'synthetic-synced-credential':'',needs_setup:true,name:'Synthetic remote',code:'123456',exp:Math.floor(Date.now()/1000)+300});
   await waitFor(p,`!!document.getElementById('pkp-setup-first')`);
   const view=await evaluate(p,`(()=>{const n=document.getElementById('pkp'),b=document.getElementById('pkp-approve'),go=document.getElementById('pkp-setup-first'),r=go.getBoundingClientRect();return{body:n.textContent,approve:b.hidden,deny:!document.getElementById('pkp-deny').hidden,width:r.width,height:r.height,overflow:document.documentElement.scrollWidth>innerWidth}})()`);
   assert.equal(view.approve,true);assert.equal(view.deny,true);assert.ok(view.width>=44&&view.height>=44&&!view.overflow);checks+=3;
   if(known){assert.match(view.body,lang==='zh'?/已有验证密钥，但不能批准/:/has a verification key but cannot approve/);assert.doesNotMatch(view.body,lang==='zh'?/尚未设置验证密钥/:/has no verification key/);}else assert.match(view.body,lang==='zh'?/尚未设置验证密钥/:/has no verification key/);checks++;
   await evaluate(p,`document.querySelector('#pkp details')?.setAttribute('open','');document.getElementById('pkp-setup-first').scrollIntoView({block:'center'})`);
   writeFileSync(`${out}/${width}-${theme}-${lang}-${known?'ineligible':'missing'}.png`,await shoot(p));
   await evaluate(p,`document.getElementById('pkp-setup-first').click()`);await waitFor(p,`!document.getElementById('settings').hidden`);await sleep(650);
   assert.deepEqual(await evaluate(p,`({uv:window.__uv,requests:window.__request})`),{uv:0,requests:0});checks++;
   await evaluate(p,`import('./js/settings.js').then(m=>m.close())`);rows.push({width,theme,lang,known,uv:0,newRequests:0});
  }
 }catch(e){writeFileSync(`${out}/${width}-${theme}-${lang}-FAIL.png`,await shoot(p));throw e;}finally{await p.dispose();}
}writeFileSync(out+'/results.json',JSON.stringify({checks,rows,scope:'actual paired Noise rejection UI; spies only prove no UV/no new setup call, not positive biometric qualification'},null,2));console.log('P0285 ineligible PASS '+checks);}finally{await B.close();await fake.stop();await web.stop();}
