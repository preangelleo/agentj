import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const html=read('../public/index.html'), settings=read('../public/js/settings.js');
test('#511: editable settings contain no duplicate command or copy control',()=>{
 for(const id of ['risk','jev','goal','qswitch','mode','iso','docker']) {
  assert.doesNotMatch(html,new RegExp(`(?:id|data-copy)="set-${id}-cmd"`));
  assert.doesNotMatch(settings,new RegExp(`el\\('set-${id}-cmd'\\)`));
  assert.match(html,new RegExp(`id="set-${id==='jev'?'jev-minus':id}"`));
 }
});
test('#511: command-only compatibility fallback and collapsed command runner remain',()=>{
 for(const id of ['set-lang-cmd-code','set-pref-cmd-code','set-term-cmd']) assert.match(html,new RegExp(`data-copy="${id}"`));
 assert.match(html,/id="rs-cmd-list"/);
 assert.match(settings,/showCmd\(key, value\)/);
});
