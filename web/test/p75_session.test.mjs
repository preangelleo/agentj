import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {padJson,KIND} from '../public/proto/wire.js';

test('P75 F36 hidden handshake refusals reconnect; authenticated removed confirms once',async()=>{
 const oldDoc=globalThis.document,oldWS=globalThis.WebSocket;
 let visibility;globalThis.document={visibilityState:'visible',addEventListener:(name,fn)=>{if(name==='visibilitychange')visibility=fn;}};
 class WS{static OPEN=1;constructor(){this.readyState=1;}close(){}send(){}}
 globalThis.WebSocket=WS;
 const S=await import('../public/js/session.js?f36');
 const ctx={relay:'ws://127.0.0.1:1',channel:'c'.repeat(22),hostPub:new Uint8Array(32)};
 const calls=[];let resumed=0;
 S.configure({setStatus(){},resume(){resumed++;S.openSession('resume',ctx);},onHostDown(){calls.push('down');},onReady(){calls.push('ready');},onRevoked(why){calls.push(why);}});
 const close=async(s)=>{s.ws.onclose({code:4010});await s.chain;};
 const msg=async(s,m)=>{s.recv={decrypt:async()=>padJson(m)};s.ws.onmessage({data:new Uint8Array([KIND.DATA]).buffer});await s.chain;};
 try{
  let s=S.openSession('resume',ctx);s.phase='hs';
  document.visibilityState='hidden';visibility();await close(s);
  document.visibilityState='visible';visibility();assert.equal(resumed,1);
  s=S.current();s.phase='hs';await close(s);
  assert.deepEqual(calls.splice(0),['down','down']);
  S.reconnectNow();s=S.current();s.phase='ready-wait';await msg(s,{t:'ready'});
  assert.equal(S.isReady(),true);assert.deepEqual(calls.splice(0),['ready']);
  await msg(s,{t:'removed',why:'revoked'});await close(s);
  assert.deepEqual(calls.splice(0),['down'],'first explicit removal silently confirms');
  S.reconnectNow();s=S.current();s.phase='ready-wait';await msg(s,{t:'removed',why:'revoked'});await close(s);
  assert.deepEqual(calls.splice(0),['revoked'],'repeated authenticated removal is final');
  s=S.openSession('resume',ctx);s.phase='ready-wait';await msg(s,{t:'removed',why:'replaced'});await close(s);
  assert.deepEqual(calls.splice(0),['replaced'],'yield to the other browser');
 }finally{S.closeSession();globalThis.document=oldDoc;globalThis.WebSocket=oldWS;}
});
test('P75 F36 replaced copy does not invite re-pairing',()=>{
 for(const lang of ['en','zh.src']){
  const dict=JSON.parse(readFileSync(new URL('../i18n/web.'+lang+'.json',import.meta.url),'utf8'));
  assert.doesNotMatch(dict.revoked.replacedLead,/agentj pair|重新配对/);
 }
});
