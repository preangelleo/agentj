#!/usr/bin/env node
// P574: real client + Noise fixture; virtual platform authenticator replaces physical biometric hardware only.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { canonicalJson } from '../public/proto/wire.js';
import { fileURLToPath } from 'node:url';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, navigate, waitFor, waitState, evaluate, shoot, sleep } from './browser.mjs';
const OUT = process.env.P574_OUT || fileURLToPath(new URL('../../../reports/qa/p574/web/', import.meta.url));
mkdirSync(OUT, { recursive: true });
const web = await startWebServer(); web.url = web.url.replace('127.0.0.1', 'localhost');
const fake = await startFakeHost(), fakePeer = await startFakeHost(), B = await launch();
const { ownerConfirmChallenge, verifyBindingAssertion, introductionChallenge } = await import('../public/js/ownergrant.js');
const NOTICES=JSON.parse(readFileSync(new URL('./fixtures/p574-notices.json',import.meta.url),'utf8'));
const GRANTS=JSON.parse(readFileSync(new URL('./fixtures/p574-grants.json',import.meta.url),'utf8'));
const regs = [], checks = [], stops = [], ceremonies = [], binds = [];
let legacyOfferEntry=null, introRequest=null;
const registeredEntries=new Map(), introAsks=new Map(), introAnswers=[];
const check = (v, text) => { assert.ok(v, text); checks.push(text); };
const b64u = (v) => Buffer.from(v).toString('base64url');
const handler = f => async (c, m, reply) => {
  if(m.t==='pk_peer_req'&&m.roster===true){introRequest={id:b64u(randomBytes(16)),code:'482913'};await reply({t:'pk_peer_res',r:m.r,ok:true,mode:'peer',...introRequest,exp:Math.floor(Date.now()/1000)+300,online:1});return true;}
  if (m.t === 'pk_peer_req') { await reply({ t: 'pk_peer_res', r: m.r, ok: true, mode: 'direct' }); await reply({ t: 'pk_offer', n: b64u(randomBytes(32)) }); return true; }
  if (m.t === 'fr_set' && m.op === 'owner-stop') { const sigOk = await f.verifyControl(c, 'fr_set', m, {friend:m.friend,op:m.op,value:m.value}); stops.push({...m,sigOk}); await reply({t:'ctl_res',r:m.r,action:'fr_set',ok:sigOk}); return true; }
  if(m.t==='fr_set'&&m.op==='owner-confirm-v3'){
    const target={friend:m.friend,op:m.op,value:m.value};
    const sigOk=await f.verifyControl(c,'fr_set',m,target);
    const expected=await ownerConfirmChallenge(target,m.n,f.channel,c.devId);
    const uvOk=!!m.fa&&await verifyBindingAssertion(registeredEntries.get(c.devId),m.fa,expected,{rp:'localhost',origin:new URL(web.url).origin});
    ceremonies.push({...m,sigOk,uvOk,host:f.channel});await reply({t:'ctl_res',r:m.r,action:'fr_set',ok:sigOk&&uvOk});return true;
  }
  if(m.t==='pk_peer_answer'&&m.ok&&introAsks.has(m.id)){
    const expected=await introductionChallenge(introAsks.get(m.id),f.channel,c.devId);
    const uvOk=await verifyBindingAssertion(registeredEntries.get(c.devId),m.fa,expected,{rp:'localhost',origin:new URL(web.url).origin});
    introAnswers.push({...m,uvOk});await reply({t:'pk_peer_answer_res',id:m.id,ok:uvOk});return true;
  }
  if(m.t==='roster_bind_get'&&legacyOfferEntry){await reply({t:'roster_bind_offer',entry:legacyOfferEntry});return true;}
  if(m.t==='roster_bind'){binds.push(m);await reply({t:'roster_bind_res',ok:true});return true;}
  if (m.t === 'pk_reg') { registeredEntries.set(c.devId,{id:m.id,alg:m.alg,spki:m.spki,rp:'localhost',dev:c.devId,sign_pub:m.roster_sign_pub,bind:m.roster_sig}); regs.push(m); await reply({ t: 'pk_reg_res', ok: true }); return true; }
  return false;
};
fake.st.onApp=handler(fake);fakePeer.st.onApp=handler(fakePeer);
async function tap(p, selector) {
  const r = await evaluate(p, `(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
  await p.send('Input.dispatchMouseEvent', { type:'mousePressed', ...r, button:'left', clickCount:1 });
  await p.send('Input.dispatchMouseEvent', { type:'mouseReleased', ...r, button:'left', clickCount:1 });
}
async function shot(p, tag) { writeFileSync(OUT+tag+'.png', await shoot(p,'')); }
try {
  if(!process.env.P574_CEREMONY_ONLY) for (const width of [390, 1440]) for (const scheme of ['light', 'dark']) for (const lang of ['zh', 'en']) {
    const tag = `${width}-${scheme}-${lang}`, zh = lang==='zh';
    fake.st.asksOpen.clear(); fake.st.answers=[]; fake.st.sigOk=[]; fake.st.turns=[];
    const p = await newPage(B,width,width===390?844:1050,scheme,{touch:width===390,allow:[web.url,fake.relay+'/']});
    await navigate(p,fake.newPairing(web.url+'?lang='+lang));
    await waitState(p,'awaiting-approval'); await fake.approve(); await waitState(p,'ready');
    await p.send('WebAuthn.enable',{enableUI:false});
    await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
    await evaluate(p, `PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>true;window.__gets=[];const get=navigator.credentials.get.bind(navigator.credentials);navigator.credentials.get=(o)=>{window.__gets.push(Array.from(o.publicKey.challenge));return get(o);};`);
    const nreg=regs.length;
    await evaluate(p, `document.querySelector('#setup-faceid').click()`); await waitFor(p, `!document.querySelector('#confirm').hidden`); await tap(p,'#confirm-yes');
    await waitFor(p, `import('./js/store.js').then(async m=>(await m.dbGet('host')).pk===true)`);
    check(regs.length===nreg+1 && regs.at(-1).roster_sig && regs.at(-1).roster_sign_pub,tag+' registration carries roster binding');
    const reg=regs.at(-1);
    const binding = await evaluate(p, `Promise.all([import('./js/store.js'),import('./js/api.js'),import('./js/ownergrant.js'),import('./proto/wire.js')]).then(async([s,a,o,w])=>{const k=await s.signKey();const pub=await crypto.subtle.importKey('raw',k.pub,'Ed25519',false,['verify']);return crypto.subtle.verify('Ed25519',pub,w.unb64u(${JSON.stringify(reg.roster_sig)}),o.rosterMessage(${JSON.stringify(reg)},await a.myDeviceId(),location.hostname));})`);
    check(binding,tag+' actual registration signature verifies with paired device Ed key');
    const fixture=structuredClone(GRANTS[lang]), otg=fixture.otg, oldTid=otg.tid;
    // Keep the exact host-rendered wording; retime only its public metadata and corresponding fixed tid line.
    otg.iat=Math.floor(Date.now()/1000);otg.exp=otg.iat+86400;
    const core=Object.fromEntries(Object.entries(otg).filter(([k])=>k!=='tid'));
    otg.tid=Buffer.from(await crypto.subtle.digest('SHA-256',new TextEncoder().encode('agentj-owner-grant-v1\n'+canonicalJson(core)))).toString('hex').slice(0,32);
    const summary=fixture.summary.replace((zh?'编号 ':'Grant ')+oldTid+'\n',(zh?'编号 ':'Grant ')+otg.tid+'\n');
    const card={id:randomBytes(16).toString('hex'),tool:'agentj secret grant',summary,owner_grant:otg,ttl:120,cat:['credentials','delete','spend','send','price'],why:zh?'完成这次测试任务':'Complete this test task',passkey_required:true,pk_kind:'sandbox',fa:reg.id,fa_s:'local'};
    const ch = await evaluate(p, `Promise.all([import('./js/ownergrant.js'),import('./js/session.js'),import('./js/api.js'),import('./proto/wire.js')]).then(async([o,s,a,w])=>w.b64u(await o.grantChallenge(${JSON.stringify(card)},s.channel(),await a.myDeviceId())))`);
    card.fa_ch=ch;
    await fake.ask(card); await waitFor(p, `import('./js/snap.js').then(m=>!!m.S.asks.get(${JSON.stringify(card.id)})?.fa_ch)`);
    await waitFor(p, `!document.querySelector('#sheet').hidden && !document.querySelector('#apprAllow').hidden`);
    check(await evaluate(p, `document.querySelector('#cmd').textContent===${JSON.stringify(summary)}`),tag+' existing confirmation card shows every grant step byte-exact');
    check(await evaluate(p, `!document.querySelector('#why').hidden && document.querySelector('#why').textContent.includes(${JSON.stringify(card.why)})`),tag+' existing execution reason line');
    await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);
    await shot(p,tag+'-grant');
    const area=await evaluate(p, `(()=>{const r=document.querySelector('#cmd').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
    await p.send('Input.dispatchMouseEvent',{type:'mouseWheel',...area,deltaX:0,deltaY:4000});
    await sleep(180);
    check(await evaluate(p, `(()=>{const e=document.querySelector('#cmd');return e.scrollHeight-e.clientHeight-e.scrollTop<3;})()`),tag+' actual scrolling exposes last authorized step and scope');
    await shot(p,tag+'-grant-last');
    await tap(p,'#apprAllow');
    await waitFor(p, `window.__gets.length===1`);
    for(let i=0;i<100&&!fake.st.answers.length;i++) await sleep(50);
    check(fake.st.answers.length===1&&fake.st.sigOk[0]&&fake.st.answers[0].fa,tag+' one signed approval with one platform assertion');
    const cd=JSON.parse(Buffer.from(fake.st.answers[0].fa.cd,'base64url').toString());
    check(cd.challenge===ch,tag+' authenticator assertion binds exact displayed grant challenge');
    await sleep(200);
    await evaluate(p, `import('./js/relay.js').then(()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})))`);
    // The host publishes the ordinary notice stream only; no new card or wait at the holding host.
    const hostNotice=structuredClone(NOTICES[lang]), notice=hostNotice.notice.text;
    await fake.addTurn(hostNotice.turn.src,hostNotice.turn.reply.text,hostNotice.turn.end);
    await waitFor(p, `document.body.innerText.includes(${JSON.stringify(notice)})`);
    check(await evaluate(p, `window.__gets.length===1`),tag+' holding-host notice requires no second assertion');
    check(await evaluate(p, `document.querySelector('#words').textContent.trim()==='' && getComputedStyle(document.querySelector('#main')).display==='none' && getComputedStyle(document.querySelector('#rmbar')).display==='none'`),tag+' real Host broadcast has no empty Agent reply card');
    const nstop=stops.length;
    await fake.send({...hostNotice.notice,peer:'test-peer',grant:'0123456789abcdef0123456789abcdef'});
    await waitFor(p, `document.querySelector('#toast .nt-acts button')!==null`);
    await shot(p,tag+'-notice');
    const layout=await evaluate(p, `(()=>{
      const box=document.querySelector('#toast'),text=box.querySelector('.nt-text'),body=box.querySelector('.nt-body'),button=box.querySelector('.nt-acts button');
      const rect=e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
      const b=rect(button),hit=document.elementFromPoint(b.x+b.width/2,b.y+b.height/2);
      return{box:rect(box),text:rect(text),body:rect(body),button:b,width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth,hit:button===hit||button.contains(hit),complete:body.textContent===${JSON.stringify(notice)}};
    })()`);
    writeFileSync(OUT+tag+'-notice-layout.json',JSON.stringify(layout,null,2)+'\n');
    check(layout.complete&&layout.text.width>=180,tag+' notice retains full text in a readable column');
    check(layout.box.height<=300&&!layout.overflow,tag+' notice has bounded height and no page overflow');
    check(layout.button.width>=44&&layout.button.height>=44&&layout.button.x>=0&&layout.button.right<=layout.width&&layout.button.y>=0&&layout.button.bottom<=layout.height&&layout.hit,tag+' stop button is fully visible and directly touchable without scrolling');
    await tap(p,'#toast .nt-acts button');
    for(let i=0;i<100&&stops.length===nstop;i++) await sleep(25);
    check(stops.length===nstop+1&&stops.at(-1).sigOk&&stops.at(-1).value==='0123456789abcdef0123456789abcdef',tag+' Not me action sends paired signed friend+grant revocation');
    check(await evaluate(p, `window.__gets.length===1`),tag+' emergency stop requires no second biometric');
    await fake.addTurn({k:'phone',text:'Empty answer regression'},'','done');
    await waitFor(p, `!document.querySelector('#pgNext').disabled`);
    await evaluate(p, `import('./js/relay.js').then(m=>m.remoteMessage(1))`);
    await waitFor(p, `document.querySelector('#omText').textContent==='Empty answer regression'`);
    check(await evaluate(p, `/无文字回复|No text reply/.test(document.querySelector('#words').textContent)&&getComputedStyle(document.querySelector('#main')).display!=='none'&&getComputedStyle(document.querySelector('#rmbar')).display!=='none'`),tag+' ordinary completed user turn retains empty-answer message and Agent card');
    await fake.addTurn({k:'host',text:'Host reply regression'},'Visible host reply','done');
    await waitFor(p, `document.querySelector('#words').textContent.includes('Visible host reply')`);
    check(await evaluate(p, `getComputedStyle(document.querySelector('#main')).display!=='none'`),tag+' host turn with real reply retains its reply card');
    await evaluate(p, `import('./js/relay.js').then(m=>m.remoteMessage(-1))`);
    await waitFor(p, `document.querySelector('#omText').textContent==='Empty answer regression'`);
    check(await evaluate(p, `/无文字回复|No text reply/.test(document.querySelector('#words').textContent)&&getComputedStyle(document.querySelector('#main')).display!=='none'`),tag+' history navigation restores ordinary empty reply');
    await evaluate(p, `import('./js/relay.js').then(m=>m.remoteMessage(-1))`);
    await waitFor(p, `document.querySelector('#rm').classList.contains('host-notice')`);
    check(await evaluate(p, `document.querySelector('#words').textContent.trim()===''&&getComputedStyle(document.querySelector('#rmbar')).display==='none'`),tag+' history return preserves notice-only rendering');
    await evaluate(p, `import('./js/relay.js').then(m=>m.remoteMessage(1))`);
    await waitFor(p, `document.querySelector('#omText').textContent==='Empty answer regression'`);
    check(await evaluate(p, `getComputedStyle(document.querySelector('#rmbar')).display!=='none'`),tag+' leaving a notice restores reply controls');
    await evaluate(p, `import('./js/relay.js').then(m=>m.remoteMessage(1))`);
    await waitFor(p, `document.querySelector('#words').textContent.includes('Visible host reply')`);
    // Malicious caller mixes the display with a challenge over different authorization: do not call get().
    const bad={...card,id:randomBytes(16).toString('hex'),summary:summary+'\nchanged'};
    await fake.ask(bad); await waitFor(p, `import('./js/snap.js').then(m=>m.S.asks.has(${JSON.stringify(bad.id)}))`); await sleep(100);
    check(await evaluate(p, `import('./js/api.js').then(async a=>{const s=await import('./js/snap.js');return !(await a.approve(s.S.asks.get(${JSON.stringify(bad.id)}),'allow')).ok&&window.__gets.length===1;})`),tag+' mismatched display/challenge never requests biometric signature');
    await fake.askDone(bad.id,'deny');
    await p.dispose();
  }
  // Narrow ceremony leg: the existing same-owner card shows the exact roster its signed challenge covers.
  {
    fake.st.asksOpen.clear();
    const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
    await navigate(p,fake.newPairing(web.url+'?lang=zh'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
    await p.send('WebAuthn.enable',{enableUI:false});
    await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
    await evaluate(p, `PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>true;window.__gets=[];window.__creates=0;const get=navigator.credentials.get.bind(navigator.credentials),create=navigator.credentials.create.bind(navigator.credentials);navigator.credentials.get=o=>{window.__gets.push(Array.from(o.publicKey.challenge));return get(o);};navigator.credentials.create=o=>{window.__creates++;return create(o);};`);
    await evaluate(p, `document.querySelector('#setup-faceid').click()`);await waitFor(p, `!document.querySelector('#confirm').hidden`);await tap(p,'#confirm-yes');
    await waitFor(p, `import('./js/store.js').then(async s=>(await s.dbGet('host')).pk===true)`);
    const reg=regs.at(-1);
    const dev=await evaluate(p, `import('./js/api.js').then(a=>a.myDeviceId())`);
    const own=await evaluate(p,`Promise.all([import('./js/store.js'),import('./proto/wire.js')]).then(async([s,w])=>w.b64u((await s.signKey()).pub))`);
    const entry={id:reg.id,alg:reg.alg,spki:reg.spki,rp:'localhost',dev,name:'Work phone',sign_pub:own};
    // Simulate a pre-P574 installation: the actual system credential exists, but has no local roster descriptor.
    await evaluate(p, `Promise.all([import('./js/store.js'),import('./js/session.js')]).then(([s,c])=>s.dbDel('owner-roster:'+c.channel()))`);
    legacyOfferEntry=entry;
    const incoming={id:randomBytes(16).toString('hex'),tool:'peer_question',summary:'Upgrade binding',ttl:120,pq:{pairing:true,friend:'test-peer',name:'Spare host',code:'482913',challenge:'0'.repeat(32),pairing_scope:JSON.stringify([['local-agent','host-ed',own,'n',[]],['test-peer','peer-ed','remote-ed','n',[]]])}};
    await fake.ask(incoming);await waitFor(p, `!document.querySelector('#confirm').hidden`);
    await evaluate(p, `import('./js/ui.js').then(m=>m.toastOff())`);await shot(p,'390-light-zh-legacy-bind');
    await tap(p,'#confirm-yes');for(let i=0;i<100&&!binds.length;i++)await sleep(25);
    await fake.askDone(incoming.id,'ended');legacyOfferEntry=null;
    check(binds.length===1,'legacy key migration emits binding only after successful UV and local SPKI verification');
    check(await evaluate(p, `window.__creates===1&&window.__gets.length===1`),'legacy migration calls get only; no key replacement or re-creation');
    const local=await evaluate(p,`Promise.all([import('./js/store.js'),import('./js/session.js')]).then(([s,c])=>s.dbGet('owner-roster:'+c.channel()))`);
    check(local.id===reg.id&&local.spki===reg.spki&&local.dev===dev,'legacy migration preserves exact existing credential and public key');
    const {bindRegistration}=await import('../public/js/ownergrant.js');
    const ed=await crypto.subtle.generateKey('Ed25519',false,['sign','verify']);
    const remoteKey={priv:ed.privateKey,pub:new Uint8Array(await crypto.subtle.exportKey('raw',ed.publicKey))};
    const pk=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},false,['sign','verify']);
    const rreg={id:b64u(randomBytes(32)),alg:-7,spki:b64u(await crypto.subtle.exportKey('spki',pk.publicKey))};
    const rsigned=await bindRegistration(rreg,'tablet',remoteKey,'localhost');
    const remote={...rreg,rp:'localhost',dev:'tablet',name:'Spare tablet',sign_pub:rsigned.roster_sign_pub,bind:rsigned.roster_sig};
    // Compromised host keeps real credential id/Ed but substitutes its own SPKI: client must not sign the binding.
    await fake.send({t:'roster_bind_offer',entry:{...entry,spki:remote.spki}});await waitFor(p, `!document.querySelector('#confirm').hidden`);await tap(p,'#confirm-yes');
    await waitFor(p, `window.__gets.length===2`);await sleep(100);
    check(binds.length===1,'legacy migration refuses host-substituted SPKI after checking actual authenticator signature');
    const p2=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fakePeer.relay+'/']});
    await navigate(p2,fakePeer.newPairing(web.url+'?lang=en'));await waitState(p2,'awaiting-approval');await fakePeer.approve();await waitState(p2,'ready');
    await p2.send('WebAuthn.enable',{enableUI:false});await p2.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
    await evaluate(p2,`PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable=async()=>true;window.__creates=0;window.__gets=0;const get=navigator.credentials.get.bind(navigator.credentials),create=navigator.credentials.create.bind(navigator.credentials);navigator.credentials.get=o=>{window.__gets++;return get(o);};navigator.credentials.create=o=>{window.__creates++;return create(o);};`);
    await evaluate(p2,`document.querySelector('#setup-faceid').click()`);await waitFor(p2,`!document.querySelector('#confirm').hidden`);await tap(p2,'#confirm-yes');await waitFor(p2,`import('./js/store.js').then(async s=>(await s.dbGet('host')).pk===true)`);
    const local2=await evaluate(p2,`Promise.all([import('./js/store.js'),import('./js/session.js')]).then(([s,c])=>s.dbGet('owner-roster:'+c.channel()))`);
    const ends=[['local-agent','host-ed',own,'nonce-a',[{...local,name:'Work phone'},remote]],['test-peer','peer-ed',local2.sign_pub,'nonce-b',[{...local2,name:'Spare tablet'}]]];
    const scope=JSON.stringify(ends);
    const challenge=await evaluate(p,`import('./proto/wire.js').then(async w=>(await w.sha256hex(new TextEncoder().encode(${JSON.stringify(scope)}))).slice(0,32))`);
    const card={id:randomBytes(16).toString('hex'),tool:'peer_question',summary:'Same owner roster approval',ttl:120,pq:{pairing:true,same_owner:true,friend:'test-peer',name:'Spare host',code:String(BigInt('0x'+challenge)%1000000n).padStart(6,'0'),challenge,pairing_scope:scope}};
    await fake.ask(card);await waitFor(p,`document.querySelector('#frOwnerAllow')&&!document.querySelector('#frOwnerAllow').disabled`);
    check(await evaluate(p,`document.querySelector('#frAsk').innerText.includes('Work phone · '+${JSON.stringify(dev)})&&document.querySelector('#frAsk').innerText.includes('Spare tablet · '+${JSON.stringify(local2.dev)})`),'ceremony existing card displays both pinned signing rosters');
    await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);await shot(p,'390-light-zh-ceremony-roster');
    check(await evaluate(p,`import('./js/snap.js').then(s=>{const a=s.S.asks.get(${JSON.stringify(card.id)});return a.pairing_roster.every(r=>r.trusted.length===1)&&a.pairing_roster[0].pending.length===1&&a.pairing_roster.every(r=>r.trusted.every(e=>e.id!==${JSON.stringify(remote.id)}));})`),'fake self-signed extra device remains pending and never enters initial trusted roots');
    check(await evaluate(p,`document.querySelector('#frAsk').innerText.includes('待已认可遥控器批准')`),'extra device is explicitly shown as awaiting trusted approval');
    const card2={...card,id:randomBytes(16).toString('hex'),pq:{...card.pq,friend:'local-agent',name:'Work host'}};
    await fakePeer.ask(card2);await waitFor(p2,`document.querySelector('#frOwnerAllow')&&!document.querySelector('#frOwnerAllow').disabled`);
    check(await evaluate(p,`document.querySelector('.fr-owner-code').textContent`)===await evaluate(p2,`document.querySelector('.fr-owner-code').textContent`),'both independent actual clients derive the same six-digit scope code');
    await evaluate(p2,`import('./js/ui.js').then(m=>m.toastOff())`);await shot(p2,'390-light-en-ceremony-roster');
    await tap(p,'#frOwnerAllow');for(let i=0;i<100&&!ceremonies.length;i++)await sleep(25);
    check(ceremonies.length===1&&ceremonies[0].sigOk&&ceremonies[0].uvOk&&ceremonies[0].value===challenge,'ceremony signs the exact visible roster challenge with paired device key');
    await tap(p2,'#frOwnerAllow');for(let i=0;i<100&&ceremonies.length<2;i++)await sleep(25);
    check(ceremonies.length===2&&ceremonies.every(c=>c.sigOk&&c.uvOk&&c.op==='owner-confirm-v3')&&new Set(ceremonies.map(c=>c.host)).size===2,'both actual paired clients provide their own UV plus Ed proof to confirm unique roots');
    check(await evaluate(p, `window.__creates===1&&window.__gets.length===3`),'setup adds one root confirmation UV and never replaces the existing key');
    await fakePeer.askDone(card2.id,'allow');
    await evaluate(p2,`import('./js/pkpeer.js').then(m=>m.startRosterIntro())`);await waitFor(p2,`document.querySelector('#pkp')?.dataset.mode==='wait'`);
    check(introRequest!==null,'existing-key introduction uses pk_peer_req roster:true');
    const intro={t:'pk_peer_ask',id:introRequest.id,device:local2.dev,name:'Spare tablet',code:introRequest.code,roster:true,sign_pub:local2.sign_pub,at:Math.floor(Date.now()/1000),exp:Math.floor(Date.now()/1000)+300,fa:local.id};
    intro.fa_ch=await evaluate(p,`Promise.all([import('./js/ownergrant.js'),import('./js/session.js'),import('./js/api.js'),import('./proto/wire.js')]).then(async([o,s,a,w])=>w.b64u(await o.introductionChallenge(${JSON.stringify(intro)},s.channel(),await a.myDeviceId())))`);
    introAsks.set(intro.id,intro);await fake.send(intro);await waitFor(p,`document.querySelector('#pkp')?.dataset.mode==='ask'`);
    await evaluate(p,`import('./js/ui.js').then(m=>m.toastOff())`);await shot(p,'390-light-zh-existing-key-intro');
    await tap(p,'#pkp-approve');for(let i=0;i<100&&!introAnswers.length;i++)await sleep(25);
    check(introAnswers.length===1&&introAnswers[0].uvOk,'trusted root gives a real UV introduction bound to the existing device Ed and roster purpose');
    check(await evaluate(p,`window.__creates===1&&window.__gets.length===4`),'trusted introducer uses an existing passkey without creating or replacing one');

    await fakePeer.send({t:'pk_peer_done',id:introRequest.id,ok:true,roster:true,by:'Trusted phone'});await waitFor(p2,`document.querySelector('#pkp').hidden`);
    check(await evaluate(p2,`window.__creates===1&&window.__gets===1`),'roster-only introduction completion never creates or replaces the existing key');
    await p2.dispose();
    await fake.askDone(card.id,'allow');
    for(const variant of ['scope','signer']) {
      const changed=structuredClone(ends);
      if(variant==='scope')changed[1][4][0].name='Substituted';else {changed[0][2]='forged';changed[0][4][0].sign_pub='forged';}
      const changedScope=JSON.stringify(changed);
      const changedHash=variant==='scope'?challenge:await evaluate(p,`import('./proto/wire.js').then(async w=>(await w.sha256hex(new TextEncoder().encode(${JSON.stringify(changedScope)}))).slice(0,32))`);
      const bad={...card,id:randomBytes(16).toString('hex'),pq:{...card.pq,pairing_scope:changedScope,challenge:changedHash,code:String(BigInt('0x'+changedHash)%1000000n).padStart(6,'0')}};
      await fake.ask(bad);await sleep(100);
      await waitFor(p,`document.querySelector('#frOwnerAllow')?.disabled===true`);
      await evaluate(p,`document.querySelector('#frOwnerAllow').click()`);await sleep(50);
      check(ceremonies.length===2,'ceremony '+variant+' substitution cannot approve');
      await fake.askDone(bad.id,'deny');
    }
    await p.dispose();
  }
  writeFileSync(OUT+(process.env.P574_CEREMONY_ONLY?'ceremony-checks.json':'checks.json'),JSON.stringify({checks,physicalBiometric:false},null,2)+'\n');
  if(process.env.AJ_PARITY_OUT) writeFileSync(process.env.AJ_PARITY_OUT,JSON.stringify({suite:'web/test/p574.mjs',results:{'p574-phone':'pass'}}));
  console.log(`P574 phone PASS: ${checks.length} checks; evidence → ${OUT}`);
} finally { await B.close(); await fake.stop(); await fakePeer.stop(); await web.stop(); }
