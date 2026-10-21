import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createAsrSetup} from '../public/js/asr-setup.js';

function harness(install) {
  const notices = [], events = [];
  const setup = createAsrSetup({t:k=>k, notify:n=>notices.push(n), confirm:async()=>true,
    install, ready:()=>events.push('ready'), repaint:()=>events.push('repaint')});
  return {setup, notices, events};
}
test('truthful stages and transient buttonless completion; no invented percentage', async()=>{
  const h = harness(async progress => { progress('download'); progress('extract'); progress('install'); progress('test'); return {ok:true,state:'ready'}; });
  await h.setup.run();
  assert.deepEqual(h.notices.slice(0,-1).map(n=>n.text), ['prepare','download','extract','install','test'].map(n=>'p387Voice.'+n));
  const done = h.notices.at(-1);
  assert.equal(done.kind,'success'); assert.equal(done.ms,1800);
  assert.equal(done.shape,'circle'); assert.equal(done.text,'p387Voice.readyTitle');
  assert.equal(done.actions,undefined); assert.ok(h.events.includes('ready'));
});
test('failure shows safe localized reason and retry invokes the installer', async()=>{
  let count = 0;
  const h = harness(async()=> ++count === 1 ? {ok:false,why:'download_failed'} : {ok:true,state:'ready'});
  await h.setup.run(); const fail=h.notices.at(-1);
  assert.equal(fail.text,'p387Voice.download_failed'); assert.equal(fail.ms,0);
  await fail.actions[0].fn(); assert.equal(count,2); assert.equal(h.notices.at(-1).kind,'success');
});
test('unknown failure never displays host output and zero-exit/off never says ready', async()=>{
  for (const r of [{ok:false,why:'/private/error'}, {ok:true,state:'off'}]) {
    const h = harness(async()=>r); await h.setup.run();
    assert.equal(h.notices.at(-1).kind,'error'); assert.ok(!h.events.includes('ready'));
    assert.equal(h.notices.at(-1).text,'p387Voice.failed');
  }
});
test('repeated mic taps while installing never start another install', async()=>{
  let finish, count=0; const h=harness(()=>{count++; return new Promise(r=>finish=r);});
  const first=h.setup.run(); await Promise.resolve(); assert.equal(h.setup.busy(),true);
  await h.setup.run(); assert.equal(count,1); finish({ok:true,state:'ready'}); await first;
  assert.equal(h.setup.busy(),false);
});
test('request progress is scoped to rid and drops on disconnect before any stale update', async()=>{
  // Exercise the actual request/ask1 functions with a transport stand-in.
  const text=readFileSync(new URL('../public/js/session.js',import.meta.url),'utf8');
  const code=text.slice(text.indexOf('const pending = new Map();'),text.indexOf('export const pendingCount'));
  const sent=[], context=vm.createContext({crypto:globalThis.crypto, isReady:()=>true, gen:()=>7,
    sendApp:async m=>sent.push(m), setTimeout:()=>0});
  vm.runInContext(code.replaceAll('export ', '')+';globalThis.h={ask1,pending};',context);
  const updates=[]; const result=context.h.ask1({t:'asr_install'},'asr_install_res',600000,m=>updates.push(m.stage));
  await Promise.resolve(); const r=sent[0].r;
  assert.equal(context.h.pending.has('stale-rid'),false);
  context.h.pending.get(r)({t:'asr_install_progress',r,stage:'download'});
  context.h.pending.get(r)({t:'offline'});
  assert.equal((await result).t,'offline'); assert.equal(context.h.pending.has(r),false);
  assert.deepEqual(updates,['download']);
});
test('mic readiness follows authoritative host ready for local or cloud engine without changing recording fallbacks',()=>{
  const relay=readFileSync(new URL('../public/js/relay.js',import.meta.url),'utf8');
  const code=relay.slice(relay.indexOf('function restingMicLabel(){'),relay.indexOf('export function paintVoiceReadiness'));
  for (const state of ['ready','not_installed','broken','off']) {
    const c=vm.createContext({C:{asr:()=>state},t:k=>k});
    vm.runInContext(code+';globalThis.label=restingMicLabel();',c);
    assert.equal(c.label,state==='ready'?'r.mic.hold':'p387Voice.preparing');
  }
  assert.ok(relay.includes('r.voice.asAttNoAsr')); // existing attachment fallback remains
});
