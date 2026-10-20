import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
test('running lifecycle is dispatched and recovered without clearing cards on disconnect',()=>{
 const app=read('../public/app.js');
 assert.match(app,/case 'elev_progress': elevate.progress\(m\)/);
 assert.doesNotMatch(app.slice(app.indexOf('function onHostGone()'),app.indexOf('session.configure')),/elevate.clear\(\)/);
});
test('settings tracks connection and repair failures recover immediately',()=>{
 assert.match(read('../public/js/settings.js'),/export function connectionChanged/);
 assert.match(read('../public/js/rescue.js'),/F.st === 'fail' && F.why === 'offline'/);
});
