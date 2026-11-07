import{readFile}from'node:fs/promises';import{resolve}from'node:path';import{pathToFileURL}from'node:url';import{createHash}from'node:crypto';
const sha=x=>createHash('sha256').update(x).digest('hex');const idsEqual=(a,b)=>Array.isArray(a)&&a.length===b.length&&new Set(a).size===a.length&&JSON.stringify([...a].sort())===JSON.stringify([...b].sort());
export function nativeProofFromBytes(policyBytes,semanticBytes,compiled){
 const policy=JSON.parse(policyBytes),ids=policy.entries?.map(x=>x.id);if(!ids?.length||ids.some(id=>typeof id!=='string'||!id)||new Set(ids).size!==ids.length||!idsEqual(compiled.map(x=>x.id),ids))throw Error('Real native policy and compiled contract ID sets differ');
 return{contractIds:ids.sort(),policySha256:sha(policyBytes),semanticSha256:sha(semanticBytes),compiledSha256:sha(JSON.stringify(compiled))};
}
export async function readNativeProof(webRoot){const root=resolve(webRoot),[policy,semantic]=await Promise.all([readFile(resolve(root,'native-control-contracts.json')),readFile(resolve(root,'component-ownership.json'))]);const{loadNativeControlContracts}=await import(pathToFileURL(resolve(root,'tools/native-control-contract.mjs')).href);const proof=nativeProofFromBytes(policy,semantic,await loadNativeControlContracts(root));const{nativeImpactContext}=await import(pathToFileURL(resolve(root,'tools/native-impact.mjs')).href);const nativeContext=await nativeImpactContext(root);if(['policySha256','semanticSha256','compiledSha256'].some(k=>nativeContext.binding[k]!==proof[k]))throw Error('Native source changed during independent proof construction');return{...proof,webRoot:root,nativeContext};}
// Source identity / exact ID census only. Actual actors require the core nativeImpactVerifier.
export function validateNativeProofRows(rows,fixtureBinding,sourceCombined,proof){
 const errors=[];if(!proof?.contractIds?.length)return{ok:false,errors:['Independent native policy/compiled bytes absent'],expectedIds:[]};
 if(fixtureBinding?.['web/native-control-contracts.json']!==proof.policySha256||fixtureBinding?.['web/component-ownership.json']!==proof.semanticSha256)errors.push('Actual native policy/semantic bytes do not match captured fixture SHA');
 for(const row of rows){const b=row.nativeContractBinding,e=row.nativeControlEvidence;
  if(!b||b.schemaVersion!==1||b.sourceCombined!==sourceCombined||!['policySha256','semanticSha256','compiledSha256'].every(k=>b[k]===proof[k])||!idsEqual(b.contractIds,proof.contractIds))errors.push({scene:row.name,error:'Runtime native binding differs from complete independently compiled policy'});
  if(!e||e.schemaVersion!==1||!idsEqual(e.records?.map(r=>r.id),proof.contractIds))errors.push({scene:row.name,error:'Runtime native records missing, duplicated or truncated against actual policy'});
 }
 return{ok:!errors.length,errors,expectedIds:proof.contractIds};
}
