import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
// Execute the real module with only DOM/event registration stubbed; no copied limit logic.
function remotes(host){
 const source=readFileSync(new URL('../public/js/remotes.js',import.meta.url),'utf8').replace(/^import .*$/gm,'').replace(/export /g,'');
 const m=new Function('el','onLang',source+';return {configure,count,onDevs};')(()=>({addEventListener(){}}),()=>{});
 m.configure({hostDevices:()=>host,myId:()=> 'remote0'});return m;
}
for(const n of [6,10])test(`P465 preferences and devs retain all ${n} devices and validated host limit`,()=>{
 const devices=Array.from({length:n},(_,i)=>({id:`remote${i}`}));
 for(const limit of [undefined,null,0,-1,65,'10',NaN])assert.deepEqual(remotes({devices,limit}).count(),{n,max:10});
 for(const limit of [5,10,12])assert.deepEqual(remotes({devices,limit}).count(),{n,max:limit},'valid server value wins');
 const m=remotes({devices});m.onDevs({items:devices,limit:12});assert.deepEqual(m.count(),{n,max:12});
 m.onDevs({items:devices,limit:0});assert.deepEqual(m.count(),{n,max:10});
});
