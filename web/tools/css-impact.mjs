#!/usr/bin/env node
// Source + runtime impact report. Missing/unsupported state coverage is an error, never an empty green report.
import {readFileSync,readdirSync,writeFileSync,existsSync,mkdirSync,statSync} from 'node:fs';
import {join,relative,resolve,dirname,basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {rules,declarations,stripComments} from './css-syntax.mjs';
import {pngEvidence} from './png-evidence.mjs';
import {validateMatrixConfigs,configKey} from '../test/p577/matrix-contract.mjs';
import{auditTokenWriterMigration}from'./css-token-writers.mjs';
import{nativeImpactContext,nativeImpactVerifier}from'./native-impact.mjs';
import {gunzipSync,gzipSync} from 'node:zlib';
const webRoot=fileURLToPath(new URL('../',import.meta.url));
const nativeContext=await nativeImpactContext(webRoot.replace(/\/$/,''));
const ownerContracts=JSON.parse(readFileSync(join(webRoot,'component-ownership.json'),'utf8'));
const writerPolicy=JSON.parse(readFileSync(join(webRoot,'token-writer-policy.json'),'utf8'));
const knownOwners=new Set([...JSON.parse(readFileSync(join(webRoot,'control-ownership-sites.json'),'utf8')).owners,...ownerContracts.components.map(c=>c.namespace),...Object.keys(ownerContracts.ownerFamilies||{})]);
export function canonicalSelector(source){let out='',quote='',space=false;source=stripComments(source);for(let i=0;i<source.length;i++){const c=source[i];if(quote){out+=c;if(c==='\\'&&i+1<source.length){out+=source[++i];continue;}if(c===quote)quote='';continue;}if(c==='"'||c==="'"){if(space&&out&&!/[>+~,]$/.test(out))out+=' ';space=false;out+=c;quote=c;continue;}if(c==='['){let q='',end=i+1;for(;end<source.length;end++){if(q){if(source[end]==='\\'){end++;continue;}if(source[end]===q)q='';continue;}if(source[end]==='"'||source[end]==="'")q=source[end];else if(source[end]===']')break;}const raw=source.slice(i+1,end),match=/^(\s*[-\w]+\s*[~|^$*]?=\s*)(["'])([-\w]+)\2(\s+[isIS])?\s*$/.exec(raw);if(space&&out&&!/[>+~,]$/.test(out))out+=' ';space=false;out+='['+(match?(match[1].replace(/\s+/g,'')+match[3]+(match[4]||'').toLowerCase()):raw.trim())+']';i=end;continue;}if(/\s/.test(c)){space=true;continue;}if(/[>+~,]/.test(c)){out=out.trimEnd()+c;space=false;continue;}if(space&&out&&!/[>+~,]$/.test(out))out+=' ';space=false;out+=c;}return out.trim();}
const inherited=new Set(['color','font','font-family','font-size','font-weight','font-style','line-height','letter-spacing','word-spacing','text-align','white-space','visibility','cursor','direction']);
const walk=d=>readdirSync(d,{withFileTypes:true}).flatMap(x=>x.isDirectory()?(x.name==='vendor'?[]:walk(join(d,x.name))):x.name.endsWith('.css')?[join(d,x.name)]:[]);
export function inventory(root){const map=new Map();for(const file of walk(root)){const src=readFileSync(file,'utf8');const occurrences=new Map();for(const r of rules(src)){const occurrenceKey=JSON.stringify([r.context,r.head]),occurrence=occurrences.get(occurrenceKey)||0;occurrences.set(occurrenceKey,occurrence+1);const key=JSON.stringify([relative(root,file),r.context,r.head,occurrence]);map.set(key,{file:relative(root,file),selector:r.head,context:r.context,declarations:r.head.startsWith('@')?[]:declarations(r.body),atRule:r.head.startsWith('@'),rawBody:r.head.startsWith('@')?r.body:undefined,line:src.slice(0,r.start).split('\n').length});}}return map;}
export function changedRules(before,after){const a=inventory(before),b=inventory(after),changes=[];for(const key of new Set([...a.keys(),...b.keys()])){const x=a.get(key),y=b.get(key);if(JSON.stringify([x?.declarations,x?.rawBody])!==JSON.stringify([y?.declarations,y?.rawBody]))changes.push({before:x,after:y});}const common=new Set([...a.keys()].filter(k=>b.has(k)));const ao=[...a.keys()].filter(k=>common.has(k)),bo=[...b.keys()].filter(k=>common.has(k));if(JSON.stringify(ao)!==JSON.stringify(bo))changes.push({kind:'cascade-order',beforeOrder:ao,afterOrder:bo});return changes;}
/** Pass to Playwright page.evaluate. Captures real CSSOM selectors, matching elements, owners, inherited descendants, and stylesheet conditions. */
export function runtimeSnapshot(){
 const states=[],errors=[],records=[];
 const name=n=>({tag:n.localName,id:n.id||null,classes:[...n.classList],owner:n.dataset.ajOwner||null,ancestorOwner:n.closest('[data-aj-owner]')?.dataset.ajOwner||null});
 const visit=(rs,contexts=[])=>{for(const r of rs){if(r.styleSheet){try{visit(r.styleSheet.cssRules,contexts);}catch(e){errors.push(String(e));}continue;}if(r.cssRules){visit(r.cssRules,[...contexts,r.conditionText||r.name||r.constructor.name]);continue;}if(!r.selectorText)continue;
  const pseudos=[...r.selectorText.matchAll(/::([a-z-]+)/g)].map(x=>'::'+x[1]);const origin=r.selectorText.replace(/::[a-z-]+(?:\([^)]*\))?/g,'');
  let hits=[];try{hits=[...document.querySelectorAll(origin)];}catch(e){errors.push({selector:r.selectorText,error:String(e)});continue;}
  const inheritedProps=[...r.style].filter(p=>p.startsWith('--')||['color','font','font-family','font-size','font-weight','line-height','text-align','white-space','visibility','cursor'].includes(p));
  const affected=[...new Set(hits.flatMap(n=>inheritedProps.length?[n,...n.querySelectorAll('*')]:[n]))];
  records.push({selector:r.selectorText,contexts,properties:[...r.style],pseudos:pseudos.flatMap(pseudo=>hits.map(n=>({origin:name(n),pseudo,computed:Object.fromEntries([...r.style].map(p=>[p,getComputedStyle(n,pseudo).getPropertyValue(p)]))}))),hits:hits.map(name),affected:affected.map(name)});
 }};
 for(const sheet of document.styleSheets)try{visit(sheet.cssRules);}catch(e){errors.push(String(e));}
 const missing=[...document.querySelectorAll('button,select,input,textarea,a[href],summary,[tabindex],[role="button"],[role="tab"]')].filter(n=>!n.dataset.ajOwner).map(name);
 return {url:location.pathname,viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},theme:document.documentElement.dataset.theme,language:document.documentElement.lang,records,missingOwner:missing,errors};
}
export function impact(before,after,samples=[],options={}){
 const changes=changedRules(before,after),problems=[],affectedIndex=new Map();let sampleCount=0,affectedRecords=0,matchedTotal=0;const seenSelectors=new Set();
 let problemCount=0;const problemCounts={};const pushProblem=Array.prototype.push.bind(problems);problems.push=(...entries)=>{for(const entry of entries){problemCount++;const kind=typeof entry==='string'?entry:entry.error||entry.field||'unknown-owner';problemCounts[kind]=(problemCounts[kind]||0)+1;options.onProblem?.(entry);if(problems.length<100)pushProblem(entry);}return problemCount;};
 const snapshotSummaries=[];

 const sha=b=>createHash('sha256').update(b).digest('hex');
 const assetWalk=d=>readdirSync(d,{withFileTypes:true}).flatMap(x=>x.isDirectory()?(x.name==='.preview'?[]:assetWalk(join(d,x.name))):[join(d,x.name)]);
 const manifests={},assetHashes={};for(const [side,root]of[['before',before],['after',after]]){try{const m=JSON.parse(readFileSync(join(root,'version.json'),'utf8'));if(!/^[0-9a-f]{64}$/.test(m.combined)||!m.files)throw Error('Invalid asset manifest');const listed=Object.keys(m.files).sort(),actual=assetWalk(root).map(p=>relative(root,p).split('\\').join('/')).filter(p=>!['version.json','version.js'].includes(p)).sort();if(JSON.stringify(listed)!==JSON.stringify(actual))throw Error('Asset inventory does not match manifest');if(sha(listed.map(p=>m.files[p]+'  '+p+'\n').join(''))!==m.combined)throw Error('Combined asset SHA mismatch');for(const [path,digest]of Object.entries(m.files))if(sha(readFileSync(join(root,path)))!==digest)throw Error('Asset SHA mismatch: '+path);manifests[side]=m.combined;assetHashes[side]=m.files;}catch(e){problems.push({side,error:'Unbound source: '+String(e)});}}
 const nonCssChanges=[...new Set([...Object.keys(assetHashes.before||{}),...Object.keys(assetHashes.after||{})])].filter(path=>!path.endsWith('.css')&&assetHashes.before?.[path]!==assetHashes.after?.[path]).map(path=>({path,beforeSha256:assetHashes.before?.[path]||null,afterSha256:assetHashes.after?.[path]||null}));
 const writerAudit=auditTokenWriterMigration(before,after,writerPolicy);for(const lost of writerAudit.lostWriters)problems.push({token:lost.name,retainedWriters:lost.retainedWriters,previousReaders:lost.previousReaders,error:'A component token writer lost its CSS consumer during migration.'});for(const site of writerAudit.afterUnresolvedDynamic)problems.push({...site,error:'Unreviewed dynamic JS setter prevents writer-migration qualification; exact source review required.'});const writerMigration={literalLossQualified:writerAudit.literalLossQualified,afterDynamicReviewQualified:writerAudit.afterDynamicReviewQualified,lostWriters:writerAudit.lostWriters,dynamicSites:writerAudit.after.dynamicSetters,unresolvedDynamic:writerAudit.afterUnresolvedDynamic,scope:writerAudit.scope};
 const unsupportedCssChanges=changes.filter(c=>c.kind==='cascade-order'||c.before?.atRule||c.after?.atRule);

 if(!changes.length){for(const path of new Set([...Object.keys(assetHashes.before||{}),...Object.keys(assetHashes.after||{})]))if(path.endsWith('.css')&&assetHashes.before?.[path]!==assetHashes.after?.[path])unsupportedCssChanges.push({kind:'unexplained-css-bytes',path});}
 const nativeVerifier=nativeImpactVerifier(after,manifests.after,nativeContext,entry=>problems.push(entry));
 const coverage=options.coverageManifest;
 if(!coverage||coverage.schemaVersion!==1||!Array.isArray(coverage.scenarios)||!coverage.scenarios.length||!Array.isArray(coverage.unusedRules))problems.push('Qualified coverage manifest required: schemaVersion=1, nonempty scenarios, explicit unusedRules[].');
 const sampleKeys=new Set(),sampleEvidence=new Map();
 const sampleKey=s=>[s.scene,s.viewport?.width,s.viewport?.height,s.theme,s.lang,s.state||'default'].join('|');
 let activeBeforeOwnerMap=options.beforeOwnerMap,ownerCache=new WeakMap();const loadedSidecars=new Map();
 const sidecarValid=()=>activeBeforeOwnerMap?.valid===true&&activeBeforeOwnerMap?.sourceCombined===manifests.before&&activeBeforeOwnerMap?.afterSourceCombined===manifests.after;
 const sidecarOwner=(sample,node)=>sample.side==='before'&&(node.elementKey||node.path)&&sidecarValid()?activeBeforeOwnerMap.entries?.[sampleKey(sample)+'|'+(node.elementKey||node.path)]?.owner:null;
 const technicalOwner=node=>{const tag=node.tag||node.localName,path=node.path||'';const inHead=/(?:^|>)\s*head(?:\:nth-of-type\(1\))?\s*(?:>|$)/.test(path);return node.visible===false&&((inHead&&['head','meta','link','base','title','script','style','template'].includes(tag))||['script','style','template'].includes(tag))?'@technical/document-resource':null;};
 const mappedOwner=(sample,node)=>{if(!node||typeof node!=='object')return null;if(ownerCache.has(node))return ownerCache.get(node);let owner=node.owner||(sample.side==='before'?sidecarOwner(sample,node):null)||node.canonicalRoot?.owner||(sample.side==='after'?node.registeredIdOwner:null)||node.inheritedOwner||technicalOwner(node)||null;if(owner&&!knownOwners.has(owner)&&!(owner==='@technical/document-resource'&&technicalOwner(node))){problems.push({scene:sample.scene,nodeId:node.nodeId,path:node.path,error:'Unregistered node owner cannot qualify impact.'});owner=null;}if(node.owner&&Array.isArray(node.expectedOwners)&&node.expectedOwners.length&&!node.expectedOwners.includes(node.owner)){problems.push({scene:sample.scene,nodeId:node.nodeId,path:node.path,error:'Direct owner violates the source-independent semantic contract.'});owner=null;}ownerCache.set(node,owner);return owner;};



 const tokenChanged=new Set(changes.flatMap(c=>[...(c.before?.declarations||[]),...(c.after?.declarations||[])].filter(d=>d.property.startsWith('--')).map(d=>d.property)));
 // Follow private and primitive token references transitively across both trees.
 const all=[...inventory(before).values(),...inventory(after).values()];let grew=true;while(grew){grew=false;for(const r of all)for(const d of r.declarations)if(d.property.startsWith('--')&&!tokenChanged.has(d.property)&&[...tokenChanged].some(t=>d.value.includes(t))){tokenChanged.add(d.property);grew=true;}}
 const selectors=new Set(changes.flatMap(c=>[c.before,c.after].filter(r=>r&&!r.atRule).map(r=>r.selector)));
 for(const r of all)if(r.declarations.some(d=>[...tokenChanged].some(t=>d.value.includes(t))))selectors.add(r.selector);
 const canonical=canonicalSelector;
 const canonSelectors=new Set([...selectors].map(canonical)), sides=new Set();
 for(const rawSample of samples){
  const sample={...(rawSample?.meta||{}),...rawSample};sampleCount++;ownerCache=new WeakMap();
  activeBeforeOwnerMap=sample.side==='before'&&options.loadBeforeOwnerMap?options.loadBeforeOwnerMap(sample):options.beforeOwnerMap;
  if(sample.side==='before'&&activeBeforeOwnerMap?._afterSnapshotSha256)loadedSidecars.set(sampleKey(sample),activeBeforeOwnerMap._afterSnapshotSha256);
  const hasDictionary=Object.hasOwn(sample,'nodes'), missingRefs=new Set(), paths=new Set();
  const refProblem=(key,error)=>{const id=String(key)+'|'+error;if(!missingRefs.has(id)){missingRefs.add(id);problems.push({scene:sample.scene,nodeId:key,error});}return{};};
  if(hasDictionary){if(!sample.nodes||typeof sample.nodes!=='object'||Array.isArray(sample.nodes)||sample.nodeDictionaryVersion!==2)refProblem(null,'Invalid node dictionary schema/version');else for(const [key,node]of Object.entries(sample.nodes)){if(!node||node.nodeId!==key||typeof node.path!=='string'||!node.path||paths.has(node.path))refProblem(key,'Node dictionary key/nodeId mismatch or missing/duplicate DOM path');else paths.add(node.path);}}
  const dereference=ref=>{if(!ref||typeof ref!=='object')return refProblem(null,'Invalid node reference');const explicit=Object.hasOwn(ref,'nodeId');if(hasDictionary){if(!explicit||typeof ref.nodeId!=='string'||!ref.nodeId)return refProblem(ref.nodeId,'Invalid explicit nodeId; dictionary references require a nonempty string');if(!sample.nodes||!Object.hasOwn(sample.nodes,ref.nodeId))return refProblem(ref.nodeId,'Node dictionary reference is missing; no owner fallback permitted.');return sample.nodes[ref.nodeId];}if(explicit)return refProblem(ref.nodeId,'Explicit nodeId without dictionary is not a legacy inline node');if(typeof ref.path!=='string'||!ref.path||typeof ref.tag!=='string')return refProblem(null,'Legacy inline node requires complete tag/path');return ref;};
  // Validate every element reference, including untouched rules and nonaffected controls.
  for(const field of ['controls','directMissing','missing','ownerTargets','semanticMismatches','nativeMedia','nativeFrames','excluded','unknownOwners','actionContextMissing'])if(Array.isArray(sample[field]))for(const ref of sample[field])dereference(ref);
  for(const rule of sample.rules||[])for(const refs of [rule.matches,rule.descendants])if(Array.isArray(refs))for(const ref of refs)dereference(ref);
  if(sample.side==='after')nativeVerifier.consume(sample,dereference);
  const sceneAffected=[];

  if(!sample||!Array.isArray(sample.rules)||!Array.isArray(sample.errors)||!Array.isArray(sample.directMissing??sample.missing)||!['before','after'].includes(sample.side)||!sample.scene||!sample.screenshot){problems.push({scene:sample?.scene,error:'Invalid runtime schema: require side before/after, scene, screenshot, rules[].matches, errors[], directMissing[] (or missing[]).'});continue;}
  sides.add(sample.side);const evidenceKey=sample.side+'|'+sampleKey(sample);if(sampleKeys.has(evidenceKey))problems.push({sample:evidenceKey,error:'Duplicate snapshot key'});sampleKeys.add(evidenceKey);sampleEvidence.set(evidenceKey,{sourceCombined:sample.sourceCombined,screenshotSha256:sample.screenshotSha256,snapshotSha256:rawSample._impactBytesSha||sha(JSON.stringify(rawSample)),snapshotFile:rawSample._impactFile||null});
  if(!sample.rules.length)problems.push({scene:sample.scene,error:'Empty runtime rule evidence cannot qualify impact.'});
  if(sample.sourceCombined!==manifests[sample.side])problems.push({scene:sample.scene,error:'Runtime/source combined SHA binding missing or mismatched.'});
  try{if(!sample.screenshotSha256)throw Error('Screenshot SHA missing');const path=resolve(options.screenshotRoot||'',sample.screenshot),png=readFileSync(path);if(sha(png)!==sample.screenshotSha256)throw Error('Screenshot SHA mismatch');const pixels=pngEvidence(png);if(!Number.isFinite(sample.viewport?.width)||!Number.isFinite(sample.viewport?.height)||pixels.width!==sample.viewport.width*(sample.viewport.dpr||1)||pixels.height!==sample.viewport.height*(sample.viewport.dpr||1))throw Error('Screenshot/viewport pixel dimensions mismatch');sample.verifiedPixels=pixels;sampleEvidence.get(evidenceKey).verifiedPixels=pixels;}catch(e){problems.push({scene:sample.scene,error:String(e)});}

  if(sample.errors.length)problems.push({scene:sample.scene,errors:sample.errors});
  for(const field of['unknownOwners','semanticMismatches','actionContextMissing'])if(sample[field]?.length)problems.push({scene:sample.scene,field,failures:sample[field]});
  const missing=(sample.directMissing??sample.missing).map(dereference).filter(node=>!sidecarOwner(sample,node));if(missing.length)problems.push({scene:sample.scene,side:sample.side,missingCount:missing.length,nodePaths:missing.map(n=>n.path||n.elementKey||n.id),error:'Required direct owner evidence missing; before side needs an exact source-equivalence entry.'});
  for(const [indexInSnapshot,r]of sample.rules.entries()){
   if(typeof r.selector!=='string'||!Array.isArray(r.matches)){problems.push({scene:sample.scene,error:'Invalid rule: selector/matches required'});continue;}
   if(!canonSelectors.has(canonical(r.selector))&&!nonCssChanges.length&&!unsupportedCssChanges.length)continue;
   const propagates=(r.properties||[]).some(p=>p.startsWith('--')||inherited.has(p));
   if(propagates&&!Array.isArray(r.descendants)){problems.push({scene:sample.scene,selector:r.selector,error:'Inherited declaration requires descendant owner evidence.'});}
   const elements=[...r.matches,...(r.descendants||[])].map(dereference);
   if(elements.some(node=>!mappedOwner(sample,node)))problems.push({scene:sample.scene,selector:r.selector,error:'Affected element has no source-bound owner evidence.'});
   if(!elements.length)continue;
   const owners=[...new Set(elements.map(x=>mappedOwner(sample,x)).filter(Boolean))];const path=r.assetPath||r.file||null;
   const row={side:sample.side,scene:sample.scene,snapshotKey:evidenceKey,screenshot:sample.screenshot,screenshotSha256:sample.screenshotSha256,verifiedPixels:sample.verifiedPixels,viewport:sample.viewport,theme:sample.theme,language:sample.lang,state:sample.state,impactReason:canonSelectors.has(canonical(r.selector))?'changed-css-or-token-consumer':(unsupportedCssChanges.length?'conservative-at-rule-or-cascade-change':'conservative-non-css-source-change'),assetPath:path,selector:r.selector,owners,matchCount:r.matches.length,descendantCount:(r.descendants||[]).length,snapshotFile:rawSample._impactFile||null,snapshotSha256:sampleEvidence.get(evidenceKey).snapshotSha256,rulePointer:'/rules/'+indexInSnapshot,nodeDictionaryPointer:sample.nodes?'/nodes':null};
   sceneAffected.push(row);affectedRecords++;matchedTotal+=r.matches.length;if(r.matches.length)seenSelectors.add(canonical(r.selector));
   const groupKey=JSON.stringify([path,r.selector]),group=affectedIndex.get(groupKey)||{assetPath:path,selector:r.selector,owners:new Set(),scenes:new Set(),observations:0,matchCount:0,descendantCount:0,examples:[]};owners.forEach(o=>group.owners.add(o));group.scenes.add(sample.scene);group.observations++;group.matchCount+=row.matchCount;group.descendantCount+=row.descendantCount;if(group.examples.length<2)group.examples.push(row);affectedIndex.set(groupKey,group);

  }
  options.onSampleAffected?.(sample,sceneAffected);snapshotSummaries.push({key:evidenceKey,side:sample.side,scene:sample.scene,sourceCombined:sample.sourceCombined,snapshotSha256:sampleEvidence.get(evidenceKey)?.snapshotSha256,matchedRecords:sceneAffected.length,nodeCount:sample.nodes?Object.keys(sample.nodes).length:null});options.onProgress?.({sampleCount,affectedRecords,problemCount,rss:process.memoryUsage().rss});
 }
 if(!sampleCount)problems.push('Runtime before/after snapshots missing; no component/page impact qualification.');
 for(const [key,sha]of loadedSidecars)if(sampleEvidence.get('after|'+key)?.snapshotSha256!==sha)problems.push({scenario:key,error:'Before-owner shard is not bound to the actual after runtime snapshot.'});
 if(!sides.has('before')||!sides.has('after'))problems.push('Both before and after runtime sides are required.');
 if(coverage?.scenarios)for(const scenario of coverage.scenarios){const key=typeof scenario==='string'?scenario:sampleKey(scenario);for(const side of['before','after'])if(!sampleKeys.has(side+'|'+key))problems.push({side,scenario:key,error:'Required scene/state sample missing.'});}
 for(const key of sampleKeys){const other=(key.startsWith('before|')?'after|':'before|')+key.slice(key.indexOf('|')+1);if(!sampleKeys.has(other))problems.push({sample:key,error:'Before/after scene/state sets differ.'});}
 const seen=seenSelectors;
 for(const selector of selectors)if(!seen.has(canonical(selector))){const expectedKeys=new Set((coverage?.scenarios||[]).flatMap(scenario=>{const key=typeof scenario==='string'?scenario:sampleKey(scenario);return['before|'+key,'after|'+key];}));
 const waived=(coverage?.unusedRules||[]).find(x=>{
  if(x.selector!==selector||!['not-instantiated','media-inapplicable'].includes(x.category)||!x.reason||x.sourceCombined?.before!==manifests.before||x.sourceCombined?.after!==manifests.after||!Array.isArray(x.evidenceSnapshots)||x.evidenceSnapshots.length!==expectedKeys.size||!Array.isArray(x.sourceEvidence)||!x.sourceEvidence.length)return false;
  const refs=new Set(x.evidenceSnapshots.map(ref=>ref.key));if(refs.size!==expectedKeys.size||[...expectedKeys].some(key=>!refs.has(key)))return false;
  for(const ref of x.evidenceSnapshots){const actual=sampleEvidence.get(ref.key);if(!actual||ref.sourceCombined!==actual.sourceCombined||ref.screenshotSha256!==actual.screenshotSha256||ref.snapshotSha256!==actual.snapshotSha256)return false;}
  for(const proof of x.sourceEvidence){if(!['before','after'].includes(proof.side)||typeof proof.path!=='string'||proof.path.startsWith('/')||proof.path.split('/').includes('..')||!proof.explanation)return false;try{if(sha(readFileSync(join(proof.side==='before'?before:after,proof.path)))!==proof.sha256)return false;}catch{return false;}}
  return true;
 });if(!waived)problems.push({selector,error:'Changed or token-dependent rule has no runtime hit; source-bound complete-coverage unused/N/A evidence required.'});}
 if(changes.length&&!matchedTotal)problems.push('No affected runtime elements for changed CSS; empty/fabricated coverage cannot qualify.');


 const nativeControlCoverage=nativeVerifier.finish();
 let scopeChecked=false,scopeQualified=false;const outOfScope=[];const plan=options.scopeOwners;
 if(plan!==undefined){if(!Array.isArray(plan)||!plan.length||plan.some(owner=>typeof owner!=='string'||!knownOwners.has(owner)))problems.push({error:'Planned scope must be a nonempty explicit list of registered namespaces; no implicit all scope.'});else{scopeChecked=true;const allowed=new Set(plan);for(const group of affectedIndex.values()){const exceeded=[...group.owners].filter(owner=>!owner.startsWith('@technical/')&&!allowed.has(owner));if(exceeded.length)outOfScope.push({assetPath:group.assetPath,selector:group.selector,owners:exceeded,scenes:[...group.scenes],examples:group.examples});}if(outOfScope.length)problems.push({error:'Actual or conservative affected components exceed the declared scope.',groups:outOfScope.length});scopeQualified=outOfScope.length===0&&problems.length===0;}}
 const cssConsumerEvidenceQualified=problems.length===0;
 const fullVisualReceiptRequired=nonCssChanges.length>0||unsupportedCssChanges.length>0;
 let fullVisualReceiptQualified=false;
 if(fullVisualReceiptRequired||options.visualReceipt){
  const receipt=options.visualReceipt,expected=[...(coverage?.scenarios||[])].map(s=>typeof s==='string'?s:sampleKey(s)).sort();
  const actual=Array.isArray(receipt?.scenarios)?[...receipt.scenarios].sort():[];
  const expectedConfigs=[...new Set(expected.map(key=>key.split('|').slice(1,5).join('|')))].sort(),receiptConfigs=Array.isArray(receipt?.configs)?receipt.configs:[];
  fullVisualReceiptQualified=validateMatrixConfigs(receiptConfigs,'full').length===0&&JSON.stringify(receiptConfigs.map(configKey).sort())===JSON.stringify(expectedConfigs)&&receipt?.schemaVersion===1&&receipt.coverage?.scope==='full'&&receipt.coverage?.completeMatrix===true&&Array.isArray(receipt.configs)&&receipt.configs.length===16&&new Set(receipt.configs.map(c=>JSON.stringify(c))).size===16&&receipt.complete===true&&receipt.changedPixels===0&&receipt.beforeCombined===manifests.before&&receipt.afterCombined===manifests.after&&JSON.stringify(actual)===JSON.stringify(expected)&&expected.length>0;
  if(nativeControlCoverage.required&&!nativeControlCoverage.qualified)fullVisualReceiptQualified=false;
  for(const key of expected){const b=sampleEvidence.get('before|'+key)?.verifiedPixels,a=sampleEvidence.get('after|'+key)?.verifiedPixels;if(!b||!a||b.width!==a.width||b.height!==a.height||b.rgbaSha256!==a.rgbaSha256){fullVisualReceiptQualified=false;problems.push({scenario:key,beforePixels:b||null,afterPixels:a||null,error:'Actual decoded PNG pixels differ or are missing; a zero-difference receipt cannot override observed pixels.'});}}
  if(!fullVisualReceiptQualified)problems.push({nonCssChanges,unsupportedCssChanges,error:'HTML/JS/assets or unmodeled CSS at-rule/cascade changed. All observed consumers are conservatively affected; source-bound complete DOM/full zero-pixel receipt required. CSS evidence alone cannot qualify whole-source impact.'});
 }

 const affected=[...affectedIndex.values()].map(row=>({...row,owners:[...row.owners],scenes:[...row.scenes]}));
 return {schemaVersion:2,ok:problems.length===0,sampleCount,affectedRecords,problemCount,problemCounts,problemsTruncated:problemCount>problems.length,snapshots:snapshotSummaries,scopeChecked,scopeQualified,plannedScopeOwners:plan===undefined?null:plan,outOfScope,scope:'CSS consumer evidence; non-CSS changes require independent complete DOM/full pixel qualification. ok is not a release or whole-visual verdict.',cssConsumerEvidenceQualified,writerMigration,nonCssChanges,nativeControlCoverage,fullVisualReceiptRequired,fullVisualReceiptQualified,unsupportedCssChanges,changedRules:changes,changedTokens:[...tokenChanged],affected,problems,note:'Union of before/after snapshots required. Unmounted, inactive pseudo states and unvisited pages require explicit additional samples and screenshots; this report never substitutes for the full zero-pixel gate.'};
}
/** Read one compressed snapshot at a time. Raw-byte SHA is preserved without adding hash-changing JSON fields. */
export function* runtimeFiles(files){for(const path of files){const bytes=readFileSync(path);let snapshot;try{snapshot=JSON.parse(path.endsWith('.gz')?gunzipSync(bytes):bytes);}catch{throw Error('Invalid runtime snapshot JSON: '+path);}if(Array.isArray(snapshot))throw Error('Aggregate runtime arrays are not supported; use one snapshot per file and --runtime-dir.');Object.defineProperties(snapshot,{_impactFile:{value:path},_impactBytesSha:{value:createHash('sha256').update(bytes).digest('hex')}});yield snapshot;}}
const runtimeWalk=dir=>readdirSync(dir,{withFileTypes:true}).flatMap(entry=>entry.isDirectory()?runtimeWalk(join(dir,entry.name)):/\.json(?:\.gz)?$/.test(entry.name)?[join(dir,entry.name)]:[]).sort();
if(process.argv[1]===fileURLToPath(import.meta.url)){
 const args=process.argv.slice(2),get=k=>args[args.indexOf(k)+1],all=k=>args.flatMap((arg,i)=>arg===k?[args[i+1]]:[]);
 if(!args.includes('--before')||!args.includes('--after'))throw Error('Usage: css-impact.mjs --before <public> --after <public> --runtime-dir <before-runtime> --runtime-dir <after-runtime> --coverage coverage.json --output report.json');
 const files=[...all('--runtime-dir').flatMap(runtimeWalk),...all('--runtime')].sort(),output=resolve(get('--output')||'css-impact.json'),evidenceDir=output.replace(/\.json$/,'')+'-evidence';mkdirSync(evidenceDir,{recursive:true});
 const readJson=path=>JSON.parse(readFileSync(path,'utf8'));
 const coverage=args.includes('--coverage')?readJson(get('--coverage')):undefined;
 const legacyOwners=args.includes('--before-owners')?readJson(get('--before-owners')):undefined;
 let ownerIndex=null,ownerDir=null;if(args.includes('--before-owners-dir')){ownerDir=resolve(get('--before-owners-dir'));ownerIndex=readJson(join(ownerDir,'index.json'));}
 let peakRss=process.memoryUsage().rss,sceneIndex=0;const started=Date.now(),evidence=[];
 const out=impact(resolve(get('--before')),resolve(get('--after')),runtimeFiles(files),{
  screenshotRoot:args.includes('--screens-root')?get('--screens-root'):undefined,coverageManifest:coverage,beforeOwnerMap:legacyOwners,
  visualReceipt:args.includes('--visual-receipt')?readJson(get('--visual-receipt')):undefined,scopeOwners:args.includes('--scope')?(()=>{const scope=readJson(get('--scope'));return Array.isArray(scope)?scope:scope.owners;})():undefined,
  loadBeforeOwnerMap:ownerIndex?sample=>{const key=[sample.scene,sample.viewport?.width,sample.viewport?.height,sample.theme,sample.lang,sample.state||'default'].join('|'),ref=ownerIndex.shards?.[key];if(!ref)return{valid:false,entries:{}};if(!ref.file||ref.file.startsWith('/')||ref.file.split('/').includes('..'))throw Error('Unsafe owner shard reference');const path=join(ownerDir,ref.file),bytes=readFileSync(path);if(createHash('sha256').update(bytes).digest('hex')!==ref.sha256)throw Error('Owner shard SHA mismatch');const map=JSON.parse(gunzipSync(bytes));Object.defineProperty(map,'_afterSnapshotSha256',{value:ref.snapshotSha256});if(ownerIndex.valid!==true||ref.valid!==true)map.valid=false;return map;}:undefined,
  onSampleAffected:(sample,rows)=>{const path=join(evidenceDir,String(sceneIndex++).padStart(6,'0')+'.jsonl.gz'),content=rows.map(row=>JSON.stringify(row)).join('\n')+'\n',bytes=gzipSync(content);writeFileSync(path,bytes);evidence.push({snapshotKey:sample.side+'|'+[sample.scene,sample.viewport?.width,sample.viewport?.height,sample.theme,sample.lang,sample.state||'default'].join('|'),file:relative(dirname(output),path),sha256:createHash('sha256').update(bytes).digest('hex'),records:rows.length});},
  onProgress:stats=>{peakRss=Math.max(peakRss,stats.rss);if(stats.sampleCount%50===0)process.stderr.write(JSON.stringify({progress:stats.sampleCount,affectedRecords:stats.affectedRecords,problemCount:stats.problemCount,peakRss})+'\n');}
 });
 out.processing={mode:'one compressed snapshot and owner shard at a time; original dictionary/ref coverage retained',files:files.length,durationMs:Date.now()-started,peakRssBytes:Math.max(peakRss,process.resourceUsage().maxRSS*1024)};out.affectedEvidence=evidence;
 if(ownerIndex&&ownerIndex.valid!==true){out.ok=false;out.problems.push({error:'Before-owner shard index is not valid; partial shards cannot qualify the complete matrix.'});}
 writeFileSync(output,JSON.stringify(out,null,2)+'\n');console.log(JSON.stringify({ok:out.ok,changes:out.changedRules.length,nonCssChanges:out.nonCssChanges.length,affectedRuleGroups:out.affected.length,affectedRecords:out.affectedRecords,problemCount:out.problemCount,processing:out.processing}));process.exitCode=out.ok?0:1;
}
