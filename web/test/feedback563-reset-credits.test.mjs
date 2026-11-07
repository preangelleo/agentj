import {test} from 'node:test';
import assert from 'node:assert/strict';
globalThis.window = {};
const {resetCreditText} = await import('../public/js/reset-credits.js');
const snap = await import('../public/js/snap.js');
const at=Date.UTC(2026,10,7,12)/1000;
test('563 confirmed zero and absent information are distinct',()=>{
 assert.equal(resetCreditText({status:'ok',count:0}), '重置券 0 张');
 for(const v of [undefined,null,{}, {status:'error',count:0},{status:'unavailable',count:null},{status:'ok',count:-1},{status:'ok',count:NaN}]) assert.equal(resetCreditText(v),'重置券信息暂不可用');
});
test('563 locale nearest expiry, multiple credits and non-expiring credit',()=>{
 assert.match(resetCreditText({status:'ok',count:3,nearest_expires_at:at}),/^重置券 3 张，最近一张 11月7日到期$/);
 assert.equal(resetCreditText({status:'ok',count:1,nearest_expires_at:null}),'重置券 1 张');
 window.AJLang={get:()=> 'en',t:(dict,key,vars)=>dict.en[key].replace(/\{(\w+)\}/g,(_,k)=>vars?.[k]??'{'+k+'}')};
 assert.equal(resetCreditText({status:'ok',count:3,nearest_expires_at:at}),'3 reset credits; next expires November 7');
 delete window.AJLang;
});
test('563 malformed expiry cannot inject text or invent a date',()=>{
 for(const at of ['<img src=x onerror=alert(1)>',NaN,Infinity,-1,1e100,{}]) assert.equal(resetCreditText({status:'ok',count:1,nearest_expires_at:at}),'重置券信息暂不可用');
});
test('563 encrypted meter snapshot keeps normalized reset metadata',()=>{
 snap.configure({onChange(){},onTurn(){}});
 const reset_credits={status:'ok',count:2,nearest_expires_at:at};
 snap.setMeter({reset_credits});assert.deepEqual(snap.snapshot().usage.reset_credits,reset_credits);
 snap.setMeter({});assert.equal(snap.snapshot().usage.reset_credits,undefined);
});
