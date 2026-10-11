// P138: real encrypted phone UI, own Chromium/server, closed in finally and checked for residual processes.
import assert from 'node:assert/strict';
import { readFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { startWebServer } from './serve.mjs';
import { startFakeHost } from './fakehost.mjs';
import { launch, newPage, evaluate, navigate, waitFor, waitState, shoot } from './browser.mjs';
import { b64u } from '../../protocol/wire.js';
const out='reports/qa/p138';mkdirSync(out,{recursive:true});
let web,fake,B;const owned=new Set();let count=0;
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
async function paired(language){
 fake.reset();const p=await newPage(B,390,844,'light',{touch:true,allow:[web.url,fake.relay+'/']});
 await navigate(p,fake.newPairing(web.url+(language==='en'?'?lang=en':'')));
 await waitState(p,'awaiting-approval');await fake.approve();await waitState(p,'ready');
 return p;
}
const answers=()=>fake.log.filter(x=>x.t==='elev_answer'||x.t==='answer');
try {
 web=await startWebServer({port:0,relayCsp:'ws://127.0.0.1:*'});fake=await startFakeHost();B=await launch();
 for(const language of ['zh','en']){
  const p=await paired(language);
  try {
   const kp=await webcrypto.subtle.generateKey({name:'X25519'},true,['deriveBits']);
   const epk=b64u(new Uint8Array(await webcrypto.subtle.exportKey('raw',kp.publicKey)));
   const id='a'.repeat(32),n='b'.repeat(32);
   await fake.send({t:'elev',id,kind:'sudo',n,epk,ttl:120,cmd:'python3 -c pass',why:'install a package',effect:'change settings',helper:[fake.devs[0].id],interpreter:true});
   await waitFor(p,`document.getElementById('elev') && !document.getElementById('elev').hidden`);
   assert.equal(await evaluate(p,`document.getElementById('elev-input').hidden`),false,'interpreter never uses helper');
   assert.equal(await evaluate(p,`document.getElementById('elev').classList.contains('interpreter')`),true);
   const text=await evaluate(p,`document.getElementById('elev').textContent`);
   assert.ok(text.includes(language==='zh'?'Agent 自述':'Agent claim'));
   assert.ok(text.includes(language==='zh'?'必须输入管理员密码':'admin password required'));
   await evaluate(p,`document.getElementById('elev-allow').click()`);await sleep(150);
   assert.equal(answers().length,0,'empty field cannot approve interpreter');
   writeFileSync(`${out}/sudo-interpreter-${language}.png`,await shoot(p));count++;
   await fake.send({t:'elev_done',id,result:'denied'});
   await fake.send({t:'elev',id:'c'.repeat(32),kind:'sudo',n,epk,ttl:120,cmd:'/usr/bin/true',why:'test',effect:'',helper:[fake.devs[0].id],interpreter:false});
   await waitFor(p,`document.getElementById('elev-input').hidden === true`);
   await evaluate(p,`document.getElementById('elev-allow').click()`);
   for(let i=0;i<50&&!answers().length;i++)await sleep(30);
   assert.equal(answers()[0]?.ok,true,'direct command helper preserved');assert.ok(!answers()[0].ct);count++;
   await fake.send({t:'elev_done',id:'c'.repeat(32),result:'done'});
   const askId='d'.repeat(32);
   await fake.send({t:'ask',id:askId,tool:'SandboxOnce',summary:language==='zh'?'沙箱不可用：bubblewrap。修复隔离工具后重试。本次不隔离运行，可能访问本机文件和凭据。':'Sandbox unavailable: bubblewrap. Repair isolation and retry. This launch runs without isolation and may access local files and credentials.',ttl:120,cat:[],why:'',passkey_required:true});
   await waitFor(p,`document.getElementById('apprAllowLbl').textContent.includes(${JSON.stringify(language==='zh'?'本次不隔离运行':'Run this launch without isolation')})`);
   assert.equal(await evaluate(p,`document.getElementById('apprBatch').hidden`),true);
   writeFileSync(`${out}/sandbox-${language}.png`,await shoot(p));
   const baseline=answers().length;
   await evaluate(p,`import('./js/api.js').then(api=>api.approve({id:'${askId}',tool:'SandboxOnce',summary:'x',passkey_required:true},'allow')).then(r=>window.__p138=r)`);
   await waitFor(p,`window.__p138`);assert.equal(await evaluate(p,`window.__p138.why`),'passkey_required');
   assert.equal(answers().length,baseline,'missing passkey sends no answer');count++;
   await evaluate(p,`import('./js/api.js').then(api=>api.approve({id:'${askId}',tool:'SandboxOnce',summary:'x',passkey_required:true},'deny'))`);
   for(let i=0;i<50&&answers().length===baseline;i++)await sleep(30);
   assert.equal(answers().at(-1)?.ok,false,'deny does not need passkey');count++;
   // Browser ceremony stand-in; cryptographic/UV verification is exercised by real Host tests.
   await evaluate(p,`window.__p138get=null; navigator.credentials.get=(options)=>{ window.__p138get={uv:options.publicKey.userVerification,ch:Array.from(options.publicKey.challenge)}; return Promise.resolve({rawId:new Uint8Array(32).buffer,response:{clientDataJSON:new Uint8Array([1]).buffer,authenticatorData:new Uint8Array([2]).buffer,signature:new Uint8Array([3]).buffer}}); }; window.PublicKeyCredential ||= class {};`);
   await evaluate(p,`import('./js/api.js').then(api=>api.approve({id:'${askId}',tool:'SandboxOnce',summary:'x',passkey_required:true,fa:'${b64u(new Uint8Array(32))}',fa_ch:'${b64u(new Uint8Array(32).fill(7))}'},'allow')).then(r=>window.__p138success=r)`);
   await waitFor(p,`window.__p138success`);
   assert.equal(await evaluate(p,`window.__p138success.ok`),true);
   assert.equal(await evaluate(p,`window.__p138get.uv`),'required');
   assert.deepEqual(await evaluate(p,`window.__p138get.ch`),Array(32).fill(7));
   assert.ok(answers().at(-1).fa);assert.equal(answers().at(-1).ok,true);count++;

   assert.deepEqual(p.problems,[]);
  } finally {await p.dispose();}
 }
 console.log(`P138 browser: ${count} positive/negative cases passed`);
} finally {
 if(B){const info=await B.browser.send('SystemInfo.getProcessInfo').catch(()=>({processInfo:[]}));for(const p of info.processInfo)owned.add(p.id);await B.close();}
 if(fake)await fake.stop();if(web)await web.stop();
 const alive=()=>[...owned].filter(pid=>{try {const stat=readFileSync(`/proc/${pid}/stat`,'utf8');return !['Z','X'].includes(stat.slice(stat.lastIndexOf(')')+2).split(' ')[0]);}catch{return false;}});
 for(let i=0;i<50&&alive().length;i++)await sleep(100);
 assert.deepEqual(alive(),[],'no owned browser processes remain');
 if(web)await assert.rejects(fetch(web.url),'test web server must be closed');
 writeFileSync(`${out}/browser-cleanup.json`,JSON.stringify({ownedPids:[...owned],runningOwnedPids:alive(),webServerClosed:true,cases:count},null,2)+'\n');
}
