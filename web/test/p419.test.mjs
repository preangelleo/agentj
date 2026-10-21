import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {controlObject} from '../../protocol/wire.js';
test('friend cleanup uses the existing signed rescue action',()=>{
 assert.equal(controlObject('rescue_do',{action:'clear_friend_asks'}),'clear_friend_asks');
 const s=readFileSync(new URL('../public/js/rescue.js',import.meta.url),'utf8');
 assert.match(s,/start\('clear', 'clear_friend_asks'\)/);
 assert.match(s,/clear.disabled = go.disabled/);
 for(const lang of ['zh','en']) assert.ok(JSON.parse(readFileSync(new URL('../i18n/web.'+lang+'.json',import.meta.url)))['p419.clearFriendAsks']);
});
