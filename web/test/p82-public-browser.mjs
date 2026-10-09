// Real Chromium opaque-frame negative controls and Python/WebCrypto cipher interop.
// Only synthetic canaries. Own temporary profile/server/process; no production request.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {parityRecorder} from '../../parity/lib.mjs';
import {launch,newPage,evaluate,navigate,waitFor,cdp} from './browser.mjs';
const root=new URL('../../..',import.meta.url),source=readFileSync(new URL('../public/../..//site/static/bots-assets/chat.js',import.meta.url),'utf8');
const contexts=[];
const server=createServer((req,res)=>{
 if(req.url==='/chat.js'){res.writeHead(200,{'content-type':'application/javascript','access-control-allow-origin':'*'});res.end(source+'\nwindow.__p82={finish,seal,open,show};');return;}
 res.writeHead(200,{'content-type':'text/html'});res.end(`<!doctype html><div id="owner">OWNER_STORAGE_CANARY</div><script>localStorage.setItem('owner','OWNER_STORAGE_CANARY');window.events=[];addEventListener('message',e=>events.push({origin:e.origin,data:e.data}));const f=document.createElement('iframe');f.id='chat-frame';f.sandbox='allow-scripts';f.srcdoc='<html data-parent-origin="'+location.origin+'"><p id="status"></p><div id="messages"></div><form id="chat"><textarea id="message"></textarea><button id="send"></button></form><button id="human"></button><button id="identity"></button><input id="order"><input id="email"><script type="module" src="'+location.origin+'/chat.js" crossorigin="anonymous"><\\/script>';document.body.append(f);</script>`);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const url='http://127.0.0.1:'+server.address().port;
const B=await launch();let python,passed=false;const rec=parityRecorder('web/test/p82-public-browser.mjs');
try{
 const p=await newPage(B,390,844,'light',{allow:[url]});p.on(m=>{if(m.method==='Runtime.executionContextCreated')contexts.push(m.params.context);});await navigate(p,url);
 await waitFor(p,"window.events.some(e=>e.data?.kind==='bot-public-key')");
 const event=await evaluate(p,"events.find(e=>e.data.kind==='bot-public-key')");assert.equal(event.origin,'null');assert.deepEqual(Object.keys(event.data).sort(),['key','kind']);
 const targets=(await B.browser.send('Target.getTargets')).targetInfos;const target=targets.find(t=>t.type==='iframe');assert.ok(target,'opaque out-of-process iframe exists');
 const childClient=cdp(`ws://127.0.0.1:${B.port}/devtools/page/${target.targetId}`);await childClient.ready;await childClient.send('Runtime.enable');
 const child=async expression=>{const r=await childClient.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 const boundary=await child(`(()=>{const tests={};for(const [name,fn] of Object.entries({parent:()=>parent.document.getElementById('owner').textContent,storage:()=>localStorage.getItem('owner'),cookie:()=>document.cookie,indexedDB:()=>indexedDB.open('owner')})){try{fn();tests[name]=false;}catch(e){tests[name]=e.name==='SecurityError';}}return tests;})()`);
 assert.deepEqual(boundary,{parent:true,storage:true,cookie:true,indexedDB:true});
 python=spawn(new URL('../../host/.venv/bin/python',import.meta.url).pathname,['-u','-c',`import json,sys,time\nfrom cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey\nfrom agentj.bots.visitor import VisitorSession,issue_claim,public\nfrom agentj.wire import b64u\nhost=Ed25519PrivateKey.generate();issuer=Ed25519PrivateKey.generate();visitor=json.loads(sys.stdin.readline())['visitor']\nticket=issue_claim(issuer,'a'*32,b64u(public(host)),visitor,1,int(time.time())+299)\ns=VisitorSession('a'*32,1,host,public(issuer));answer=s.accept(ticket)\nprint(json.dumps({'answer':answer,'manifest':{'bot':'a'*32,'host':b64u(public(host)),'epoch':1},'cipher':s.cipher.seal({'t':'reply','text':'PRIVATE_REPLY_CANARY'}).hex()}),flush=True)\nframe=bytes.fromhex(json.loads(sys.stdin.readline())['frame']);assert b'PRIVATE_CHAT_CANARY' not in frame;obj=s.decode(frame);assert obj['text']=='PRIVATE_CHAT_CANARY';print(json.dumps({'cipher_only':True,'decrypted_on_host':True}),flush=True)`],{env:{...process.env,PYTHONPATH:new URL('../../host',import.meta.url).pathname},stdio:['pipe','pipe','pipe']});
 let buffer='',waiters=[];python.stdout.on('data',data=>{buffer+=data;while(buffer.includes('\n')){const i=buffer.indexOf('\n'),line=buffer.slice(0,i);buffer=buffer.slice(i+1);waiters.shift()?.(JSON.parse(line));}});
 const next=()=>new Promise((r,j)=>{const timer=setTimeout(()=>j(Error('Python interop timeout')),10000);waiters.push(v=>{clearTimeout(timer);r(v);});});
 const ready=next();python.stdin.write(JSON.stringify({visitor:event.data.key})+'\n');const f=await ready;
 assert.ok(!Buffer.from(f.cipher,'hex').includes(Buffer.from('PRIVATE_REPLY_CANARY')));
 await child(`__p82.finish(${JSON.stringify(f.answer)},${JSON.stringify(f.manifest)})`);
 const inbound=await child(`__p82.open(Uint8Array.from(${JSON.stringify([...Buffer.from(f.cipher,'hex')])}).buffer)`);assert.equal(inbound.text,'PRIVATE_REPLY_CANARY');
 const frameHex=await child(`__p82.seal({t:'say',r:'b'.repeat(32),text:'PRIVATE_CHAT_CANARY'}).then(b=>Array.from(new Uint8Array(b),v=>v.toString(16).padStart(2,'0')).join(''))`);
 const result=next();python.stdin.write(JSON.stringify({frame:frameHex})+'\n');const interop=await result;
 await child(`__p82.show('AI','<img src=x onerror=alert(1)>')`);assert.equal(await child("document.querySelectorAll('#messages img').length"),0);
 assert.equal(p.offsite.length,0);assert.deepEqual(p.problems,[]);
 const output={scope:'Real local Chromium + product Python visitor cipher; not production Turnstile/model/relay acceptance',boundary,interop,third_party_requests:0,owner_storage_exposed:false};
 mkdirSync(new URL('../../../reports/qa/p82/public-browser/',import.meta.url),{recursive:true});writeFileSync(new URL('../../../reports/qa/p82/public-browser/results.json',import.meta.url),JSON.stringify(output,null,2)+'\n');console.log(JSON.stringify(output));childClient.close();passed=true;
}finally{rec.record('opaque-storage-and-cipher',passed);rec.write();python?.kill('SIGTERM');await B.close();await new Promise(r=>server.close(r));}
