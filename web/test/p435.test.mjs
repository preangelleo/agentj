import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const src = readFileSync(new URL('../public/js/relay.js', import.meta.url), 'utf8');
const store = await import('../public/js/longrec.js');
function finish(bytes, secs, send = true){
  const calls = {drop: 0, asr: 0, toast: 0, interrupted: 0};
  const ctx = vm.createContext({LR: {...store, dropSeg(){calls.drop++;}}, localEpoch: 1, WAV_MAX_S: 600,
    takeFinished(){calls.asr++;}, intNote(){calls.interrupted++;}, toast(){calls.toast++;}, t: x => x});
  vm.runInContext(src.slice(src.indexOf('function pttFinish('), src.indexOf('// A finished take,')), ctx);
  ctx.p = {send, epoch: 1, stream: null, takeP: Promise.resolve('t')};
  ctx.blob = new Blob([new Uint8Array(bytes)], {type: 'audio/mp4'});
  vm.runInContext(`pttFinish(p, 0, blob, ${secs})`, ctx);
  return Promise.resolve().then(() => calls);
}
test('P435 completed tiny/short locked recordings are silently discarded before ASR', async () => {
  for (const [bytes, secs] of [[5, 8], [10000, .49], [0, 1]]) {
    const calls = await finish(bytes, secs);
    assert.equal(calls.asr, 0, `${bytes} bytes / ${secs}s`);
    assert.equal(calls.drop, 1); assert.equal(calls.toast, 0); assert.equal(calls.interrupted, 0);
  }
});
test('P435 valid half-second recording still reaches transcription', async () => {
  const calls = await finish(16044, .5);
  assert.equal(calls.asr, 1); assert.equal(calls.drop, 0);
});
test('P435 recovery cancel remains Discard in both generated dictionaries', () => {
  for (const [language, expected] of [['zh', '丢弃'], ['en', 'Discard']]) {
    const dict = JSON.parse(readFileSync(new URL(`../i18n/web.${language}.json`, import.meta.url)));
    assert.equal(dict.r.rec.backNo, expected);
  }
});
test('P435 send rejects a stale empty audio chip and preserves the other file and words', async () => {
  const audio = {file: new Blob([new Uint8Array(5)], {type:'audio/webm'}), origin:'file'};
  const other = {file: new Blob(['notes'], {type:'text/plain'}), origin:'file'};
  const ctx = vm.createContext({LR:store, sending:false, input:{value:'keep these words'}, atts:[audio,other],
    toast:()=>{}, t:x=>x, hasSayable:()=>false});
  ctx.removeAtt = a => {ctx.atts.splice(ctx.atts.indexOf(a),1);};
  vm.runInContext(src.slice(src.indexOf('async function say(voiceReady'),src.indexOf('// ---- the offline queue')),ctx);
  await vm.runInContext('say()',ctx);
  assert.deepEqual(ctx.atts,[other]); assert.equal(ctx.input.value,'keep these words');
});
test('P435 recorder without mimeType keeps the MP4 chunk MIME', () => {
  let got;
  const rec = {mimeType:''};
  const ctx = vm.createContext({performance:{now:()=>1000}, Blob, segAttach(){throw Error('wrong segment');},
    pttFinish(p,n,blob,secs){got={type:blob.type,secs};}});
  vm.runInContext(src.slice(src.indexOf('function segStop('),src.indexOf('// The watch,')),ctx);
  ctx.p = {rec,over:true,recT1:1000}; ctx.rec=rec; ctx.chunks=[new Blob([new Uint8Array(400)],{type:'audio/mp4'})];
  vm.runInContext('segStop(p,0,chunks,rec,0)',ctx);
  assert.deepEqual(got,{type:'audio/mp4',secs:1});
});
test('P435 already inactive recorder also keeps the MP4 chunk MIME', () => {
  let got;
  const p = {over:false,t0:0,seg:0,rec:{state:'inactive',mimeType:''},
    chunks:[new Blob([new Uint8Array(400)],{type:'audio/mp4'})]};
  const ctx = vm.createContext({p,ptt:p,Blob,clearInterval(){},clearTimeout(){},wakeOff(){},meterOff(){},
    performance:{now:()=>1000},PTT_MIN_MS:500,recTotalMs:()=>1000,pttUI(){},pttFinish(p,n,b){got=b.type;}});
  vm.runInContext(src.slice(src.indexOf('function pttEnd('),src.indexOf('// A take longer than T')),ctx);
  vm.runInContext('pttEnd(p,"release",true)',ctx);
  assert.equal(got,'audio/mp4');
});

test('P435 emergency stop is only in Safety, and legacy commands never dispatch', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url),'utf8');
  assert.match(html, /id="set-estop"/);
  const menu = src.slice(src.indexOf('function renderMenu('), src.indexOf('function renderMenu(')+7000);
  assert.doesNotMatch(menu, /estop: "stop"|estop\.btn/);
  assert.match(src, /p435\.stopWhere/);
});
