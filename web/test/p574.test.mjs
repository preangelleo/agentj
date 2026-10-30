import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { bindRegistration, rosterMessage, grantChallenge, introductionChallenge } from '../public/js/ownergrant.js';
import { shownDigest, canonicalJson, sha256hex } from '../public/proto/wire.js';
import { elevateChallenge } from '../public/js/faceid.js';
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const grantFixture=JSON.parse(readFileSync(new URL('./fixtures/p574-grants.json',import.meta.url),'utf8'));
const grantNow=grantFixture.en.otg.iat;

test('P574 roster registration signs exact canonical public binding; substitution fails', async () => {
  const kp = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']);
  const key = { priv: kp.privateKey, pub: new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey)) };
  const reg = { id: 'credential', alg: -7, spki: 'public-key' };
  const out = await bindRegistration(reg, 'remote', key, 'localhost');
  assert.equal(new TextDecoder().decode(rosterMessage(reg, 'remote', 'localhost')), 'agentj-owner-roster-v1\n{"alg":-7,"dev":"remote","id":"credential","rp":"localhost","spki":"public-key"}');
  const sig = Buffer.from(out.roster_sig, 'base64url');
  assert.ok(await crypto.subtle.verify('Ed25519', kp.publicKey, sig, rosterMessage(reg, 'remote', 'localhost')));
  for (const patch of [{ id: 'other' }, { alg: -8 }, { spki: 'substituted' }]) assert.equal(await crypto.subtle.verify('Ed25519', kp.publicKey, sig, rosterMessage({ ...reg, ...patch }, 'remote', 'localhost')), false);
  assert.equal(await crypto.subtle.verify('Ed25519', kp.publicKey, sig, rosterMessage(reg, 'other', 'localhost')), false);
});
test('P574 grant challenge binds actual host-rendered public OTG, shown bytes and card identity', async () => {
  const f=grantFixture.en;
  const m={tool:f.tool,id:'card',pk_kind:'sandbox',summary:f.summary,owner_grant:f.otg};
  const expected=await elevateChallenge('channel','remote',m.id,'sandbox',m.id,await shownDigest(m.tool,m.summary));
  assert.deepEqual(await grantChallenge(m,'channel','remote',grantNow),expected);
  for(const patch of [{summary:m.summary.replace('Deploy sample','Another task')},{id:'other'},{pk_kind:'perm'}])assert.notDeepEqual(await grantChallenge({...m,...patch},'channel','remote',grantNow),expected);
  for(const patch of [{summary:m.summary+' '},{pk_kind:'unrecognized'},{owner_grant:undefined}])await assert.rejects(grantChallenge({...m,...patch},'channel','remote',grantNow));
});
test('P574 P547 introduction binds new paired signing public key', async () => {
  const m = { id: 'card', device: 'new-remote', name: 'Tablet', code: '123456', sign_pub: 'A'.repeat(43) };
  const ch = await introductionChallenge(m, 'channel', 'approver');
  assert.notDeepEqual(await introductionChallenge({ ...m, sign_pub: 'B'.repeat(43) }, 'channel', 'approver'), ch);
  await assert.rejects(introductionChallenge({ ...m, sign_pub: '' }, 'channel', 'approver'));
});
async function entry(name, dev) {
  const ed=await crypto.subtle.generateKey('Ed25519',false,['sign','verify']);
  const key={priv:ed.privateKey,pub:new Uint8Array(await crypto.subtle.exportKey('raw',ed.publicKey))};
  const pk=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},false,['sign','verify']);
  const reg={id:Buffer.from(name).toString('base64url'),alg:-7,spki:Buffer.from(await crypto.subtle.exportKey('spki',pk.publicKey)).toString('base64url')};
  const signed=await bindRegistration(reg,dev,key,'localhost');
  return {key,pk,record:{...reg,dev,rp:'localhost',name,sign_pub:signed.roster_sign_pub,bind:signed.roster_sig}};
}
test('P574 F7 initial roster rejects substituted SPKI/id, unsigned entries, missing self and fake local descriptor', async()=>{
  const {pairingScope}=await import('../public/js/ownergrant.js');const {sha256hex}=await import('../public/proto/wire.js');
  const local=await entry('Phone','phone'),other=await entry('Tablet','tablet');
  const ends=[['here','host-ed',local.record.sign_pub,'nonce-a',[local.record]],['there','peer-ed',other.record.sign_pub,'nonce-b',[other.record]]];
  const pq=async(e)=>{const pairing_scope=JSON.stringify(e);return {friend:'there',pairing_scope,challenge:(await sha256hex(new TextEncoder().encode(pairing_scope))).slice(0,32)};};
  assert.deepEqual((await pairingScope(await pq(ends),local.record.sign_pub,'phone',local.record)).map(e=>e.remotes),[['Phone · phone'],['Tablet · tablet']]);
  for(const field of ['spki','id','alg','rp','dev']) {
    const bad=structuredClone(ends);bad[0][4][0][field]=field==='spki'?other.record.spki:field==='alg'?-8:'substituted';
    await assert.rejects(pairingScope(await pq(bad),local.record.sign_pub,'phone',local.record),field+' must be bound despite preserving real own[2] and names');
  }
  const missing=structuredClone(ends);missing[0][4]=[];
  await assert.rejects(pairingScope(await pq(missing),local.record.sign_pub,'phone',local.record));
  const unsigned=structuredClone(ends);delete unsigned[1][4][0].bind;
  await assert.rejects(pairingScope(await pq(unsigned),local.record.sign_pub,'phone',local.record));
  await assert.rejects(pairingScope(await pq(ends),local.record.sign_pub,'phone',{...local.record,id:'fake-local'}));
  await assert.rejects(pairingScope(await pq(ends),local.record.sign_pub,'phone',null));
});
test('P574 legacy UV binding verifies actual SPKI, UV, credential, RP, origin and challenge',async()=>{
  const {bindChallenge,verifyBindingAssertion}=await import('../public/js/ownergrant.js');const {concat,sha256}=await import('../public/proto/noise.js');
  const local=await entry('Phone','phone'),other=await entry('Other','phone');const ch=await bindChallenge(local.record);
  const b64=b=>Buffer.from(b).toString('base64url');
  const make=async(patch={},flags=5)=>{
    const cd=new TextEncoder().encode(JSON.stringify({type:'webauthn.get',origin:'http://localhost',challenge:b64(ch),...patch}));
    const ad=concat(await sha256(new TextEncoder().encode('localhost')),new Uint8Array([flags,0,0,0,1]));
    const raw=new Uint8Array(await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},local.pk.privateKey,concat(ad,await sha256(cd))));
    const integer=u=>{let i=0;while(i<u.length-1&&u[i]===0)i++;let v=u.slice(i);if(v[0]&128)v=concat(new Uint8Array([0]),v);return concat(new Uint8Array([2,v.length]),v);};
    const ints=concat(integer(raw.slice(0,32)),integer(raw.slice(32)));const sig=concat(new Uint8Array([48,ints.length]),ints);
    return {id:local.record.id,cd:b64(cd),ad:b64(ad),sig:b64(sig)};
  };
  const env={rp:'localhost',origin:'http://localhost'},fa=await make();
  assert.ok(await verifyBindingAssertion(local.record,fa,ch,env));
  assert.equal(await verifyBindingAssertion({...local.record,spki:other.record.spki},fa,ch,env),false);
  assert.equal(await verifyBindingAssertion(local.record,{...fa,id:'wrong'},ch,env),false);
  for(const patch of [{origin:'https://evil.invalid'},{challenge:'wrong'},{type:'webauthn.create'},{crossOrigin:true}])assert.equal(await verifyBindingAssertion(local.record,await make(patch),ch,env),false);
  assert.equal(await verifyBindingAssertion(local.record,await make({},1),ch,env),false);
});
test('P574 extra self-signed device never becomes initial trust; tuple roots and derived code are mandatory',async()=>{
  const {pairingScope,pairingCode}=await import('../public/js/ownergrant.js');const {sha256hex}=await import('../public/proto/wire.js');
  const local=await entry('Phone','phone'),peer=await entry('Tablet','tablet'),fake=await entry('Extra','fake-phone');fake.record.name='Phone';
  const ends=[['here','host-ed',local.record.sign_pub,'nonce-a',[local.record,fake.record]],['there','peer-ed',peer.record.sign_pub,'nonce-b',[peer.record]]];
  const pq=async e=>{const pairing_scope=JSON.stringify(e),challenge=(await sha256hex(new TextEncoder().encode(pairing_scope))).slice(0,32);return {friend:'there',pairing_scope,challenge,code:pairingCode(challenge)};};
  const out=await pairingScope(await pq(ends),local.record.sign_pub,'phone',local.record);
  assert.equal(out[0].trusted.length,1);assert.equal(out[0].trusted[0].id,local.record.id);
  assert.equal(out[0].pending.length,1);assert.equal(out[0].pending[0].id,fake.record.id);
  assert.equal(out.flatMap(e=>e.trusted).some(e=>e.id===fake.record.id),false);
  const swapped=structuredClone(ends);swapped[0][2]=fake.record.sign_pub;
  await assert.rejects(pairingScope(await pq(swapped),local.record.sign_pub,'phone',local.record));
  await assert.rejects(pairingScope({...await pq(ends),code:'000000'},local.record.sign_pub,'phone',local.record));
});
test('P574 root-confirm UV is bound to explicit modern op, exact scope and locally generated nonce',async()=>{
  const {ownerConfirmChallenge}=await import('../public/js/ownergrant.js');
  const target={friend:'peer',op:'owner-confirm-v3',value:'1'.repeat(32)};
  const ch=await ownerConfirmChallenge(target,'nonce','channel','device');
  assert.notDeepEqual(await ownerConfirmChallenge({...target,value:'2'.repeat(32)},'nonce','channel','device'),ch);
  assert.notDeepEqual(await ownerConfirmChallenge(target,'other','channel','device'),ch);
  await assert.rejects(ownerConfirmChallenge({...target,op:'owner-confirm'},'nonce','channel','device'));
});

const fixturePython=fileURLToPath(new URL('../../host/.venv/bin/python',import.meta.url));
test('P574 screenshot grant fixture exactly matches the real host renderer', {skip:!existsSync(fixturePython)},()=>{
  const r=spawnSync(fixturePython,[fileURLToPath(new URL('./p574-fixture.py',import.meta.url))],{encoding:'utf8'});
  assert.equal(r.status,0,r.stdout+r.stderr);
});

test('P574 OTG clock bounds and JCS metadata must verify before biometric signing',async()=>{
  const {verifyPublicGrant,checkGrantTime}=await import('../public/js/ownergrant.js');
  const f=grantFixture.zh, base={tool:f.tool,summary:f.summary,owner_grant:f.otg};
  // The original hash/text were generated by Python og.jcs/card_text, including Chinese labels.
  assert.deepEqual(await verifyPublicGrant(base,grantNow),f.otg);
  const reordered=Object.fromEntries(Object.entries(f.otg).reverse());
  await verifyPublicGrant({...base,owner_grant:reordered},grantNow);
  await verifyPublicGrant(base,grantNow-600);
  await assert.rejects(verifyPublicGrant(base,grantNow-601));
  await verifyPublicGrant(base,f.otg.exp-0.001);
  await assert.rejects(verifyPublicGrant(base,f.otg.exp));
  const changed=async patch=>{
    const otg={...structuredClone(f.otg),...patch};const core=Object.fromEntries(Object.entries(otg).filter(([k])=>k!=='tid'));
    otg.tid=(await sha256hex(new TextEncoder().encode('agentj-owner-grant-v1\n'+canonicalJson(core)))).slice(0,32);
    return {...base,owner_grant:otg,summary:base.summary.replace('编号 '+f.otg.tid+'\n','编号 '+otg.tid+'\n')};
  };
  for(const duration of [86399,86401])await assert.rejects(verifyPublicGrant(await changed({exp:grantNow+duration}),grantNow));
  await assert.rejects(verifyPublicGrant(await changed({iat:grantNow+601,exp:grantNow+601+86400}),grantNow));
  await assert.rejects(verifyPublicGrant({...base,owner_grant:{...f.otg,why:'altered without hash'}},grantNow));
  await assert.rejects(verifyPublicGrant({...base,summary:base.summary.replace('编号 '+f.otg.tid,'编号 '+'0'.repeat(32))},grantNow));
  await assert.rejects(verifyPublicGrant({...base,summary:base.summary+'\nextra tail'},grantNow));
  assert.throws(()=>checkGrantTime(f.otg,f.otg.exp),'tap-time expiration remains fail closed after preparation');
});

test('P574 owner confirmation uses the app static controls dependency without dynamic imports',()=>{
  const snap=readFileSync(new URL('../public/js/snap.js',import.meta.url),'utf8');
  const app=readFileSync(new URL('../public/app.js',import.meta.url),'utf8');
  assert.doesNotMatch(snap,/\bimport\s*\(/);
  assert.match(app,/prepareOwnerConfirm: \(target\) => controls\.signed\('fr_set', target, \{ t: 'fr_set', \.\.\.target \}\)/);
  assert.match(snap,/if \(!prepareOwnerConfirm\) throw new Error\('unconfigured owner confirmation'\)/);
  assert.match(snap,/const obj = await prepareOwnerConfirm\(target\)/);
  assert.match(snap,/ownerConfirmChallenge\(target, obj\.n, ch, dev\)/);
});
