import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as snap from '../public/js/snap.js';
test('P76 shared Claude uses exact context percentage and source age across reconnect',()=>{
 const now=Date.now;
 snap.configure({onChange(){},onTurn(){}});
 try{
  Date.now=()=>2000000;
  snap.setMeter({model:'claude-opus-4-6',model_name:'Opus 4.6',effort:'high',ctx:{pct:34.5,max:200000},week:{pct:60},h5:{pct:25},source_at:1000});
  let u=snap.snapshot().usage;assert.equal(u.context_pct,35);assert.equal(u.week_pct,60);assert.equal(u.five_hour_pct,25);assert.equal(u.model,'Opus 4.6');assert.equal(u.age_s,1000);
  Date.now=()=>2100000;snap.setMeter({source_at:1000});
  assert.equal(snap.snapshot().usage.age_s,1100);
  snap.setMeter({ctx:{used:50000,max:200000}});assert.equal(snap.snapshot().usage.context_pct,25);assert.equal(snap.snapshot().usage.age_s,0);
  snap.setMeter({source_at:Infinity});assert.equal(snap.snapshot().usage.age_s,0);
 }finally{Date.now=now;}
});

import {controlMessage,controlObject} from '../public/proto/wire.js';
import {readFileSync} from 'node:fs';
test('P76 update signature binds channel device nonce and latest target; menu authorizes explicitly',async()=>{
 assert.equal(controlObject('update',{}),'latest');
 const key=await crypto.subtle.generateKey({name:'Ed25519'},false,['sign','verify']);
 const a=await controlMessage('host','phone','update','ab'.repeat(16),123,{});
 const sig=await crypto.subtle.sign('Ed25519',key.privateKey,a);
 assert.equal(await crypto.subtle.verify('Ed25519',key.publicKey,sig,a),true);
 const b=await controlMessage('other-host','phone','update','ab'.repeat(16),123,{});
 assert.equal(await crypto.subtle.verify('Ed25519',key.publicKey,sig,b),false);
 const ui=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
 assert.ok(ui.includes("t('r.update.title')"));assert.ok(ui.includes('b.disabled = !!same'));
});
