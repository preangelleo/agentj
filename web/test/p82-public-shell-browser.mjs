// Actual Worker response headers + built shell/chat assets under Chromium CSP.
// External frames and WebSockets are blocked by a closed local proxy; no production requests.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {setTimeout as sleep} from 'node:timers/promises';
import worker from '../../worker/src/index.ts';
import {launch,newPage,navigate,waitFor,cdp} from './browser.mjs';
import {parityRecorder} from '../../parity/lib.mjs';
let base,challenge=0;const bid='a'.repeat(32);
const env={BOT_PUBLIC_ORIGIN:'',BOT_VISITOR_ORIGIN:'https://visitor.agentjarvis.net',APEX_HOST:'127.0.0.1',
 ASSETS:{fetch:async req=>{try{return new Response(readFileSync(new URL('../../site/public'+new URL(req.url).pathname,import.meta.url)),{headers:{'content-type':req.url.endsWith('.js')?'application/javascript':'text/css'}});}catch{return new Response(null,{status:404});}}},
 APP:{fetch:async req=>{if(new URL(req.url).pathname.endsWith('/challenge')){challenge++;return new Response('{}',{status:503});}return new Response(JSON.stringify({manifest:{bot:bid,title:'Public fixture',description:'Public metadata',embed_origins:[]}}),{headers:{'content-type':'application/json'}});}}};
const server=createServer(async(req,res)=>{try{const r=await worker.fetch(new Request(base+req.url,{method:req.method}),env);res.writeHead(r.status,Object.fromEntries(r.headers));res.end(Buffer.from(await r.arrayBuffer()));}catch{res.writeHead(500);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));base='http://127.0.0.1:'+server.address().port;env.BOT_PUBLIC_ORIGIN=base;
const B=await launch({args:['--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=localhost;127.0.0.1']});let child,ok=false;const rec=parityRecorder('web/test/p82-public-shell-browser.mjs');
try{
 const page=await newPage(B,390,844,'light',{allow:[base,'https://visitor.agentjarvis.net','https://verify.agentj.app','wss://relay.agentj.app']});
 await navigate(page,base+'/bots/test-shop');
 await waitFor(page,"document.querySelector('#frames iframe[title^=\"AI customer\"]')",15000);
 const target=(await B.browser.send('Target.getTargets')).targetInfos.find(t=>t.type==='iframe'&&t.url==='about:srcdoc');assert.ok(target);
 child=cdp(`ws://127.0.0.1:${B.port}/devtools/page/${target.targetId}`);await child.ready;await child.send('Runtime.enable');
 const run=async expression=>{const r=await child.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('child evaluation failed');return r.result?.value;};
 let ready=false;for(let i=0;i<100;i++){if(challenge){ready=true;break;}await sleep(100);}assert.ok(ready,'built opaque chat module must run and post its public key through actual Worker CSP');
 await run("window.__violations=[];document.addEventListener('securitypolicyviolation',e=>__violations.push({directive:e.effectiveDirective,url:e.blockedURI}));window.__probe=new WebSocket('wss://relay.agentj.app/b/'+ 'a'.repeat(32))");await sleep(500);
 const relay=await run("__violations.filter(e=>e.url.startsWith('wss://relay.agentj.app'))");assert.deepEqual(relay,[],'inherited parent CSP must allow the chat relay');
 await run("try{new WebSocket('wss://outside.invalid/bot')}catch{};fetch('"+base+"/account').catch(()=>{})");await sleep(300);
 const denied=await run('__violations');assert.ok(denied.some(e=>e.url.startsWith('wss://outside.invalid')&&e.directive==='connect-src'));assert.ok(denied.some(e=>e.url.startsWith(base+'/account')&&e.directive==='connect-src'));
 const evidence={scope:'actual Worker HTML/CSP and built scripts; external network blocked by closed local proxy',chat_module_runs:true,relay_not_blocked_by_CSP:true,off_list_and_owner_HTTP_blocked:true,production_requests:false};
 const out=new URL('../../../reports/qa/p82/public-shell-browser/',import.meta.url);mkdirSync(out,{recursive:true});writeFileSync(new URL('results.json',out),JSON.stringify(evidence,null,2)+'\n');console.log('public-shell-csp PASS');ok=true;
}finally{rec.record('actual-worker-csp-chat',ok);rec.write();child?.close();await B.close();await new Promise(r=>server.close(r));}
