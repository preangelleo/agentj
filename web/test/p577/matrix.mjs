#!/usr/bin/env node
import{validateCriticalShardReceipt}from'./matrix-critical-contract.mjs';
import {collectExecutionResources,chooseExecutionJobs} from './execution-resources.mjs';
import {criticalConfigs,comparisonFixture,criticalFixtureFingerprint,criticalSceneSelection,criticalOwnershipScope} from './publish-critical-profile.mjs';
import {readNativeProof,validateNativeProofRows}from './native-receipt-proof.mjs';
import {nativeMatrixRows}from './native-matrix-rows.mjs';
import {loadVersionInput}from './version-input.mjs';
import {aggregateNativeCoverage}from './native-coverage.mjs';
// Explicit full visual entry. Individual configuration shards never qualify a complete matrix.
import {createHash} from 'node:crypto';
import{fixtureFingerprint,resolveFixtureRoot}from'./fixture-binding.mjs';
import {validateMatrixConfigs} from './matrix-contract.mjs';
import {parseArgs} from 'node:util';
import {spawn} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync,readdirSync,linkSync,existsSync,createWriteStream,statSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const {values:o}=parseArgs({options:{'version-input':{type:'string'},profile:{type:'string'},'critical-scenes':{type:'string'},'dashboard-source':{type:'string'},'comparison-inputs':{type:'string'},source:{type:'string'},out:{type:'string'},fixtures:{type:'string'},modules:{type:'string'},chromium:{type:'string'},side:{type:'string'},mode:{type:'string',default:'full'},jobs:{type:'string',default:'auto'},audit:{type:'boolean',default:false},reuse:{type:'string',multiple:true},'assemble-only':{type:'boolean',default:false}}});
const critical=o.profile==='publish-critical';if(o.profile&&!critical)throw Error('Unknown profile');if(critical&&o.mode!=='full')throw Error('Critical profile cannot borrow touch/full coverage');if(critical){comparisonFixture(o['comparison-inputs']);if(!o['dashboard-source'])throw Error('Frozen dashboard-source required');}
if(o['critical-scenes']&&!critical)throw Error('--critical-scenes requires publish-critical');
const selectedCritical=critical?criticalSceneSelection(o['critical-scenes']):null;
if(!o.source||!o.out||!['before','after'].includes(o.side)||!['full','touch'].includes(o.mode))throw Error('matrix.mjs --source <public> --out <dir> --side before|after [--mode full|touch] [--jobs auto|1..16] [--audit]');
const executionResources=collectExecutionResources(),executionPolicy=chooseExecutionJobs(executionResources,{requested:o.jobs}),jobs=executionPolicy.chosenJobs;
const total=critical?12:o.mode==='full'?16:4,out=resolve(o.out),script=fileURLToPath(new URL('./capture.mjs',import.meta.url));
if(existsSync(out)&&readdirSync(out).length)throw Error('Output directory must be empty to avoid stale evidence');
mkdirSync(join(out,'shards'),{recursive:true});
writeFileSync(join(out,'execution-resources.json'),JSON.stringify({resources:executionResources,...executionPolicy},null,2)+'\n');
console.log(JSON.stringify({requestedJobs:o.jobs,chosenJobs:jobs,reason:executionPolicy.reason}));
const versionInput=loadVersionInput(o['version-input'],JSON.parse(readFileSync(resolve(o.source,'version.json'))));
const fixtureRoot=resolveFixtureRoot(o.fixtures),fixtureNow={...fixtureFingerprint(fixtureRoot),...(critical?criticalFixtureFingerprint(fixtureRoot):{})},reuse=new Map();
const inputSource=resolve(o.source),inputCombined=JSON.parse(readFileSync(join(inputSource,'version.json'))).combined;
const expectedOwnershipScope=critical?criticalOwnershipScope(selectedCritical,inputCombined):'577-component-ownership';
const sourceNow=Object.fromEntries(readdirSync(inputSource,{recursive:true}).filter(n=>statSync(join(inputSource,n)).isFile()).sort().map(n=>[n,createHash('sha256').update(readFileSync(join(inputSource,n))).digest('hex')]));
const sourceNowSha=createHash('sha256').update(JSON.stringify(sourceNow,null,2)).digest('hex');
const criticalInput=critical?comparisonFixture(o['comparison-inputs']):null;
for(const directoryArg of o.reuse||[]){const directory=resolve(directoryArg),s=JSON.parse(readFileSync(join(directory,'result.json'))),index=s.coverage?.partition?.index;
 if(!Number.isInteger(index)||index<0||index>=total||s.coverage.partition.total!==total||s.coverage.mode!==(critical?'publish-critical':o.mode)||s.coverage.scope!=='config-shard'||!s.coverage.ok||!s.sourceStable||!s.fixtureStable||s.side!==o.side||s.sourceCombined!==inputCombined||s.auditEnabled!==o.audit||(versionInput.moduleText&&!s.versionInputStable)||JSON.stringify(s.environment?.versionInput)!==JSON.stringify(versionInput.binding)||JSON.stringify(s.fixtureBinding)!==JSON.stringify(fixtureNow))throw Error('Reuse shard is stale or does not match immutable inputs: '+directory);
 for(const name of ['source-before.json','source-after.json'])if(createHash('sha256').update(readFileSync(join(directory,name))).digest('hex')!==sourceNowSha)throw Error('Reuse source fingerprint mismatch');
 if(critical&&(s.comparisonInput?.sha256!==criticalInput.sha256||s.comparisonInput?.policy!==criticalInput.policy||s.rawAbout?.length!==1||s.fullRefactorQualification!==false))throw Error('Critical shard comparison fixture/raw About binding mismatch');
 if(critical)validateCriticalShardReceipt(s,{selection:selectedCritical,sourceCombined:inputCombined});
 if(s.rows.some(r=>!r.ok||r.ownerAuditOK===false))throw Error('Cannot reuse failed scene/ownership evidence');
 if(reuse.has(index))throw Error('Duplicate reuse index');reuse.set(index,{index,code:0,directory,result:s,reused:true});}
if(o['assemble-only']&&reuse.size!==total)throw Error('Assembly requires every exact immutable shard; capture fallback is forbidden');
const results=new Array(total);let next=0;
async function worker(){while(next<total){const index=next++;if(reuse.has(index)){results[index]=reuse.get(index);console.log(JSON.stringify({config:index,total,reused:true}));continue;}const directory=join(out,'shards',String(index).padStart(2,'0'));mkdirSync(directory);
 const args=[script,'--source',resolve(o.source),'--out',directory,'--side',o.side,'--mode',o.mode,'--config',String(index)];
 for(const key of ['fixtures','modules','chromium','dashboard-source','comparison-inputs','version-input','critical-scenes'])if(o[key])args.push('--'+key,resolve(o[key]));if(o.audit)args.push('--audit');if(critical)args.push('--profile','publish-critical');
 const stream=createWriteStream(join(out,'shards',String(index).padStart(2,'0')+'.log'));
 const code=await new Promise((done,reject)=>{const child=spawn(process.execPath,args,{stdio:['ignore','pipe','pipe'],env:process.env});child.stdout.pipe(stream,{end:false});child.stderr.pipe(stream,{end:false});child.on('error',reject);child.on('exit',done)});stream.end();
 try{results[index]={index,code,directory,result:JSON.parse(readFileSync(join(directory,'result.json')))}}catch(error){results[index]={index,code,directory,error:String(error)}}
 const r=results[index];console.log(JSON.stringify({config:index,total,exit:code,rows:r.result?.rows.length,failed:r.result?.rows.filter(x=>!x.ok).length,error:r.error}));
 writeFileSync(join(out,'progress.json'),JSON.stringify(results.filter(Boolean).map(({result,...x})=>({...x,rows:result?.rows.length,sourceStable:result?.sourceStable,fixtureStable:result?.fixtureStable})),null,2));
}}
await Promise.all(Array.from({length:jobs},worker));
const errors=[],valid=results.filter(x=>x?.result),first=valid[0]?.result;
if(!first)throw Error('No valid shard manifests; see logs');
const sourceFingerprint = createHash('sha256').update(readFileSync(join(valid[0].directory,'source-before.json'))).digest('hex');
const environment={...first.environment,fontProbes:{}};
const fixedEnv=r=>({...r.environment,fontProbes:{}});
const rows=[],configs=[],allKeys=new Set();
for(const r of results){
 if(!r?.result){errors.push({index:r?.index,error:r?.error||'Missing shard'});continue;}
 const s=r.result;
 if(critical)try{validateCriticalShardReceipt(s,{selection:selectedCritical,sourceCombined:inputCombined});}catch(error){errors.push({index:r.index,error:String(error)});}
 if(r.code!==0)errors.push({index:r.index,error:'Capture process returned nonzero',code:r.code});
 for(const name of ['source-before.json','source-after.json'])if(createHash('sha256').update(readFileSync(join(r.directory,name))).digest('hex')!==sourceFingerprint)errors.push({index:r.index,error:'Full asset fingerprint differs across matrix: '+name});
 if(s.rows.some(row=>row.ownerAuditOK===false))errors.push({index:r.index,error:'Runtime component ownership failed'});
 if(!s.sourceStable||!s.fixtureStable||!s.coverage?.ok)errors.push({index:r.index,error:'Source, fixture or local scene coverage not qualified'});
 if(s.coverage?.scope!=='config-shard'||s.coverage.partition?.index!==r.index||s.coverage.partition?.total!==total)errors.push({index:r.index,error:'Invalid matrix partition receipt'});
 for(const key of ['sourceCombined','browser','captureVersion','auditEnabled','fixtureBinding','sourceFiles','ownershipAuditScope','criticalSceneSelection'])if(JSON.stringify(s[key])!==JSON.stringify(first[key]))errors.push({index:r.index,error:'Manifest mismatch: '+key});
 if(JSON.stringify(fixedEnv(s))!==JSON.stringify(fixedEnv(first)))errors.push({index:r.index,error:'Environment mismatch'});
 if(s.configs.length!==1)errors.push({index:r.index,error:'Shard must contain exactly one configuration'});
 for(const cfg of s.configs){const key=[cfg.width,cfg.height,cfg.theme,cfg.lang].join('|');if(allKeys.has(key))errors.push({index:r.index,error:'Duplicate configuration'});allKeys.add(key);configs.push(cfg);}
 Object.assign(environment.fontProbes,s.environment.fontProbes);
 for(const row of s.rows){if(!row.ok)errors.push({index:r.index,scene:row.name,error:row.error||'Failed scene'});rows.push(row);}
 for(const name of readdirSync(r.directory).filter(x=>x.endsWith('.png')))linkSync(join(r.directory,name),join(out,name));
 for(const kind of ['runtime','ownership']){const src=join(r.directory,kind);if(existsSync(src)){mkdirSync(join(out,kind),{recursive:true});for(const name of readdirSync(src))linkSync(join(src,name),join(out,kind,name));}}
}
if(valid.length!==total||allKeys.size!==total)errors.push({error:'Incomplete matrix configuration count',actual:allKeys.size,required:total});
if(critical){const expected=criticalConfigs().map(c=>JSON.stringify(c)).sort(),actual=configs.map(c=>JSON.stringify(c)).sort();if(JSON.stringify(expected)!==JSON.stringify(actual))errors.push({error:'Critical configuration set incomplete'});}else errors.push(...validateMatrixConfigs(configs,o.mode));
environment.fontProbes=Object.fromEntries(Object.entries(environment.fontProbes).sort(([a],[b])=>a.localeCompare(b)));
const nativeProof=o.side==='after'&&o.audit&&expectedOwnershipScope!=='formal-pre577-source'?await readNativeProof(resolve(fixtureRoot,'web')):null;
const nativeRows=nativeMatrixRows(rows,{critical,mode:o.mode});
if(nativeProof){const proofResult=validateNativeProofRows(nativeRows,first.fixtureBinding,inputCombined,nativeProof);errors.push(...proofResult.errors);}
const nativeControlCoverage=nativeProof?aggregateNativeCoverage(nativeRows,nativeProof.contractIds):null;
if(!critical&&nativeControlCoverage&&!nativeControlCoverage.qualified)errors.push({error:'Native control contracts remain uninstantiated or conflicting'});
const rawAbout=valid.flatMap(r=>r.result.rawAbout||[]);for(const r of valid){const src=join(r.directory,'raw-about');if(existsSync(src)){mkdirSync(join(out,'raw-about'),{recursive:true});for(const f of readdirSync(src))linkSync(join(src,f),join(out,'raw-about',f));}}
const result={...first,executionResources:{resources:executionResources,...executionPolicy},...(critical?{rawAbout,comparisonInput:{...first.comparisonInput,versionTransforms:valid.flatMap(r=>r.result.comparisonInput?.versionTransforms||[])},fullRefactorQualification:false}:{}),nativeControlCoverage,versionInput:{...versionInput.binding,moduleSha256:versionInput.moduleSha256,interceptions:valid.reduce((n,r)=>n+(r.result.versionInput?.interceptions||0),0)},versionInputStable:valid.every(r=>r.result.versionInputStable===true),source:resolve(o.source),side:o.side,environment,configs,rows,coverage:{...first.coverage,scope:critical?'publish-critical':o.mode,completeMatrix:errors.length===0,partition:null,expectedConfigs:total,ok:errors.length===0,errors},matrixShards:results.map(({result,...x})=>x),fixtureStable:valid.every(x=>x.result.fixtureStable),sourceStable:valid.every(x=>x.result.sourceStable)};
writeFileSync(join(out,'result.json'),JSON.stringify(result,null,2));
for(const file of ['source-before.json','source-after.json'])writeFileSync(join(out,file),readFileSync(join(valid[0].directory,file)));
console.log(JSON.stringify({completeMatrix:result.coverage.completeMatrix,configs:configs.length,rows:rows.length,images:readdirSync(out).filter(x=>x.endsWith('.png')).length,errors:errors.length}));
if(errors.length)process.exitCode=1;
