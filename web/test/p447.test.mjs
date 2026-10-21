import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const base = new URL('../', import.meta.url);
const source = readFileSync(new URL('public/js/pair-guide.js',base),'utf8').replace(/^import .*;$/gm,'').replace('export function','function');
for (const lang of ['zh','en']) test(`${lang}: success guidance renders device-specific next steps and relanguages`,()=>{
 const d=JSON.parse(readFileSync(new URL(`i18n/web.${lang}.json`,base))).pairGuide;
 const node={hidden:true,dataset:{}}; let callback;
 const c=vm.createContext({el:()=>node,t:k=>d[k.split('.')[1]],onLang:f=>{callback=f;}});
 vm.runInContext(source,c);
 for(const kind of ['phone','computer']){
  vm.runInContext(`showPairGuide('${kind}')`,c);
  assert.equal(node.hidden,false); assert.equal(node.textContent,d[kind]);
  assert.ok(kind==='phone'?node.textContent.includes('m.agentj.app'):node.textContent.includes(lang==='zh'?'添加遥控器':'Add a remote'));
  callback(); assert.equal(node.textContent,d[kind]);
 }
 node.hidden=true; vm.runInContext("showPairGuide('other')",c); assert.equal(node.hidden,true);
});
test('host success hint is routed and hidden on a new pairing',()=>{
 const app=readFileSync(new URL('public/app.js',base),'utf8');
 assert.ok(app.includes("case 'pair_guide': showPairGuide(m.kind); return;"));
 assert.ok(app.includes("$('pair-guide').hidden = true"));
 const html=readFileSync(new URL('public/index.html',base),'utf8');
 assert.match(html, /id="pair-guide"[^>]*role="status"[^>]*hidden/);
});
