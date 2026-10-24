// P503d: real browser, Noise and synthetic platform authenticator; no physical-biometric claim.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
const out=new URL('../../../reports/qa/p503/',import.meta.url);mkdirSync(out,{recursive:true});
const web=await startWebServer();web.url=web.url.replace('127.0.0.1','localhost');const fake=await startFakeHost(),B=await launch();const checks=[];
try {
 for(const lang of ['zh','en']) {
  fake.reset();const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await sleep(500);
  await p.send('WebAuthn.enable');
  await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
  const vectors=await evaluate(p,`(async()=>{const f=await import('./js/faceid.js');const opt={relayIndex:2,channel:'AAAAAAAAAAAAAAAAAAAAAA',hostPub:new Uint8Array(32)};const old=await f.register(new Uint8Array(32).fill(1),opt);const newer=await f.register(new Uint8Array(32).fill(2),{...opt,replace:old.id});return {old,newer};})()`);
  const py=process.env.AJ_PYTHON || execFileSync('git',['rev-parse','--path-format=absolute','--git-common-dir'],{encoding:'utf8'}).trim()+'/../host/.venv/bin/python';
  execFileSync(py,['-c',`import json,sys;sys.path.insert(0,'agentjarvis/host');from agentj import passkey
v=json.load(sys.stdin);old=passkey.verify_registration(v['old'],bytes([1])*32);new=passkey.verify_registration(v['newer'],bytes([2])*32)
f=passkey.approval_fields(v['newer']['replace_fa']);assert f['id']==old['id'];passkey.verify_assertion(old,f,passkey.replacement_offer_challenge(bytes([2])*32,old))`],{input:JSON.stringify(vectors),env:{...process.env,AGENTJ_TEST_PASSKEY_ORIGIN:new URL(web.url).origin}});
  checks.push(lang+': browser attestation + old-credential UV replacement verified by real host');
  await evaluate(p,`import('./js/session.js').then(s=>s.peer.mailuv1=true)`);
  const target={kind:'mail_test',before:{address:'fixture@example.com',smtp:{host:'smtp.example.com',port:465,security:'ssl'}}};
  let valid=0;
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='policy_prepare'){await send({t:'policy_ready',r:m.r,n:'a'.repeat(32),target,level:'uv',summary:'SMTP smtp.example.com:465'});return true;}
   return false;
  };
  await evaluate(p,`import('./js/controls.js').then(c=>{window.mailUV=null;c.signed('host_write',{kind:'mail_test'},{}).then(x=>window.mailUV=x,e=>window.mailUV={why:e.message});})`);
  await waitFor(p,'window.mailUV !== null');assert.equal((await evaluate(p,'window.mailUV')).why,'no_passkey');
  checks.push(lang+': mail test cannot sign without registered remote passkey');
  await sleep(6300); // let the missing-passkey toast dismiss before the next native gesture
  // A server-issued exact target is signed only after confirmation + real virtual UV.
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='policy_prepare'){await send({t:'policy_ready',r:m.r,n:'b'.repeat(32),target,level:'uv',summary:'SMTP smtp.example.com:465',fa:vectors.newer.id,fa_ch:'AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM'});return true;}
   if(m.t==='p503_verify'){assert.equal(await fake.verifyControl(c,'host_write',m,target),true);valid++;await send({t:'p503_ok',r:m.r});return true;}
   return false;
  };
  await evaluate(p,`import('./js/controls.js').then(c=>{window.mailUV=null;c.signed('host_write',{kind:'mail_test'},{}).then(x=>window.mailUV=x,e=>window.mailUV={why:e.message});})`);
  await waitFor(p,"!document.getElementById('confirm').hidden");
  writeFileSync(new URL(`mail-uv-${lang}.png`,out),await shoot(p,''));
  await evaluate(p,"document.getElementById('confirm-yes').click()");await waitFor(p,'window.mailUV !== null');
  const auth=await evaluate(p,'window.mailUV');assert.ok(auth.fa,JSON.stringify(auth));
  execFileSync(py,['-c',`import json,sys;sys.path.insert(0,'agentjarvis/host');from agentj import passkey
v=json.load(sys.stdin);old=passkey.verify_registration(v['old'],bytes([2])*32);passkey.verify_assertion(old,passkey.approval_fields(v['fa']),bytes([3])*32)`],{input:JSON.stringify({old:vectors.newer,fa:auth.fa}),env:{...process.env,AGENTJ_TEST_PASSKEY_ORIGIN:new URL(web.url).origin}});
  await evaluate(p,"import('./js/session.js').then(s=>s.ask1({...window.mailUV,t:'p503_verify'},'p503_ok',2000))");assert.equal(valid,1);
  checks.push(lang+': mail exact target signature + UV verified');
  // Exercise mail.js's actual confirmation failure UI in each locale, not just signed() error codes.
  const tt=await evaluate(p,`import('./js/t.js').then(({t})=>({declined:t('mail.saveFail',{why:t('mail.why.denied')}),missing:t('mail.saveFail',{why:t('ask.passkeyRequired')}),failed:t('mail.saveFail',{why:t('elev.fa.cancel')}),offline:t('mail.saveFail',{why:t('set.offline')})}))`);
  for(const failure of ['cancel','no_passkey','NotAllowedError','offline']) {
   fake.st.onApp=async(c,m,send)=>{
    if(m.t==='policy_prepare') {
     if(failure==='offline') {await send({t:'offline',r:m.r});return true;}
     await send({t:'policy_ready',r:m.r,n:'c'.repeat(32),target,level:'uv',summary:'SMTP smtp.example.com:465',
       ...(failure==='no_passkey'?{}:{fa:vectors.newer.id,fa_ch:'AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM'})});return true;
    }
    return false;
   };
   if(failure==='NotAllowedError')await evaluate(p,"window.originalGet=navigator.credentials.get.bind(navigator.credentials);navigator.credentials.get=async()=>{throw new DOMException('internal_detail','NotAllowedError')}");
   await evaluate(p,"document.getElementById('mail-msg').textContent='';document.getElementById('mail-test').click()");
   if(['cancel','NotAllowedError'].includes(failure)) {
    await waitFor(p,"!document.getElementById('confirm').hidden");
    await evaluate(p,`document.getElementById('${failure==='cancel'?'confirm-no':'confirm-yes'}').click()`);
   }
   await waitFor(p,"document.getElementById('mail-msg').textContent.length > 0");
   const message=await evaluate(p,"document.getElementById('mail-msg').textContent");
   assert.equal(message,tt[({cancel:'declined',no_passkey:'missing',NotAllowedError:'failed',offline:'offline'})[failure]]);
   assert.doesNotMatch(message,/cancel|no_passkey|NotAllowedError|internal_detail/);
   if(failure==='NotAllowedError')await evaluate(p,"navigator.credentials.get=window.originalGet");
  }
  checks.push(lang+': mail cancellation, missing passkey, WebAuthn failure and offline are localized');

  let answers=0;fake.st.onApp=async(c,m)=>{if(m.t==='elev_answer'){answers++;return true;}return false;};
  for(const kind of ['sudo','secret']) {
   const card={id:'d'.repeat(32),n:'e'.repeat(32),kind,epk:'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',ttl:120,passkey_required:true,
    ...(kind==='sudo'?{cmd:'printf fixture',why:'fixture',effect:'fixture'}:{name:'FIXTURE',purpose:'fixture',dest:'env:fixture#FIXTURE'})};
   await evaluate(p,`import('./js/elevate.js').then(e=>e.add(${JSON.stringify(card)}))`);await sleep(100);
   await evaluate(p,"document.getElementById('elev-input').value='synthetic-password';document.getElementById('elev-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))");await sleep(100);assert.equal(answers,0);
   writeFileSync(new URL(`blocked-${kind}-${lang}.png`,out),await shoot(p,''));await evaluate(p,"import('./js/elevate.js').then(e=>e.clear())");
  }
  checks.push(lang+': no-passkey administrator + secret submit blocked');await p.dispose();
 }
} finally {await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('phone-d.json',out),JSON.stringify({ok:true,checks,scope:'Chromium + virtual authenticator; no physical phone claim'},null,2)+'\n');console.log('P503d phone PASS',checks);

if(process.env.AJ_PARITY_OUT)writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p503.mjs',results:{'p503d-mail-uv':'pass'}}));
