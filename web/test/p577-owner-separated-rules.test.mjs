import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {rules} from '../tools/css-syntax.mjs';
import {auditRule,splitSelectors} from '../tools/ownership-contract.mjs';

const web=fileURLToPath(new URL('../',import.meta.url));
const semantic=JSON.parse(readFileSync(web+'component-ownership.json','utf8'));
semantic.idOwners=JSON.parse(readFileSync(web+'css-node-owners.json','utf8')).idOwners;
const controls=JSON.parse(readFileSync(web+'control-ownership-sites.json','utf8'));
const families=[...new Set([...controls.owners,...semantic.components.map(c=>c.namespace),...Object.keys(semantic.cssRoles||{})])];
const authored=rules(readFileSync(web+'public/app.css','utf8'));

test('approval and auth cards retain identical appearance without a shared-owner declaration',()=>{
 const pairs=[
  ['#sheet[data-approval-v="2"]','.approval-special541'],
  ['#sheet[data-approval-v="2"] #sheetTitle','.approval-special541 h2'],
  ['.source541','.auth-source541'],
  ['.srcmeta541','.auth-meta541'],
  ['.srcmeta541>span','.auth-meta541>span'],
  ['#sheet[data-approval-v="2"] .action541','.approval-special541 .auth-action541'],
 ];
 for(const [left,right] of pairs){
  const l=authored.filter(r=>r.head===left),r=authored.filter(rule=>rule.head===right);
  assert.ok(l.length && r.length,`${left} / ${right} must be present`);
  const shared=l.filter(a=>r.some(b=>a.body===b.body&&JSON.stringify(a.context)===JSON.stringify(b.context)));
  assert.ok(shared.length,`${left} / ${right} must preserve the formerly shared appearance`);
  for(const rule of [...l,...r]) assert.ok(!auditRule(rule,semantic,families).issues.includes('cross-owner-selector'),rule.head);
 }
});

test('cross-owner command controls never share one CSS declaration',()=>{
 const heads=['.fixbtn:disabled','.fixbtn.off','.codeblock .bar .term-command:disabled','.codeblock .bar .term-command.off'];
 const group=heads.map(h=>authored.find(r=>r.head===h));
 assert.ok(group.every(Boolean));
 assert.ok(group.every(r=>r.body===group[0].body));
 assert.ok(group.every(r=>splitSelectors(r.head).length===1));
});
