import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
globalThis.window ??= globalThis;
globalThis.addEventListener ??= () => {};
globalThis.document ??= {addEventListener(){},documentElement:{dataset:{}},getElementById:()=>null,querySelectorAll:()=>[],body:{}};
const {parseRouting,parseCorrection,routingRole}=await import('../public/js/keys.js');

test('P450 accepts bounded public routing and immutable correction names only',()=>{
 assert.equal(parseRouting({main:'ark-agent-plan/main',rows:[{role:'small_model',model:'agentsrelay/small',provider:'agentsrelay'}],different:true,writable:true,stamp:'a'.repeat(56)}).rows[0].provider,'agentsrelay');
 assert.equal(parseRouting({main:'x'.repeat(200),rows:[]}),null);
 assert.equal(parseCorrection({offer:'FIX_'+'a'.repeat(24),source:'BAILIAN_CODING_PLAN_API_KEY',target:'DASHSCOPE_API_KEY'}).target,'DASHSCOPE_API_KEY');
 assert.equal(parseCorrection({offer:'untrusted',source:'ARK_API_KEY',target:'ARK_AGENT_PLAN_API_KEY'}),null);
 assert.equal(parseCorrection({offer:'FIX_'+'a'.repeat(24),source:'ARK_API_KEY',target:'ARBITRARY_KEY'}),null);
});
test('P450 UI copy is present in both language dictionaries',()=>{
 const src=readFileSync(new URL('../public/js/keys.js',import.meta.url),'utf8');
 const names=new Set([...src.matchAll(/'(p450\.[\w]+)'/g)].map(m=>m[1]));
 for(const lang of ['zh','en']) {const d=JSON.parse(readFileSync(new URL('../i18n/web.'+lang+'.json',import.meta.url)));for(const k of names)assert.ok(d[k],lang+': '+k);}
});

test('P476 route roles use owner-facing bilingual labels and hide unknown internal fields',()=>{
 for(const lang of ['zh','en']) {
  const d=JSON.parse(readFileSync(new URL('../i18n/web.'+lang+'.json',import.meta.url)));
  const translate=(key,vars={})=>d[key].replace(/\{(\w+)\}/g,(_,k)=>vars[k]);
  assert.equal(routingRole('small_model',translate),lang==='zh'?'辅助小模型':'Helper model');
  assert.equal(routingRole('agent:研究员',translate),lang==='zh'?'子 Agent「研究员」':'Sub-agent “研究员”');
  for(const role of ['future_internal_role','agent:','',null,{},undefined])
   assert.equal(routingRole(role,translate),lang==='zh'?'其它用途':'Other use');
 }
});
