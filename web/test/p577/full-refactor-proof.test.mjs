import test from 'node:test';
import assert from 'node:assert/strict';
import {requirePureSource,verifyFullRefactor,validatePureMatrix} from './full-refactor-proof.mjs';
test('missing final pure source cannot be qualified by critical scenes or a reported ok flag',async()=>{
 for(const candidate of [undefined,{ok:true,complete:true},{commit:'a'.repeat(40)},{publicCombined:'b'.repeat(64)}]){
  assert.throws(()=>requirePureSource(candidate),/PENDING/);
  await assert.rejects(verifyFullRefactor({contract:{pureRefactorCandidate:candidate},directory:'/absent'}),/PENDING/);
 }
});
test('exact pure source identity requires both immutable commit and public identity',()=>{
 assert.doesNotThrow(()=>requirePureSource({commit:'a'.repeat(40),publicCombined:'b'.repeat(64)}));
 assert.throws(()=>requirePureSource({commit:'HEAD',publicCombined:'b'.repeat(64)}),/PENDING/);
});

import {expectedMatrixConfigs} from './matrix-contract.mjs';
const manifest={scenes:['chat-idle','settings-ios'],additionalDesktopScenes:['terminal-intro'],touchScenes:['chat-idle','touch-menu']};
function captured(mode){const configs=expectedMatrixConfigs(mode);return{sourceCombined:'c'.repeat(64),sourceStable:true,fixtureStable:true,auditEnabled:true,coverage:{scope:mode,completeMatrix:true,ok:true},configs,rows:configs.flatMap(cfg=>(mode==='touch'?manifest.touchScenes:[...manifest.scenes,...(cfg.width>=768?manifest.additionalDesktopScenes:[])]).map(name=>({...cfg,name,ok:true,ownerAuditOK:true})))};}
test('full matrix cannot satisfy the independent required touch configurations/scenes',()=>{
 const r=captured('full');assert.ok(validatePureMatrix(r,{mode:'touch',side:'after',combined:r.sourceCombined,manifest}).length);
});
test('declared complete and ok cannot hide missing, duplicate, arbitrary N/A or failed ownership rows',()=>{
 const r=captured('touch'),opts={mode:'touch',side:'after',combined:r.sourceCombined,manifest};
 assert.deepEqual(validatePureMatrix(r,opts),[]);
 for(const mutate of [x=>x.rows.pop(),x=>x.rows.push(x.rows[0]),x=>x.configs.pop(),x=>x.rows[0].notApplicable='not sampled',x=>x.rows[0].ownerAuditOK=false,x=>x.fixtureStable=false]){
  const x=structuredClone(r);mutate(x);assert.ok(validatePureMatrix(x,opts).length);
 }
});
