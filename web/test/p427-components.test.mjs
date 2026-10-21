import test from 'node:test';import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
globalThis.window={};
const {parseComponent,contract}=await import('../public/js/components.js');
test('P427 registry and generated phone/Agent contracts cannot drift',()=>execFileSync(process.execPath,['web/generate-components.mjs','--check']));
for(const c of contract.components.filter(x=>x.schema))test('P427 '+c.name+' schema and adversarial data',()=>{
 const good=JSON.stringify(c.example);assert.deepEqual(parseComponent(good),c.example);
 for(const src of [good.slice(0,-1),good.slice(0,-1)+',"onclick":"run"}',JSON.stringify({...c.example,component:'HTML'}),'null','[]','x'.repeat(contract.maxChars+1)])assert.equal(parseComponent(src),null);
});
test('P427 duplicate/dangling graphs, unsafe forms, non-finite and oversized charts are rejected',()=>{
 const ex=name=>structuredClone(contract.components.find(x=>x.name===name).example);
 let v=ex('flowchart');v.nodes[1].id='a';assert.equal(parseComponent(JSON.stringify(v)),null);
 v=ex('flowchart');v.edges[0].to='missing';assert.equal(parseComponent(JSON.stringify(v)),null);
 for(const field of [{id:'password',label:'Password',type:'text'},{id:'name',label:'Name',type:'password'},{id:'pick',label:'Pick',type:'select'}]){v=ex('form');v.fields=[field];assert.equal(parseComponent(JSON.stringify(v)),null);}
 v=ex('form');v.fields.push(v.fields[0]);assert.equal(parseComponent(JSON.stringify(v)),null);
 v=ex('chart');v.items[0].value=Infinity;assert.equal(parseComponent(JSON.stringify(v)),null);
 v=ex('chart');v.items=Array(31).fill(v.items[0]);assert.equal(parseComponent(JSON.stringify(v)),null);
});

test('P427 legacy table limits stay aligned with the registered contract',async()=>{
 const {RelayMD}=await import('../public/js/md.js');
 const p=contract.components.find(x=>x.name==='table').parameters;
 assert.deepEqual([p.columns,p.rows,p.cells],[RelayMD.LIMITS.MAX_COLS,RelayMD.LIMITS.MAX_ROWS,RelayMD.LIMITS.MAX_CELLS].map(x=>'≤'+x));
});
