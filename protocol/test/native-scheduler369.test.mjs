import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import * as protocol from '../wire.js';
import * as phone from '../../web/public/proto/wire.js';
const target={platform:'linux',source:'systemd',scope:'system',id:'fixture.timer',snapshot:'a'.repeat(64),op:'disable'};
test('native object canonicalization agrees across Python, protocol and phone',()=>{
  const text=protocol.controlObject('native_scheduler_control',target);
  assert.equal(text,phone.controlObject('native_scheduler_control',target));
  const script="import json,sys;sys.path.insert(0,'agentjarvis/host');from agentj.controls import object_text;print(object_text('native_scheduler_control',json.loads(sys.argv[1])))";
  assert.equal(execFileSync('python3',['-c',script,JSON.stringify(target)],{cwd:new URL('../../../',import.meta.url),encoding:'utf8'}).trim(),text);
});
test('exact identity, platform, scope, snapshot and operation each alter signed object',()=>{
  const base=protocol.controlObject('native_scheduler_control',target);
  for(const [key,value] of Object.entries({platform:'darwin',source:'launchd',scope:'user',id:'other.timer',snapshot:'b'.repeat(64),op:'restore'})) {
    assert.notEqual(protocol.controlObject('native_scheduler_control',{...target,[key]:value}),base);
  }
  assert.ok(protocol.CONTROL_ACTIONS.includes('native_scheduler_control'));
  assert.ok(phone.CONTROL_ACTIONS.includes('native_scheduler_control'));
});
