// Contract/rejection fixtures only; these tests never qualify browser pixels.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,writeFileSync,mkdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {validateContract,preflight,configs,validateCapture,matrixArgs,playwrightModules,noiseConfigurationSummary,gitArchive} from './visual-publish.mjs';
const contract=JSON.parse(readFileSync(new URL('../visual-baseline.json',import.meta.url)));
test('unresolved same-input interface is explicitly pending, never a visual PASS',()=>{
 assert.ok(validateContract({...contract,comparisonInput:{...contract.comparisonInput,status:'pending-fixture'}}).some(x=>x.startsWith('PENDING')));
});
test('bad baseline and missing width/theme contracts are rejected',()=>{
 for(const patch of [{baseline:{}},{viewports:[[390,844],[1440,900]]},{viewports:'invalid'},{themes:['dark']},{viewports:[[390,844],[390,844],[1440,900]]}]){
  assert.ok(validateContract({...contract,...patch}).length>0);
 }
});
test('source absence and nonexistent baseline remain separate explicit failures',()=>{
 const root=mkdtempSync(join(tmpdir(),'ajv-contract-'));
 try{
  mkdirSync(join(root,'agentjarvis/web'),{recursive:true});writeFileSync(join(root,'web/visual-baseline.json'),JSON.stringify({...contract,baseline:{...contract.baseline,commit:'0'.repeat(40)}}));
  const p=preflight(root);assert.ok(p.errors.some(x=>x.includes('module')));assert.ok(p.errors.some(x=>x.includes('Git source unavailable')));
 }finally{rmSync(root,{recursive:true,force:true});}
});
test('both sides missing the same scene cannot pass on a remaining PNG intersection',()=>{
 const root=mkdtempSync(join(tmpdir(),'ajv-scenes-'));
 try{
  const c={...contract,viewports:[[390,844]],themes:['light'],languages:['zh']};const cfg=configs(c)[0];
  const manifest={scenes:['chat-idle'],additionalDesktopScenes:[]},source={};
  for(const side of ['before','after']){
   const d=join(root,side);mkdirSync(d);writeFileSync(join(d,'source-before.json'),'{}');writeFileSync(join(d,'source-after.json'),'{}');
   writeFileSync(join(d,'result.json'),JSON.stringify({side,sourceStable:true,fixtureStable:true,auditEnabled:true,coverage:{scope:'full',completeMatrix:true,ok:true,requiredScenes:manifest},configs:[cfg],rows:[{...cfg,name:'chat-idle',ok:true,ownerAuditOK:true}]}));
   const r=validateCapture(d,source,c,manifest,()=>{throw Error('must not invent image');},side,'a'.repeat(64));
   assert.ok(r.errors.some(x=>x.includes('missing/invalid PNG')));assert.ok(r.errors.some(x=>x.includes('screenshot set incomplete')));
  }
 }finally{rmSync(root,{recursive:true,force:true});}
});
test('untrusted source hashes and incomplete configuration cannot qualify',()=>{
 const root=mkdtempSync(join(tmpdir(),'ajv-manifest-'));
 try{
  writeFileSync(join(root,'source-before.json'),'{}');writeFileSync(join(root,'source-after.json'),'{}');
  writeFileSync(join(root,'result.json'),JSON.stringify({side:'after',coverage:{ok:true,completeMatrix:true,scope:'full'},configs:[],rows:[]}));
  const r=validateCapture(root,{'app.js':'a'.repeat(64)},contract,{scenes:[],additionalDesktopScenes:[]},()=>{},'after','b'.repeat(64));
  assert.ok(r.errors.some(x=>x.includes('actual public bytes')));assert.ok(r.errors.some(x=>x.includes('width/theme/language')));
 }finally{rmSync(root,{recursive:true,force:true});}
});

test('publication invokes two critical scopes, never the all-page mode',()=>{
 const args=matrixArgs({root:'/frozen',source:'/public',out:'/out',side:'before',contract:{comparisonInput:{matrixFlag:'--comparison-inputs',file:'fixture.json'}},dashboardSource:'/dashboard'});
 assert.equal(args[args.indexOf('--profile')+1],'publish-critical');assert.ok(!args.includes('--mode'));assert.equal(args[args.indexOf('--dashboard-source')+1],'/dashboard');
 const code=readFileSync(new URL('./visual-publish.mjs',import.meta.url),'utf8');assert.ok(!code.includes('before-repeat'));assert.equal(configs(contract).length,12);assert.equal(contract.criticalScenes.length,42);
 const na=configs(contract).filter(x=>x.width>390).length;assert.equal(12*42-na,496);
});

test('desktop iOS omission needs the exact existing product reason, never an arbitrary N/A',()=>{
 const root=mkdtempSync(join(tmpdir(),'ajv-na-'));
 try{
  const c={...contract,criticalScenes:['settings-ios'],viewports:[[834,1112]],themes:['light'],languages:['zh']},cfg=configs(c)[0];
  writeFileSync(join(root,'source-before.json'),'{}');writeFileSync(join(root,'source-after.json'),'{}');
  writeFileSync(join(root,'result.json'),JSON.stringify({side:'before',sourceStable:true,fixtureStable:true,auditEnabled:true,coverage:{scope:'publish-critical',completeMatrix:true,ok:true,requiredScenes:{scenes:['settings-ios']}},configs:[cfg],rows:[{...cfg,name:'settings-ios',ok:true,notApplicable:'not captured'}]}));
  const r=validateCapture(root,{},c,{scenes:['settings-ios']},()=>{},'before','a'.repeat(64));assert.ok(r.errors.some(x=>x.includes('invalid product N/A')));
 }finally{rmSync(root,{recursive:true,force:true});}
});

test('actual runner resolves both installed Playwright directory and module-file contracts',()=>{
 const root=mkdtempSync(join(tmpdir(),'ajv-pw-'));
 try{const modules=join(root,'node_modules'),pw=join(modules,'playwright');mkdirSync(pw,{recursive:true});writeFileSync(join(pw,'index.mjs'),'export {};');
  assert.equal(playwrightModules({AJ_PLAYWRIGHT_MODULE:pw}),modules);
  assert.equal(playwrightModules({AJ_PLAYWRIGHT_MODULE:join(pw,'index.mjs')}),modules);
  assert.equal(playwrightModules({P577_NODE_MODULES:'/explicit/modules',AJ_PLAYWRIGHT_MODULE:pw}),'/explicit/modules');
 }finally{rmSync(root,{recursive:true,force:true});}
});

test('1012 envelope contract rejects stale pixel policy and configuration summary preserves uncertainty',()=>{
 for(const acceptance of [undefined,{...contract.acceptance,minimumSamples:2},{...contract.acceptance,outsidePixels:1},{...contract.acceptance,uncertainFraction:0.01}])assert.ok(validateContract({...contract,acceptance}).some(x=>x.includes('1012')));
 const summary=noiseConfigurationSummary({sampleCount:3,rows:[{name:'390-light-zh-chat-idle.png',envelopePixels:16,envelopeFraction:16/(390*844),outsidePixels:0,verdict:'PASS'},{name:'390-light-zh-reply-active.png',envelopePixels:2000,envelopeFraction:0.006,outsidePixels:0,verdict:'UNCERTAIN'}]},contract);
 assert.equal(summary[0].sampleCount,3);assert.equal(summary[0].envelopePixels,2016);assert.equal(summary[0].verdict,'UNCERTAIN');
});

import {execFileSync} from 'node:child_process';
test('real Git archive over1MiB fails the old default buffer but the bounded source archive helper preserves every byte',()=>{
 const temp=mkdtempSync(join(tmpdir(),'ajv-large-archive-'));
 try{const git=(...args)=>execFileSync('git',args,{cwd:temp,stdio:['ignore','pipe','pipe']});git('init','-q');const bytes=Buffer.alloc(1200000,7);writeFileSync(join(temp,'large-fixture.bin'),bytes);git('add','large-fixture.bin');git('-c','user.name=Synthetic QA','-c','user.email=synthetic@example.invalid','commit','-qm','fixture');assert.throws(()=>git('archive','HEAD','large-fixture.bin'),error=>error.code==='ENOBUFS');const actual=gitArchive(temp,'HEAD','large-fixture.bin');assert.ok(actual.length>1024*1024);const expected=execFileSync('git',['archive','HEAD','large-fixture.bin'],{cwd:temp,maxBuffer:2*1024*1024});assert.ok(actual.equals(expected));assert.equal(execFileSync('git',['show','HEAD:large-fixture.bin'],{cwd:temp,maxBuffer:2*1024*1024}).equals(bytes),true);}
 finally{rmSync(temp,{recursive:true,force:true});}
});
