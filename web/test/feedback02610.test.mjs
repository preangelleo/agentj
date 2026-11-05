import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {RelayMD} from '../public/js/md.js';
import {rgba,over,surfaceContrast} from '../public/js/surface-outline.js';
for(const alias of [':codex-file-citation',':codex-citation',':file-citation']){
 test('#495 '+alias+' prose citation vanishes without local attachment nodes',()=>{
  const tree=JSON.stringify(RelayMD.parse('Summary\n'+alias+'{path="~/coding/inbox/own.pdf" purpose="source"}'));
  assert.ok(tree.includes('Summary'));assert.ok(!tree.includes(alias));assert.ok(!tree.includes('own.pdf'));assert.ok(!tree.includes('mslot'));
 });
 test('#495 '+alias+' literal fenced and inline code survives',()=>{
  const source=alias+'{path="~/coding/inbox/own.pdf" purpose="source"}';
  const fenced=RelayMD.parse('```text\n'+source+'\n```');
  assert.equal(fenced[0].tag,'pre');assert.equal(fenced[0].code,source);
  const inline=RelayMD.parse('`'+source+'`');
  assert.equal(inline[0].children[0].tag,'code');assert.equal(inline[0].children[0].children[0].text,source);
 });
}
test('#490 contrast uses composed actual backgrounds including translucent scrims',()=>{
 const noir=rgba('rgb(20, 20, 20)'),green=rgba('rgb(98, 152, 135)');
 assert.ok(surfaceContrast(noir,noir)<1.5);assert.ok(surfaceContrast(noir,green)>1.5);
 assert.ok(surfaceContrast(noir,over(rgba('rgba(20, 20, 20, 0.55)'),noir))<1.5);
});
test('2c goal uses existing dance, observes termination and consumes host boolean only',()=>{
 const logo=readFileSync('web/public/js/logo.js','utf8'),app=readFileSync('web/public/app.js','utf8');
 assert.match(logo,/dancing=outside \|\| b.dataset.goalActive==='1'/);assert.match(logo,/toggle\('father-dance',!!dancing\)/);
 assert.match(logo,/'data-goal-active'/);assert.match(app,/m.goal_active === true \? '1' : '0'/);
});
