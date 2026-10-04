// Regression for a native acquisition promise which never settles, or settles
// after the owner cancels. Original recording parity remains unchanged.
import assert from 'node:assert/strict';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,evaluate,waitFor,waitState,key,sleep} from './browser.mjs';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
try {
 const P=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
 await navigate(P,fake.newPairing(web.url));await waitState(P,'awaiting-approval');await fake.approve();await waitState(P,'ready');
 await evaluate(P,`document.activeElement.blur(); window.nativeGum=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); window.late=[]; window.stopped=0; window.captureErrors=[]; window.addEventListener('agentj-audio-error',e=>window.captureErrors.push(e.detail)); navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.late.push(resolve));`);
 const down=()=>key(P,'m',{code:'KeyM',type:'down'}),up=()=>key(P,'m',{code:'KeyM',type:'up'});
 await down();await waitFor(P,'window.late.length === 1');await waitFor(P,"document.getElementById('ptt').hidden && window.captureErrors.some(e=>e.stage==='capture' && e.error==='TimeoutError')",17000);await up();
 // A later request can start; a late stream from the timed-out take is closed
 // immediately and cannot replace the current take or its UI.
 await down();await waitFor(P,'window.late.length === 2');await evaluate(P,`window.late[0]({getTracks:()=>[{stop:()=>window.stopped++}]})`);await waitFor(P,'window.stopped === 1');assert.equal(await evaluate(P,'document.getElementById("ptt").hidden'),false);
 await up();await waitFor(P,'document.getElementById("ptt").hidden');await evaluate(P,`window.late[1]({getTracks:()=>[{stop:()=>window.stopped++}]})`);await waitFor(P,'window.stopped === 2');
 await evaluate(P,'navigator.mediaDevices.getUserMedia=window.nativeGum');await down();await waitFor(P,`/^\\d+:\\d\\d$/.test(document.getElementById('pttTime').textContent)`,3000);await sleep(400);await up();await waitFor(P,'document.getElementById("ptt").hidden');
 assert.equal(P.problems.length,0,P.problems.join('\n'));console.log('PASS microphone acquisition timeout, cancellation, late-stream isolation, native retry');await P.dispose();
}finally{await B.close();await web.stop();await fake.stop();}
