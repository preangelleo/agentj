import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
test('P479: terminal outcomes show localized results and known exit codes',()=>{
 const js=read('../public/js/elevate.js'),body=js.slice(js.indexOf('function resultText('),js.indexOf('function paintOut('));
 for(const lang of ['en','zh']){
  const d=JSON.parse(read('../i18n/web.'+lang+'.json'));
  const result=vm.runInNewContext(body+';resultText',{t:(k,args={})=>d[k].replace(/\{(\w+)\}/g,(_,n)=>args[n])});
  assert.equal(result({result:'done',code:0}),lang==='zh'?'sudo 已完成（退出码 0）':'sudo finished (exit code 0)');
  assert.match(result({result:'done',code:17}),/17/);
  assert.match(result({result:'done'}),/—/);
  for(const status of ['stopped','timeout','failed'])assert.equal(result({result:status}),d['p406.sudo.'+status]);
 }
});
test('P479: preserve the localized source/override projections',()=>{
 const values=['zh','zh.src','zh.override'].map(lang=>JSON.parse(read('../i18n/web.'+lang+'.json'))['p479.sudo.done']);
 assert.equal(new Set(values).size,1);
});
