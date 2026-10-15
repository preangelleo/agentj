import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const LIB = new URL('../../tests/lib/host.mjs', import.meta.url);   // the e2e host harness is private (not in the public export)
const { HostProc } = existsSync(LIB) ? await import(LIB) : {};

test('test host stop waits for the owned child to exit even if a previous exit flag is set', { skip: !HostProc && 'no agentjarvis/tests/lib (public export)' }, async t => {
  const dir=mkdtempSync(join(tmpdir(),'p271-stop-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const h=new HostProc({relay:'ws://127.0.0.1:1',web:'http://127.0.0.1:1',stateDir:dir});
  const p=new EventEmitter();p.exitCode=null;p.signalCode=null;const signals=[];
  p.kill=s=>{signals.push(s);return true;};h.proc=p;h.exited=0;
  let finished=false;const stopped=h.stop().then(()=>{finished=true;});
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(signals,['SIGTERM']);assert.equal(finished,false);
  p.signalCode='SIGTERM';p.emit('exit',null,'SIGTERM');
  await stopped;assert.equal(finished,true);
});

test('test host stop does not signal an already exited child', { skip: !HostProc && 'no agentjarvis/tests/lib (public export)' }, async t => {
  const dir=mkdtempSync(join(tmpdir(),'p271-dead-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const h=new HostProc({relay:'ws://127.0.0.1:1',web:'http://127.0.0.1:1',stateDir:dir});
  h.proc={exitCode:0,signalCode:null,kill(){assert.fail('must not signal an exited child');}};
  await h.stop();
});

test('test host stop still waits after requesting SIGKILL', { skip: !HostProc && 'no agentjarvis/tests/lib (public export)' }, async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const dir=mkdtempSync(join(tmpdir(),'p271-force-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const h=new HostProc({relay:'ws://127.0.0.1:1',web:'http://127.0.0.1:1',stateDir:dir});
  const p=new EventEmitter();p.exitCode=null;p.signalCode=null;const signals=[];
  p.kill=s=>{signals.push(s);return true;};h.proc=p;
  let finished=false;const stopped=h.stop().then(()=>{finished=true;});
  t.mock.timers.tick(10000);await new Promise(r=>setImmediate(r));
  assert.deepEqual(signals,['SIGTERM','SIGKILL']);assert.equal(finished,false);
  t.mock.timers.tick(1000);await new Promise(r=>setImmediate(r));
  assert.equal(finished,false,'a kill request alone is not an exit');
  p.signalCode='SIGKILL';p.emit('exit',null,'SIGKILL');
  await new Promise(r=>setImmediate(r));t.mock.timers.tick(200);
  await stopped;assert.equal(finished,true);
});
