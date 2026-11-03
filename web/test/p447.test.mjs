import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const base = new URL('../', import.meta.url);
const source = readFileSync(new URL('public/js/pair-guide.js',base),'utf8').replace(/^import .*;$/gm,'').replace(/export (function|const)/g,'$1');
for (const lang of ['zh','en']) test(`${lang}: success guidance renders device-specific next steps`,()=>{
 const d=JSON.parse(readFileSync(new URL(`i18n/web.${lang}.json`,base))).pairGuide;
 for(const kind of ['phone','computer']){
  const shown=[];   // #435: a fresh device each time (no "seen" mark), exactly one remote paired
  const c=vm.createContext({t:k=>d[k.split('.')[1]],notifyOnce:(spec)=>shown.push({text:spec.text,ms:spec.ms,k:spec.kind})});
  vm.runInContext(source,c); vm.runInContext('noteRemotes({items:[{}]})',c);
  vm.runInContext(`showPairGuide('other')`,c); assert.equal(shown.length,0);
  vm.runInContext(`showPairGuide('${kind}')`,c);
  assert.deepEqual(shown,[{text:d[kind],ms:10000,k:'info'}]);
  assert.ok(kind==='phone'?shown[0].text.includes('m.agentj.app'):shown[0].text.includes(lang==='zh'?'添加遥控器':'Add a remote'));
 }
});
test('host success hint is routed to the toast; no resident element',()=>{
 const app=readFileSync(new URL('public/app.js',base),'utf8');
 assert.ok(app.includes("case 'pair_guide': showPairGuide(m.kind); return;"));
 assert.ok(!app.includes("$('pair-guide')"));
 const html=readFileSync(new URL('public/index.html',base),'utf8');
 assert.doesNotMatch(html, /id="pair-guide"/);
});
