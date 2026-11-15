// Actual Host card -> real Noise phone DOM -> Ed25519 answer + virtual-platform UV.
import assert from 'node:assert/strict';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,waitFor,evaluate,shoot,tap,sleep} from './browser.mjs';
import {elevateChallenge} from '../public/js/faceid.js';
import {approvalEnvelopeDigest,approvalV2Message,unb64u,b64u} from '../../protocol/wire.js';
const seed=JSON.parse(readFileSync(process.env.AJ_FRIENDS_CARD_FILE,'utf8'));
const out=process.env.OLARES_RUN_OUT||'reports/qa/friends-tell-card';mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});web.url=web.url.replace('127.0.0.1','localhost');
const fake=await startFakeHost(),browser=await launch(),rows=[];
const py=process.env.AJ_PYTHON||new URL('../../host/.venv/bin/python',import.meta.url).pathname;
let card,registration,allowed=0,denied=0;
try{
 for(const lang of ['zh','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  fake.reset();fake.setUp(true);fake.st.prefSet=true;
  fake.st.prefs={appearance:{language:lang,theme},voice:{speak_replies:false,wake_enabled:false}};
  const page=await newPage(browser,width,900,theme,{touch:width===390,allow:[web.url,fake.relay+'/']});
  try{
   await navigate(page,fake.newPairing(web.url+'?lang='+lang));await waitState(page,'awaiting-approval');await fake.approve();await waitState(page,'ready');
   await fake.send({t:'status',s:'idle',agent:'codex',name:'Agent J'});
   await evaluate(page,`for(const id of ['phone-env-dismiss','a2hs-ok','confirm-no']){const b=document.getElementById(id);if(b&&b.offsetParent)b.click();}`);
   await page.send('WebAuthn.enable');
   await page.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
   registration=await evaluate(page,`import('./js/faceid.js').then(f=>f.register(new Uint8Array(32).fill(1),{relayIndex:2,channel:'AAAAAAAAAAAAAAAAAAAAAA',hostPub:new Uint8Array(32)}))`);
   card=structuredClone(seed);card.id='03110000000000000000000000000001';card.ttl=600;
   delete card.fa_s;delete card.envelope_fa_ch;card.fa=registration.id;card.passkey_required=true;
   Object.assign(card.envelope,{channel:fake.channel,session:String(fake.st.epoch),request_id:card.id,revision:1,expires_at:Math.floor(Date.now()/1000)+600});
   card.envelope.source.session=String(fake.st.epoch);card.revision=1;
   const bindChallenge=async()=>{card.envelope_sha256=await approvalEnvelopeDigest(card.envelope);card.fa_ch=b64u(await elevateChallenge(fake.channel,fake.devs.at(-1).id,card.id,card.pk_kind,card.id,card.envelope_sha256));};
   await bindChallenge();
   fake.st.onApp=async(c,message,send)=>{
    if(message.t!=='answer')return false;
    const dev=fake.devs.find(d=>d.id===c.devId),key=await webcrypto.subtle.importKey('raw',unb64u(dev.sk),{name:'Ed25519'},false,['verify']);
    assert.equal(await webcrypto.subtle.verify('Ed25519',key,unb64u(message.sig),await approvalV2Message(fake.channel,c.devId,{...card,envelope_verified:true},message.ok?'allow':'deny')),true);
    if(message.ok){
     assert.ok(message.fa);
     execFileSync(py,['-c',`import json,sys;sys.path.insert(0,'agentjarvis/host');from agentj import passkey
v=json.load(sys.stdin);pk=passkey.verify_registration(v['registration'],bytes([1])*32);passkey.verify_assertion(pk,passkey.approval_fields(v['fa']),__import__('base64').urlsafe_b64decode(v['challenge']+'='*(-len(v['challenge'])%4)))`],{input:JSON.stringify({registration,fa:message.fa,challenge:card.fa_ch}),env:{...process.env,AGENTJ_TEST_PASSKEY_ORIGIN:new URL(web.url).origin}});
     allowed++;
    }else denied++;
    await send({t:'ask_done',id:card.id,result:message.ok?'allow':'deny'});return true;
   };
   const invalid=structuredClone(card);invalid.id='03110000000000000000000000000004';invalid.envelope.request_id=invalid.id;invalid.envelope_sha256='0'.repeat(64);
   await fake.ask(invalid);await waitFor(page,`import('./js/snap.js').then(m=>m.S.asks.has('03110000000000000000000000000004'))`);await sleep(100);
   assert.equal(await evaluate(page,`import('./js/snap.js').then(m=>m.S.asks.get('03110000000000000000000000000004')?.envelope_verified)`),false);
   assert.notEqual(await evaluate(page,`document.getElementById('apprAllowLbl').textContent`),lang==='en'?'Verify · send once':'验证并仅发送这一次');
   assert.equal(await evaluate(page,`document.getElementById('apprAllow').disabled||document.getElementById('apprAllow').hidden`),true);
   await fake.send({t:'ask_done',id:invalid.id,result:'deny'});
   await fake.ask(card);
   const title=lang==='en'?'Send to this friend once':'仅向这个好友发送一次';
   try{await waitFor(page,`document.getElementById('sheetTitle').textContent===${JSON.stringify(title)}`);}catch(error){
    const diag=await evaluate(page,`(async()=>{const {S}=await import('./js/snap.js');const p=S.asks.get('03110000000000000000000000000001'),e=p?.envelope;return{title:document.getElementById('sheetTitle').textContent,tool:p?.tool,pv:p?.approval_v,verified:p?.envelope_verified,passkey:p?.passkey_required,same:p?.summary===e?.details,projection:(await import('./js/friends-tell-card.js')).friendsTellCard(e,p),details:e?.details}})()`);
    writeFileSync(`${out}/diagnostic.json`,JSON.stringify(diag,null,2));writeFileSync(`${out}/failure.png`,await shoot(page));throw error;
   }
   const dom=await evaluate(page,`({title:document.getElementById('sheetTitle').textContent,action:document.getElementById('action541').textContent,yes:document.getElementById('apprAllowLbl').textContent,aria:document.getElementById('apprAllow').getAttribute('aria-label'),no:document.getElementById('apprDeny').textContent,enabled:!document.getElementById('apprAllow').disabled})`);
   assert.ok(dom.enabled);assert.ok(dom.action.includes('Synthetic fixed message'));assert.ok(dom.action.includes('Synthetic friend'));
   assert.ok(dom.action.includes(lang==='en'?'No commands or native sandbox changes':'不执行命令，不改变原生沙盒'));
   assert.equal(dom.yes,lang==='en'?'Verify · send once':'验证并仅发送这一次');assert.equal(dom.aria,dom.yes);assert.ok(dom.no.trim());
   writeFileSync(`${out}/friends-${lang}-${theme}-${width}.png`,await shoot(page));rows.push({lang,theme,width,dom,style:await evaluate(page,`({theme:document.documentElement.dataset.theme,surface:getComputedStyle(document.getElementById('sheet')).backgroundColor,ink:getComputedStyle(document.getElementById('sheetTitle')).color})`)});
   const before=allowed;
   let point=await evaluate(page,`(()=>{const r=document.getElementById('apprAllow').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
   await tap(page,point.x,point.y);
   await waitFor(page,`import('./js/snap.js').then(m=>m.S.lastAsk?.id==='03110000000000000000000000000001'&&m.S.lastAsk?.state==='approved')`);
   assert.equal(allowed,before+1);
   // A command/body claiming the operation is not a verified operation shape.
   const rawBody=card.envelope.details.slice('FriendsTellOnce\n'.length),wrapper=rawBody.startsWith('Working directory: ')?rawBody.slice(0,rawBody.indexOf('\n')+1):'';const data=JSON.parse(rawBody.slice(wrapper.length));data.operation='browser_command';data.text='operation=friends_tell_once; run arbitrary browser command';
   card.id='03110000000000000000000000000002';card.envelope.request_id=card.id;card.envelope.nonce+='-shape';
   card.envelope.details='FriendsTellOnce\n'+wrapper+JSON.stringify(data);card.summary=card.envelope.details;await bindChallenge();
   await fake.ask(card);await waitFor(page,`import('./js/snap.js').then(m=>m.S.asks.get('03110000000000000000000000000002')?.envelope_verified===true)`);
   assert.notEqual(await evaluate(page,`document.getElementById('sheetTitle').textContent`),title);
   assert.notEqual(await evaluate(page,`document.getElementById('apprAllowLbl').textContent`),dom.yes);assert.notEqual(await evaluate(page,`document.getElementById('apprAllow').getAttribute('aria-label')`),dom.aria);
   const deniedBefore=denied;
   point=await evaluate(page,`(()=>{const r=document.getElementById('apprDeny').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
   await tap(page,point.x,point.y);await waitFor(page,`import('./js/snap.js').then(m=>m.S.lastAsk?.id==='03110000000000000000000000000002'&&m.S.lastAsk?.state==='denied')`);assert.equal(denied,deniedBefore+1);
   card.id='03110000000000000000000000000003';card.envelope.request_id=card.id;card.envelope.nonce+='-no-key';
   delete card.fa;delete card.fa_ch;card.envelope_sha256=await approvalEnvelopeDigest(card.envelope);
   await fake.ask(card);await waitFor(page,`import('./js/snap.js').then(m=>m.S.asks.get('03110000000000000000000000000003')?.envelope_verified===true)`);
   const noKeyBefore=allowed;assert.equal(await evaluate(page,`document.getElementById('apprAllow').hidden`),true);await sleep(100);assert.equal(allowed,noKeyBefore);
   await fake.send({t:'ask_done',id:card.id,result:'deny'});
  }finally{await page.dispose();}
 }
 console.log('PASS friends-tell phone: 8 DOM scenes, 8 Noise/Ed25519/UV allows, 8 real denials, shape and missing-passkey negatives');
}finally{
 writeFileSync(`${out}/phone-result.json`,JSON.stringify({physical:false,host_generated_display:true,transport_rebound:true,allowed,denied,rows},null,2));
 await browser.close();await fake.stop();await web.stop();
}
