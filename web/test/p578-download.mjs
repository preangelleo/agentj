// Real HTTPS/WSS, paired Noise control, browser WebCrypto and actual browser downloads to disk.
// The fixture owns synthetic source files and simulates Host policy; production Host/relay tests remain separate.
import assert from 'node:assert/strict';
import {measurePage} from './p0285-rework-c-dom.mjs';
const layoutOnly=process.env.P578_LAYOUT_ONLY==='1';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,mkdirSync,readFileSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash,randomBytes,webcrypto} from 'node:crypto';
import {startWebServer} from './serve.mjs';
import {startFakeHost} from './fakehost.mjs';
import {fileAAD,fileNonce,fileGetMessage} from '../public/js/private-transfer.js';
const require=createRequire(import.meta.url),pw=require(process.env.AJ_PLAYWRIGHT_MODULE||'/tmp/qa571-pw/node_modules/playwright');
const engine=process.env.P578_ENGINE||'webkit',out=process.env.P578_DOWNLOAD_OUT||'reports/qa/p578-web/'+engine;mkdirSync(out,{recursive:true});
const tmp=mkdtempSync(join(tmpdir(),'aj-578-tls-')),keyPath=join(tmp,'fixture.key'),certPath=join(tmp,'fixture.crt');
const cert=spawnSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',keyPath,'-out',certPath,'-subj','/CN=localhost','-days','1','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'});assert.equal(cert.status,0);
const tls={key:readFileSync(keyPath),cert:readFileSync(certPath)},sha=b=>createHash('sha256').update(b).digest('hex'),b64=b=>Buffer.from(b).toString('base64url');
const files=new Map(),requests=[],claims=[],nonces=new Set(),rows=[];let origin='',fake,web,B;
async function http(req,res){
 try{
  if(req.headers.origin!==origin){res.writeHead(403).end();return;}
  const cors={'Access-Control-Allow-Origin':origin,'Vary':'Origin','Cache-Control':'no-store'};
  if(req.method==='OPTIONS'){res.writeHead(204,{...cors,'Access-Control-Allow-Methods':'GET','Access-Control-Allow-Headers':'X-AJ-File-Key,X-AJ-File-Signature,X-AJ-File-Time,X-AJ-File-Nonce,X-AJ-File-Transfer'}).end();return;}
  const m=new RegExp('^/v1/file/'+fake.channel+'/([a-f0-9]{32})/chunk/(\\d+)$').exec(req.url||'');
  const f=m&&files.get(m[1]),h=req.headers,n=h['x-aj-file-nonce'],time=h['x-aj-file-time'],transfer=h['x-aj-file-transfer'];
  if(req.method!=='GET'||!f?.claim||f.disabled||f.card.expires<=Date.now()/1000||h['x-aj-file-key']!==f.claim.pk||transfer!==f.claim.transfer||nonces.has(n)||Math.abs(Date.now()/1000-Number(time))>30){res.writeHead(403,cors).end();return;}
  const pk=await crypto.subtle.importKey('raw',Buffer.from(f.claim.pk,'base64url'),'Ed25519',false,['verify']);
  if(!await crypto.subtle.verify('Ed25519',pk,Buffer.from(h['x-aj-file-signature']||'','base64url'),fileGetMessage(req.url,time,n,transfer))){res.writeHead(403,cors).end();return;}
  nonces.add(n);const index=Number(m[2]);requests.push({offer:f.card.id,index,transfer,https:true});
  if(f.fail){res.writeHead(503,cors).end();return;}
  if(f.disableAfter&&requests.filter(x=>x.offer===f.card.id).length>=f.disableAfter)f.disabled=true;
  const chunk=f.data.subarray(index*65536,(index+1)*65536),cipher=Buffer.from(await crypto.subtle.encrypt({name:'AES-GCM',iv:fileNonce(f.prefix,index),additionalData:fileAAD(f.card,transfer,index)},f.aes,chunk));
  if(f.tamper)cipher[0]^=1;
  res.writeHead(200,{...cors,'Content-Type':'application/octet-stream','Content-Length':String(cipher.length)}).end(cipher);
 }catch{res.writeHead(500).end();}
}
function pdf(n){
 const pre=Buffer.from('%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>\nendobj\n4 0 obj\n<< /Length 0 >>\nstream\nendstream\nendobj\n');
 const tail=Buffer.from('\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n'),b=Buffer.alloc(n,32);pre.copy(b);tail.copy(b,n-tail.length);return b;
}
async function offer(bytes,name){
 const data=pdf(bytes),id=randomBytes(16).toString('hex'),key=randomBytes(32),prefix=randomBytes(8),card={transport:'https1',id,seat:fake.channel,bytes,expires:Math.floor(Date.now()/1000)+86400,chunk_size:65536,sha256:sha(data),mime:'application/pdf'};
 const aes=await crypto.subtle.importKey('raw',key,'AES-GCM',false,['encrypt']),f={data,name,key,prefix,aes,card};files.set(id,f);return f;
}
try{
 fake=await startFakeHost({tls,httpHandler:(req,res)=>{void http(req,res);}});fake.st.extraCaps=['privatefiles1','privatefiles2'];
 web=await startWebServer({tls,...(process.env.P578_PUBLIC?{publicDir:process.env.P578_PUBLIC}:{}),relayCsp:fake.relay+' '+fake.relay.replace('wss:','https:')+'/v1/file/'});origin=new URL(web.url).origin;
 fake.st.onApp=async(c,m,send)=>{
  if(m.t!=='private_file_get'&&m.t!=='private_file_revoke')return false;
  const r=m.request,f=files.get(r?.id);assert.ok(await fake.verifyControl(c,m.t,m,{request:r}),'real paired Noise control signature');
  const reply=o=>send({t:'private_file_res',r:m.r,...o});
  if(!f||r.seat!==fake.channel||f.disabled)return reply({ok:false,why:'expired'}).then(()=>true);
  if(m.t==='private_file_revoke'){f.disabled=true;await reply({ok:true});return true;}
  if(r.info){await reply({ok:true,card:f.card,name:f.name});return true;}
  if(r.complete){assert.ok(f.claim&&f.claim.device===c.devId&&f.claim.transfer===r.transfer);f.completed=true;await reply({ok:true});return true;}
  if(f.claim&&(f.claim.transfer!==r.transfer||f.claim.device!==c.devId)){await reply({ok:false,why:'claimed'});return true;}
  const pk=fake.devs.find(x=>x.id===c.devId).sk;
  if(!f.claim){f.claim={transfer:r.transfer,device:c.devId,pk};claims.push({offer:f.card.id,transfer:r.transfer,device:c.devId});}
  f.lastRequest=r;
  await reply({ok:true,direct:true,total:f.card.bytes,chunk_size:65536,chunks:Math.ceil(f.card.bytes/65536),card:f.card,key:b64(f.key),nonce_prefix:b64(f.prefix),endpoint:fake.relay.replace('wss:','https:')+'/v1/file/'+fake.channel+'/'+f.card.id,grant:{v:1,offer:f.card.id,transfer:r.transfer,device_pk:pk,expires:f.card.expires}});return true;
 };
 B=await pw[engine].launch(engine==='chromium'?{executablePath:'/usr/bin/chromium',args:['--ignore-certificate-errors']}:{});
 const ctx=await B.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,ignoreHTTPSErrors:true,acceptDownloads:true});await ctx.addInitScript(()=>{window.AudioContext=undefined;window.webkitAudioContext=undefined;window.Audio=function(){return {preload:'',volume:1,currentTime:0,paused:true,play(){this.paused=false;return Promise.resolve();},pause(){this.paused=true;},addEventListener(){},removeEventListener(){}};};});const p=await ctx.newPage();
 p.on('pageerror',e=>console.error('page-error',e.name));p.on('crash',()=>console.error('PAGE CRASH at ciphertext request',requests.length));
 await p.goto(fake.newPairing(web.url+'?lang=zh'));await p.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p.waitForFunction(()=>window.__ajState==='ready');await fake.send({t:'status',s:'idle',agent:'claude',name:'Agent J'});await p.evaluate(()=>document.getElementById('phone-env-dismiss')?.click());
 const module=()=>p.evaluate(()=>import('./js/private-files.js').then(m=>window.__privateFiles=m));await module();
 for(const [mb,normal] of (layoutOnly?[[0.0625,true]]:[[18,true],[50,false]])){
  const f=await offer(mb*1048576,'synthetic-'+mb+'.pdf'),turn=await fake.addTurn({k:'phone',text:'下载测试文件'},'隔离 PDF 下载测试');
  const media={mid:randomBytes(16).toString('base64url'),name:f.name,mime:'application/pdf',kind:'pdf',bytes:f.card.bytes,sha256:f.card.sha256,download:f.card};
  await fake.updateTurn(turn.id,normal?{media:[media],media_skip:[]}:{media:[],media_skip:[{name:f.name,why:'too_big',bytes:f.card.bytes,download:f.card}]});
  await p.waitForFunction(()=>document.querySelector('.mcard .dl,[data-private-file] button:last-child'));
  if(normal)await p.locator('.mcard .dl').click();else await p.locator('[data-private-file] button:last-child').click();
  await p.waitForFunction(()=>document.getElementById('private-file-locator')&&!document.getElementById('private-file-locator').hidden&&document.querySelector('#private-file-locator button:last-child')?.textContent==='保存到设备',null,{timeout:120000});
  const screenshot=out+'/'+mb+'MiB-ready.png';await p.screenshot({path:screenshot});
  if(normal){for(const width of (process.env.C_WIDTHS||'390,1440').split(',').map(Number))for(const theme of (process.env.C_THEMES||'light,dark').split(','))for(const language of (process.env.C_LANGS||'zh,en').split(',')){
   await p.setViewportSize({width,height:900});await p.emulateMedia({colorScheme:theme});await p.evaluate(language=>window.AJLang.set(language),language);await p.waitForTimeout(100);
   const g=await p.evaluate(()=>{const r=document.querySelector('#private-file-locator .sq').getBoundingClientRect();return {left:r.left,right:r.right,overflow:document.documentElement.scrollWidth>innerWidth,actions:[...document.querySelectorAll('#private-file-locator button,#private-file-locator a')].filter(x=>!x.hidden).map(x=>{const b=x.getBoundingClientRect();const cs=getComputedStyle(x);return {width:b.width,height:b.height,top:b.top,bottom:b.bottom,paddingInline:parseFloat(cs.paddingLeft)+parseFloat(cs.paddingRight)};})};});
   if(layoutOnly){const dom=await p.evaluate(measurePage,{scene:'download',groups:[['#private-file-locator .gdlg-row',12]]});const peer=await p.evaluate(()=>{const open=document.querySelector('#private-file-locator a'),actions=document.querySelector('#private-file-locator .gdlg-row'),O=open.getBoundingClientRect(),A=actions.getBoundingClientRect();return{gate:1,name:'download open link occupies a separate row above paired buttons',pass:open.parentElement!==actions&&A.top-O.bottom>=7.4,detail:{linkBottom:O.bottom,actionsTop:A.top,min:8,sameParent:open.parentElement===actions}}});dom.checks.push(peer);const buttons=await p.evaluate(()=>[...document.querySelectorAll('#private-file-locator .gdlg-row button')].filter(n=>!n.hidden&&n.getBoundingClientRect().width>0).length);dom.checks.push({gate:1,name:'download ready has two visible peer buttons',pass:buttons===2,detail:{buttons}});rows.push({width,theme,lang:language,...dom});await p.screenshot({path:out+'/layout-'+width+'-'+theme+'-'+language+'.png'});continue;}
   assert.ok(g.left>=0&&g.right<=width);assert.equal(g.overflow,false);assert.ok(g.actions.every(x=>x.width>=44&&x.height>=44));assert.ok(g.actions[0].bottom+8<=g.actions[1].top,'Open file owns a separate row with at least 8px gap');await p.screenshot({path:out+'/layout-'+width+'-'+theme+'-'+language+'.png'});
  }await p.setViewportSize({width:390,height:844});await p.emulateMedia({colorScheme:'light'});await p.evaluate(()=>window.AJLang.set('zh'));}
  if(layoutOnly)break;
  const event=p.waitForEvent('download',{timeout:30000});await p.locator('#private-file-locator button:last-child').click();const download=await event,path=join(tmp,mb+'-saved.pdf');await download.saveAs(path);
  assert.equal(readFileSync(path).length,f.card.bytes);assert.equal(sha(readFileSync(path)),f.card.sha256);assert.equal(download.suggestedFilename(),f.name);
  assert.equal(claims.filter(x=>x.offer===f.card.id).length,1);assert.ok(!fake.log.some(x=>x.t==='media_get'),'file bytes never use Noise media_get');
  rows.push({engine,mb,bytes:f.card.bytes,sha256:f.card.sha256,downloaded_sha256:sha(readFileSync(path)),https_chunks:requests.filter(x=>x.offer===f.card.id).length,claims:1,normal_media:normal,screenshot});
  await p.locator('#private-file-locator button:first-child').click();console.log('PASS',engine,mb+'MiB','disk SHA and one claim');
 }

 if(!layoutOnly){
 const attempt=(page,f)=>page.evaluate(async card=>{try{await (await import('./js/private-files.js')).downloadFile(card,'negative-fixture.pdf');return 'ready';}catch(e){return e.code||'failed';}},f.card);
 const retry=await offer(8*1048576,'retry.pdf');retry.fail=true;assert.equal(await attempt(p,retry),'failed');const bound=retry.claim.transfer;retry.fail=false;
 assert.equal(await attempt(p,retry),'ready');assert.equal(retry.claim.transfer,bound);assert.equal(retry.lastRequest.retry,true);assert.equal(claims.filter(x=>x.offer===retry.card.id).length,1);
 await p.locator('#private-file-locator button:first-child').click();rows.push({scenario:'retry',same_transfer:true,claims:1});
 const bad=await offer(8*1048576,'tampered.pdf');bad.tamper=true;assert.equal(await attempt(p,bad),'invalid');assert.equal(await p.locator('#private-file-locator').isVisible(),false);rows.push({scenario:'tampered_ciphertext',refused:true});
 const stopped=await offer(8*1048576,'revoked.pdf');stopped.disableAfter=2;assert.equal(await attempt(p,stopped),'expired');assert.equal(await p.locator('#private-file-locator').isVisible(),false);rows.push({scenario:'live_revocation',refused:true});
 const before=claims.length;assert.equal(await attempt(p,{card:{...retry.card,bytes:65*1048576}}),'budget');assert.equal(await attempt(p,{card:{...retry.card,seat:'x'.repeat(22)}}),'unavailable');assert.equal(claims.length,before);rows.push({scenario:'budget_cross_seat_preclaim',refused:true});
 const ctx2=await B.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,ignoreHTTPSErrors:true});
 await ctx2.addInitScript(()=>{window.AudioContext=undefined;window.webkitAudioContext=undefined;window.Audio=function(){return {preload:'',volume:1,currentTime:0,paused:true,play(){return Promise.resolve();},pause(){},addEventListener(){},removeEventListener(){}};};});
 const p2=await ctx2.newPage();await p2.goto(fake.newPairing(web.url+'?lang=zh'));await p2.waitForFunction(()=>window.__ajState==='awaiting-approval');await fake.approve();await p2.waitForFunction(()=>window.__ajState==='ready');
 const once=await offer(8*1048576,'concurrent.pdf'),concurrent=await Promise.all([attempt(p,once),attempt(p2,once)]);assert.deepEqual(concurrent.sort(),['claimed','ready']);assert.equal(claims.filter(x=>x.offer===once.card.id).length,1);rows.push({scenario:'two_paired_devices_concurrent_claim',results:concurrent,claims:1});await ctx2.close();
 console.log('PASS',engine,'retry, tamper, live revoke, preclaim budget/cross-seat, two-device atomic claim');
 }
 await ctx.close();
}finally{writeFileSync(out+'/result.json',JSON.stringify({physical:false,rows,http_requests:requests.length,claims:claims.length},null,2));await B?.close();await fake?.stop();await web?.stop();rmSync(tmp,{recursive:true,force:true});}
console.log(layoutOnly?'MEASURED #578 ready layout via real 64KiB HTTPS+AES download':'PASS #578 '+engine+' real 18/50 MiB HTTPS downloads');
