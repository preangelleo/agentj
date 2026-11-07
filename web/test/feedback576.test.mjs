import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {resolveDefaults} from '../tools/css-defaults.mjs';
import {readFileSync} from 'node:fs';
const read=p=>readFileSync(new URL('../public/'+p,import.meta.url),'utf8');
test('#576 shared element builder preserves specialised quote, reply and robot component classes',()=>{
 const source=read('js/ui.js').match(/export function mk\([^\n]+/)[0].replace('export ','');
 const document={createElement:tag=>({tag,className:'',classList:{add(){throw new Error('global button class injection')}}})};
 for(const cls of ['omq','busy-dots','opt','setnav']) {
  const node=vm.runInNewContext(source+`;mk('button',${JSON.stringify(cls)},'fixture')`,{document});
  assert.equal(node.className,cls); assert.equal(node.textContent,'fixture');
 }
});
test('#576 named refresh remains a labelled standard action instead of the icon-only rule',()=>{
 const html=read('index.html'),css=read('menu.css');
 assert.match(html,/<button[^>]*id="set-refresh"[^>]*class="aj-action aj-action--sm"/);
 assert.doesNotMatch(css,/html:root :is\([^)]*#set-refresh/);
 assert.match(css,/#settings #set-refresh\{[^}]*width:auto/);
});
test('#576 removes the global #542 interaction fill while retaining original quote and reply styling',()=>{
 const css=resolveDefaults(read('app.css'));
 assert.doesNotMatch(css,/body \.aj-action\.aj-action--primary:is\(:hover,:active,:focus\)/);
 assert.match(css,/\.omq \.qb\{[^}]*min-width:0;display:flex;flex-direction:column/);
 assert.match(css,/\.sug button/);
});
