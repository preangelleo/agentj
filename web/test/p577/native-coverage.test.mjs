import test from 'node:test';import assert from 'node:assert/strict';import {aggregateNativeCoverage}from './native-coverage.mjs';
test('native contracts cannot qualify from ordinary owner ok or empty/unobserved evidence',()=>{
 const row=(name,a,b)=>({name,nativeControlEvidence:{records:[{id:'a',matches:a},{id:'b',matches:b}],conflicts:[],errors:[]}});
 assert.equal(aggregateNativeCoverage([{name:'empty',productOwnerAudit:{ok:true}}],['a','b']).qualified,false);
 assert.equal(aggregateNativeCoverage([row('a',1,0)],['a','b']).qualified,false);
 const union=aggregateNativeCoverage([row('a',1,0),row('b',0,1)],['a','b']);assert.equal(union.qualified,true);assert.equal(union.observed,2);
 assert.equal(aggregateNativeCoverage([row('a',1,1)],['a','b','c']).qualified,false);
});
