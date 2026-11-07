import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import DICT from '../public/i18n.js';
import {describeDevice} from '../public/js/device-label.js';
import {dictionaries} from '../build.mjs';
const resources=dictionaries();
const fields=s=>[...s.matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort();
const translate=lang=>(key,vars)=>resources[lang][key].replace(/\{(\w+)\}/g,(m,k)=>vars?.[k]??m);
test('bots and device resources agree across both languages and shipped dictionary',()=>{
 const keys=Object.keys(resources.zh).filter(k=>k.startsWith('bots.')||k.startsWith('device.'));
 assert.equal(keys.length,162);
 for(const key of keys){
  assert.equal(typeof resources.en[key],'string',key);
  assert.deepEqual(fields(resources.zh[key]),fields(resources.en[key]),key);
  for(const lang of ['zh','en'])assert.equal(DICT[lang][key],resources[lang][key],key);
 }
});
test('named parameters are values and remain literal, including dollar signs and braces',()=>{
 for(const lang of ['zh','en']){
  const provider='<provider> $& {number}';
  const result=translate(lang)('bots.model_key',{provider});
  assert.ok(result.includes(provider));
  assert.ok(!result.includes('{provider}'));
  assert.ok(translate(lang)('bots.wizard_step',{step:2}).includes('2/3'));
 }
});
test('device labels preserve old Chinese defaults and select English via translator',()=>{
 const cases=[['HUAWEI Mobile','华为手机','Huawei phone'],['HarmonyOS MatePad','华为平板','Huawei tablet'],['Android Chrome/','Android','Android'],['iPhone Safari/','iOS','iOS'],['Windows Edg/','Windows','Windows'],['Linux Firefox/','Linux','Linux'],['Mac OS X Safari/','macOS','macOS'],['unknown','','']];
 for(const [ua,zh,en] of cases){
  assert.ok(describeDevice(ua).startsWith('网页 · '));
  assert.ok(describeDevice(ua).includes(zh));
  assert.ok(describeDevice(ua,false,translate('en')).startsWith('Web · '));
  assert.ok(describeDevice(ua,false,translate('en')).includes(en));
  assert.ok(describeDevice(ua,true,translate('en')).endsWith('Home screen'));
 }
 assert.equal(describeDevice('unknown'),'网页 · 浏览器');
 assert.equal(describeDevice('unknown',false,translate('en')),'Web · Browser');
});
test('bots user-facing Chinese has moved out of executable lines; protocol identifiers stay fixed',()=>{
 const src=readFileSync(new URL('../public/js/bots.js',import.meta.url),'utf8');
 const executable=src.split('\n').filter(l=>!l.trimStart().startsWith('//')).join('\n');
 assert.doesNotMatch(executable,/[\u3400-\u9fff]/);
 for(const token of ["signedWrite('bots_write'", "t:'bots_read'", "op:'tool_approve'", "approval:m.id,digest:m.digest", "allow:false", "signedWrite"])assert.ok(src.includes(token),token);
 assert.match(src,/import \{ lang, t, both \} from '.\/t.js'/);
});
