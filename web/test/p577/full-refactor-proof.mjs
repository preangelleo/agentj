// Admission for the independent pure-refactor matrix. Critical publication scenes cannot satisfy this contract.
import {readFileSync,existsSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {gunzipSync} from 'node:zlib';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {compareDirectories} from './compare.mjs';
import {validateMatrixConfigs,configKey,expectedMatrixConfigs} from './matrix-contract.mjs';
import {readNativeProof} from './native-receipt-proof.mjs';
import {pathToFileURL} from 'node:url';
import {verifyPairPhase,manifestAllowance} from './pair-proof.mjs';
import {loadVersionInput} from './version-input.mjs';
import {fixtureFingerprint} from './fixture-binding.mjs';
import {nativeImpactVerifier} from '../../tools/native-impact.mjs';
import {pngEvidence} from '../../tools/png-evidence.mjs';
const json=p=>JSON.parse(readFileSync(p,'utf8'));
const sha=b=>createHash('sha256').update(b).digest('hex');
export function requirePureSource(c){
 if(!/^[a-f0-9]{40}$/.test(c?.commit||'')||!/^[a-f0-9]{64}$/.test(c?.publicCombined||''))throw Error('PENDING: exact final pure #577 source commit/publicCombined has not been delivered');
}
export function validatePureMatrix(r,{mode,side,combined,manifest}){
 const errors=[];
 if(r.sourceCombined!==combined||!r.sourceStable||!r.fixtureStable||!r.auditEnabled||r.coverage?.scope!==mode||r.coverage?.completeMatrix!==true||!r.coverage?.ok)errors.push('source/fixture/audit/complete matrix binding absent');
 if(validateMatrixConfigs(r.configs||[],mode).length)errors.push('incomplete or duplicate configurations');
 const expected=new Set();for(const cfg of r.configs||[]){const names=mode==='touch'?manifest.touchScenes:[...manifest.scenes,...(cfg.width>=768?manifest.additionalDesktopScenes:[])];for(const name of names)expected.add(configKey(cfg)+'|'+name);}
 const actual=(r.rows||[]).map(x=>configKey(x)+'|'+x.name);
 if(!expected.size||actual.length!==expected.size||new Set(actual).size!==actual.length||actual.some(k=>!expected.has(k)))errors.push('scenes missing, duplicated or unexpected');
 if((r.rows||[]).some(x=>!x.ok||(x.notApplicable&&!(mode==='full'&&x.name==='settings-ios'&&x.width>390))))errors.push('failed or undeclared uncaptured scene');
 if(side==='after'&&(r.rows||[]).some(x=>!x.notApplicable&&x.ownerAuditOK!==true))errors.push('component ownership audit failed');
 return errors;
}
function git(root,...args){return execFileSync('git',args,{cwd:root,encoding:'utf8',maxBuffer:32*1024*1024});}
function frozenFiles(root,commit,prefix,actual){
 const names=git(root,'ls-tree','-r','--name-only',commit,'--',prefix).trim().split('\n').filter(Boolean),hashes={};
 for(const p of names){const rel=p.slice(prefix.length+1),bytes=execFileSync('git',['show',commit+':'+p],{cwd:root,maxBuffer:32*1024*1024});hashes[rel]=sha(bytes);if(sha(readFileSync(join(actual,rel)))!==hashes[rel])throw Error('Pure source/fixture archive differs from immutable Git: '+p);}
 return hashes;
}
export async function verifyFullRefactor({gitRoot,contract,pureWebRoot,directory}){
 requirePureSource(contract.pureRefactorCandidate);
 const beforeCombined=contract.pureRefactorBaseline?.publicCombined,afterCombined=contract.pureRefactorCandidate.publicCombined;
 if(!/^[a-f0-9]{64}$/.test(beforeCombined||''))throw Error('Missing independent formal #576 baseline identity');
 const commit=contract.pureRefactorCandidate.commit;
 const actualVersion=JSON.parse(git(gitRoot,'show',commit+':web/public/version.json'));
 if(actualVersion.combined!==afterCombined)throw Error('Pure candidate version is not bound to Git');
 const fixtureFiles=frozenFiles(gitRoot,commit,'agentjarvis/web',pureWebRoot);
 const expectedFixture=fixtureFingerprint(resolve(pureWebRoot,'..'));
 for(const [key,hash]of Object.entries(expectedFixture)){if(key.startsWith('p577/'))continue;const raw=execFileSync('git',['show',commit+':agentjarvis/'+key],{cwd:gitRoot,maxBuffer:32*1024*1024});if(sha(raw)!==hash)throw Error('Pure fixture is not immutable candidate Git bytes: '+key);}
 const sourceMaps={};
 for(const [side,sourceCommit]of [['before',contract.pureRefactorBaseline.commit],['after',commit]]){const m={};for(const p of git(gitRoot,'ls-tree','-r','--name-only',sourceCommit,'--','web/public').trim().split('\n').filter(Boolean))m[p.slice('web/public/'.length)]=sha(execFileSync('git',['show',sourceCommit+':'+p],{cwd:gitRoot,maxBuffer:32*1024*1024}));sourceMaps[side]=m;}
 const same=(a,b)=>JSON.stringify(Object.entries(a).sort())===JSON.stringify(Object.entries(b).sort());
 const versionPath=resolve(directory,'../pure-version-input.json'),input=json(versionPath);if(input.combined!==beforeCombined)throw Error('Pure shared VERSION input does not preserve the declared formal baseline identity');
 const allowedResponses={},manifests={},noticeVariants={};for(const [side,sourceCommit]of [['before',contract.pureRefactorBaseline.commit],['after',commit]]){const actual=JSON.parse(git(gitRoot,'show',sourceCommit+':web/public/version.json'));const loaded=loadVersionInput(versionPath,actual);allowedResponses[side]={'version.js':[{sha256:sha(loaded.moduleText),transformation:'same-version-combined-input'}]};noticeVariants[side]=[{sha256:sha(JSON.stringify({...actual,version:'98.0.0'})),transformation:'version-notice-fixture'}];manifests[side]=Object.fromEntries(['manifest.webmanifest','manifest-dark.webmanifest','manifest-light.webmanifest'].map(name=>[name,git(gitRoot,'show',sourceCommit+':web/public/'+name)]));}
 const {seatRoute,seatManifest}=await import(pathToFileURL(join(pureWebRoot,'worker.ts')));
 const manifestResponseAllowed=manifestAllowance({manifests,seatRoute,seatManifest});
 const {buildVisualReceipt}=await import('./make-visual-receipt.mjs');
 const proof=await readNativeProof(pureWebRoot),manifest=json(join(pureWebRoot,'test/p577/scene-manifest.json'));
 const errors=[],verifier=nativeImpactVerifier(join(pureWebRoot,'public'),afterCombined,proof.nativeContext,e=>errors.push(e));
 const result={schema:'agentj-pure-refactor-v1',candidate:contract.pureRefactorCandidate,baseline:contract.pureRefactorBaseline,fixtureFiles,phases:{}};
 for(const mode of ['full','touch']){
  const before=join(directory,mode,'before'),after=join(directory,mode,'after'),a=json(join(before,'result.json')),b=json(join(after,'result.json'));
  for(const [side,r,combined]of[['before',a,beforeCombined],['after',b,afterCombined]]){
   const shapeErrors=validatePureMatrix(r,{mode,side,combined,manifest});if(shapeErrors.length)throw Error(mode+'/'+side+': '+shapeErrors.join('; '));
   const captured=join(directory,mode,side);
   if(!same(r.fixtureBinding||{},expectedFixture)||!same(json(join(captured,'source-before.json')),sourceMaps[side])||!same(json(join(captured,'source-after.json')),sourceMaps[side]))throw Error(mode+'/'+side+': actual immutable source/fixture byte manifests differ');
   for(const row of r.rows.filter(x=>!x.notApplicable)){const img=pngEvidence(readFileSync(join(captured,`${row.width}-${row.theme}-${row.lang}-${row.name}.png`)));if(row.selfStability?.attempts<2||!row.selfStability?.rgbaSha256||img.rgbaSha256!==row.selfStability.rgbaSha256||img.width!==row.width||img.height!==row.height)throw Error(mode+'/'+side+': actual stable decoded PNG binding missing');}
  }
  for(const row of b.rows.filter(r=>!r.notApplicable)){
   const name=`${row.width}-${row.theme}-${row.lang}-${row.name}.json.gz`,path=join(after,'runtime',name);
   if(!existsSync(path))throw Error(mode+': actual native runtime snapshot missing: '+name);
   const sample=JSON.parse(gunzipSync(readFileSync(path)));
   verifier.consume(sample,ref=>{const n=sample.nodes?.[ref.nodeId];if(!n||n.nodeId!==ref.nodeId)throw Error('Invalid actual native reference');return n;});
  }
  if(errors.length)throw Error(mode+': independent source-bound native actor/selector/scope evidence rejected: '+JSON.stringify(errors.slice(0,3)));
  const pixels=compareDirectories(before,after,join(directory,mode,'verified-diff'),{requireManifests:true});
  const painted=b.rows.filter(r=>!r.notApplicable).length;
  if(!pixels.ok||pixels.images!==painted||pixels.rows.some(r=>r.changedPixels!==0))throw Error(mode+': full zero-pixel matrix has not passed');
  if(mode==='full'){const original=await buildVisualReceipt(before,after,join(directory,mode,'verified-full'),{nativeWebRoot:pureWebRoot});if(!original.complete)throw Error('Original full #577 receipt rejected: '+JSON.stringify(original.problems.slice(0,3)));}
  const pairProofs=verifyPairPhase(join(directory,mode),{sources:sourceMaps,configs:expectedMatrixConfigs(mode),mode,allowedResponses:Object.fromEntries(['before','after'].map(side=>[side,{...allowedResponses[side],...(mode==='full'?{'version.json':noticeVariants[side]}:{})}])),manifestResponseAllowed});
  result.phases[mode]={images:painted,pixels,pairProofs};
 }
 result.native=verifier.finish();if(errors.length||!result.native.qualified)throw Error('Full/touch native union incomplete or invalid: '+JSON.stringify(errors.slice(0,3)));
 result.complete=true;return result;
}
