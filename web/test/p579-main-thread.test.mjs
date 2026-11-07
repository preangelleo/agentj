import test from 'node:test';import assert from 'node:assert/strict';import {createHash}from'node:crypto';import {controlMessage,controlObject}from'../../protocol/wire.js';
test('p579 exact Host canonical target preserves numeric and null turns',async()=>{
 for(const turn of [42,null]){const request={turn,session:'a'.repeat(24)},text=JSON.stringify({session:request.session,turn});assert.equal(controlObject('main_thread_interrupt',{request}),text);const actual=await controlMessage('synthetic-channel','synthetic-device','main_thread_interrupt','b'.repeat(32),123456,{request});assert.equal(new TextDecoder().decode(actual),`agentjarvis-control-v1\nsynthetic-channel\nsynthetic-device\nmain_thread_interrupt\n${'b'.repeat(32)}\n123456\n${createHash('sha256').update(text).digest('hex')}`);}
});
test('p579 proof changes for stale turn/session/device/nonce/time',async()=>{
 const request={turn:42,session:'a'.repeat(24)},msg=(r=request,d='device',n='b'.repeat(32),ts=123)=>controlMessage('channel',d,'main_thread_interrupt',n,ts,{request:r}),original=await msg();for(const altered of [await msg({...request,turn:43}),await msg({...request,session:'c'.repeat(24)}),await msg(request,'other'),await msg(request,'device','c'.repeat(32)),await msg(request,'device','b'.repeat(32),124)])assert.notDeepEqual(altered,original);
});
