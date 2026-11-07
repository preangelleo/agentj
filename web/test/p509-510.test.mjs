import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {rules, declarations} from '../tools/css-syntax.mjs';

test('#509 bottom sheets share the composer ground without a border or outline',()=>{
  const css=readFileSync(new URL('../public/app.css',import.meta.url),'utf8');
  assert.match(css,/\.sheet:not\(\.cp-docked\)\s*\{background:var\(--pg\);border:0;outline:none\}/);
  // A comma group may be emitted as independent rules; every original selector keeps the exact declarations.
  const outlineContract=['.toast.aj-surface-outline','.sheet.aj-surface-outline','.ajmodal .box.aj-surface-outline'];
  const relevant=rules(css).filter(r=>r.head.split(',').some(s=>outlineContract.includes(s.trim())));
  assert.deepEqual(relevant.flatMap(r=>r.head.split(',').map(s=>s.trim())).sort(),outlineContract.slice().sort(),'exact original selector branches; no added targets or duplicates');
  for(const selector of outlineContract) {
    const matching=rules(css).filter(r=>r.head.split(',').map(s=>s.trim()).includes(selector));
    assert.equal(matching.length,1,'one effective surface outline rule: '+selector);
    assert.deepEqual(declarations(matching[0].body).map(({property,value})=>[property,value]),[['outline','2px solid var(--emerald-oasis)'],['outline-offset','-2px']],selector);
  }
});
test('#510 service category survives host snapshot filtering',()=>{
  const snap=readFileSync(new URL('../public/js/snap.js',import.meta.url),'utf8');
  assert.match(snap,/const CATS = \[[^\]]*'service'/);
  for(const l of ['zh','en']){
    const d=JSON.parse(readFileSync(new URL(`../i18n/web.${l}.json`,import.meta.url),'utf8'));
    assert.ok(d['ask.cat.service']);
  }
});

import {approvalNote} from '../public/js/approval-note.js';
const zh=JSON.parse(readFileSync(new URL('../i18n/web.zh.json',import.meta.url),'utf8'));
const tr=(key,v)=>String(zh[key]||key).replace(/\{(\w+)\}/g,(_,k)=>v[k]);
test('#510 Chinese UI explains English signed notes by category; English and original Chinese stay intact',()=>{
  assert.equal(approvalNote('Restart in 45 seconds',{cats:['service']},'zh',tr,'运行一条命令'),'重启 Agent J 服务；Agent 原始说明见「展开全部」');
  assert.equal(approvalNote('Restart in 45 seconds',{cats:['service']},'en',tr,'Run command'),'Restart in 45 seconds');
  assert.equal(approvalNote('重启 Agent J 服务',{cats:['service']},'zh',tr,'运行一条命令'),'重启 Agent J 服务');
  assert.equal(approvalNote('',{cats:['service']},'zh',tr,'运行一条命令'),'');
  assert.equal(approvalNote('Run command',{cats:['unknown']},'zh',tr,'运行一条命令'),'运行一条命令；Agent 原始说明见「展开全部」');
});

test('#510 ASCII Schedule is not Chinese',()=>{assert.equal(approvalNote('Schedule',{cats:['service']},'zh',tr,'运行一条命令'),'重启 Agent J 服务；Agent 原始说明见「展开全部」');});
