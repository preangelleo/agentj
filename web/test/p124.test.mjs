import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src=readFileSync(new URL('../public/js/api.js',import.meta.url),'utf8');
const speech=readFileSync(new URL('../public/js/speak.js',import.meta.url),'utf8');
const reasons=['missing_key','provider_rejected','provider_busy','provider_unavailable','network','busy','not_ready'];
function context(){
 let request;const events=[];
 const ctx=vm.createContext({gen:()=>1,newId:()=> 'request',sendApp:async m=>{request=m;},Audio:class {constructor(){this.paused=true;}play(){this.paused=false;return Promise.resolve();}pause(){this.paused=true;}removeAttribute(){this.src="";}},setTimeout,clearTimeout,Blob,btoa,atob,Uint8Array,URL,window:{speechSynthesis:{cancel(){}}},onState:(...args)=>events.push(args)});
 vm.runInContext(src.slice(src.indexOf('const speechWait ='),src.indexOf('// P67:')).replaceAll('export function','function'),ctx);
 vm.runInContext(speech.replace(/^import .*;$/gm,'').replaceAll('export const','const').replaceAll('export function','function').replace('export class Speaker','class Speaker'),ctx);
 return {ctx,events,getRequest:()=>request};
}
test('P124 encrypted tts_end → speechAudio → Speaker preserves safe why and clears reader',async()=>{
 for(const why of reasons){
  const {ctx,events,getRequest}=context();
  const job=vm.runInContext("const reader=new Speaker(onState);reader.hostTap('turn')",ctx);
  const r=getRequest().r;vm.runInContext(`routeSpeech({t:'tts_end',r:${JSON.stringify(r)},ok:false,why:${JSON.stringify(why)}})`,ctx);
  await job;assert.deepEqual(events.at(-1),['turn','failed',why]);assert.equal(vm.runInContext('reader.id',ctx),null);
 }
});
test('P124 unknown provider payload cannot become UI text; old missing/offline map to not_ready',async()=>{
 const {ctx,events,getRequest}=context();const job=vm.runInContext("new Speaker(onState).hostTap('turn')",ctx);
 vm.runInContext(`routeSpeech({t:'tts_end',r:${JSON.stringify(getRequest().r)},ok:false,why:'PRIVATE_SENTINEL'})`,ctx);await job;
 assert.deepEqual(events.at(-1),['turn','failed','engine']);
 assert.equal(vm.runInContext("speechFailureWhy({why:'missing'})",ctx),'not_ready');
 assert.equal(vm.runInContext("speechFailureWhy({message:'offline'})",ctx),'not_ready');
});
test('P124 both languages expose actionable fixed speech guidance',()=>{
 for(const lang of ['en','zh']){const d=JSON.parse(readFileSync(new URL(`../i18n/web.${lang}.json`,import.meta.url)));for(const why of reasons)assert.ok(d['r.speak.err.'+why]);
 assert.match(d['r.speak.err.missing_key'],lang==='en'?/service environment.*restart/:/服务环境.*重启/);
 assert.match(d['r.speak.err.provider_rejected'],lang==='en'?/key, credit and voice/:/key、额度和音色/);}
});

test('P124 unlock happens before synthesis and blocked playback retries same element without synthesis',async()=>{
 const {ctx,events,getRequest}=context();
 vm.runInContext(`let calls=0,plays=0;Audio=class {constructor(){this.paused=true;calls++;}play(){plays++;if(plays===2){this.paused=true;return Promise.reject({name:'NotAllowedError'});}this.paused=false;return Promise.resolve();}pause(){this.paused=true;}removeAttribute(){this.src='';}};const reader=new Speaker(onState);`,ctx);
 const job=vm.runInContext("reader.hostTap('turn')",ctx);
 assert.equal(vm.runInContext('plays',ctx),1);
 vm.runInContext(`routeSpeech({t:'tts_chunk',r:'request',i:0,data:btoa('audio')});routeSpeech({t:'tts_end',r:'request',ok:true,bytes:5,mime:'audio/wav'})`,ctx);
 await job;assert.deepEqual(events.at(-1),['turn','failed','tap_play']);assert.equal(vm.runInContext("reader.state('turn')",ctx),'failed');
 await vm.runInContext("reader.hostTap('turn')",ctx);assert.equal(vm.runInContext('calls',ctx),1);assert.deepEqual(events.at(-1),['turn','playing',undefined]);
});

function phoneContext(){
 const c=context();vm.runInContext(`let voices=[],spoken=[],notices=[],listeners=new Set();window.speechSynthesis={getVoices:()=>voices,cancel(){},speak:u=>{spoken.push(u);u.onstart();},addEventListener:(n,f)=>listeners.add(f),removeEventListener:(n,f)=>listeners.delete(f)};window.SpeechSynthesisUtterance=class {constructor(text){this.text=text;}};const RelayMD={parse:t=>[{text:t}]};const phone=new Speaker(onState,why=>notices.push(why));`,c.ctx);return c;
}
test('P124 phone ignores cloud voice, falls back once, and keeps phone voice separate',async()=>{
 const {ctx,events}=phoneContext();vm.runInContext(`voices=[{name:'Local',voiceURI:'local',lang:'en-US',localService:true,default:true},{name:'Chosen',voiceURI:'chosen',lang:'en-GB',localService:true}];configureSpeech({tts:{mode:'phone',voice:'Anna Su',phone_voice:'missing'}});`,ctx);
 await vm.runInContext("phone.tap('first','Hello')",ctx);assert.equal(vm.runInContext('spoken[0].voice.name',ctx),'Local');assert.equal(events.at(-1)[1],'playing');
 await vm.runInContext("phone.tap('second','Hello')",ctx);assert.equal(vm.runInContext('notices.length',ctx),1);
 vm.runInContext("configureSpeech({tts:{mode:'phone',voice:'Anna Su',phone_voice:'chosen'}})",ctx);await vm.runInContext("phone.tap('third','Hello')",ctx);assert.equal(vm.runInContext('spoken.at(-1).voice.name',ctx),'Chosen');
});
test('P124 empty iOS voices waits for voiceschanged, cancels stale reply and removes listener',async()=>{
 const {ctx}=phoneContext();const job=vm.runInContext("phone.tap('first','Hello')",ctx);assert.equal(vm.runInContext('spoken.length',ctx),0);
 vm.runInContext("voices=[{name:'Local',lang:'en-US',localService:true,default:true}];for(const f of listeners)f()",ctx);await job;assert.equal(vm.runInContext('spoken.length',ctx),1);assert.equal(vm.runInContext('listeners.size',ctx),0);
 vm.runInContext('voices=[]',ctx);const stale=vm.runInContext("phone.tap('stale','Hello')",ctx);vm.runInContext("phone.stop();voices=[{name:'Local',lang:'en-US',localService:true}];for(const f of listeners)f()",ctx);await stale;assert.equal(vm.runInContext('spoken.length',ctx),1);
});
test('P124 voices timeout and network-only voices fail without sending reply remotely',async()=>{
 const {ctx,events}=phoneContext();await vm.runInContext("phone.tap('empty','Hello')",ctx);assert.deepEqual(events.at(-1),['empty','failed','novoice']);assert.equal(vm.runInContext('listeners.size',ctx),0);
 vm.runInContext("voices=[{name:'Network',lang:'en',localService:false}]",ctx);await vm.runInContext("phone.tap('network','Hello')",ctx);assert.deepEqual(events.at(-1),['network','failed','novoice']);assert.equal(vm.runInContext('spoken.length',ctx),0);
});
