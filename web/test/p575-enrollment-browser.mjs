// Synthetic enrollment frames through actual paired Noise. No approval, real password or hardware prompt.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitState,waitFor,shoot,key,sleep} from './browser.mjs';
const out='reports/qa/p575-enrollment/';mkdirSync(out,{recursive:true});
const web=await startWebServer(),fake=await startFakeHost(),B=await launch(),results=[];
const b64=n=>Buffer.alloc(n,7).toString('base64url');
try{for(const width of [390,1440])for(const theme of ['light','dark'])for(const lang of ['zh','en']){
 fake.reset();fake.st.prefs={appearance:{theme,language:lang}};
 const p=await newPage(B,width,900,theme,{allow:[web.url,fake.relay+'/']});
 try{
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`document.getElementById('phone-env-dismiss')?.click()`);
  const msg={t:'pk_peer_ask',id:b64(16),name:'Synthetic device',at:Date.now()/1000,exp:Math.floor(Date.now()/1000)+300,code:'111111',fa_ch:b64(32),needs_setup:true,bootstrap:true,epk:b64(32),n:'synthetic-nonce',digest:'synthetic-digest'};
  await fake.send(msg);await waitFor(p,`!document.getElementById('pkp')?.hidden&&!!document.querySelector('#pkp input[type=password]')`);
  assert.equal(await evaluate(p,`document.getElementById('pkp-approve').hidden`),true,'first-key bootstrap never fabricates UV');
  const early=await evaluate(p,`(()=>{const n=document.querySelector('#pkp input[type=password]'),r=n.getBoundingClientRect();return {height:r.height,width:r.width,minHeight:getComputedStyle(n).minHeight,animations:document.getElementById('pkp').getAnimations({subtree:true}).map(a=>({state:a.playState,duration:a.effect.getComputedTiming().duration}))}})()`);
  await evaluate(p,`Promise.all(document.getElementById('pkp').getAnimations({subtree:true}).filter(a=>a.effect.getComputedTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})))`);
  const geometry=await evaluate(p,`(()=>{const n=document.querySelector('#pkp input[type=password]'),r=n.getBoundingClientRect(),root=document.querySelector('#pkp .box').getBoundingClientRect();return {w:r.width,h:r.height,left:r.left,right:r.right,boxLeft:root.left,boxRight:root.right,buttons:[...document.querySelectorAll('#pkp button')].filter(n=>n.getClientRects().length).map(n=>{const r=n.getBoundingClientRect();return {id:n.id,w:r.width,h:r.height}})}})()`);
  writeFileSync(out+`${width}-${theme}-${lang}-geometry.json`,JSON.stringify({early,geometry},null,2));
  assert.ok(geometry.w>=44&&geometry.h>=44,JSON.stringify({early,geometry}));assert.ok(geometry.left>=geometry.boxLeft&&geometry.right<=geometry.boxRight);for(const b of geometry.buttons)assert.ok(b.w>=44&&b.h>=44,b.id);
  writeFileSync(out+`${width}-${theme}-${lang}-first-key.png`,await shoot(p));
  await evaluate(p,`window.__bootstrapInput=document.querySelector('#pkp input[type=password]');window.__bootstrapInput.value='synthetic-only-password'`);
  await key(p,'Escape');await waitFor(p,`document.getElementById('pkp').hidden`);assert.equal(await evaluate(p,`window.__bootstrapInput.value`),'','Esc wipes controlled input');
  await fake.send({t:'pk_peer_close',id:msg.id});
  await fake.send({...msg,bootstrap:false,roster:true,id:Buffer.alloc(16,8).toString('base64url')});await waitFor(p,`!document.getElementById('pkp').hidden`);
  assert.equal(await evaluate(p,`!!document.querySelector('#pkp input[type=password]')`),false,'roster path has no password fallback');
  assert.equal(await evaluate(p,`document.getElementById('pkp-approve').hidden`),true);
  writeFileSync(out+`${width}-${theme}-${lang}-no-fallback.png`,await shoot(p));
  assert.equal(fake.log.filter(m=>m.t==='pk_peer_answer'&&m.ok).length,0,'viewing and closing never approve');
  results.push({width,theme,lang,early,geometry});console.log('PASS',width,theme,lang);
 }finally{await p.dispose();}
}}finally{writeFileSync(out+'results.json',JSON.stringify(results,null,2));await B.close();await fake.stop();await web.stop();}
