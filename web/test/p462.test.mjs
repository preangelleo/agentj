import { test } from 'node:test';
import assert from 'node:assert/strict';
const mem = new Map();
globalThis.localStorage = { getItem: k => mem.get(k) ?? null, setItem: (k,v) => mem.set(k,v) };
const o = await import('../public/js/toolorder.js');
const ids = ['file','image','camera','mic','full'];
const perms = a => a.length ? a.flatMap((v,i) => perms(a.filter((_,j)=>i!==j)).map(p=>[v,...p])) : [[]];
test('P462 all 24 legacy archives migrate without losing order, including host broadcasts', () => {
  for(const old of perms(ids.slice(0,4))) {
    mem.set('aj.toolOrder',JSON.stringify(old));
    assert.deepEqual(o._reload(),[...old,'full']);
    o.fromHost({ui:{composer_buttons:old}});
    assert.deepEqual(o.order(),[...old,'full']);
    assert.deepEqual(JSON.parse(mem.get('aj.toolOrder')),[...old,'full']);
  }
});
test('P462 all 120 five-item permutations paint and survive reload', () => {
  const row = {children:[],dataset:{},append(n){this.children.splice(this.children.indexOf(n),1);this.children.push(n)}};
  const nodes = Object.fromEntries(ids.map(id=>[o.TOOL_DOM[id],{id:o.TOOL_DOM[id],parentElement:row}]));
  row.children = Object.values(nodes);
  const doc = {getElementById:id=>nodes[id],activeElement:null};
  for(const p of perms(ids)) {
    assert.equal(o.isOrder(p),true); o.set(p); o.paint(doc);
    assert.deepEqual(row.children.map(n=>n.id),p.map(id=>o.TOOL_DOM[id]));
    assert.deepEqual(o._reload(),p);
  }
});
test('P462 incomplete, duplicate and unknown five-item archives use default', () => {
  for(const p of [['full','file','image','camera'],['full','full','file','image','mic'],['file','image','camera','mic','other']])
    assert.deepEqual(o.normalize(p),ids);
});
