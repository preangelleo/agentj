#!/usr/bin/env node
// Real Chromium WebAuthn virtual authenticator + real Noise/device signature.
// A delayed native promise reproduces iOS returning an assertion across a socket gap.
import assert from 'node:assert/strict';
import {generateKeyPairSync,randomBytes,createHash,verify,webcrypto} from 'node:crypto';
import {mkdirSync,writeFileSync} from 'node:fs';
process.env.P541_LIBRARY='1';
const H=await import('./p541.mjs');
const {waitFor,sleep}=await import('./browser.mjs');
const wire=await import('../../protocol/wire.js');
H.web.url=H.web.url.replace('127.0.0.1','localhost');
const out=process.env.P0282_AIR_UV_OUT||'reports/qa/p0282-air-web/uv';mkdirSync(out,{recursive:true});const rows=[];
try{
 for(const interrupted of [false,true]){
  const p=await H.setup(390,844,'light');
  const pair=generateKeyPairSync('ec',{namedCurve:'P-256'}),cred=randomBytes(16);
  await p.send('WebAuthn.enable',{enableUI:false});
  const {authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
  await p.send('WebAuthn.addCredential',{authenticatorId,credential:{credentialId:cred.toString('base64'),isResidentCredential:true,rpId:'localhost',privateKey:pair.privateKey.export({type:'pkcs8',format:'der'}).toString('base64'),userHandle:Buffer.from('isolated-0282').toString('base64'),signCount:0}});
  const card=H.currentCard();card.fa=cred.toString('base64url');await H.fake.ask(card);await sleep(150);
  let accepted=0;
  H.fake.st.onApp=async(c,m,send)=>{
   if(m.t!=='answer')return false;
   assert.equal(m.approval_v,2);assert.equal(m.revision,card.revision);assert.equal(m.envelope_sha256,card.envelope_sha256);assert.ok(m.fa);
   const cd=Buffer.from(m.fa.cd,'base64url'),ad=Buffer.from(m.fa.ad,'base64url'),client=JSON.parse(cd);
   assert.equal(client.challenge,card.fa_ch);assert.equal(client.origin,new URL(H.web.url).origin);assert.equal(client.type,'webauthn.get');assert.ok(ad[32]&4,'UV flag preserved');assert.equal(m.fa.id,card.fa);
   assert.ok(verify('sha256',Buffer.concat([ad,createHash('sha256').update(cd).digest()]),pair.publicKey,Buffer.from(m.fa.sig,'base64url')),'native WebAuthn signature verifies');
   const sk=await webcrypto.subtle.importKey('raw',wire.unb64u(H.fake.devs.find(d=>d.id===c.devId).sk),'Ed25519',false,['verify']);
   assert.ok(await webcrypto.subtle.verify('Ed25519',sk,wire.unb64u(m.sig),await wire.approvalV2Message(H.fake.channel,c.devId,{...card,envelope_verified:true},'allow')),'v2 shown-envelope device signature verifies');
   accepted++;await H.fake.askDone(card.id,'allow');return true;
  };
  if(interrupted)await H.ev(p,`{const native=navigator.credentials.get.bind(navigator.credentials);navigator.credentials.get=opts=>native(opts).then(async cred=>{window.__nativeUvReady=true;while(!window.__releaseUv)await new Promise(r=>setTimeout(r,20));return cred;});}`);
  await H.ev(p,`import('./js/api.js').then(m=>window.__airApi=m)`);await H.ev(p,`window.__uvCard=${JSON.stringify({...card,envelope_verified:true})};window.__uvResult=window.__airApi.approve(window.__uvCard,'allow');void 0`);
  if(interrupted){await waitFor(p,'window.__nativeUvReady===true');H.fake.kick();await sleep(500);await waitFor(p,`window.__ajState==='ready'`,15000);await H.ev(p,'window.__releaseUv=true');}
  for(let i=0;i<200&&accepted===0;i++)await sleep(50);
  assert.equal(accepted,1,'native assertion is handed in once and accepted');await waitFor(p,`document.body.dataset.sheet==='0'`);
  assert.equal(await H.ev(p,`document.getElementById('toast').textContent.includes('连接已恢复')`),false,'reconnection produces no recovery overlay');
  rows.push({engine:'Chromium',native_virtual_webauthn:true,physical_human:false,socket_interrupted:interrupted,accepted});await p.dispose();
 }
}finally{writeFileSync(out+'/result.json',JSON.stringify({rows},null,2));await H.B.close();await H.fake.stop();await H.web.stop();}
console.log('PASS p0282-air native virtual WebAuthn '+rows.length+' cases: uninterrupted and socket-gap hand-in');
