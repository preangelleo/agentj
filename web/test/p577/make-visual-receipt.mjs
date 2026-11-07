#!/usr/bin/env node
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {readNativeProof,validateNativeProofRows}from './native-receipt-proof.mjs';
import {aggregateNativeCoverage}from './native-coverage.mjs';
import {compareDirectories} from './compare.mjs';
import {validateMatrixConfigs} from './matrix-contract.mjs';
const manifest=JSON.parse(readFileSync(new URL('./scene-manifest.json',import.meta.url)));
const state=name=>/^(?:reply|source-quote|robot)-(hover|focus-visible|active)$/.exec(name)?.[1]||'default';
export const scenarioKey=row=>[row.name,row.width,row.height,row.theme,row.lang,state(row.name)].join('|');
export async function buildVisualReceipt(before,after,out,{nativeWebRoot=fileURLToPath(new URL('../../',import.meta.url))}={}){
 const a=JSON.parse(readFileSync(join(before,'result.json'))),b=JSON.parse(readFileSync(join(after,'result.json'))),problems=[];
 for(const [side,m]of[['before',a],['after',b]]){
  if(m.coverage?.scope!=='full'||m.coverage.completeMatrix!==true)problems.push(side+': requires complete full matrix');
  problems.push(...validateMatrixConfigs(m.configs||[],'full').map(e=>({side,...e})));
  for(const cfg of m.configs||[]){
   const actual=(m.rows||[]).filter(r=>['width','height','theme','lang'].every(k=>r[k]===cfg[k]));
   const required=[...manifest.scenes,...(cfg.width>=768?manifest.additionalDesktopScenes:[])];
   for(const scene of required){const rows=actual.filter(r=>r.name===scene);if(rows.length!==1)problems.push({side,scene,error:'Required scene must occur exactly once'});else if(rows[0].notApplicable&&!(scene==='settings-ios'&&cfg.width>390))problems.push({side,scene,error:'Undeclared N/A scene'});}
   for(const row of actual)if(!required.includes(row.name))problems.push({side,scene:row.name,error:'Unexpected scene'});
  }
  if(!/^[a-f0-9]{64}$/.test(m.sourceCombined||''))problems.push(side+': missing source hash');
 }
 let nativeProof=null;try{nativeProof=await readNativeProof(nativeWebRoot)}catch(error){problems.push('Independent native policy/compiled bytes unavailable: '+error.message)}
 const paintedRows=(b.rows||[]).filter(r=>r.ok&&!r.notApplicable),nativeProofResult=validateNativeProofRows(paintedRows,b.fixtureBinding,b.sourceCombined,nativeProof);
 problems.push(...nativeProofResult.errors);
 const nativeControlCoverage=aggregateNativeCoverage(paintedRows,nativeProofResult.expectedIds);
 if(!b.auditEnabled||!nativeProofResult.ok||!nativeControlCoverage.qualified)problems.push('After native-control evidence must cover all source-bound exact contracts without conflicts');
 if(a.auditEnabled!==b.auditEnabled)problems.push('Full matrix audit inputs differ');
 const keys=m=>m.rows.filter(r=>r.ok&&!r.notApplicable).map(scenarioKey).sort();
 const scenarios=keys(a);if(JSON.stringify(scenarios)!==JSON.stringify(keys(b)))problems.push('Scenario/state sets differ');
 if(new Set(scenarios).size!==scenarios.length)problems.push('Duplicate scenario/state key');
 const pixels=compareDirectories(before,after,join(out,'diff'),{requireManifests:true});
 if(pixels.rows.length!==scenarios.length)problems.push('Actual PNG count differs from required painted scenes');
 const changedPixels=pixels.rows.reduce((n,r)=>n+(r.changedPixels||0),0);
 return{schemaVersion:1,nativeControlCoverage,complete:problems.length===0&&pixels.ok&&changedPixels===0,changedPixels,beforeCombined:a.sourceCombined,afterCombined:b.sourceCombined,coverage:{scope:'full',completeMatrix:a.coverage?.completeMatrix===true&&b.coverage?.completeMatrix===true&&problems.length===0},configs:a.configs,scenarios,images:pixels.rows.length,problems:[...problems,...pixels.errors],rawPixelResult:pixels,note:a.versionInput?.mode==='same-version-combined-input'?'Explicit build-identity comparison input is recorded; actual production source identities remain independently bound. No mask, tolerance or PNG byte-only comparison.':'Real build hash remains visible. No mask, text replacement, tolerance or PNG byte-only comparison.'};
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
 const{values:o}=parseArgs({options:{before:{type:'string'},after:{type:'string'},out:{type:'string'},coverage:{type:'string'},'native-web-root':{type:'string'}}});
 if(!o.before||!o.after||!o.out)throw Error('make-visual-receipt.mjs --before <full-before> --after <full-after> --out <dir> [--coverage <file>]');
 const out=resolve(o.out);mkdirSync(out,{recursive:true});const r=await buildVisualReceipt(resolve(o.before),resolve(o.after),out,{nativeWebRoot:o['native-web-root']});writeFileSync(join(out,'visual-receipt.json'),JSON.stringify(r,null,2));
 if(o.coverage)writeFileSync(resolve(o.coverage),JSON.stringify({schemaVersion:1,scope:'full',completeMatrix:r.coverage.completeMatrix,scenarios:r.scenarios,unusedRules:[]},null,2));
 console.log(JSON.stringify({complete:r.complete,images:r.images,changedPixels:r.changedPixels,problems:r.problems.length}));if(!r.complete)process.exitCode=1;
}
