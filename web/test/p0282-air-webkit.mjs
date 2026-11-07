// Actual WebKit page + Noise fixture; engine has no native virtual WebAuthn API.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {approvalEnvelopeDigest} from '../../protocol/wire.js';
const require=createRequire(import.meta.url),pw=require(process.env.AJ_PLAYWRIGHT_MODULE||'playwright');
const out=process.env.P0282_AIR_WEBKIT_OUT||'reports/qa/p0282-air-web/webkit';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await pw.webkit.launch(),rows=[];
try{
 for(const w of [390,600])for(const theme of ['light','dark']){
  fake.reset();const ctx=await B.newContext({viewport:{width:w,height:900},isMobile:true,hasTouch:true,colorScheme:theme});const p=await ctx.newPage();
  await p.goto(fake.newPairing(web.url.replace('127.0.0.1','localhost')+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await p.evaluate(()=>document.getElementById('phone-env-dismiss')?.click());
  const id='02820000000000000000000000000002',e={v:2,channel:fake.channel,session:String(fake.st.epoch),request_id:id,revision:1,nonce:'nonce',title:'领取流程测试',source:{channel:'unknown',verified:false,suspicious:false,text:'',message_id:null},action:'仅本人验证后显示',details:'测试元数据',special:{tool:'SecretPickup',pickup_uv_allowed:true},effects:['secret_display'],plan:null,expires_at:Math.floor(Date.now()/1000)+600};
  await fake.send({t:'secret_out',id,n:'0'.repeat(32),kind:'text',name:'DEMO_KEY',purpose:'隔离测试',filename:'',size:8,ttl:600,fa:'c3ludGhldGljLXJldmlldy1jcmVk',fa_s:'local',pickup_uv_allowed:true,v:2,envelope:e,envelope_sha256:await approvalEnvelopeDigest(e),revision:1,review_state:'pending'});
  await p.waitForFunction(()=>document.getElementById('sout-open')&&!document.getElementById('sout-open').disabled);await p.evaluate(()=>document.querySelector('#sout .special-fold541').click());await p.waitForFunction(()=>document.getElementById('sout').hidden);await p.evaluate(()=>document.getElementById('pending541').click());await p.waitForFunction(()=>!document.getElementById('sout').hidden);
  const g=await p.evaluate(()=>{const a=document.getElementById('sout').getBoundingClientRect(),b=document.getElementById('stage').getBoundingClientRect(),c=document.querySelector('.composer').getBoundingClientRect();return{cardTop:a.top,cardBottom:a.bottom,stageTop:b.top,stageBottom:b.bottom,composerTop:c.top,overflow:document.documentElement.scrollWidth>innerWidth};});
  assert.ok(g.cardTop>=g.stageTop-1&&g.cardBottom<=g.stageBottom+1);assert.ok(g.cardBottom<=g.composerTop+1);assert.equal(g.overflow,false);assert.equal(fake.log.filter(m=>m.t==='approval_review').length,0);
  await p.screenshot({path:out+`/${w}-${theme}.png`});rows.push({w,theme,engine:'WebKit',physical:false,native_webauthn:false,...g});await ctx.close();
 }
}finally{writeFileSync(out+'/result.json',JSON.stringify({rows},null,2));await B.close();await fake.stop();await web.stop();}
console.log('PASS p0282-air WebKit '+rows.length+' cases; no physical or native WebAuthn claim');
