import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
test('P589 bilingual cost and quiet-hour instructions exist',()=>{
 for(const l of ['zh','en']){const d=JSON.parse(read('../i18n/web.'+l+'.json'));for(const k of ['quiet','start','end','quietNote','note','usage','tokens','detail','unavailable','scope','partial'])assert.ok(d['p589.'+k]);}
 assert.match(read('../public/js/settings.js'),/quietKnown/);
 assert.match(read('../public/js/snap.js'),/heartbeat_usage: m\.heartbeat_usage/);
});
