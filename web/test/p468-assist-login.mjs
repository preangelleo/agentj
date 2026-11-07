// Real customer page + Noise + virtual UV. A real X25519/AES-GCM fixture host opens the dedicated login response.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {randomBytes,generateKeyPairSync,webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitFor,waitState,evaluate,shoot,sleep,rect} from './browser.mjs';
import {b64u,unb64u,elevateFields,elevateDigest,elevateMessage,SEAL_CONTEXT} from '../../protocol/wire.js';
const subtle=webcrypto.subtle,enc=new TextEncoder(),out=resolve(process.env.P468_LOGIN_OUT||'reports/qa/remote-assist-468-20261106/web-login');mkdirSync(out,{recursive:true});
const PASSWORD='fixture-password-only-468',gid='1234567890abcdef1234567890abcdef';
async function keypair(){const k=await subtle.generateKey({name:'X25519'},true,['deriveBits']);return {priv:k.privateKey,pub:new Uint8Array(await subtle.exportKey('raw',k.publicKey))};}
async function unseal(priv,hostPub,epk,ct,parts){const p=unb64u(epk),b=unb64u(ct),pk=await subtle.importKey('raw',p,{name:'X25519'},false,[]);const bits=await subtle.deriveBits({name:'X25519',public:pk},priv,256),hk=await subtle.importKey('raw',bits,'HKDF',false,['deriveKey']);const k=await subtle.deriveKey({name:'HKDF',hash:'SHA-256',salt:new Uint8Array([...hostPub,...p]),info:enc.encode(SEAL_CONTEXT)},hk,{name:'AES-GCM',length:256},false,['decrypt']);return new Uint8Array(await subtle.decrypt({name:'AES-GCM',iv:b.slice(0,12),additionalData:enc.encode([SEAL_CONTEXT,...parts].join('\n'))},k,b.slice(12)));}
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'}),fake=await startFakeHost(),B=await launch(),page=web.url.replace('127.0.0.1','localhost');let checks=0;const check=(ok,msg)=>{assert.ok(ok,msg);checks++;};
try{
for(const [lang,theme,width] of [['zh','light',390],['zh','dark',390],['en','light',1440],['en','dark',1440]]){
 fake.reset();fake.setUp(true);fake.st.extraCaps=['assist_term1'];fake.st.prefs={appearance:{language:lang,theme}};
 const h={fa:'',answers:[],login:null,retries:0};
 const until=Math.floor(Date.now()/1000)+1800;
 const state=discovery=>fake.send({t:'assist_state',scope:'terminal',active:true,id:gid,key_id:'official-test',until,discovery});
 fake.st.onApp=async(c,m,send)=>{
  if(m.t==='assist_discover_retry'){h.retries++;await send({t:'ctl_res',r:m.r,ok:true});await state('publishing');return true;}
  if(m.t==='elev_answer'&&h.login&&m.id===h.login.m.id){
   const L=h.login,d=fake.devs[0],digest=await elevateDigest('login',elevateFields(L.m)),ctSha=m.ok?Buffer.from(await subtle.digest('SHA-256',unb64u(m.ct))).toString('hex'):null;
   const pk=await subtle.importKey('raw',unb64u(d.sk),{name:'Ed25519'},false,['verify']);
   check(await subtle.verify({name:'Ed25519'},pk,unb64u(m.sig),elevateMessage(fake.channel,d.id,m.id,'login',m.ok?'allow':'deny',m.n,m.ts,digest,ctSha)),'login device signature verified');
   check(!('password'in m)&&!('value'in m)&&!JSON.stringify(m).includes(PASSWORD),'no plaintext password field');
   const faCh=b64u(new Uint8Array(await subtle.digest('SHA-256',enc.encode(['agentjarvis/passkey/elevate/v1',fake.channel,d.id,m.id,'login','approve',m.n,digest].join('\n')))));
   check(m.fa&&JSON.parse(Buffer.from(m.fa.cd,'base64url').toString()).challenge===faCh,'login UV binds original displayed login target');
   const pw=await unseal(L.k.priv,L.k.pub,m.epk,m.ct,[fake.channel,d.id,m.id,'login',m.n,digest]);
   const ok=new TextDecoder().decode(pw)===PASSWORD;pw.fill(0);h.answers.push({ok,sealed:true});
   const wrong=await keypair();let rejected=false;try{await unseal(wrong.priv,wrong.pub,m.epk,m.ct,[fake.channel,d.id,m.id,'login',m.n,digest]);}catch{rejected=true;}check(rejected,'unrelated host key cannot open password');
   await send({t:ok?'elev_done':'elev_refused',id:m.id,result:ok?'done':'denied'});return true;
  }
  return false;
 };
 const p=await newPage(B,width,900,theme,{touch:width<500,allow:[web.url,page,fake.relay+'/']});
 const click=async id=>{await evaluate(p,`document.getElementById('${id}').scrollIntoView({block:'center'})`);await sleep(30);const r=await rect(p,'#'+id);await p.send('Input.dispatchMouseEvent',{type:'mousePressed',x:r.x,y:r.y,button:'left',clickCount:1});await p.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:r.x,y:r.y,button:'left',clickCount:1});};
 const save=async tag=>writeFileSync(resolve(out,`${tag}-${lang}-${theme}-${width}.png`),await shoot(p,''));
 const dbg=()=>evaluate(p,`import('./js/assist-terminal.js').then(m=>m._debug())`);
 const card=async(extra={})=>{const k=await keypair(),m={t:'elev',kind:'login',assist_gid:gid,term:gid.slice(0,16),id:randomBytes(16).toString('hex'),n:randomBytes(16).toString('hex'),epk:b64u(k.pub),fa:h.fa,ttl:120,tries:3,...extra};h.login={m,k};await fake.send(m);return m;};
 try{
  await p.send('WebAuthn.enable',{enableUI:false});const {authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',ctap2Version:'ctap2_1',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true,hasPrf:false}});
  const id=randomBytes(16);h.fa=b64u(id);const priv=generateKeyPairSync('ec',{namedCurve:'P-256'}).privateKey.export({type:'pkcs8',format:'der'}).toString('base64');await p.send('WebAuthn.addCredential',{authenticatorId,credential:{credentialId:id.toString('base64'),isResidentCredential:true,rpId:'localhost',privateKey:priv,userHandle:Buffer.from('p468-login-fixture').toString('base64'),signCount:0}});
  await navigate(p,fake.newPairing(page+'?lang='+lang));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,`import('./js/settings.js').then(m=>{m.applyThemeLocal('${theme}');m.openRescue();return true})`);
  await waitFor(p,`!!document.getElementById('ast-card')`);await save('duration');
  check(await evaluate(p,`[...document.querySelectorAll('.ast__durations button')].every(b=>getComputedStyle(b).whiteSpace==='nowrap')`),'duration chips never wrap');
  await card();await sleep(100);check(!(await dbg()).login,'login before grant refused');
  await state('failed');await sleep(100);await click('ast-open');await save('discovery-failed');
  check(await evaluate(p,`!document.getElementById('ast-discovery-retry').hidden`),'failed discovery has retry');
  await click('ast-discovery-retry');await sleep(100);check(h.retries===1,'bounded retry submitted');check(await evaluate(p,`document.getElementById('ast-discovery-retry').hidden||document.getElementById('ast-discovery-retry').disabled`),'retry cannot spam');
  await state('ready');await sleep(50);check(!(await dbg()).state.connected,'directory ready is not a connected official terminal');
  await card({assist_gid:'other'});await sleep(100);check(!(await dbg()).login,'wrong grant marker refused');
  await card({term:'other'});await sleep(100);check(!(await dbg()).login,'wrong login target refused');
  await card();await waitFor(p,`!document.getElementById('ast-login').hidden&&!document.getElementById('ast-login-yes').disabled`);await save('login-card');await evaluate(p,`document.getElementById('ast-login-password').value='pending-close-fixture';true`);await click('ast-close');check(await evaluate(p,`document.getElementById('ast-login-password').value===''`),'closing observer clears password field');await click('ast-open');
  await evaluate(p,`document.getElementById('ast-login-password').value='wrong-fixture-password';true`);await click('ast-login-yes');await waitFor(p,`document.getElementById('ast-login-yes')&&!document.getElementById('ast-login-yes').disabled`);
  check(h.answers.length===1&&!h.answers[0].ok,'wrong-password sealed response stays denied');check(await evaluate(p,`document.getElementById('ast-login-password').value===''`),'password field cleared after denied answer');
  await card({bad:1,tries:2});await waitFor(p,`!document.getElementById('ast-login-yes').disabled`);await save('login-retry');
  await evaluate(p,`document.getElementById('ast-login-password').value=${JSON.stringify(PASSWORD)};true`);await click('ast-login-yes');await waitFor(p,`document.getElementById('ast-login').hidden`);check(h.answers.length===2&&h.answers[1].ok,'real seal opened by fixture host after customer UV');
  await card();await waitFor(p,`!document.getElementById('ast-login').hidden`);await evaluate(p,`document.getElementById('ast-login-password').value='pending-fixture';true`);await fake.send({t:'assist_state',active:false});await sleep(100);
  check(!(await dbg()).login&&await evaluate(p,`document.getElementById('ast-login-password').value===''`),'revocation clears pending login and password');
  check(!p.offsite.length,'no off-site requests');check(!p.problems.filter(x=>!/WebSocket|ERR_CONNECTION|net::/i.test(x)).length,'no console problems');console.log('PASS',lang,theme,width);
 }finally{await p.dispose();}
}
writeFileSync(resolve(out,'result.json'),JSON.stringify({checks,kind:'real-browser-local-noise-virtual-uv-real-x25519-seal',physical:false},null,2)+'\n');console.log('checks',checks);
}finally{await B.close();await fake.stop();await web.stop();}
