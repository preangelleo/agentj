import test from 'node:test';
import assert from 'node:assert/strict';
import {createRecovery} from '../public/js/recovery574.js';
test('recovery duration survives quiet restarts, short drops stay silent, evidence is bounded', () => {
 let now = Date.UTC(2026, 10, 7, 12); const r = createRecovery({now:()=>now});
 r.drop();now+=1000;assert.equal(r.up(),null);
 r.drop();now+=6000;r.drop();now+=4000;
 assert.deepEqual(r.up({age_ms:100,ms:4500}),{seconds:10,stallSeconds:5,frequent:false});
 r.drop();now+=6000;assert.equal(r.up({age_ms:999999,ms:5000}).stallSeconds,null);
 r.drop();now+=6000;assert.equal(r.up().frequent,true);
 now+=86400000;r.drop();now+=6000;assert.equal(r.up().frequent,false);
 r.reset();assert.equal(r.up(),null);
});
