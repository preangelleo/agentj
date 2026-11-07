import test from 'node:test';import assert from 'node:assert/strict';
import{mkdtempSync,mkdirSync,writeFileSync,rmSync}from'node:fs';import{tmpdir}from'node:os';import{join}from'node:path';
import{buildVisualReceipt}from'./make-visual-receipt.mjs';import{encodeRGBA}from'./compare.mjs';import{expectedMatrixConfigs}from'./matrix-contract.mjs';
test('P577 fake full receipt with 16 configs and zero pixels still fails when named scenes are missing',async()=>{
 const root=mkdtempSync(join(tmpdir(),'p577-receipt-'));try{
  const a=join(root,'a'),b=join(root,'b');mkdirSync(a);mkdirSync(b);
  const configs=expectedMatrixConfigs('full'),rows=configs.map(c=>({...c,name:'login',ok:true}));
  const metadata={sourceCombined:'a'.repeat(64),sourceStable:true,fixtureStable:true,fixtureBinding:{public:'fixture'},browser:'synthetic test',captureVersion:'p577-v2',environment:{},auditEnabled:true,configs,rows,coverage:{scope:'full',completeMatrix:true,ok:true}};
  for(const dir of[a,b]){writeFileSync(join(dir,'result.json'),JSON.stringify(metadata));for(const c of configs)writeFileSync(join(dir,`${c.width}-${c.theme}-${c.lang}-login.png`),encodeRGBA(1,1,Buffer.from([1,2,3,255])));}
  const receipt=await buildVisualReceipt(a,b,join(root,'result'));assert.equal(receipt.changedPixels,0);assert.equal(receipt.images,16);assert.equal(receipt.complete,false);assert.ok(receipt.problems.some(p=>p.error==='Required scene must occur exactly once'));assert.equal(receipt.nativeControlCoverage.qualified,false);assert.ok(receipt.problems.some(p=>typeof p==='string'&&p.includes('source-bound exact')));
 }finally{rmSync(root,{recursive:true,force:true})}
});
