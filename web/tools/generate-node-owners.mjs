/** #577 ID compatibility cache. Authority is the semantic registry and current constructors. */
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const web=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sha=s=>createHash('sha256').update(s).digest('hex');
export function parseHtml(html){
 const nodes=[],stack=[]; const voids=new Set('area base br col embed hr img input link meta param source track wbr'.split(' '));
 // Quote-aware scan: > inside attribute values is not an element terminator.
 let i=0;
 while(i<html.length){const start=html.indexOf('<',i);if(start<0)break;if(html.startsWith('<!--',start)){i=html.indexOf('-->',start)+3;if(i<3)break;continue;}
 let end=start+1,q='';for(;end<html.length;end++){const c=html[end];if(q){if(c===q)q='';}else if(c==='"'||c==="'")q=c;else if(c==='>')break;}if(end>=html.length)break;i=end+1;
 const token=html.slice(start+1,end);const m=/^(\/?)\s*([\w-]+)/.exec(token);if(!m)continue;const tag=m[2].toLowerCase();
 if(m[1]){const n=stack.findLastIndex(x=>x.tag===tag);if(n>=0)stack.length=n;continue;}
 const attrs={};const ar=/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;ar.lastIndex=m[0].length;let a;while((a=ar.exec(token)))attrs[a[1].toLowerCase()]=a[2]??a[3]??a[4]??'';
 const node={tag,attrs,classes:(attrs.class||'').split(/\s+/).filter(Boolean),parent:stack.at(-1),line:html.slice(0,start).split('\n').length};nodes.push(node);
 if(!voids.has(tag)&&!token.endsWith('/'))stack.push(node);
 if(tag==='script'||tag==='style'){const close=html.toLowerCase().indexOf('</'+tag,i);if(close>=0)i=close;}
 }return nodes;
}
function matches(node,selector){
 const pieces=selector.trim().split(/\s+/);let n=node;
 function atom(x,s){let tag=/^[\w-]+/.exec(s);if(tag&&x.tag!==tag[0])return false;for(const m of s.matchAll(/([.#])([\w-]+)/g))if(m[1]==='#'?x.attrs.id!==m[2]:!x.classes.includes(m[2]))return false;return true;}
 if(!atom(n,pieces.pop()))return false;while(pieces.length){const p=pieces.pop();n=n.parent;while(n&&!atom(n,p))n=n.parent;if(!n)return false;}return true;
}
export function generateNodeOwners({html,registry,sources={}}){
 const nodes=parseHtml(html),cfg=registry.nodeOwnership,ids=new Map(),errors=[];
 for(const n of nodes)if(n.attrs.id){if(ids.has(n.attrs.id))errors.push('Duplicate static ID '+n.attrs.id);ids.set(n.attrs.id,n);}
 const familyOwner=f=>registry.components.find(x=>x.id===f)?.namespace||(f==='technical-viewport-shell'?'aj-shell':null);
 const classOwner=c=>{const a=registry.classOwners[c];if(!a||a.length!==1)throw Error('Class contract is not unique: '+c);return a[0];};
 const inside=(n,id)=>{for(let p=n;p;p=p.parent)if(p.attrs.id===id)return true;return false;};
 for(const [id,c]of Object.entries(cfg.rootIds))if(!ids.has(id))errors.push('Missing canonical root '+id);else if(!familyOwner(c.family))errors.push('Unknown canonical family '+id);else if(c.owner&&registry.ownerFamilies[c.owner]!==c.family)errors.push('Canonical subowner/family conflict '+id);
 function local(n){
  const id=n.attrs.id,exact=cfg.exactIds[id];let expected;
  if(exact){expected=exact.ownerClass?classOwner(exact.ownerClass):familyOwner(exact.family);if(registry.ownerFamilies[expected]!==exact.family)errors.push('Exact ID family conflict '+id);if(!inside(n,exact.withinRoot))errors.push('Exact ID root conflict '+id);if(exact.ownerClass&&!n.classes.includes(exact.ownerClass))errors.push('Exact ID missing class '+id);}
  const direct=n.attrs['data-aj-owner'];if(direct&&cfg.rootIds[id]&&registry.ownerFamilies[direct]!==cfg.rootIds[id].family)errors.push('Canonical root family conflict '+id);if(direct&&!registry.ownerFamilies[direct])errors.push('Unknown direct owner '+id+': '+direct);
  if(direct&&cfg.rootIds[id]?.owner&&direct!==cfg.rootIds[id].owner)errors.push('Known-but-wrong canonical subowner '+id);
  if(direct&&expected&&direct!==expected)errors.push('Known-but-wrong direct owner '+id+': '+direct+' expected '+expected);
  if(expected)return {owner:expected,method:'exact-semantic-id',source:'public/index.html',line:n.line,evidence:exact.reason};
  if(direct)return {owner:direct,method:'direct-data-owner',source:'public/index.html',line:n.line};
  if(cfg.rootIds[id])return {owner:cfg.rootIds[id].owner||familyOwner(cfg.rootIds[id].family),method:'canonical-component-root',source:'public/index.html',line:n.line};
  const roots=[...cfg.classRoots,...cfg.scopedClassRoots].filter(c=>matches(n,c.selector));
  if(roots.length){const max=Math.max(...roots.map(c=>(c.selector.match(/[.#]/g)||[]).length));const winning=roots.filter(c=>(c.selector.match(/[.#]/g)||[]).length===max);const os=[...new Set(winning.map(c=>{if(c.owner){if(!registry.ownerFamilies[c.owner])throw Error('Unknown explicit root owner '+c.owner);return c.owner;}return classOwner(c.ownerClass);}))];if(os.length>1){errors.push('Ambiguous explicit class roots '+id+': '+winning.map(x=>x.selector));return null;}return {owner:os[0],method:'registered-component-class',source:'public/index.html',line:n.line,evidence:winning.map(c=>c.selector).join(',')};}
  return null;
 }
 const idOwners={},unowned=[];
 for(const [id,n]of ids){let proof=local(n);if(!proof)for(let p=n.parent;p;p=p.parent){if(['html','body'].includes(p.tag))break;const candidate=local(p);if(candidate&&candidate.owner!=='aj-shell'){proof={...candidate,method:'explicit-component-ancestor',rootId:p.attrs.id||null,rootClasses:p.classes,line:n.line,rootLine:p.line};break;}}
 if(proof)idOwners[id]={...proof,tag:n.tag,classes:n.classes};else unowned.push({id,tag:n.tag,line:n.line,classes:n.classes});}
 for(const c of cfg.dynamicIds){if(!ids.has(c.withinRoot))errors.push('Missing dynamic component root '+c.withinRoot);for(const id of c.ids)if(ids.has(id))errors.push('Dynamic/static ID collision '+id);const src=sources[c.source];if(!src){errors.push('Missing dynamic constructor '+c.source);continue;}const table=new RegExp('const\\s+'+c.idTable+'\\s*=\\s*(\\[[^;]*\\]);').exec(src);let targets=[];try{targets=JSON.parse(table?.[1]).map(a=>a[c.tableTargetIndex]);}catch{errors.push('Invalid dynamic ID table '+c.idTable);}
 if(JSON.stringify(targets)!==JSON.stringify(c.ids))errors.push('Dynamic ID table drift '+c.idTable);
 const body=new RegExp('function\\s+'+c.constructor+'\\s*\\([^)]*\\)\\s*\\{([\\s\\S]*?)\\n\\}').exec(src)?.[1]||'';
 const owner=familyOwner(c.family),ownerSet=new RegExp(c.nodeVariable+'\\.setAttribute\\([\"\']data-aj-owner[\"\'],\\s*[\"\']'+owner+'[\"\']\\)');
 if(!ownerSet.test(body))errors.push('Known-but-wrong dynamic clone owner '+c.constructor+' expected '+owner);
 const mirror=new RegExp('const\\s+'+c.mirrorTable+'\\s*=\\s*([^;]+);').exec(src)?.[1]||'';if(!mirror||mirror.includes('data-aj-owner'))errors.push('Dynamic mirror overrides owner '+c.mirrorTable);
 for(const id of c.ids)idOwners[id]={owner,method:'explicit-dynamic-constructor',source:c.source,line:src.slice(0,src.indexOf('function '+c.constructor)).split('\n').length,constructor:c.constructor,rootId:c.withinRoot,evidence:c.reason};
 }
 if(errors.length)throw Error(errors.join('\n'));
 return {schema:2,authority:'component-ownership.json + current explicit HTML + verified dynamic constructors; no ID prefix inference',sourceHashes:{'public/index.html':sha(html),'component-ownership.json':sha(JSON.stringify(registry)),...Object.fromEntries(Object.entries(sources).map(([k,v])=>[k,sha(v)]))},idOwners,unowned};
}
export function checkNodeOwners(actual,expected){if(JSON.stringify(actual)!==JSON.stringify(expected))throw Error('ID ownership cache drift: regenerate from current semantic registry and source');return true;}
export async function loadNodeOwners(){const registry=JSON.parse(await readFile(path.join(web,'component-ownership.json'),'utf8'));const html=await readFile(path.join(web,'public/index.html'),'utf8');const sources={};for(const c of registry.nodeOwnership.dynamicIds)sources[c.source]=await readFile(path.join(web,c.source),'utf8');return generateNodeOwners({html,registry,sources});}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){try{const result=await loadNodeOwners();if(process.argv.includes('--write'))await writeFile(path.join(web,'css-node-owners.json'),JSON.stringify(result,null,2)+'\n');else checkNodeOwners(JSON.parse(await readFile(path.join(web,'css-node-owners.json'),'utf8')),result);console.log(JSON.stringify({ok:true,ids:Object.keys(result.idOwners).length,unowned:result.unowned}));}catch(e){console.error(e.message);process.exitCode=1;}}
