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
test('ten foreground takes use one acquisition; idle is disabled, hide stops it, return does not acquire',async()=>{
 const f=fixture();
 for(let i=0;i<10;i++){const take=await f.mic.acquire();assert.equal(take.stream.getAudioTracks()[0].enabled,true);take.release();assert.equal(f.tracks[0].enabled,false);}
 assert.equal(f.calls,1);f.hide();assert.equal(f.tracks[0].readyState,'ended');f.show();assert.equal(f.calls,1);
 const take=await f.mic.acquire();assert.equal(f.calls,2);take.release();
});
test('ended track reacquires; release from an old recorder cannot mute the next take',async()=>{
 const f=fixture(),a=await f.mic.acquire();a.release();const b=await f.mic.acquire();a.release();assert.equal(f.tracks[0].enabled,true);b.release();f.tracks[0].stop();await f.mic.acquire();assert.equal(f.calls,2);f.mic.close();
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
