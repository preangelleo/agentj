import {classNames,declarations} from './css-syntax.mjs';
export function splitSelectors(s){let depth=0,quote='',start=0,out=[];for(let i=0;i<s.length;i++){const c=s[i];if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}if(c==='"'||c==="'"){quote=c;continue;}if(c==='('||c==='[')depth++;if(c===')'||c===']')depth--;if(c===','&&!depth){out.push(s.slice(start,i).trim());start=i+1;}}out.push(s.slice(start).trim());return out;}
function expand(s){const m=/:((?:is|where))\(/.exec(s);if(!m)return[s];let start=m.index+m[0].length,depth=1,end=start;for(;end<s.length;end++){if(s[end]==='(')depth++;if(s[end]===')'&&!--depth)break;}if(depth)throw Error('Unterminated selector function');const variants=splitSelectors(s.slice(start,end)).flatMap(v=>expand(s.slice(0,m.index)+v+s.slice(end+1)));if(variants.length>512)throw Error('Selector expansion exceeds ownership limit');return variants;}
function simplifyIntersections(selector){
 let changed=true;while(changed){changed=false;const matches=[...selector.matchAll(/:is\(/g)].reverse();for(const m of matches){let d=1,end=m.index+4;for(;end<selector.length;end++){if(selector[end]==='(')d++;if(selector[end]===')'&&!--d)break;}if(!selector.startsWith(':where(',end+1))continue;let j=end+8,wd=1;for(;j<selector.length;j++){if(selector[j]==='(')wd++;if(selector[j]===')'&&!--wd)break;}const branch=selector.slice(end+8,j);if(splitSelectors(selector.slice(m.index+4,end)).includes(branch)){selector=selector.slice(0,m.index)+branch+selector.slice(j+1);changed=true;break;}}}return selector;
}
function withoutRelational(selector){return selector.replace(/:(?:has|not)\(/g,(x)=>x);}
export function branchOwners(selector,registry){
 const exact=registry.selectorOwners?.[selector];if(exact)return Array.isArray(exact)?exact:[exact];
 selector=stripRelationalAssertions(simplifyIntersections(selector));
 const narrowed=registry.selectorOwners?.[selector];if(narrowed)return Array.isArray(narrowed)?narrowed:[narrowed];
 const roots=[...registry.components.flatMap(c=>c.existingClasses.map(root=>({root,owner:c.namespace}))),...Object.keys(registry.independentFamilies||{}).map(root=>({root,owner:root}))];
 const find=c=>{if(registry.nonActorContainers?.classes.includes(c))return[];const x=registry.classOwners?.[c];if(x)return Array.isArray(x)?x:[x];return [...new Set(roots.filter(x=>c===x.root||c.startsWith(x.root+'-')||c.startsWith(x.root+'__')).map(x=>x.owner))];};
 return [...new Set(splitSelectors(selector).flatMap(expand).map(branch=>{
  const explicit=/data-aj-owner\s*=\s*["']([\w-]+)["']/.exec(branch)?.[1];if(explicit)return explicit;
  const ids=[...branch.matchAll(/#([_a-zA-Z][\w-]*)/g)].map(x=>registry.idOwners?.[x[1]]?.owner).filter(Boolean);
  const candidates=classNames(branch).filter(c=>!['on','open','still','in','bye','gone','selected','active','disabled','hidden','working','ready','error'].includes(c)).reverse().map(find).filter(x=>x.length);
  for(const c of candidates){if(c.length===1)return c[0];for(const other of candidates){const overlap=c.filter(x=>other.includes(x));if(overlap.length===1)return overlap[0];}}
  return ids.at(-1)||null;
 }).filter(Boolean))];
}
export function auditRule(rule,registry,families){
 const issues=[],ds=declarations(rule.body),subjects=nativeSubjects(rule.head,registry),selfOwners=[...new Set(subjects.map(s=>s.selfOwner).filter(Boolean))],owners=subjects.length&&subjects.every(s=>s.selfOwner)?selfOwners:branchOwners(rule.head,registry),explicit=[...rule.head.matchAll(/data-aj-owner\s*=\s*["']([\w-]+)["']/g)].map(x=>x[1]);
 const family=t=>[...families].sort((a,b)=>b.length-a.length).find(o=>t.startsWith('--'+o+'-'));
 const refs=privateTokenRefs(rule.body),tokenOwners=[...new Set(refs.map(family).filter(Boolean))];
 const selectionOnly=ds.every(d=>['user-select','-webkit-user-select','-webkit-touch-callout','-webkit-tap-highlight-color'].includes(d.property));
 if(owners.length>1&&!selectionOnly)issues.push('cross-owner-selector');
 if(!owners.length&&classNames(rule.head).some(c=>registry.nonActorContainers?.classes.includes(c))&&ds.some(d=>!d.property.startsWith('--')))issues.push('composition-container-cannot-authorize-descendant-appearance');
 if(!owners.length&&tokenOwners.length&&ds.some(d=>!d.property.startsWith('--')))issues.push('private-token-cannot-authorize-appearance');
 if(tokenOwners.length>1||owners.length===1&&tokenOwners.some(x=>x!==owners[0]))issues.push('cross-owner-private-token');
 if(explicit.some(x=>!families.includes(x)))issues.push('unknown-owner');
 const nativeType=/(?:^|[\s>+~])(?:button|select|h[1-6]|a|small|hr|code|pre|kbd|samp)(?=$|[\s>+~:.\[#])/;
 const global=splitSelectors(rule.head).flatMap(expand).some(h=>nativeType.test(h)&&!h.includes('data-aj-owner')&&!classNames(h).length&&!/[#][_a-zA-Z]/.test(h));
 const reset=(rule.head==='button'&&ds.every(d=>({font:'inherit',color:'inherit',border:'0',background:'none',cursor:'pointer'}[d.property]===d.value)))||(rule.head==='input, button, textarea, select'&&ds.every(d=>['font','color'].includes(d.property)&&d.value==='inherit'));
 if(global&&!reset)issues.push('global-native-appearance');
 if(rule.head.includes('\\'))issues.push('unsupported-selector-escape');
 if(unquotedIdentifierEscape(rule.body))issues.push('unsupported-declaration-identifier-escape');
 for(const subject of subjects)if(subject.explicit&&subject.actorOwners?.some(o=>o!==subject.selfOwner))issues.push('native-subject-owner-conflicts-with-normative-class');
 if(!reset&&!selectionOnly&&(subjects.some(s=>!s.selfOwner)||unsupportedNativePseudo(rule.head)))issues.push('uncontracted-native-descendant');
 return {issues,owners,tokenOwners};
}
export const factoryInjectsAppearance=source=>/if\s*\(\s*tag\s*===?\s*['"](?:button|select)['"]\s*\)[^\n;]*(?:classList|className)/.test(source);
// A root may not borrow another family's skin. Whitespace/combinators are real component boundaries.
export function selectorBorrowsSkin(selector){
 const highRisk=c=>['busy-dots','busy-one','busy-org-person','busy-org-root','omq','omqfold','qc','opt','qmore','lt-opt'].includes(c)||/^aj-(?:busy|quote|choice)(?:[-_]|$)/.test(c);
 const skin=c=>/^aj-(?:action|icon-action|navigation-action|dialog-action)(?:[-_]|$)/.test(c);
 for(const branch of splitSelectors(selector).flatMap(expand)){
  let parts=[],start=0,depth=0,bracket=0,quote='';
  for(let i=0;i<=branch.length;i++){const c=branch[i];if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}if(c==='"'||c==="'"){quote=c;continue;}if(c==='[')bracket++;if(c===']')bracket--;if(c==='(')depth++;if(c===')')depth--;if(i===branch.length||!depth&&!bracket&&/[\s>+~]/.test(c)){if(i>start)parts.push(branch.slice(start,i));start=i+1;}}
  for(const part of parts){const classes=classNames(stripRelationalAssertions(part));if(classes.some(highRisk)&&classes.some(skin))return true;}
 }return false;
}

// Positive subject compounds only: :not/:has and other functional assertions never lend ownership.
export function nativeSubjects(selector,registry){
 const states=new Set(['on','off','open','closed','cur','yes','go','selected','active','disabled','hidden','working','ready','error','tui']);
 function positive(s){let out='';for(let i=0;i<s.length;i++){if(s[i]===':'&&/^:{1,2}[\w-]+\(/.test(s.slice(i))){const m=/^:{1,2}[\w-]+\(/.exec(s.slice(i));let j=i+m[0].length,d=1,q='';for(;j<s.length;j++){const c=s[j];if(q){if(c==='\\')j++;else if(c===q)q='';continue;}if(c==='"'||c==="'")q=c;else if(c==='(')d++;else if(c===')'&&!--d)break;}i=j;continue;}out+=s[i];}return out;}
 return splitSelectors(selector).map(simplifyIntersections).flatMap(expand).map(branch=>{const clean=positive(branch);let start=0,bracket=0,q='';for(let i=0;i<clean.length;i++){const c=clean[i];if(q){if(c==='\\')i++;else if(c===q)q='';continue;}if(c==='"'||c==="'")q=c;else if(c==='[')bracket++;else if(c===']')bracket--;else if(!bracket&&/[\s>+~]/.test(c))start=i+1;}const subject=clean.slice(start),tag=/^(button|select)(?=$|[.#[:])/i.exec(subject)?.[1]?.toLowerCase();if(!tag)return null;const own=/\[data-aj-owner\s*=\s*["']?([\w-]+)["']?\]/.exec(subject)?.[1];const classes=classNames(subject).filter(c=>!states.has(c));const clsOwners=[...new Set(classes.flatMap(c=>registry.classOwners?.[c]||registry.components.filter(x=>x.existingClasses.includes(c)).map(x=>x.namespace)))];const ids=[...subject.matchAll(/#([\w-]+)/g)].flatMap(m=>registry.idOwners?.[m[1]]?.owner||[]);const owners=own?[own]:clsOwners.length===1?clsOwners:[...new Set(ids)];const actorOwners=classes.flatMap(c=>registry.nativeActorClasses?.[c]?.owner||[]);return{branch,positiveBranch:clean,subject,tag,actorOwners,selfOwner:owners.length===1&&registry.ownerFamilies?.[owners[0]]?owners[0]:null,explicit:!!own,hasAncestor:start>0};}).filter(Boolean);
}

// Complex selector arguments on native subjects are not supported by textual branch expansion.
// Reject them instead of losing the outer native subject to a trailing universal compound.
export function unsupportedNativePseudo(selector){
 selector=splitSelectors(selector).map(simplifyIntersections).join(',');
 if(!/(?:^|[\s>+~,(])(?:button|select)(?=$|[\s>+~:.#\[,)])/i.test(selector))return false;
 for(const m of selector.matchAll(/:(?:is|where)\(/g)){let end=m.index+m[0].length,d=1,q='';for(;end<selector.length;end++){const c=selector[end];if(q){if(c==='\\')end++;else if(c===q)q='';continue;}if(c==='"'||c==="'")q=c;else if(c==='(')d++;else if(c===')'&&!--d)break;}for(const arg of splitSelectors(selector.slice(m.index+m[0].length,end))){let bracket=0,paren=0,quote='';for(let i=0;i<arg.length;i++){const c=arg[i];if(quote){if(c==='\\')i++;else if(c===quote)quote='';continue;}if(c==='"'||c==="'"){quote=c;continue;}if(c==='[')bracket++;else if(c===']')bracket--;else if(c==='(')paren++;else if(c===')')paren--;else if(!bracket&&!paren&&/[\s>+~]/.test(c))return true;}}}
 return false;
}

export function unquotedIdentifierEscape(text){let quote='',comment=false;for(let i=0;i<text.length;i++){const c=text[i],pair=text.slice(i,i+2);if(comment){if(pair==='*/'){comment=false;i++;}continue;}if(quote){if(c==='\\')i++;else if(c===quote)quote='';continue;}if(pair==='/*'){comment=true;i++;continue;}if(c==='"'||c==="'"){quote=c;continue;}if(c==='\\')return true;}return false;}

export function stripRelationalAssertions(selector){let out='';for(let i=0;i<selector.length;i++){const m=/^:(?:not|has)\(/.exec(selector.slice(i));if(!m){out+=selector[i];continue;}let d=1,q='',j=i+m[0].length;for(;j<selector.length;j++){const c=selector[j];if(q){if(c==='\\')j++;else if(c===q)q='';continue;}if(c==='"'||c==="'")q=c;else if(c==='(')d++;else if(c===')'&&!--d)break;}i=j;}return out;}

export function privateTokenRefs(text){const refs=[];let quote='',comment=false;for(let i=0;i<text.length;i++){const c=text[i],pair=text.slice(i,i+2);if(comment){if(pair==='*/'){comment=false;i++;}continue;}if(quote){if(c==='\\')i++;else if(c===quote)quote='';continue;}if(pair==='/*'){comment=true;i++;continue;}if(c==='"'||c==="'"){quote=c;continue;}if(text.startsWith('var(',i)){const m=/^var\(\s*(--aj-[\w-]+)/.exec(text.slice(i));if(m)refs.push(m[1]);}}return refs;}
