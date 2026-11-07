import test from 'node:test';import assert from 'node:assert/strict';
import{expectedMatrixConfigs,validateMatrixConfigs}from'./matrix-contract.mjs';
test('P577 complete matrix cannot be replaced by a subset or duplicated config count',()=>{
 const full=expectedMatrixConfigs('full');assert.equal(full.length,16);assert.deepEqual(validateMatrixConfigs(full,'full'),[]);
 assert.ok(validateMatrixConfigs(full.slice(0,15),'full').length);
 assert.ok(validateMatrixConfigs([...full.slice(0,15),full[0]],'full').length);
 assert.ok(validateMatrixConfigs(full.map((c,i)=>i?c:{...c,height:844}),'full').length,'360 must be 800px, not smoke default 844');
});
test('P577 touch matrix is real 390 x 844 light/dark x zh/en coverage, independently complete',()=>{
 const touch=expectedMatrixConfigs('touch');assert.equal(touch.length,4);assert.deepEqual(validateMatrixConfigs(touch,'touch'),[]);
 assert.ok(validateMatrixConfigs(touch.slice(0,1),'touch').length);assert.ok(validateMatrixConfigs(expectedMatrixConfigs('full'),'touch').length);
});
