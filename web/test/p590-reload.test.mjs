import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createReloadGuard} from '../public/js/reload-guard.js';
const store=()=>{const m=new Map();return {getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,v)};};
test('P590 fourth automatic reload stops and reports once across page instances',()=>{
 const local=store(), session=store(), moves=[], notices=[], reports=[];let now=1000000;
 const make=()=>createReloadGuard({local,session,now:()=>now,wait:(f,ms)=>{moves.push(ms);f()},navigate:()=>{},report:r=>reports.push(r),notice:r=>notices.push(r)});
 for(let i=0;i<3;i++){assert.equal(make()('version.follow'),true);now+=1000;}
 assert.deepEqual(moves,[0,5000,30000]);
 assert.equal(make()('version.follow'),false);
 assert.equal(make()('version.follow'),false);
 assert.deepEqual(notices,['version.follow']);
 assert.equal(make()('settings.manual',{manual:true}),true);
 assert.equal(make()('version.follow'),false);
 return new Promise(resolve=>queueMicrotask(()=>{assert.deepEqual(reports,['version.follow']);resolve()}));
});
test('P590 preview bootstrap checks cookie and fails closed after formal reload',()=>{
 const code=readFileSync(new URL('../worker.ts',import.meta.url),'utf8');
 assert.match(code,/body\?\.op === "has"/);
 assert.match(code,/previewVerify\(proof, req, seat, pin\)/);
 assert.match(code,/if\(!active\)await fail\('formal_after_reload'\)/);
 assert.match(code,/if\(\(await post\(\{op:'has'\}\)\)\.has\)/);
 assert.match(code,/sessionStorage\.getItem\(failedKey\)/);
 assert.doesNotMatch(code,/location\.reload\(\)/);
});
