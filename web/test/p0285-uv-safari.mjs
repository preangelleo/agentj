// Real DOM and native Chromium virtual CTAP. Not physical Safari or live Host.
import assert from 'node:assert/strict';
import {mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {startWebServer} from './serve.mjs';
import {launch,newPage,navigate,evaluate} from './browser.mjs';
const out=resolve(process.env.UV_OUT||'reports/qa/uv-safari-passkey-missing-20261108/web');mkdirSync(out,{recursive:true});
const web=await startWebServer({port:0});web.url=web.url.replace('127.0.0.1','localhost');const B=await launch(),rows=[];
try{
for(const width of [390,1440])for(const theme of ['light','dark'])for(const language of ['zh','en']){
 const p=await newPage(B,width,900,theme,{allow:[web.url]});
 try{await navigate(p,web.url+'?lang='+language);
 const r=await evaluate(p,`(async()=>{const A=await import('./js/pkassist.js'),T=await import('./js/t.js');T.setTextOs('mac');document.documentElement.dataset.theme='${theme}';
 const row=document.createElement('section');row.style.cssText='margin:20px;background:var(--pg);color:var(--text);padding:24px;border:1px solid currentColor';
 const label=document.createElement('p'),note=document.createElement('p'),setupBtn=document.createElement('button'),acts=document.createElement('div'),testBtn=document.createElement('button');testBtn.textContent='Test';acts.append(testBtn);row.append(label,note,setupBtn,acts);document.body.replaceChildren(row);
 const opts={row,label,note,setupBtn,acts,testBtn,canTest:true};A.paintPkRow('local',opts);const registered=label.textContent;if(registered.includes('已设好')||registered.endsWith(': set up'))throw Error('unproven availability');
 const box=document.createElement('div');row.append(box);let resets=0,retries=0;A.configure({setupFaceId:()=>resets++});A.paintPk(box,{fa:'local',err:'NotAllowedError',retry:()=>retries++});box.querySelector('.pkassist__setup').click();if(resets!==1||retries!==0)throw Error('reset wiring');const failure=box.textContent;if(failure.includes('记录找不到')||failure.includes("can't be found"))throw Error('missing assertion');A.paintPkRow('local',{...opts,failed:true});if(setupBtn.hidden)throw Error('reset hidden');return {registered,failure,resets,retries}})()`);
 const{data}=await p.send('Page.captureScreenshot',{format:'png'});writeFileSync(resolve(out,`${width}-${theme}-${language}.png`),Buffer.from(data,'base64'));rows.push({width,theme,language,...r});}finally{await p.close()}
}
if(!process.env.UV_DOM_ONLY){
const p=await newPage(B,390,844,'light',{allow:[web.url]});
try{await navigate(p,web.url);await p.send('WebAuthn.enable');const{authenticatorId}=await p.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
await evaluate(p,`(async()=>{window.F=await import('./js/faceid.js');F.setChannel('AAAAAAAAAAAAAAAAAAAAAA');window.opts={relayIndex:0,channel:'AAAAAAAAAAAAAAAAAAAAAA',hostPub:new Uint8Array(32).fill(2),tag:new Uint8Array(8).fill(3),name:'Isolated QA'};window.old=await F.register(crypto.getRandomValues(new Uint8Array(32)),opts);F.clearFailed(old.id);await F.approve(new Uint8Array(32),old.id,false,'local');return true})()`);
await p.send('WebAuthn.clearCredentials',{authenticatorId});const start=Date.now();
const lost=await evaluate(p,`F.approve(new Uint8Array(32),old.id,false,'local').then(()=>({unexpected:true}),e=>({name:e.name}))`);assert.equal(lost.name,'NotAllowedError');
const reset=await evaluate(p,`(async()=>{window.fresh=await F.register(crypto.getRandomValues(new Uint8Array(32)),{...opts,peer:true});F.clearFailed(fresh.id);const proof=await F.approve(new Uint8Array(32),old.id,false,'local');return {newKey:fresh.id!==old.id,originalCardUsesNew:proof.id===fresh.id,failed:F.failedHere()}})()`);assert.deepEqual(reset,{newKey:true,originalCardUsesNew:true,failed:false});rows.push({nativeCTAP:{missingError:lost.name,missingElapsedMs:Date.now()-start,...reset},scope:'Actual WebAuthn/virtual CTAP product faceid functions; no Host peer approval or Safari claim'});
}finally{await p.close()}
}
writeFileSync(resolve(out,process.env.UV_DOM_ONLY?'dom-final.json':'result.json'),JSON.stringify(rows,null,2)+'\n');console.log(process.env.UV_DOM_ONLY ? 'PASS 8 DOM layouts/reset wiring; native CTAP NOT_RUN (DOM-only)' : 'PASS 8 DOM layouts/reset wiring + native CTAP saved/lost/recreated/original-card assertion');
}finally{await B.close();await web.stop()}
