#!/usr/bin/env node
import {collectExecutionResources,chooseExecutionJobs} from './execution-resources.mjs';
import {mkdirSync,writeFileSync,readFileSync,readdirSync,existsSync,createWriteStream,statSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {parseArgs} from 'node:util';
export function pairConfigurations(phase){if(phase.profile&&phase.profile!=='publish-critical')throw Error('Unknown pair profile');if(!['full','touch'].includes(phase.mode||'full'))throw Error('Unknown pair mode');if(phase.profile&&phase.mode==='touch')throw Error('Critical is separate from touch');return phase.profile?12:phase.mode==='touch'?4:16;}
export function pairPasses(phase){const n=phase.baselineSamples??1;if(!Number.isInteger(n)||(n!==1&&n<3))throw Error('Baseline samples must be 1 (legacy) or at least 3');if(n>1&&phase.profile!=='publish-critical')throw Error('Baseline envelope samples are restricted to publish-critical');return [...Array.from({length:n},(_,index)=>({label:index===0?'before':'baseline-'+index,captureSide:'before',sampleIndex:index})),{label:'after',captureSide:'after',sampleIndex:null}];}
export function validatePairPhases(phases){
 if(!Array.isArray(phases)||!phases.length)throw Error('At least one explicit pair phase is required');const names=new Set(),outputs=new Set();
 return phases.map(p=>{if(!p.name||names.has(p.name))throw Error('Unique phase name required');names.add(p.name);for(const k of ['sourceBefore','sourceAfter','fixtures','out'])if(typeof p[k]!=='string'||!p[k])throw Error('Missing pair phase '+k);const q={...p,mode:p.mode||'full'};for(const k of ['sourceBefore','sourceAfter','fixtures','out','dashboardSource','dashboardSourceBefore','comparisonInputs','versionInput','criticalScenes'])if(q[k])q[k]=resolve(q[k]);if(outputs.has(q.out))throw Error('Duplicate phase output');outputs.add(q.out);if(q.profile&&(!q.comparisonInputs||!q.dashboardSource))throw Error('Critical requires explicit comparison input and Dashboard bytes');q.baselineSamples=q.baselineSamples??1;pairPasses(q);q.total=pairConfigurations(q);return q;});
}
export function capturePassArguments(job,pass){
 const side=pass.label,captureSide=pass.captureSide,args=['--source',job[captureSide==='before'?'sourceBefore':'sourceAfter'],'--out',join(job.directory,side),'--fixtures',job.fixtures,'--side',captureSide,'--mode',job.mode];
 if(['full','touch'].includes(job.mode))args.push('--config',String(job.index));else for(const key of ['width','theme','lang'])if(job[key])args.push('--'+key,String(job[key]));
 if(job.audit!==false)args.push('--audit');
 for(const [key,value]of Object.entries({modules:job.modules,chromium:job.chromium,'version-input':job.versionInput,profile:job.profile,'critical-scenes':job.criticalScenes,'comparison-inputs':job.comparisonInputs,'dashboard-source':captureSide==='before'?(job.dashboardSourceBefore||job.dashboardSource):job.dashboardSource}))if(value)args.push('--'+key,value);
 return args;
}
export function safeSceneProgress(line){return /^\d+-(?:light|dark)-(?:zh|en) [a-z0-9-]+ PASS$/.test(line)?line:null;}
export function sceneProgress(phases,results,now=Date.now()){
 return phases.map(p=>{const counts=Object.fromEntries(pairPasses(p).map(x=>[x.label,0]));let newest=0;for(let i=0;i<p.total;i++)for(const {label:side} of pairPasses(p)){const dir=join(p.out,'pairs',String(i).padStart(2,'0'),side);if(!existsSync(dir))continue;for(const file of readdirSync(dir).filter(x=>x.endsWith('.png'))){counts[side]++;newest=Math.max(newest,statSync(join(dir,file)).mtimeMs);}}
 const rows=results.filter(r=>r?.phase===p.name);return{event:'visual-progress',phase:p.profile?'critical':p.mode,beforePng:counts.before,afterPng:counts.after,baselinePngs:Object.fromEntries(pairPasses(p).filter(x=>x.captureSide==='before').map(x=>[x.label,counts[x.label]])),baselineSamples:p.baselineSamples??1,finishedConfigurations:rows.length,failedConfigurations:rows.filter(r=>r.code!==0).length,totalConfigurations:p.total,newestProductPngAgeSeconds:newest?Math.max(0,Math.floor((now-newest)/1000)):null};});
}
function command(args,{env,log,label='assembly'}){return new Promise((done,reject)=>{const stream=createWriteStream(log);const child=spawn(process.execPath,args,{env,stdio:['ignore','pipe','pipe']});child.stdout.pipe(stream,{end:false});child.stderr.pipe(stream,{end:false});let pending='';const forward=line=>{const safe=safeSceneProgress(line);if(safe)console.log('['+label+'] '+safe);};child.stdout.on('data',chunk=>{pending+=chunk.toString();let n;while((n=pending.indexOf('\n'))>=0){forward(pending.slice(0,n).replace(/\r$/,''));pending=pending.slice(n+1);}});child.once('error',e=>{stream.end();reject(e)});child.once('exit',(code,signal)=>{if(pending)forward(pending);stream.end();done({code:code??1,signal})});});}
export async function runPairPhases({root,phases,modules,chromium,jobs='auto',env=process.env}){
 const checked=validatePairPhases(phases),scriptRoot=resolve(root,'web/test/p577');
 for(const p of checked)for(const {label:side} of pairPasses(p))if(existsSync(join(p.out,side))&&readdirSync(join(p.out,side)).length)throw Error('Refusing stale pair side output: '+join(p.out,side));
 const resources=collectExecutionResources(),policy=chooseExecutionJobs(resources,{requested:jobs});
 const queue=checked.flatMap(p=>Array.from({length:p.total},(_,index)=>({phase:p,index}))),results=new Array(queue.length);let next=0;
 for(const p of checked){mkdirSync(join(p.out,'pairs'),{recursive:true});writeFileSync(join(p.out,'execution-resources.json'),JSON.stringify({resources,...policy,scheduling:'one-shared-pool-across-all-phases'},null,2)+'\n');}
 async function worker(){while(next<queue.length){const ordinal=next++,{phase:p,index}=queue[ordinal],directory=join(p.out,'pairs',String(index).padStart(2,'0'));if(existsSync(directory))throw Error('Refusing stale pair configuration: '+directory);mkdirSync(directory);const job={...p,index,directory,scriptRoot,modules:modules&&resolve(modules),chromium:chromium&&resolve(chromium)};const jobFile=join(directory,'job.json');writeFileSync(jobFile,JSON.stringify(job,null,2)+'\n');const status=await command([join(scriptRoot,'pair-worker.mjs'),jobFile],{env,log:join(directory,'capture.log'),label:(p.profile?'critical':p.mode)+'/'+String(index).padStart(2,'0')});results[ordinal]={phase:p.name,index,directory,...status};writeFileSync(join(p.out,'pair-progress.json'),JSON.stringify(results.filter(r=>r?.phase===p.name),null,2)+'\n');}}
 const heartbeat=setInterval(()=>{for(const row of sceneProgress(checked,results))console.log(JSON.stringify(row));},30000);
 try{await Promise.all(Array.from({length:Math.min(policy.chosenJobs,queue.length)},worker));}finally{clearInterval(heartbeat);for(const row of sceneProgress(checked,results))console.log(JSON.stringify(row));}
 const errors=results.filter(r=>r.code!==0),assemblies=[];
 // Assembly reuses the original matrix validator only; it cannot launch missing captures.
 for(const p of checked)for(const {label:side,captureSide} of pairPasses(p)){
  const args=[join(scriptRoot,'matrix.mjs'),'--source',p[captureSide==='before'?'sourceBefore':'sourceAfter'],'--out',join(p.out,side),'--side',captureSide,'--mode',p.mode,'--fixtures',p.fixtures,'--audit','--assemble-only','--jobs','1'];
  for(const [key,value] of Object.entries({modules,chromium,profile:p.profile,'version-input':p.versionInput,'comparison-inputs':p.comparisonInputs,'critical-scenes':p.criticalScenes,'dashboard-source':captureSide==='before'?(p.dashboardSourceBefore||p.dashboardSource):p.dashboardSource}))if(value)args.push('--'+key,value);
  for(let i=0;i<p.total;i++)args.push('--reuse',join(p.out,'pairs',String(i).padStart(2,'0'),side));
  const status=await command(args,{env,log:join(p.out,'assembly-'+side+'.log')});assemblies.push({phase:p.name,side,captureSide,...status});if(status.code!==0)errors.push(assemblies.at(-1));
 }
 const receipt={schema:'p577-pair-phases-v1',topology:'one-owned-page-per-configuration-new-documents-per-side',executionResources:{resources,...policy},phases:checked.map(p=>({name:p.name,mode:p.mode,profile:p.profile||null,configurations:p.total,baselineSamples:p.baselineSamples,passes:pairPasses(p),out:p.out})),results,assemblies,ok:errors.length===0,errors};
 for(const p of checked)writeFileSync(join(p.out,'pair-phases.json'),JSON.stringify(receipt,null,2)+'\n');
 if(errors.length)throw Error('Pair capture or immutable matrix assembly failed; original logs/receipts retained: '+JSON.stringify(errors));
 return receipt;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){const{values:o}=parseArgs({options:{root:{type:'string'},plan:{type:'string'},modules:{type:'string'},chromium:{type:'string'},jobs:{type:'string',default:'auto'}}});if(!o.root||!o.plan)throw Error('pair-matrix.mjs --root <repository> --plan <JSON phases> [--modules <existing node_modules>] [--chromium <binary>] [--jobs auto|1..16]');const plan=JSON.parse(readFileSync(resolve(o.plan)));await runPairPhases({...o,phases:Array.isArray(plan)?plan:plan.phases});}
