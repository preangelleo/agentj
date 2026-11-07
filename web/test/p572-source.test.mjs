import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {approvalEnvelopeDigest,approvalV2Message,approveMessage} from '../../protocol/wire.js';
test('ordinary v2 approval binds earliest human request, trigger, chain and reason without accepting v1',async()=>{
 const envelope={v:2,channel:'synthetic-channel',request_id:'1'.repeat(32),revision:1,nonce:'synthetic-nonce',details:'rm -- old.csv',source:{root:{text:'保留本月数据'},text:'清理旧文件',chain:['主人','贾维斯','掌枢']},action:'删除后无法撤回'};
 const p={id:envelope.request_id,approval_v:2,envelope,revision:1,envelope_verified:true,envelope_sha256:await approvalEnvelopeDigest(envelope)};
 const key=await webcrypto.subtle.generateKey('Ed25519',false,['sign','verify']),message=await approvalV2Message(envelope.channel,'synthetic-device',p,'allow'),sig=await webcrypto.subtle.sign('Ed25519',key.privateKey,message);
 assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,message),true);
 for(const edit of [e=>e.source.root.text='改写原话',e=>e.source.text='外部邮件',e=>e.source.chain.push('伪造代理'),e=>e.action='改变理由',e=>e.details+=' other.csv',e=>e.nonce+='x',e=>e.revision++]){
  const e=structuredClone(envelope);edit(e);const changed={...p,envelope:e,revision:e.revision,envelope_sha256:await approvalEnvelopeDigest(e)};
  assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,await approvalV2Message(e.channel,'synthetic-device',changed,'allow')),false);
 }
 assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,await approveMessage(envelope.channel,'synthetic-device',p.id,'allow','Bash',envelope.details)),false);
 await assert.rejects(approvalV2Message(envelope.channel,'synthetic-device',{...p,approval_v:undefined},'allow'));
 await assert.rejects(approvalV2Message(envelope.channel,'synthetic-device',{...p,envelope_verified:false},'allow'));
 await assert.rejects(approvalV2Message(envelope.channel,'synthetic-device',p,'allow_batch'));
});
