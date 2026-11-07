#!/usr/bin/env node
// Ownership is explicit. Unregistered selectors are findings, not silently assigned to a catch-all owner.
import {readFileSync,readdirSync,writeFileSync,existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {join} from 'node:path';import {fileURLToPath} from 'node:url';
import {rules,declarations,classNames} from './css-syntax.mjs';
import {auditAnimations,auditAnimationJs} from './animation-contract.mjs';
import {privateTokenRefs,auditRule,selectorBorrowsSkin} from './ownership-contract.mjs';
import {validateComposition} from './composition-contract.mjs';
import {validatesTechnical} from './technical-contract.mjs';
import {validateNativeRule} from './native-control-contract.mjs';
const web=fileURLToPath(new URL('../',import.meta.url));
const controls=JSON.parse(readFileSync(join(web,'control-ownership-sites.json'),'utf8'));
const registry=JSON.parse(readFileSync(join(web,'component-ownership.json'),'utf8'));
if(existsSync(join(web,'css-node-owners.json')))registry.idOwners=JSON.parse(readFileSync(join(web,'css-node-owners.json'),'utf8')).idOwners;
const families=[...new Set([...controls.owners,...registry.components.map(x=>x.namespace),...Object.keys(registry.cssRoles||{})])].sort((a,b)=>b.length-a.length);
const privateOwner=t=>families.find(o=>t.startsWith('--'+o+'-'));
const known=registry.components.flatMap(c=>c.existingClasses.map(root=>({root,owner:c.namespace})));
const selectorRegistry=JSON.parse(readFileSync(join(web,'css-selector-owners.json'),'utf8'));
const technicalPolicy=JSON.parse(readFileSync(join(web,'technical-policy.json'),'utf8'));
const compositionPolicy=JSON.parse(readFileSync(join(web,'composition-contracts.json'),'utf8'));
const compositionUsed=new Set();
const nativePolicy=JSON.parse(readFileSync(join(web,'native-control-contracts.json'),'utf8'));
const nativeUsed=new Set(),nativeRuntimeContracts=[];
const issues=[],unknown=[],ruleOwners=[];
for(const[file,hash]of Object.entries(compositionPolicy.sourceBindings||{})){if(!existsSync(join(web,file))||createHash('sha256').update(readFileSync(join(web,file))).digest('hex')!==hash)issues.push({file,reason:'Composition producer source drift; independent role review/binding required'});}if(!Object.keys(compositionPolicy.sourceBindings||{}).length)issues.push({reason:'Composition producer source bindings missing'});
const knownClass=c=>{
 const direct=registry.classOwners?.[c];if(direct)return Array.isArray(direct)?direct:[direct];
 return [...new Set(known.filter(x=>c===x.root||c.startsWith(x.root+'-')||c.startsWith(x.root+'__')||(x.root.endsWith('-')&&c.startsWith(x.root))).map(x=>x.owner))];
};
const walk=d=>readdirSync(d,{withFileTypes:true}).flatMap(x=>x.isDirectory()?(x.name==='vendor'?[]:walk(join(d,x.name))):x.name.endsWith('.css')?[join(d,x.name)]:[]);
const files=[...walk(join(web,'styles')),...readdirSync(join(web,'public')).filter(x=>x.endsWith('.css')).map(x=>join(web,'public',x))];
function at(file,src,r,reason){return{file:file.slice(web.length),line:src.slice(0,r.start).split('\n').length,selector:r.head,reason};}
for(const file of files){const src=readFileSync(file,'utf8');let parsed;try{parsed=rules(src);}catch(e){issues.push({file,reason:String(e)});continue;}
 const occurrences=new Map();for(const r of parsed){const occurrenceKey=JSON.stringify([r.head,r.context]),occurrence=occurrences.get(occurrenceKey)||0;occurrences.set(occurrenceKey,occurrence+1);if(r.head.startsWith('@')){if(!/^@(?:keyframes|-webkit-keyframes|font-face)\b/.test(r.head))issues.push(at(file,src,r,'Unregistered definition at-rule needs an explicit owner contract'));continue;}const composition=compositionPolicy.entries.find(e=>e.file===file.slice(web.length)&&e.selector===r.head&&e.occurrence===occurrence&&JSON.stringify(e.context)===JSON.stringify(r.context));const compositionAudit=composition?validateComposition(r,composition,registry,occurrence):null;if(compositionAudit?.ok)compositionUsed.add(composition.id);else if(compositionAudit)for(const reason of compositionAudit.issues)issues.push(at(file,src,r,reason));const reviewedActor=compositionAudit?.ok?{owner:composition.role}:selectorRegistry.entries.find(e=>e.file===file.slice(web.length)&&e.selector===r.head);const ruleRegistry=reviewedActor?{...registry,selectorOwners:{...registry.selectorOwners,[r.head]:reviewedActor.owner}}:registry;const audited=auditRule(r,ruleRegistry,families);const exactTechnical=technicalPolicy.entries.find(e=>e.file===file.slice(web.length)&&e.selector===r.head&&e.occurrence===occurrence&&validatesTechnical(r,e));let nativeResult=null;if((audited.issues.includes('uncontracted-native-descendant')||nativePolicy.entries.some(e=>e.mode==='self-owned-native-context'&&e.file===file.slice(web.length)&&e.selector===r.head&&e.occurrence===occurrence&&JSON.stringify(e.context)===JSON.stringify(r.context)))&&!exactTechnical){const contract=nativePolicy.entries.find(e=>e.file===file.slice(web.length)&&e.selector===r.head&&e.occurrence===occurrence&&JSON.stringify(e.context)===JSON.stringify(r.context));nativeResult=validateNativeRule({file:file.slice(web.length),rule:r,occurrence},contract,registry);if(nativeResult.ok){nativeUsed.add(contract.id);nativeRuntimeContracts.push({id:contract.id,file:contract.file,selector:contract.selector,context:contract.context,occurrence,bodySha256:contract.bodySha256,bindings:nativeResult.bindings});}else for(const reason of nativeResult.issues)issues.push(at(file,src,r,reason));}for(const reason of audited.issues)if(!((reason==='global-native-appearance'||reason==='uncontracted-native-descendant')&&exactTechnical)&&!(reason==='uncontracted-native-descendant'&&nativeResult?.ok))issues.push(at(file,src,r,reason));const ds=declarations(r.body),refs=privateTokenRefs(r.body);
 const tokenOwners=[...new Set(refs.map(privateOwner).filter(Boolean))];
 const explicit=[...r.head.matchAll(/data-aj-owner="([a-z-]+)"/g)].map(x=>x[1]);
 const nativeOwners=nativeResult?.ok?[...new Set(nativeResult.bindings.flatMap(b=>b.allowedOwners))]:[];const contextual=classNames(r.head).flatMap(knownClass);let owner=nativeOwners.length===1?nativeOwners[0]:explicit.length===1?explicit[0]:audited.owners.length===1?audited.owners[0]:null;
 const reviewed=compositionAudit?.ok?{owner:composition.role}:selectorRegistry.entries.find(e=>e.file===file.slice(web.length)&&e.selector===r.head);if(reviewed)owner=reviewed.owner;
 const technical=technicalPolicy.entries.find(e=>e.file===file.slice(web.length)&&e.selector===r.head&&JSON.stringify(e.context)===JSON.stringify(r.context)&&e.occurrence===occurrence);
 if(technical){if(!validatesTechnical(r,technical))issues.push(at(file,src,r,'Technical whitelist properties/values changed; explicit policy review required'));else owner=technical.role;}
 if(!owner&&ds.length&&ds.every(d=>d.property.startsWith('--')||d.property==='color-scheme'))owner='design-tokens';
 if(explicit.some(x=>!families.includes(x)))issues.push(at(file,src,r,'Unknown explicit owner'));
 if(tokenOwners.length>1)issues.push(at(file,src,r,'Cross-owner private token graph: '+tokenOwners.join(', ')));
 if(explicit.length===1&&tokenOwners.some(x=>x!==explicit[0]))issues.push(at(file,src,r,'Private token fallback crosses explicit owner'));
 // A naked global focus ring is a documented accessibility policy; component radius is not.
 if(r.head===':focus-visible'&&ds.some(d=>!['outline','outline-offset'].includes(d.property)))issues.push(at(file,src,r,'Global focus appearance outside a11y whitelist'));
 if(/^(button|select)(?:\b|[[:.#])/u.test(r.head)&&!r.head.includes('data-aj-owner')&&!/^[^,]*\.[\w-]+/.test(r.head)){
  const reset=r.head==='button'&&ds.every(d=>({font:'inherit',color:'inherit',border:'0',background:'none',cursor:'pointer'}[d.property]===d.value));
  if(!reset)issues.push(at(file,src,r,'Naked native control appearance; explicit semantic owner required'));
 }
 if(selectorBorrowsSkin(r.head))issues.push(at(file,src,r,'A robot/quote/answer root borrows action/icon/navigation skin, including modifier-only classes'));
 if(!owner)unknown.push(at(file,src,r,'No registered component owner; provide selector-specific ownership, never default web/action'));
 else ruleOwners.push(at(file,src,r,owner));
 }
}
for(const entry of compositionPolicy.entries)if(!compositionUsed.has(entry.id))issues.push({file:entry.file,selector:entry.selector,reason:'Composition role is stale or has no valid exact authored rule'});
const animationRegistry=JSON.parse(readFileSync(join(web,'animation-ownership.json'),'utf8'));
const animationAudit=auditAnimations(files.map(file=>[file.slice(web.length),readFileSync(file,'utf8')]),animationRegistry,new Set(families),registry);issues.push(...animationAudit.issues);
for(const file of ['public/app.js',...readdirSync(join(web,'public/js')).filter(p=>p.endsWith('.js')).map(p=>'public/js/'+p)])issues.push(...auditAnimationJs(file,readFileSync(join(web,file),'utf8'),animationRegistry));
for(const contract of nativePolicy.entries)if(!nativeUsed.has(contract.id))issues.push({file:contract.file,selector:contract.selector,reason:'Native contract is stale or has no current authored consumer'});
// Token-only design globals. color-scheme is a narrowly documented browser technical contract.
const palette=readFileSync(join(web,'public/brand/palette.css'),'utf8');for(const r of rules(palette))for(const d of declarations(r.body))if(!(r.head==='@font-face'&&['font-family','src','font-weight','font-style','font-display'].includes(d.property))&&!d.property.startsWith('--')&&d.property!=='color-scheme')issues.push(at(join(web,'public/brand/palette.css'),palette,r,'Design globals may contain only tokens'));
// Global tag-to-style injection caused the original accident. All generic DOM builders remain appearance-free.
for(const f of ['public/js/ui.js','public/js/components.js']){const s=readFileSync(join(web,f),'utf8');if(/if\s*\(\s*tag\s*===?\s*['"](?:button|select)['"]\s*\)[^\n;]*(?:classList|className)/.test(s))issues.push({file:f,reason:'Generic DOM factory injects a visual class by tag'});}
for(const[file,hash]of Object.entries(selectorRegistry.source6ProducerBindings||{}))if(!existsSync(join(web,file))||createHash('sha256').update(readFileSync(join(web,file))).digest('hex')!==hash)issues.push({file,reason:'Source-reviewed component slot producer drift; review exact ownership before rebinding'});
const result={ok:issues.length===0&&unknown.length===0,issues,unknown,ruleOwners,nativeRuntimeContracts,scope:'Web authored source CSS; vendor boundary excluded; full runtime owner and pixel gates are separate requirements.'};
const output=process.argv.includes('--output')?process.argv[process.argv.indexOf('--output')+1]:join(web,'reports/css-namespace-lint.json');writeFileSync(output,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({ok:result.ok,violations:issues.length,unownedRules:unknown.length,ownedRules:ruleOwners.length,report:output}));if(!result.ok)process.exitCode=1;
