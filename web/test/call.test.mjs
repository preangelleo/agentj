import {test} from 'node:test';
import assert from 'node:assert/strict';
import {VoiceCall,mountCall,VOICE_PROTOTYPE_RELEASED} from '../public/js/call.js';
function setup() {
  const sent=[],spoken=[],shown=[];let cancelled=0,ready=true,serial=0;
  const call=new VoiceCall({send:async m=>sent.push(m),ready:()=>ready,speak:t=>spoken.push(t),
    cancelSpeech:()=>cancelled++,show:t=>shown.push(t),now:()=>String(++serial).padStart(22,'a')});
  return {call,sent,spoken,shown,cancelled:()=>cancelled,offline:()=>{ready=false;}};
}
test('default off and capability opt in',async()=>{
  const h=setup();await assert.rejects(h.call.start(),/unavailable/);
  h.call.capability({enabled:true});await h.call.start();assert.equal(h.sent[0].t,'call_start');
});
test('PCM immediately sent before finish with sequence and little endian',async()=>{
  const h=setup();h.call.capability({enabled:true});await h.call.start();
  await h.call.pcm(new Int16Array([1000,-1000]));assert.equal(h.sent.at(-1).t,'call_pcm');
  assert.equal(h.sent.at(-1).seq,0);assert.equal(Buffer.from(h.sent.at(-1).pcm,'base64').readInt16LE(),1000);
  await h.call.pcm(new Int16Array([1]));assert.equal(h.sent.at(-1).seq,1);
  await h.call.finish();assert.equal(h.sent.at(-1).t,'call_finish');
});
test('partial/final display and ordered incremental sentences',async()=>{
  const h=setup();h.call.capability({enabled:true});await h.call.start();const id=h.call.id;
  h.call.event({id,kind:'partial',text:'part'});assert.deepEqual(h.shown,['part']);
  h.call.event({id,kind:'final',text:'final'});
  h.call.event({id,kind:'sentence',seq:1,text:'First.'});assert.deepEqual(h.spoken,['First.']);
  h.call.event({id,kind:'sentence',seq:2,text:'Last.'});assert.deepEqual(h.spoken,['First.','Last.']);
});
test('interruption cancels old audio and rejects old task/device events',async()=>{
  const h=setup();h.call.capability({enabled:true});await h.call.start();const old=h.call.id;
  await h.call.start();assert.ok(h.cancelled()>=2);
  h.call.event({id:old,kind:'sentence',seq:1,text:'stale'});
  h.call.event({id:'other-device',kind:'sentence',seq:1,text:'foreign'});assert.deepEqual(h.spoken,[]);
  assert.equal(h.sent.filter(m=>m.t==='call_stop').length,1);
});
test('unknown event and repeated/out of order sentences ignored',async()=>{
  const h=setup();h.call.capability({enabled:true});await h.call.start();const id=h.call.id;
  for(const m of [{kind:'unknown',seq:1},{kind:'sentence',seq:2},{kind:'sentence',seq:0}])h.call.event({id,text:'bad',...m});
  assert.deepEqual(h.spoken,[]);
});
test('size limit stops capture and bounds outstanding sends',async()=>{
  const h=setup();h.call.capability({enabled:true});await h.call.start();
  await assert.rejects(h.call.pcm(new Int16Array(3201)),/backpressure/);assert.equal(h.call.id,null);
});
test('stop and disconnect do not retry main submission',async()=>{
  const h=setup();h.call.capability({enabled:true});await h.call.start();h.offline();
  await h.call.stop();await h.call.pcm(new Int16Array([1]));
  assert.equal(h.sent.length,1);assert.equal(h.call.id,null);
});
test('finish and stop release microphone',async()=>{
  const h=setup();h.call.capability({enabled:true});await h.call.start();let released=0;
  h.call.releaseMic=async()=>released++;await h.call.finish();assert.equal(released,1);
  await h.call.stop();assert.equal(released,2);
});

test('release mount ignores an enabled capability without DOM, microphone or messages',async()=>{
  assert.equal(VOICE_PROTOTYPE_RELEASED,false);
  let sent=0;const panel=mountCall({send:async()=>sent++,ready:()=>true,lang:()=> 'en'});
  panel.capability({enabled:true});panel.event({kind:'sentence',text:'not released'});panel.reset();
  await Promise.resolve();assert.equal(sent,0);
});
