import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const LIB = new URL('../../tests/lib/host.mjs', import.meta.url);
const { HostProc } = existsSync(LIB) ? await import(LIB) : {};
const {guardedSpawn,stopGroup} = existsSync(LIB) ? await import('../../tests/lib/reap.mjs') : {};
const skip = !HostProc && 'no agentjarvis/tests/lib (public export)';
const turn = () => new Promise(r=>setImmediate(r));
async function fixture(t, mode='controlled') {
  const dir=mkdtempSync(join(tmpdir(),'p271-stop-owned-'));
  const h=new HostProc({relay:'ws://127.0.0.1:1',web:'http://127.0.0.1:1',stateDir:dir});
  const source="process.on('SIGTERM',()=>{});process.stdin.on('data',()=>process.exit(0));console.log('ready');setInterval(()=>{},1000)";
  const p=guardedSpawn(process.execPath,['-e',mode==='exited'?'process.exit(0)':source],
    {env:{...process.env,AGENTJ_STATE_DIR:dir},stdio:['pipe','pipe','pipe']});
  t.after(async()=>{t.mock.timers.reset();await stopGroup(p);rmSync(dir,{recursive:true,force:true});});
  if(mode==='exited')await once(p,'exit');else await once(p.stdout,'data');
  h.proc=p;
  return {h,p};
}

test('test host stop waits for the owned child to exit even if a previous exit flag is set', {skip,timeout:15000}, async t=>{
  const {h,p}=await fixture(t);h.exited=0;
  const signals=[],send=p.kill.bind(p);p.kill=s=>{signals.push(s);return send(s);};
  let finished=false;const stopped=h.stop().then(()=>{finished=true;});
  await turn();assert.deepEqual(signals,['SIGTERM']);assert.equal(finished,false);
  p.stdin.write('finish\n');await stopped;assert.equal(finished,true);
});

test('test host stop does not signal an already exited child', {skip,timeout:15000}, async t=>{
  const {h,p}=await fixture(t,'exited');
  p.kill=()=>assert.fail('must not signal an exited child');
  await h.stop();assert.equal(p.exitCode,0);
});

test('test host stop still waits after requesting SIGKILL', {skip,timeout:15000}, async t=>{
  const {h,p}=await fixture(t);
  const signals=[],send=p.kill.bind(p);
  // Explicit request/exit seam: the registered real child receives TERM, while
  // forwarding of the KILL request is held until assertions inspect stop's wait.
  // Safety registry and cleanup are real and never accept an unregistered mock.
  p.kill=s=>{signals.push(s);return s==='SIGKILL'?true:send(s);};
  t.mock.timers.enable({apis:['setTimeout']});
  let finished=false;const stopped=h.stop().then(()=>{finished=true;});
  t.mock.timers.tick(10000);await turn();
  assert.deepEqual(signals,['SIGTERM','SIGKILL']);assert.equal(finished,false);
  t.mock.timers.tick(1000);await turn();assert.equal(finished,false,'a kill request alone is not an exit');
  const exited=once(p,'exit');send('SIGKILL');await exited;await turn();
  t.mock.timers.tick(200);await stopped;assert.equal(finished,true);
});
