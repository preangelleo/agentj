// WebKit executes the actual Speaker with a controlled delayed synthesis and gesture policy.
// This regression is not physical iPhone audio acceptance.
import {createRequire} from 'node:module';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {webkit}=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/aj-webmcp-playwright/node_modules/playwright');
const browser=await webkit.launch();
const src=readFileSync(new URL('../public/js/speak.js',import.meta.url),'utf8').replace(/^import .*;$/gm,'').replaceAll('export const','const').replaceAll('export function','function').replace('export class Speaker','class Speaker');
try {
 const page=await browser.newPage();
 await page.setContent('<button id="tap">Read</button>');
 await page.addScriptTag({content:`let deny=false,gesture=false,created=0,requests=0,plays=[],states=[],finish;const windowAudio=class {constructor(){created++;this.paused=true;this.authorized=false;}play(){plays.push({gesture,src:this.src});if(deny){deny=false;this.paused=true;return Promise.reject(new DOMException('gesture','NotAllowedError'));}if(gesture)this.authorized=true;if(!this.authorized)return Promise.reject(new DOMException('gesture','NotAllowedError'));this.paused=false;return Promise.resolve();}pause(){this.paused=true;}removeAttribute(){this.src='';}};window.Audio=windowAudio;const speechAudio=()=>{requests++;return new Promise(resolve=>finish=()=>resolve(new Blob(['audio'],{type:'audio/wav'})));};${src};const reader=new Speaker((...v)=>states.push(v));document.querySelector('#tap').onclick=()=>{gesture=true;reader.hostTap('one');gesture=false;};window.snapshot=()=>({created,requests,plays,states});window.complete=()=>finish();window.auto=()=>reader.hostTap('two');`});
 await page.click('#tap');let v=await page.evaluate(()=>snapshot());assert.equal(v.created,1);assert.equal(v.plays[0].gesture,true);assert.equal(v.requests,1);
 await page.evaluate(()=>complete());await page.waitForFunction(()=>snapshot().states.some(s=>s[1]==='playing'));
 await page.evaluate(()=>reader.stop());await page.evaluate(()=>{auto();complete();});await page.waitForFunction(()=>snapshot().states.some(s=>s[0]==='two'&&s[1]==='playing'));
 await page.evaluate(()=>{reader.stop();deny=true;reader.hostTap('three');complete();});await page.waitForFunction(()=>snapshot().states.some(s=>s[2]==='tap_play'));
 await page.evaluate(()=>reader.hostTap('three'));await page.waitForFunction(()=>snapshot().states.some(s=>s[0]==='three'&&s[1]==='playing'));
 v=await page.evaluate(()=>snapshot());assert.equal(v.created,1);assert.equal(v.requests,3);assert.equal(v.plays.at(-1).gesture,false);
 const phone=await browser.newPage();await phone.setContent('<button id="local">Read local</button>');
 await phone.addScriptTag({content:`let voices=[],spoken=[],notices=[],states=[],listeners=new Set();const ss={getVoices:()=>voices,cancel(){},speak:u=>{spoken.push({name:u.voice.name,text:u.text});u.onstart();},addEventListener:(n,f)=>listeners.add(f),removeEventListener:(n,f)=>listeners.delete(f)};Object.defineProperty(window,'speechSynthesis',{value:ss});Object.defineProperty(window,'SpeechSynthesisUtterance',{value:class{constructor(text){this.text=text;}}});const RelayMD={parse:t=>[{text:t}]};${src};configureSpeech({tts:{mode:'phone',voice:'Anna Su',phone_voice:'unavailable'}});const local=new Speaker((...s)=>states.push(s),n=>notices.push(n));document.querySelector('#local').onclick=()=>local.tap('local','Hello');window.loadVoices=()=>{voices=[{name:'Local',lang:'en-US',localService:true,default:true},{name:'Chinese',lang:'zh-CN',localService:true,default:true},{name:'Network',lang:'en',localService:false}];for(const f of listeners)f();};window.localSnapshot=()=>({spoken,notices,states,listeners:listeners.size});`});
 await phone.click('#local');assert.equal((await phone.evaluate(()=>localSnapshot())).spoken.length,0);
 await phone.evaluate(()=>loadVoices());await phone.waitForFunction(()=>localSnapshot().spoken.length===1);
 await phone.click('#local');await phone.evaluate(()=>{local.stop();local.tap('next','Hello');});
 let pv=await phone.evaluate(()=>localSnapshot());assert.equal(pv.notices.length,1);assert.equal(pv.spoken.at(-1).name,'Local');assert.equal(pv.listeners,0);
 await phone.evaluate(()=>{configureSpeech({tts:{mode:'phone',voice:'Anna Su',phone_voice:'Chinese'}});local.tap('zh','你好世界。');});
 pv=await phone.evaluate(()=>localSnapshot());assert.equal(pv.spoken.at(-1).name,'Chinese');
 await phone.evaluate(()=>{voices=[];local.tap('timeout','Hello');});await phone.waitForFunction(()=>localSnapshot().states.some(s=>s[0]==='timeout'&&s[2]==='novoice'));
 pv=await phone.evaluate(()=>localSnapshot());assert.equal(pv.listeners,0);
 mkdirSync('reports/qa/p124',{recursive:true});writeFileSync('reports/qa/p124/webkit-speech.json',JSON.stringify({engine:'WebKit',physical:false,controlled_audio_policy:true,complete:true,local_voice:pv,...v},null,2)+'\n');console.log('PASS WebKit actual Speaker: synchronous unlock, delayed speech, automatic reuse');
}finally {await browser.close();}
