// Dynamic Node client/protocol checks. These are not browser/Host/Noise e2e evidence.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash, webcrypto } from 'node:crypto';
import vm from 'node:vm';
import * as wire from '../../protocol/wire.js';
import * as webwire from '../public/proto/wire.js';
import { sanitize } from '../public/js/mediawire.js';
let checks = 0;
for (const action of ['mail_fwd', 'private_file_get', 'private_file_revoke']) {
  const target = {request:{to:[{addr:'synthetic@example.test',name:'合成'}],turn:7,input:false,again:true}};
  assert.deepEqual(await wire.controlMessage('s'.repeat(22),'device',action,'a'.repeat(32),123,target),
    await webwire.controlMessage('s'.repeat(22),'device',action,'a'.repeat(32),123,target)); checks++;
}
const card = {id:'a'.repeat(32),seat:'s'.repeat(22),bytes:26*1024*1024,expires:2000000000};
const clean = sanitize({media_skip:[{name:'large.zip',why:'too_big',bytes:card.bytes,download:card}]});
assert.equal(clean.skip[0].download.id,card.id); checks++;
assert.equal(sanitize({media_skip:[{name:'bad',why:'too_big',bytes:card.bytes,download:{...card,seat:'../../bad'}}]}).skip[0].download,undefined); checks++;
let generation = {}, offset=0, calls=0, aborts=0, closes=0, mode='good';
let received = createHash('sha256');
const chunkSize=45056;
const context=vm.createContext({crypto:webcrypto, Uint8Array, console, Error, Math});
const session={peer:{privatefiles1:true},channel:()=>card.seat,gen:()=>generation,whenReady:async()=>generation,
  ask1:async obj=>{
    calls++;
    if(mode==='offline') return {t:'offline'};
    if(mode==='generation') generation={};
    const n=Math.min(chunkSize,card.bytes-obj.request.offset), bytes=new Uint8Array(n);
    const result={ok:true,offset:obj.request.offset,d:wire.b64u(bytes),last:obj.request.offset+n===card.bytes,sha256:await wire.sha256hex(bytes)};
    if(mode==='corrupt') result.sha256='0'.repeat(64);
    return result;
  }};
context.showSaveFilePicker=async()=>({createWritable:async()=>({
  write:async bytes=>{assert.ok(bytes.length<=chunkSize);offset+=bytes.length;received.update(bytes);},
  close:async()=>{closes++;}, abort:async()=>{aborts++;}
})});
function synthetic(exports) {
  return new vm.SyntheticModule(Object.keys(exports),function(){for(const [k,v] of Object.entries(exports))this.setExport(k,v);},{context});
}
const source=await readFile(new URL('../public/js/private-files.js',import.meta.url),'utf8');
const module=new vm.SourceTextModule(source,{context});
await module.link(name=>{
  if(name==='./session.js') return synthetic(session);
  if(name==='./t.js') return synthetic({t:key=>key,onLang:()=>{}});
  if(name==='./layers.js') return synthetic({registerLayer:()=>{}});
  if(name==='./controls.js') return synthetic({signed:async(action,target,extra)=>({...extra,n:'n',sig:'synthetic'})});
  if(name==='../proto/wire.js') return synthetic({unb64u:wire.unb64u,sha256hex:wire.sha256hex});
  throw new Error('unexpected import '+name);
});
await module.evaluate();
await module.namespace.downloadFile(card,'synthetic.zip');
assert.equal(offset,card.bytes);assert.equal(calls,606);assert.equal(closes,1);assert.equal(aborts,0);checks++;
for(mode of ['offline','generation','corrupt']) {
  const before=aborts;
  await assert.rejects(module.namespace.downloadFile(card,'synthetic.zip'));
  assert.equal(aborts,before+1);checks++;
}
session.peer.privatefiles1=false;
await assert.rejects(module.namespace.downloadFile(card,'synthetic.zip'));checks++;
console.log(`PASS ${checks} dynamic Node checks; 606 chunk writes; offline/session-change/corruption abort; no browser claim`);
