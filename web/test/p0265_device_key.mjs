// Own fresh Chromium, actual shipped WebCrypto/IDB; no host, QA account or private export.
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
import assert from 'node:assert/strict';
import {launch,newPage,navigate,evaluate} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const out=process.env.AJ_SHOTS||'reports/qa/p0265-device-key';await mkdir(out,{recursive:true});
const pub=resolve('web/public');
const server=createServer(async(req,res)=>{
 try{const path=new URL(req.url,'http://local').pathname;
  if(path==='/probe'){res.writeHead(200,{'content-type':'text/html'}).end('<!doctype html><title>Owned identity test</title>');return;}
  const p=resolve(pub,'.'+path);if(!p.startsWith(pub+'/'))throw Error('path');
  res.writeHead(200,{'content-type':extname(p)==='.js'?'text/javascript':'application/octet-stream'}).end(await readFile(p));
 }catch{res.writeHead(404).end();}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const url='http://127.0.0.1:'+server.address().port;
const B=await launch(),rows=[],rec=parityRecorder('web/test/p0265_device_key.mjs');
const setup=`const store=await import('/js/store.js'),api=await import('/js/api.js'),wire=await import('/proto/wire.js');`;
async function run(name,body,seat=false){
 const p=await newPage(B,390,844,'dark',{allow:[url]});let ok=false;
 try{
  if(seat)await p.send('Page.addScriptToEvaluateOnNewDocument',{source:"globalThis.AJSeat={db:n=>n+'@synthetic-seat'}"});
  await navigate(p,url+'/probe');
  const value=await evaluate(p,`(async()=>{${setup}${body}})()`);
  assert.equal(value.ok,true,JSON.stringify(value));ok=true;rows.push({name,...value});console.log('PASS '+name+' '+JSON.stringify(value));
 }catch(e){rows.push({name,ok:false,error:String(e.message)});console.log('FAIL '+name+' '+e.message);}
 finally{rec.record(name,ok);await p.dispose();}
}
try{
 for(let i=0;i<3;i++)await run('singleflight-controlled-'+i,`
  const original=crypto.subtle.generateKey.bind(crypto.subtle),gates=[];
  crypto.subtle.generateKey=function(...a){const pending=original(...a);if(a[0]?.name!=='X25519'||new Error().stack.includes('x25519Persists'))return pending;return pending.then(v=>new Promise(r=>gates.push(()=>r(v))));};
  const end=performance.now()+5000;while(false){};
  const early=api.myDeviceId();while(gates.length<1){if(performance.now()>end)throw Error('first gate');await new Promise(r=>setTimeout(r,5));}
  const later=store.deviceKey();await new Promise(r=>setTimeout(r,80));
  gates[0]();const id=await early;if(gates[1])gates[1]();const kp=await later;
  crypto.subtle.generateKey=original;
  const current=await store.deviceKey(),post=await api.myDeviceId();
  return {ok:gates.length===1&&id===post&&post===await wire.deviceId(current.pub)&&await wire.deviceId(kp.pub)===post,final_generations:gates.length,pre_post_api_equal:id===post,api_public_equal:post===await wire.deviceId(current.pub)};
 `);
 for(let i=0;i<6;i++)await run('singleflight-normal-'+i,`
  const [id,a,b]=await Promise.all([api.myDeviceId(),store.deviceKey(),store.deviceKey()]);
  return {ok:id===await wire.deviceId(a.pub)&&id===await wire.deviceId(b.pub)&&id===await api.myDeviceId(),private_nonextractable:a.priv.extractable===false};
 `);
 await run('singleflight-failure-retry',`
  const original=crypto.subtle.generateKey.bind(crypto.subtle);let finals=0;
  crypto.subtle.generateKey=function(...a){if(a[0]?.name==='X25519'&&!new Error().stack.includes('x25519Persists')){finals++;return Promise.reject(Error('synthetic generation failure'));}return original(...a);};
  const failures=await Promise.allSettled([store.deviceKey(),store.deviceKey()]);crypto.subtle.generateKey=original;
  const kp=await store.deviceKey(),again=await store.deviceKey();
  return {ok:finals===1&&failures.every(x=>x.status==='rejected')&&await wire.deviceId(kp.pub)===await wire.deviceId(again.pub),generation_failures:finals,retry_ok:kp.pub.length===32};
 `);
 await run('singleflight-persist-failure-retry',`
  const original=IDBObjectStore.prototype.put;let writes=0;
  IDBObjectStore.prototype.put=function(v,k){if(k==='device'||k==='device2'){writes++;throw Error('synthetic put failure');}return original.call(this,v,k);};
  const failures=await Promise.allSettled([store.deviceKey(),store.deviceKey()]);IDBObjectStore.prototype.put=original;
  const kp=await store.deviceKey();
  return {ok:writes===1&&failures.every(x=>x.status==='rejected')&&kp.pub.length===32,persist_failures:writes};
 `);
 await run('singleflight-wrapped-key-and-reuse',`
  const original=crypto.subtle.generateKey.bind(crypto.subtle);let finals=0;
  crypto.subtle.generateKey=function(...a){if(a[0]?.name==='X25519'){if(new Error().stack.includes('x25519Persists'))return Promise.reject(Error('synthetic unsupported IDB X25519'));finals++;}return original(...a);};
  const [a,b]=await Promise.all([store.deviceKey(),store.deviceKey()]);crypto.subtle.generateKey=original;
  const record=await store.dbGet('device2'),again=await store.deviceKey();
  return {ok:finals===1&&record?.v===2&&await wire.deviceId(a.pub)===await wire.deviceId(b.pub)&&await wire.deviceId(a.pub)===await wire.deviceId(again.pub)&&a.priv.extractable===false,final_generations:finals,wrapped_record_v2:record?.v===2,private_nonextractable:a.priv.extractable===false};
 `);
 await run('singleflight-adoption-waits-inflight-and-resets',`
  const root=await new Promise((res,rej)=>{const r=indexedDB.open('agentjarvis',1);r.onupgradeneeded=()=>r.result.createObjectStore('kv');r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error);});
  const pair=await crypto.subtle.generateKey({name:'X25519'},false,['deriveBits']),publicBytes=new Uint8Array(await crypto.subtle.exportKey('raw',pair.publicKey));
  await new Promise((res,rej)=>{const tx=root.transaction('kv','readwrite'),s=tx.objectStore('kv');s.put({priv:pair.privateKey,pub:publicBytes},'device');s.put({approved:true,channel:'synthetic'},'host');tx.oncomplete=res;tx.onerror=()=>rej(tx.error);});root.close();
  const original=crypto.subtle.generateKey.bind(crypto.subtle),gates=[];
  crypto.subtle.generateKey=function(...a){const pending=original(...a);if(a[0]?.name!=='X25519'||new Error().stack.includes('x25519Persists'))return pending;return pending.then(v=>new Promise(r=>gates.push(()=>r(v))));};
  const pending=store.deviceKey(),end=performance.now()+5000;
  while(gates.length<1){if(performance.now()>end)throw Error('adoption gate');await new Promise(r=>setTimeout(r,5));}
  let completed=false;const adoption=store.adoptRootStore().then(v=>{completed=true;return v;});await new Promise(r=>setTimeout(r,80));const waited=!completed;
  gates[0]();await pending;const copied=await adoption;crypto.subtle.generateKey=original;
  const current=await store.deviceKey(),persisted=await store.dbGet('device');
  return {ok:waited&&copied&&await wire.deviceId(current.pub)===await wire.deviceId(publicBytes)&&await wire.deviceId(persisted.pub)===await wire.deviceId(publicBytes),waited_inflight:waited,root_public_preserved:await wire.deviceId(current.pub)===await wire.deviceId(publicBytes)};
 `,true);
 await writeFile(resolve(out,'results.json'),JSON.stringify({scope:'owned synthetic real WebCrypto/IndexedDB only; public identity metadata, no private export/QA/model',rows},null,2)+'\n');
 assert.ok(rows.every(r=>r.ok),'device-key singleflight regression');
}finally{rec.write();await B.close();await new Promise(r=>server.close(r));}
