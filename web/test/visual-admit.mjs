#!/usr/bin/env node
// Post-capture admission only. Never starts a browser or changes raw capture evidence.
import{readFileSync,writeFileSync,mkdirSync,existsSync,mkdtempSync,rmSync}from'node:fs';
import{createHash}from'node:crypto';import{execFileSync}from'node:child_process';
import{resolve,join,dirname,sep}from'node:path';import{homedir}from'node:os';import{pathToFileURL,fileURLToPath}from'node:url';import{parseArgs}from'node:util';
import{captureOrigin,withCaptureSnapshot,verifySourceBindings}from'./visual-admission-binding.mjs';
export const SCHEMA='agentj-web-visual-admission-v1';
const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),'../../..'),ENTRY='web/test/visual-admit.mjs',BINDING='web/test/visual-admission-binding.mjs',CONTRACT='web/visual-baseline.json',CAPTURE='web/test/visual-publish.mjs';
const json=p=>JSON.parse(readFileSync(p,'utf8')),hash=b=>createHash('sha256').update(b).digest('hex'),equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function verifyFrozenController(currentRoot,snapshotRoot){for(const name of [CONTRACT,CAPTURE])if(!readFileSync(join(currentRoot,name)).equals(readFileSync(join(snapshotRoot,name))))throw Error('Frozen capture controller/acceptance contract changed: '+name);}
export function primaryEnvelopeAccepted(comparison){return comparison?.mode==='baseline-noise-envelope'&&comparison.sampleCount>=3&&comparison.verdict==='PASS'&&comparison.ok===true&&comparison.images>0&&!comparison.errors.length&&comparison.rows.length===comparison.images&&comparison.rows.every(r=>r.verdict==='PASS'&&r.outsidePixels===0&&r.envelopeFraction<=0.005);}
export function admissionBinding(root=ROOT){return hash(Buffer.concat([ENTRY,BINDING,CONTRACT,CAPTURE].flatMap(p=>[readFileSync(join(root,p)),Buffer.from('\0')])));}
const git=(root,...args)=>execFileSync('git',args,{cwd:root,encoding:'utf8',maxBuffer:256*1024*1024}).trim();
async function phase({snapshot,directory,c,baseline,candidate,formal=false,review,workspace}){
 const p577=join(snapshot,'web/test/p577'),wrapper=await import(pathToFileURL(join(snapshot,CAPTURE))),{verifyPairPhase,manifestAllowance}=await import(pathToFileURL(join(p577,'pair-proof.mjs'))),{seatRoute,seatManifest}=await import(pathToFileURL(join(snapshot,'web/worker.ts')));
 const sourceMaps={before:wrapper.fingerprint(baseline),after:wrapper.fingerprint(candidate)},allowed={},manifests={},input=json(join(snapshot,c.comparisonInput.file));
 for(const [side,dir]of [['before',baseline],['after',candidate]]){allowed[side]={};for(const name of ['version.js','version.json']){const served=readFileSync(join(dir,name),'utf8').replace(/("version"\s*:\s*)"[^"]*"/,(_,prefix)=>prefix+JSON.stringify(input.displayVersion));allowed[side][name]=[{sha256:hash(served),transformation:'fixed-interface-data-only'}];}manifests[side]=Object.fromEntries(['manifest.webmanifest','manifest-dark.webmanifest','manifest-light.webmanifest'].map(n=>[n,readFileSync(join(dir,n),'utf8')]));}
 const pairProofs=verifyPairPhase(directory,{sources:sourceMaps,configs:wrapper.configs(c),mode:'full',profile:'publish-critical',baselineSamples:3,allowedResponses:allowed,manifestResponseAllowed:manifestAllowance({manifests,seatRoute,seatManifest})});
 const selection=formal?json(join(snapshot,c.acceptance.formalDrift.sceneContract)):null,contract={...c,expectedInputSha256:hash(readFileSync(join(snapshot,c.comparisonInput.file))),...(formal?{criticalScenes:selection.scenes,criticalSelectionSha256:hash(readFileSync(join(snapshot,c.acceptance.formalDrift.sceneContract))),formalLegacyOwner:true}:{})};
 const manifest={...json(join(p577,'scene-manifest.json')).publishCritical,scenes:contract.criticalScenes},{pngEvidence}=await import(pathToFileURL(join(snapshot,'web/tools/png-evidence.mjs'))),{environmentFingerprint}=await import(pathToFileURL(join(p577,'environment-attestation.mjs')));
 const samples=Array.from({length:3},(_,i)=>join(directory,i?'baseline-'+i:'before'));
 const {compareNoiseEnvelope}=await import(pathToFileURL(join(p577,'compare.mjs'))),diff=join(workspace,formal?'formal-diff':'critical-diff'),comparison=compareNoiseEnvelope(samples,join(directory,'after'),diff);
 // Memoize only evidence from the actual decoded PNGs just compared. This avoids
 // decoding the same product PNGs twice while retaining exact RGBA/hash checks.
 const decoded=new Map(comparison.samples.flatMap(s=>Object.values(s.images||{})).map(image=>[image.pngSha256,{width:image.width,height:image.height,rgbaSha256:image.rgbaSha256}]));
 const actualPngEvidence=bytes=>decoded.get(hash(bytes))||pngEvidence(bytes);let environment;
 for(const dir of [...samples,join(directory,'after')]){const after=dir===join(directory,'after'),combined=json(join(after?candidate:baseline,'version.json')).combined,check=wrapper.validateCapture(dir,sourceMaps[after?'after':'before'],contract,manifest,actualPngEvidence,after?'after':'before',combined);if(check.errors.length)throw Error(check.errors.join('\n'));const actual=environmentFingerprint(check.receipt.environment);if(environment&&actual!==environment)throw Error('Actual phase static environments differ');environment=actual;}
 // The primary criterion has no business-change waiver.
 if(!formal&&!primaryEnvelopeAccepted(comparison))return{ok:false,comparison,pairProofs,environmentFingerprint:environment,noiseSummary:wrapper.noiseConfigurationSummary(comparison,contract),errors:['Primary candidate has outside-envelope pixels, incomplete samples or UNCERTAIN noise']};
 let driftReview=null,outsideRegions=null;
 if(formal){const {formalOutsideRegions,admitFormalDrift}=await import(pathToFileURL(join(p577,'formal-drift.mjs')));outsideRegions=Object.fromEntries(comparison.rows.filter(r=>r.outsidePixels).map(row=>[row.name,formalOutsideRegions(diff,row)]));driftReview=admitFormalDrift(comparison,review,{baselineCommit:c.acceptance.formalDrift.baseline.commit,referenceCommit:c.baseline.commit},outsideRegions);}
 return{ok:formal?driftReview.ok:true,comparison,pairProofs,environmentFingerprint:environment,noiseSummary:wrapper.noiseConfigurationSummary(comparison,contract),outsideRegions,review:driftReview,errors:formal?driftReview.errors:[]};
}
export async function admit({root=ROOT,raw,reviewFile}){
 const directory=dirname(resolve(raw)),origin=captureOrigin(join(directory,'../run.log')),rawBytes=readFileSync(raw),rawReceipt=JSON.parse(rawBytes),binding=admissionBinding(root),admissionPath=join(directory,'publish-admission.json');
 mkdirSync(join(homedir(),'.cache'),{recursive:true});process.env.TMPDIR=join(homedir(),'.cache');
 if(!reviewFile&&existsSync(join(directory,'formal-business-review.json')))reviewFile=join(directory,'formal-business-review.json');
 if(rawReceipt.schema!=='agentj-web-visual-envelope-v3')throw Error('Old capture schema cannot qualify current admission');
 const workspace=join(directory,'admission-evidence-'+binding.slice(0,16)+'-'+(reviewFile?hash(readFileSync(reviewFile)).slice(0,16):'unreviewed'));
 if(existsSync(workspace))rmSync(workspace,{recursive:true,force:true});mkdirSync(workspace);
 let result;
 try{result=await withCaptureSnapshot(root,origin.commit,async snapshot=>{
  verifyFrozenController(root,snapshot);
  const wrapper=await import(pathToFileURL(join(snapshot,CAPTURE))),p=wrapper.preflight(snapshot);if(p.errors.length)throw Error(p.errors.join('\n'));const c=p.contract;
  for(const [name,sha]of Object.entries(rawReceipt.artifacts||{})){const file=resolve(directory,name);if(!file.startsWith(resolve(directory)+sep)||hash(readFileSync(file))!==sha)throw Error('Changed original recorded capture artifact '+name);}
  if(rawReceipt.verdict==='PASS'&&!Object.keys(rawReceipt.artifacts||{}).length)throw Error('Original PASS has no recorded artifacts');
  const after=json(join(directory,'after/result.json')),sourceBinding=await verifySourceBindings({currentRoot:root,snapshotRoot:snapshot,origin,fixtureFiles:rawReceipt.fixtureFiles,captureFixtureBinding:after.fixtureBinding});
  const {fixtureFingerprint,resolveFixtureRoot}=await import(pathToFileURL(join(snapshot,'web/test/p577/fixture-binding.mjs'))),{criticalFixtureFingerprint}=await import(pathToFileURL(join(snapshot,'web/test/p577/publish-critical-profile.mjs')));c.expectedFixtureBinding={...fixtureFingerprint(resolveFixtureRoot(snapshot)),...criticalFixtureFingerprint(resolveFixtureRoot(snapshot))};
  const archives=join(workspace,'source');mkdirSync(archives);for(const [name,commit]of [['business',c.baseline.commit],['formal',c.acceptance.formalDrift.baseline.commit]]){mkdirSync(join(archives,name));execFileSync('tar',['-x','-C',join(archives,name)],{input:wrapper.gitArchive(snapshot,commit,'web/public')});}
  const external=reviewFile?json(reviewFile):{schemaVersion:1,decisions:[]};if(reviewFile&&(external.authorizationSha256!==c.acceptance.clarification.sha256||external.reviewedBy!=='release-owner'))throw Error('Formal business review lacks exact1025 source/owner authorization');
  const main=await phase({snapshot,directory,c,baseline:join(archives,'business/web/public'),candidate:join(snapshot,'web/public'),workspace});
  const formal=await phase({snapshot,directory:join(directory,'formal576-drift'),c,baseline:join(archives,'formal/web/public'),candidate:join(archives,'business/web/public'),formal:true,review:external,workspace});
  if(main.environmentFingerprint!==formal.environmentFingerprint)throw Error('Two actual phases used different static environments');
  return{schema:SCHEMA,verdict:main.ok&&formal.ok?'PASS':'FAIL',admissionBinding:binding,captureOrigin:origin,captureContractBinding:wrapper.contractBinding(snapshot),sourceBinding,rawReceipt:{file:'publish-visual.json',sha256:hash(rawBytes),verdict:rawReceipt.verdict},review:reviewFile?{file:resolve(reviewFile),sha256:hash(readFileSync(reviewFile)),authorizationSha256:external.authorizationSha256}:null,environmentFingerprint:main.environmentFingerprint,candidatePublicTree:git(snapshot,'rev-parse','HEAD:web/public'),acceptance:c.acceptance,main,formal,errors:[...main.errors,...formal.errors]};
 });}catch(error){result={schema:SCHEMA,verdict:'FAIL',admissionBinding:binding,captureOrigin:origin,rawReceipt:{file:'publish-visual.json',sha256:hash(rawBytes),verdict:rawReceipt.verdict},errors:[String(error.message||error)]};}
 // New admission is a separate record, never an edit of the source capture verdict.
 result.evidenceDirectory=workspace.slice(directory.length+1);const bytes=JSON.stringify(result,null,2)+'\n';if(existsSync(admissionPath)){const previous=readFileSync(admissionPath);if(!previous.equals(Buffer.from(bytes))){mkdirSync(join(directory,'admission-history'),{recursive:true});writeFileSync(join(directory,'admission-history',hash(previous)+'.json'),previous);}}writeFileSync(admissionPath,bytes);return{path:admissionPath,result};
}
async function main(){const{values:o}=parseArgs({options:{raw:{type:'string'},review:{type:'string'}}});if(!o.raw)throw Error('visual-admit.mjs --raw <original publish-visual.json> [--review <external formal1025 review JSON>]');const {path,result}=await admit({raw:resolve(o.raw),reviewFile:o.review&&resolve(o.review)});console.log(JSON.stringify({schema:SCHEMA,verdict:result.verdict,admissionBinding:result.admissionBinding,path,sha256:hash(readFileSync(path))}));if(result.verdict!=='PASS')process.exitCode=1;}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))await main();
