import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {checkOffer,checkGrant,fileAAD,fileNonce,fileGetMessage,receiveFile,FILE_BUFFER_MAX,fileRetryDelay} from '../public/js/private-transfer.js';
import {sanitize} from '../public/js/mediawire.js';
const hash=b=>createHash('sha256').update(b).digest('hex'),b64=b=>Buffer.from(b).toString('base64url');
const now=Math.floor(Date.now()/1000),transfer='b'.repeat(32),relay='wss://relay.agentj.app';
async function fixture(bytes=90000){
 const data=new Uint8Array(bytes).fill(37),key=crypto.getRandomValues(new Uint8Array(32)),prefix=crypto.getRandomValues(new Uint8Array(8));
 const kp=await crypto.subtle.generateKey('Ed25519',false,['sign','verify']),keypair={priv:kp.privateKey,pub:new Uint8Array(await crypto.subtle.exportKey('raw',kp.publicKey))};
 const card={transport:'https1',id:'a'.repeat(32),seat:'c'.repeat(22),bytes,expires:now+86400,chunk_size:65536,sha256:hash(data),mime:'application/pdf'};
 const result={direct:true,total:bytes,chunk_size:65536,chunks:Math.ceil(bytes/65536),card,key:b64(key),nonce_prefix:b64(prefix),endpoint:'https://relay.agentj.app/v1/file/'+card.seat+'/'+card.id,grant:{v:1,offer:card.id,transfer,device_pk:b64(keypair.pub),expires:card.expires}};
 const aes=await crypto.subtle.importKey('raw',key,'AES-GCM',false,['encrypt']);
 const encrypted=await Promise.all(Array.from({length:result.chunks},(_,i)=>crypto.subtle.encrypt({name:'AES-GCM',iv:fileNonce(prefix,i),additionalData:fileAAD(card,transfer,i)},aes,data.slice(i*65536,(i+1)*65536)).then(x=>new Uint8Array(x))));
 const requests=[];
 const fetcher=async(url,options)=>{const index=Number(url.split('/').at(-1)),h=options.headers;requests.push({url,options});assert.equal(options.redirect,'error');assert.equal(options.credentials,'omit');assert.equal(options.cache,'no-store');
  assert.equal(h['X-AJ-File-Key'],b64(keypair.pub));assert.equal(h['X-AJ-File-Transfer'],transfer);assert.ok(await crypto.subtle.verify('Ed25519',kp.publicKey,Buffer.from(h['X-AJ-File-Signature'],'base64url'),fileGetMessage(new URL(url).pathname,h['X-AJ-File-Time'],h['X-AJ-File-Nonce'],transfer)));
  assert.ok(!url.includes(result.key)&&!JSON.stringify(options).includes(result.key),'file key never enters HTTP');return new Response(encrypted[index],{headers:{'content-length':String(encrypted[index].length)}});};
 return {data,keypair,card,result,encrypted,fetcher,requests};
}
test('#578 HTTPS receive verifies every AEAD block and complete SHA with existing device proofs',async()=>{
 const f=await fixture(),progress=[];const data=await receiveFile(f.card,f.result,{...f,transfer,relay,onProgress:n=>progress.push(n)});
 assert.equal(hash(data),f.card.sha256);assert.equal(f.requests.length,2);assert.equal(progress.at(-1),f.data.length);assert.equal(new Set(f.requests.map(x=>x.options.headers['X-AJ-File-Nonce'])).size,2);
});
test('#578 invalid grants and over-budget offers fail before any HTTP access',async()=>{
 const f=await fixture();assert.throws(()=>checkOffer({...f.card,bytes:FILE_BUFFER_MAX+1}),e=>e.code==='budget');
 for(const patch of [{endpoint:'https://evil.invalid/v1/file/'+f.card.seat+'/'+f.card.id},{endpoint:f.result.endpoint+'?key=x'},{endpoint:f.result.endpoint+'#key=x'},{grant:{...f.result.grant,device_pk:'x'.repeat(43)}},{grant:{...f.result.grant,transfer:'d'.repeat(32)}},{grant:{...f.result.grant,expires:now+86401}},{total:f.card.bytes+1},{card:{...f.card,sha256:'0'.repeat(64)}}])assert.throws(()=>checkGrant(f.card,{...f.result,...patch},transfer,f.keypair.pub,relay));
 assert.equal(f.requests.length,0);
});
test('#578 ciphertext tamper, bad nonce, wrong final hash and revoked live state never produce a file',async()=>{
 for(const fault of ['tag','nonce','sha','live','size']){
  const f=await fixture();if(fault==='tag')f.encrypted[0][0]^=1;if(fault==='nonce')f.result.nonce_prefix=b64(new Uint8Array(8));if(fault==='sha'){f.card.sha256='0'.repeat(64);f.result.card=f.card;}
  const fetcher=fault==='size'?async()=>new Response(new Uint8Array(65536+17)):f.fetcher;
  await assert.rejects(receiveFile(f.card,f.result,{...f,fetcher,transfer,relay,live:()=>fault!=='live'}));
 }
});
test('#578 retry uses fresh HTTP nonce with the same claimed transfer',async()=>{
 const f=await fixture(60000);let retry=0;const seen=[];const fetcher=async(url,options)=>{seen.push(options.headers);if(retry++===0)return new Response('',{status:503});return f.fetcher(url,options);};
 assert.equal(hash(await receiveFile(f.card,f.result,{...f,fetcher,transfer,relay})),f.card.sha256);assert.equal(seen.length,2);assert.notEqual(seen[0]['X-AJ-File-Nonce'],seen[1]['X-AJ-File-Nonce']);assert.equal(seen[0]['X-AJ-File-Transfer'],seen[1]['X-AJ-File-Transfer']);
});
test('#578 ordinary 18 MiB media retains its HTTPS download metadata without trusting changed hashes',()=>{
 const bytes=18*1048576,download={transport:'https1',id:'a'.repeat(32),seat:'c'.repeat(22),bytes,expires:now+3600,sha256:'f'.repeat(64),chunk_size:65536,mime:'application/pdf'},m={mid:'m'.repeat(22),name:'report.pdf',mime:'application/pdf',kind:'pdf',bytes,sha256:download.sha256,download};
 assert.equal(sanitize({media:[m]}).media[0].download.transport,'https1');assert.equal(sanitize({media:[{...m,sha256:'0'.repeat(64)}]}).media[0].download,undefined);assert.equal(sanitize({media_skip:[{name:'report.pdf',why:'too_big',bytes,download}]}).skip[0].download.transport,'https1');
});

test('#578 429 respects Retry-After and re-signs the same claimed chunk with a fresh nonce',async()=>{
 const f=await fixture(60000),seen=[];let count=0;
 const fetcher=async(url,options)=>{seen.push({at:Date.now(),url,headers:options.headers});if(count++===0)return new Response('',{status:429,headers:{'Retry-After':'1'}});return f.fetcher(url,options);};
 assert.equal(hash(await receiveFile(f.card,f.result,{...f,fetcher,transfer,relay})),f.card.sha256);
 assert.equal(seen.length,2);assert.ok(seen[1].at-seen[0].at>=950);assert.equal(seen[0].url,seen[1].url);assert.equal(seen[0].headers['X-AJ-File-Transfer'],seen[1].headers['X-AJ-File-Transfer']);assert.notEqual(seen[0].headers['X-AJ-File-Nonce'],seen[1].headers['X-AJ-File-Nonce']);
});
test('#578 Retry-After is bounded and a stopped live session cancels the wait promptly',async()=>{
 for(const [raw,ms]of [[null,1000],['0',1000],['1',1000],['60',60000],['9999',60000],['bad',1000]])assert.equal(fileRetryDelay(raw),ms);
 const f=await fixture(60000);let live=true,calls=0;const timer=setTimeout(()=>{live=false;},30),started=Date.now();
 try{await assert.rejects(receiveFile(f.card,f.result,{...f,transfer,relay,live:()=>live,fetcher:async()=>{calls++;return new Response('',{status:429,headers:{'Retry-After':'60'}});}}),e=>e.code==='changed');assert.equal(calls,1);assert.ok(Date.now()-started<1500,'stop is observed during short wait slices');}finally{clearTimeout(timer);}
});
