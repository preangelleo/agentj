import test from 'node:test';
import assert from 'node:assert/strict';
import {createMicrophone} from '../public/js/microphone.js';

function fixture(){
 let visible=true,calls=0,resolve;
 const tracks=[];
 const gum=async()=>{calls++;const tr={readyState:'live',enabled:true,stop(){this.readyState='ended';this.enabled=false;}};tracks.push(tr);return {getTracks:()=>[tr],getAudioTracks:()=>[tr]};};
 const mic=createMicrophone({getUserMedia:gum,visible:()=>visible});
 return {mic,tracks,get calls(){return calls;},hide(){visible=false;mic.close();},show(){visible=true;},gum,
  deferred(){return createMicrophone({getUserMedia:()=>{calls++;return new Promise(r=>resolve=r);},visible:()=>visible});},resolve(s){resolve(s);}};
}
test('ten foreground takes stop every source; foreground return acquires only on a gesture',async()=>{
 const f=fixture();
 for(let i=0;i<10;i++){const take=await f.mic.acquire();assert.equal(take.stream.getAudioTracks()[0].enabled,true);take.release();assert.equal(f.tracks[i].readyState,'ended');}
 assert.equal(f.calls,10);f.hide();assert.equal(f.tracks[0].readyState,'ended');f.show();assert.equal(f.calls,10);
 const take=await f.mic.acquire();assert.equal(f.calls,11);take.release();
});
test('ended track reacquires; release from an old recorder cannot mute the next take',async()=>{
 const f=fixture(),a=await f.mic.acquire();a.release();const b=await f.mic.acquire();a.release();assert.equal(f.tracks[1].enabled,true);b.release();await f.mic.acquire();assert.equal(f.calls,3);f.mic.close();
});
test('pending authorization cannot deliver live audio after hide and return',async()=>{
 const f=fixture(),mic=f.deferred();const p=mic.acquire();mic.close();const stream=await f.gum();f.resolve(stream);
 await assert.rejects(p,{name:'AbortError'});assert.equal(stream.getTracks()[0].readyState,'ended');
});
test('hidden acquisition fails without asking the browser',async()=>{
 const f=fixture();f.hide();await assert.rejects(f.mic.acquire(),{name:'AbortError'});assert.equal(f.calls,0);
});
test('denial is retryable and simultaneous acquire is single-flight',async()=>{
 let calls=0,resolve;const track={readyState:'live',enabled:true,stop(){this.readyState='ended';}};
 const mic=createMicrophone({visible:()=>true,getUserMedia:()=>{calls++;if(calls===1)return Promise.reject(Error('denied'));return new Promise(r=>resolve=r);}});
 await assert.rejects(mic.acquire(),/denied/);const a=mic.acquire(),b=mic.acquire();resolve({getTracks:()=>[track],getAudioTracks:()=>[track]});
 const x=await a,y=await b;x.release();assert.equal(track.enabled,true);y.release();assert.equal(track.enabled,false);assert.equal(calls,2);mic.close();
});
test('cancelled permission resolving after a newer successful take cannot stop or replace it',async()=>{
 const pending=[]; const mic=createMicrophone({visible:()=>true,getUserMedia:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))});
 const source=()=>{const tracks=[0,1].map(()=>({readyState:'live',enabled:true,stop(){this.readyState='ended';}}));return {getTracks:()=>tracks,getAudioTracks:()=>tracks};};
 const old=mic.acquire();const rejected=assert.rejects(old,{name:'AbortError'});mic.close();
 const newer=mic.acquire(),fresh=source();pending[1].resolve(fresh);const take=await newer;
 const late=source();pending[0].resolve(late);await rejected;
 assert.ok(late.getTracks().every(t=>t.readyState==='ended'&&!t.enabled));assert.ok(fresh.getTracks().every(t=>t.readyState==='live'&&t.enabled));
 take.release();assert.ok(fresh.getTracks().every(t=>t.readyState==='ended'&&!t.enabled));
});
test('late denial from a cancelled permission cannot clear a newer in-flight request',async()=>{
 const pending=[];let calls=0;const mic=createMicrophone({visible:()=>true,getUserMedia:()=>{calls++;return new Promise((resolve,reject)=>pending.push({resolve,reject}));}});
 const old=mic.acquire();const denied=assert.rejects(old,{name:'NotAllowedError'});mic.close();const next=mic.acquire();
 pending[0].reject(new DOMException('Denied','NotAllowedError'));await denied;
 const also=mic.acquire();assert.equal(calls,2);
 const track={readyState:'live',enabled:true,stop(){this.readyState='ended';}};pending[1].resolve({getTracks:()=>[track],getAudioTracks:()=>[track]});
 const a=await next,b=await also;a.release();assert.equal(track.readyState,'live');b.release();assert.equal(track.readyState,'ended');
});
