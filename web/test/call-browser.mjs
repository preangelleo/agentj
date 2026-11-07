import {VOICE_PROTOTYPE_RELEASED} from '../public/js/call.js';
// Release gate is tested first. The retained capture laboratory runs only after a future explicit release decision.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,evaluate,navigate,waitState,waitFor,sleep} from './browser.mjs';
if (!VOICE_PROTOTYPE_RELEASED) {
 const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
 let p;
 try {
  p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/']});
  await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
  await evaluate(p,`window.__gateMic=0;const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=(...args)=>{window.__gateMic++;return original(...args)}`);
  await fake.send({t:'call_capability',enabled:true});await sleep(150);
  assert.equal(await evaluate(p,`!!document.getElementById('voice-call')`),false);
  assert.equal(await evaluate(p,`window.__gateMic`),0);
  assert.equal(fake.log.filter(m=>m.t?.startsWith('call_')).length,0);
  console.log('PASS unreleased voice mount: enabled capability cannot expose UI, acquire microphone or send call packets; prototype capture remains unqualified');
 }finally{await p?.dispose();await B.close();await fake.stop();await web.stop();}
} else {
const root=fileURLToPath(new URL('../../../',import.meta.url));
const py=spawn('python3',['-u',root+'host/tests/voice_stream_fixture.py'],{
  cwd:root,env:{...process.env,PYTHONPATH:root+'agentjarvis/host:'+root+'host/tests'},stdio:['pipe','pipe','pipe']});
let stderr='';py.stderr.on('data',d=>stderr+=d);
const events=[],packets=[];let forward;
createInterface({input:py.stdout}).on('line',line=>{
  const m=JSON.parse(line);events.push({at:performance.now(),...m});
  if(m.t==='call_event')void forward?.(m);
});
const web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});
const fake=await startFakeHost();let B,p;let passed=0;
try {
  B=await launch({args:['--use-file-for-fake-audio-capture='+root+'agentjarvis/site/static/web-report/gallery/R06-audio/assets/episode.wav']});p=await newPage(B,390,844,'light',{allow:[web.url,fake.relay+'/'],touch:true});
  await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');
  await fake.approve();await waitState(p,'ready');
  assert.equal(await evaluate(p,"!!document.getElementById('voice-call')"),false);passed++;
  fake.st.onApp=async(c,m,send)=>{
    if(!m.t.startsWith('call_'))return false;
    forward=send;packets.push({at:performance.now(),...m});py.stdin.write(JSON.stringify(m)+'\n');return true;
  };
  await evaluate(p,`(()=>{
    window.__callSpoken=[];window.__callCancel=0;
    speechSynthesis.getVoices=()=>[{name:'synthetic-local',localService:true,lang:'en-US'}];
    speechSynthesis.speak=u=>window.__callSpoken.push({text:u.text,at:performance.now()});
    speechSynthesis.cancel=()=>window.__callCancel++;
  })()`);
  await fake.send({t:'call_capability',enabled:true});
  await waitFor(p,"!!document.getElementById('voice-call') && !document.getElementById('voice-call').hidden");passed++;
  await evaluate(p,"document.getElementById('voice-call-start').click()");
  // Start handler with a synthetic pointer (capture itself uses real getUserMedia/AudioContext).
  await evaluate(p,`(()=>{const b=document.getElementById('voice-call-hold');b.setPointerCapture=()=>{};b.onpointerdown({pointerId:1});})()`);
  for(let i=0;i<100&&!events.some(m=>m.kind==='partial');i++)await sleep(20);
  const partial=events.find(m=>m.kind==='partial');assert.ok(partial,stderr);
  assert.equal(events.some(m=>m.kind==='final'),false);assert.ok(packets.some(m=>m.t==='call_pcm'));passed++;
  await evaluate(p,"document.getElementById('voice-call-hold').onpointerup()");
  await waitFor(p,'window.__callSpoken.length===2',8000);
  assert.equal(events.filter(m=>m.t==='fixture_submission').length,1);
  const final=events.find(m=>m.kind==='final'),sentences=events.filter(m=>m.kind==='sentence');
  assert.ok(partial.at<final.at);assert.ok(sentences[0].at<sentences[1].at);passed++;
  const old=packets.find(m=>m.t==='call_start').id;
  await evaluate(p,"document.getElementById('voice-call-hold').onpointerdown({pointerId:2})");
  await sleep(100);await fake.send({t:'call_event',id:old,kind:'sentence',seq:3,text:'stale'});
  assert.equal(await evaluate(p,'window.__callSpoken.some(m=>m.text===\'stale\')'),false);
  assert.ok(await evaluate(p,'window.__callCancel>=2'));passed++;
  await evaluate(p,"document.getElementById('voice-call-end').click()");
  await fake.send({t:'call_capability',enabled:false});
  await waitFor(p,"document.getElementById('voice-call').hidden");passed++;
  const pcm=packets.find(m=>m.t==='call_pcm');
  console.log(JSON.stringify({passed,failed:0,coverage:'real Chromium getUserMedia/AudioContext/PCM + real Noise + Python Manager/Call; fake ASR/model, intercepted local speechSynthesis',
    pcmBytes:Buffer.from(pcm.pcm,'base64').length,latencyMs:{pcmToPartial:partial.at-pcm.at,pcmToFinal:final.at-pcm.at,
    pcmToFirstSentence:sentences[0].at-pcm.at,pcmToLastSentence:sentences[1].at-pcm.at},
    NOT_VERIFIED:['real Qwen WS dialect','real Agent token streaming','audible phone TTS','physical phone','production relay']},null,2));
} catch(error) {
  console.error('FAILED_DIAGNOSTICS',JSON.stringify({events,packets:packets.map(({pcm,...m})=>({...m,pcmBytes:pcm?Buffer.from(pcm,'base64').length:0,rms:pcm?(()=>{const b=Buffer.from(pcm,'base64');let sum=0;for(let i=0;i<b.length;i+=2)sum+=b.readInt16LE(i)**2;return Math.sqrt(sum/(b.length/2));})():0})),stderr}));
  throw error;
} finally {
  await p?.dispose();await B?.close();await fake.stop();await web.stop();py.stdin.end();
}

}
