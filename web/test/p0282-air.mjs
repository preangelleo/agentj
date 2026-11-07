#!/usr/bin/env node
// Isolated real client + Noise transport. No physical human UV is claimed.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const sha=file=>createHash('sha256').update(readFileSync(file)).digest('hex');
process.env.P541_LIBRARY='1';
const H=await import('./p541.mjs');
const wire=await import('../../protocol/wire.js');
const {waitFor,sleep,shoot}=await import('./browser.mjs');
const out=process.env.P0282_AIR_OUT||'reports/qa/p0282-air-web';mkdirSync(out,{recursive:true});
const rows=[];
try{
 for(const [device,w,h] of [['android',390,844],['iphone',390,844],['mac-window',600,900],['desktop',1440,900]])for(const theme of ['light','dark']){
  const p=await H.setup(w,h,theme);if(device==='android')await p.send('Emulation.setUserAgentOverride',{userAgent:'Mozilla/5.0 (Linux; Android 16; Test Phone) AppleWebKit/537.36 Chrome/142.0.0.0 Mobile Safari/537.36',platform:'Linux armv8l'});let reviews=0;
  H.fake.st.onApp=async(c,m,send)=>{if(m.t==='approval_review'){reviews++;await send({t:'approval_review_result',id:m.id,op:m.op,event_id:m.event_id,ok:false,why:'authority_changed'});return true;}return false;};
  await H.ev(p,`document.getElementById('sheetClose').click();document.getElementById('pending541').click()`);await sleep(250);
  const ordinary=await H.ev(p,`document.body.dataset.sheet==='1'`);
  const before=out+`/${device}-${theme}-fold.png`;writeFileSync(before,await shoot(p));
  assert.ok(ordinary,'pending ordinary card reopens locally despite host task context change');assert.equal(reviews,0,'local fold/reopen sends no review or authorization');
  await H.fake.send({t:'ask_done',id:H.currentCard().id,result:'deny'});await sleep(100);
  const id='02820000000000000000000000000001';
  const e={...structuredClone(H.currentCard().envelope),request_id:id,title:'查看领取测试',source:{channel:'unknown',verified:false,suspicious:true,text:'',message_id:null},special:{tool:'SecretPickup',pickup_uv_allowed:true},action:'仅在本人确认后显示',details:'隔离测试元数据'};
  const card={t:'secret_out',id,n:'0'.repeat(32),kind:'text',name:'DEMO_KEY',purpose:'隔离领取回放',filename:'',size:8,ttl:600,fa:'c3ludGhldGljLXJldmlldy1jcmVk',fa_s:'local',pickup_uv_allowed:true,v:2,envelope:e,envelope_sha256:await wire.approvalEnvelopeDigest(e),revision:e.revision,review_state:'pending'};
  await H.fake.send(card);await waitFor(p,`document.getElementById('sout')&&!document.getElementById('sout').hidden`);await sleep(150);
  assert.equal(await H.ev(p,`document.getElementById('sout-open').disabled`),false,'signed Host-owned pickup-only UV allowance enables unknown-source own pickup');
  await H.ev(p,`document.querySelector('#sout .special-fold541').click();document.getElementById('pending541').click()`);await sleep(150);
  assert.equal(await H.ev(p,`document.getElementById('sout').hidden`),false,'same secret card reopens after local collapse');assert.equal(reviews,0,'secret local reopen never rotates the challenge');
  const geometry=await H.ev(p,`(()=>{const a=document.querySelector('#sout').getBoundingClientRect(),b=document.getElementById('stage').getBoundingClientRect(),c=document.querySelector('.composer').getBoundingClientRect();return {stageTop:b.top,stageBottom:b.bottom,cardTop:a.top,cardBottom:a.bottom,composerTop:c.top,horizontalOverflow:document.getElementById('sout').scrollWidth>document.getElementById('sout').clientWidth,status:document.querySelector('#sout .special-status0282').textContent};})()`);
  assert.ok(geometry.cardTop>=geometry.stageTop-1&&geometry.cardBottom<=geometry.stageBottom+1,'special approval stays in the ordinary reading region');assert.ok(geometry.cardBottom<=geometry.composerTop+1,'composer and five actions remain uncovered');assert.equal(geometry.horizontalOverflow,false);assert.equal(geometry.status,'等待你确认');
  const after=out+`/${device}-${theme}-pickup.png`;writeFileSync(after,await shoot(p));
  const mod=`import('./js/approval-special541.js')`;
  assert.equal(await H.ev(p,`${mod}.then(m=>m.canSpecialApprove({approval_v:2,envelope_verified:true,pickup_uv_allowed:true,review_state:'pending',envelope:{special:{tool:'Elevate',pickup_uv_allowed:true},source:{verified:false,suspicious:false}}}))`),false,'pickup flag cannot authorize elevation');
  assert.equal(await H.ev(p,`${mod}.then(m=>m.canSpecialApprove({approval_v:2,envelope_verified:true,pickup_uv_allowed:true,review_state:'pending',envelope:{special:{tool:'SecretPickup',pickup_uv_allowed:true},source:{verified:false,suspicious:true}}}))`),false,'known external suspicious pickup stays disabled');
  assert.equal(await H.ev(p,`${mod}.then(m=>m.canSpecialApprove({approval_v:2,envelope_verified:false,pickup_uv_allowed:true,review_state:'pending',envelope:{special:{tool:'SecretPickup',pickup_uv_allowed:true},source:{verified:false,suspicious:false}}}))`),false,'invalid envelope cannot enable UV');
  await H.fake.send({t:'secret_out_done',id,result:'declined'});await sleep(100);
  assert.equal(await H.ev(p,`document.getElementById('pending541').hidden`),true,'terminal card cannot be locally reopened');
  rows.push({device,width:w,height:h,w,h,theme,ordinary,reviews,geometry,before,after,file:after,sha256:sha(after)});await p.dispose();
 }
}finally{writeFileSync(out+'/measurements.json',JSON.stringify({schema:'agentj-approval-layout-v1',source_css_sha256:sha(new URL('../public/app.css',import.meta.url)),producer_sha256:sha(new URL(import.meta.url)),rows},null,2));writeFileSync(out+'/result.json',JSON.stringify({rows},null,2));await H.B.close();await H.fake.stop();await H.web.stop();}
console.log('PASS p0282-air '+rows.length+' configurations; pending local reopen and signed recipient pickup UI with negative controls');
