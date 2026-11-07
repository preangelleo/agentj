import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {demo,sections} from './p477-fixture.mjs';
import {rules, declarations} from '../tools/css-syntax.mjs';
import {resolveDefaults} from '../tools/css-defaults.mjs';

// The original five source rules contain 25 semantic selector branches. Splitting
// their spelling must not expand the text targets or change a single declaration.
const textTargets=['h1','h2','h3','h4','h5','h6','strong','b','em','del','p','li','code','th','td','label','.lhost','.lang','.rxnote','.rc-note'];
const textGroup='.rm #words :is('+textTargets.join(',')+')';
const oldExpandedContract=[
 ...textTargets.map(t=>['.rm #words '+t,[['color','var(--lotion)']]]),
 ['.rm #words code.hljs span',[['color','var(--lotion)']]],
 ['.rm #words li::marker',[['color','var(--lotion)']]],
 ['.rm #words a:not(.mtap):not(.mcard a)',[['color','var(--lotion)'],['text-decoration-line','underline']]],
 ['.rm #words .reply-component svg text[text-anchor]',[['fill','var(--lotion)'],['color','var(--lotion)']]],
 ['.rm #words .fp-link',[['-webkit-text-fill-color','var(--lotion)']]],
];
function selectorBranches(head){
 const out=[];let start=0,depth=0,quote='';
 for(let i=0;i<=head.length;i++){
  const c=head[i];
  if(quote){if(c==='\\'){i++;continue;}if(c===quote)quote='';continue;}
  if(c==='"'||c==="'"){quote=c;continue;}
  if(c==='('||c==='[')depth++;if(c===')'||c===']')depth--;
  if((c===','&&!depth)||i===head.length){out.push(head.slice(start,i).trim());start=i+1;}
 }
 return out;
}
function assertReplyTextContract(css){
 const expanded=[];
 for(const rule of rules(css)){
  assert.deepEqual(rule.context,[],'the original text rules are unconditional');
  assert.doesNotMatch(rule.body,/background|border|stroke|--[a-z]+:/,'no shape paint, borders, strokes or variable declarations');
  const body=declarations(resolveDefaults(rule.body)).map(({property,value})=>[property,value]);
  for(const selector of selectorBranches(rule.head)){
   assert.ok(selector.startsWith('.rm #words'),'every branch stays in the reply body');
   if(selector===textGroup){
    for(const target of textTargets)expanded.push(['.rm #words '+target,body]);
   }else if(selector.startsWith(textGroup+':where(')){
    const target=selector.slice((textGroup+':where(').length,-1);
    assert.ok(selector.endsWith(')')&&textTargets.includes(target),'only an original :is branch may be filtered');
    expanded.push(['.rm #words '+target,body]);
   }else expanded.push([selector,body]);
  }
 }
 const sort=entries=>entries.slice().sort((a,b)=>a[0].localeCompare(b[0]));
 assert.deepEqual(sort(expanded),sort(oldExpandedContract),'exact original expanded selector and declaration set, without omissions or duplicates');
}
// #443: sources are located from this file, not from the caller's cwd (repo root, agentjarvis/ or an export tree)
test('P477 covers eleven formats with the original synthetic demonstration',()=>{assert.equal(sections.length,11);assert.match(demo,/flowchart LR/);assert.match(demo,/"kind":"line"/)});
test('P477 CSS only targets reply body text; never recolours shape containers',()=>{
 const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8').split('/* P477:')[1]?.split(/\n\n\/\* P\d+/)[0].replace(/^[\s\S]*?\*\//,'').replace(/\/\*[\s\S]*?\*\//g,'');assert.ok(css);
 assertReplyTextContract(css);
 assert.match(css,/svg text\[text-anchor\]\{fill:var\(--lotion\)/);assert.match(css,/li::marker/);assert.match(css,/text-decoration-line:underline/);
 // Negative controls retain the old scope/shape/marker/SVG/underline protections.
 for(const bad of [
  css.replace('.rm #words','body'),
  css.replace('{color:','{background:red;color:'),
  css.replace('{color:','{stroke:red;color:'),
  css.replace('{color:','{--shape:red;color:'),
  css.replace('svg text[text-anchor]','svg rect'),
  css.replace('li::marker','li::before'),
  css.replace('text-decoration-line:underline','text-decoration-line:none'),
 ]) assert.throws(()=>assertReplyTextContract(bad));
});
test('P477 Mermaid colour and cache are local to reply body; security config is retained',()=>{
 const src=readFileSync(new URL('../public/js/render.js',import.meta.url),'utf8');
 assert.match(src,/wrap.closest\('\.rm #words'\)/);assert.match(src,/replyBody \? ':reply' : ''/);assert.match(src,/if \(replyBody\) s = s.replace/);assert.match(src,/text,#\$\{id\} tspan/);assert.match(src,/securityLevel: 'strict', htmlLabels: false/);
});
