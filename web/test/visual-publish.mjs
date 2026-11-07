#!/usr/bin/env node
// Required release coordinator. Capture and RGBA comparison belong to #577, never this wrapper.
import {createHash} from 'node:crypto';
import {execFileSync,spawnSync} from 'node:child_process';
import {readFileSync,writeFileSync,existsSync,mkdirSync,readdirSync,statSync,lstatSync,mkdtempSync,rmSync} from 'node:fs';
import {resolve,join,dirname,relative,sep} from 'node:path';
import {homedir} from 'node:os';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {parseArgs} from 'node:util';
export const SCHEMA='agentj-web-visual-envelope-v3';
const ENTRY=fileURLToPath(import.meta.url), ROOT=resolve(dirname(ENTRY),'../../..');
export const CONTRACT='web/visual-baseline.json';
const CAPTURE='web/test/p577/capture.mjs';
const MATRIX='web/test/p577/matrix.mjs', COMPARE='web/test/p577/compare.mjs';
const REQUIRED=['web/test/p0033-dashboard-adapter.mjs','web/test/p577/environment-attestation.mjs','web/test/p577/pair-matrix.mjs','web/test/p577/full-refactor-proof.mjs','web/test/p577/pair-proof.mjs',MATRIX,COMPARE,'web/test/p577/capture.mjs','web/test/p577/scene-manifest.json','web/tools/png-evidence.mjs'];
export function gitArchive(root,commit,...paths){return execFileSync('git',['archive',commit,...paths],{cwd:root,maxBuffer:256*1024*1024});}
export const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const json=p=>JSON.parse(readFileSync(p,'utf8'));
const git=(root,...args)=>execFileSync('git',args,{cwd:root,encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
export function playwrightModules(env=process.env){const p=env.AJ_PLAYWRIGHT_MODULE;if(env.P577_NODE_MODULES)return env.P577_NODE_MODULES;if(!p)return undefined;const path=p.startsWith('file:')?fileURLToPath(p):p;return statSync(path).isDirectory()?dirname(path):resolve(dirname(path),'..');}
export function contractBinding(root=ROOT){return digest(Buffer.concat([readFileSync(join(root,'web/test/visual-publish.mjs')),Buffer.from('\0'),readFileSync(join(root,CONTRACT))]));}
export function configs(contract){return contract.viewports.flatMap(([width,height])=>contract.themes.flatMap(theme=>contract.languages.map(lang=>({width,height,theme,lang}))));}
const key=c=>[c.width,c.height,c.theme,c.lang].join('|');
export function validateContract(c){
 const errors=[];
 if(c?.acceptance?.mode!=='baseline-noise-envelope'||c.acceptance.minimumSamples!==3||c.acceptance.uncertainFraction!==0.005||c.acceptance.outsidePixels!==0||!/^INCOMING-0280-from-jarvis-1012\.md$/.test(c.acceptance.authorization?.file||'')||! /^[a-f0-9]{64}$/.test(c.acceptance.authorization?.sha256||''))errors.push('Explicit 1012 baseline-noise acceptance contract missing');
 if(c?.captureTopology?.schema!=='p577-owned-page-envelope-v3'||c?.captureTopology?.topology!=='one-owned-page-new-documents-per-side')errors.push('New-document owned Page topology contract missing');
 if(c?.schemaVersion!==1)errors.push('Unknown visual baseline schema');
 if(!/^[a-f0-9]{40}$/.test(c?.baseline?.commit||'')||! /^[a-f0-9]{64}$/.test(c?.baseline?.publicCombined||''))errors.push('Missing exact baseline commit/publicCombined');
 const ports=Array.isArray(c?.viewports)?c.viewports:[],widths=ports.map(x=>Array.isArray(x)?x[0]:null);
 if(widths.length<3||new Set(widths).size!==widths.length||!widths.includes(390)||!widths.includes(1440)||!widths.some(x=>x===768||x===834)||ports.some(x=>!Array.isArray(x)||x.length!==2||x.some(n=>!Number.isInteger(n)||n<1)))errors.push('Phone/tablet/desktop viewport contract missing');
 if(JSON.stringify(c?.themes)!==JSON.stringify(['light','dark'])||JSON.stringify(c?.languages)!==JSON.stringify(['zh','en']))errors.push('Both themes and languages are required');
 if(!Array.isArray(c?.criticalScenes)||c.criticalScenes.length<8||new Set(c.criticalScenes).size!==c.criticalScenes.length)errors.push('Critical scene contract missing or duplicated');
 if(c?.cacheEnvironment?.status!=='ready')errors.push('PENDING: fresh remote environment cache attestation');
 if(c?.scope!=='publish-critical'||c?.runnerInterface?.status!=='ready')errors.push('PENDING: frozen #577 publish-critical profile');
 if(c?.dashboard?.status!=='preserved-unchanged'||!/^[a-f0-9]{64}$/.test(c?.dashboard?.baselineAssetsSha256||''))errors.push('PENDING: preserved Dashboard asset baseline binding');
 if(!/^[a-f0-9]{64}$/.test(c?.sceneManifestSha256||''))errors.push('PENDING: frozen #577 critical scene manifest digest');
 if(c?.comparisonInput?.status!=='ready')errors.push('PENDING: frozen #577 same-VERSION/time fixture interface');
 else if(c.comparisonInput.matrixFlag!=='--comparison-inputs'||typeof c.comparisonInput.file!=='string'||!c.comparisonInput.file.startsWith('web/test/p577/')||typeof c.comparisonInput.receiptField!=='string'||typeof c.comparisonInput.rawAboutField!=='string')errors.push('Unverified comparison-input interface');
 return errors;
}
export function preflight(root=ROOT){
 let c;const errors=[];
 try{c=json(join(root,CONTRACT));errors.push(...validateContract(c));}catch(e){errors.push('Baseline contract unavailable: '+e.message);}
 for(const p of REQUIRED)if(!existsSync(join(root,p)))errors.push('Missing frozen #577 module: '+p);
 if(c?.acceptance?.baselineStatus!=='confirmed')errors.push('PENDING: formal576 / late-business noise and reference source clarification');
 for(const name of ['authorization','clarification'])try{const a=c.acceptance[name];if(digest(readFileSync(join(root,a.copy)))!==a.sha256)errors.push(name+' authorization copy changed');}catch{errors.push(name+' authorization copy missing');}
 for(const file of [c?.acceptance?.formalDrift?.sceneContract,c?.acceptance?.formalDrift?.decisions])if(!file||!existsSync(join(root,file)))errors.push('PENDING: formal576 drift source contract missing');
 if(c?.sceneManifestSha256&&existsSync(join(root,'web/test/p577/scene-manifest.json'))&&digest(readFileSync(join(root,'web/test/p577/scene-manifest.json')))!==c.sceneManifestSha256)errors.push('Frozen scene manifest changed');
 if(c?.comparisonInput?.file&&!existsSync(join(root,c.comparisonInput.file)))errors.push('Missing comparison input fixture');
 if(/^[a-f0-9]{40}$/.test(c?.baseline?.commit||'')){try{const v=JSON.parse(git(root,'show',c.baseline.commit+':web/public/version.json'));if(v.combined!==c.baseline.publicCombined)errors.push('Baseline publicCombined does not bind Git source');}catch{errors.push('Baseline Git source unavailable');}}
 return {contract:c,errors};
}
export function fingerprint(root){return Object.fromEntries(readdirSync(root,{recursive:true}).filter(p=>{const s=lstatSync(join(root,p));if(s.isSymbolicLink())throw Error('Visual source/artifact contains a symlink');return s.isFile();}).sort().map(p=>[p,digest(readFileSync(join(root,p)))]));}
export function fixtureFiles(root){
 const paths=git(root,'ls-files','-z','--','web/test','web/tools','agentjarvis/protocol','web/component-ownership.json','web/control-ownership-sites.json','web/reply-components.json','agentjarvis/dashboard','agentjarvis/worker/src').split('\0').filter(Boolean).sort();
 return Object.fromEntries(paths.map(p=>{const file=join(root,p);if(lstatSync(file).isSymbolicLink())throw Error('Fixture source contains a symlink');return [p,digest(readFileSync(file))];}));
}
function equal(a,b){return JSON.stringify(a)===JSON.stringify(b);}
export function validateCapture(directory,source,contract,manifest,pngEvidence,side,expectedCombined){
 const r=json(join(directory,'result.json')),errors=[];
 if(contract.expectedFixtureBinding&&!equal(r.fixtureBinding,contract.expectedFixtureBinding))errors.push(side+': actual fixture source fingerprint differs');
 if(contract.formalLegacyOwner&&r.ownershipAuditScope!=='formal-pre577-source')errors.push(side+': pre577 ownership audit boundary absent');
 if(contract.criticalSelectionSha256&&r.criticalSceneSelection?.sha256!==contract.criticalSelectionSha256)errors.push(side+': formal critical subset binding differs');
 if(r.side!==side||!r.sourceStable||!r.fixtureStable||r.auditEnabled!==true||r.coverage?.scope!=='publish-critical'||r.coverage?.completeMatrix!==true||r.coverage?.ok!==true||r.fullRefactorQualification!==false)errors.push(side+': incomplete/drifting real matrix');
 if(!equal(json(join(directory,'source-before.json')),source)||!equal(json(join(directory,'source-after.json')),source))errors.push(side+': source manifest does not bind actual public bytes');
 if(!equal(r.coverage?.requiredScenes,manifest))errors.push(side+': scene manifest differs from frozen runner input');
 if(!expectedCombined||r.sourceCombined!==expectedCombined)errors.push(side+': runtime source combined mismatch');
 const input=r[contract.comparisonInput.receiptField];
 if(!contract.expectedInputSha256||input?.sha256!==contract.expectedInputSha256||input?.policy!=='fixed-interface-data-only')errors.push(side+': same-input fixture receipt pending or mismatched');
 const raw=r[contract.comparisonInput.rawAboutField];
 for(const cfg of configs(contract)){
  const entries=Array.isArray(raw)?raw.filter(x=>key(x)===key(cfg)):[];
  if(entries.length!==1||entries[0].sourceCombined!==expectedCombined){errors.push(side+': real About receipt missing');continue;}
  try{const file=resolve(directory,entries[0].screenshot);if(!file.startsWith(resolve(directory)+sep)||digest(readFileSync(file))!==entries[0].sha256)throw Error();pngEvidence(readFileSync(file));const textFile=resolve(directory,entries[0].textFile);if(!textFile.startsWith(resolve(directory)+sep)||digest(readFileSync(textFile))!==entries[0].textSha256||!readFileSync(textFile,'utf8').includes(expectedCombined))throw Error();}catch{errors.push(side+': real About image missing or changed');}
 }
 const expected=configs(contract).map(key).sort(), actual=(r.configs||[]).map(key).sort();
 if(!equal(expected,actual)||new Set(actual).size!==actual.length)errors.push(side+': required width/theme/language missing or duplicated');
 const expectedFiles=new Set(),rowKeys=new Set();
 for(const cfg of configs(contract)){
  const names=contract.criticalScenes;
  for(const name of names){
   const row=(r.rows||[]).filter(x=>key(x)===key(cfg)&&x.name===name);
   const filename=`${cfg.width}-${cfg.theme}-${cfg.lang}-${name}.png`;rowKeys.add(key(cfg)+'|'+name);
   const na=(contract.notApplicable||[]).some(x=>x.scene===name&&x.widths.includes(cfg.width));
   if(na){if(row.length!==1||row[0].ok!==true||row[0].notApplicable!=='Desktop UA uses browser install menu')errors.push(side+': invalid product N/A '+filename);continue;}
   expectedFiles.add(filename);
   if(row.length!==1||row[0].ok!==true||(side==='after'&&!contract.formalLegacyOwner&&name!=='publish-dashboard-account'&&row[0].ownerAuditOK!==true)){errors.push(side+': missing/failed scene '+filename);continue;}
   if(name==='publish-dashboard-account'&&(row[0].sourceScope!=='dashboard-preserved'||row[0].preservedAssetsSha256!==contract.dashboard.baselineAssetsSha256||row[0].accountBootComplete!==true))errors.push(side+': unchanged Dashboard source binding missing');
   try{const png=pngEvidence(readFileSync(join(directory,filename)));if(!(row[0].selfStability?.attempts>=2)||png.rgbaSha256!==row[0].selfStability?.rgbaSha256||png.width!==cfg.width||png.height!==cfg.height)errors.push(side+': image hash/dimensions mismatch '+filename);}catch{errors.push(side+': missing/invalid PNG '+filename);}
  }
 }
 if((r.rows||[]).some(x=>!rowKeys.has(key(x)+'|'+x.name))||r.rows?.length!==rowKeys.size)errors.push(side+': unexpected or duplicated scenes');
 if(!equal([...expectedFiles].sort(),readdirSync(directory).filter(x=>x.endsWith('.png')).sort()))errors.push(side+': screenshot set incomplete');
 for(const name of contract.criticalScenes)if(!manifest.scenes.includes(name))errors.push(side+': required critical interface absent '+name);
 return {receipt:r,errors};
}
export function matrixArgs({root,source,out,side,contract,modules,chromium,dashboardSource}){
 return [join(root,MATRIX),'--source',source,'--out',out,'--side',side,'--profile','publish-critical','--dashboard-source',dashboardSource,'--fixtures',root,'--audit','--jobs','auto',...(modules?['--modules',modules]:[]),...(chromium?['--chromium',chromium]:[]),contract.comparisonInput.matrixFlag,join(root,contract.comparisonInput.file)];
}
function command(args,env){const r=spawnSync(process.execPath,args,{stdio:'inherit',env});if(r.error)throw r.error;if(r.status!==0)throw Error('Visual command failed ('+r.status+'): '+relative(ROOT,args[0]));}
export async function run({root=ROOT,out}){
 const p=preflight(root);if(p.errors.length)throw Error(p.errors.join('\n'));
 const {fixtureFingerprint,resolveFixtureRoot}=await import(pathToFileURL(join(root,'web/test/p577/fixture-binding.mjs'))),{criticalFixtureFingerprint}=await import(pathToFileURL(join(root,'web/test/p577/publish-critical-profile.mjs')));p.contract.expectedFixtureBinding={...fixtureFingerprint(resolveFixtureRoot(root)),...criticalFixtureFingerprint(resolveFixtureRoot(root))};
 const binding=contractBinding(root),c=p.contract,publicDir=join(root,'web/public'),source=fingerprint(publicDir),fixtures=fixtureFiles(root),candidateCommit=git(root,'rev-parse','HEAD'),candidatePublicTree=git(root,'rev-parse','HEAD:web/public');
 if(git(root,'status','--porcelain','--untracked-files=no','--','agentjarvis/web'))throw Error('Tracked Web inputs are not frozen');
 if(existsSync(out)&&readdirSync(out).length)throw Error('Visual output directory must be fresh');
 mkdirSync(out,{recursive:true});mkdirSync(join(homedir(),'.cache'),{recursive:true});
 const temp=mkdtempSync(join(homedir(),'.cache','ajvp-')),env={...process.env,TMPDIR:temp};
 try{
  const archive=gitArchive(root,c.baseline.commit,'web/public','agentjarvis/dashboard/public');execFileSync('tar',['-x','-C',temp],{input:archive});
  const baseline=join(temp,'web/public'),before=join(out,'before'),after=join(out,'after'),diff=join(out,'diff');
  const dashboardBefore=join(temp,'agentjarvis/dashboard/public'),dashboardAfter=join(root,'agentjarvis/dashboard/public');
  const dashboardAssets=fingerprint(dashboardAfter);
  if(!equal(fingerprint(dashboardBefore),dashboardAssets)||digest(Buffer.from(JSON.stringify(dashboardAssets)))!==c.dashboard.baselineAssetsSha256)throw Error('Preserved Dashboard assets differ from fixed baseline');
  const formalDirectory=join(out,'formal576-drift'),formalArchive=join(temp,'formal576');mkdirSync(formalArchive);execFileSync('tar',['-x','-C',formalArchive],{input:gitArchive(root,c.acceptance.formalDrift.baseline.commit,'web/public')});const formalSource=join(formalArchive,'web/public');
  const modulePath=playwrightModules();
  const {runPairPhases}=await import(pathToFileURL(join(root,'web/test/p577/pair-matrix.mjs')));
  await runPairPhases({root,phases:[{name:'formal576-drift',profile:'publish-critical',mode:'full',baselineSamples:c.acceptance.minimumSamples,sourceBefore:formalSource,sourceAfter:baseline,fixtures:root,out:formalDirectory,dashboardSource:dashboardBefore,dashboardSourceBefore:dashboardBefore,comparisonInputs:join(root,c.comparisonInput.file),criticalScenes:join(root,c.acceptance.formalDrift.sceneContract)},{name:'critical',profile:'publish-critical',mode:'full',baselineSamples:c.acceptance.minimumSamples,sourceBefore:baseline,sourceAfter:publicDir,fixtures:root,out,dashboardSource:dashboardAfter,dashboardSourceBefore:dashboardBefore,comparisonInputs:join(root,c.comparisonInput.file)}],modules:modulePath,chromium:process.env.CHROMIUM,jobs:'auto',env});
  const formalDrift=await qualifyFormalDrift({root,out:formalDirectory,c,baseline:formalSource,candidate:baseline,requirePass:false});
  const {verifyPairPhase,manifestAllowance}=await import(pathToFileURL(join(root,'web/test/p577/pair-proof.mjs')));
  const criticalAllowed={},criticalManifests={};for(const [side,dir]of [['before',baseline],['after',publicDir]]){criticalAllowed[side]={};for(const name of ['version.js','version.json']){const production=readFileSync(join(dir,name),'utf8'),served=production.replace(/("version"\s*:\s*)"[^"]*"/,(_,prefix)=>prefix+JSON.stringify(json(join(root,c.comparisonInput.file)).displayVersion));criticalAllowed[side][name]=[{sha256:digest(served),transformation:'fixed-interface-data-only'}];}criticalManifests[side]=Object.fromEntries(['manifest.webmanifest','manifest-dark.webmanifest','manifest-light.webmanifest'].map(name=>[name,readFileSync(join(dir,name),'utf8')]));}
  const {seatRoute,seatManifest}=await import(pathToFileURL(join(root,'web/worker.ts')));
  const criticalPairProofs=verifyPairPhase(out,{sources:{before:fingerprint(baseline),after:source},configs:configs(c),mode:'full',profile:'publish-critical',baselineSamples:c.acceptance.minimumSamples,allowedResponses:criticalAllowed,manifestResponseAllowed:manifestAllowance({manifests:criticalManifests,seatRoute,seatManifest})});
  const {pngEvidence}=await import(pathToFileURL(join(root,'web/tools/png-evidence.mjs'))),manifest=json(join(root,'web/test/p577/scene-manifest.json')).publishCritical;
  c.expectedInputSha256=digest(readFileSync(join(root,c.comparisonInput.file)));
  const {environmentFingerprint}=await import(pathToFileURL(join(root,'web/test/p577/environment-attestation.mjs')));
  const sampleDirectories=Array.from({length:c.acceptance.minimumSamples},(_,i)=>join(out,i?'baseline-'+i:'before'));
  const sampleCaptures=sampleDirectories.map(dir=>validateCapture(dir,fingerprint(baseline),c,manifest,pngEvidence,'before',c.baseline.publicCombined));
  const a=validateCapture(before,fingerprint(baseline),c,manifest,pngEvidence,'before',c.baseline.publicCombined),b=validateCapture(after,source,c,manifest,pngEvidence,'after',json(join(publicDir,'version.json')).combined);
  if(sampleCaptures.some(x=>x.errors.length)||b.errors.length)throw Error([...sampleCaptures.flatMap(x=>x.errors),...b.errors].join('\n'));
  const environmentHash=environmentFingerprint(a.receipt.environment);
  if(sampleCaptures.some(x=>environmentFingerprint(x.receipt.environment)!==environmentHash)||environmentFingerprint(b.receipt.environment)!==environmentHash)throw Error('Actual before/after environment differs');
  const {compareDirectories,compareNoiseEnvelope}=await import(pathToFileURL(join(root,COMPARE)));
  const rawAboutComparison=compareDirectories(join(before,'raw-about'),join(after,'raw-about'),join(out,'raw-about-diff'));
  if(rawAboutComparison.errors.length||rawAboutComparison.images!==configs(c).length)throw Error('Raw About original image diff incomplete');
  const rawAboutText=configs(c).map(cfg=>{const name=`${cfg.width}-${cfg.theme}-${cfg.lang}.txt`;return {...cfg,before:readFileSync(join(before,'raw-about',name),'utf8'),after:readFileSync(join(after,'raw-about',name),'utf8')};});
  writeFileSync(join(out,'raw-about-diff/text.json'),JSON.stringify(rawAboutText,null,2)+'\n');
  const comparison=compareNoiseEnvelope(sampleDirectories,after,diff);
  writeFileSync(join(out,'noise-summary.json'),JSON.stringify(noiseConfigurationSummary(comparison,c),null,2)+'\n');
  if(!formalDrift.review.ok)throw Error('Formal576 drift FAIL/UNCERTAIN: actual regions/review report retained; primary envelope result also retained');
  if(comparison.ok!==true||comparison.verdict!=='PASS'||comparison.images!==a.receipt.rows.filter(x=>!x.notApplicable).length||comparison.errors?.length||comparison.rows?.some(x=>x.outsidePixels!==0||x.verdict!=='PASS'))throw Error('Noise envelope '+comparison.verdict+': original samples/diff/side-by-side evidence retained');
  if(binding!==contractBinding(root)||!equal(source,fingerprint(publicDir))||!equal(fixtures,fixtureFiles(root)))throw Error('Candidate source/fixtures drifted during visual qualification');
  const artifacts=Object.fromEntries([...sampleDirectories.map(dir=>relative(out,dir)),'after','diff','raw-about-diff','pairs','formal576-drift'].flatMap(dir=>Object.entries(fingerprint(join(out,dir))).map(([path,sha])=>[dir+'/'+path,sha])));
  const receipt={schema:SCHEMA,verdict:'PASS',contractBinding:binding,baseline:c.baseline,candidateCommit,candidatePublicTree,fixtureFiles:fixtures,scope:'publish-critical',dashboardAssetsSha256:c.dashboard.baselineAssetsSha256,environmentFingerprint:environmentHash,captureTopology:c.captureTopology,criticalPairProofs,acceptance:c.acceptance,baselineSamples:sampleDirectories.map((dir,index)=>({index,directory:relative(out,dir),source:c.baseline,inputSha256:c.expectedInputSha256,environmentFingerprint:environmentHash})),fullRefactor:{status:'post-release-required',blocksRelease:false,authorization:c.acceptance.authorization},noiseSummary:noiseConfigurationSummary(comparison,c),formalDrift,rawAboutComparison,comparison,artifacts};
  writeFileSync(join(out,'publish-visual.json'),JSON.stringify(receipt,null,2)+'\n');return receipt;
 }finally{rmSync(temp,{recursive:true,force:true});}
}
async function qualifyFormalDrift({root,out,c,baseline,candidate,verify=false,requirePass=true}){
 const contractFile=join(root,c.acceptance.formalDrift.sceneContract),selection=json(contractFile),subset={...c,criticalScenes:selection.scenes,criticalSelectionSha256:digest(readFileSync(contractFile)),formalLegacyOwner:true};
 if(selection.formalBaseline!==c.acceptance.formalDrift.baseline.commit||selection.businessReference!==c.baseline.commit||!Array.isArray(selection.scenes)||!selection.scenes.length)throw Error('Formal576 existing-scene source contract differs');
 const sourceMaps={before:fingerprint(baseline),after:fingerprint(candidate)},allowed={},manifests={};
 for(const [side,dir]of [['before',baseline],['after',candidate]]){allowed[side]={};for(const name of ['version.js','version.json']){const served=readFileSync(join(dir,name),'utf8').replace(/("version"\s*:\s*)"[^"]*"/,(_,prefix)=>prefix+JSON.stringify(json(join(root,c.comparisonInput.file)).displayVersion));allowed[side][name]=[{sha256:digest(served),transformation:'fixed-interface-data-only'}];}manifests[side]=Object.fromEntries(['manifest.webmanifest','manifest-dark.webmanifest','manifest-light.webmanifest'].map(n=>[n,readFileSync(join(dir,n),'utf8')]));}
 const {verifyPairPhase,manifestAllowance}=await import(pathToFileURL(join(root,'web/test/p577/pair-proof.mjs'))),{seatRoute,seatManifest}=await import(pathToFileURL(join(root,'web/worker.ts')));
 const pairProofs=verifyPairPhase(out,{sources:sourceMaps,configs:configs(c),mode:'full',profile:'publish-critical',baselineSamples:c.acceptance.minimumSamples,allowedResponses:allowed,manifestResponseAllowed:manifestAllowance({manifests,seatRoute,seatManifest})});
 const {pngEvidence}=await import(pathToFileURL(join(root,'web/tools/png-evidence.mjs'))),{environmentFingerprint}=await import(pathToFileURL(join(root,'web/test/p577/environment-attestation.mjs'))),manifest={...json(join(root,'web/test/p577/scene-manifest.json')).publishCritical,scenes:selection.scenes};subset.expectedInputSha256=digest(readFileSync(join(root,c.comparisonInput.file)));
 const samples=Array.from({length:c.acceptance.minimumSamples},(_,i)=>join(out,i?'baseline-'+i:'before'));let env;
 for(const label of [...samples,join(out,'after')]){const after=label===join(out,'after'),check=validateCapture(label,sourceMaps[after?'after':'before'],subset,manifest,pngEvidence,after?'after':'before',after?c.baseline.publicCombined:c.acceptance.formalDrift.baseline.publicCombined);if(check.errors.length)throw Error(check.errors.join('\n'));const hash=environmentFingerprint(check.receipt.environment);if(env&&hash!==env)throw Error('Formal576 sample static environment differs');env=hash;}
 const {compareNoiseEnvelope,verifyNoiseEnvelopeArtifacts}=await import(pathToFileURL(join(root,COMPARE))),diff=join(out,'diff');const comparison=verify?verifyNoiseEnvelopeArtifacts(samples,join(out,'after'),diff):compareNoiseEnvelope(samples,join(out,'after'),diff);
 const {admitFormalDrift,formalOutsideRegions}=await import(pathToFileURL(join(root,'web/test/p577/formal-drift.mjs'))),regions=Object.fromEntries(comparison.rows.filter(r=>r.outsidePixels).map(row=>[row.name,formalOutsideRegions(diff,row)]));
 const review=admitFormalDrift(comparison,json(join(root,c.acceptance.formalDrift.decisions)),{baselineCommit:c.acceptance.formalDrift.baseline.commit,referenceCommit:c.baseline.commit},regions),proof={schema:'agentj-formal576-noise-v1',authorization:c.acceptance.clarification,baseline:c.acceptance.formalDrift.baseline,reference:c.baseline,sceneSelectionSha256:subset.criticalSelectionSha256,environmentFingerprint:env,pairProofs,comparison,noiseSummary:noiseConfigurationSummary(comparison,subset),outsideRegions:regions,review};
 if(!verify){writeFileSync(join(out,'formal-drift-proof.json'),JSON.stringify(proof,null,2)+'\n');}else if(!equal(json(join(out,'formal-drift-proof.json')),proof))throw Error('Formal576 drift artifact differs');
 if(requirePass&&!review.ok)throw Error('Formal576 drift FAIL/UNCERTAIN: actual outside regions and source-bound review requirements retained');return proof;
}
export function noiseConfigurationSummary(comparison,contract){
 return configs(contract).map(cfg=>{const prefix=`${cfg.width}-${cfg.theme}-${cfg.lang}-`,rows=(comparison.rows||[]).filter(r=>r.name.startsWith(prefix));return {...cfg,scenes:rows.length,sampleCount:comparison.sampleCount,envelopePixels:rows.reduce((n,r)=>n+r.envelopePixels,0),maxSceneEnvelopeFraction:Math.max(0,...rows.map(r=>r.envelopeFraction)),outsidePixels:rows.reduce((n,r)=>n+r.outsidePixels,0),verdict:rows.some(r=>r.verdict==='FAIL')?'FAIL':rows.some(r=>r.verdict==='UNCERTAIN')?'UNCERTAIN':'PASS'};});
}
export async function verifyReceipt({root=ROOT,path}){
 mkdirSync(join(homedir(),'.cache'),{recursive:true});process.env.TMPDIR=join(homedir(),'.cache');
 const p=preflight(root);if(p.errors.length)throw Error(p.errors.join('\n'));const {fixtureFingerprint,resolveFixtureRoot}=await import(pathToFileURL(join(root,'web/test/p577/fixture-binding.mjs'))),{criticalFixtureFingerprint}=await import(pathToFileURL(join(root,'web/test/p577/publish-critical-profile.mjs')));p.contract.expectedFixtureBinding={...fixtureFingerprint(resolveFixtureRoot(root)),...criticalFixtureFingerprint(resolveFixtureRoot(root))};const c=p.contract,r=json(path),directory=dirname(path);
 if(r.schema!==SCHEMA||r.verdict!=='PASS'||r.contractBinding!==contractBinding(root)||!equal(r.baseline,c.baseline)||!equal(r.acceptance,c.acceptance)||r.candidatePublicTree!==git(root,'rev-parse','HEAD:web/public')||!equal(r.fixtureFiles,fixtureFiles(root)))throw Error('Stale/source-mismatched envelope receipt');
 for(const [name,sha]of Object.entries(r.artifacts||{})){const file=resolve(directory,name);if(!file.startsWith(resolve(directory)+sep)||digest(readFileSync(file))!==sha)throw Error('Changed envelope artifact '+name);}
 const samples=Array.from({length:c.acceptance.minimumSamples},(_,i)=>i?'baseline-'+i:'before');
 if(r.baselineSamples?.length!==samples.length||r.baselineSamples.some((x,i)=>x.index!==i||x.directory!==samples[i]||!equal(x.source,c.baseline)||x.inputSha256!==digest(readFileSync(join(root,c.comparisonInput.file)))))throw Error('Independent baseline sample identity missing');
 const {verifyNoiseEnvelope}=await import(pathToFileURL(join(root,COMPARE)));await verifyNoiseEnvelope(samples.map(x=>join(directory,x)),join(directory,'after'),join(directory,'diff'));if(!equal(json(join(directory,'diff/result.json')),r.comparison)||!equal(noiseConfigurationSummary(r.comparison,c),r.noiseSummary))throw Error('Envelope summary differs from actual pixel result');
 const temp=mkdtempSync(join(homedir(),'.cache','ajvp-verify-'));
 try{
  execFileSync('tar',['-x','-C',temp],{input:gitArchive(root,c.baseline.commit,'web/public')});const baseline=join(temp,'web/public'),source=join(root,'web/public');
  const {verifyPairPhase,manifestAllowance}=await import(pathToFileURL(join(root,'web/test/p577/pair-proof.mjs'))),{seatRoute,seatManifest}=await import(pathToFileURL(join(root,'web/worker.ts')));
  const allowed={},manifests={};for(const [side,dir]of [['before',baseline],['after',source]]){allowed[side]={};for(const name of ['version.js','version.json']){const served=readFileSync(join(dir,name),'utf8').replace(/("version"\s*:\s*)"[^"]*"/,(_,prefix)=>prefix+JSON.stringify(json(join(root,c.comparisonInput.file)).displayVersion));allowed[side][name]=[{sha256:digest(served),transformation:'fixed-interface-data-only'}];}manifests[side]=Object.fromEntries(['manifest.webmanifest','manifest-dark.webmanifest','manifest-light.webmanifest'].map(n=>[n,readFileSync(join(dir,n),'utf8')]));}
  const formalRoot=join(temp,'formal');mkdirSync(formalRoot);execFileSync('tar',['-x','-C',formalRoot],{input:gitArchive(root,c.acceptance.formalDrift.baseline.commit,'web/public')});
  const formal=await qualifyFormalDrift({root,out:join(directory,'formal576-drift'),c,baseline:join(formalRoot,'web/public'),candidate:baseline,verify:true});if(!equal(formal,r.formalDrift))throw Error('Formal576 anti-drift proof differs');
  const proofs=verifyPairPhase(directory,{sources:{before:fingerprint(baseline),after:fingerprint(source)},configs:configs(c),mode:'full',profile:'publish-critical',baselineSamples:c.acceptance.minimumSamples,allowedResponses:allowed,manifestResponseAllowed:manifestAllowance({manifests,seatRoute,seatManifest})});if(!equal(proofs,r.criticalPairProofs))throw Error('Baseline/candidate isolation proof differs');
  const {pngEvidence}=await import(pathToFileURL(join(root,'web/tools/png-evidence.mjs'))),manifest=json(join(root,'web/test/p577/scene-manifest.json')).publishCritical;c.expectedInputSha256=digest(readFileSync(join(root,c.comparisonInput.file)));
  for(const label of [...samples,'after']){const after=label==='after',check=validateCapture(join(directory,label),fingerprint(after?source:baseline),c,manifest,pngEvidence,after?'after':'before',after?json(join(source,'version.json')).combined:c.baseline.publicCombined);if(check.errors.length)throw Error(check.errors.join('\n'));}
 }finally{rmSync(temp,{recursive:true,force:true});}
 return r;
}
export async function attest({root=ROOT,out}){
 const p=preflight(root);if(p.errors.length)throw Error(p.errors.join('\n'));
 if(existsSync(out)&&readdirSync(out).length)throw Error('Environment evidence directory must be fresh');mkdirSync(out,{recursive:true});
 const modulePath=playwrightModules();
 command([join(root,CAPTURE),'--source',join(root,'web/public'),'--out',out,'--fixtures',root,'--environment-only',...(modulePath?['--modules',modulePath]:[]),...(process.env.CHROMIUM?['--chromium',process.env.CHROMIUM]:[])],process.env);
 const r=json(join(out,'environment-only.json')),{environmentFingerprint}=await import(pathToFileURL(join(root,'web/test/p577/environment-attestation.mjs')));
 if(r.schema!=='p577-environment-only-v1'||!r.sourceStable||!r.fixtureStable||r.pagesCreated!==0||r.screenshots!==0||r.sourceCombined!==json(join(root,'web/public/version.json')).combined||r.environmentFingerprint!==environmentFingerprint(r.environment))throw Error('Actual environment probe is incomplete or drifting');
 const result={schema:'agentj-web-environment-v1',contractBinding:contractBinding(root),candidatePublicTree:git(root,'rev-parse','HEAD:web/public'),environmentFingerprint:r.environmentFingerprint,pagesCreated:0,screenshots:0};
 writeFileSync(join(out,'publish-environment.json'),JSON.stringify(result,null,2)+'\n');return result;
}
async function main(){
 const {values}=parseArgs({options:{check:{type:'boolean',default:false},'environment-only':{type:'boolean',default:false},'verify-receipt':{type:'string'},out:{type:'string'}}});
 if(values['verify-receipt']){await verifyReceipt({root:ROOT,path:resolve(values['verify-receipt'])});console.log(JSON.stringify({verified:true,schema:SCHEMA}));return;}
 if(values.check){const p=preflight();console.log(JSON.stringify({qualified:false,interfaceReady:p.errors.length===0,pendingQualifications:['critical baseline N>=3 noise-envelope qualification; full 2376 deferred by 1012 authorization'],errors:p.errors}));if(p.errors.length)process.exitCode=1;return;}
 const out=resolve(values.out||process.env.OLARES_RUN_OUT||'reports/qa/web-visual-publish');
 try{if(values['environment-only']){console.log(JSON.stringify(await attest({out})));return;}const r=await run({out});console.log(JSON.stringify({verdict:r.verdict,images:r.comparison.images}));}
 catch(e){mkdirSync(out,{recursive:true});writeFileSync(join(out,existsSync(join(out,'publish-visual.json'))?'publish-visual-failure-'+Date.now()+'.json':'publish-visual.json'),JSON.stringify({schema:SCHEMA,verdict:'FAIL',errors:[e.message]},null,2)+'\n');console.error('FAIL web-visual: '+e.message);process.exitCode=1;}
}
if(process.argv[1]&&resolve(process.argv[1])===ENTRY)await main();
