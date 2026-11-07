#!/usr/bin/env node
// One owned Chromium process, two sequential fresh documents in one Page, one configuration.
import {createHash}from'node:crypto';
import {pairPasses,capturePassArguments}from'./pair-matrix.mjs';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {requireTool} from './dependencies.mjs';
import {launchOwnedBrowser,withCaptureSession} from './owned-browser-session.mjs';
const job=JSON.parse(readFileSync(resolve(process.argv[2])));
if(job.modules)process.env.P577_NODE_MODULES=resolve(job.modules);
const {chromium}=requireTool('playwright');
const browser=await launchOwnedBrowser({chromium,executablePath:job.chromium||process.env.CHROMIUM||chromium.executablePath()});
const sides=[],passes=pairPasses(job),envelope=(job.baselineSamples??1)>1;let failed=false;
const fileBinding=file=>{try{const bytes=readFileSync(join(job.directory,file));return {file,sha256:createHash('sha256').update(bytes).digest('hex')};}catch{return null;}};
const cdp=await browser.newBrowserCDPSession();
const browserProcessId=(await cdp.send('SystemInfo.getProcessInfo')).processInfo.find(p=>p.type==='browser')?.id;await cdp.detach();
if(!browserProcessId)throw Error('Owned browser process identity unavailable');
try{
 for(const pass of passes){const side=pass.label,captureSide=pass.captureSide;
  const out=join(job.directory,side);mkdirSync(out,{recursive:true});
  const args=capturePassArguments(job,pass);
  process.exitCode=0;let error=null;
  try{await withCaptureSession(browser,args,()=>import(pathToFileURL(join(job.scriptRoot,'capture.mjs')).href+'?pair='+side+'-'+sides.length));}catch(e){error=String(e);console.error(e);}
  const code=error?1:Number(process.exitCode||0);failed||=code!==0;
  const result=(()=>{try{return JSON.parse(readFileSync(join(out,'result.json')))}catch{return null}})();
  sides.push({side,...(envelope?{captureSide,sampleIndex:pass.sampleIndex,source:result?.source,captureReceipt:result?fileBinding(side+'/result.json'):null,sourceManifests:result?{before:fileBinding(side+'/source-before.json'),after:fileBinding(side+'/source-after.json')}:null}:{}),code,error,contextsAfter:browser.contexts().length,isolation:result?.pairedCapture?.isolation,environmentFingerprint:result?.environmentFingerprint,sourceCombined:result?.sourceCombined,sourceStable:result?.sourceStable,fixtureStable:result?.fixtureStable});
  if(error||browser.contexts().length!==1)break;
 }
 if(sides.length!==passes.length||!sides[0]?.environmentFingerprint||sides.some(s=>envelope&&(!s.captureReceipt||!s.sourceManifests?.before||!s.sourceManifests?.after)||!s.isolation?.cleanup?.complete||!s.isolation.rendererProcessId||s.isolation.contextId!==sides[0].isolation.contextId||s.isolation.pageTargetId!==sides[0].isolation.pageTargetId||s.isolation.rendererProcessId!==sides[0].isolation.rendererProcessId||s.environmentFingerprint!==sides[0].environmentFingerprint)||new Set(sides.map(s=>s.isolation?.loaderId)).size!==passes.length||new Set(sides.map(s=>s.isolation?.documentNonce)).size!==passes.length)failed=true;
}finally{await browser.close();}
writeFileSync(join(job.directory,'pair.json'),JSON.stringify({schema:envelope?'p577-owned-page-envelope-v3':'p577-owned-page-pair-v2',...(envelope?{baselineSamples:job.baselineSamples}:{}),topology:'one-owned-page-new-documents-per-side',browserProcessId,contextId:sides[0]?.isolation?.contextId,pageTargetId:sides[0]?.isolation?.pageTargetId,index:job.index,mode:job.mode,profile:job.profile||null,sides,ok:!failed},null,2)+'\n');
process.exitCode=failed?1:0;
