// Real Chromium + Noise + virtual UV authenticator; no live service, account or credential.
import assert from 'node:assert/strict';
import {parityRecorder} from '../../parity/lib.mjs';
const rec=parityRecorder('web/test/p450.mjs');let passed=false;
import {mkdirSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync,randomBytes} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,rect,sleep} from './browser.mjs';
import {keysHandler} from './p352-fixture.mjs';
const out=new URL(process.env.P476_ROUTE_SHOTS || '../reports/p450/',import.meta.url);mkdirSync(out,{recursive:true});
const web=await startWebServer(), page=web.url.replace('127.0.0.1','localhost'), fake=await startFakeHost(), b=await launch();
const cred=randomBytes(16), fa=cred.toString('base64url'), priv=generateKeyPairSync('ec',{namedCurve:'P-256'}).privateKey.export({type:'pkcs8',format:'der'}).toString('base64');
const calls=keysHandler(fake.st,{fa}); const prev=fake.st.onApp; let moved=false,unified=false;
fake.st.onApp=async(c,m,send)=>{
 if(m.t==='keys_op'&&m.op==='check'){await send({t:'keys_res',r:m.r,ok:true,valid:false,offer:'FIX_'+'a'.repeat(24),source:'BAILIAN_CODING_PLAN_API_KEY',target:'DASHSCOPE_API_KEY'});return true;}
 if(m.t==='keys_op'&&['move','unify'].includes(m.op)){assert.ok(m.fa?.sig,'write carries a real UV assertion');if(m.op==='move')moved=true;else unified=true;}
 return prev(c,m,async obj=>{
  if(obj.t==='keys'){
   obj.routing={main:'ark-agent-plan/doubao-seed-2.1-pro',effective:true,rows:[{role:'small_model',model:unified?'ark-agent-plan/doubao-seed-2.1-pro':'agentsrelay/claude-small',provider:unified?'ark-agent-plan':'agentsrelay'},{role:'agent:研究员',model:'agentsrelay/research',provider:'agentsrelay'},{role:'future_internal_role',model:'agentsrelay/other',provider:'agentsrelay'}],different:!unified,writable:true,stamp:'b'.repeat(56)};
   if(!moved)obj.base.find(v=>v.id==='bailian').rows.push({type:'sub',name:'BAILIAN_CODING_PLAN_API_KEY',set:true});
  }
  await send(obj);
 });
};
async function click(p,sel){await evaluate(p,`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({block:'center'})`);await sleep(60);const r=await rect(p,sel);for(const type of ['mousePressed','mouseReleased'])await p.send('Input.dispatchMouseEvent',{type,x:r.x,y:r.y,button:'left',clickCount:1});}
try{
 const p=await newPage(b,390,844,'light',{touch:false,allow:[web.url,page,fake.relay+'/']});
 await p.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
 await p.send('WebAuthn.enable',{enableUI:false});const {authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
 await p.send('WebAuthn.addCredential',{authenticatorId,credential:{credentialId:cred.toString('base64'),isResidentCredential:true,rpId:'localhost',privateKey:priv,userHandle:Buffer.from('p450-fixture').toString('base64'),signCount:0}});
 await p.send('Page.addScriptToEvaluateOnNewDocument',{source:"Date.now=()=>1792641720000;"});
 await navigate(p,fake.newPairing(page+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,"document.getElementById('phone-env-dismiss')?.click();document.getElementById('setBtn').click()");await sleep(200);await click(p,'#open-models');await waitFor(p,"document.body.dataset.view==='models'");
 await waitFor(p,"!!document.querySelector('[data-provider-routing] button')");
 for(const language of ['zh','en'])for(const width of [390,1280]){
  await p.send('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width<500});
  await fake.send({t:'preferences',value:{appearance:{language,theme:'dark'},voice:{speak_replies:false,wake_enabled:false}},problem:null});await sleep(200);
  await evaluate(p,"(()=>{document.querySelector('.ajpages').scrollTop=0;if(!document.getElementById('p476-freeze')){const s=document.createElement('style');s.id='p476-freeze';s.textContent='*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';document.head.append(s)}})()");
  await evaluate(p,"(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})));for(const a of document.getAnimations()){a.pause();a.currentTime=0}})()");await sleep(250);
  writeFileSync(new URL(`${language}-${width}.png`,out),await shoot(p,''));
  const route=await evaluate(p,`(()=>{const e=document.querySelector('[data-provider-routing]'),r=e.getBoundingClientRect();return {text:e.textContent,rect:{x:r.x,y:r.y,width:r.width,height:r.height}}})()`);
  writeFileSync(new URL(`${language}-${width}.json`,out),JSON.stringify(route,null,2));
  if (!process.env.P476_ROUTE_BASELINE) {
   for (const text of language==='zh'?['辅助小模型','子 Agent「研究员」','其它用途']:['Helper model','Sub-agent “研究员”','Other use']) assert.ok(route.text.includes(text),text);
   for (const field of ['small_model','agent:研究员','future_internal_role']) assert.ok(!route.text.includes(field),'internal role hidden: '+field);
  }
  assert.equal(await evaluate(p,'document.documentElement.scrollWidth<=innerWidth'),true,'no document overflow');
 }
 await click(p,'[data-provider-routing] button');
 await waitFor(p,"!document.querySelector('[data-provider-routing] button')");
 const row='li[data-name="BAILIAN_CODING_PLAN_API_KEY"] .keys-correction';
 await click(p,row+' button');await waitFor(p,`document.querySelectorAll('${row} button').length===2`);
 await sleep(150);await click(p,row+' button:last-child');await waitFor(p,'!document.querySelector(\'li[data-name="BAILIAN_CODING_PLAN_API_KEY"]\')');
 assert.ok(unified&&moved);assert.ok(calls.filter(c=>['move','unify'].includes(c.op)).every(c=>c.fa));
 passed=true;console.log('PASS P450 bilingual narrow/wide routing, real UV unify and row move over Noise');
}finally{rec.record('P450 bilingual narrow/wide routing, real UV unify and row move over Noise',passed);rec.write();await b.close();await fake.stop();await web.stop();}
