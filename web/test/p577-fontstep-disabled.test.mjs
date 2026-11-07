import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {rules} from '../tools/css-syntax.mjs';
import {validateNativeRule,nativeBodySha} from '../tools/native-control-contract.mjs';
const json=p=>JSON.parse(readFileSync(new URL(p,import.meta.url)));
const semantic=json('../component-ownership.json'),policy=json('../native-control-contracts.json');
const entry=policy.entries.find(x=>x.id==='native-19');
const rule=rules(readFileSync(new URL('../public/app.css',import.meta.url),'utf8')).find(x=>x.head===entry.selector);
const input=r=>({file:'public/app.css',rule:r,occurrence:0});
test('font reset and step actors share only the exact disabled opacity purpose',()=>{
 const result=validateNativeRule(input(rule),entry,semantic);assert.ok(result.ok,JSON.stringify(result.issues));
 assert.deepEqual(result.bindings[0].allowedOwners,['aj-settings','aj-settings-control']);
 assert.deepEqual(semantic.nativeControlScopes.scopes.find(x=>x.id==='fontstep').actorOwners,['aj-settings-control']);
});
test('rebinding the body hash cannot grant padding or a different opacity through the shared purpose',()=>{
 for(const body of ['opacity:.35;padding:0','opacity:1']){
  const changed={...rule,body};const result=validateNativeRule(input(changed),{...entry,bodySha256:nativeBodySha(changed)},semantic);
  assert.ok(result.issues.includes('native-role-declaration-contract-violation'));
 }
});
