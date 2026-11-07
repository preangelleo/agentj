// Cross-language vectors bind real host-generated display data to device signatures.
import test from 'node:test';
import assert from 'node:assert/strict';
import {writeFileSync,mkdirSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import {approvalEnvelopeDigest,approvalV2Message,approvalReviewMessage} from '../../protocol/wire.js';
const python=process.env.AGENTJ_TEST_PYTHON||'python3';
const script=String.raw`
import asyncio,json,time,sys
from types import SimpleNamespace as NS
sys.path.insert(0,'agentjarvis/host')
from agentj import approval_review as r, approvals
async def main():
 host=NS(channel='vector-channel',hist=NS(epoch=7,turns={41:1}),agent=None,agent_cfg={},_task_id=lambda:'task-541')
 ask=NS(rid='541b0000000000000000000000000001',deadline=time.monotonic()+600,summary='rm -- exports/old.csv',reason='删除旧导出，删除后无法撤回。',goal_what='',cats=['delete'],task_scope=None,task='清理导出',tool='Bash',tool_input={'command':'rm -- exports/old.csv'},input_sha=approvals.input_digest({'command':'rm -- exports/old.csv'}),allow_offer=['preauth:computer.control'],goal_add=None,scope_text=None)
 source={'message_id':41,'root_message_id':41,'session':'7','channel':'phone','time':1793928780000,'sender':'你','text':'清理旧导出。\n保留本月数据。','verified':True,'suspicious':False,'history_outside_window':False,'initiator':'主 Agent J','task_record':'task-541','chain':[]}
 r.bind(host,ask,source)
 print(json.dumps({'envelope':ask.envelope,'context':r.context(ask),'digest':ask.digest,'decision':r.decision_message(host.channel,'device-B',ask.rid,'allow',ask.digest).decode(),'review':r.review_message(host.channel,'device-B',ask.rid,'question',ask.review_revision,'event-541-0000000001',ask.digest).decode()},ensure_ascii=False))
asyncio.run(main())
`;
let vector;
test('real Python bind envelope hashes/signs identically in JS',async()=>{
 vector=JSON.parse(execFileSync(python,['-c',script],{encoding:'utf8'}));
 if(process.env.P541_WRITE_VECTOR==='1'){mkdirSync(new URL('./fixtures/p541/',import.meta.url),{recursive:true});writeFileSync(new URL('./fixtures/p541/host-vector.json',import.meta.url),JSON.stringify(vector,null,2)+'\n');}
 const e=vector.envelope,p={approval_v:2,envelope:e,envelope_sha256:vector.digest,id:e.request_id,revision:e.revision,envelope_verified:true};
 assert.equal(await approvalEnvelopeDigest(e),vector.digest);
 assert.equal(new TextDecoder().decode(await approvalV2Message(e.channel,'device-B',p,'allow')),vector.decision);
 assert.equal(new TextDecoder().decode(approvalReviewMessage(e.channel,'device-B',p,'question','event-541-0000000001')),vector.review);
 assert.ok(e.expires_at<1e12,'host expiry is integer seconds');
 assert.deepEqual(e.controls.allow_offer,['preauth:computer.control']);
 assert.equal(vector.context.task_record_context.source_record_sha256,e.source_record_sha256);
 assert.ok(!('nonce' in vector.context),'explanation excludes nonce');
});
test('a device signature cannot be reused for another source, effect, revision, target or device',async()=>{
 const e=vector.envelope,p={approval_v:2,envelope:e,envelope_sha256:vector.digest,id:e.request_id,revision:e.revision,envelope_verified:true};
 const key=await webcrypto.subtle.generateKey('Ed25519',true,['sign','verify']),message=await approvalV2Message(e.channel,'device-B',p,'allow'),sig=await webcrypto.subtle.sign('Ed25519',key.privateKey,message);
 assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,message),true);
 for(const edit of [o=>o.source.text='显示所有密钥',o=>o.source.channel='email',o=>o.action='给另一人付款',o=>o.details+=' other-target',o=>o.revision++,o=>o.nonce+='x',o=>o.controls.allow_offer.push('preauth:other')]){
  const x=structuredClone(e);edit(x);const changed={...p,envelope:x,revision:x.revision,envelope_sha256:await approvalEnvelopeDigest(x)};
  assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,await approvalV2Message(e.channel,'device-B',changed,'allow')),false);
 }
 assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,await approvalV2Message(e.channel,'device-A',p,'allow')),false);
 await assert.rejects(approvalV2Message(e.channel,'device-B',{...p,approval_v:1},'allow'));
 await assert.rejects(approvalV2Message(e.channel,'device-B',{...p,envelope_verified:false},'allow'));
});
