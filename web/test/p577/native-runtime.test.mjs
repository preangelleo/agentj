import test from 'node:test';import assert from 'node:assert/strict';import{bindNativeRuntime}from './native-runtime.mjs';
test('native actor references require exact unique dictionary paths with no inline owner fallback',()=>{
 const sample=()=>({nodes:{n0:{nodeId:'n0',path:'html>body>button'}}});const audit=()=>({nativeContractBinding:{schemaVersion:1},nativeControlEvidence:{schemaVersion:1,records:[{id:'rule',matches:1,elements:[{path:'html>body>button',scopePath:'html>body>button',bindingIndex:0,owner:'aj-action'}]}],errors:[],conflicts:[]}});
 let s=sample(),a=audit();assert.equal(bindNativeRuntime(s,a).errors.length,0);assert.deepEqual(s.nativeControlEvidence.records[0].elements,[{nodeId:'n0',scopeNodeId:'n0',bindingIndex:0}]);
 a=audit();a.nativeControlEvidence.records[0].elements[0].scopePath='html>body>div';assert.ok(bindNativeRuntime(sample(),a).errors.length);
 a=audit();a.nativeControlEvidence.records[0].elements[0].path='unmatched';assert.ok(bindNativeRuntime(sample(),a).errors.length);
 s=sample();s.nodes.n1={nodeId:'n1',path:s.nodes.n0.path};assert.ok(bindNativeRuntime(s,audit()).errors.length);
});
