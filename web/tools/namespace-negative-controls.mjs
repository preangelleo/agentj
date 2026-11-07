#!/usr/bin/env node
import assert from 'node:assert/strict';import{readFileSync,writeFileSync,readdirSync}from'node:fs';import{fileURLToPath}from'node:url';import{join}from'node:path';
import{rules,classNames,stripComments,declarations}from'./css-syntax.mjs';import{auditRule,factoryInjectsAppearance,selectorBorrowsSkin}from'./ownership-contract.mjs';
import{resolveDefaults}from'./css-defaults.mjs';
import{validatesTechnical}from'./technical-contract.mjs';
const web=fileURLToPath(new URL('../',import.meta.url)),registry=JSON.parse(readFileSync(join(web,'component-ownership.json'),'utf8'));
const families=JSON.parse(readFileSync(join(web,'control-ownership-sites.json'),'utf8')).owners;
const checks=[];const test=(name,fn)=>{try{fn();checks.push({name,pass:true});}catch(e){checks.push({name,pass:false,error:String(e)});}};
for(const [name,css,expected]of[
 ['cross-owner comma selector','.busy-dots,.aj-action{padding:12px}','cross-owner-selector'],
 ['cross-owner :is selector',':is(.busy-dots,.aj-action){background:red}','cross-owner-selector'],
 ['mixed known class cannot hide button','.aj-action,button{padding:12px}','global-native-appearance'],
 ['mixed known code class cannot hide pre','.aj-code,pre{background:red}','global-native-appearance'],
 ['mixed :is cannot hide naked pre',':is(.aj-code,pre){background:red}','global-native-appearance'],
 ['naked global heading font','h1{font-size:90px}','global-native-appearance'],
 ['naked global button padding','button{padding:12px}','global-native-appearance'],
 ['naked global select background','select:hover{background:red}','global-native-appearance'],
 ['busy reads action private token','.busy-dots{background:var(--aj-action-hover-background,red)}','cross-owner-private-token'],
 ['unknown data owner','button:where([data-aj-owner="aj-unregistered"]){padding:1px}','unknown-owner'],
])test(name,()=>assert.ok(auditRule(rules(css)[0],registry,families).issues.includes(expected),expected));
test('generic DOM factory injection',()=>assert.equal(factoryInjectsAppearance("if(tag === 'button') node.classList.add('aj-action');"),true));
test('metadata-only generic DOM factory permitted',()=>assert.equal(factoryInjectsAppearance("if(owner) node.setAttribute('data-aj-owner',owner);"),false));
test('internal selector comment offset roundtrip',()=>{const css='.before{--x:1}\n/* section */\n.a, /* ownership note */ .b { color:red }\n@media(x){.c{display:block}}';for(const r of rules(css)){assert.equal(stripComments(css.slice(r.start,r.open)).trim(),r.head);assert.equal(css[r.end-1],'}');}const r=rules(css)[1];const next=css.slice(0,r.start)+css.slice(r.start,r.end)+css.slice(r.end);assert.equal(next,css);});
test('unsupported nested qualified style fails closed',()=>assert.throws(()=>rules('.a{color:red;.b{padding:1px}}'),/Unsupported nested/));
test('keyframe blocks remain supported',()=>assert.equal(rules('@keyframes dance{0%{opacity:0}100%{opacity:1}}')[0].head,'@keyframes dance'));
test('attribute/string dots are not classes',()=>assert.deepEqual(classNames('.real[data-url="a.not-a-class"]:hover'),['real']));
test('quoted comment markers preserve selector/declaration semantics',()=>{const css='.x[data-value="/*keep*/"]{/*drop*/content:"/*keep*/";background:url("a/*keep*/b")}';const r=rules(css)[0];assert.equal(r.head,'.x[data-value="/*keep*/"]');assert.equal(declarations(r.body)[0].value,'"/*keep*/"');assert.equal(declarations(r.body)[1].value,'url("a/*keep*/b")');});
test('technical whitelist rejects new appearance or extended selector',()=>{const policy=JSON.parse(readFileSync(join(web,'technical-policy.json'),'utf8'));const entry=policy.entries.find(e=>e.selector==='p'&&e.file.includes('03-content'));assert.ok(entry);assert.equal(validatesTechnical(rules('p{text-wrap:pretty}')[0],entry),true);assert.equal(validatesTechnical(rules('p{text-wrap:pretty;padding:12px}')[0],entry),false);assert.equal(validatesTechnical(rules('p,button{text-wrap:pretty}')[0],entry),false);});
test('declared private var without inline fallback is retained for actual CSS resolution',()=>assert.equal(resolveDefaults('.x{--aj-prose-size:16px;font-size:var(--aj-prose-size)}'),'.x{--aj-prose-size:16px;font-size:var(--aj-prose-size)}'));
test('modifier-only skins cannot bypass high-risk family isolation',()=>{for(const root of['.busy-one','.omq','.opt'])for(const family of['aj-action','aj-icon-action','aj-navigation-action','aj-dialog-action'])for(const variant of['primary','quiet'])assert.equal(selectorBorrowsSkin(root+'.'+family+'--'+variant),true);assert.equal(selectorBorrowsSkin('.omq .aj-action--primary'),false,'an independent nested action is a separate component');});
test('entire current authored source parses with exact rule offsets',()=>{const paths=[...readdirSync(join(web,'public')).filter(x=>x.endsWith('.css')).map(x=>join(web,'public',x)),...readdirSync(join(web,'styles')).filter(x=>x.endsWith('.css')).map(x=>join(web,'styles',x))];for(const p of paths){const text=readFileSync(p,'utf8');for(const r of rules(text))assert.equal(stripComments(text.slice(r.start,r.open)).trim(),r.head,p);}});
const result={pass:checks.every(x=>x.pass),checks,scope:'CSS structure/ownership checks plus a documented JS factory-pattern guard; this is not a full JavaScript AST parser or runtime/pixel qualification.'};
const out=process.argv.includes('--output')?process.argv[process.argv.indexOf('--output')+1]:join(web,'reports/namespace-negative-controls.json');writeFileSync(out,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({pass:result.pass,checks:checks.length,failures:checks.filter(x=>!x.pass)}));if(!result.pass)process.exitCode=1;
