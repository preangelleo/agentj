import test from'node:test';import assert from'node:assert/strict';import{nativeProofFromBytes,validateNativeProofRows}from'./native-receipt-proof.mjs';
test('self-reported consistently shortened native binding and records fail against real policy bytes even with unchanged policy SHA',()=>{
 const proof=nativeProofFromBytes(Buffer.from('{"entries":[{"id":"a"},{"id":"b"}]}'),Buffer.from('{}'),[{id:'a'},{id:'b'}]),source='a'.repeat(64),fixture={'web/native-control-contracts.json':proof.policySha256,'web/component-ownership.json':proof.semanticSha256};
 const row={name:'scene',nativeContractBinding:{...proof,schemaVersion:1,sourceCombined:source},nativeControlEvidence:{schemaVersion:1,records:[{id:'a'},{id:'b'}]}};assert.equal(validateNativeProofRows([row],fixture,source,proof).ok,true);
 const shrunk=structuredClone(row);shrunk.nativeContractBinding.contractIds=['a'];shrunk.nativeControlEvidence.records=[{id:'a'}];assert.equal(shrunk.nativeContractBinding.policySha256,proof.policySha256);assert.equal(validateNativeProofRows([shrunk],fixture,source,proof).ok,false);
});
