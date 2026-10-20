// P393 #129 / #131 / #132: official cards default on, a drafted slash command gets its trailing space, card words are Lotion
// (the browser proofs are p360.mjs and p386.mjs; the host side is host/tests/test_p360_cards.py).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const src=(f)=>readFileSync(new URL('../public/'+f,import.meta.url),'utf8');
test('#131: a drafted slash command ends in a space, other drafts stay as written',()=>{
  const line=src('js/jcards.js').split('\n').find((l)=>l.startsWith('export const draftText'));
  const draftText=new Function('return '+line.replace('export const draftText = ','').replace(/;$/,''))();
  assert.equal(draftText('/agentj-add-todo'),'/agentj-add-todo ');
  assert.equal(draftText('/agentj-add-note'),'/agentj-add-note ');
  assert.equal(draftText('记一下 /agentj-add-note'),'记一下 /agentj-add-note ');
  assert.equal(draftText('/agentj-add-todo '),'/agentj-add-todo ');
  assert.equal(draftText('写一条待办'),'写一条待办');
  assert.match(src('js/jcards.js'),/if \(a\.draft\) \{ hooks\.draft\(draftText\(text\)\); return; \}/);
  assert.match(src('js/five-pages.js'),/const cmd='\/'\+plusSkill\(\)\+' '/);   // the header + already does
});
test('#132: every card accent writes Lotion, the colour picker counts contrast against Lotion',()=>{
  const css=src('cards.css');
  for(const a of ['emerald-oasis','windstorm','magic-carpet','peachy-feeling','noir']) assert.match(css,new RegExp(`\\.jc-accent-${a}\\{--jc-fill:#[0-9a-f]{6};--jc-ink:#faf9f5\\}`));
  const js=src('js/jcard-color.js');
  assert.match(js,/export function ink\(hex\) \{ return \{color:LOTION,/);
  assert.doesNotMatch(js,/ratio<4\.5/);
  assert.match(js,/SYSTEM=\['#629887','#6a9bcc','#827dbd','#d97757','#2c2b26'\]/);
});
