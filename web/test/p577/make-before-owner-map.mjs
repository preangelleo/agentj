#!/usr/bin/env node
// Source-equivalent ownership only; never adds attributes to the before product.
import {gunzipSync,gzipSync} from 'node:zlib';
import {readdirSync,readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {createHash} from 'node:crypto';
const [dirArg,beforeCombined,outArg,...flags]=process.argv.slice(2);
if(!dirArg||!beforeCombined||!outArg)throw Error('Usage: make-before-owner-map.mjs <after-runtime-dir> <before-combined> <output> [--sharded]');
const dir=resolve(dirArg),output=resolve(outArg),sharded=flags.includes('--sharded');
const key=(s,n)=>[s.scene,s.viewport?.width,s.viewport?.height,s.theme,s.lang,s.state||'default',n.path].join('|');
const sampleKey=s=>[s.scene,s.viewport?.width,s.viewport?.height,s.theme,s.lang,s.state||'default'].join('|');
const sha=buffer=>createHash('sha256').update(buffer).digest('hex');
let total=0,entryCount=0,afterCombined=null;
const errors=[],allEntries={},shards={};
if(sharded)mkdirSync(output,{recursive:true});
for(const file of readdirSync(dir).filter(x=>/\.json(?:\.gz)?$/.test(x)).sort()){
  const bytes=readFileSync(join(dir,file)),s=JSON.parse(file.endsWith('.gz')?gunzipSync(bytes):bytes),entries={},localErrors=[];
  total++;
  if(s.side!=='after'||!s.sourceCombined||!s.screenshotSha256)localErrors.push('After source/screenshot binding absent');
  if(!s.nativeControlEvidence?.records?.length||s.nativeControlEvidence.conflicts?.length||s.nativeControlEvidence.errors?.length)localErrors.push('Missing/conflicting native control evidence prevents source-equivalent owner sidecar');
  if(afterCombined&&afterCombined!==s.sourceCombined)localErrors.push('Mixed after sources');
  afterCombined=s.sourceCombined;
  for(const name of ['directMissing','unknownOwners','semanticMismatches','actionContextMissing'])if(s[name]?.length)localErrors.push(`${name}: ${s[name].length}`);
  if(s.nodes){const paths=new Set();for(const [id,node]of Object.entries(s.nodes)){if(!id||node.nodeId!==id||typeof node.path!=='string'||!node.path||paths.has(node.path))localErrors.push('Invalid or duplicate node dictionary key/path: '+id);paths.add(node.path);}}
  const all=[...(s.ownerTargets||s.controls||[]),...(s.rules||[]).flatMap(r=>[...(r.matches||[]),...(r.descendants||[])])];
  for(const ref of all){
    const hasId=Object.prototype.hasOwnProperty.call(ref,'nodeId');
    if(s.nodes&&(!hasId||typeof ref.nodeId!=='string'||!ref.nodeId||!Object.prototype.hasOwnProperty.call(s.nodes,ref.nodeId))){localErrors.push('Invalid/missing dictionary node reference');continue;}
    const n=s.nodes?s.nodes[ref.nodeId]:ref;
    if(!n){localErrors.push('Missing node dictionary reference: '+ref.nodeId);continue;}
    const owner=n.owner||n.canonicalRoot?.owner||n.registeredIdOwner||n.inheritedOwner;
    if(!owner||!n.path)continue;
    const k=key(s,n),entry={owner,method:n.owner?'direct-source-equivalent':n.canonicalRoot?.owner?'manual-canonical-root-source-equivalent':n.registeredIdOwner?'registered-id-source-equivalent':'content-inherits-explicit-source-owner',afterPath:n.path,afterScreenshot:s.screenshot,afterScreenshotSha256:s.screenshotSha256};
    if(entries[k]&&entries[k].owner!==owner)localErrors.push('Conflicting path ownership: '+n.path);
    entries[k]=entry;
  }
  entryCount+=Object.keys(entries).length;
  const proof={schemaVersion:1,sourceCombined:beforeCombined,afterSourceCombined:afterCombined,samples:1,valid:localErrors.length===0,entries,errors:localErrors};
  if(sharded){
    const name=file.replace(/\.json(?:\.gz)?$/,'.owners.json.gz'),compressed=gzipSync(JSON.stringify(proof));
    writeFileSync(join(output,name),compressed);
    const k=sampleKey(s);if(shards[k])localErrors.push('Duplicate scene/state key');
    shards[k]={file:name,sha256:sha(compressed),snapshotSha256:sha(bytes),valid:proof.valid,entries:Object.keys(entries).length};
  }else Object.assign(allEntries,entries);
  errors.push(...localErrors.map(error=>({file,error})));
}
const result={schemaVersion:1,sourceCombined:beforeCombined,afterSourceCombined:afterCombined,samples:total,entryCount,valid:total>0&&errors.length===0,...(sharded?{shards}:{entries:allEntries}),errors,note:'Exact per-scene/config DOM paths. Before raw missing metadata is retained; unmatched paths fail. No markup is changed.'};
writeFileSync(sharded?join(output,'index.json'):output,JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({valid:result.valid,samples:total,entries:entryCount,errors:errors.length,sharded}));
if(!result.valid)process.exitCode=1;
