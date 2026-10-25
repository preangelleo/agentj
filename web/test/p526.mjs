// P526: actual Settings → UV → sealed mail card → result. Synthetic mailbox only.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep} from './browser.mjs';
import {elevateFields,elevateDigest,elevateMessage,b64u,unb64u,SEAL_CONTEXT} from '../../protocol/wire.js';
const py=process.env.AJ_PYTHON || execFileSync('git',['rev-parse','--path-format=absolute','--git-common-dir'],{encoding:'utf8'}).trim()+'/../host/.venv/bin/python';
function host(code,value) {return JSON.parse(execFileSync(py,['-c',`import sys,json;sys.path.insert(0,'agentjarvis/host');from agentj import mail,action_policy,passkey,wire\nv=json.load(sys.stdin)\n${code}`],{input:JSON.stringify(value),encoding:'utf8',env:{...process.env,AGENTJ_TEST_PASSKEY_ORIGIN:origin}}));}
let origin='http://localhost';
const out=new URL('../../../reports/qa/p526/',import.meta.url);mkdirSync(out,{recursive:true});
const web=await startWebServer(process.env.P526_BASELINE_DIR?{publicDir:process.env.P526_BASELINE_DIR}:{});web.url=web.url.replace('127.0.0.1','localhost');origin=new URL(web.url).origin;
const fake=await startFakeHost(),B=await launch(),checks=[];const subtle=webcrypto.subtle;
const hex=()=>webcrypto.getRandomValues(new Uint8Array(16)).toHex();
try {
 for(const lang of ['zh','en']) for(const [width,scheme] of [[390,'dark'],[1440,'light']]) {
  fake.reset();const p=await newPage(B,width,844,scheme,{touch:true,allow:[web.url,fake.relay+'/']});
  const shot=async name=>{if(lang==='zh'&&width===390)writeFileSync(new URL(name+'.png',out),await shoot(p,''));};
  let v=host("c=mail.apply_preset(mail.blank_config(),'qq','old@example.com');print(json.dumps({'config':c,'presets':mail.preset_list(v),'has_password':False,'today':{}}))",lang);
  let pending=null,card=null,registered,pk;let fail=false,failSave=false,offers=0,keys=0,sets=0,answers=0;const seq=[];
  fake.st.onApp=async(c,m,send)=>{
   if(m.t==='policy_prepare') {
    offers++;seq.push(m.t);const target={...m.target,before:structuredClone(v.config)};
    const display=host(`print(json.dumps(action_policy.summary('host_write',v['target'],lang=v['lang'],presentation=True)))`,{target,lang});
    pending={target,n:hex()};await send({t:'policy_ready',r:m.r,...pending,level:'uv',...display,fa:registered.id,fa_ch:b64u(new Uint8Array(32).fill(3))});return true;
   }
   if(['mail_get','mail_set','mail_test','mail_key'].includes(m.t)) {
    seq.push(m.t);
    if(m.t==='mail_set') {
     sets++;assert.ok(pending);assert.deepEqual(m.cfg,pending.target.cfg);
     assert.ok(await fake.verifyControl(c,'host_write',m.authorization,pending.target));
     host("rec=passkey.verify_registration(v['registered'],bytes([1])*32);passkey.verify_assertion(rec,passkey.approval_fields(v['fa']),bytes([3])*32);print('true')",{registered,fa:m.authorization.fa});
     pending=null;
     if(failSave){await send({t:'mail_res',r:m.r,ok:false,why:'stale',view:v});return true;}
     v.config=host("print(json.dumps(action_policy.mail_config(v['before'],v['cfg'])))",{before:v.config,cfg:m.cfg});
    }
    if(m.t==='mail_key') {
     keys++;const pair=await subtle.generateKey({name:'X25519'},true,['deriveBits']);pk=pair.privateKey;
     card={t:'elev',id:hex(),n:hex(),kind:'secret',epk:b64u(new Uint8Array(await subtle.exportKey('raw',pair.publicKey))),ttl:600,tries:3,
      name:'AGENTJ_MAIL_PASSWORD',purpose:host('print(json.dumps(mail.password_purpose(v["cfg"],v["lang"])))',{cfg:v.config,lang}),dest:lang==='zh'?'Agent J 邮件通道（本机加密）':'Agent J mail channel (encrypted on the host)',verify:'IMAP + SMTP login',passkey_required:true,fa:registered.id};
     card.request=m.r;await send(card);return true;
    }
    await send({t:'mail_res',r:m.r,ok:true,view:v});return true;
   }
   if(m.t==='elev_answer') {
    answers++;assert.equal(m.id,card.id);assert.equal(m.ok,true);assert.ok(m.ct);assert.ok(m.fa);
    const digest=await elevateDigest('secret',elevateFields(card)),ct=unb64u(m.ct),dev=fake.devs[0];
    const key=await subtle.importKey('raw',unb64u(dev.sk),{name:'Ed25519'},false,['verify']);
    const hash=new Uint8Array(await subtle.digest('SHA-256',ct)).toHex();
    assert.ok(await subtle.verify('Ed25519',key,unb64u(m.sig),elevateMessage(fake.channel,dev.id,card.id,'secret','allow',card.n,m.ts,digest,hash)));
    host("rec=passkey.verify_registration(v['registered'],bytes([1])*32);ch=passkey.elevate_challenge(v['channel'],v['device'],v['id'],'secret',v['n'],v['digest']);passkey.verify_assertion(rec,passkey.approval_fields(v['fa']),ch);print('true')",{registered,channel:fake.channel,device:dev.id,id:card.id,n:card.n,digest,fa:m.fa});
    const epk=unb64u(m.epk),pub=await subtle.importKey('raw',epk,'X25519',false,[]);
    const shared=await subtle.deriveBits({name:'X25519',public:pub},pk,256),ikm=await subtle.importKey('raw',shared,'HKDF',false,['deriveKey']);
    const seal=await subtle.deriveKey({name:'HKDF',hash:'SHA-256',salt:new Uint8Array([...unb64u(card.epk),...epk]),info:new TextEncoder().encode(SEAL_CONTEXT)},ikm,{name:'AES-GCM',length:256},false,['decrypt']);
    const aad=new TextEncoder().encode([SEAL_CONTEXT,fake.channel,dev.id,card.id,'secret',card.n,digest].join('\n'));
    const value=new TextDecoder().decode(await subtle.decrypt({name:'AES-GCM',iv:ct.slice(0,12),additionalData:aad},seal,ct.slice(12)));
    assert.equal(value,'abcd efgh ijkl mnop');assert.ok(!JSON.stringify(m).includes(value));
    v.has_password=true;
    const receipt={verify:fail?'fail':'ok',detail:fail?'imap_login':'IMAP+SMTP'};
    await send({t:'elev_done',id:card.id,result:'saved',verify:receipt.verify,receipt});
    await send({t:'mail_res',r:card.request,ok:true,verify:receipt.verify,detail:receipt.detail,view:v});return true;
   }
   return false;
  };
  try {
   await navigate(p,fake.newPairing(web.url+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
   await p.send('WebAuthn.enable');await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
   registered=await evaluate(p,`import('./js/faceid.js').then(f=>f.register(new Uint8Array(32).fill(1),{relayIndex:2,channel:'AAAAAAAAAAAAAAAAAAAAAA',hostPub:new Uint8Array(32)}))`);
   await evaluate(p,`import('./js/session.js').then(s=>s.peer.mailuv1=true)`);
   await evaluate(p,`import('./js/settings.js').then(s=>s.openMail())`);await waitFor(p,"document.getElementById('mail-preset').options.length>0");
   await evaluate(p,`(()=>{const a=document.getElementById('mail-addr');a.value='demo@gmail.com';a.dispatchEvent(new Event('change'));document.getElementById('mail-poll').value='10';document.getElementById('mail-key').click()})()`);
   await waitFor(p,"!document.getElementById('confirm').hidden");
   assert.equal(await evaluate(p,"document.getElementById('mail-addr').value"),'demo@gmail.com','draft survives confirmation');
   const text=await evaluate(p,"document.getElementById('confirm-text').textContent");assert.match(text,/Gmail/);assert.doesNotMatch(text,/generation|SHA-256|host_write|"cfg"/);
   assert.equal(await evaluate(p,"document.getElementById('confirm-details').open"),false);
   assert.match(await evaluate(p,"document.getElementById('confirm-raw').textContent"),/Request SHA-256/);
   await shot('01-confirm');assert.equal(keys,0);
   await evaluate(p,"document.querySelector('#confirm-details summary').click()");assert.equal(await evaluate(p,"document.getElementById('confirm-raw').getClientRects().length>0"),true);
   await evaluate(p,"document.getElementById('confirm-no').click()");await waitFor(p,"!document.getElementById('mail-key').disabled");assert.equal(sets,0);assert.equal(keys,0);
   assert.equal(await evaluate(p,"document.getElementById('mail-addr').value"),'demo@gmail.com','cancel preserves draft');
   failSave=true;await evaluate(p,"document.getElementById('mail-key').click()");await waitFor(p,"!document.getElementById('confirm').hidden");await evaluate(p,"document.getElementById('confirm-yes').click()");await waitFor(p,"!document.getElementById('mail-key').disabled");assert.equal(keys,0,'save failure stops key request');
   failSave=false;await evaluate(p,"document.getElementById('mail-key').click()");await waitFor(p,"!document.getElementById('confirm').hidden");await evaluate(p,"document.getElementById('confirm-yes').click()");
   await waitFor(p,"document.getElementById('elev-input')&&!document.getElementById('elev').hidden");
   // The settings panel must never cover the key input.
   assert.equal(await evaluate(p,`(()=>{const e=document.getElementById('elev-input'),r=e.getBoundingClientRect();return e.type==='password'&&r.width>0&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===e})()`),true);
   assert.match(await evaluate(p,"document.getElementById('elev-title').textContent"),/Gmail/);
   const official = lang === 'zh' ? '服务商官方说明 / 获取入口' : await evaluate(p,"import('./js/t.js').then(m=>m.t('mail.official'))");
   assert.equal(await evaluate(p,`[...document.querySelectorAll('#elev dt')].filter(e=>e.textContent===${JSON.stringify(official)}).length`),1,'one shared official heading');
   assert.equal(await evaluate(p,"document.querySelectorAll('#elev a[target=_blank]').length"),2,'both Gmail links retained');
   assert.equal(await evaluate(p,"document.querySelectorAll('#elev details').length"),0,'fixed Gmail has no server settings');
   await shot('02-key');
   // All presets and custom use the same signed-purpose renderer; no provider text inferred in the browser.
   const purposes=host("print(json.dumps([{ 'id':p, 'purpose':mail.password_purpose(mail.apply_preset(mail.blank_config(),p,'demo@example.com'),v)} for p in mail.PRESETS]))",lang);
   for(const item of purposes) {
    const info=await evaluate(p,`import('./js/mail-password.js').then(m=>{const rows=document.createElement('dl');m.paintMailPassword({kind:'secret',name:'AGENTJ_MAIL_PASSWORD',purpose:${JSON.stringify(item.purpose)}},document.createElement('label'),document.createElement('input'),rows);return {servers:rows.querySelectorAll('details').length,headings:[...rows.querySelectorAll('dt')].filter(e=>e.textContent===${JSON.stringify(official)}).length,links:rows.querySelectorAll('a').length}})`);
    assert.equal(info.servers,item.id==='custom'?1:0,item.id+' custom-only servers');
    assert.equal(info.headings,info.links?1:0,item.id+' shared heading');
   }
   async function enter() {await sleep(120);await evaluate(p,"document.getElementById('elev-input').value='abcd efgh ijkl mnop';document.getElementById('elev-form').requestSubmit()");await waitFor(p,"document.getElementById('elev').hidden");await waitFor(p,"!document.getElementById('mail-key').disabled");}
   await enter();assert.equal(answers,1);const ok=await evaluate(p,"document.getElementById('mail-msg').textContent");assert.match(ok,lang==='zh'?/连接正常/:/connected|connection.*OK|IMAP.*SMTP/i);await sleep(2600);await shot('03-success');
   const count=offers;fail=true;await evaluate(p,"document.getElementById('mail-key').click()");await waitFor(p,"!document.getElementById('elev').hidden");assert.equal(offers,count,'unchanged settings go directly to the key card');
   await enter();assert.equal(answers,2);const bad=await evaluate(p,"document.getElementById('mail-msg').textContent");assert.match(bad,lang==='zh'?/失败/:/failed/i);
   assert.equal(await evaluate(p,"document.getElementById('toast').dataset.kind === 'success'"),false);await shot('04-failure');
   assert.ok(seq.indexOf('mail_set')<seq.indexOf('mail_key'));assert.equal(p.problems.length,0,p.problems.join('\n'));
   checks.push(`${lang}/${width}/${scheme}: cancel, save refusal, real UV+signature, visible sealed input, success, failed login, direct replacement`);
  }finally{await p.dispose();}
 }
}finally{await B.close();await fake.stop();await web.stop();}
writeFileSync(new URL('phone.json',out),JSON.stringify({ok:true,checks,scope:'Chromium + virtual passkey; synthetic Noise host, real host summary and crypto verification; no physical Mac/Gmail claim'},null,2)+'\n');
console.log('P526 phone PASS',checks);
