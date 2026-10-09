import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {controlObject,controlMessage} from '../../protocol/wire.js';
import {assetRequest} from '../worker.ts';
test('bots owner writes bind all config/tool/knowledge fields to independent control signature',async()=>{
 const request={op:'tool_save',id:'a'.repeat(32),tool:{name:'lookup',enabled:true}};
 assert.equal(controlObject('bots_write',{request}),'{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","op":"tool_save","tool":{"enabled":true,"name":"lookup"}}');
 const a=await controlMessage('channel','owner','bots_write','a'.repeat(32),123,{request});
 const b=await controlMessage('channel','owner','bots_write','a'.repeat(32),123,{request:{...request,tool:{...request.tool,enabled:false}}});
 assert.notDeepEqual(a,b);
});
test('owner /bots route serves paired application, subroutes cannot alias management',()=>{
 assert.equal(new URL(assetRequest(new Request('https://m.agentj.app/bots')).url).pathname,'/');
 assert.equal(new URL(assetRequest(new Request('https://m.agentj.app/bots/fake')).url).pathname,'/bots/fake');
});
test('bots UI has required owner views, signed writes, text rendering and no raw key input',()=>{
 const src=readFileSync(new URL('../public/js/bots.js',import.meta.url),'utf8');
 for(const name of ['settings','limits','knowledge','tools','conversations','statistics','embed'])assert.ok(src.includes(name));
 assert.match(src,/signedWrite\('bots_write'/);assert.match(src,/bots_read/);assert.doesNotMatch(src,/innerHTML|localStorage|indexedDB|apiKey\s*:/);
 assert.match(src,/write.*approval every time/i);assert.match(src,/expires<=Date.now\(\)/);
});
