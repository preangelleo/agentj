// Remaining microphone lifecycle races on the real Noise-paired page. Instrumented permission/recorder seam.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {launch,newPage,navigate,waitState,evaluate,waitFor,sleep} from './browser.mjs';
const web=await startWebServer(),fake=await startFakeHost(),B=await launch();
const out='reports/qa/p493-races';mkdirSync(out,{recursive:true});
try {
 const p=await newPage(B,390,844,'dark',{allow:[web.url,fake.relay+'/']});
 await p.send('Page.addScriptToEvaluateOnNewDocument',{source:`
 window.AudioContext=window.webkitAudioContext=undefined;
 window.__requests=[];window.__tracks=[];window.__recs=[];window.__recFail=false;
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:()=>new Promise((resolve,reject)=>window.__requests.push({resolve,reject}))}});
 window.__source=()=>{const tracks=[0,1].map(()=>Object.assign(new EventTarget(),{enabled:true,readyState:'live',stop(){this.enabled=false;this.readyState='ended';},getSettings:()=>({noiseSuppression:false})}));window.__tracks.push(tracks);return {getTracks:()=>tracks,getAudioTracks:()=>tracks};};
 window.MediaRecorder=class{static isTypeSupported(){return true;}constructor(s){if(window.__recFail)throw Error('recorder failed');this.stream=s;this.state='inactive';this.mimeType='audio/webm';window.__recs.push(this);}start(){this.state='recording';}requestData(){}stop(){this.state='inactive';setTimeout(()=>this.onstop?.(),20);}};
 `});
 await navigate(p,fake.newPairing(web.url+'?lang=en'));await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');await evaluate(p,"document.getElementById('phone-env-dismiss')?.click()");
 const begin=async n=>{await evaluate(p,"document.getElementById('mic').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'}))");await waitFor(p,`window.__requests.length===${n}`);};
 const end=()=>evaluate(p,"document.getElementById('mic').dispatchEvent(new KeyboardEvent('keyup',{key:'Enter'}))");
 await begin(1);await end();await begin(2);
 await evaluate(p,"__requests[1].resolve(__source())");await waitFor(p,'__recs.length===1');
 await evaluate(p,"__requests[0].resolve(__source())");await sleep(60);
 assert.ok(await evaluate(p,"__tracks[0].every(t=>t.enabled&&t.readyState==='live')&&__tracks[1].every(t=>!t.enabled&&t.readyState==='ended')"));
 await evaluate(p,"dispatchEvent(new Event('pagehide'))");
 assert.ok(await evaluate(p,"__tracks.every(ts=>ts.every(t=>!t.enabled&&t.readyState==='ended'))&&__recs.every(r=>r.state==='inactive')"));
 const count=await evaluate(p,'__requests.length');await evaluate(p,"dispatchEvent(new Event('pageshow'));document.dispatchEvent(new Event('visibilitychange'))");await sleep(60);assert.equal(await evaluate(p,'__requests.length'),count);
 await begin(3);await evaluate(p,"__requests[2].reject(new DOMException('Denied','NotAllowedError'))");await sleep(60);
 await begin(4);await evaluate(p,"__requests[3].resolve(__source())");await waitFor(p,'__recs.length===2');await end();
 assert.ok(await evaluate(p,"__tracks.every(ts=>ts.every(t=>t.readyState==='ended'))"));
 // pagehide while browser permission is pending, then late success must be discarded.
 await begin(5);await evaluate(p,"dispatchEvent(new Event('pagehide'));__requests[4].resolve(__source())");await sleep(60);
 assert.ok(await evaluate(p,"__tracks.every(ts=>ts.every(t=>t.readyState==='ended'&&!t.enabled))&&__recs.length===2"));
 // MediaRecorder constructor failure must release all tracks too.
 await begin(6);await evaluate(p,"__recFail=true;__requests[5].resolve(__source())");await sleep(60);
 assert.ok(await evaluate(p,"__tracks.every(ts=>ts.every(t=>t.readyState==='ended'&&!t.enabled))"));
 const result={cancelLateAfterNew:true,pagehideActive:true,returnPassive:true,deniedRetry:true,pagehidePendingLate:true,recorderFailureReleased:true};
 writeFileSync(out+'/results.json',JSON.stringify(result,null,2)+'\n');console.log('PASS '+JSON.stringify(result));
}finally{await B.close();await fake.stop();await web.stop();}
