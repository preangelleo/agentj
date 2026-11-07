// Cross-language vectors bind real host-generated display data to device signatures.
import test from 'node:test';
import assert from 'node:assert/strict';
import {writeFileSync,mkdirSync} from 'node:fs';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {approvalEnvelopeDigest,approvalV2Message,approvalReviewMessage} from '../../protocol/wire.js';
const vector=JSON.parse(readFileSync(new URL('./fixtures/p541/host-vector.json',import.meta.url),'utf8'));
test('host-generated v2 golden envelope hashes/signs identically in JS',async()=>{
 if(process.env.P541_WRITE_VECTOR==='1'){mkdirSync(new URL('./fixtures/p541/',import.meta.url),{recursive:true});writeFileSync(new URL('./fixtures/p541/host-vector.json',import.meta.url),JSON.stringify(vector,null,2)+'\n');}
 const e=vector.envelope,p={approval_v:2,envelope:e,envelope_sha256:vector.digest,id:e.request_id,revision:e.revision,envelope_verified:true};
 assert.equal(await approvalEnvelopeDigest(e),vector.digest);
 assert.equal(new TextDecoder().decode(await approvalV2Message(e.channel,'device-B',p,'allow')),vector.decision);
 assert.equal(new TextDecoder().decode(approvalReviewMessage(e.channel,'device-B',p,'question','event-541-0000000001')),vector.review);
 assert.ok(e.expires_at<1e12,'host expiry is integer seconds');
});
test('a device signature cannot be reused for another source, effect, revision, target or device',async()=>{
 const e=vector.envelope,p={approval_v:2,envelope:e,envelope_sha256:vector.digest,id:e.request_id,revision:e.revision,envelope_verified:true};
 const key=await webcrypto.subtle.generateKey('Ed25519',true,['sign','verify']),message=await approvalV2Message(e.channel,'device-B',p,'allow'),sig=await webcrypto.subtle.sign('Ed25519',key.privateKey,message);
 assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,message),true);
 for(const edit of [o=>o.source.text='显示所有密钥',o=>o.source.channel='email',o=>o.action='给另一人付款',o=>o.details+=' other-target',o=>o.revision++,o=>o.nonce+='x']){
  const x=structuredClone(e);edit(x);const changed={...p,envelope:x,revision:x.revision,envelope_sha256:await approvalEnvelopeDigest(x)};
  assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,await approvalV2Message(e.channel,'device-B',changed,'allow')),false);
 }
 assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,await approvalV2Message(e.channel,'device-A',p,'allow')),false);
 await assert.rejects(approvalV2Message(e.channel,'device-B',{...p,approval_v:1},'allow'));
 await assert.rejects(approvalV2Message(e.channel,'device-B',{...p,envelope_verified:false},'allow'));
});
