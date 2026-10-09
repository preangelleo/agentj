// Real Chromium: nonextractable per-bot visitor keys survive reload in isolated storage.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {launch,newPage,navigate,evaluate,waitFor,cdp} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
const assets=new URL('../../site/static/bots-assets/',import.meta.url),chatSource=readFileSync(new URL('chat.js',assets),'utf8'),vaultSource=readFileSync(new URL('vault.js',assets),'utf8');
let origin='',storage='';
const vault=createServer((req,res)=>{res.writeHead(200,{'content-type':req.url==='/vault.js'?'application/javascript':'text/html'});res.end(req.url==='/vault.js'?vaultSource:`<html data-parent-origin="${origin}" data-bot="${new URL(req.url,'http://x').searchParams.get('bot')}"><script type="module" src="/vault.js"></script></html>`);});
await new Promise(r=>vault.listen(0,'127.0.0.1',r));storage='http://127.0.0.1:'+vault.address().port;
const server=createServer((req,res)=>{
 if(req.url==='/chat.js'){res.writeHead(200,{'content-type':'application/javascript','access-control-allow-origin':'*'});res.end(chatSource+'\nwindow.__p82={key};');return;}
 const bot=new URL(req.url,'http://x').searchParams.get('bot')||'a'.repeat(32);assert.match(bot,/^[a-f0-9]{32}$/);
 res.writeHead(200,{'content-type':'text/html'});res.end(`<!doctype html><p id="owner">OWNER_CANARY</p><script>localStorage.setItem('owner','OWNER_CANARY');window.events=[];let loaded=false,ready=false,connected=false;const v=document.createElement('iframe');v.id='vault';v.sandbox='allow-scripts allow-same-origin';v.src='${storage}/?bot=${bot}';const f=document.createElement('iframe');f.id='chat';f.sandbox='allow-scripts';function connect(){if(!loaded||!ready||connected)return;connected=true;const c=new MessageChannel();f.contentWindow.postMessage({kind:'bot-key-port'},'*',[c.port1]);v.contentWindow.postMessage({kind:'bot-key-port'},'${storage}',[c.port2]);}v.onload=()=>{loaded=true;connect();};addEventListener('message',e=>{if(e.source!==f.contentWindow||e.origin!=='null')return;events.push(e.data);if(e.data.kind==='bot-key-ready'){ready=true;connect();}});f.srcdoc='<html data-parent-origin="'+location.origin+'"><p id="status"></p><div id="messages"></div><form id="chat"><textarea id="message"></textarea><button id="send"></button></form><button id="human"></button><button id="identity"></button><input id="order"><input id="email"><script type="module" src="'+location.origin+'/chat.js" crossorigin="anonymous"><\\/script>';document.body.append(v,f);</script>`);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));origin='http://127.0.0.1:'+server.address().port;
const rec=parityRecorder('web/test/p82-session-browser.mjs'),B=await launch();let pass=false;
try{
 const p=await newPage(B,390,844,'light',{allow:[origin,storage]});
 const load=async bot=>{await navigate(p,origin+'/?bot='+bot);await waitFor(p,"events.some(e=>e.kind==='bot-public-key')");return evaluate(p,"events.find(e=>e.kind==='bot-public-key').key");};
 const a=await load('a'.repeat(32)),again=await load('a'.repeat(32)),b=await load('b'.repeat(32));assert.equal(a,again);assert.notEqual(a,b);
 assert.equal(await evaluate(p,"(()=>{try{document.getElementById('vault').contentWindow.document.body;return false;}catch(e){return e.name==='SecurityError';}})()"),true);
 const target=(await B.browser.send('Target.getTargets')).targetInfos.find(t=>t.type==='iframe'&&t.url==='about:srcdoc');assert.ok(target);
 const child=cdp(`ws://127.0.0.1:${B.port}/devtools/page/${target.targetId}`);await child.ready;
 const r=await child.send('Runtime.evaluate',{expression:"({extractable:__p82.key.privateKey.extractable,ownerBlocked:(()=>{try{return parent.document.getElementById('owner').textContent==='';}catch(e){return e.name==='SecurityError';}})()})",returnByValue:true});assert.deepEqual(r.result.value,{extractable:false,ownerBlocked:true});child.close();
 assert.ok((await evaluate(p,'events')).every(e=>['bot-key-ready','bot-public-key'].includes(e.kind)));assert.equal(p.offsite.length,0);assert.deepEqual(p.problems,[]);
 const output={scope:'real local Chromium, no production DNS/configuration',reload_same_key:true,other_bot_different_key:true,private_key_extractable:false,owner_and_vault_DOM_isolated:true,parent_received_only_metadata:true};mkdirSync(new URL('../../../reports/qa/p82/session-browser/',import.meta.url),{recursive:true});writeFileSync(new URL('../../../reports/qa/p82/session-browser/results.json',import.meta.url),JSON.stringify(output,null,2)+'\n');pass=true;
}finally{rec.record('isolated-anonymous-reload',pass);rec.write();await B.close();await new Promise(r=>server.close(r));await new Promise(r=>vault.close(r));}
