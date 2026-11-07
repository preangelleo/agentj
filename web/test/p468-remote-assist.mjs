// Real page, local Noise host and virtual platform UV. This does not stand for physical Face ID or a real official peer.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { randomBytes, generateKeyPairSync } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep, rect, key } from './browser.mjs';
import { b64u } from '../../protocol/wire.js';
const out=resolve(process.env.P468_WEB_OUT || 'reports/qa/remote-assist-468-20261106/web'); mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch();
const page=web.url.replace('127.0.0.1','localhost'),gid='assist-grant-468',tid='abcdef0123456789';
let checks=0; const check=(v,k)=>{assert.ok(v,k);checks++;};
try {
for(const [lang,theme,w] of [['zh','light',390],['zh','dark',390],['en','light',1440],['en','dark',1440]]) {
 fake.reset();fake.setUp(true);fake.st.prefs={appearance:{language:lang,theme}};fake.st.extraCaps=['assist_term1'];
 const h={fa:'',n:'',ch:'',grants:[],answers:[],inputs:[],revokes:0};
 const until=Math.floor(Date.now()/1000)+1800;
 const state=()=>fake.send({t:'assist_state',scope:'terminal',active:true,id:gid,key_id:'official-test',until});
 fake.st.onApp=async(c,m,send)=>{
  if(m.t==='assist_term_grant'&&!m.sig){h.n=randomBytes(16).toString('hex');h.ch=b64u(randomBytes(32));await send({t:'ctl_res',r:m.r,ok:false,why:'passkey_required',n:h.n,fa:h.fa,fa_ch:h.ch,key_id:'official-test',seat:'seat-test'});return true;}
  if(m.t==='assist_term_grant'){
   const ok=await fake.verifyControl(c,'assist_term_grant',m,{minutes:m.minutes,key_id:'official-test',seat:'seat-test',scope:'terminal'});
   const cd=JSON.parse(Buffer.from(m.fa.cd,'base64url').toString());check(ok&&m.scope==='terminal'&&m.minutes===30&&cd.challenge===h.ch&&m.fa.id===h.fa,'scope,default30,signature,UV verified');h.grants.push(m);
   await send({t:'ctl_res',r:m.r,ok:true});await state();return true;
  }
  if(m.t==='assist_term_answer'){if(m.obj.t==='term_line') check(await fake.verifyControl(c,'term_line',m.obj,{id:m.obj.id,line:m.obj.line}),'customer line signature verified');h.answers.push(m);await send({t:'ctl_res',r:m.r,ok:true});return true;}
  if(m.t==='assist_revoke'){check(await fake.verifyControl(c,'assist_revoke',m,{}),'revoke signed');h.revokes++;await send({t:'ctl_res',r:m.r,ok:true});await fake.send({t:'assist_state',active:false});return true;}
  if(m.t==='assist_log'){await send({t:'assist_log_res',r:m.r,gid,more:false,next_cursor:null,entries:[{ts:Date.now(),text:'Service restart approved',result:'done'}],report:{summary:lang==='zh'?'服务已恢复。':'Service restored.'}});return true;}
  if(/^term_/.test(m.t)||m.t==='assist_term_in')h.inputs.push(m);
  return false;
 };
 const p=await newPage(B,w,900,theme,{touch:w<500,allow:[web.url,page,fake.relay+'/']});
 const click=async id=>{await evaluate(p,`document.getElementById('${id}').scrollIntoView({block:'center'})`);await sleep(40);const r=await rect(p,'#'+id);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});};
 const dbg=()=>evaluate(p,`import('./js/assist-terminal.js').then(m=>m._debug())`);
 const save=async name=>writeFileSync(resolve(out,`${name}-${lang}-${theme}-${w}.png`),await shoot(p,''));
 try {
 await p.send('WebAuthn.enable',{enableUI:false});const {authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',ctap2Version:'ctap2_1',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true,hasPrf:false}});
 const cred=randomBytes(16);h.fa=b64u(cred);const priv=generateKeyPairSync('ec',{namedCurve:'P-256'}).privateKey.export({type:'pkcs8',format:'der'}).toString('base64');
 await p.send('WebAuthn.addCredential',{authenticatorId,credential:{credentialId:cred.toString('base64'),isResidentCredential:true,rpId:'localhost',privateKey:priv,userHandle:Buffer.from('remote-assist-468').toString('base64'),signCount:0}});
 await navigate(p,fake.newPairing(page+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.openRescue();return true})`);
 await waitFor(p,`!!document.getElementById('ast-grant')`);
 check((await dbg()).minutes===30,'default30');
 await save('consent-before');await click('ast-grant');await waitFor(p,`document.getElementById('ast-grant')?.textContent.includes('${lang==='zh'?'授权':'Authorize'}')`);await save('consent');
 await evaluate(p,`window.__p468get=navigator.credentials.get;navigator.credentials.get=()=>Promise.reject(new DOMException('Cancelled','NotAllowedError'));true`);
 await click('ast-grant');await sleep(150);check(h.grants.length===0,'cancelled UV produces no grant');
 await evaluate(p,`navigator.credentials.get=window.__p468get;true`);await click('ast-grant');await sleep(80);await click('ast-grant');
 await waitFor(p,`import('./js/assist-terminal.js').then(m=>m._debug().state.active)`);check(h.grants.length===1,'one grant');
 await fake.send({t:'assist_term_event',gid,event:{t:'term_ready',id:tid,expires_at:until,fa:h.fa,mode:'pty',cols:80,rows:24}});
 await click('ast-open');await fake.send({t:'assist_term_event',gid,event:{t:'term_out',id:tid,seq:1,d:'\u001b[32mAgent J service restored\u001b[0m\r\n$ '}});
 await waitFor(p,`document.querySelector('#ast-screen .xterm')!==null`);await sleep(200);await save('observer');
 check((await dbg()).output>0,'observer output');
 await fake.send({t:'assist_term_event',gid,event:{t:'term_resize',id:tid,cols:100,rows:30}});await sleep(50);
 await fake.send({t:'assist_term_event',gid,event:{t:'term_ready',id:tid,expires_at:until,fa:h.fa,cols:100,rows:30}});await sleep(50);
 check((await dbg()).seq===1,'duplicate ready preserves output replay boundary');const previous=(await dbg()).output;
 await fake.send({t:'assist_term_event',gid:'wrong',event:{t:'term_out',id:tid,seq:2,d:'CROSS GRANT'}});await sleep(80);check((await dbg()).output===previous,'cross grant refused');
 await key(p,'x',{text:'x'});await key(p,'Enter');await sleep(80);check(h.inputs.length===0,'observer no input');
 await fake.send({t:'assist_term_event',gid,event:{t:'term_confirm',id:tid,seq:1,line:'rm -rf ./temporary-test-folder',n:'a'.repeat(32),uv:0}});
 await waitFor(p,`!document.getElementById('ast-confirm').hidden`);await save('approval');await click('ast-no');await sleep(80);check(h.answers.at(-1)?.obj.t==='term_confirm_no'&&h.answers.at(-1).obj.n==='a'.repeat(32),'customer deny carries host nonce');
 await fake.send({t:'assist_term_event',gid,event:{t:'term_confirm',id:tid,seq:2,line:'touch ./approved-fixture',n:'b'.repeat(32),uv:0}});await click('ast-yes');await sleep(150);check(h.answers.at(-1)?.obj.t==='term_line','customer signed line');
 const uvch=b64u(randomBytes(32));
 await fake.send({t:'assist_term_event',gid,event:{t:'term_confirm',id:tid,seq:3,line:'curl https://example.invalid/script',n:'c'.repeat(32),uv:1,fa:h.fa,fa_ch:uvch}});
 await waitFor(p,`!document.getElementById('ast-confirm').hidden`);await click('ast-yes');await sleep(200);
 const uv=h.answers.at(-1)?.obj;check(uv?.seq===3&&JSON.parse(Buffer.from(uv.fa.cd,'base64url').toString()).challenge===uvch,'dangerous confirmation uses customer UV');
 await click('ast-log');await waitFor(p,`document.getElementById('ast-report').textContent.includes('${lang==='zh'?'服务已恢复':'Service restored'}')`);await save('log-report');
 await click('ast-stop');await sleep(150);check(!(await dbg()).state.active&&h.revokes===1,'customer revoke closes');
 await fake.send({t:'assist_term_event',gid,event:{t:'term_out',id:tid,d:'LATE'}});await sleep(50);check((await dbg()).output===0,'late frames refused');
 await fake.send({t:'assist_state',scope:'terminal',active:true,id:gid,key_id:'official-test',until:1});await sleep(50);check(!(await dbg()).state.active,'expired grant inactive');
 check(p.offsite.length===0,'no off-site requests');const problems=p.problems.filter(x=>!/WebSocket|ERR_CONNECTION|net::/i.test(x));check(!problems.length,'no console problems '+problems.join(' | '));
 console.log('PASS',lang,theme,w);
 }finally{await p.dispose();}
}
fake.reset();fake.setUp(true);fake.st.extraCaps=[];fake.st.onApp=null;
const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,page,fake.relay+'/']});
try {await navigate(p,fake.newPairing(page+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`import('./js/settings.js').then(m=>{m.openRescue();return true})`);await sleep(150);check(await evaluate(p,`!document.getElementById('ast-card')`),'no cap means no terminal assistance entry');}finally{await p.dispose();}
writeFileSync(resolve(out,'result.json'),JSON.stringify({checks,kind:'real-browser-local-noise-virtual-webauthn',physical:false},null,2)+'\n');console.log('checks',checks);
}finally{await B.close();await fake.stop();await web.stop();}
