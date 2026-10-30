import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash, webcrypto} from 'node:crypto';
import {approveMessage,keyOpMessage} from '../../protocol/wire.js';
const hex=s=>createHash('sha256').update(s).digest('hex');
const dec=b=>new TextDecoder().decode(b);
test('task plan is signed on allow and deny; altered concrete actions invalidate its bytes',async()=>{
  const scope=JSON.stringify({task:'turn-1',actions:[{tool:'Bash',input_sha256:'a'.repeat(64),class:'money'}]});
  for(const decision of ['allow','deny']){
    const base=dec(await approveMessage('channel','device','id',decision,'Bash','concrete plan'));
    const signed=await approveMessage('channel','device','id',decision,'Bash','concrete plan',undefined,scope);
    assert.equal(dec(signed),base+'\ntask:'+hex(scope));
    const key=await webcrypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);
    const sig=await webcrypto.subtle.sign('Ed25519',key.privateKey,signed);
    const altered=await approveMessage('channel','device','id',decision,'Bash','concrete plan',undefined,scope.replace('turn-1','turn-2'));
    assert.equal(await webcrypto.subtle.verify('Ed25519',key.publicKey,sig,altered),false);
  }
  await assert.rejects(approveMessage('c','d','i','allow_batch','tool','summary','batch',scope));
});
test('key-management signature matches the host canonical target; changed destination, nonce or source changes bytes',async()=>{
 const target={id:'page',op:'import',name:'TEST_NAME',n:'1'.repeat(32),src:'s1234567890123456',keep:true,fp:'fixture'};
 const json=JSON.stringify(Object.fromEntries(Object.entries(target).sort(([a],[b])=>a.localeCompare(b))));
 const bytes=dec(await keyOpMessage('c','d',target.n,123,target));
 assert.equal(bytes,'agentjarvis-control-v1\nc\nd\nkeys_op\n'+target.n+'\n123\n'+hex(json));
 for(const patch of [{name:'OTHER_NAME'},{src:'other'},{keep:false}]) assert.notEqual(dec(await keyOpMessage('c','d',target.n,123,{...target,...patch})),bytes);
});
